from __future__ import annotations

import hashlib
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException

from app.config.settings import (
    PERSONAL_STORAGE_BASE_DIR,
    SYNC_LOCAL_BASE_DIR_GENERAL,
    SYNC_LOCAL_BASE_DIR_KIWIX,
    UPLOAD_DIR,
)
from app.repositories.common import execute, fetch_all, fetch_one
from app.services.common.naming_service import build_display_name
from app.services.features.document_metadata_service import refresh_general_document_metadata


def as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def clean_node_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def node_name_key(value: Any) -> str:
    return clean_node_name(value).casefold()


def find_child_node_by_alias(
    parent_id: Optional[int],
    alias: Any,
    *,
    exclude_node_id: Optional[int] = None,
    folder_only: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    key = node_name_key(alias)
    if not key:
        return None

    params: List[Any] = [parent_id]
    sql = """
        SELECT node_id, parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
               name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
          FROM tree_nodes
         WHERE parent_id IS NOT DISTINCT FROM %s
    """
    if exclude_node_id is not None:
        sql += " AND node_id<>%s"
        params.append(int(exclude_node_id))
    if folder_only is True:
        sql += " AND is_folder=TRUE"
    elif folder_only is False:
        sql += " AND is_folder=FALSE"
    sql += " ORDER BY sort_order, node_id"

    for row in fetch_all(sql, tuple(params)):
        if node_name_key(row.get("alias") or row.get("name")) == key:
            return row
    return None


def is_admin(user_id: int) -> bool:
    row = fetch_one("SELECT is_admin FROM users WHERE user_id=%s", (int(user_id),))
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    return bool(int(row.get("is_admin") or 0))


def _permission_suffix(permission: str) -> str:
    perm = str(permission or "").strip().lower()
    if perm not in {"r", "w", "x"}:
        raise ValueError("permission must be one of r, w, x")
    return perm


def active_group_ids_for_user(user_id: int) -> Set[int]:
    rows = fetch_all(
        """
        SELECT gm.group_id
          FROM group_members gm
          JOIN org_groups g ON g.group_id = gm.group_id
         WHERE gm.user_id=%s
           AND gm.is_active=TRUE
           AND g.is_active=TRUE
        UNION
        SELECT u.group_id
          FROM users u
          JOIN org_groups g ON g.group_id = u.group_id
         WHERE u.user_id=%s
           AND u.group_id IS NOT NULL
           AND COALESCE(u.is_active, TRUE)=TRUE
           AND g.is_active=TRUE
        """,
        (int(user_id), int(user_id)),
    )
    out: Set[int] = set()
    for row in rows or []:
        gid = as_int(row.get("group_id"))
        if gid is not None:
            out.add(gid)
    return out


def policy_requires_target_group(policy: Optional[Dict[str, Any]]) -> bool:
    if not policy:
        return False
    code = str(policy.get("policy_code") or "").strip().upper()
    if code in {"GROUP", "SG"}:
        return True
    if code in {"PUBLIC", "PRIVATE", "S1", "S2", "S3"}:
        return False
    group_bits = bool(policy.get("group_r") or policy.get("group_w") or policy.get("group_x"))
    other_bits = bool(policy.get("other_r") or policy.get("other_w") or policy.get("other_x"))
    return group_bits and not other_bits


PERSONAL_KB_TYPES = {"personal", "person"}
PERSONAL_UPLOAD_MODES = {"personal"}


def _norm_policy_value(value: Any) -> str:
    return str(value or "").strip().lower()


def row_is_personal_document(row: Dict[str, Any]) -> bool:
    if not row:
        return False
    return (
        _norm_policy_value(row.get("upload_mode")) in PERSONAL_UPLOAD_MODES
        or _norm_policy_value(row.get("kb_type")) in PERSONAL_KB_TYPES
    )


def row_owned_by_user(row: Dict[str, Any], user_id: int) -> bool:
    if not row:
        return False
    uid = int(user_id)
    for key in ("kb_owner_user_id", "uploader_user_id", "owner_user_id", "created_by"):
        value = as_int(row.get(key))
        if value is not None and value == uid:
            return True
    return False


def admin_can_access_document(row: Dict[str, Any], user_id: int) -> bool:
    if not row or bool(row.get("is_folder")):
        return False
    if not row_is_personal_document(row):
        return True
    return row_owned_by_user(row, user_id)


def admin_object_permission_exists_clause(
    user_id: int,
    *,
    object_alias: str = "so",
) -> Tuple[str, Tuple[Any, ...]]:
    uid = int(user_id)
    return (
        f"""
        EXISTS(
            SELECT 1
              FROM tree_nodes admin_tn
              LEFT JOIN acl_bindings admin_ab ON admin_ab.node_id = admin_tn.node_id
             WHERE admin_tn.doc_id = {object_alias}.object_id
               AND admin_tn.is_folder=FALSE
               AND (
                    (
                        COALESCE({object_alias}.upload_mode, '') <> 'personal'
                        AND COALESCE(admin_tn.kb_type, '') NOT IN ('personal', 'person')
                    )
                    OR admin_tn.kb_owner_user_id=%s
                    OR {object_alias}.uploader_user_id=%s
                    OR admin_tn.created_by=%s
                    OR admin_ab.owner_user_id=%s
               )
        )
        """,
        (uid, uid, uid, uid),
    )


def row_has_document_permission(
    row: Dict[str, Any],
    user_id: int,
    permission: str = "r",
    *,
    user_group_ids: Optional[Set[int]] = None,
    admin_override: bool = False,
) -> bool:
    if admin_override:
        return admin_can_access_document(row, user_id)
    if not row or bool(row.get("is_folder")):
        return False
    perm = _permission_suffix(permission)
    if row.get(f"other_{perm}"):
        return True
    owner_id = as_int(row.get("owner_user_id"))
    if owner_id is not None and owner_id == int(user_id) and row.get(f"owner_{perm}"):
        return True
    target_group_id = as_int(row.get("target_group_id"))
    if target_group_id is not None and row.get(f"group_{perm}"):
        groups = user_group_ids if user_group_ids is not None else active_group_ids_for_user(int(user_id))
        if target_group_id in groups:
            return True
    return False


def acl_permission_clause(
    user_id: int,
    permission: str = "r",
    *,
    acl_alias: str = "ab",
    policy_alias: str = "pp",
) -> Tuple[str, Tuple[Any, ...]]:
    perm = _permission_suffix(permission)
    clause = f"""
        (
            COALESCE({policy_alias}.other_{perm}, FALSE)=TRUE
            OR (
                {acl_alias}.owner_user_id=%s
                AND COALESCE({policy_alias}.owner_{perm}, FALSE)=TRUE
            )
            OR (
                {acl_alias}.target_group_id IS NOT NULL
                AND COALESCE({policy_alias}.group_{perm}, FALSE)=TRUE
                AND (
                    EXISTS(
                        SELECT 1
                          FROM group_members gm
                          JOIN org_groups g ON g.group_id = gm.group_id
                         WHERE gm.group_id = {acl_alias}.target_group_id
                           AND gm.user_id=%s
                           AND gm.is_active=TRUE
                           AND g.is_active=TRUE
                    )
                    OR EXISTS(
                        SELECT 1
                          FROM users u
                          JOIN org_groups g ON g.group_id = u.group_id
                         WHERE u.user_id=%s
                           AND u.group_id = {acl_alias}.target_group_id
                           AND COALESCE(u.is_active, TRUE)=TRUE
                           AND g.is_active=TRUE
                    )
                )
            )
        )
    """
    return clause, (int(user_id), int(user_id), int(user_id))


def object_permission_exists_clause(
    user_id: int,
    permission: str = "r",
    *,
    object_alias: str = "so",
) -> Tuple[str, Tuple[Any, ...]]:
    if is_admin(int(user_id)):
        return admin_object_permission_exists_clause(int(user_id), object_alias=object_alias)
    perm_clause, params = acl_permission_clause(
        int(user_id),
        permission,
        acl_alias="perm_ab",
        policy_alias="perm_pp",
    )
    return (
        f"""
        EXISTS(
            SELECT 1
              FROM tree_nodes perm_tn
              JOIN acl_bindings perm_ab ON perm_ab.node_id = perm_tn.node_id
              JOIN perm_policies perm_pp ON perm_pp.policy_code = perm_ab.policy_code
             WHERE perm_tn.doc_id = {object_alias}.object_id
               AND perm_tn.is_folder=FALSE
               AND {perm_clause}
        )
        """,
        params,
    )


def has_object_permission(user_id: int, object_id: int, permission: str = "r") -> bool:
    if is_admin(int(user_id)):
        rows = fetch_all(
            """
            SELECT
                so.object_id,
                so.upload_mode,
                so.uploader_user_id,
                tn.node_id,
                tn.is_folder,
                tn.kb_type,
                tn.kb_owner_user_id,
                tn.created_by,
                ab.owner_user_id
              FROM storage_objects so
              JOIN tree_nodes tn ON tn.doc_id = so.object_id
              LEFT JOIN acl_bindings ab ON ab.node_id = tn.node_id
             WHERE so.object_id=%s
               AND tn.is_folder=FALSE
            """,
            (int(object_id),),
        )
        return any(admin_can_access_document(row, user_id) for row in rows or [])
    clause, params = acl_permission_clause(int(user_id), permission, acl_alias="ab", policy_alias="pp")
    row = fetch_one(
        f"""
        SELECT 1 AS ok
          FROM tree_nodes tn
          JOIN acl_bindings ab ON ab.node_id = tn.node_id
          JOIN perm_policies pp ON pp.policy_code = ab.policy_code
         WHERE tn.doc_id=%s
           AND tn.is_folder=FALSE
           AND {clause}
         LIMIT 1
        """,
        tuple([int(object_id)] + list(params)),
    )
    return bool(row)


def require_object_permission(user_id: int, object_id: int, permission: str = "r") -> None:
    if not has_object_permission(int(user_id), int(object_id), permission):
        raise HTTPException(status_code=403, detail="Document permission denied")


def filter_object_ids_by_permission(
    user_id: int,
    object_ids: Iterable[int],
    permission: str = "r",
) -> List[int]:
    ids = [int(x) for x in dict.fromkeys(object_ids or []) if x is not None]
    if not ids:
        return []
    if is_admin(int(user_id)):
        placeholders = ",".join(["%s"] * len(ids))
        clause, params = object_permission_exists_clause(int(user_id), permission, object_alias="so")
        rows = fetch_all(
            f"""
            SELECT DISTINCT so.object_id
              FROM storage_objects so
             WHERE so.object_id IN ({placeholders})
               AND {clause}
            """,
            tuple(ids + list(params)),
        )
        allowed = {int(row["object_id"]) for row in rows or [] if row.get("object_id") is not None}
        return [oid for oid in ids if oid in allowed]
    placeholders = ",".join(["%s"] * len(ids))
    clause, params = acl_permission_clause(int(user_id), permission, acl_alias="ab", policy_alias="pp")
    rows = fetch_all(
        f"""
        SELECT DISTINCT tn.doc_id AS object_id
          FROM tree_nodes tn
          JOIN acl_bindings ab ON ab.node_id = tn.node_id
          JOIN perm_policies pp ON pp.policy_code = ab.policy_code
         WHERE tn.doc_id IN ({placeholders})
           AND tn.is_folder=FALSE
           AND {clause}
        """,
        tuple(ids + list(params)),
    )
    allowed = {int(row["object_id"]) for row in rows or [] if row.get("object_id") is not None}
    return [oid for oid in ids if oid in allowed]


def accessible_object_ids_for_categories(
    user_id: int,
    categories: Optional[Iterable[Any]] = None,
    permission: str = "x",
    *,
    limit: int = 5000,
) -> List[int]:
    cats = [clean_node_name(c) for c in (categories or []) if clean_node_name(c)]
    item_type_codes: List[int] = []
    if cats:
        placeholders_for_codes = ",".join(["%s"] * len(cats))
        code_rows = fetch_all(
            f"""
            SELECT DISTINCT item_type_code
              FROM tree_nodes
             WHERE is_folder=TRUE
               AND item_type_code IS NOT NULL
               AND (name IN ({placeholders_for_codes}) OR alias IN ({placeholders_for_codes}))
            """,
            tuple(cats + cats),
        )
        for row in code_rows or []:
            code = as_int(row.get("item_type_code"))
            if code is not None and code not in item_type_codes:
                item_type_codes.append(code)
    params: List[Any] = []
    conds = ["so.doc_status='active'", "tn.is_folder=FALSE", "tn.doc_id IS NOT NULL"]
    if cats and not any(c.casefold() == "se kb" for c in cats):
        placeholders = ",".join(["%s"] * len(cats))
        code_cond = ""
        if item_type_codes:
            code_placeholders = ",".join(["%s"] * len(item_type_codes))
            code_cond = f" OR tn.item_type_code IN ({code_placeholders})"
        conds.append(
            f"""(
                so.relative_path IN ({placeholders})
                OR tn.name IN ({placeholders})
                OR tn.alias IN ({placeholders})
                OR CAST(tn.item_type_code AS TEXT) IN ({placeholders})
                {code_cond}
            )"""
        )
        params.extend(cats)
        params.extend(cats)
        params.extend(cats)
        params.extend(cats)
        params.extend(item_type_codes)
    if is_admin(int(user_id)):
        clause, acl_params = object_permission_exists_clause(int(user_id), permission, object_alias="so")
        conds.append(clause)
        params.extend(acl_params)
    else:
        clause, acl_params = acl_permission_clause(int(user_id), permission, acl_alias="ab", policy_alias="pp")
        conds.append(clause)
        params.extend(acl_params)
    params.append(max(1, min(int(limit or 5000), 20000)))
    rows = fetch_all(
        f"""
        SELECT DISTINCT so.object_id
          FROM storage_objects so
          JOIN tree_nodes tn ON tn.doc_id = so.object_id
          LEFT JOIN acl_bindings ab ON ab.node_id = tn.node_id
          LEFT JOIN perm_policies pp ON pp.policy_code = ab.policy_code
         WHERE {" AND ".join(conds)}
         ORDER BY so.object_id
         LIMIT %s
        """,
        tuple(params),
    )
    return [int(row["object_id"]) for row in rows or [] if row.get("object_id") is not None]


def stored_filename(doc_name: Any, file_right: Any) -> str:
    return build_display_name(
        str(doc_name or "").strip(),
        str(file_right or "").strip().lstrip("."),
        fallback="file",
    )


def normalize_branch_type(value: Any) -> str:
    v = str(value or "general").strip().lower()
    return "kiwix" if v == "kiwix" else "general"


def branch_label(value: Any) -> str:
    return normalize_branch_type(value).upper()


def base_dir_for_branch(branch_type: Any) -> str:
    return SYNC_LOCAL_BASE_DIR_KIWIX if normalize_branch_type(branch_type) == "kiwix" else SYNC_LOCAL_BASE_DIR_GENERAL


def base_dir_for_storage_row(row: Dict[str, Any]) -> str:
    if row_is_personal_document(row):
        return PERSONAL_STORAGE_BASE_DIR
    return base_dir_for_branch(row.get("branch_type"))


def base_dir_for_document_parent(parent: Dict[str, Any], branch_type: Any) -> str:
    if row_is_personal_document(parent):
        return PERSONAL_STORAGE_BASE_DIR
    return base_dir_for_branch(branch_type)


def normalize_rel(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def join_rel_path(*parts: Any) -> str:
    clean = [normalize_rel(p) for p in parts if normalize_rel(p)]
    return "/".join(clean)


def storage_target_path(row: Dict[str, Any]) -> str:
    return join_rel_path(
        row.get("relative_path"),
        row.get("sub_path"),
        stored_filename(row.get("doc_name"), row.get("file_right")),
    )


def resolve_storage_path(row: Dict[str, Any]) -> str:
    rel = storage_target_path(row)
    base = base_dir_for_storage_row(row)
    if rel:
        return os.path.abspath(os.path.join(base, rel.replace("/", os.sep)))
    return os.path.abspath(os.path.join(UPLOAD_DIR, stored_filename(row.get("doc_name"), row.get("file_right"))))


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_meta(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    st = p.stat()
    return {
        "size_bytes": int(st.st_size),
        "checksum_sha256": file_sha256(p),
        "source_created_at": datetime.fromtimestamp(st.st_ctime),
        "source_updated_at": datetime.fromtimestamp(st.st_mtime),
        "mime_type": mimetypes.guess_type(str(p))[0] or "application/octet-stream",
    }


def split_filename(filename: str) -> Tuple[str, str]:
    base = os.path.basename(str(filename or "").strip())
    name, ext = os.path.splitext(base)
    return (name or base or "file", ext.lstrip(".").lower())


def get_node(node_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT node_id, parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
               name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
          FROM tree_nodes
         WHERE node_id=%s
        """,
        (int(node_id),),
    )


def require_node_edit_access(user_id: int, row: Dict[str, Any]) -> None:
    """Allow personal-KB mutations only to the owner and other KB mutations to admins."""
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    uid = int(user_id)
    if _norm_policy_value(row.get("kb_type")) in PERSONAL_KB_TYPES:
        if as_int(row.get("kb_owner_user_id")) != uid:
            raise HTTPException(status_code=403, detail="Personal KB edit permission required")
        return
    if not is_admin(uid):
        raise HTTPException(status_code=403, detail="Admin permission required")


def require_kb_read_access(user_id: int, root: Dict[str, Any]) -> None:
    """Do not disclose another user's personal KB, including its empty folders."""
    if not root:
        raise HTTPException(status_code=404, detail="KB tree not found")
    if (
        _norm_policy_value(root.get("kb_type")) in PERSONAL_KB_TYPES
        and as_int(root.get("kb_owner_user_id")) != int(user_id)
    ):
        raise HTTPException(status_code=403, detail="Personal KB permission required")


def get_kb_entry(kb_id: int) -> Dict[str, Any]:
    row = get_node(int(kb_id))
    if not row:
        raise HTTPException(status_code=404, detail="KB tree not found")
    if not bool(row.get("is_folder")):
        raise HTTPException(status_code=400, detail="KB id must be a folder node")
    return row


def node_path_parts(node_id: Optional[int], *, use_alias: bool = False, include_self: bool = True) -> List[str]:
    if node_id is None:
        return []
    parts: List[str] = []
    cur: Optional[int] = int(node_id)
    safety = 0
    while cur is not None and safety < 256:
        safety += 1
        row = get_node(cur)
        if not row:
            break
        parent_id = as_int(row.get("parent_id"))
        if parent_id is not None and (include_self or cur != node_id):
            name = row.get("alias" if use_alias else "name") or row.get("name")
            if name:
                parts.append(str(name))
        cur = parent_id
    parts.reverse()
    return parts


def rel_sub_for_parent(parent_id: Optional[int]) -> Tuple[str, str]:
    parts = node_path_parts(parent_id, use_alias=False, include_self=True)
    if not parts:
        return "", ""
    return parts[0], "/".join(parts[1:])


def storage_rel_sub_for_parent(parent_id: Optional[int]) -> Tuple[str, str]:
    if parent_id is None:
        return "", ""

    chain: List[Dict[str, Any]] = []
    cur = as_int(parent_id)
    safety = 0
    while cur is not None and safety < 256:
        safety += 1
        row = get_node(cur)
        if not row:
            break
        chain.append(row)
        cur = as_int(row.get("parent_id"))

    if not chain:
        return "", ""

    chain.reverse()
    root = chain[0]
    if _norm_policy_value(root.get("kb_type")) in PERSONAL_KB_TYPES:
        parts = [str(row.get("name") or "").strip() for row in chain]
        parts = [part for part in parts if part]
        if not parts:
            return "", ""
        return parts[0], "/".join(parts[1:])

    return rel_sub_for_parent(parent_id)


def next_sort_order(parent_id: Optional[int]) -> int:
    if parent_id is None:
        row = fetch_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM tree_nodes WHERE parent_id IS NULL")
    else:
        row = fetch_one("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM tree_nodes WHERE parent_id=%s", (int(parent_id),))
    return int((row or {}).get("n") or 0)


def next_item_type_code() -> int:
    row = fetch_one("SELECT nextval('public.item_type_code_seq') AS n")
    if not row or row.get("n") is None:
        raise RuntimeError("PostgreSQL did not return an item_type_code sequence value")
    return int(row["n"])


def collect_descendants(node_ids: Iterable[int]) -> Set[int]:
    out: Set[int] = set()
    stack = [int(x) for x in node_ids if x is not None]
    rows = fetch_all("SELECT node_id, parent_id FROM tree_nodes")
    children_by_parent: Dict[int, List[int]] = {}
    for row in rows or []:
        parent_id = as_int(row.get("parent_id"))
        child_id = as_int(row.get("node_id"))
        if parent_id is not None and child_id is not None:
            children_by_parent.setdefault(parent_id, []).append(child_id)
    while stack:
        nid = stack.pop()
        if nid in out:
            continue
        out.add(nid)
        for cid in children_by_parent.get(nid, []):
            if cid not in out:
                stack.append(cid)
    return out


def ensure_acl(
    node_id: int,
    policy_code: str = "PUBLIC",
    owner_user_id: Optional[int] = None,
    target_group_id: Optional[int] = None,
) -> None:
    execute(
        """
        INSERT INTO acl_bindings(node_id, policy_code, owner_user_id, target_group_id, created_at, updated_at)
        VALUES(%s,%s,%s,%s,NOW(),NOW())
        ON CONFLICT (node_id) DO UPDATE SET
            policy_code=EXCLUDED.policy_code,
            owner_user_id=EXCLUDED.owner_user_id,
            target_group_id=EXCLUDED.target_group_id,
            updated_at=NOW()
        """,
        (int(node_id), policy_code, owner_user_id, target_group_id),
    )


def node_policy_matrix(row: Dict[str, Any]) -> Optional[List[str]]:
    if not row or row.get("owner_r") is None:
        return None
    return [
        f"{'r' if row.get('owner_r') else '-'}{'w' if row.get('owner_w') else '-'}{'x' if row.get('owner_x') else '-'}",
        f"{'r' if row.get('group_r') else '-'}{'w' if row.get('group_w') else '-'}{'x' if row.get('group_x') else '-'}",
        f"{'r' if row.get('other_r') else '-'}{'w' if row.get('other_w') else '-'}{'x' if row.get('other_x') else '-'}",
    ]


def enqueue_ai_job(object_id: int, action: str, created_by: Optional[int] = None) -> None:
    safe_action = str(action or "").strip().lower()
    if safe_action not in {"add", "update", "delete", "reindex"}:
        return

    ai = fetch_one("SELECT ai_document_id, index_status FROM ai_document_info WHERE object_id=%s", (int(object_id),))
    if not ai:
        return

    ai_document_id = str(ai.get("ai_document_id") or "").strip()
    if not ai_document_id:
        return

    if safe_action == "delete":
        from app.services.features.minio_document_lifecycle_service import requires_index_delete

        index_delete_required = requires_index_delete(ai_document_id)
        execute(
            """
            UPDATE ai_index_jobs
               SET status='cancelled',
                   cancel_requested_at=NOW(),
                   finished_at=NOW(),
                   updated_at=NOW()
             WHERE ai_document_id=%s
               AND action IN ('add', 'update', 'reindex')
               AND status IN ('pending', 'running', 'sent')
            """,
            (ai_document_id,),
        )
        if not index_delete_required:
            execute(
                """
                UPDATE ai_index_jobs
                   SET status='cancelled',
                       cancel_requested_at=NOW(),
                       finished_at=NOW(),
                       updated_at=NOW()
                 WHERE ai_document_id=%s
                   AND action='delete'
                   AND status IN ('pending', 'running', 'sent')
                """,
                (ai_document_id,),
            )
            return
        exists = fetch_one(
            """
            SELECT job_id
              FROM ai_index_jobs
             WHERE ai_document_id=%s
               AND action='delete'
               AND status IN ('pending', 'running', 'sent')
             LIMIT 1
            """,
            (ai_document_id,),
        )
        if exists:
            return
    else:
        row = fetch_one(
            """
            SELECT so.object_id, so.doc_status
              FROM storage_objects so
             WHERE so.object_id=%s
               AND so.doc_status='active'
             LIMIT 1
            """,
            (int(object_id),),
        )
        if not row or str(ai.get("index_status") or "").strip().lower() == "deleted":
            return

    execute(
        """
        INSERT INTO ai_index_jobs(
            ai_document_id, object_id, action, stage, status, retry_count,
            scheduled_at, created_by, created_at, updated_at
        )
        VALUES(%s,%s,%s,%s,'pending',0,NOW(),%s,NOW(),NOW())
        """,
        (
            ai_document_id,
            int(object_id),
            safe_action,
            "delete" if safe_action == "delete" else "parse",
            created_by,
        ),
    )
def enqueue_ai_metadata_job(object_id: int, created_by: Optional[int] = None) -> None:
    """Queue an index-only refresh when parsed document content is reusable."""
    document = fetch_one(
        """
        SELECT ai.ai_document_id, ai.source_revision_id, ai.index_status,
               so.doc_status
          FROM ai_document_info ai
          JOIN storage_objects so ON so.object_id=ai.object_id
         WHERE ai.object_id=%s
         LIMIT 1
        """,
        (int(object_id),),
    )
    if not document or str(document.get("doc_status") or "").lower() != "active":
        return
    if str(document.get("index_status") or "").lower() == "deleted":
        return

    ai_document_id = str(document.get("ai_document_id") or "").strip()
    revision_id = as_int(document.get("source_revision_id"))
    if not ai_document_id or revision_id is None:
        enqueue_ai_job(object_id, "update", created_by)
        return

    artifact = fetch_one(
        """
        SELECT artifact_id
          FROM ai_parse_artifacts
         WHERE revision_id=%s
           AND artifact_type='manifest'
           AND artifact_status='READY'
         ORDER BY verified_at DESC NULLS LAST, artifact_id DESC
         LIMIT 1
        """,
        (revision_id,),
    )
    if not artifact:
        active_job = fetch_one(
            """
            SELECT job_id
              FROM ai_index_jobs
             WHERE ai_document_id=%s
               AND status IN ('pending','running','sent')
               AND action IN ('add','update','reindex')
             LIMIT 1
            """,
            (ai_document_id,),
        )
        if not active_job:
            enqueue_ai_job(object_id, "update", created_by)
        return

    pending = fetch_one(
        """
        SELECT job_id
          FROM ai_index_jobs
         WHERE ai_document_id=%s
           AND revision_id=%s
           AND stage='index'
           AND status='pending'
         ORDER BY job_id DESC
         LIMIT 1
        """,
        (ai_document_id, revision_id),
    )
    if pending:
        execute(
            """
            UPDATE ai_index_jobs
               SET artifact_id=%s, action='update', created_by=COALESCE(%s,created_by),
                   scheduled_at=NOW(), last_error=NULL, updated_at=NOW()
             WHERE job_id=%s
            """,
            (int(artifact["artifact_id"]), created_by, int(pending["job_id"])),
        )
    else:
        execute(
            """
            INSERT INTO ai_index_jobs(
                ai_document_id, object_id, revision_id, artifact_id, action, stage,
                status, retry_count, scheduled_at, created_by, created_at, updated_at
            )
            VALUES(%s,%s,%s,%s,'update','index','pending',0,NOW(),%s,NOW(),NOW())
            """,
            (
                ai_document_id,
                int(object_id),
                revision_id,
                int(artifact["artifact_id"]),
                created_by,
            ),
        )
    execute(
        """
        UPDATE ai_document_info
           SET index_status='pending', updated_at=NOW()
         WHERE ai_document_id=%s
        """,
        (ai_document_id,),
    )


def ensure_ai_document_info(object_id: int, *, index_status: str = "pending") -> None:
    row = fetch_one(
        """
        SELECT so.object_id, so.doc_code, so.doc_name, so.file_right, so.relative_path, so.sub_path,
               so.uploader_user_id, so.upload_mode,
               tn.item_type_code, tn.kb_type, tn.kb_owner_user_id, tn.created_by
          FROM storage_objects so
          JOIN tree_nodes tn ON tn.doc_id = so.object_id
         WHERE so.object_id=%s
         ORDER BY tn.node_id
         LIMIT 1
        """,
        (int(object_id),),
    )
    if not row:
        return
    execute(
        """
        INSERT INTO ai_document_info(
            ai_document_id, object_id, user_id, item_type_code, relative_path, sub_path,
            doc_name, file_right, doc_code, index_status, created_at, updated_at
        ) VALUES(CONCAT('aid_', REPLACE(gen_random_uuid()::text, '-', '')), %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (object_id) DO UPDATE SET
            user_id=EXCLUDED.user_id,
            item_type_code=EXCLUDED.item_type_code,
            relative_path=EXCLUDED.relative_path,
            sub_path=EXCLUDED.sub_path,
            doc_name=EXCLUDED.doc_name,
            file_right=EXCLUDED.file_right,
            doc_code=EXCLUDED.doc_code,
            index_status=EXCLUDED.index_status,
            updated_at=NOW()
        """,
        (
            int(object_id),
            as_int(row.get("uploader_user_id"))
            or as_int(row.get("kb_owner_user_id"))
            or as_int(row.get("created_by")),
            int(row.get("item_type_code") or 10),
            row.get("relative_path") or "",
            row.get("sub_path") or "",
            row.get("doc_name") or "",
            row.get("file_right") or "",
            row.get("doc_code") or "",
            index_status,
        ),
    )
    refresh_general_document_metadata(int(object_id))


def update_subtree_item_type(
    node_id: int,
    item_type_code: int,
    created_by: Optional[int] = None,
) -> None:
    ids = collect_descendants([int(node_id)])
    if not ids:
        return
    placeholders = ",".join(["%s"] * len(ids))
    doc_rows = fetch_all(
        f"""
        SELECT
            tn.doc_id,tn.item_type_code AS old_item_type_code,tn.kb_type,
            ai.index_status
          FROM tree_nodes tn
          LEFT JOIN ai_document_info ai ON ai.object_id=tn.doc_id
         WHERE tn.doc_id IS NOT NULL
           AND tn.node_id IN ({placeholders})
        """,
        tuple(ids),
    )
    execute(
        f"UPDATE tree_nodes SET item_type_code=%s, updated_at=NOW() WHERE node_id IN ({placeholders})",
        tuple([int(item_type_code)] + list(ids)),
    )
    for row in doc_rows or []:
        oid = as_int(row.get("doc_id"))
        if oid is not None:
            ensure_ai_document_info(
                oid,
                index_status=str(row.get("index_status") or "pending").lower(),
            )
            old_code = as_int(row.get("old_item_type_code"), 10) or 10
            new_code = int(item_type_code)
            if (
                old_code != new_code
                and str(row.get("kb_type") or "").strip().lower() == "admin"
                and str(row.get("index_status") or "").strip().lower() == "indexed"
            ):
                enqueue_ai_metadata_job(oid, created_by)
