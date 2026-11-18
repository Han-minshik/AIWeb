from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Set

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.deps.auth_dep import get_current_user_id
from app.repositories.common import execute, fetch_all, fetch_one
from app.services.features.document_db import has_object_permission, require_object_permission

BookmarkNodeType = Literal["FOLDER", "ITEM"]


class BookmarkIn(BaseModel):
    storage_id: Optional[int] = None
    title: Optional[str] = None
    parent_id: Optional[int] = None
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    preferred_node_id: Optional[int] = None


class BookmarkDeleteIn(BaseModel):
    storage_id: Optional[int] = None
    target_type: Optional[str] = None
    target_id: Optional[int] = None


class BookmarkNodeIn(BaseModel):
    name: Optional[str] = None
    type: BookmarkNodeType
    parent_id: Optional[int] = None
    bookmark_id: Optional[int] = None
    sort_order: Optional[int] = 0


class BookmarkNodePatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None


class BookmarkNodesBulkDelete(BaseModel):
    node_ids: List[int]


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _resolve_object_id(payload: Any) -> Optional[int]:
    storage_id = _as_int(getattr(payload, "storage_id", None))
    if storage_id is not None:
        return storage_id
    return _as_int(getattr(payload, "target_id", None))


def _resolve_doc_node_id(object_id: int) -> Optional[int]:
    row = fetch_one(
        "SELECT node_id FROM tree_nodes WHERE doc_id=%s ORDER BY node_id LIMIT 1",
        (int(object_id),),
    )
    return _as_int((row or {}).get("node_id"))


def _fetch_bookmark_node(node_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT bookmark_node_id, user_id, parent_id, bookmark_id, is_folder, name, sort_order
          FROM bookmark_nodes
         WHERE bookmark_node_id=%s
        """,
        (int(node_id),),
    )


def _fetch_bookmark(bookmark_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT b.bookmark_id, b.user_id, b.node_id, b.title, tn.doc_id AS object_id
          FROM bookmarks b
          JOIN tree_nodes tn ON tn.node_id = b.node_id
         WHERE b.bookmark_id=%s
        """,
        (int(bookmark_id),),
    )


def _assert_parent_folder(parent_id: Optional[int], user_id: int) -> None:
    if parent_id is None:
        return
    parent = _fetch_bookmark_node(parent_id)
    if not parent or int(parent["user_id"]) != int(user_id):
        raise HTTPException(status_code=404, detail="Parent node not found")
    if not bool(parent.get("is_folder")):
        raise HTTPException(status_code=400, detail="Parent node must be a folder")


def _collect_descendants(user_id: int, start_id: int) -> Set[int]:
    out: Set[int] = set()
    stack = [int(start_id)]
    while stack:
        nid = stack.pop()
        if nid in out:
            continue
        out.add(nid)
        rows = fetch_all("SELECT bookmark_node_id FROM bookmark_nodes WHERE user_id=%s AND parent_id=%s", (int(user_id), nid))
        for row in rows or []:
            cid = _as_int(row.get("bookmark_node_id"))
            if cid is not None and cid not in out:
                stack.append(cid)
    return out


def _storage_title(object_id: int) -> str:
    row = fetch_one("SELECT doc_name, file_right FROM storage_objects WHERE object_id=%s", (int(object_id),))
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return str(row.get("doc_name") or f"Object {object_id}")


