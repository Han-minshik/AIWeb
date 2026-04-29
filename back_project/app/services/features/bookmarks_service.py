from fastapi import Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Literal
from app.deps.auth_dep import get_current_user_id
from app.repositories.common import fetch_all, fetch_one, execute


BookmarkNodeType = Literal["FOLDER", "ITEM"]
BookmarkTargetType = Literal[
    "DOC_MASTER",
    "STORAGE_OBJECT",
    "PROJECT",
    "PROJECT_NODE",
    "CHAT_SESSION",
    "KB_NODE",
]


class BookmarkIn(BaseModel):
    target_type: BookmarkTargetType
    target_id: int
    title: Optional[str] = None
    preferred_node_id: Optional[int] = None


class GroupIn(BaseModel):
    name: str


class GroupRenameIn(BaseModel):
    name: str


class BookmarkNodeIn(BaseModel):
    name: str
    type: BookmarkNodeType
    parent_id: Optional[int] = None
    target_type: Optional[BookmarkTargetType] = None
    target_id: Optional[int] = None


class BookmarkNodePatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None


class BookmarkNodesBulkDelete(BaseModel):
    node_ids: List[int]


def _fetch_node(node_id: int):
    return fetch_one(
        "SELECT node_id, group_id, parent_id, name, type, target_type, target_id "
        "FROM bookmark_nodes WHERE node_id=%s",
        (node_id,),
    )


def _collect_descendants(group_id: int, start_id: int):
    out = set()
    stack = [start_id]
    while stack:
        nid = stack.pop()
        rows = fetch_all(
            "SELECT node_id FROM bookmark_nodes WHERE group_id=%s AND parent_id=%s",
            (group_id, nid),
        )
        for r in rows:
            cid = r["node_id"]
            if cid not in out:
                out.add(cid)
                stack.append(cid)
    return out


