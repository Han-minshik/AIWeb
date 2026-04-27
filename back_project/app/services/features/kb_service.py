from __future__ import annotations

import os
import logging
import re
from typing import Dict, Any, List, Optional, Literal, Tuple, Set

from fastapi import HTTPException, Body, Depends, Query, Path
from pydantic import BaseModel, Field

from app.utils.db import fetch_all, fetch_one, execute
from app.deps.auth_dep import get_current_user_id
from app.core.config import (
    DELETE_UPLOADS_ON_RECORD_DELETE,
    KIWIX_FOLDER_ORDER,
    SE_KB_ID,
    UPLOAD_DIR,
)
from app.services.common.naming_service import (
    build_display_name as _build_display_name,
    normalize_folder_slug as _normalize_folder_slug,
)

log = logging.getLogger(__name__)

def _make_in_clause(seq: List[int]) -> tuple[str, tuple]:
    items = list(dict.fromkeys(seq or []))
    if not items:
        return "", tuple()
    return ",".join(["%s"] * len(items)), tuple(items)




def _build_local_folder_path(node_id: int) -> str:
    parts: List[str] = []
    current = node_id
    safety = 0
    while current is not None and safety < 256:
        safety += 1
        row = _fetch_node(current)
        if not row:
            break
        parts.append(_normalize_folder_slug(row.get("name") or ""))
        current = row.get("parent_id")
    parts.reverse()
    return os.path.join(UPLOAD_DIR, *parts)


def _is_kiwix_root_path(node_id: int) -> bool:
    root = _root_node_info(node_id)
    if not root:
        return False
    root_name = str(root.get("name") or "").strip().lower()
    return "kiwix" in root_name



def _is_kiwix_root_name(name: Optional[str]) -> bool:
    root_name = str(name or "").strip().lower()
    return "kiwix" in root_name


def _kiwix_order_rank(name: Optional[str]) -> int:
    up = str(name or "").upper()
    for idx, token in enumerate(KIWIX_FOLDER_ORDER):
        if re.search(rf"(^|[^A-Z0-9]){token}([^A-Z0-9]|$)", up):
            return idx
    return len(KIWIX_FOLDER_ORDER)


def _kiwix_sort_key(row: Dict[str, Any]):
    sort_order = int(row.get("sort_order") or 0)
    name = str(row.get("name") or "").lower()
    if bool(row.get("is_folder")):
        return (0, _kiwix_order_rank(row.get("name")), sort_order, name)
    return (1, sort_order, name)