def add_bookmark(bm: BookmarkIn, user_id: int = Depends(get_current_user_id)):
    object_id = _resolve_object_id(bm)
    if object_id is None:
        raise HTTPException(status_code=400, detail="storage_id is required")
    doc_node_id = _resolve_doc_node_id(object_id)
    if doc_node_id is None:
        raise HTTPException(status_code=404, detail="Document node not found")

    require_object_permission(user_id, object_id, "r")
    parent_id = bm.parent_id if bm.parent_id is not None else bm.preferred_node_id
    _assert_parent_folder(parent_id, user_id)
    title = str(bm.title or "").strip() or _storage_title(object_id)

    existing = fetch_one(
        "SELECT bookmark_id FROM bookmarks WHERE user_id=%s AND node_id=%s",
        (int(user_id), int(doc_node_id)),
    )
    if existing:
        bookmark_id = int(existing["bookmark_id"])
        execute("UPDATE bookmarks SET title=%s WHERE bookmark_id=%s", (title, bookmark_id))
    else:
        bookmark_id = execute(
            "INSERT INTO bookmarks(user_id, node_id, title, created_at) VALUES(%s,%s,%s,NOW())",
            (int(user_id), int(doc_node_id), title),
            True,
        )

    node = fetch_one(
        "SELECT bookmark_node_id FROM bookmark_nodes WHERE user_id=%s AND bookmark_id=%s LIMIT 1",
        (int(user_id), int(bookmark_id)),
    )
    if node:
        execute(
            "UPDATE bookmark_nodes SET parent_id=%s, name=%s, updated_at=NOW() WHERE bookmark_node_id=%s",
            (parent_id, title, int(node["bookmark_node_id"])),
        )
    else:
        execute(
            """
            INSERT INTO bookmark_nodes(user_id, parent_id, bookmark_id, is_folder, name, sort_order, created_at, updated_at)
            VALUES(%s,%s,%s,FALSE,%s,0,NOW(),NOW())
            """,
            (int(user_id), parent_id, int(bookmark_id), title),
        )
    return {"message": "bookmark added", "bookmark_id": bookmark_id, "storage_id": object_id, "node_id": doc_node_id}


def remove_bookmark(body: BookmarkDeleteIn, user_id: int = Depends(get_current_user_id)):
    object_id = _resolve_object_id(body)
    if object_id is None:
        raise HTTPException(status_code=400, detail="storage_id is required")
    doc_node_id = _resolve_doc_node_id(object_id)
    if doc_node_id is None:
        return {"message": "not found", "deleted": 0}
    row = fetch_one("SELECT bookmark_id FROM bookmarks WHERE user_id=%s AND node_id=%s", (int(user_id), int(doc_node_id)))
    if not row:
        return {"message": "not found", "deleted": 0}
    bookmark_id = int(row["bookmark_id"])
    execute("DELETE FROM bookmark_nodes WHERE user_id=%s AND bookmark_id=%s", (int(user_id), bookmark_id))
    execute("DELETE FROM bookmarks WHERE user_id=%s AND bookmark_id=%s", (int(user_id), bookmark_id))
    return {"message": "bookmark removed", "deleted": 1}


