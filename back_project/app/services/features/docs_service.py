from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import tempfile
from typing import List, Optional, Dict, Any, Set, Literal

from fastapi import UploadFile, File, Form, Depends, HTTPException, Header, Body, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.repositories.common import fetch_all, fetch_one, execute
from app.utils.normalize import to_db_status, to_client_status
from app.core.config import UPLOAD_DIR, SE_KB_ID
from app.deps.auth_dep import get_current_user_id
from app.services.common.naming_service import (
    build_display_name as _build_display_name,
    normalize_folder_slug as _normalize_folder_slug,
)



def _normalize_title(title: str) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())
    return title.lower()


def _extract_text_for_index(path: str, mime_type: Optional[str] = None) -> str:
    if not path:
        return ""
    mt = (mime_type or "").lower()
    if mt.startswith("text/") or path.lower().endswith((".txt", ".md", ".csv")):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    return ""


def _object_basename(object_id: int, original_name: str | None = None) -> str:
    safe_name = (original_name or "").replace(os.sep, "_").strip() or "file"
    return safe_name


def _object_path(object_id: int, original_name: str | None = None) -> str:
    rel = _object_basename(object_id, original_name)
    return os.path.abspath(os.path.join(UPLOAD_DIR, rel))




def _se_kb_rel_path_for_node(node_id: int) -> Optional[str]:
    parts: List[str] = []
    cur = node_id
    safety = 0
    while cur is not None and safety < 256:
        safety += 1
        row = fetch_one(
            "SELECT node_id, parent_id, tree_id, name, is_folder FROM tree_nodes WHERE node_id=%s",
            (cur,),
        )
        if not row:
            break
        if int(row.get("tree_id") or 0) != SE_KB_ID:
            return None
        if row.get("is_folder"):
            parts.append(_normalize_folder_slug(row.get("name") or ""))
        cur = row.get("parent_id")
    parts.reverse()
    if not parts:
        return None
    return "/".join(parts)


def _se_kb_root_slug_for_parent(parent_node_id: Optional[int], tree_id: Optional[int]) -> Optional[str]:
    if tree_id != SE_KB_ID or parent_node_id is None:
        return None
    return _se_kb_rel_path_for_node(parent_node_id)



def _se_kb_root_slug_for_doc(doc_id: int) -> Optional[str]:
    row = fetch_one("SELECT origin_kb_id, origin_node_id FROM doc_master WHERE doc_master_id=%s", (doc_id,))
    if not row:
        return None
    if int(row.get("origin_kb_id") or 0) != SE_KB_ID:
        return None
    origin_node = row.get("origin_node_id")
    if origin_node is None:
        return None
    return _se_kb_rel_path_for_node(int(origin_node))



def _object_save_path(object_id: int, original_name: str | None, subdir: Optional[str] = None) -> str:
    rel = _object_basename(object_id, original_name)
    if subdir:
        rel = os.path.join(subdir, rel)
    return os.path.abspath(os.path.join(UPLOAD_DIR, rel))



def _stored_filename(object_id: int, name: Optional[str], file_right: Optional[str]) -> str:
    return _build_display_name(name, file_right)


def _normalize_rel_prefix(rel: str) -> str:
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    base = os.path.basename(os.path.abspath(UPLOAD_DIR)).lower()
    if base and rel.lower() == base:
        return ""
    prefix = base + "/"
    if base and rel.lower().startswith(prefix):
        return rel[len(prefix):]
    return rel



def _split_rel_path(rel: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not rel:
        return None, None
    rel_norm = _normalize_rel_prefix(rel)
    parts = [p for p in rel_norm.split("/") if p]
    if not parts:
        return None, None
    root = parts[0]
    sub = "/".join(parts[1:]) if len(parts) > 1 else None
    return root, sub


def _join_rel_root_sub(root: Optional[str], sub: Optional[str]) -> Optional[str]:
    root = (root or "").strip().replace("\\", "/").strip("/")
    sub = (sub or "").strip().replace("\\", "/").strip("/")
    if root and sub:
        return f"{root}/{sub}"
    if root:
        return root
    if sub:
        return sub
    return None

def _resolve_storage_file_path(
    rel: Optional[str],
    sub_path: Optional[str],
    object_id: int,
    name: Optional[str],
    file_right: Optional[str],
) -> str:
    stored = _stored_filename(object_id, name, file_right)
    full_rel = _join_rel_root_sub(rel, sub_path)
    if full_rel:
        rel_norm = _normalize_rel_prefix(str(full_rel))
        if rel_norm.lower() == stored.lower() or rel_norm.lower().endswith("/" + stored.lower()):
            return os.path.abspath(os.path.join(UPLOAD_DIR, rel_norm))
        candidate = os.path.abspath(os.path.join(UPLOAD_DIR, rel_norm, stored))
        if os.path.isfile(candidate):
            return candidate
    candidate = _object_path(object_id, _build_display_name(name, file_right))
    if os.path.isfile(candidate):
        return candidate
    legacy = f"object_{object_id}__{_build_display_name(name, file_right)}"
    legacy_path = os.path.abspath(os.path.join(UPLOAD_DIR, legacy))
    return legacy_path


def _to_relative(save_path: str) -> str:
    rel = os.path.relpath(save_path, UPLOAD_DIR)
    rel = rel.replace("\\", "/").lstrip("/").lstrip("\\")
    return rel


def _save_upload_to_temp(upload: UploadFile, use_upload_dir: bool = True) -> tuple[str, str]:
    tmp_dir = None
    if use_upload_dir:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        tmp_dir = UPLOAD_DIR
    with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False) as tmp:
        hasher = hashlib.sha256()
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
            hasher.update(chunk)
        return tmp.name, hasher.hexdigest()


def _doc_branch_type(doc_id: int) -> str:
    row = fetch_one(
        """
        SELECT UPPER(COALESCE(n.branch_type, 'GENERAL')) AS branch_type
          FROM pf_node_props p
          JOIN tree_nodes n ON n.node_id = p.node_id
         WHERE p.doc_master_id=%s
         ORDER BY n.node_id
         LIMIT 1
        """,
        (doc_id,),
    )
    bt = str((row or {}).get("branch_type") or "GENERAL").strip().upper()
    return bt if bt in {"GENERAL", "KIWIX"} else "GENERAL"


def _safe_upload_abs_path(rel_or_abs: Optional[str]) -> Optional[str]:
    if not rel_or_abs:
        return None
    uploads_abs = os.path.abspath(UPLOAD_DIR)
    raw = str(rel_or_abs).strip()
    if os.path.isabs(raw):
        abs_path = os.path.abspath(raw)
    else:
        abs_path = os.path.abspath(os.path.join(UPLOAD_DIR, raw.lstrip("/\\")))
    if abs_path == uploads_abs:
        return None
    if not abs_path.startswith(uploads_abs + os.sep):
        return None
    return abs_path


