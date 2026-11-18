from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.deps.auth_dep import get_current_user_id
from app.repositories.common import execute, fetch_all, fetch_one
from app.services.features.document_db import (
    active_group_ids_for_user,
    as_int,
    collect_descendants,
    ensure_ai_document_info,
    enqueue_ai_job,
    enqueue_ai_metadata_job,
    get_kb_entry,
    get_node,
    is_admin,
    node_policy_matrix,
    row_has_document_permission,
    require_kb_read_access,
    require_node_edit_access,
)


def _tree_sort_key(row: Dict[str, Any]) -> Tuple[int, str, int]:
    return (int(row.get("sort_order") or 0), str(row.get("alias") or row.get("name") or "").lower(), int(row["node_id"]))


def _node_type(row: Dict[str, Any]) -> str:
    return "KB_FOLDER" if bool(row.get("is_folder")) else "file"


def _row_to_tree(row: Dict[str, Any], children: List[Dict[str, Any]]) -> Dict[str, Any]:
    is_folder = bool(row.get("is_folder"))
    object_id = row.get("doc_id")
    return {
        "id": row["node_id"],
        "node_id": row["node_id"],
        "parent": row.get("parent_id"),
        "parent_id": row.get("parent_id"),
        "name": row.get("alias") or row.get("name"),
        "original_name": row.get("name"),
        "node_type": _node_type(row),
        "is_folder": is_folder,
        "policy_code": row.get("policy_code"),
        "policy_matrix": node_policy_matrix(row),
        "sort_order": row.get("sort_order") or 0,
        "meta": {
            "doc_id": object_id,
            "object_id": object_id,
            "storage_object_id": object_id,
            "storage_object_name": row.get("doc_name"),
            "storage_object_file_right": row.get("file_right"),
            "storage_object_relative_path": row.get("relative_path"),
            "storage_object_sub_path": row.get("sub_path"),
            "branch_type": str(row.get("branch_type") or "general").upper(),
            "doc_status": row.get("doc_status"),
        },
        "children": children,
    }


def _fetch_rows(kb_root_id: int) -> List[Dict[str, Any]]:
    return fetch_all(
        """
        SELECT
            n.node_id, n.parent_id, n.kb_root_id, n.doc_id, n.name, n.alias,
            n.kb_type, n.kb_owner_user_id, n.is_folder, n.sort_order, n.item_type_code,
            n.created_by, n.created_at, n.updated_at,
            so.doc_name, so.file_right, so.relative_path, so.sub_path, so.branch_type,
            so.upload_mode, so.uploader_user_id, so.doc_status,
            ab.owner_user_id,
            ab.policy_code,
            ab.target_group_id,
            pp.owner_r, pp.owner_w, pp.owner_x,
            pp.group_r, pp.group_w, pp.group_x,
            pp.other_r, pp.other_w, pp.other_x
          FROM tree_nodes n
          LEFT JOIN storage_objects so ON so.object_id = n.doc_id
          LEFT JOIN acl_bindings ab ON ab.node_id = n.node_id
          LEFT JOIN perm_policies pp ON pp.policy_code = ab.policy_code
         WHERE n.kb_root_id=%s
         ORDER BY COALESCE(n.parent_id,0), n.sort_order, n.alias, n.name
        """,
        (int(kb_root_id),),
    )


