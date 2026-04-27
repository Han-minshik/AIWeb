from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, HTTPException, Body, Query, Path
from pydantic import BaseModel, Field

from app.deps.auth_dep import get_current_user_id
from app.repositories.common import fetch_all, fetch_one, execute
from app.core.config import UPLOAD_DIR, DELETE_UPLOADS_ON_RECORD_DELETE  # 업로드 경로/토글

log = logging.getLogger(__name__)

def _make_in_clause(seq: List[int]) -> tuple[str, tuple]:
    items = list(dict.fromkeys(seq or []))
    if not items:
        return "", tuple()
    return ",".join(["%s"] * len(items)), tuple(items)

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
            if c == "key": 
                return "so.`key`"
            return f"so.{c}"

    if {"dir", "filename"} <= have:
        return "CONCAT(so.dir,'/',so.filename)"

    return "'__NO_PATH__'"

def _normalize_under_uploads(rel_path: str) -> Optional[str]:
  if not rel_path:
    return None
  safe_rel = rel_path.lstrip("/\\")
  abs_path = os.path.abspath(os.path.join(UPLOAD_DIR, safe_rel))
  uploads_abs = os.path.abspath(UPLOAD_DIR)
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
      log.warning("[projects.nodes:bulk] outside uploads skipped: %s", rel)
      continue
    try:
      if os.path.exists(abs_path):
        os.remove(abs_path)
        stats["deleted"] += 1
        log.info("[projects.nodes:bulk] deleted file: %s", abs_path)
      else:
        stats["skipped_missing"] += 1
        log.info("[projects.nodes:bulk] not found (skip): %s", abs_path)
    except Exception as e:
      stats["errors"] += 1
      log.exception("[projects.nodes:bulk] delete failed: %s (%s)", abs_path, e)
  return stats

def _collect_deletable_paths_for_nodes(node_ids: List[int]) -> Set[str]:
    if not node_ids:
        return set()

    placeholders_nodes, args_nodes = _make_in_clause(node_ids)
    if not placeholders_nodes:
        return set()

    path_expr = _detect_storage_path_expr()

    rows = fetch_all(
        f"""
        SELECT DISTINCT
               so.object_id,
               {path_expr} AS rel_path
          FROM project_nodes pn
          JOIN storage_objects so ON so.object_id = pn.storage_object_id
         WHERE pn.node_id IN ({placeholders_nodes})
           AND pn.storage_object_id IS NOT NULL
           AND {path_expr} IS NOT NULL
           AND {path_expr} <> ''
        """,
        args_nodes,
    )
    if not rows:
        return set()

    deletable: Set[str] = set()

    notin_ph, notin_args = placeholders_nodes, args_nodes
    for r in rows:
        so_id = r["object_id"]
        rel_path = r["rel_path"]

        cnt = fetch_one(
            f"""
            SELECT COUNT(*) AS cnt
              FROM project_nodes
             WHERE storage_object_id = %s
               AND node_id NOT IN ({notin_ph})
            """,
            tuple([so_id]) + notin_args,
        )
        remaining = int((cnt or {}).get("cnt", 0))
        if remaining == 0 and rel_path:
            deletable.add(rel_path)

    return deletable

class ProjectIn(BaseModel):
  name: str
  kind: Optional[str] = "KB"

def list_projects(
  kind: Optional[str] = Query(None),
  user_id: int = Depends(get_current_user_id),
):
  sql = "SELECT project_id, name, kind, created_at, updated_at FROM projects WHERE 1=1"
  params: List[Any] = []
  if kind:
    sql += " AND kind=%s"
    params.append(kind.upper())
  sql += " ORDER BY updated_at DESC"
  return fetch_all(sql, tuple(params))

def create_project(
  body: ProjectIn,
  user_id: int = Depends(get_current_user_id),
):
  pid = execute(
    "INSERT INTO projects(owner_user_id, name, kind) VALUES(%s,%s,%s)",
    (user_id, body.name, (body.kind or "KB").upper()),
    True,
  )
  return {"project_id": pid, "message": "생성"}