def _delete_storage_objects(object_ids: List[int]) -> None:
    ids = [int(x) for x in (object_ids or []) if x is not None]
    if not ids:
        return
    ph = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT object_id, name, file_right, relative_path, sub_path
          FROM storage_objects
         WHERE object_id IN ({ph})
        """,
        tuple(ids),
    )
    paths: Set[str] = set()
    for r in rows:
        rel = r.get("relative_path")
        abs_path = _resolve_storage_file_path(rel, r.get("sub_path"), int(r["object_id"]), r.get("name"), r.get("file_right"))
        if abs_path:
            paths.add(abs_path)
    for abs_path in paths:
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except OSError:
            pass
    execute(f"DELETE FROM doc_props WHERE object_id IN ({ph})", tuple(ids))
    execute(f"DELETE FROM storage_objects WHERE object_id IN ({ph})", tuple(ids))


def _ensure_tree_and_parent(tree_id: Optional[int], parent_node_id: Optional[int]) -> None:
    if tree_id is None:
        return
    tree = fetch_one("SELECT tree_id FROM trees WHERE tree_id=%s", (tree_id,))
    if not tree:
        raise HTTPException(400, "Invalid tree_id")
    if parent_node_id is not None:
        parent = fetch_one(
            "SELECT node_id FROM tree_nodes WHERE node_id=%s AND tree_id=%s",
            (parent_node_id, tree_id),
        )
        if not parent:
            raise HTTPException(400, "Invalid parent_node_id for the given tree_id")


def _get_linked_node_id(node_id: int) -> Optional[int]:
    row = fetch_one(
        "SELECT linked_node_id FROM node_links WHERE node_id=%s AND link_type='MIRROR' LIMIT 1",
        (node_id,),
    )
    if row and row.get("linked_node_id") is not None:
        return int(row["linked_node_id"])
    row = fetch_one(
        "SELECT node_id FROM node_links WHERE linked_node_id=%s AND link_type='MIRROR' LIMIT 1",
        (node_id,),
    )
    if row and row.get("node_id") is not None:
        return int(row["node_id"])
    return None


def _link_nodes(a: int, b: int) -> None:
    if not a or not b or a == b:
        return
    execute(
        "INSERT IGNORE INTO node_links(node_id, linked_node_id, link_type) VALUES(%s,%s,'MIRROR')",
        (a, b),
    )
    execute(
        "INSERT IGNORE INTO node_links(node_id, linked_node_id, link_type) VALUES(%s,%s,'MIRROR')",
        (b, a),
    )


def _link_kb_root(a: int, b: int) -> None:
    if not a or not b or a == b:
        return
    execute(
        "INSERT IGNORE INTO node_links(node_id, linked_node_id, link_type) VALUES(%s,%s,'KB_ROOT')",
        (a, b),
    )
    execute(
        "INSERT IGNORE INTO node_links(node_id, linked_node_id, link_type) VALUES(%s,%s,'KB_ROOT')",
        (b, a),
    )


def _kb_root_pair_for_node(node_id: int) -> tuple[Optional[int], Optional[int]]:
    cur = node_id
    while cur is not None:
        row = fetch_one(
            "SELECT linked_node_id FROM node_links WHERE node_id=%s AND link_type='KB_ROOT' LIMIT 1",
            (cur,),
        )
        if row and row.get("linked_node_id") is not None:
            a = int(cur)
            b = int(row["linked_node_id"])
            a_tree = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (a,))
            b_tree = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (b,))
            if a_tree and b_tree:
                if int(a_tree["tree_id"]) == SE_KB_ID:
                    return a, b
                if int(b_tree["tree_id"]) == SE_KB_ID:
                    return b, a
            return None, None
        row = fetch_one(
            "SELECT node_id FROM node_links WHERE linked_node_id=%s AND link_type='KB_ROOT' LIMIT 1",
            (cur,),
        )
        if row and row.get("node_id") is not None:
            a = int(row["node_id"])
            b = int(cur)
            a_tree = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (a,))
            b_tree = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (b,))
            if a_tree and b_tree:
                if int(a_tree["tree_id"]) == SE_KB_ID:
                    return a, b
                if int(b_tree["tree_id"]) == SE_KB_ID:
                    return b, a
            return None, None

        row = fetch_one("SELECT parent_id FROM tree_nodes WHERE node_id=%s", (cur,))
        if not row or row.get("parent_id") is None:
            break
        cur = row["parent_id"]
    return None, None


def _root_node_info(node_id: int) -> Optional[Dict[str, Any]]:
    cur = node_id
    while cur is not None:
        row = fetch_one("SELECT node_id, parent_id, tree_id, name FROM tree_nodes WHERE node_id=%s", (cur,))
        if not row:
            return None
        if row.get("parent_id") is None:
            return row
        cur = row["parent_id"]
    return None


def _ensure_kb_root_pair_for_node(node_id: int) -> tuple[Optional[int], Optional[int]]:
    se_root, kb_root = _kb_root_pair_for_node(node_id)
    if se_root and kb_root:
        return se_root, kb_root

    root = _root_node_info(node_id)
    if not root:
        return None, None
    root_id = int(root["node_id"])
    tree_id = int(root["tree_id"])
    name = root.get("name")
    if not name or SE_KB_ID <= 0:
        return None, None

    if tree_id == SE_KB_ID:
        kb_tree = fetch_one(
            "SELECT tree_id FROM trees WHERE name=%s AND kind='KB' AND kb_type='admin' LIMIT 1",
            (name,),
        )
        if not kb_tree:
            return None, None
        kb_tree_id = int(kb_tree["tree_id"])
        kb_root_row = fetch_one(
            "SELECT node_id FROM tree_nodes WHERE tree_id=%s AND parent_id IS NULL AND name=%s LIMIT 1",
            (kb_tree_id, name),
        )
        if not kb_root_row:
            return None, None
        kb_root_id = int(kb_root_row["node_id"])
        _link_kb_root(root_id, kb_root_id)
        return root_id, kb_root_id

    se_root_row = fetch_one(
        "SELECT node_id FROM tree_nodes WHERE tree_id=%s AND parent_id IS NULL AND name=%s LIMIT 1",
        (SE_KB_ID, name),
    )
    if not se_root_row:
        return None, None
    se_root_id = int(se_root_row["node_id"])
    _link_kb_root(se_root_id, root_id)
    return se_root_id, root_id


def _ensure_mirror_for_folder(node_id: int, user_id: int) -> Optional[int]:
    existing = _get_linked_node_id(node_id)
    if existing:
        return existing
    row = fetch_one(
        "SELECT node_id, parent_id, name, node_type, is_folder FROM tree_nodes WHERE node_id=%s",
        (node_id,),
    )
    if not row:
        return None
    if not row.get("is_folder"):
        return None
    mirror_parent = _mirror_parent_for_new_node(row.get("parent_id"), user_id)
    if mirror_parent is None:
        return None
    mirror_tree = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (mirror_parent,))
    if not mirror_tree:
        return None
    mirror_tree_id = int(mirror_tree["tree_id"])
    existing = fetch_one(
        """
        SELECT node_id
          FROM tree_nodes
         WHERE tree_id=%s AND parent_id <=> %s AND name=%s
         LIMIT 1
        """,
        (mirror_tree_id, mirror_parent, row.get("name")),
    )
    if existing and existing.get("node_id"):
        _link_nodes(int(row["node_id"]), int(existing["node_id"]))
        return int(existing["node_id"])

    mirror_node_id = execute(
        """
        INSERT INTO tree_nodes
            (tree_id, parent_id, name, node_type, is_folder, sort_order, created_by)
        VALUES (%s, %s, %s, %s, 1, 0, %s)
        """,
        (mirror_tree_id, mirror_parent, row.get("name"), row.get("node_type") or "KB_FOLDER", user_id),
        True,
    )
    _link_nodes(int(row["node_id"]), mirror_node_id)
    return mirror_node_id


def _mirror_parent_for_new_node(parent_node_id: Optional[int], user_id: Optional[int] = None) -> Optional[int]:
    if parent_node_id is None:
        return None
    se_root, kb_root = _ensure_kb_root_pair_for_node(parent_node_id)
    if not se_root or not kb_root:
        return None
    if parent_node_id == se_root:
        return kb_root
    if parent_node_id == kb_root:
        return se_root
    linked = _get_linked_node_id(parent_node_id)
    if linked:
        return linked
    if user_id:
        return _ensure_mirror_for_folder(parent_node_id, user_id)
    return None


def _create_tree_file_node(
    *,
    tree_id: Optional[int],
    parent_node_id: Optional[int],
    name: str,
    doc_master_id: int,
    user_id: int,
    alias_name: Optional[str] = None,
    branch_type: Optional[str] = None,
) -> Optional[int]:
    if tree_id is None:
        return None

    _ensure_tree_and_parent(tree_id, parent_node_id)

    bt = (branch_type or "GENERAL").strip().upper()
    if bt not in {"GENERAL", "KIWIX"}:
        bt = "GENERAL"

    node_id = execute(
        """
        INSERT INTO tree_nodes(tree_id, parent_id, is_folder, node_type, name, sort_order, created_by, branch_type)
        VALUES (%s, %s, 0, 'file', %s, 0, %s, %s)
        """,
        (tree_id, parent_node_id, name, user_id, bt),
        True,
    )

    execute(
        """
        INSERT INTO pf_node_props(node_id, doc_master_id, alias_name)
        VALUES(%s,%s,%s)
        ON DUPLICATE KEY UPDATE doc_master_id=VALUES(doc_master_id), alias_name=VALUES(alias_name)
        """,
        (node_id, doc_master_id, alias_name),
    )
    mirror_parent = _mirror_parent_for_new_node(parent_node_id, user_id)
    if mirror_parent is not None:
        mirror_tree = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (mirror_parent,))
        if mirror_tree:
            mirror_tree_id = int(mirror_tree["tree_id"])
            existing = fetch_one(
                """
                SELECT n.node_id
                  FROM tree_nodes n
                  JOIN pf_node_props pfn ON pfn.node_id = n.node_id
                 WHERE n.tree_id=%s
                   AND n.parent_id <=> %s
                   AND pfn.doc_master_id=%s
                 LIMIT 1
                """,
                (mirror_tree_id, mirror_parent, doc_master_id),
            )
            if existing and existing.get("node_id"):
                _link_nodes(node_id, int(existing["node_id"]))
            else:
                mirror_node_id = execute(
                    """
                    INSERT INTO tree_nodes(tree_id, parent_id, is_folder, node_type, name, sort_order, created_by, branch_type)
                    VALUES (%s, %s, 0, 'file', %s, 0, %s, %s)
                    """,
                    (mirror_tree_id, mirror_parent, name, user_id, bt),
                    True,
                )
                execute(
                    """
                    INSERT INTO pf_node_props(node_id, doc_master_id, alias_name)
                    VALUES(%s,%s,%s)
                    ON DUPLICATE KEY UPDATE doc_master_id=VALUES(doc_master_id), alias_name=VALUES(alias_name)
                    """,
                    (mirror_node_id, doc_master_id, alias_name),
                )
                _link_nodes(node_id, mirror_node_id)
    return node_id


def _find_existing_node(doc_master_id: int, tree_id: Optional[int], parent_node_id: Optional[int]) -> Optional[int]:
    if tree_id is None:
        return None
    row = fetch_one(
        """
        SELECT n.node_id
          FROM tree_nodes n
          JOIN pf_node_props pfn ON pfn.node_id = n.node_id
         WHERE n.tree_id=%s
           AND n.parent_id <=> %s
           AND pfn.doc_master_id=%s
         LIMIT 1
        """,
        (tree_id, parent_node_id, doc_master_id),
    )
    if row and row.get("node_id") is not None:
        return int(row["node_id"])
    return None


def _get_latest_active_revision(doc_master_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT revision_id, rev_no, storage_object_id
          FROM doc_revisions
         WHERE doc_master_id=%s AND deleted_at IS NULL
         ORDER BY rev_no DESC
         LIMIT 1
        """,
        (doc_master_id,),
    )


