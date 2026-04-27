import re
from fastapi import HTTPException, Query, Depends
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field
from app.utils.db import fetch_all, fetch_one, execute
from app.deps.auth_dep import get_current_user_id



def _natural_name_key(name: Any) -> Tuple[Tuple[int, Any], ...]:
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


def _tree_sort_key(row: Dict[str, Any]):
    return (
        int(row.get("sort_order") or 0),
        _natural_name_key(row.get("name")),
        str(row.get("name") or "").lower(),
    )

def get_tree(
    kb_id: int = Query(..., description="KB ID (필수)"),
    group_by: str = Query("none", description="트리 그룹 기준: none | status | mime"),
    user_id: int = Depends(get_current_user_id),
):
    kb_row = fetch_one("SELECT tree_id, name FROM trees WHERE tree_id=%s AND kind='KB'", (kb_id,))
    if not kb_row:
        raise HTTPException(status_code=404, detail="KB 없음")

    rows = fetch_all(
        """
        SELECT
            n.node_id,
            n.parent_id,
            n.name,
            n.node_type,
            n.sort_order,
            ab.policy_code,
            CONCAT(IF(pp.owner_r, 'r','-'), IF(pp.owner_w,'w','-'), IF(pp.owner_x,'x','-'))  AS own_perm,
            CONCAT(IF(pp.group_r, 'r','-'), IF(pp.group_w,'w','-'), IF(pp.group_x,'x','-'))  AS grp_perm,
            CONCAT(IF(pp.other_r, 'r','-'), IF(pp.other_w,'w','-'), IF(pp.other_x,'x','-'))  AS oth_perm
          FROM tree_nodes n
          LEFT JOIN acl_bindings ab
                 ON ab.resource_type='TREE_NODE' AND ab.resource_pk=n.node_id
          LEFT JOIN perm_policies pp
                 ON pp.policy_code = ab.policy_code
         WHERE n.tree_id=%s
           AND fn_acl_can('TREE_NODE', n.node_id, %s, 'r') = 1
         ORDER BY COALESCE(n.parent_id,0), n.sort_order, n.name
        """,
        (kb_id, user_id),
    )

    by_parent: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for r in rows:
        r['is_folder'] = ('folder' in (r.get('node_type') or '').lower())
        if r.get('own_perm') and r.get('grp_perm') and r.get('oth_perm'):
            r['policy_matrix'] = [r['own_perm'], r['grp_perm'], r['oth_perm']]
        else:
            r['policy_matrix'] = None
        by_parent.setdefault(r.get("parent_id"), []).append(r)

    def to_tree(pid: Optional[int]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        siblings = sorted(by_parent.get(pid, []), key=_tree_sort_key)
        for r in siblings:
            children = to_tree(r["node_id"])
            out.append(
                {
                    "id": r["node_id"],
                    "parent": r["parent_id"],
                    "name": r["name"],
                    "node_type": r["node_type"],
                    "is_folder": r["is_folder"] or (len(children) > 0),
                    "policy_code": r.get("policy_code"),
                    "policy_matrix": r.get("policy_matrix"),
                    "sort_order": r.get("sort_order") or 0,
                    "children": children,
                }
            )
        return out

    return {"kb_id": kb_id, "nodes": to_tree(None)}

def batch_rename(
    items: List[Dict[str, Any]],
    user_id: int = Depends(get_current_user_id),
):
    count = 0
    for it in items or []:
        node_id = it.get("node_id")
        new_name = (it.get("name") or "").strip()
        if not node_id or not new_name:
            continue

        can = fetch_one(
            "SELECT fn_acl_can('TREE_NODE', %s, %s, 'w') AS ok",
            (node_id, user_id),
        )
        if not can or not bool(can.get("ok")):
            raise HTTPException(status_code=403, detail=f"node {node_id} 권한 없음")

        execute("UPDATE tree_nodes SET name=%s, updated_at=NOW() WHERE node_id=%s", (new_name, node_id))
        count += 1

    return {"ok": True, "count": count}

def batch_delete(
    node_ids: List[int],
    user_id: int = Depends(get_current_user_id),
):
    if not node_ids:
        return {"ok": True, "count": 0}

    allowed: List[int] = []
    for nid in node_ids:
        can = fetch_one(
            "SELECT fn_acl_can('TREE_NODE', %s, %s, 'w') AS ok",
            (nid, user_id),
        )
        if can and bool(can.get("ok")):
            allowed.append(nid)

    if not allowed:
        return {"ok": False, "count": 0, "message": "삭제 권한 없음"}

    placeholders = ", ".join(["%s"] * len(allowed))
    execute(f"DELETE FROM tree_nodes WHERE node_id IN ({placeholders})", tuple(allowed))
    return {"ok": True, "count": len(allowed)}

class NodesReorderIn(BaseModel):
    parent_id: Optional[int] = None
    orders: List[Dict[str, int]] = Field(..., description="[{node_id, sort_order}, ...]")

def reorder_nodes(
    kb_id: int = Query(..., description="KB ID"),
    body: NodesReorderIn = None,
    user_id: int = Depends(get_current_user_id),
):
    if not body or not body.orders:
        return {"ok": True, "count": 0}

    count = 0
    for item in body.orders:
        node_id = item.get("node_id")
        sort_order = item.get("sort_order")
        if node_id is None or sort_order is None:
            continue

        can = fetch_one(
            "SELECT fn_acl_can('TREE_NODE', %s, %s, 'w') AS ok",
            (node_id, user_id),
        )
        if not can or not bool(can.get("ok")):
            continue

        row = fetch_one("SELECT parent_id, tree_id FROM tree_nodes WHERE node_id=%s", (node_id,))
        if not row or row["tree_id"] != kb_id:
            continue
        if row["parent_id"] != body.parent_id:
            continue

        execute(
            "UPDATE tree_nodes SET sort_order=%s, updated_at=NOW() WHERE node_id=%s",
            (sort_order, node_id),
        )
        count += 1

    return {"ok": True, "count": count}

def list_file_nodes(
    kb_id: int = Query(...),
    parent_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    kb_row = fetch_one("SELECT tree_id FROM trees WHERE tree_id=%s AND kind='KB'", (kb_id,))
    if not kb_row:
        raise HTTPException(status_code=404, detail="KB 없음")

    conds = ["n.tree_id=%s", "fn_acl_can('TREE_NODE', n.node_id, %s, 'r')=1"]
    params: List[Any] = [kb_id, user_id]

    conds.append("(COALESCE(n.is_folder, 0)=0 OR LOWER(n.node_type) NOT LIKE '%%folder%%')")

    if parent_id is not None:
        conds.append("n.parent_id = %s")
        params.append(parent_id)

    if q:
        conds.append("n.name LIKE %s")
        params.append(f"%{q}%")

    rows = fetch_all(
        f"""
        SELECT
            n.node_id,
            n.parent_id,
            n.name,
            UPPER(COALESCE(n.branch_type, 'GENERAL')) AS branch_type,
            n.node_type,
            n.sort_order,
            n.created_at,
            n.updated_at,
            pfn.doc_master_id,
            dr.storage_object_id,
            so.file_right AS storage_object_file_right,
            so.relative_path AS storage_object_relative_path,
            so.sub_path AS storage_object_sub_path,
            dr.rev_no,
            ab.policy_code,
            CONCAT(IF(pp.owner_r,'r','-'),IF(pp.owner_w,'w','-'),IF(pp.owner_x,'x','-')) AS own_perm,
            CONCAT(IF(pp.group_r,'r','-'),IF(pp.group_w,'w','-'),IF(pp.group_x,'x','-')) AS grp_perm,
            CONCAT(IF(pp.other_r,'r','-'),IF(pp.other_w,'w','-'),IF(pp.other_x,'x','-')) AS oth_perm
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
        LEFT JOIN acl_bindings ab ON ab.resource_type='TREE_NODE' AND ab.resource_pk=n.node_id
        LEFT JOIN perm_policies pp ON pp.policy_code = ab.policy_code
        WHERE {" AND ".join(conds)}
        ORDER BY n.sort_order, n.name
        """,
        tuple(params),
    )

    items = []
    for r in rows or []:
        policy_matrix = None
        if r.get("own_perm") and r.get("grp_perm") and r.get("oth_perm"):
            policy_matrix = [r["own_perm"], r["grp_perm"], r["oth_perm"]]
        items.append({
            "node_id": r["node_id"],
            "parent_id": r.get("parent_id"),
            "name": r["name"],
            "branch_type": r.get("branch_type"),
            "node_type": r.get("node_type"),
            "is_folder": False,
            "sort_order": r.get("sort_order") or 0,
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "doc_id": r.get("doc_master_id"),
            "object_id": r.get("storage_object_id"),
            "storage_object_file_right": r.get("storage_object_file_right"),
            "storage_object_relative_path": r.get("storage_object_relative_path"),
            "storage_object_sub_path": r.get("storage_object_sub_path"),
            "rev_no": r.get("rev_no"),
            "policy_code": r.get("policy_code"),
            "policy_matrix": policy_matrix,
        })

    return {"kb_id": kb_id, "parent_id": parent_id, "items": items}