def _dup_name_exists(project_id: int, parent_id: Optional[int], name: str, exclude_node_id: Optional[int] = None) -> bool:
  base_sql = """
      SELECT node_id FROM project_nodes
       WHERE project_id=%s
         AND name=%s
         AND parent_id <=> %s
  """
  params: List[Any] = [project_id, name, parent_id]
  if exclude_node_id is not None:
    base_sql += " AND node_id<>%s"
    params.append(exclude_node_id)
  return fetch_one(base_sql, tuple(params)) is not None

def _assert_parent_is_valid(project_id: int, new_parent: Optional[int]) -> None:
  if new_parent is None:
    return
  if new_parent <= 0:
    raise HTTPException(status_code=400, detail="부모는 NULL(루트) 또는 유효한 노드 ID여야 합니다.")
  parent = fetch_one(
    "SELECT node_id, kind FROM project_nodes WHERE project_id=%s AND node_id=%s",
    (project_id, new_parent),
  )
  if not parent:
    raise HTTPException(status_code=404, detail="부모 노드가 존재하지 않습니다.")
  if parent["kind"] != "folder":
    raise HTTPException(status_code=400, detail="부모는 폴더여야 합니다.")

def _collect_descendants(project_id: int, start_id: int) -> Set[int]:
  descendants: Set[int] = set()

  def _dfs(nid: int):
    rows = fetch_all(
      "SELECT node_id FROM project_nodes WHERE project_id=%s AND parent_id=%s",
      (project_id, nid),
    )
    for r in rows:
      cid = r["node_id"]
      if cid not in descendants:
        descendants.add(cid)
        _dfs(cid)

  _dfs(start_id)
  return descendants