def add_bookmark(bm: BookmarkIn, user_id: int = Depends(get_current_user_id)):
    if bm.target_type == "DOC_MASTER":
        exists = fetch_one(
            """SELECT bookmark_id FROM bookmarks
               WHERE user_id=%s AND doc_master_id=%s""",
            (user_id, bm.target_id),
        )
        if exists:
            return {"message": "이미 북마크됨"}

        execute(
            """INSERT INTO bookmarks (org_id, user_id, target_type, target_pk, title, doc_master_id, preferred_node_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (1, user_id, bm.target_type, bm.target_id, bm.title, bm.target_id, bm.preferred_node_id),
        )
    else:
        exists = fetch_one(
            """SELECT bookmark_id FROM bookmarks
               WHERE user_id=%s AND target_type=%s AND target_pk=%s""",
            (user_id, bm.target_type, bm.target_id),
        )
        if exists:
            return {"message": "이미 북마크됨"}

        execute(
            """INSERT INTO bookmarks (org_id, user_id, target_type, target_pk, title)
               VALUES (%s, %s, %s, %s, %s)""",
            (1, user_id, bm.target_type, bm.target_id, bm.title),
        )

    row = fetch_one(
        "SELECT group_id FROM bookmark_groups WHERE user_id=%s ORDER BY group_id LIMIT 1",
        (user_id,),
    )
    if not row:
        gid = execute(
            "INSERT INTO bookmark_groups (user_id, name) VALUES (%s,%s)",
            (user_id, "Default"),
            True,
        )
    else:
        gid = row["group_id"]

    exists_node = fetch_one(
        """SELECT node_id FROM bookmark_nodes
           WHERE group_id=%s AND target_type=%s AND target_id=%s""",
        (gid, bm.target_type, bm.target_id),
    )
    if not exists_node:
        execute(
            """
            INSERT INTO bookmark_nodes
                (group_id, parent_id, name, type, target_type, target_id, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW())
            """,
            (
                gid,
                None,
                bm.title or f"{bm.target_type}-{bm.target_id}",
                "ITEM",
                bm.target_type,
                bm.target_id,
            ),
        )

    return {"message": "북마크 저장"}


def remove_bookmark(bm: BookmarkIn, user_id: int = Depends(get_current_user_id)):
    if bm.target_type == "DOC_MASTER":
        execute(
            """DELETE FROM bookmarks
               WHERE user_id=%s AND doc_master_id=%s""",
            (user_id, bm.target_id),
        )
        execute(
            """DELETE FROM bookmark_nodes
               WHERE target_type=%s AND target_id=%s""",
            (bm.target_type, bm.target_id),
        )
    else:
        execute(
            """DELETE FROM bookmarks
               WHERE user_id=%s AND target_type=%s AND target_pk=%s""",
            (user_id, bm.target_type, bm.target_id),
        )
        execute(
            """DELETE FROM bookmark_nodes
               WHERE target_type=%s AND target_id=%s""",
            (bm.target_type, bm.target_id),
        )

    return {"message": "북마크 제거"}


def create_group(data: GroupIn, user_id: int = Depends(get_current_user_id)):
    gid = execute(
        "INSERT INTO bookmark_groups (user_id, name) VALUES (%s,%s)",
        (user_id, data.name),
        True,
    )
    return {"group_id": gid}


def rename_group(group_id: int, data: GroupRenameIn, user_id: int = Depends(get_current_user_id)):
    owned = fetch_one(
        "SELECT 1 FROM bookmark_groups WHERE group_id=%s AND user_id=%s",
        (group_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")
    execute(
        "UPDATE bookmark_groups SET name=%s WHERE group_id=%s",
        (data.name, group_id),
    )
    return {"message": "ok"}


def delete_group(group_id: int, user_id: int = Depends(get_current_user_id)):
    owned = fetch_one(
        "SELECT 1 FROM bookmark_groups WHERE group_id=%s AND user_id=%s",
        (group_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")

    execute("DELETE FROM bookmark_nodes WHERE group_id=%s", (group_id,))
    execute("DELETE FROM bookmark_groups WHERE group_id=%s", (group_id,))
    return {"message": "삭제"}


def list_groups(user_id: int = Depends(get_current_user_id)):
    rows = fetch_all(
        "SELECT group_id, name, created_at FROM bookmark_groups "
        "WHERE user_id=%s ORDER BY created_at DESC",
        (user_id,),
    )
    return [
        {
            "id": row["group_id"],
            "group_id": row["group_id"],
            "name": row["name"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_tree(group_id: int, user_id: int = Depends(get_current_user_id)):
    owned = fetch_one(
        "SELECT 1 FROM bookmark_groups WHERE group_id=%s AND user_id=%s",
        (group_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")

    rows = fetch_all(
        """
        SELECT
            bn.node_id,
            bn.parent_id,
            bn.name,
            bn.type,
            bn.target_type,
            bn.target_id,
            dm.doc_master_id,
            dm.title AS doc_title,
            dr.storage_object_id,
            so.name AS storage_object_name,
            so.file_right AS storage_object_file_right,
            so.relative_path AS storage_object_relative_path,
            so.sub_path AS storage_object_sub_path,
            so.mime_type AS storage_object_mime_type,
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
            ) AS branch_type
        FROM bookmark_nodes bn
        LEFT JOIN doc_master dm
               ON bn.target_type='DOC_MASTER'
              AND dm.doc_master_id = bn.target_id
        LEFT JOIN doc_revisions dr
               ON dr.revision_id = (
                    SELECT r.revision_id
                      FROM doc_revisions r
                     WHERE r.doc_master_id = dm.doc_master_id
                       AND r.deleted_at IS NULL
                     ORDER BY r.rev_no DESC
                     LIMIT 1
               )
        LEFT JOIN storage_objects so
               ON so.object_id = dr.storage_object_id
        WHERE bn.group_id=%s
        ORDER BY
            CASE WHEN bn.parent_id IS NULL THEN 0 ELSE 1 END,
            bn.parent_id,
            bn.name
        """,
        (group_id,),
    )

    by_parent = {}
    for r in rows:
        by_parent.setdefault(r["parent_id"], []).append(r)

    def build(pid):
        out = []
        for r in by_parent.get(pid, []):
            out.append(
                {
                    "id": r["node_id"],
                    "parent": r["parent_id"],
                    "name": r["name"],
                    "node_type": r["type"],
                    "is_folder": r["type"] == "FOLDER",
                    "meta": {
                        "target_type": r["target_type"],
                        "target_id": r["target_id"],
                        "doc_id": r.get("doc_master_id"),
                        "doc_master_id": r.get("doc_master_id"),
                        "doc_title": r.get("doc_title"),
                        "object_id": r.get("storage_object_id"),
                        "storage_object_id": r.get("storage_object_id"),
                        "storage_object_name": r.get("storage_object_name"),
                        "storage_object_file_right": r.get("storage_object_file_right"),
                        "storage_object_relative_path": r.get("storage_object_relative_path"),
                        "storage_object_sub_path": r.get("storage_object_sub_path"),
                        "mime_type": r.get("storage_object_mime_type"),
                        "branch_type": r.get("branch_type"),
                    },
                    "children": build(r["node_id"]),
                }
            )
        return out

    return {
        "group_id": group_id,
        "nodes": build(None),
    }


def create_node(group_id: int, data: BookmarkNodeIn, user_id: int = Depends(get_current_user_id)):
    owned = fetch_one(
        "SELECT 1 FROM bookmark_groups WHERE group_id=%s AND user_id=%s",
        (group_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")

    if data.parent_id is not None:
        parent = _fetch_node(data.parent_id)
        if not parent or parent["group_id"] != group_id:
            raise HTTPException(status_code=404, detail="부모 노드 없음")
        if parent["type"] != "FOLDER":
            raise HTTPException(status_code=400, detail="부모는 폴더여야 함")

    node_id = execute(
        """
        INSERT INTO bookmark_nodes
            (group_id, parent_id, name, type, target_type, target_id, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,NOW())
        """,
        (
            group_id,
            data.parent_id,
            data.name,
            data.type,
            data.target_type,
            data.target_id,
        ),
        True,
    )
    return {"node_id": node_id}


def patch_node(node_id: int, data: BookmarkNodePatch, user_id: int = Depends(get_current_user_id)):
    row = _fetch_node(node_id)
    if not row:
        raise HTTPException(status_code=404, detail="노드 없음")

    owned = fetch_one(
        "SELECT 1 FROM bookmark_groups WHERE group_id=%s AND user_id=%s",
        (row["group_id"], user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")

    fields_set = getattr(data, "model_fields_set", getattr(data, "__fields_set__", set()))

    if "name" in fields_set:
        new_name = data.name
    else:
        new_name = row["name"]

    if "parent_id" in fields_set:
        new_parent = data.parent_id
        if new_parent == node_id:
            raise HTTPException(status_code=400, detail="부모를 자기 자신으로 설정 불가")
        if new_parent is not None:
            parent = _fetch_node(new_parent)
            if not parent or parent["group_id"] != row["group_id"]:
                raise HTTPException(status_code=404, detail="부모 노드 없음")
            if parent["type"] != "FOLDER":
                raise HTTPException(status_code=400, detail="부모는 폴더여야 함")
            descendants = _collect_descendants(row["group_id"], node_id)
            if new_parent in descendants:
                raise HTTPException(status_code=400, detail="자손을 부모로 설정 불가")
    else:
        new_parent = row["parent_id"]

    execute(
        "UPDATE bookmark_nodes SET name=%s, parent_id=%s WHERE node_id=%s",
        (new_name, new_parent, node_id),
    )
    return {"message": "ok"}


def delete_nodes_bulk(group_id: int, body: BookmarkNodesBulkDelete, user_id: int = Depends(get_current_user_id)):
    owned = fetch_one(
        "SELECT 1 FROM bookmark_groups WHERE group_id=%s AND user_id=%s",
        (group_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")

    to_delete = set()

    for nid in body.node_ids:
        n = _fetch_node(nid)
        if n and n["group_id"] == group_id:
            to_delete.add(nid)
            to_delete |= _collect_descendants(group_id, nid)

    if not to_delete:
        return {"deleted": 0}

    placeholders = ",".join(["%s"] * len(to_delete))
    execute(
        f"DELETE FROM bookmark_nodes WHERE node_id IN ({placeholders})",
        tuple(to_delete),
    )
    return {"deleted": len(to_delete)}