def _natural_name_key(name: Optional[str]) -> Tuple[Tuple[int, Any], ...]:
    raw = str(name or "").strip().lower()
    parts = re.split(r"(\d+)", raw)
    key: List[Tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _default_tree_sort_key(row: Dict[str, Any]):
    return (
        int(row.get("sort_order") or 0),
        _natural_name_key(row.get("name")),
        str(row.get("name") or "").lower(),
    )


def _detect_storage_path_expr() -> str:
    cols = fetch_all(
        """
        SELECT COLUMN_NAME
          FROM INFORMATION_SCHEMA.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'storage_objects'
        """
    )
    have = {r["COLUMN_NAME"].lower() for r in (cols or [])}
    for c in ["path", "relative_path", "file_path", "rel_path", "uri", "key", "name"]:
        if c in have:
            return "so.`key`" if c == "key" else f"so.{c}"
    if {"dir", "filename"} <= have:
        return "CONCAT(so.dir,'/',so.filename)"
    return "'__NO_PATH__'"


def _object_basename(object_id: int, original_name: Optional[str]) -> str:
    safe_name = (original_name or "").replace(os.sep, "_").strip() or "file"
    return f"object_{object_id}__{safe_name}"



def _normalize_rel_prefix(rel: str) -> str:
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    base = os.path.basename(os.path.abspath(UPLOAD_DIR)).lower()
    if base and rel.lower() == base:
        return ""
    prefix = base + "/"
    if base and rel.lower().startswith(prefix):
        return rel[len(prefix):]
    return rel


def _normalize_under_uploads(rel_path: str) -> Optional[str]:
    if not rel_path:
        return None
    uploads_abs = os.path.abspath(UPLOAD_DIR)
    raw = rel_path.strip()
    if os.path.isabs(raw):
        abs_path = os.path.abspath(raw)
    else:
        safe_rel = raw.lstrip("/\\")
        abs_path = os.path.abspath(os.path.join(UPLOAD_DIR, safe_rel))
    if abs_path == uploads_abs:
        return None
    if not abs_path.startswith(uploads_abs + os.sep):
        return None
    return abs_path


def _delete_files_safely(paths: Set[str]) -> Dict[str, int]:
    stats = {"attempted": 0, "deleted": 0, "skipped_missing": 0, "skipped_outside": 0, "errors": 0}
    for rel in paths:
        stats["attempted"] += 1
        abs_path = _normalize_under_uploads(rel)
        if not abs_path:
            stats["skipped_outside"] += 1
            log.warning("[kb.nodes:bulk] outside uploads skipped: %s", rel)
            continue
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
                stats["deleted"] += 1
                log.info("[kb.nodes:bulk] deleted file: %s", abs_path)
            else:
                stats["skipped_missing"] += 1
                log.info("[kb.nodes:bulk] not found (skip): %s", abs_path)
        except Exception as e:
            stats["errors"] += 1
            log.exception("[kb.nodes:bulk] delete failed: %s (%s)", abs_path, e)
    return stats

def _get_user_org_admin(user_id: int) -> Dict[str, Any]:
    row = fetch_one(
        "SELECT org_id, is_admin FROM users WHERE user_id=%s",
        (user_id,),
    )
    if not row:
        raise HTTPException(status_code=401, detail="?몄쬆 ?ъ슜???놁쓬")
    return {"org_id": int(row["org_id"]), "is_admin": int(row["is_admin"] or 0)}


def _has_perm(user_id: int, node_id: int, mode: str) -> bool:
    row = fetch_one(
        "SELECT fn_acl_can('TREE_NODE', %s, %s, %s) AS ok",
        (node_id, user_id, mode),
    )
    return bool(row and int(row["ok"] or 0) == 1)


def _ensure_kb_tree(kb_id: int, user_id: int) -> Dict[str, Any]:
    u = _get_user_org_admin(user_id)
    tree = fetch_one(
        "SELECT tree_id AS kb_id, org_id, name FROM trees WHERE tree_id=%s AND kind='KB'",
        (kb_id,),
    )
    if not tree:
        raise HTTPException(status_code=404, detail="KB ?몃━ ?놁쓬")
    if int(tree["org_id"]) != u["org_id"]:
        raise HTTPException(status_code=403, detail="?ㅻⅨ 議곗쭅??KB")

    if u["is_admin"]:
        return {"kb_id": int(tree["kb_id"]), "name": tree["name"], "org_id": int(tree["org_id"]), "is_admin": True}

    can_read_any = fetch_one(
        """
        SELECT 1 AS ok
          FROM tree_nodes t
         WHERE t.tree_id=%s
           AND fn_acl_can('TREE_NODE', t.node_id, %s, 'r') = 1
         LIMIT 1
        """,
        (kb_id, user_id),
    )
    if not can_read_any:
        raise HTTPException(status_code=403, detail="?쎄린 沅뚰븳 ?놁쓬")
    return {"kb_id": int(tree["kb_id"]), "name": tree["name"], "org_id": int(tree["org_id"]), "is_admin": False}


def _fetch_node(node_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT node_id, tree_id, parent_id, name, node_type, is_folder, sort_order FROM tree_nodes WHERE node_id=%s",
        (node_id,),
    )


def _is_folder(tree_id: int, node_id: int) -> bool:
    row = fetch_one(
        "SELECT is_folder FROM tree_nodes WHERE tree_id=%s AND node_id=%s",
        (tree_id, node_id),
    )
    return bool(row and int(row["is_folder"] or 0) == 1)


def _is_folder_by_type(node_type: Optional[str]) -> int:
    nt = (node_type or "").upper()
    return 1 if "FOLDER" in nt else 0


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


def _get_kb_root_linked_node_id(node_id: int) -> Optional[int]:
    row = fetch_one(
        "SELECT linked_node_id FROM node_links WHERE node_id=%s AND link_type='KB_ROOT' LIMIT 1",
        (node_id,),
    )
    if row and row.get("linked_node_id") is not None:
        return int(row["linked_node_id"])
    row = fetch_one(
        "SELECT node_id FROM node_links WHERE linked_node_id=%s AND link_type='KB_ROOT' LIMIT 1",
        (node_id,),
    )
    if row and row.get("node_id") is not None:
        return int(row["node_id"])
    return None


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

    # from non-SE KB -> find SE KB root by name
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
            (tree_id, parent_id, name, node_type, is_folder, sort_order, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 1, 0, %s, NOW(), NOW())
        """,
        (mirror_tree_id, mirror_parent, row.get("name"), row.get("node_type") or "KB_FOLDER", user_id),
        True,
    )
    _link_nodes(int(row["node_id"]), mirror_node_id)
    return mirror_node_id


def _mirror_parent_for_new_node(parent_id: Optional[int], user_id: Optional[int] = None) -> Optional[int]:
    if parent_id is None:
        return None
    se_root, kb_root = _ensure_kb_root_pair_for_node(parent_id)
    if not se_root or not kb_root:
        return None
    if parent_id == se_root:
        return kb_root
    if parent_id == kb_root:
        return se_root
    linked = _get_linked_node_id(parent_id)
    if linked:
        return linked
    if user_id:
        return _ensure_mirror_for_folder(parent_id, user_id)
    return None


def _maybe_create_mirror_node(
    *,
    node_id: int,
    parent_id: Optional[int],
    name: str,
    node_type: str,
    is_folder: int,
    user_id: int,
) -> Optional[int]:
    mirror_parent = _mirror_parent_for_new_node(parent_id, user_id)
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
        (mirror_tree_id, mirror_parent, name),
    )
    if existing and existing.get("node_id"):
        _link_nodes(node_id, int(existing["node_id"]))
        return int(existing["node_id"])

    mirror_node_id = execute(
        """
        INSERT INTO tree_nodes
            (tree_id, parent_id, name, node_type, is_folder, sort_order, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, 0, %s, NOW(), NOW())
        """,
        (mirror_tree_id, mirror_parent, name, node_type, is_folder, user_id),
        True,
    )
    _link_nodes(node_id, mirror_node_id)
    return mirror_node_id


def _ensure_kb_from_folder(
    *,
    kb_id: int,
    node_id: int,
    parent_id: Optional[int],
    name: str,
    user_id: int,
) -> Optional[int]:
    if kb_id != SE_KB_ID:
        return None
    if parent_id is not None:
        return None
    if not name:
        return None

    if _get_kb_root_linked_node_id(node_id):
        return None

    tree_row = fetch_one(
        "SELECT tree_id FROM trees WHERE name=%s AND kind='KB' AND kb_type='admin' LIMIT 1",
        (name,),
    )
    if tree_row and tree_row.get("tree_id"):
        kb_tree_id = int(tree_row["tree_id"])
    else:
        se_tree = fetch_one("SELECT org_id FROM trees WHERE tree_id=%s", (kb_id,)) or {}
        org_id = int(se_tree.get("org_id") or 1)
        kb_tree_id = execute(
            """
            INSERT INTO trees(org_id, name, kb_type, is_user_kb, kind)
            VALUES(%s,%s,'admin',0,'KB')
            """,
            (org_id, name),
            True,
        )

    root = fetch_one(
        """
        SELECT node_id
          FROM tree_nodes
         WHERE tree_id=%s AND parent_id IS NULL AND name=%s
         LIMIT 1
        """,
        (kb_tree_id, name),
    )
    if root and root.get("node_id"):
        root_id = int(root["node_id"])
    else:
        root_id = execute(
            """
            INSERT INTO tree_nodes
                (tree_id, parent_id, name, node_type, is_folder, sort_order, created_by, created_at, updated_at)
            VALUES (%s, NULL, %s, 'KB_FOLDER', 1, 0, %s, NOW(), NOW())
            """,
            (kb_tree_id, name, user_id),
            True,
        )

    _link_kb_root(node_id, root_id)
    return kb_tree_id




def _dup_name_exists(
    tree_id: int,
    parent_id: Optional[int],
    name: str,
    exclude_node_id: Optional[int] = None,
) -> bool:
    sql = """
        SELECT node_id FROM tree_nodes
         WHERE tree_id=%s
           AND name=%s
           AND parent_id <=> %s
    """
    params: List[Any] = [tree_id, name, parent_id]
    if exclude_node_id is not None:
        sql += " AND node_id<>%s"
        params.append(exclude_node_id)
    return fetch_one(sql, tuple(params)) is not None


def _collect_descendants(tree_id: int, start_id: int) -> Set[int]:
    descendants: Set[int] = set()
    stack = [start_id]
    while stack:
        nid = stack.pop()
        rows = fetch_all(
            "SELECT node_id FROM tree_nodes WHERE tree_id=%s AND parent_id=%s",
            (tree_id, nid),
        )
        for r in rows:
            cid = r["node_id"]
            if cid not in descendants:
                descendants.add(cid)
                stack.append(cid)
    return descendants


def _collect_doc_master_ids(node_ids: List[int]) -> Set[int]:
    if not node_ids:
        return set()
    ph_nodes, args_nodes = _make_in_clause(sorted(node_ids))
    if not ph_nodes:
        return set()
    rows = fetch_all(
        f"""
        SELECT DISTINCT pfn.doc_master_id
          FROM pf_node_props pfn
         WHERE pfn.node_id IN ({ph_nodes})
           AND pfn.doc_master_id IS NOT NULL
        """,
        args_nodes,
    )
    return {int(r["doc_master_id"]) for r in (rows or [])}


def _collect_orphan_doc_master_ids_for_kb_delete(kb_id: int, node_ids: List[int]) -> Set[int]:
    if not node_ids:
        return set()

    to_delete: Set[int] = set()
    for nid in node_ids:
        n = _fetch_node(nid)
        if n and n["tree_id"] == kb_id:
            to_delete.add(nid)
            to_delete |= _collect_descendants(kb_id, nid)
    if not to_delete:
        return set()

    doc_ids = _collect_doc_master_ids(list(to_delete))
    if not doc_ids:
        return set()

    ph_docs, args_docs = _make_in_clause(sorted(doc_ids))
    ph_nodes, args_nodes = _make_in_clause(sorted(to_delete))

    remain = fetch_all(
        f"""
        SELECT DISTINCT pfn.doc_master_id
          FROM pf_node_props pfn
         WHERE pfn.doc_master_id IN ({ph_docs})
           AND pfn.node_id NOT IN ({ph_nodes})
        """,
        tuple(list(args_docs) + list(args_nodes)),
    )
    still_used = {int(r["doc_master_id"]) for r in (remain or [])}
    return doc_ids - still_used

def _collect_deletable_paths_for_kb_nodes(kb_id: int, node_ids: List[int]) -> Set[str]:
    if not node_ids:
        return set()

    to_delete: Set[int] = set()
    for nid in node_ids:
        n = _fetch_node(nid)
        if n and n["tree_id"] == kb_id:
            to_delete.add(nid)
            to_delete |= _collect_descendants(kb_id, nid)
    if not to_delete:
        return set()

    ph_nodes, args_nodes = _make_in_clause(sorted(to_delete))
    if not ph_nodes:
        return set()

    path_expr = _detect_storage_path_expr()

    rows = fetch_all(
        f"""
        SELECT DISTINCT
               so.object_id,
               so.name AS object_name,
               {path_expr} AS rel_path
          FROM pf_node_props pfn
          JOIN doc_revisions dr ON dr.doc_master_id = pfn.doc_master_id
          JOIN storage_objects so ON so.object_id = dr.storage_object_id
         WHERE pfn.node_id IN ({ph_nodes})
           AND dr.deleted_at IS NULL
        """,
        args_nodes,
    )
    if not rows:
        return set()

    deletable: Set[str] = set()
    for r in rows:
        obj_id = r["object_id"]
        rel_path = r.get("rel_path")
        if not rel_path or rel_path == "__NO_PATH__":
            rel_path = _object_basename(int(obj_id), r.get("object_name"))

        cnt = fetch_one(
            f"""
            SELECT COUNT(*) AS cnt
              FROM doc_revisions dr
              JOIN pf_node_props pfn ON pfn.doc_master_id = dr.doc_master_id
             WHERE dr.storage_object_id = %s
               AND pfn.node_id NOT IN ({ph_nodes})
            """,
            tuple([obj_id]) + args_nodes,
        )
        remaining = int((cnt or {}).get("cnt", 0))
        if remaining == 0 and rel_path:
            deletable.add(rel_path)

    return deletable

def _collect_orphan_object_ids_for_kb_delete(kb_id: int, node_ids: List[int]) -> Set[int]:
    if not node_ids:
        return set()

    to_delete: Set[int] = set()
    for nid in node_ids:
        n = _fetch_node(nid)
        if n and n["tree_id"] == kb_id:
            to_delete.add(nid)
            to_delete |= _collect_descendants(kb_id, nid)
    if not to_delete:
        return set()

    ph_nodes, args_nodes = _make_in_clause(sorted(to_delete))

    rows = fetch_all(
        f"""
        SELECT DISTINCT dr.storage_object_id AS object_id
          FROM pf_node_props pfn
          JOIN doc_revisions dr ON dr.doc_master_id = pfn.doc_master_id
         WHERE pfn.node_id IN ({ph_nodes})
           AND dr.storage_object_id IS NOT NULL
        """,
        args_nodes,
    )
    objs = {int(r["object_id"]) for r in (rows or [])}
    if not objs:
        return set()

    ph_objs, args_objs = _make_in_clause(sorted(objs))

    kb_remain = fetch_all(
        f"""
        SELECT dr.storage_object_id AS object_id
          FROM pf_node_props pfn
          JOIN doc_revisions dr ON dr.doc_master_id = pfn.doc_master_id
         WHERE dr.storage_object_id IN ({ph_objs})
           AND pfn.node_id NOT IN ({ph_nodes})
        GROUP BY dr.storage_object_id
        """,
        tuple(list(args_objs) + list(args_nodes)),
    )
    still_kb = {int(r["object_id"]) for r in (kb_remain or [])}

    proj_remain = fetch_all(
        f"""
        SELECT pn.storage_object_id AS object_id
          FROM project_nodes pn
         WHERE pn.storage_object_id IN ({ph_objs})
        GROUP BY pn.storage_object_id
        """,
        args_objs,
    )
    still_proj = {int(r["object_id"]) for r in (proj_remain or [])}

    return objs - still_kb - still_proj


def _collect_paths_for_object_ids(object_ids: Set[int]) -> Set[str]:
    if not object_ids:
        return set()
    ph, args = _make_in_clause(sorted(object_ids))
    rows = fetch_all(
        f"""
        SELECT object_id, name, file_right, relative_path
          FROM storage_objects
         WHERE object_id IN ({ph})
        """,
        args,
    )
    if not rows:
        return set()
    paths: Set[str] = set()
    for r in rows:
        obj_id = int(r["object_id"])
        display = _build_display_name(r.get("name"), r.get("file_right"))
        stored = display
        rel = _normalize_rel_prefix(r.get("relative_path") or "")
        if rel:
            if rel.lower() == stored.lower() or rel.lower().endswith("/" + stored.lower()):
                paths.add(rel)
            else:
                paths.add(f"{rel}/{stored}")
        else:
            paths.add(stored)
    return paths

class KBTiny(BaseModel):
    kb_id: int
    name: str


class KBNodeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[int] = None
    node_type: str = Field("KB_FOLDER", min_length=1, max_length=64)


class KBNodePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_id: Optional[int] = None
    node_type: Optional[str] = Field(None, min_length=1, max_length=64)


class KBNodesBulkDeleteIn(BaseModel):
    node_ids: List[int] = Field(..., min_items=1)


class KBNodesReorderIn(BaseModel):
    parent_id: Optional[int] = None
    orders: List[Tuple[int, int]] = Field(..., description="[(node_id, sort_order), ...]")

def list_kb_trees(user_id: int = Depends(get_current_user_id)):
    u = fetch_one("SELECT is_admin FROM users WHERE user_id=%s", (user_id,))
    is_admin = bool(u and u["is_admin"])

    if is_admin:
        rows = fetch_all(
            """
            SELECT tree_id AS kb_id, name
              FROM trees
             WHERE kind='KB' AND kb_type='admin'
             ORDER BY (name='SE KB') DESC, name
            """
        )
    else:
        rows = fetch_all(
            """
            SELECT tree_id AS kb_id, name
              FROM trees
             WHERE kind='KB'
               AND kb_type='admin'
               AND is_user_kb=0
               AND org_id=(SELECT org_id FROM users WHERE user_id=%s)
             ORDER BY (name='SE KB') DESC, name
            """,
            (user_id,),
        )
    return rows


def get_kb_tree(
    kb_id: int = Path(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    t = fetch_one(
        "SELECT is_user_kb, owner_user_id, org_id, name FROM trees WHERE tree_id=%s AND kb_type = 'admin'",
        (kb_id,),
    )
    if not t:
        raise HTTPException(status_code=404, detail="KB ?몃━ ?놁쓬")

    is_user_kb = bool(t.get("is_user_kb", 0))
    owner_user_id = t.get("owner_user_id")

    u = fetch_one("SELECT org_id, is_admin FROM users WHERE user_id=%s", (user_id,))
    if not u:
        raise HTTPException(status_code=401, detail="?몄쬆 ?ъ슜???놁쓬")

    is_admin = bool(u.get("is_admin", 0))

    if is_user_kb:
        if not (user_id == owner_user_id or is_admin):
            raise HTTPException(status_code=403, detail="媛쒖씤??KB ?묎렐 遺덇?")
    else:
        _ = _ensure_kb_tree(kb_id, user_id)

    if (not is_user_kb) or is_admin or (is_user_kb and user_id == owner_user_id):
        rows = fetch_all(
            """
            SELECT 
                n.node_id,
                n.parent_id,
                n.name,
                n.node_type,
                n.is_folder,
                n.sort_order,
                pfn.doc_master_id          AS doc_master_id,
                dr.storage_object_id       AS storage_object_id,
                so.name                    AS storage_object_name,
                so.file_right              AS storage_object_file_right,
                so.relative_path           AS storage_object_relative_path,
                so.sub_path                AS storage_object_sub_path
              FROM tree_nodes n
              LEFT JOIN pf_node_props pfn ON pfn.node_id = n.node_id
              LEFT JOIN doc_revisions dr
                ON dr.revision_id = (
                  SELECT r.revision_id
                    FROM doc_revisions r
                   WHERE r.doc_master_id=pfn.doc_master_id AND r.deleted_at IS NULL
                   ORDER BY r.rev_no DESC
                   LIMIT 1
                )
              LEFT JOIN storage_objects so ON so.object_id = dr.storage_object_id
             WHERE n.tree_id=%s
               AND (n.is_folder = 1 OR pfn.doc_master_id IS NOT NULL)
             ORDER BY COALESCE(n.parent_id,0), n.sort_order, n.name
            """,
            (kb_id,),
        )
    else:
        rows = fetch_all(
            """
            SELECT 
                n.node_id,
                n.parent_id,
                n.name,
                n.node_type,
                n.is_folder,
                n.sort_order,
                pfn.doc_master_id          AS doc_master_id,
                dr.storage_object_id       AS storage_object_id,
                so.name                    AS storage_object_name,
                so.file_right              AS storage_object_file_right,
                so.relative_path           AS storage_object_relative_path,
                so.sub_path                AS storage_object_sub_path
              FROM tree_nodes n
              LEFT JOIN pf_node_props pfn ON pfn.node_id = n.node_id
              LEFT JOIN doc_revisions dr
                ON dr.revision_id = (
                  SELECT r.revision_id
                    FROM doc_revisions r
                   WHERE r.doc_master_id=pfn.doc_master_id AND r.deleted_at IS NULL
                   ORDER BY r.rev_no DESC
                   LIMIT 1
                )
              LEFT JOIN storage_objects so ON so.object_id = dr.storage_object_id
             WHERE n.tree_id=%s
               AND fn_acl_can('TREE_NODE', n.node_id, %s, 'r') = 1
               AND (n.is_folder = 1 OR pfn.doc_master_id IS NOT NULL)
             ORDER BY COALESCE(n.parent_id,0), n.sort_order, n.name
            """,
            (kb_id, user_id),
        )

    by_parent: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for r in rows:
        by_parent.setdefault(r.get("parent_id"), []).append(r)

    def to_tree(pid: Optional[int], in_kiwix: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        siblings = by_parent.get(pid, [])
        if in_kiwix:
            siblings = sorted(siblings, key=_kiwix_sort_key)
        else:
            siblings = sorted(siblings, key=_default_tree_sort_key)
        for r in siblings:
            child_in_kiwix = in_kiwix or _is_kiwix_root_name(r.get("name"))
            children = to_tree(r["node_id"], child_in_kiwix)
            out.append(
                {
                    "id": r["node_id"],
                    "parent": r["parent_id"],
                    "name": r["name"],
                    "kind": r["node_type"],
                    "node_type": r["node_type"],
                    "is_folder": bool(r.get("is_folder")),
                    "sort_order": r.get("sort_order") or 0,
                    "meta": {
                        "doc_id": r.get("doc_master_id"),
                        "storage_object_id": r.get("storage_object_id"),
                        "storage_object_name": r.get("storage_object_name"),
                        "storage_object_file_right": r.get("storage_object_file_right"),
                        "storage_object_relative_path": r.get("storage_object_relative_path"),
                        "storage_object_sub_path": r.get("storage_object_sub_path"),
                    },
                    "children": children,
                }
            )
        return out

    return {
        "kb_id": kb_id,
        "kb_name": t["name"],
        "is_user_kb": is_user_kb,
        "nodes": to_tree(None),
    }


def create_kb_node(
    kb_id: int,
    data: KBNodeIn,
    user_id: int = Depends(get_current_user_id),
):
    info = _ensure_kb_tree(kb_id, user_id)

    if data.parent_id is not None:
        parent = _fetch_node(data.parent_id)
        if not parent or parent["tree_id"] != kb_id:
            raise HTTPException(status_code=404, detail="遺紐??몃뱶 ?놁쓬")
        if not _is_folder(kb_id, parent["node_id"]):
            raise HTTPException(status_code=400, detail="遺紐⑤뒗 ?대뜑?ъ빞 ?⑸땲??")
        if not info["is_admin"] and not _has_perm(user_id, parent["node_id"], "w"):
            raise HTTPException(status_code=403, detail="?곌린 沅뚰븳 ?놁쓬")
    else:
        if not info["is_admin"]:
            raise HTTPException(status_code=403, detail="猷⑦듃 ?섏쐞 異붽???愿由ъ옄留??덉슜")

    if _dup_name_exists(kb_id, data.parent_id, data.name):
        raise HTTPException(status_code=409, detail="?숈씪???대쫫???대? 議댁옱?⑸땲??")

    node_type = (data.node_type or "KB_FOLDER").upper()
    is_folder_val = _is_folder_by_type(node_type)

    node_id = execute(
        """
        INSERT INTO tree_nodes
            (tree_id, parent_id, name, node_type, is_folder, sort_order, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, 0, %s, NOW(), NOW())
        """,
        (kb_id, data.parent_id, data.name, node_type, is_folder_val, user_id),
        True,
    )
    _ensure_kb_from_folder(
        kb_id=kb_id,
        node_id=node_id,
        parent_id=data.parent_id,
        name=data.name,
        user_id=user_id,
    )
    _maybe_create_mirror_node(
        node_id=node_id,
        parent_id=data.parent_id,
        name=data.name,
        node_type=node_type,
        is_folder=is_folder_val,
        user_id=user_id,
    )

    resp = {"node_id": node_id, "message": "?앹꽦"}
    if kb_id == SE_KB_ID and is_folder_val and not _is_kiwix_root_path(node_id):
        local_dir = _build_local_folder_path(node_id)
        try:
            os.makedirs(local_dir, exist_ok=True)
        except Exception as e:
            log.warning("[kb] failed to create local folder %s: %s", local_dir, e)
            resp["fs_warning"] = f"Failed to create local folder: {local_dir}"
    return resp


def patch_kb_node(
    node_id: int,
    data: KBNodePatch,
    user_id: int = Depends(get_current_user_id),
):
    row = _fetch_node(node_id)
    if not row:
        raise HTTPException(status_code=404, detail="?몃뱶 ?놁쓬")

    info = _ensure_kb_tree(row["tree_id"], user_id)
    if not info["is_admin"] and not _has_perm(user_id, node_id, "w"):
        raise HTTPException(status_code=403, detail="?곌린 沅뚰븳 ?놁쓬")

    tree_id = row["tree_id"]
    old_parent = row["parent_id"]
    old_name = row["name"]

    provided_parent = "parent_id" in data.model_fields_set
    provided_node_type = "node_type" in data.model_fields_set

    new_parent = old_parent if not provided_parent else data.parent_id
    new_name = old_name if data.name is None else data.name

    sets: List[str] = []
    params: List[Any] = []

    if provided_parent:
        if new_parent == node_id:
            raise HTTPException(status_code=400, detail="遺紐⑤? ?먭린 ?먯떊?쇰줈 ?ㅼ젙?????놁뒿?덈떎.")
        if new_parent is not None:
            parent = _fetch_node(new_parent)
            if not parent or parent["tree_id"] != tree_id:
                raise HTTPException(status_code=404, detail="遺紐??몃뱶 ?놁쓬")
            if not _is_folder(tree_id, new_parent):
                raise HTTPException(status_code=400, detail="遺紐⑤뒗 ?대뜑?ъ빞 ?⑸땲??")
            descendants = _collect_descendants(tree_id, node_id)
            if new_parent in descendants:
                raise HTTPException(status_code=400, detail="遺紐⑤? ?먯넀?쇰줈 ?ㅼ젙?????놁뒿?덈떎.")
            if not info["is_admin"] and not _has_perm(user_id, new_parent, "w"):
                raise HTTPException(status_code=403, detail="??遺紐⑥뿉 ????곌린 沅뚰븳 ?놁쓬")
        sets.append("parent_id=%s")
        params.append(new_parent)

    if data.name is not None or provided_parent:
        if _dup_name_exists(tree_id, new_parent, new_name, exclude_node_id=node_id):
            raise HTTPException(status_code=409, detail="?숈씪???대쫫???대? 議댁옱?⑸땲??")

    if data.name is not None:
        sets.append("name=%s")
        params.append(new_name)

    if provided_node_type and data.node_type is not None:
        new_node_type = (data.node_type or "").upper()
        sets.append("node_type=%s")
        params.append(new_node_type)
        sets.append("is_folder=%s")
        params.append(_is_folder_by_type(new_node_type))

    if not sets:
        return {"message": "蹂寃??놁쓬"}

    sql = f"UPDATE tree_nodes SET {', '.join(sets)}, updated_at=NOW() WHERE node_id=%s"
    params.append(node_id)
    execute(sql, tuple(params))

    mirror_id = _get_linked_node_id(node_id)
    if mirror_id:
        mirror_sets: List[str] = []
        mirror_params: List[Any] = []
        if data.name is not None:
            mirror_sets.append("name=%s")
            mirror_params.append(new_name)
        if provided_parent:
            mirror_parent = _mirror_parent_for_new_node(new_parent, user_id)
            if new_parent is None or mirror_parent is not None:
                mirror_sets.append("parent_id=%s")
                mirror_params.append(mirror_parent)
        if provided_node_type and data.node_type is not None:
            mirror_sets.append("node_type=%s")
            mirror_params.append(new_node_type)
            mirror_sets.append("is_folder=%s")
            mirror_params.append(_is_folder_by_type(new_node_type))
        if mirror_sets:
            mirror_params.append(mirror_id)
            execute(
                f"UPDATE tree_nodes SET {', '.join(mirror_sets)}, updated_at=NOW() WHERE node_id=%s",
                tuple(mirror_params),
            )

    if row["tree_id"] == SE_KB_ID and _is_folder(row["tree_id"], node_id):
        kb_root_id = _get_kb_root_linked_node_id(node_id)
        if kb_root_id:
            kb_tree = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (kb_root_id,)) or {}
            kb_tree_id = kb_tree.get("tree_id")
            if kb_tree_id:
                if data.name is not None:
                    execute("UPDATE trees SET name=%s WHERE tree_id=%s", (new_name, kb_tree_id))
                    execute(
                        "UPDATE tree_nodes SET name=%s, updated_at=NOW() WHERE node_id=%s",
                        (new_name, kb_root_id),
                    )
    return {"message": "?낅뜲?댄듃 ?꾨즺"}

def _bulk_delete_nodes(kb_id: int, node_ids: List[int], user_id: int) -> Tuple[int, List[str]]:
    info = _ensure_kb_tree(kb_id, user_id)
    ids = list(dict.fromkeys(node_ids or []))
    if not ids:
        return 0, []

    to_delete: Set[int] = set()
    for nid in ids:
        n = _fetch_node(nid)
        if n and n["tree_id"] == kb_id:
            if info["is_admin"] or _has_perm(user_id, nid, "w"):
                to_delete.add(nid)
                to_delete |= _collect_descendants(kb_id, nid)

    if not to_delete:
        return 0, []

    ph_link, args_link = _make_in_clause(sorted(to_delete))
    if ph_link:
        linked_rows = fetch_all(
            f"""
            SELECT linked_node_id AS nid
              FROM node_links
             WHERE link_type='MIRROR' AND node_id IN ({ph_link})
            UNION
            SELECT node_id AS nid
              FROM node_links
             WHERE link_type='MIRROR' AND linked_node_id IN ({ph_link})
            """,
            tuple(list(args_link) + list(args_link)),
        )
        for r in linked_rows or []:
            try:
                to_delete.add(int(r["nid"]))
            except Exception:
                continue

        kb_link_rows = fetch_all(
            f"""
            SELECT node_id, linked_node_id
              FROM node_links
             WHERE link_type='KB_ROOT'
               AND (node_id IN ({ph_link}) OR linked_node_id IN ({ph_link}))
            """,
            tuple(list(args_link) + list(args_link)),
        )
        kb_tree_ids: Set[int] = set()
        for r in kb_link_rows or []:
            a = r.get("node_id")
            b = r.get("linked_node_id")
            if a is None or b is None:
                continue
            a_row = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (a,))
            b_row = fetch_one("SELECT tree_id FROM tree_nodes WHERE node_id=%s", (b,))
            if not a_row or not b_row:
                continue
            a_tree = int(a_row["tree_id"])
            b_tree = int(b_row["tree_id"])
            if a_tree == SE_KB_ID:
                kb_tree_ids.add(b_tree)
            elif b_tree == SE_KB_ID:
                kb_tree_ids.add(a_tree)

        if kb_tree_ids:
            ph_kb, args_kb = _make_in_clause(sorted(kb_tree_ids))
            execute(f"DELETE FROM tree_nodes WHERE tree_id IN ({ph_kb})", args_kb)
            execute(f"DELETE FROM trees WHERE tree_id IN ({ph_kb})", args_kb)

    orphan_docs = _collect_orphan_doc_master_ids_for_kb_delete(kb_id, list(to_delete))
    orphan_objects = _collect_orphan_object_ids_for_kb_delete(kb_id, list(to_delete))
    deletable_paths = _collect_paths_for_object_ids(orphan_objects)

    placeholders = ", ".join(["%s"] * len(to_delete))

    execute(f"DELETE FROM pf_node_props WHERE node_id IN ({placeholders})", tuple(to_delete))
    execute(f"DELETE FROM tree_nodes   WHERE node_id IN ({placeholders})", tuple(to_delete))

    if orphan_docs:
        ph_d, args_d = _make_in_clause(sorted(orphan_docs))
        execute(f"DELETE FROM doc_revisions WHERE doc_master_id IN ({ph_d})", args_d)
        execute(f"DELETE FROM doc_master WHERE doc_master_id IN ({ph_d})", args_d)

    if orphan_objects:
        ph_o, args_o = _make_in_clause(sorted(orphan_objects))
        execute(f"DELETE FROM doc_props WHERE object_id IN ({ph_o})", args_o)
        execute(f"DELETE FROM storage_objects WHERE object_id IN ({ph_o})", args_o)

    removed: List[str] = []
    if DELETE_UPLOADS_ON_RECORD_DELETE and deletable_paths:
        stats = _delete_files_safely(deletable_paths)
        removed = sorted(list(deletable_paths))

    return len(to_delete), removed


def delete_kb_nodes_bulk(
    kb_id: int,
    body: KBNodesBulkDeleteIn,
    user_id: int = Depends(get_current_user_id),
):
    deleted, removed = _bulk_delete_nodes(kb_id, body.node_ids, user_id)
    return {"deleted": deleted, "files_removed": removed}


def delete_kb_nodes_bulk_safe(
    kb_id: int,
    body: KBNodesBulkDeleteIn,
    user_id: int = Depends(get_current_user_id),
):
    deleted, removed = _bulk_delete_nodes(kb_id, body.node_ids, user_id)
    return {"deleted": deleted, "files_removed": removed}


def delete_kb_nodes_bulk_post(
    kb_id: int,
    body: KBNodesBulkDeleteIn,
    user_id: int = Depends(get_current_user_id),
):
    deleted, removed = _bulk_delete_nodes(kb_id, body.node_ids, user_id)
    return {"deleted": deleted, "files_removed": removed}


def reorder_kb_nodes(
    kb_id: int,
    body: KBNodesReorderIn,
    user_id: int = Depends(get_current_user_id),
):
    info = _ensure_kb_tree(kb_id, user_id)
    if not body.orders:
        return {"updated": 0}

    count = 0
    for node_id, sort_order in body.orders:
        row = _fetch_node(node_id)
        if not row or row["tree_id"] != kb_id:
            continue
        if row["parent_id"] != body.parent_id:
            continue
        if not info["is_admin"] and not _has_perm(user_id, node_id, "w"):
            continue
        execute(
            "UPDATE tree_nodes SET sort_order=%s, updated_at=NOW() WHERE node_id=%s",
            (sort_order, node_id),
        )
        count += 1

    return {"updated": count}

def _get_table_columns(table: str) -> Set[str]:
    rows = fetch_all(
        """
        SELECT COLUMN_NAME
          FROM INFORMATION_SCHEMA.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = %s
        """,
        (table,),
    )
    return {r["COLUMN_NAME"].lower() for r in (rows or [])}

class KbCreateIn(BaseModel):
    name: str
    kb_type: Optional[str] = "admin"
    is_user_kb: int = 0
    kind: Optional[str] = "KB"

def create_kb_tree(
    body: KbCreateIn,
    user_id: int = Depends(get_current_user_id),
):
    u = _get_user_org_admin(user_id)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name ?꾩슂")

    have = _get_table_columns("trees")

    columns: List[str] = []
    placeholders: List[str] = []
    args: List[Any] = []

    if "org_id" in have:
        columns.append("org_id")
        placeholders.append("%s")
        args.append(u["org_id"])

    columns.extend(["name", "kb_type", "is_user_kb", "kind"])
    placeholders.extend(["%s", "%s", "%s", "%s"])
    args.extend([name, body.kb_type, body.is_user_kb, body.kind])

    if "created_at" in have:
        columns.append("created_at")
        placeholders.append("NOW()")
    if "updated_at" in have:
        columns.append("updated_at")
        placeholders.append("NOW()")

    sql = f"INSERT INTO trees ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
    kb_id = execute(sql, tuple(args), True)

    return {"kb_id": kb_id, "name": name, "kb_type": body.kb_type, "is_user_kb": body.is_user_kb, "kind": body.kind}

def delete_kb_tree(
    kb_id: int,
    user_id: int = Depends(get_current_user_id),
):
    info = _ensure_kb_tree(kb_id, user_id)
    if not info["is_admin"]:
        raise HTTPException(status_code=403, detail="Only admins can delete KB trees")
    execute("DELETE FROM trees WHERE tree_id=%s", (kb_id,))
    return {"ok": True, "kb_id": kb_id}