def get_tree(project_id: int, user_id: int = Depends(get_current_user_id)):
  rows = fetch_all(
    """
    SELECT node_id, parent_id, name, kind, storage_object_id, sort_order
      FROM project_nodes
     WHERE project_id = %s
     ORDER BY COALESCE(parent_id,0), sort_order, name
    """,
    (project_id,),
  )

  by_parent: Dict[Optional[int], List[Dict[str, Any]]] = {}
  for r in rows:
    by_parent.setdefault(r.get("parent_id"), []).append(r)

  def build(pid: Optional[int]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for n in by_parent.get(pid, []):
      node = {
        "id": n["node_id"],
        "name": n["name"],
        "parent": n["parent_id"],
        "project_id": project_id,
        "kind": n.get("kind"),
        "sort_order": n.get("sort_order") or 0,
        "meta": {
          "node_id": n["node_id"],
          "storage_object_id": n.get("storage_object_id"),
          "kind": n.get("kind"),
        },
      }
      node["children"] = build(n["node_id"])
      items.append(node)
    return items

  return {"nodes": build(None)}

def list_project_files(
  project_id: int,
  q: Optional[str] = Query(None, description="파일명 검색"),
  user_id: int = Depends(get_current_user_id),
):
  conds: List[str] = [
    "pn.project_id=%s",
    "pn.storage_object_id IS NOT NULL",
    "fn_acl_can('STORAGE_OBJECT', so.object_id, %s, 'r') = 1",
  ]
  params: List[Any] = [project_id, user_id]

  if q:
    conds.append("so.name LIKE %s")
    params.append(f"%{q}%")

  rows = fetch_all(
    f"""
    SELECT
      so.object_id,
      so.name,
      so.created_at AS uploaded_at,
      so.updated_at AS updated_at,
      so.mime_type,
      dp.status,
      ab.policy_code,
      CONCAT(IF(pp.owner_r,'r','-'), IF(pp.owner_w,'w','-'), IF(pp.owner_x,'x','-')) AS own_perm,
      CONCAT(IF(pp.group_r,'r','-'), IF(pp.group_w,'w','-'), IF(pp.group_x,'x','-')) AS grp_perm,
      CONCAT(IF(pp.other_r,'r','-'), IF(pp.other_w,'w','-'), IF(pp.other_x,'x','-')) AS oth_perm
    FROM project_nodes pn
    JOIN storage_objects so ON so.object_id = pn.storage_object_id
    LEFT JOIN doc_props dp   ON dp.object_id = so.object_id
    LEFT JOIN acl_bindings ab
           ON ab.resource_type='STORAGE_OBJECT' AND ab.resource_pk=so.object_id
    LEFT JOIN perm_policies pp
           ON pp.policy_code = ab.policy_code
    WHERE {" AND ".join(conds)}
    GROUP BY so.object_id
    ORDER BY so.created_at DESC
    """,
    tuple(params),
  )

  return [{
    "object_id": r["object_id"],
    "name": r["name"],
    "uploaded_at": r.get("uploaded_at"),
    "updated_at": r.get("updated_at"),
    "mime_type": r.get("mime_type"),
    "status": (r.get("status") or ""),
    "policy_code": r.get("policy_code"),
    "policy_matrix": [r.get("own_perm"), r.get("grp_perm"), r.get("oth_perm")] if r.get("own_perm") else None,
  } for r in rows or []]

class NodeCreateIn(BaseModel):
  name: str = Field(..., min_length=1, max_length=255)
  parent: Optional[int] = None
  kind: Optional[str] = "FOLDER" 
  storage_object_id: Optional[int] = None

def create_node(
  project_id: int,
  body: NodeCreateIn,
  user_id: int = Depends(get_current_user_id),
):
  kind = (body.kind or "FOLDER").lower()
  if kind not in ("folder", "file"):
    raise HTTPException(status_code=400, detail="kind는 'folder' 또는 'file'이어야 합니다.")

  _assert_parent_is_valid(project_id, body.parent)

  if _dup_name_exists(project_id, body.parent, body.name):
    raise HTTPException(status_code=409, detail="동일한 이름이 이미 존재합니다.")

  nid = execute(
    """INSERT INTO project_nodes(project_id, parent_id, name, kind, storage_object_id, sort_order)
       VALUES (%s,%s,%s,%s,%s,0)""",
    (project_id, body.parent, body.name, kind, body.storage_object_id),
    True,
  )
  return {"node_id": nid, "message": "노드 생성"}

class NodePatchIn(BaseModel):
  name: Optional[str] = Field(None, min_length=1, max_length=255)
  parent: Optional[int] = None
  kind: Optional[str] = None         
  storage_object_id: Optional[int] = None

def patch_node(
  node_id: int,
  body: NodePatchIn,
  user_id: int = Depends(get_current_user_id),
):
  existed = fetch_one(
    "SELECT node_id, project_id, parent_id, name, kind FROM project_nodes WHERE node_id=%s",
    (node_id,),
  )
  if not existed:
    raise HTTPException(status_code=404, detail="노드 없음")

  project_id = existed["project_id"]

  sets: List[str] = []
  params: List[Any] = []

  if body.parent is not None:
    if body.parent == node_id:
      raise HTTPException(status_code=400, detail="부모를 자기 자신으로 설정할 수 없습니다.")
    _assert_parent_is_valid(project_id, body.parent)

    descendants = _collect_descendants(project_id, node_id)
    if body.parent in descendants:
      raise HTTPException(status_code=400, detail="부모를 자손으로 설정할 수 없습니다.")

    new_parent = body.parent
    sets.append("parent_id=%s")
    params.append(new_parent)
  else:
    new_parent = existed["parent_id"]

  new_name = existed["name"]
  if body.name is not None:
    new_name = body.name
    sets.append("name=%s")
    params.append(new_name)

  if body.kind is not None:
    kind = (body.kind or "").lower()
    if kind not in ("folder", "file"):
      raise HTTPException(status_code=400, detail="kind는 'folder' 또는 'file'이어야 합니다.")
    sets.append("kind=%s")
    params.append(kind)

  if body.storage_object_id is not None:
    sets.append("storage_object_id=%s")
    params.append(body.storage_object_id)

  if not sets:
    return {"message": "변경 없음"}

  if _dup_name_exists(project_id, new_parent, new_name, exclude_node_id=node_id):
    raise HTTPException(status_code=409, detail="동일한 이름이 이미 존재합니다.")

  sql = f"UPDATE project_nodes SET {', '.join(sets)}, updated_at=NOW() WHERE node_id=%s"
  params.append(node_id)
  execute(sql, tuple(params))
  return {"message": "업데이트 완료"}

class BulkDeleteIn(BaseModel):
  node_ids: List[int] = Field(..., min_items=1)

def bulk_delete(
  project_id: int,
  body: BulkDeleteIn,
  user_id: int = Depends(get_current_user_id),
):
  ids = list(dict.fromkeys(body.node_ids or []))
  if not ids:
    return {"message": "대상 없음", "count": 0}

  deletable_paths = _collect_deletable_paths_for_nodes(ids)

  placeholders = ",".join(["%s"] * len(ids))
  sql = f"DELETE FROM project_nodes WHERE project_id=%s AND node_id IN ({placeholders})"
  execute(sql, tuple([project_id] + ids))

  if DELETE_UPLOADS_ON_RECORD_DELETE and deletable_paths:
    stats = _delete_files_safely(deletable_paths)
    log.info("[projects.nodes:bulk] file removal summary: %s", stats)

  return {"message": "삭제", "count": len(ids)}

class MoveIn(BaseModel):
  node_ids: List[int] = Field(..., min_items=1)
  new_parent: Optional[int] = None

def bulk_move(
  project_id: int,
  body: MoveIn,
  user_id: int = Depends(get_current_user_id),
):
  ids = list(dict.fromkeys(body.node_ids or []))
  if not ids:
    return {"message": "대상 없음", "count": 0}

  _assert_parent_is_valid(project_id, body.new_parent)

  for nid in ids:
    row = fetch_one(
      "SELECT node_id, kind FROM project_nodes WHERE project_id=%s AND node_id=%s",
      (project_id, nid),
    )
    if not row:
      raise HTTPException(status_code=404, detail=f"노드 {nid} 없음")

  if body.new_parent is not None:
    if body.new_parent in ids:
      raise HTTPException(status_code=400, detail="부모를 자기 자신으로 설정할 수 없습니다.")
    descendants_all: Set[int] = set()
    for nid in ids:
      descendants_all |= _collect_descendants(project_id, nid)
    if body.new_parent in descendants_all:
      raise HTTPException(status_code=400, detail="부모를 자손으로 설정할 수 없습니다.")

  moving_rows = fetch_all(
    "SELECT node_id, name FROM project_nodes WHERE project_id=%s AND node_id IN (" + ",".join(["%s"] * len(ids)) + ")",
    tuple([project_id] + ids),
  )
  for mr in moving_rows:
    if _dup_name_exists(project_id, body.new_parent, mr["name"], exclude_node_id=mr["node_id"]):
      raise HTTPException(status_code=409, detail=f"동일한 이름이 이미 존재합니다: {mr['name']}")

  execute(
    "UPDATE project_nodes SET parent_id=%s, updated_at=NOW() WHERE project_id=%s AND node_id IN (" + ",".join(["%s"] * len(ids)) + ")",
    tuple([body.new_parent, project_id] + ids),
  )
  return {"message": "이동", "count": len(ids)}

class NodesReorderIn(BaseModel):
  parent: Optional[int] = None
  orders: List[Dict[str, int]] = Field(..., description="[{node_id, sort_order}, ...]")

def reorder_nodes(
  project_id: int,
  body: NodesReorderIn,
  user_id: int = Depends(get_current_user_id),
):
  if not body or not body.orders:
    return {"message": "대상 없음", "count": 0}

  count = 0
  for item in body.orders:
    node_id = item.get("node_id")
    sort_order = item.get("sort_order")
    if node_id is None or sort_order is None:
      continue

    row = fetch_one("SELECT project_id, parent_id FROM project_nodes WHERE node_id=%s", (node_id,))
    if not row or row["project_id"] != project_id:
      continue
    if row["parent_id"] != body.parent:
      continue

    execute(
      "UPDATE project_nodes SET sort_order=%s, updated_at=NOW() WHERE node_id=%s",
      (sort_order, node_id),
    )
    count += 1

  return {"message": "재정렬", "count": count}