def _ensure_doc_master(doc_master_id: int) -> Dict[str, Any]:
    row = fetch_one(
        """
        SELECT doc_master_id, title, title_norm, content_hash, deleted_at
          FROM doc_master
         WHERE doc_master_id=%s
        """,
        (doc_master_id,),
    )
    if not row or row.get("deleted_at") is not None:
        raise HTTPException(404, "Document not found")
    return row


class RevisionsReorderIn(BaseModel):
    revision_ids: List[int] = Field(..., min_items=1)


class DocDeleteIn(BaseModel):
    mode: Literal["latest", "all"]


def list_docs(
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    kb_id: Optional[int] = Query(None),
    parent_node_id: Optional[int] = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    params: List[Any] = []
    conds: List[str] = ["dm.deleted_at IS NULL"]

    if q:
        conds.append("dm.title LIKE %s")
        params.append(f"%{q}%")

    if status:
        db_status = to_db_status(status)
        if db_status is None:
            raise HTTPException(400, "Invalid status")
        conds.append("dp.status=%s")
        params.append(db_status)

    rows = fetch_all(
        f"""
        SELECT
          dm.doc_master_id,
          dm.title,
          dm.created_at AS created_at,
          dr.storage_object_id,
          dr.rev_no,
          so.mime_type,
          dp.status,
          ab.policy_code,
          CONCAT(IF(pp.owner_r, 'r','-'), IF(pp.owner_w,'w','-'), IF(pp.owner_x,'x','-'))  AS own_perm,
          CONCAT(IF(pp.group_r, 'r','-'), IF(pp.group_w,'w','-'), IF(pp.group_x,'x','-'))  AS grp_perm,
          CONCAT(IF(pp.other_r, 'r','-'), IF(pp.other_w,'w','-'), IF(pp.other_x,'x','-'))  AS oth_perm
        FROM doc_master dm
        LEFT JOIN doc_revisions dr
          ON dr.revision_id = (
            SELECT r.revision_id
              FROM doc_revisions r
             WHERE r.doc_master_id=dm.doc_master_id AND r.deleted_at IS NULL
             ORDER BY r.rev_no DESC
             LIMIT 1
          )
        LEFT JOIN storage_objects so ON so.object_id = dr.storage_object_id
        LEFT JOIN doc_props dp ON dp.object_id = so.object_id
        LEFT JOIN acl_bindings ab
               ON ab.resource_type='STORAGE_OBJECT' AND ab.resource_pk=so.object_id
        LEFT JOIN perm_policies pp
               ON pp.policy_code = ab.policy_code
        WHERE {" AND ".join(conds)}
        ORDER BY dm.created_at DESC
        """,
        tuple(params),
    )

    out: List[Dict[str, Any]] = []
    for r in rows or []:
        out.append({
            "doc_id": r["doc_master_id"],
            "title": r["title"],
            "object_id": r.get("storage_object_id"),
            "rev_no": r.get("rev_no"),
            "mime_type": r.get("mime_type"),
            "status": to_client_status(r.get("status")),
            "policy_code": r.get("policy_code"),
            "policy_matrix": [r["own_perm"], r["grp_perm"], r["oth_perm"]] if r.get("own_perm") else None,
        })
    return out


def get_doc(doc_id: int):
    dm = _ensure_doc_master(doc_id)
    latest = _get_latest_active_revision(doc_id)
    if not latest:
        raise HTTPException(404, "Document not found")
    doc = fetch_one(
        """
        SELECT d.object_id, d.name, p.status, p.doc_type, p.author_name, d.mime_type
          FROM storage_objects d
          LEFT JOIN doc_props p ON p.object_id = d.object_id
         WHERE d.object_id=%s
        """,
        (latest["storage_object_id"],),
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    doc["status"] = to_client_status(doc.get("status"))
    return {
        "doc_id": dm["doc_master_id"],
        "title": dm["title"],
        "object_id": doc["object_id"],
        "rev_no": latest["rev_no"],
        "doc_type": doc.get("doc_type"),
        "author_name": doc.get("author_name"),
        "mime_type": doc.get("mime_type"),
        "status": doc.get("status"),
    }


def list_doc_revisions(doc_id: int):
    _ensure_doc_master(doc_id)
    rows = fetch_all(
        """
        SELECT
            r.revision_id,
            r.rev_no,
            r.storage_object_id,
            r.created_at,
            r.deleted_at,
            so.name AS file_name,
            so.mime_type
        FROM doc_revisions r
        LEFT JOIN storage_objects so ON so.object_id = r.storage_object_id
        WHERE r.doc_master_id=%s
        ORDER BY r.rev_no DESC
        """,
        (doc_id,),
    )
    return {"doc_id": doc_id, "revisions": rows or []}


def reorder_doc_revisions(doc_id: int, body: RevisionsReorderIn):
    _ensure_doc_master(doc_id)
    ids = [int(x) for x in (body.revision_ids or []) if x is not None]
    if not ids:
        return {"ok": True, "count": 0}

    ph = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT revision_id
          FROM doc_revisions
         WHERE doc_master_id=%s AND revision_id IN ({ph}) AND deleted_at IS NULL
        """,
        tuple([doc_id] + ids),
    )
    allowed = [int(r["revision_id"]) for r in (rows or [])]
    allowed_set = set(allowed)
    ordered = [rid for rid in ids if rid in allowed_set]
    if not ordered:
        return {"ok": True, "count": 0}

    max_row = fetch_one(
        """
        SELECT COALESCE(MAX(rev_no), 0) AS max_rev_no
          FROM doc_revisions
         WHERE doc_master_id=%s
        """,
        (doc_id,),
    )
    offset = int((max_row or {}).get("max_rev_no") or 0) + 1000

    # Move existing rev_no out of the way to avoid UNIQUE (doc_master_id, rev_no) conflicts.
    ph = ",".join(["%s"] * len(ordered))
    execute(
        f"""
        UPDATE doc_revisions
           SET rev_no = rev_no + %s
         WHERE doc_master_id=%s AND revision_id IN ({ph})
        """,
        tuple([offset, doc_id] + ordered),
    )

    # Update in a single statement for the final order.
    rev_no = len(ordered)
    cases = []
    params: List[Any] = []
    for rid in ordered:
        cases.append("WHEN %s THEN %s")
        params.extend([rid, rev_no])
        rev_no -= 1
    case_sql = " ".join(cases)
    ph = ",".join(["%s"] * len(ordered))
    params.append(doc_id)
    params.extend(ordered)
    execute(
        f"""
        UPDATE doc_revisions
           SET rev_no = CASE revision_id {case_sql} END
         WHERE doc_master_id=%s AND revision_id IN ({ph})
        """,
        tuple(params),
    )

    return {"ok": True, "count": len(ordered)}


def upload_doc(
    file: UploadFile = File(...),
    tree_id: Optional[int] = Form(None),
    parent_node_id: Optional[int] = Form(None),
    status: Optional[str] = Form("Draft"),
    doc_type: Optional[str] = Form(None),
    branch_type: Optional[str] = Form("GENERAL"),
    user_id: int = Depends(get_current_user_id),
):
    db_status = to_db_status(status) if status else "DRAFT"
    if db_status is None:
        raise HTTPException(400, "Invalid status")

    bt = (branch_type or "GENERAL").strip().upper()
    if bt not in {"GENERAL", "KIWIX"}:
        raise HTTPException(400, "Invalid branch_type")

    _doc_type = (doc_type or "").strip().upper()
    if _doc_type not in {"PDF", "DOC", "PPT", "XLS", "MD", "OTHER"}:
        guessed = (os.path.splitext(file.filename or "")[1] or "").lower()
        if guessed in {".pdf"}:
            _doc_type = "PDF"
        elif guessed in {".doc", ".docx"}:
            _doc_type = "DOC"
        elif guessed in {".ppt", ".pptx"}:
            _doc_type = "PPT"
        elif guessed in {".xls", ".xlsx", ".csv"}:
            _doc_type = "XLS"
        elif guessed in {".md"}:
            _doc_type = "MD"
        else:
            _doc_type = "OTHER"

    if tree_id is not None:
        _ensure_tree_and_parent(tree_id, parent_node_id)

    filename = file.filename or "file"
    base, ext = os.path.splitext(filename)
    if base and ext:
        name_base = base
        file_right = ext.lstrip(".").lower()
    else:
        name_base = filename
        file_right = None
    display_name = _build_display_name(name_base, file_right)
    title_norm = _normalize_title(display_name)

    tmp_path, checksum = _save_upload_to_temp(file, use_upload_dir=(bt != "KIWIX"))

    existing = fetch_one(
        """
        SELECT dm.doc_master_id
          FROM doc_revisions r
          JOIN storage_objects so ON so.object_id = r.storage_object_id
          JOIN doc_master dm ON dm.doc_master_id = r.doc_master_id
         WHERE r.deleted_at IS NULL
           AND dm.deleted_at IS NULL
           AND so.checksum_sha256=%s
         ORDER BY r.rev_no DESC, r.revision_id DESC
         LIMIT 1
        """,
        (checksum,),
    )

    if existing:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        node_id = _find_existing_node(existing["doc_master_id"], tree_id, parent_node_id)
        if node_id is not None:
            raise HTTPException(status_code=409, detail="Duplicate file in this folder")
        node_id = _create_tree_file_node(
            tree_id=tree_id,
            parent_node_id=parent_node_id,
            name=display_name,
            doc_master_id=existing["doc_master_id"],
            user_id=user_id,
            alias_name=None,
            branch_type=bt,
        )
        execute(
            """
            UPDATE doc_master
               SET origin_kb_id=COALESCE(origin_kb_id, %s),
                   origin_node_id=COALESCE(origin_node_id, %s)
             WHERE doc_master_id=%s
            """,
            (tree_id, node_id, existing["doc_master_id"]),
        )
        latest = _get_latest_active_revision(existing["doc_master_id"])
        return {
            "doc_id": existing["doc_master_id"],
            "title": display_name,
            "duplicate": True,
            "tree_node_id": node_id,
            "rev_no": (latest or {}).get("rev_no", 1),
        }

    object_id = execute(
        """
        INSERT INTO storage_objects(org_id, owner_user_id, name, file_right)
        VALUES(%s,%s,%s,%s)
        """,
        (1, user_id, name_base, file_right),
        True
    )

    # KIWIX: no local file mutation on web upload; DB/Web metadata only.
    subdir_full = None if bt == "KIWIX" else _se_kb_root_slug_for_parent(parent_node_id, tree_id)
    root_rel, sub_rel = _split_rel_path(subdir_full)
    if bt == "KIWIX":
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        mime = file.content_type or mimetypes.guess_type(display_name)[0] or "application/octet-stream"
    else:
        save_path = _object_save_path(object_id, display_name, subdir=subdir_full)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        shutil.move(tmp_path, save_path)
        mime = file.content_type or mimetypes.guess_type(save_path)[0] or "application/octet-stream"
    rel = root_rel
    sub = sub_rel
    execute(
        """
        UPDATE storage_objects
           SET mime_type=%s, relative_path=%s, sub_path=%s, checksum_sha256=%s
         WHERE object_id=%s
        """,
        (mime, rel, sub, checksum, object_id)
    )

    execute(
        """
        INSERT INTO doc_props(object_id, doc_type, status)
        VALUES(%s,%s,%s)
        ON DUPLICATE KEY UPDATE doc_type=VALUES(doc_type), status=VALUES(status)
        """,
        (object_id, _doc_type, db_status),
    )

    doc_master_id = execute(
        """
        INSERT INTO doc_master(org_id, title, title_norm, content_hash)
        VALUES(%s,%s,%s,%s)
        """,
        (1, display_name, title_norm, checksum),
        True,
    )

    revision_id = execute(
        """
        INSERT INTO doc_revisions(doc_master_id, rev_no, storage_object_id, created_by)
        VALUES(%s, %s, %s, %s)
        """,
        (doc_master_id, 1, object_id, user_id),
        True,
    )

    execute(
        "UPDATE doc_master SET current_revision_id=%s WHERE doc_master_id=%s",
        (revision_id, doc_master_id),
    )

    node_id = _create_tree_file_node(
        tree_id=tree_id,
        parent_node_id=parent_node_id,
        name=display_name,
        doc_master_id=doc_master_id,
        user_id=user_id,
        alias_name=None,
        branch_type=bt,
    )

    execute(
        """
        UPDATE doc_master
           SET origin_kb_id=COALESCE(origin_kb_id, %s),
               origin_node_id=COALESCE(origin_node_id, %s)
         WHERE doc_master_id=%s
        """,
        (tree_id, node_id, doc_master_id),
    )

    return {
        "doc_id": doc_master_id,
        "title": display_name,
        "status": to_client_status(db_status),
        "doc_type": _doc_type,
        "tree_node_id": node_id,
        "rev_no": 1,
    }


def upload_doc_version(
    doc_id: int,
    file: UploadFile = File(...),
    status: Optional[str] = Form("Draft"),
    doc_type: Optional[str] = Form(None),
    user_id: int = Depends(get_current_user_id),
):
    _ensure_doc_master(doc_id)

    db_status = to_db_status(status) if status else "DRAFT"
    if db_status is None:
        raise HTTPException(400, "Invalid status")

    _doc_type = (doc_type or "").strip().upper()
    if _doc_type not in {"PDF", "DOC", "PPT", "XLS", "MD", "OTHER"}:
        guessed = (os.path.splitext(file.filename or "")[1] or "").lower()
        if guessed in {".pdf"}:
            _doc_type = "PDF"
        elif guessed in {".doc", ".docx"}:
            _doc_type = "DOC"
        elif guessed in {".ppt", ".pptx"}:
            _doc_type = "PPT"
        elif guessed in {".xls", ".xlsx", ".csv"}:
            _doc_type = "XLS"
        elif guessed in {".md"}:
            _doc_type = "MD"
        else:
            _doc_type = "OTHER"

    bt = _doc_branch_type(doc_id)
    tmp_path, checksum = _save_upload_to_temp(file, use_upload_dir=(bt != "KIWIX"))

    dup_global = fetch_one(
        """
        SELECT r.doc_master_id
          FROM doc_revisions r
          JOIN storage_objects so ON so.object_id = r.storage_object_id
         WHERE r.deleted_at IS NULL
           AND so.checksum_sha256=%s
           AND r.doc_master_id <> %s
         LIMIT 1
        """,
        (checksum, doc_id),
    )
    if dup_global:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise HTTPException(status_code=409, detail="Duplicate file exists in another document")

    dup = fetch_one(
        """
        SELECT r.revision_id
          FROM doc_revisions r
          JOIN storage_objects so ON so.object_id = r.storage_object_id
         WHERE r.doc_master_id=%s
           AND r.deleted_at IS NULL
           AND so.checksum_sha256=%s
         LIMIT 1
        """,
        (doc_id, checksum),
    )
    if dup:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise HTTPException(status_code=409, detail="Duplicate file")

    filename = file.filename or "file"
    base, ext = os.path.splitext(filename)
    if base and ext:
        name_base = base
        file_right = ext.lstrip(".").lower()
    else:
        name_base = filename
        file_right = None
    display_name = _build_display_name(name_base, file_right)

    object_id = execute(
        """
        INSERT INTO storage_objects(org_id, owner_user_id, name, file_right)
        VALUES(%s,%s,%s,%s)
        """,
        (1, user_id, name_base, file_right),
        True
    )

    subdir_full = None if bt == "KIWIX" else _se_kb_root_slug_for_doc(doc_id)
    root_rel, sub_rel = _split_rel_path(subdir_full)
    if bt == "KIWIX":
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        mime = file.content_type or mimetypes.guess_type(display_name)[0] or "application/octet-stream"
    else:
        save_path = _object_save_path(object_id, display_name, subdir=subdir_full)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        shutil.move(tmp_path, save_path)
        mime = file.content_type or mimetypes.guess_type(save_path)[0] or "application/octet-stream"
    rel = root_rel
    sub = sub_rel
    execute(
        """
        UPDATE storage_objects
           SET mime_type=%s, relative_path=%s, sub_path=%s, checksum_sha256=%s
         WHERE object_id=%s
        """,
        (mime, rel, sub, checksum, object_id)
    )

    execute(
        """
        INSERT INTO doc_props(object_id, doc_type, status)
        VALUES(%s,%s,%s)
        ON DUPLICATE KEY UPDATE doc_type=VALUES(doc_type), status=VALUES(status)
        """,
        (object_id, _doc_type, db_status),
    )

    row = fetch_one(
        "SELECT COALESCE(MAX(rev_no),0) AS max_no FROM doc_revisions WHERE doc_master_id=%s",
        (doc_id,),
    )
    next_no = int(row["max_no"] or 0) + 1

    revision_id = execute(
        """
        INSERT INTO doc_revisions(doc_master_id, rev_no, storage_object_id, created_by)
        VALUES(%s, %s, %s, %s)
        """,
        (doc_id, next_no, object_id, user_id),
        True,
    )

    execute(
        "UPDATE doc_master SET current_revision_id=%s WHERE doc_master_id=%s",
        (revision_id, doc_id),
    )

    return {
        "doc_id": doc_id,
        "object_id": object_id,
        "rev_no": next_no,
        "status": to_client_status(db_status),
        "doc_type": _doc_type,
    }


def delete_doc(doc_id: int, body: DocDeleteIn, user_id: int = Depends(get_current_user_id)):
    _ensure_doc_master(doc_id)
    mode = (body.mode or "all").lower()

    revs = fetch_all(
        """
        SELECT revision_id, rev_no, storage_object_id
          FROM doc_revisions
         WHERE doc_master_id=%s
         ORDER BY rev_no DESC, revision_id DESC
        """,
        (doc_id,),
    )
    if not revs:
        node_rows = fetch_all("SELECT node_id FROM pf_node_props WHERE doc_master_id=%s", (doc_id,))
        node_ids = [int(r["node_id"]) for r in (node_rows or []) if r.get("node_id") is not None]
        if node_ids:
            ph = ",".join(["%s"] * len(node_ids))
            execute(
                f"DELETE FROM node_links WHERE node_id IN ({ph}) OR linked_node_id IN ({ph})",
                tuple(node_ids + node_ids),
            )
            execute(f"DELETE FROM pf_node_props WHERE node_id IN ({ph})", tuple(node_ids))
            execute(f"DELETE FROM tree_nodes WHERE node_id IN ({ph})", tuple(node_ids))

        title_row = fetch_one("SELECT title FROM doc_master WHERE doc_master_id=%s", (doc_id,))
        title = (title_row or {}).get("title")
        if title:
            orphan_rows = fetch_all(
                """
                SELECT so.object_id
                  FROM storage_objects so
                  LEFT JOIN doc_revisions dr ON dr.storage_object_id = so.object_id
                 WHERE dr.storage_object_id IS NULL
                   AND so.name = %s
                """,
                (title,),
            )
            orphan_ids = [int(r["object_id"]) for r in (orphan_rows or []) if r.get("object_id") is not None]
            _delete_storage_objects(orphan_ids)

        execute("DELETE FROM doc_master WHERE doc_master_id=%s", (doc_id,))
        return {"ok": True, "mode": "all", "deleted_doc_id": doc_id, "deleted_nodes": len(node_ids)}

    if mode == "latest" and len(revs) > 1:
        latest = revs[0]
        next_rev = revs[1]
        execute("DELETE FROM doc_revisions WHERE revision_id=%s", (latest["revision_id"],))
        _delete_storage_objects([latest["storage_object_id"]])
        execute(
            "UPDATE doc_master SET current_revision_id=%s WHERE doc_master_id=%s",
            (next_rev["revision_id"], doc_id),
        )
        return {"ok": True, "mode": "latest", "deleted_revision_id": latest["revision_id"]}

    # delete all (or latest-only when only one revision remains)
    node_rows = fetch_all("SELECT node_id FROM pf_node_props WHERE doc_master_id=%s", (doc_id,))
    node_ids = [int(r["node_id"]) for r in (node_rows or []) if r.get("node_id") is not None]
    if node_ids:
        ph = ",".join(["%s"] * len(node_ids))
        execute(
            f"DELETE FROM node_links WHERE node_id IN ({ph}) OR linked_node_id IN ({ph})",
            tuple(node_ids + node_ids),
        )
        execute(f"DELETE FROM pf_node_props WHERE node_id IN ({ph})", tuple(node_ids))
        execute(f"DELETE FROM tree_nodes WHERE node_id IN ({ph})", tuple(node_ids))

    object_ids = [int(r["storage_object_id"]) for r in revs if r.get("storage_object_id") is not None]
    execute("DELETE FROM doc_revisions WHERE doc_master_id=%s", (doc_id,))
    _delete_storage_objects(object_ids)
    execute("DELETE FROM doc_master WHERE doc_master_id=%s", (doc_id,))

    return {"ok": True, "mode": "all", "deleted_doc_id": doc_id, "deleted_nodes": len(node_ids)}


def update_doc(doc_id: int, payload: Dict[str, Any]):
    _ensure_doc_master(doc_id)

    if "name" in payload:
        title = payload["name"]
        title_norm = _normalize_title(title)
        execute(
            "UPDATE doc_master SET title=%s, title_norm=%s WHERE doc_master_id=%s",
            (title, title_norm, doc_id),
        )
        execute(
            """
            UPDATE tree_nodes tn
            JOIN pf_node_props pfn ON pfn.node_id = tn.node_id
               SET tn.name=%s
             WHERE pfn.doc_master_id=%s AND pfn.alias_name IS NULL
            """,
            (title, doc_id),
        )

    if "status" in payload or "doc_type" in payload:
        latest = _get_latest_active_revision(doc_id)
        if not latest:
            raise HTTPException(404, "Document not found")
        if "status" in payload:
            val = to_db_status(payload["status"])
            if val is None:
                raise HTTPException(400, "Invalid status")
            execute("UPDATE doc_props SET status=%s WHERE object_id=%s", (val, latest["storage_object_id"]))
        if "doc_type" in payload:
            _doc_type = (payload.get("doc_type") or "").strip().upper()
            if _doc_type not in {"PDF", "DOC", "PPT", "XLS", "MD", "OTHER"}:
                raise HTTPException(400, "Invalid doc_type")
            execute("UPDATE doc_props SET doc_type=%s WHERE object_id=%s", (_doc_type, latest["storage_object_id"]))

    latest = _get_latest_active_revision(doc_id)
    if not latest:
        return None
    row = fetch_one(
        """
        SELECT d.object_id, d.name, p.status, p.doc_type, d.mime_type
          FROM storage_objects d
          LEFT JOIN doc_props p ON p.object_id = d.object_id
         WHERE d.object_id=%s
        """,
        (latest["storage_object_id"],),
    )
    if row:
        row["status"] = to_client_status(row.get("status"))
        row["doc_id"] = doc_id
        row["rev_no"] = latest["rev_no"]
    return row


def _read_range(path: str, range_header: str | None, chunk_size: int = 1024 * 512):
    file_size = os.path.getsize(path)

    def _full_iter():
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data

    if not range_header:
        return _full_iter(), 0, file_size - 1, file_size, False

    try:
        _, rng = range_header.split("=", 1)
        start_s, end_s = (rng.split("-", 1) + [""])[:2]
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start < 0:
            raise ValueError()
    except Exception:
        return _full_iter(), 0, file_size - 1, file_size, False

    def _partial_iter():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return _partial_iter(), start, end, file_size, True


def download_doc(
    doc_id: int,
    range: Optional[str] = Header(None),
    as_attachment: bool = False,
):
    _ensure_doc_master(doc_id)
    latest = _get_latest_active_revision(doc_id)
    if not latest:
        raise HTTPException(status_code=404, detail="File not found")

    row = fetch_one(
        "SELECT name, file_right, mime_type, relative_path, sub_path FROM storage_objects WHERE object_id=%s",
        (latest["storage_object_id"],),
    )
    if not row:
        raise HTTPException(status_code=404, detail="File not found")

    path = _resolve_storage_file_path(
        row.get("relative_path"),
        row.get("sub_path"),
        latest["storage_object_id"],
        row.get("name"),
        row.get("file_right"),
    )
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Stored file not found")

    mime = row.get("mime_type") or mimetypes.guess_type(path)[0] or "application/octet-stream"

    iterator, start, end, size, is_partial = _read_range(path, range)

    headers = {"Accept-Ranges": "bytes"}
    status_code = 206 if is_partial else 200
    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    stream = StreamingResponse(iterator, media_type=mime, status_code=status_code, headers=headers)
    if as_attachment:
        disp_name = _build_display_name(row.get("name"), row.get("file_right"))
        stream.headers["Content-Disposition"] = f'attachment; filename="{disp_name}"'
    return stream


def copy_doc(
    doc_id: int,
    new_name: Optional[str] = Body(None, embed=True),
    parent_node_id: Optional[int] = Body(None, embed=True),
    tree_id: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(get_current_user_id),
):
    _ensure_doc_master(doc_id)
    latest = _get_latest_active_revision(doc_id)
    if not latest:
        raise HTTPException(status_code=404, detail="?癒?궚 ?얜챷苑???곸벉")

    row = fetch_one("SELECT name, file_right, mime_type, checksum_sha256, relative_path, sub_path FROM storage_objects WHERE object_id=%s", (latest["storage_object_id"],))
    if not row:
        raise HTTPException(status_code=404, detail="?癒?궚 ?얜챷苑???곸벉")

    src_name = row["name"]
    file_right = row.get("file_right")
    new_name = new_name or f"{src_name}_copy"
    display_name = _build_display_name(new_name, file_right)

    new_object_id = execute(
        "INSERT INTO storage_objects(org_id, owner_user_id, name, file_right) VALUES(%s,%s,%s,%s)",
        (1, user_id, new_name, file_right),
        True
    )

    src_path = _resolve_storage_file_path(
        row.get("relative_path"),
        row.get("sub_path"),
        latest["storage_object_id"],
        src_name,
        file_right,
    )
    subdir_full = _se_kb_root_slug_for_parent(parent_node_id, tree_id)
    root_rel, sub_rel = _split_rel_path(subdir_full)
    dst_path = _object_save_path(new_object_id, display_name, subdir=subdir_full)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(src_path):
        shutil.copy2(src_path, dst_path)

    new_mime = row.get("mime_type") or mimetypes.guess_type(dst_path)[0] or "application/octet-stream"
    new_rel = root_rel
    new_sub = sub_rel
    execute(
        "UPDATE storage_objects SET mime_type=%s, relative_path=%s, sub_path=%s, checksum_sha256=%s WHERE object_id=%s",
        (new_mime, new_rel, new_sub, row.get("checksum_sha256"), new_object_id)
    )

    prop = fetch_one("SELECT status, doc_type, author_name FROM doc_props WHERE object_id=%s", (latest["storage_object_id"],))
    if prop:
        execute(
            """
            INSERT INTO doc_props(object_id, doc_type, status, author_name)
            VALUES(%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE doc_type=VALUES(doc_type), status=VALUES(status), author_name=VALUES(author_name)
            """,
            (new_object_id, prop["doc_type"], prop["status"], prop.get("author_name")),
        )

    title_norm = _normalize_title(display_name)
    doc_master_id = execute(
        """
        INSERT INTO doc_master(org_id, title, title_norm, content_hash)
        VALUES(%s,%s,%s,%s)
        """,
        (1, display_name, title_norm, row.get("checksum_sha256")),
        True,
    )

    revision_id = execute(
        """
        INSERT INTO doc_revisions(doc_master_id, rev_no, storage_object_id, created_by)
        VALUES(%s, %s, %s, %s)
        """,
        (doc_master_id, 1, new_object_id, user_id),
        True,
    )

    execute(
        "UPDATE doc_master SET current_revision_id=%s WHERE doc_master_id=%s",
        (revision_id, doc_master_id),
    )

    node_id = _create_tree_file_node(
        tree_id=tree_id,
        parent_node_id=parent_node_id,
        name=new_name,
        doc_master_id=doc_master_id,
        user_id=user_id,
        alias_name=None,
    )

    execute(
        """
        UPDATE doc_master
           SET origin_kb_id=COALESCE(origin_kb_id, %s),
               origin_node_id=COALESCE(origin_node_id, %s)
         WHERE doc_master_id=%s
        """,
        (tree_id, node_id, doc_master_id),
    )


def move_doc(
    doc_id: int,
    new_parent_node: Optional[int] = Body(None, embed=True),
    new_tree: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(get_current_user_id),
):
    _ensure_doc_master(doc_id)
    node = fetch_one(
        """
        SELECT pfn.node_id, tn.tree_id
          FROM pf_node_props pfn
          JOIN tree_nodes tn ON tn.node_id = pfn.node_id
         WHERE pfn.doc_master_id=%s
         ORDER BY tn.created_at DESC
         LIMIT 1
        """,
        (doc_id,),
    )
    if not node:
        _ensure_tree_and_parent(new_tree, new_parent_node)
        created = _create_tree_file_node(
            tree_id=new_tree,
            parent_node_id=new_parent_node,
            name=(_ensure_doc_master(doc_id) or {}).get("title") or "file",
            doc_master_id=doc_id,
            user_id=user_id,
            alias_name=None,
        )

    if new_tree is None:
        new_tree = node["tree_id"]
    _ensure_tree_and_parent(new_tree, new_parent_node)

    execute(
        "UPDATE tree_nodes SET tree_id=%s, parent_id=%s, updated_at=NOW() WHERE node_id=%s",
        (new_tree, new_parent_node, node["node_id"]),
    )
    return {"doc_id": doc_id, "moved_node_id": node["node_id"], "message": "??猷??袁⑥┷"}


def create_note(
    title: str = Body(..., embed=True),
    content: Optional[str] = Body("", embed=True),
    parent_node_id: Optional[int] = Body(None, embed=True),
    tree_id: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(get_current_user_id),
):
    _ensure_tree_and_parent(tree_id, parent_node_id)

    checksum = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    title_norm = _normalize_title(title)

    existing = fetch_one(
        """
        SELECT doc_master_id
          FROM doc_master
         WHERE title_norm=%s AND content_hash=%s AND deleted_at IS NULL
        """,
        (title_norm, checksum),
    )
    if existing:
        node_id = _create_tree_file_node(
            tree_id=tree_id,
            parent_node_id=parent_node_id,
            name=title,
            doc_master_id=existing["doc_master_id"],
            user_id=user_id,
            alias_name=None,
        )
        return {"doc_id": existing["doc_master_id"], "title": title, "tree_node_id": node_id, "duplicate": True}

    file_right = "txt"
    display_name = _build_display_name(title, file_right)
    object_id = execute(
        "INSERT INTO storage_objects(org_id, owner_user_id, name, file_right) VALUES(%s,%s,%s,%s)",
        (1, user_id, title, file_right),
        True
    )

    subdir_full = _se_kb_root_slug_for_parent(parent_node_id, tree_id)
    root_rel, sub_rel = _split_rel_path(subdir_full)
    save_path = _object_save_path(object_id, display_name, subdir=subdir_full)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(content or "")

    mime = "text/plain"
    rel = root_rel
    sub = sub_rel
    execute(
        "UPDATE storage_objects SET mime_type=%s, relative_path=%s, sub_path=%s, checksum_sha256=%s WHERE object_id=%s",
        (mime, rel, sub, checksum, object_id)
    )

    execute(
        """
        INSERT INTO doc_props(object_id, doc_type, status)
        VALUES(%s,%s,%s)
        ON DUPLICATE KEY UPDATE doc_type=VALUES(doc_type), status=VALUES(status)
        """,
        (object_id, "OTHER", "DRAFT"),
    )

    doc_master_id = execute(
        """
        INSERT INTO doc_master(org_id, title, title_norm, content_hash)
        VALUES(%s,%s,%s,%s)
        """,
        (1, title, title_norm, checksum),
        True,
    )

    revision_id = execute(
        """
        INSERT INTO doc_revisions(doc_master_id, rev_no, storage_object_id, created_by)
        VALUES(%s, %s, %s, %s)
        """,
        (doc_master_id, 1, object_id, user_id),
        True,
    )

    execute(
        "UPDATE doc_master SET current_revision_id=%s WHERE doc_master_id=%s",
        (revision_id, doc_master_id),
    )

    node_id = _create_tree_file_node(
        tree_id=tree_id,
        parent_node_id=parent_node_id,
        name=title,
        doc_master_id=doc_master_id,
        user_id=user_id,
        alias_name=None,
    )

    return {"doc_id": doc_master_id, "title": title, "tree_node_id": node_id, "message": "?紐낅뱜 ??밴쉐 ?袁⑥┷"}


class NodesReorderIn(BaseModel):
    parent: Optional[int] = None
    orders: List[Dict[str, int]] = Field(..., description="[{node_id, sort_order}, ...]")


def reorder_tree_nodes(
    tree_id: int = Body(...),
    body: NodesReorderIn = None,
    user_id: int = Depends(get_current_user_id),
):
    if not body or not body.orders:
        return {"ok": True, "count": 0}

    _ensure_tree_and_parent(tree_id, body.parent)

    count = 0
    for item in body.orders:
        node_id = item.get("node_id")
        sort_order = item.get("sort_order")
        if node_id is None or sort_order is None:
            continue

        row = fetch_one("SELECT tree_id, parent_id FROM tree_nodes WHERE node_id=%s", (node_id,))
        if not row or row["tree_id"] != tree_id:
            continue
        if row["parent_id"] != body.parent:
            continue

        execute(
            "UPDATE tree_nodes SET sort_order=%s, updated_at=NOW() WHERE node_id=%s",
            (sort_order, node_id),
        )
        count += 1

    return {"ok": True, "count": count}


def search_bookmarked_docs(
    q: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    params: List[Any] = [user_id]
    conds: List[str] = [
        "b.user_id=%s",
        "b.doc_master_id IS NOT NULL",
    ]

    if q:
        conds.append("(dm.title LIKE %s OR b.title LIKE %s)")
        params.append(f"%{q}%")
        params.append(f"%{q}%")

    rows = fetch_all(
        f"""
        SELECT
          dm.doc_master_id,
          dm.title,
          dr.storage_object_id,
          dr.rev_no,
          so.file_right AS storage_object_file_right,
          so.relative_path AS storage_object_relative_path,
          so.sub_path AS storage_object_sub_path,
          UPPER(
            COALESCE(
              (
                SELECT n.branch_type
                  FROM pf_node_props p
                  JOIN tree_nodes n ON n.node_id = p.node_id
                 WHERE p.doc_master_id = dm.doc_master_id
                 ORDER BY n.node_id
                 LIMIT 1
              ),
              'GENERAL'
            )
          ) AS branch_type,
          so.created_at AS created_at,
          so.updated_at AS updated_at,
          so.mime_type,
          dp.status,
          ab.policy_code,
          CONCAT(IF(pp.owner_r, 'r','-'), IF(pp.owner_w,'w','-'), IF(pp.owner_x,'x','-'))  AS own_perm,
          CONCAT(IF(pp.group_r, 'r','-'), IF(pp.group_w,'w','-'), IF(pp.group_x,'x','-'))  AS grp_perm,
          CONCAT(IF(pp.other_r, 'r','-'), IF(pp.other_w,'w','-'), IF(pp.other_x,'x','-'))  AS oth_perm
        FROM bookmarks b
        JOIN doc_master dm ON dm.doc_master_id = b.doc_master_id
        LEFT JOIN doc_revisions dr
          ON dr.revision_id = (
            SELECT r.revision_id
              FROM doc_revisions r
             WHERE r.doc_master_id=dm.doc_master_id AND r.deleted_at IS NULL
             ORDER BY r.rev_no DESC
             LIMIT 1
          )
        LEFT JOIN storage_objects so ON so.object_id = dr.storage_object_id
        LEFT JOIN doc_props dp ON dp.object_id = so.object_id
        LEFT JOIN acl_bindings ab
               ON ab.resource_type='STORAGE_OBJECT' AND ab.resource_pk=so.object_id
        LEFT JOIN perm_policies pp
               ON pp.policy_code = ab.policy_code
        WHERE {" AND ".join(conds)}
        ORDER BY dm.title ASC
        """,
        tuple(params),
    )

    out: List[Dict[str, Any]] = []
    for r in rows or []:
        out.append({
            "doc_id": r["doc_master_id"],
            "title": r["title"],
            "object_id": r.get("storage_object_id"),
            "rev_no": r.get("rev_no"),
            "storage_object_file_right": r.get("storage_object_file_right"),
            "storage_object_relative_path": r.get("storage_object_relative_path"),
            "storage_object_sub_path": r.get("storage_object_sub_path"),
            "branch_type": r.get("branch_type"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "mime_type": r.get("mime_type"),
            "status": to_client_status(r.get("status")),
            "policy_code": r.get("policy_code"),
            "policy_matrix": [r["own_perm"], r["grp_perm"], r["oth_perm"]] if r.get("own_perm") else None,
        })
    return out