def get_tree(
    kb_id: int = Query(...),
    group_by: str = Query("none"),
    admin_view: bool = Query(False),
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    require_kb_read_access(user_id, root)
    rows = _fetch_rows(int(root["kb_root_id"]))
    user_is_admin = is_admin(user_id)
    if admin_view and not user_is_admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    use_admin_view = bool(admin_view and user_is_admin)
    group_ids = active_group_ids_for_user(user_id) if not use_admin_view else set()
    visible_rows: List[Dict[str, Any]] = []
    for row in rows or []:
        if bool(row.get("is_folder")):
            visible_rows.append(row)
            continue
        if row_has_document_permission(row, user_id, "r", user_group_ids=group_ids, admin_override=use_admin_view):
            visible_rows.append(row)

    by_parent: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for row in visible_rows:
        by_parent.setdefault(as_int(row.get("parent_id")), []).append(row)

    def build(parent_id: Optional[int]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for row in sorted(by_parent.get(parent_id, []), key=_tree_sort_key):
            children = build(int(row["node_id"]))
            if bool(row.get("is_folder")) and not children:
                continue
            out.append(_row_to_tree(row, children))
        return out

    return {"kb_id": int(root["node_id"]), "nodes": build(int(root["node_id"]))}


def batch_rename(
    items: List[Dict[str, Any]],
    user_id: int = Depends(get_current_user_id),
):
    validated: List[Tuple[int, str, Dict[str, Any]]] = []
    for item in items or []:
        node_id = as_int(item.get("node_id"))
        name = str(item.get("name") or "").strip()
        if node_id is None or not name:
            continue
        row = get_node(node_id)
        require_node_edit_access(user_id, row)
        validated.append((node_id, name, row))
    count = 0
    for node_id, name, row in validated:
        execute("UPDATE tree_nodes SET alias=%s, updated_at=NOW() WHERE node_id=%s", (name, node_id))
        descendant_ids = collect_descendants([node_id]) if bool(row.get("is_folder")) else {node_id}
        if descendant_ids:
            placeholders = ",".join(["%s"] * len(descendant_ids))
            documents = fetch_all(
                f"SELECT doc_id FROM tree_nodes WHERE doc_id IS NOT NULL AND node_id IN ({placeholders})",
                tuple(descendant_ids),
            )
            for document in documents or []:
                object_id = as_int(document.get("doc_id"))
                if object_id is not None:
                    ensure_ai_document_info(object_id)
                    enqueue_ai_metadata_job(object_id, user_id)
        count += 1
    return {"ok": True, "count": count}


def batch_delete(
    node_ids: List[int],
    user_id: int = Depends(get_current_user_id),
):
    ids = [int(x) for x in node_ids or [] if x is not None]
    if not ids:
        return {"ok": True, "count": 0}
    for node_id in ids:
        require_node_edit_access(user_id, get_node(node_id))
    all_ids = collect_descendants(ids)
    if not all_ids:
        return {"ok": True, "count": 0}
    placeholders = ",".join(["%s"] * len(all_ids))
    docs = fetch_all(
        f"SELECT node_id, doc_id FROM tree_nodes WHERE doc_id IS NOT NULL AND node_id IN ({placeholders})",
        tuple(all_ids),
    )
    for doc in docs or []:
        object_id = as_int(doc.get("doc_id"))
        node_id = as_int(doc.get("node_id"))
        if object_id is None or node_id is None:
            continue
        ai = fetch_one("SELECT ai_document_id FROM ai_document_info WHERE object_id=%s", (object_id,))
        execute("UPDATE storage_objects SET doc_status='deleted_pending', db_updated_at=NOW() WHERE object_id=%s", (object_id,))
        execute(
            """
            INSERT INTO document_deletions(
                object_id, node_id, ai_document_id, status, detected_at, remain_until,
                detected_by_user_id, detected_source, reason, created_at, updated_at
            )
            VALUES(%s,%s,%s,'pending',NOW(),NOW() + INTERVAL '7 days',%s,'manual_confirm','manual delete',NOW(),NOW())
            ON CONFLICT (object_id, active_deletion_marker) DO UPDATE SET
                status='pending',
                detected_at=NOW(),
                remain_until=NOW() + INTERVAL '7 days',
                detected_by_user_id=EXCLUDED.detected_by_user_id,
                updated_at=NOW()
            """,
            (object_id, node_id, (ai or {}).get("ai_document_id"), user_id),
        )
        ensure_ai_document_info(object_id, index_status="deleted")
        enqueue_ai_job(object_id, "delete", user_id)
    if not docs:
        execute(f"DELETE FROM tree_nodes WHERE doc_id IS NULL AND node_id IN ({placeholders})", tuple(all_ids))
    return {"ok": True, "count": len(all_ids)}


class NodesReorderIn(BaseModel):
    parent_id: Optional[int] = None
    orders: List[Dict[str, int]] = Field(..., description="[{node_id, sort_order}, ...]")


def reorder_nodes(
    kb_id: int = Query(...),
    body: NodesReorderIn = None,
    user_id: int = Depends(get_current_user_id),
):
    if not body or not body.orders:
        return {"ok": True, "count": 0}
    root = get_kb_entry(kb_id)
    require_node_edit_access(user_id, root)
    if body.parent_id is not None:
        parent = get_node(body.parent_id)
        require_node_edit_access(user_id, parent)
        if as_int(parent.get("kb_root_id")) != as_int(root.get("kb_root_id")):
            raise HTTPException(status_code=403, detail="Parent folder must belong to the selected KB")
    count = 0
    for item in body.orders:
        node_id = as_int(item.get("node_id"))
        sort_order = as_int(item.get("sort_order"))
        if node_id is None or sort_order is None:
            continue
        row = get_node(node_id)
        if not row or as_int(row.get("parent_id")) != body.parent_id:
            continue
        execute("UPDATE tree_nodes SET sort_order=%s, updated_at=NOW() WHERE node_id=%s", (sort_order, node_id))
        count += 1
    return {"ok": True, "count": count}


def list_file_nodes(
    kb_id: int = Query(...),
    parent_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    admin_view: bool = Query(False),
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    require_kb_read_access(user_id, root)
    params: List[Any] = [int(root["kb_root_id"])]
    conds = ["n.kb_root_id=%s", "n.doc_id IS NOT NULL"]
    if parent_id is not None:
        conds.append("n.parent_id=%s")
        params.append(int(parent_id))
    else:
        descendants = collect_descendants([int(root["node_id"])])
        if descendants:
            placeholders = ",".join(["%s"] * len(descendants))
            conds.append(f"n.node_id IN ({placeholders})")
            params.extend(list(descendants))
    if q:
        conds.append("(n.alias ILIKE %s OR n.name ILIKE %s OR so.doc_name ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])

    rows = fetch_all(
        f"""
        SELECT
            n.node_id, n.parent_id, n.name, n.alias, n.sort_order, n.created_at, n.updated_at,
            n.doc_id, n.item_type_code, n.kb_type, n.kb_owner_user_id, n.created_by,
            so.doc_name, so.file_right, so.relative_path, so.sub_path, so.mime_type,
            so.branch_type, so.upload_mode, so.uploader_user_id, so.uploaded_at, so.db_updated_at, so.doc_status,
            dr.rev_no,
            ab.owner_user_id,
            ab.policy_code,
            ab.target_group_id,
            pp.owner_r, pp.owner_w, pp.owner_x,
            pp.group_r, pp.group_w, pp.group_x,
            pp.other_r, pp.other_w, pp.other_x
          FROM tree_nodes n
          JOIN storage_objects so ON so.object_id = n.doc_id
          LEFT JOIN doc_revisions dr
            ON dr.revision_id = (
                SELECT r.revision_id
                  FROM doc_revisions r
                 WHERE r.doc_id = so.object_id
                 ORDER BY r.rev_no DESC, r.revision_id DESC
                 LIMIT 1
            )
          LEFT JOIN acl_bindings ab ON ab.node_id = n.node_id
          LEFT JOIN perm_policies pp ON pp.policy_code = ab.policy_code
         WHERE {" AND ".join(conds)}
         ORDER BY n.sort_order, n.alias, n.name
        """,
        tuple(params),
    )

    items: List[Dict[str, Any]] = []
    user_is_admin = is_admin(user_id)
    if admin_view and not user_is_admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    use_admin_view = bool(admin_view and user_is_admin)
    group_ids = active_group_ids_for_user(user_id) if not use_admin_view else set()
    for row in rows or []:
        if not row_has_document_permission(row, user_id, "r", user_group_ids=group_ids, admin_override=use_admin_view):
            continue
        object_id = row.get("doc_id")
        items.append(
            {
                "node_id": row["node_id"],
                "id": row["node_id"],
                "parent_id": row.get("parent_id"),
                "name": row.get("alias") or row.get("name"),
                "original_name": row.get("name"),
                "branch_type": str(row.get("branch_type") or "general").upper(),
                "storage_scope": "personal"
                if str(row.get("upload_mode") or "").lower() == "personal"
                or str(row.get("kb_type") or "").lower() in {"personal", "person"}
                else "kiwix"
                if str(row.get("branch_type") or "").upper() == "KIWIX"
                else "admin",
                "upload_mode": row.get("upload_mode"),
                "kb_type": row.get("kb_type"),
                "kb_owner_user_id": row.get("kb_owner_user_id"),
                "node_type": "file",
                "is_folder": False,
                "sort_order": row.get("sort_order") or 0,
                "item_type_code": row.get("item_type_code"),
                "created_at": row.get("uploaded_at") or row.get("created_at"),
                "updated_at": row.get("db_updated_at") or row.get("updated_at"),
                "doc_id": object_id,
                "object_id": object_id,
                "storage_object_id": object_id,
                "storage_object_name": row.get("doc_name"),
                "storage_object_file_right": row.get("file_right"),
                "storage_object_relative_path": row.get("relative_path"),
                "storage_object_sub_path": row.get("sub_path"),
                "mime_type": row.get("mime_type"),
                "rev_no": row.get("rev_no"),
                "policy_code": row.get("policy_code"),
                "target_group_id": row.get("target_group_id"),
                "policy_matrix": node_policy_matrix(row),
                "doc_status": row.get("doc_status"),
                "disabled": row.get("doc_status") in {"deleted_pending", "inactive_path_missing"},
            }
        )
    return {"kb_id": kb_id, "parent_id": parent_id, "items": items}