def get_tree(user_id: int = Depends(get_current_user_id)):
    rows = fetch_all(
        """
        SELECT
            bn.bookmark_node_id,
            bn.parent_id,
            bn.is_folder,
            bn.name,
            bn.bookmark_id,
            bn.sort_order,
            b.node_id AS target_node_id,
            b.title AS bookmark_title,
            tn.doc_id AS object_id,
            tn.alias AS tree_alias,
            so.doc_name,
            so.file_right,
            so.relative_path,
            so.sub_path,
            so.mime_type,
            so.branch_type
          FROM bookmark_nodes bn
          LEFT JOIN bookmarks b ON b.bookmark_id = bn.bookmark_id AND b.user_id = bn.user_id
          LEFT JOIN tree_nodes tn ON tn.node_id = b.node_id
          LEFT JOIN storage_objects so ON so.object_id = tn.doc_id
         WHERE bn.user_id=%s
         ORDER BY COALESCE(bn.parent_id,0), bn.sort_order, bn.bookmark_node_id
        """,
        (int(user_id),),
    )
    by_parent: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for row in rows or []:
        object_id = _as_int(row.get("object_id"))
        if not bool(row.get("is_folder")) and object_id is not None:
            if not has_object_permission(user_id, object_id, "r"):
                continue
        by_parent.setdefault(_as_int(row.get("parent_id")), []).append(row)

    def build(parent_id: Optional[int]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for row in by_parent.get(parent_id, []):
            is_folder = bool(row.get("is_folder"))
            object_id = _as_int(row.get("object_id"))
            title = str(row.get("name") or row.get("bookmark_title") or row.get("tree_alias") or row.get("doc_name") or "").strip()
            children = build(int(row["bookmark_node_id"]))
            out.append(
                {
                    "id": int(row["bookmark_node_id"]),
                    "parent": row.get("parent_id"),
                    "name": title or f"Node {row['bookmark_node_id']}",
                    "node_type": "FOLDER" if is_folder else "ITEM",
                    "is_folder": is_folder,
                    "meta": {
                        "target_type": "STORAGE_OBJECT" if object_id is not None else None,
                        "target_id": object_id,
                        "bookmark_id": _as_int(row.get("bookmark_id")),
                        "storage_id": object_id,
                        "object_id": object_id,
                        "storage_object_id": object_id,
                        "doc_id": object_id,
                        "doc_title": row.get("bookmark_title") or row.get("tree_alias") or row.get("doc_name"),
                        "storage_object_name": row.get("doc_name"),
                        "storage_object_file_right": row.get("file_right"),
                        "storage_object_relative_path": row.get("relative_path"),
                        "storage_object_sub_path": row.get("sub_path"),
                        "mime_type": row.get("mime_type"),
                        "branch_type": str(row.get("branch_type") or "general").upper(),
                    },
                    "children": children,
                }
            )
        return out

    return {"nodes": build(None)}


def create_node(data: BookmarkNodeIn, user_id: int = Depends(get_current_user_id)):
    _assert_parent_folder(data.parent_id, user_id)
    is_folder = data.type == "FOLDER"
    name = str(data.name or "").strip()
    bookmark_id = _as_int(data.bookmark_id)
    if is_folder:
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        bookmark_id = None
    else:
        if bookmark_id is None:
            raise HTTPException(status_code=400, detail="bookmark_id is required")
        bm = _fetch_bookmark(bookmark_id)
        if not bm or int(bm["user_id"]) != int(user_id):
            raise HTTPException(status_code=404, detail="bookmark not found")
        if not name:
            name = str(bm.get("title") or "").strip() or f"Bookmark {bookmark_id}"
    node_id = execute(
        """
        INSERT INTO bookmark_nodes(user_id, parent_id, bookmark_id, is_folder, name, sort_order, created_at, updated_at)
        VALUES(%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """,
        (int(user_id), data.parent_id, bookmark_id, bool(is_folder), name, int(data.sort_order or 0)),
        True,
    )
    return {"node_id": node_id, "id": node_id}


def patch_node(node_id: int, data: BookmarkNodePatch, user_id: int = Depends(get_current_user_id)):
    row = _fetch_bookmark_node(node_id)
    if not row or int(row["user_id"]) != int(user_id):
        raise HTTPException(status_code=404, detail="node not found")
    fields = getattr(data, "model_fields_set", getattr(data, "__fields_set__", set()))
    name = row.get("name")
    parent_id = row.get("parent_id")
    sort_order = row.get("sort_order") or 0
    if "name" in fields and data.name is not None:
        name = str(data.name).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
    if "parent_id" in fields:
        if data.parent_id == node_id:
            raise HTTPException(status_code=400, detail="parent_id cannot be self")
        _assert_parent_folder(data.parent_id, user_id)
        parent_id = data.parent_id
    if "sort_order" in fields and data.sort_order is not None:
        sort_order = int(data.sort_order)
    execute(
        "UPDATE bookmark_nodes SET name=%s, parent_id=%s, sort_order=%s, updated_at=NOW() WHERE bookmark_node_id=%s",
        (name, parent_id, sort_order, int(node_id)),
    )
    return {"message": "ok"}


def delete_nodes_bulk(body: BookmarkNodesBulkDelete, user_id: int = Depends(get_current_user_id)):
    ids: Set[int] = set()
    bookmark_ids: Set[int] = set()
    for raw in body.node_ids or []:
        node_id = _as_int(raw)
        if node_id is None:
            continue
        row = _fetch_bookmark_node(node_id)
        if not row or int(row["user_id"]) != int(user_id):
            continue
        ids |= _collect_descendants(user_id, node_id)
    if not ids:
        return {"deleted": 0}
    placeholders = ",".join(["%s"] * len(ids))
    rows = fetch_all(f"SELECT bookmark_id FROM bookmark_nodes WHERE bookmark_node_id IN ({placeholders})", tuple(ids))
    for row in rows or []:
        bid = _as_int(row.get("bookmark_id"))
        if bid is not None:
            bookmark_ids.add(bid)
    execute(f"DELETE FROM bookmark_nodes WHERE bookmark_node_id IN ({placeholders})", tuple(ids))
    if bookmark_ids:
        ph = ",".join(["%s"] * len(bookmark_ids))
        execute(f"DELETE FROM bookmarks WHERE user_id=%s AND bookmark_id IN ({ph})", tuple([int(user_id)] + list(bookmark_ids)))
    return {"deleted": len(ids)}
