from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

import httpx
from fastapi import Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.deps.auth_dep import get_current_user_id
from app.repositories.common import execute, fetch_all, fetch_one
from app.repositories.transaction import db_transaction
from app.services import admin_service
from app.services.features.document_db import (
    active_group_ids_for_user,
    as_int,
    collect_descendants,
    clean_node_name,
    ensure_acl,
    ensure_ai_document_info,
    enqueue_ai_job,
    enqueue_ai_metadata_job,
    find_child_node_by_alias,
    get_kb_entry,
    get_node,
    is_admin,
    next_item_type_code,
    next_sort_order,
    node_policy_matrix,
    policy_requires_target_group,
    rel_sub_for_parent,
    row_has_document_permission,
    update_subtree_item_type,
)


class KBTiny(BaseModel):
    kb_id: int
    name: str


class KBNodeIn(BaseModel):
    name: str = Field(..., min_length=1)
    parent_id: Optional[int] = None
    node_type: Optional[str] = "KB_FOLDER"


class KBNodePatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    item_type_code: Optional[int] = None
    sort_order: Optional[int] = None
    policy_code: Optional[str] = None
    target_group_id: Optional[int] = None


class KBNodesBulkDeleteIn(BaseModel):
    node_ids: List[int]


class KBNodesReorderIn(BaseModel):
    parent_id: Optional[int] = None
    orders: List[Tuple[int, int]]


class KBPermissionsBulkIn(BaseModel):
    node_ids: List[int] = Field(..., min_length=1, max_length=5000)
    policy_code: str = Field(..., min_length=1, max_length=32)
    target_group_id: Optional[int] = Field(default=None, ge=1)


class KbCreateIn(BaseModel):
    name: str
    kb_type: Optional[str] = "admin"
    is_user_kb: int = 0
    kind: Optional[str] = "KB"


class PersonalKbCreateIn(BaseModel):
    display_name: Optional[str] = None

class PersonalAiJobActionIn(BaseModel):
    job_id: int = Field(..., ge=1)
    action: Literal["add", "update", "delete", "reindex"]

class PersonalAiJobsIn(BaseModel):
    jobs: List[PersonalAiJobActionIn] = Field(
        default_factory=list,
        max_length=5000,
    )

class PersonalDeletedActionIn(BaseModel):
    deletion_id: int = Field(..., ge=1)
    action: Literal["restore", "purge"]

class PersonalDeletedActionsIn(BaseModel):
    requests: List[PersonalDeletedActionIn] = Field(
        default_factory=list,
        max_length=5000,
    )

def _batch_response(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    succeeded = sum(1 for item in items if item.get("success") is True)
    failed = len(items) - succeeded
    return {
        "ok": failed == 0,
        "requested": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "items": items,
    }

def _http_exception_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, str):
        return detail
    return str(detail or "Request failed")

def _require_admin(user_id: int) -> None:
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin permission required")


def _safe_local_folder_name(value: str) -> str:
    raw = clean_node_name(value)
    for ch in '<>:"/\\|?*':
        raw = raw.replace(ch, "_")
    raw = raw.strip().strip(".")
    return raw or "user"


def _fetch_user(user_id: int) -> Dict[str, Any]:
    user = fetch_one(
        """
        SELECT user_id, username, full_name, is_active
          FROM users
         WHERE user_id=%s
        """,
        (int(user_id),),
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not bool(user.get("is_active")):
        raise HTTPException(status_code=403, detail="Inactive user")
    return user


def _personal_kb_row(user_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT node_id, parent_id, kb_root_id, kb_type, kb_owner_user_id,
               name, alias, item_type_code, sort_order, created_at, updated_at
          FROM tree_nodes
         WHERE parent_id IS NULL
           AND is_folder=TRUE
           AND kb_type='personal'
           AND kb_owner_user_id=%s
         ORDER BY node_id
         LIMIT 1
        """,
        (int(user_id),),
    )


def _personal_kb_payload(row: Optional[Dict[str, Any]], user: Dict[str, Any]) -> Dict[str, Any]:
    usage = fetch_one(
        """
        SELECT committed_bytes, reserved_bytes
          FROM personal_kb_storage_usage
         WHERE user_id=%s
        """,
        (int(user["user_id"]),),
    ) or {}
    committed_bytes = max(int(usage.get("committed_bytes") or 0), 0)
    reserved_bytes = max(int(usage.get("reserved_bytes") or 0), 0)
    quota_bytes = max(int(settings.MINIO_PERSONAL_QUOTA_BYTES), 0)
    accounted_bytes = committed_bytes + reserved_bytes
    storage = {
        "committed_bytes": committed_bytes,
        "reserved_bytes": reserved_bytes,
        "accounted_bytes": accounted_bytes,
        "quota_bytes": quota_bytes,
        "available_bytes": max(quota_bytes - accounted_bytes, 0),
        "usage_percent": round((accounted_bytes / quota_bytes) * 100, 2) if quota_bytes else 0,
    }
    if not row:
        folder_name = _safe_local_folder_name(str(user.get("username") or f"user_{user['user_id']}"))
        return {
            "exists": False,
            "user_id": int(user["user_id"]),
            "username": user.get("username"),
            "folder_name": folder_name,
            "kb": None,
            "storage": storage,
        }
    folder_name = str(row.get("name") or user.get("username") or f"user_{user['user_id']}")
    return {
        "exists": True,
        "user_id": int(user["user_id"]),
        "username": user.get("username"),
        "folder_name": folder_name,
        "storage": storage,
        "kb": {
            "kb_id": int(row["node_id"]),
            "id": int(row["node_id"]),
            "name": row.get("alias") or row.get("name"),
            "original_name": row.get("name"),
            "kb_root_id": int(row["kb_root_id"]),
            "kb_type": row.get("kb_type"),
            "kb_owner_user_id": row.get("kb_owner_user_id"),
            "item_type_code": row.get("item_type_code"),
            "sort_order": row.get("sort_order") or 0,
        },
    }


def _is_personal_kb_owner(root: Dict[str, Any], user_id: int) -> bool:
    return root.get("kb_type") == "personal" and int(root.get("kb_owner_user_id") or 0) == int(user_id)


def _require_kb_edit_access(root: Dict[str, Any], user_id: int) -> None:
    kb_type = str(root.get("kb_type") or "").strip().lower()
    if kb_type == "personal":
        if not _is_personal_kb_owner(root, user_id):
            raise HTTPException(status_code=403, detail="Personal KB edit permission required")
        return
    _require_admin(user_id)


def _node_is_within_kb(node_id: int, kb_id: int) -> bool:
    current = int(node_id)
    target = int(kb_id)
    visited: Set[int] = set()
    while current not in visited:
        if current == target:
            return True
        visited.add(current)
        row = get_node(current)
        parent_id = as_int((row or {}).get("parent_id"))
        if parent_id is None:
            return False
        current = parent_id
    return False


def _require_node_edit_access(row: Dict[str, Any], user_id: int) -> None:
    _require_kb_edit_access(row, user_id)


def _require_personal_deletion_owner(deletion_id: int, user_id: int) -> Dict[str, Any]:
    row = fetch_one(
        """
        SELECT
            dd.deletion_id,
            dd.object_id,
            dd.node_id,
            dd.ai_document_id,
            dd.status,
            tn.kb_type,
            tn.kb_owner_user_id,
            so.upload_mode,
            so.uploader_user_id
          FROM document_deletions dd
          LEFT JOIN tree_nodes tn ON tn.node_id = dd.node_id
          LEFT JOIN storage_objects so ON so.object_id = dd.object_id
         WHERE dd.deletion_id=%s
         LIMIT 1
        """,
        (int(deletion_id),),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Personal deleted document not found")
    owned_by_tree = row.get("kb_type") == "personal" and int(row.get("kb_owner_user_id") or 0) == int(user_id)
    owned_by_storage = row.get("upload_mode") == "personal" and int(row.get("uploader_user_id") or 0) == int(user_id)
    if not (owned_by_tree or owned_by_storage):
        raise HTTPException(status_code=403, detail="Personal KB deletion permission required")
    return row


def _node_type(row: Dict[str, Any]) -> str:
    return "KB_FOLDER" if bool(row.get("is_folder")) else "file"


def _row_to_tree_node(row: Dict[str, Any], children: List[Dict[str, Any]]) -> Dict[str, Any]:
    is_folder = bool(row.get("is_folder"))
    object_id = row.get("doc_id")
    branch_type = str(row.get("branch_type") or "general").upper()
    meta = {
        "doc_id": object_id,
        "object_id": object_id,
        "storage_object_id": object_id,
        "storage_object_name": row.get("doc_name"),
        "storage_object_file_right": row.get("file_right"),
        "storage_object_relative_path": row.get("relative_path"),
        "storage_object_sub_path": row.get("sub_path"),
        "mime_type": row.get("mime_type"),
        "branch_type": branch_type,
        "doc_status": row.get("doc_status"),
        "item_type_code": row.get("item_type_code"),
        "kb_root_id": row.get("kb_root_id"),
        "kb_type": row.get("kb_type"),
        "kb_owner_user_id": row.get("kb_owner_user_id"),
        "upload_mode": row.get("upload_mode"),
        "target_group_id": row.get("target_group_id"),
    }
    return {
        "id": row["node_id"],
        "node_id": row["node_id"],
        "parent": row.get("parent_id"),
        "parent_id": row.get("parent_id"),
        "kb_root_id": row.get("kb_root_id"),
        "kb_type": row.get("kb_type"),
        "kb_owner_user_id": row.get("kb_owner_user_id"),
        "name": row.get("alias") or row.get("name"),
        "original_name": row.get("name"),
        "kind": _node_type(row),
        "node_type": _node_type(row),
        "is_folder": is_folder,
        "sort_order": row.get("sort_order") or 0,
        "item_type_code": row.get("item_type_code"),
        "policy_code": row.get("policy_code"),
        "policy_matrix": node_policy_matrix(row),
        "disabled": row.get("doc_status") in {"deleted_pending", "inactive_path_missing"},
        "meta": meta,
        "children": children,
    }


def _fetch_kb_rows(kb_root_id: int) -> List[Dict[str, Any]]:
    return fetch_all(
        """
        SELECT
            n.node_id, n.parent_id, n.kb_root_id, n.doc_id, n.name, n.alias,
            n.kb_type, n.kb_owner_user_id, n.is_folder, n.sort_order, n.item_type_code,
            n.created_by, n.created_at, n.updated_at,
            so.doc_name, so.file_right, so.relative_path, so.sub_path, so.mime_type,
            so.branch_type, so.upload_mode, so.uploader_user_id, so.doc_status,
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
         ORDER BY COALESCE(n.parent_id,0), n.sort_order, n.alias, n.name, n.node_id
        """,
        (int(kb_root_id),),
    )


def _filter_kb_entries_by_document_visibility(
    entries: List[Dict[str, Any]],
    user_id: int,
) -> List[Dict[str, Any]]:
    if is_admin(user_id):
        return entries

    group_ids = active_group_ids_for_user(user_id)
    rows_by_root: Dict[int, List[Dict[str, Any]]] = {}
    states_by_root: Dict[int, Dict[int, Tuple[bool, bool, Set[int]]]] = {}
    visible_entries: List[Dict[str, Any]] = []

    for entry in entries:
        root_id = int(entry["kb_root_id"])
        if root_id not in rows_by_root:
            rows_by_root[root_id] = _fetch_kb_rows(root_id)
            source_rows = rows_by_root[root_id]
            children_by_parent: Dict[int, List[Dict[str, Any]]] = {}
            readable_document_ids: Set[int] = set()

            for row in source_rows:
                parent_id = as_int(row.get("parent_id"))
                if parent_id is not None:
                    children_by_parent.setdefault(parent_id, []).append(row)
                if not bool(row.get("is_folder")) and row_has_document_permission(
                    row,
                    user_id,
                    "r",
                    user_group_ids=group_ids,
                    admin_override=False,
                ):
                    readable_document_ids.add(int(row["node_id"]))

            state_cache: Dict[int, Tuple[bool, bool, Set[int]]] = {}
            visiting: Set[int] = set()

            def subtree_state(node_id: int) -> Tuple[bool, bool, Set[int]]:
                cached = state_cache.get(node_id)
                if cached is not None:
                    return cached
                if node_id in visiting:
                    return False, False, set()

                visiting.add(node_id)
                has_document = False
                has_readable_document = False
                readable_type_codes: Set[int] = set()

                for child in children_by_parent.get(node_id, []):
                    child_id = int(child["node_id"])
                    if bool(child.get("is_folder")):
                        child_has_document, child_has_readable, child_codes = subtree_state(child_id)
                        has_document = has_document or child_has_document
                        has_readable_document = has_readable_document or child_has_readable
                        readable_type_codes.update(child_codes)
                        continue

                    has_document = True
                    if child_id in readable_document_ids:
                        has_readable_document = True
                        item_type_code = child.get("item_type_code")
                        if item_type_code is not None:
                            readable_type_codes.add(int(item_type_code))

                visiting.remove(node_id)
                result = (has_document, has_readable_document, readable_type_codes)
                state_cache[node_id] = result
                return result

            for candidate in entries:
                if int(candidate["kb_root_id"]) == root_id:
                    subtree_state(int(candidate["kb_id"]))
            states_by_root[root_id] = state_cache

        has_document, has_readable_document, readable_type_codes = states_by_root[root_id].get(
            int(entry["kb_id"]),
            (False, False, set()),
        )
        if has_document and not has_readable_document:
            continue

        entry["item_type_codes"] = sorted(readable_type_codes)
        visible_entries.append(entry)

    return visible_entries


def _dup_alias_exists(parent_id: Optional[int], alias: str, exclude_node_id: Optional[int] = None) -> bool:
    return bool(find_child_node_by_alias(parent_id, alias, exclude_node_id=exclude_node_id))


def _refresh_storage_paths_for_nodes(node_ids: Set[int], *, enqueue_jobs: bool = True) -> None:
    if not node_ids:
        return
    placeholders = ",".join(["%s"] * len(node_ids))
    rows = fetch_all(
        f"""
        SELECT node_id, doc_id, parent_id
          FROM tree_nodes
         WHERE doc_id IS NOT NULL
           AND node_id IN ({placeholders})
        """,
        tuple(node_ids),
    )
    for row in rows or []:
        doc_id = as_int(row.get("doc_id"))
        if doc_id is None:
            continue
        rel, sub = rel_sub_for_parent(as_int(row.get("parent_id")))
        execute(
            """
            UPDATE storage_objects
               SET relative_path=%s,
                   sub_path=%s,
                   db_updated_at=NOW()
             WHERE object_id=%s
            """,
            (rel, sub, doc_id),
        )
        if enqueue_jobs:
            ensure_ai_document_info(doc_id)
            enqueue_ai_metadata_job(doc_id)


def _queue_delete_for_doc(node_id: int, object_id: int, user_id: Optional[int]) -> None:
    ai = fetch_one("SELECT ai_document_id FROM ai_document_info WHERE object_id=%s", (int(object_id),))
    execute(
        """
        UPDATE storage_objects
           SET doc_status='deleted_pending',
               db_updated_at=NOW()
         WHERE object_id=%s
        """,
        (int(object_id),),
    )
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
            reason=EXCLUDED.reason,
            updated_at=NOW()
        """,
        (int(object_id), int(node_id), (ai or {}).get("ai_document_id"), user_id),
    )
    ensure_ai_document_info(object_id, index_status="deleted")
    enqueue_ai_job(object_id, "delete", user_id)


def get_personal_kb(user_id: int = Depends(get_current_user_id)):
    user = _fetch_user(user_id)
    return _personal_kb_payload(_personal_kb_row(user_id), user)


def create_personal_kb(
    body: Optional[PersonalKbCreateIn] = Body(default=None),
    user_id: int = Depends(get_current_user_id),
):
    user = _fetch_user(user_id)
    existing = _personal_kb_row(user_id)
    if existing:
        return _personal_kb_payload(existing, user)

    folder_name = _safe_local_folder_name(str(user.get("username") or f"user_{user_id}"))
    display_name = clean_node_name((body.display_name if body else None) or folder_name) or folder_name
    next_row = fetch_one(
        """
        SELECT nextval(pg_get_serial_sequence('tree_nodes', 'node_id')) AS next_id
        """
    )
    next_node_id = int((next_row or {}).get("next_id") or 0)
    if next_node_id <= 0:
        raise HTTPException(status_code=500, detail="Cannot allocate personal KB id")

    node_id = execute(
        """
        INSERT INTO tree_nodes(
            node_id, parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
            name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
        )
        VALUES(%s,NULL,%s,'personal',%s,NULL,%s,%s,TRUE,%s,0,%s,NOW(),NOW())
        """,
        (
            next_node_id,
            next_node_id,
            int(user_id),
            folder_name,
            display_name,
            next_item_type_code(),
            int(user_id),
        ),
        True,
    )
    if int(node_id or 0) != next_node_id:
        execute("UPDATE tree_nodes SET kb_root_id=%s, updated_at=NOW() WHERE node_id=%s", (int(node_id), int(node_id)))

    row = _personal_kb_row(user_id)
    return _personal_kb_payload(row, user)


def list_personal_deleted_documents(
    status: str = Query("pending"),
    user_id: int = Depends(get_current_user_id),
):
    safe_status = str(status or "pending").strip().lower()
    if safe_status not in {"pending", "restored", "purged", "all"}:
        safe_status = "pending"
    params: List[Any] = [int(user_id), int(user_id)]
    cond = ""
    if safe_status != "all":
        cond = "AND dd.status=%s"
        params.append(safe_status)
    rows = fetch_all(
        f"""
        SELECT
            dd.deletion_id,
            dd.object_id,
            dd.node_id,
            dd.ai_document_id,
            dd.status,
            dd.detected_at,
            dd.remain_until,
            dd.detected_by_user_id,
            dd.detected_source,
            dd.reason,
            so.doc_name,
            so.file_right,
            so.relative_path,
            so.sub_path,
            so.branch_type,
            so.doc_status,
            tn.alias,
            tn.item_type_code
          FROM document_deletions dd
          LEFT JOIN storage_objects so ON so.object_id = dd.object_id
          LEFT JOIN tree_nodes tn ON tn.node_id = dd.node_id
         WHERE (
                (tn.kb_type='personal' AND tn.kb_owner_user_id=%s)
             OR (so.upload_mode='personal' AND so.uploader_user_id=%s)
         )
           {cond}
         ORDER BY dd.detected_at DESC, dd.deletion_id DESC
        """,
        tuple(params),
    )
    return {"items": rows or [], "total": len(rows or [])}


def restore_personal_deleted_document(
    deletion_id: int = Path(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    _require_personal_deletion_owner(deletion_id, user_id)
    return admin_service.restore_deleted_document(deletion_id, actor_user_id=user_id)


def purge_personal_deleted_document(
    deletion_id: int = Path(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    _require_personal_deletion_owner(deletion_id, user_id)
    return admin_service.purge_deleted_document(deletion_id, actor_user_id=user_id)

def apply_personal_deleted_document_actions(
    payload: PersonalDeletedActionsIn,
    user_id: int = Depends(get_current_user_id),
):
    results: List[Dict[str, Any]] = []

    for request in payload.requests:
        deletion_id = int(request.deletion_id)
        action = request.action

        try:
            if action == "restore":
                result = restore_personal_deleted_document(
                    deletion_id=deletion_id,
                    user_id=user_id,
                )
            else:
                result = purge_personal_deleted_document(
                    deletion_id=deletion_id,
                    user_id=user_id,
                )

            results.append({
                "deletion_id": deletion_id,
                "action": action,
                "success": True,
                "object_id": result.get("object_id"),
                "restored": result.get("restored"),
                "purged": result.get("purged"),
                "status": result.get("status"),
                "job_id": result.get("job_id") or result.get("delete_job_id"),
                "error": None,
            })
        except HTTPException as exc:
            results.append({
                "deletion_id": deletion_id,
                "action": action,
                "success": False,
                "status_code": exc.status_code,
                "error": _http_exception_message(exc),
            })
        except Exception as exc:
            results.append({
                "deletion_id": deletion_id,
                "action": action,
                "success": False,
                "status_code": 500,
                "error": str(exc) or "Unexpected server error",
            })

    return _batch_response(results)

def _personal_ai_owner_sql() -> str:
    return """
        (
            (
                ai.user_id=%s
                AND (
                    COALESCE(tn.kb_type, '') IN ('personal', 'person')
                 OR COALESCE(so.upload_mode, '') = 'personal'
                 OR COALESCE(dd_tn.kb_type, '') IN ('personal', 'person')
                )
            )
         OR (tn.kb_type='personal' AND tn.kb_owner_user_id=%s)
         OR (so.upload_mode='personal' AND so.uploader_user_id=%s)
         OR (dd_tn.kb_type='personal' AND dd_tn.kb_owner_user_id=%s)
        )
    """


def list_personal_ai_index_jobs(
    status: str = Query("failed"),
    limit: int = Query(200, ge=1, le=5000),
    user_id: int = Depends(get_current_user_id),
):
    safe_status = str(status or "failed").strip().lower()
    params: List[Any] = [int(user_id), int(user_id), int(user_id), int(user_id)]
    cond = ""
    if safe_status != "all":
        cond = "AND j.status=%s"
        params.append(safe_status)
    params.append(max(1, min(int(limit or 200), 5000)))
    rows = fetch_all(
        f"""
        SELECT DISTINCT
            j.job_id,
            j.ai_document_id,
            j.object_id,
            j.action,
            j.status,
            j.retry_count,
            j.scheduled_at,
            j.started_at,
            j.finished_at,
            j.last_error,
            j.created_at,
            j.updated_at,
            ai.doc_name,
            ai.relative_path,
            ai.sub_path,
            ai.file_right,
            ai.user_id,
            ai.index_status
          FROM ai_index_jobs j
          JOIN ai_document_info ai ON ai.ai_document_id = j.ai_document_id
          LEFT JOIN storage_objects so ON so.object_id = COALESCE(j.object_id, ai.object_id)
          LEFT JOIN tree_nodes tn ON tn.doc_id = COALESCE(j.object_id, ai.object_id)
          LEFT JOIN document_deletions dd ON dd.ai_document_id = j.ai_document_id
          LEFT JOIN tree_nodes dd_tn ON dd_tn.node_id = dd.node_id
         WHERE {_personal_ai_owner_sql()}
           {cond}
         ORDER BY j.updated_at DESC, j.job_id DESC
         LIMIT %s
        """,
        tuple(params),
    )
    return {"items": rows or [], "total": len(rows or [])}


def retry_personal_ai_index_jobs(
    payload: PersonalAiJobsIn,
    user_id: int = Depends(get_current_user_id),
):
    from app.services.features.pipeline_job_service import retry_jobs

    return retry_jobs(
        [item.job_id for item in payload.jobs],
        actor_user_id=int(user_id),
        is_admin=False,
    )


def dispatch_personal_ai_index_jobs(
    payload: PersonalAiJobsIn,
    user_id: int = Depends(get_current_user_id),
):
    raise HTTPException(
        status_code=410,
        detail="Direct dispatch was removed; use the standalone Pipeline Dispatcher",
    )


def list_kb_trees(
    kb_type: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    requested_type = str(kb_type or "").strip().lower()
    params: List[Any] = []
    visibility_sql = "AND (n.kb_type <> 'personal' OR n.kb_owner_user_id=%s)"
    params.append(int(user_id))
    if requested_type == "admin":
        visibility_sql = "AND n.kb_type <> 'personal'"
        params = []
    elif requested_type == "personal":
        visibility_sql = "AND n.kb_type='personal' AND n.kb_owner_user_id=%s"
        params = [int(user_id)]
    rows = fetch_all(
        f"""
        SELECT
            n.node_id AS kb_id,
            n.node_id AS id,
            n.alias AS name,
            n.name AS original_name,
            n.parent_id,
            n.kb_root_id,
            n.kb_type,
            n.kb_owner_user_id,
            n.item_type_code,
            (
                SELECT STRING_AGG(DISTINCT doc.item_type_code::text, ',' ORDER BY doc.item_type_code::text)
                  FROM tree_nodes doc
                  JOIN storage_objects doc_so ON doc_so.object_id = doc.doc_id
                 WHERE doc.kb_root_id = n.kb_root_id
                   AND doc.is_folder = FALSE
                   AND doc.doc_id IS NOT NULL
                   AND doc.item_type_code IS NOT NULL
                   AND doc_so.doc_status = 'active'
                   AND (n.parent_id IS NULL OR doc.item_type_code = n.item_type_code)
            ) AS document_item_type_codes,
            n.sort_order,
            n.created_at
          FROM tree_nodes n
         WHERE n.is_folder=TRUE
           AND (n.parent_id IS NULL OR n.parent_id = n.kb_root_id)
           {visibility_sql}
         ORDER BY
            CASE WHEN n.parent_id IS NULL THEN 0 ELSE 1 END,
            n.item_type_code,
            n.sort_order,
            n.alias,
            n.node_id
        """,
        tuple(params),
    )
    for row in rows or []:
        raw_codes = str(row.pop("document_item_type_codes", "") or "")
        row["item_type_codes"] = [int(code) for code in raw_codes.split(",") if code.strip()]
    return _filter_kb_entries_by_document_visibility(rows or [], user_id)


def list_perm_policies(user_id: int = Depends(get_current_user_id)):
    _require_admin(user_id)
    rows = fetch_all(
        """
        SELECT
            policy_code,
            owner_r, owner_w, owner_x,
            group_r, group_w, group_x,
            other_r, other_w, other_x
          FROM perm_policies
         ORDER BY policy_code
        """
    )
    out = []
    for row in rows or []:
        out.append(
            {
                "policy_code": row.get("policy_code"),
                "matrix": node_policy_matrix(row),
                "owner": {
                    "r": bool(row.get("owner_r")),
                    "w": bool(row.get("owner_w")),
                    "x": bool(row.get("owner_x")),
                },
                "group": {
                    "r": bool(row.get("group_r")),
                    "w": bool(row.get("group_w")),
                    "x": bool(row.get("group_x")),
                },
                "other": {
                    "r": bool(row.get("other_r")),
                    "w": bool(row.get("other_w")),
                    "x": bool(row.get("other_x")),
                },
            }
        )
    return {"items": out}


def get_kb_tree(
    kb_id: int = Path(..., ge=1),
    include_empty_folders: bool = Query(False),
    admin_view: bool = Query(False),
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    if root.get("kb_type") == "personal" and not _is_personal_kb_owner(root, user_id):
        raise HTTPException(status_code=403, detail="Personal KB permission required")
    rows = _fetch_kb_rows(int(root["kb_root_id"]))
    user_is_admin = is_admin(user_id)
    if admin_view and not user_is_admin:
        raise HTTPException(status_code=403, detail="Admin permission required")
    use_admin_view = bool(admin_view and user_is_admin)
    group_ids = active_group_ids_for_user(user_id) if not use_admin_view else set()
    visible_document_ids: Set[int] = set()
    for row in rows or []:
        if not bool(row.get("is_folder")) and row_has_document_permission(
            row,
            user_id,
            "r",
            user_group_ids=group_ids,
            admin_override=use_admin_view,
        ):
            visible_document_ids.add(int(row["node_id"]))

    by_parent: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for row in rows or []:
        by_parent.setdefault(as_int(row.get("parent_id")), []).append(row)

    def build(parent_id: Optional[int]) -> Tuple[List[Dict[str, Any]], bool, bool]:
        out: List[Dict[str, Any]] = []
        has_document = False
        has_visible_document = False
        siblings = sorted(
            by_parent.get(parent_id, []),
            key=lambda r: (int(r.get("sort_order") or 0), str(r.get("alias") or r.get("name") or "").lower(), int(r["node_id"])),
        )
        for row in siblings:
            if not bool(row.get("is_folder")):
                has_document = True
                if int(row["node_id"]) in visible_document_ids:
                    has_visible_document = True
                    out.append(_row_to_tree_node(row, []))
                continue

            children, child_has_document, child_has_visible_document = build(int(row["node_id"]))
            has_document = has_document or child_has_document
            has_visible_document = has_visible_document or child_has_visible_document

            # A genuinely empty folder remains visible. A folder whose subtree
            # contains documents but none readable by this user is hidden.
            if include_empty_folders or child_has_visible_document or not child_has_document:
                out.append(_row_to_tree_node(row, children))
        return out, has_document, has_visible_document

    nodes, _, _ = build(int(root["node_id"]))

    return {
        "kb_id": int(root["node_id"]),
        "kb_name": root.get("alias") or root.get("name"),
        "is_user_kb": root.get("kb_type") == "personal",
        "item_type_code": root.get("item_type_code"),
        "nodes": nodes,
    }


def update_kb_permissions_bulk(
    kb_id: int,
    body: KBPermissionsBulkIn,
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    _require_admin(user_id)
    if str(root.get("kb_type") or "").strip().lower() == "personal":
        raise HTTPException(status_code=400, detail="Personal KB documents always use PRIVATE policy")

    node_ids = list(dict.fromkeys(int(node_id) for node_id in body.node_ids if int(node_id) > 0))
    if not node_ids:
        raise HTTPException(status_code=400, detail="No document nodes were selected")

    policy_code = str(body.policy_code or "").strip().upper()
    policy = fetch_one(
        """
        SELECT policy_code, group_r, group_w, group_x, other_r, other_w, other_x
          FROM perm_policies
         WHERE policy_code=%s
        """,
        (policy_code,),
    )
    if not policy:
        raise HTTPException(status_code=400, detail="Unknown policy_code")

    group_required = policy_requires_target_group(policy)
    target_group_id = int(body.target_group_id) if body.target_group_id is not None else None
    if group_required:
        if target_group_id is None:
            raise HTTPException(status_code=400, detail="target_group_id is required for GROUP policy")
        group = fetch_one(
            "SELECT group_id FROM org_groups WHERE group_id=%s AND is_active=TRUE",
            (target_group_id,),
        )
        if not group:
            raise HTTPException(status_code=400, detail="Unknown active target_group_id")
    else:
        target_group_id = None

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH RECURSIVE subtree AS (
                    SELECT node_id
                      FROM tree_nodes
                     WHERE node_id=%s
                    UNION ALL
                    SELECT child.node_id
                      FROM tree_nodes child
                      JOIN subtree parent ON child.parent_id=parent.node_id
                )
                SELECT n.node_id, n.doc_id, n.created_by, ab.owner_user_id
                  FROM tree_nodes n
                  JOIN subtree s ON s.node_id=n.node_id
                  LEFT JOIN acl_bindings ab ON ab.node_id=n.node_id
                 WHERE n.node_id=ANY(%s)
                   AND n.is_folder=FALSE
                   AND n.doc_id IS NOT NULL
                   AND n.kb_type <> 'personal'
                 FOR UPDATE OF n
                """,
                (int(kb_id), node_ids),
            )
            rows = cursor.fetchall() or []
            for row in rows:
                owner_user_id = (
                    as_int(row.get("owner_user_id"))
                    or as_int(row.get("created_by"))
                    or int(user_id)
                )
                cursor.execute(
                    """
                    INSERT INTO acl_bindings(
                        node_id, policy_code, owner_user_id, target_group_id,
                        created_at, updated_at
                    )
                    VALUES(%s,%s,%s,%s,NOW(),NOW())
                    ON CONFLICT (node_id) DO UPDATE SET
                        policy_code=EXCLUDED.policy_code,
                        owner_user_id=EXCLUDED.owner_user_id,
                        target_group_id=EXCLUDED.target_group_id,
                        updated_at=NOW()
                    """,
                    (
                        int(row["node_id"]),
                        policy_code,
                        owner_user_id,
                        target_group_id,
                    ),
                )

    for row in rows:
        object_id = as_int(row.get("doc_id"))
        if object_id is not None:
            ensure_ai_document_info(object_id)
            enqueue_ai_metadata_job(object_id, user_id)

    updated = len(rows)
    return {
        "requested": len(node_ids),
        "matched": updated,
        "updated": updated,
        "skipped": len(node_ids) - updated,
        "policy_code": policy_code,
        "target_group_id": target_group_id,
    }


def create_kb_node(
    kb_id: int,
    data: KBNodeIn,
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    _require_kb_edit_access(root, user_id)
    name = clean_node_name(data.name)
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    parent_id = data.parent_id if data.parent_id is not None else kb_id
    parent = get_node(parent_id)
    if not parent or not bool(parent.get("is_folder")):
        raise HTTPException(status_code=404, detail="Parent folder not found")
    if not _node_is_within_kb(int(parent_id), int(root["node_id"])):
        raise HTTPException(status_code=403, detail="Parent folder must belong to the selected KB")
    _require_node_edit_access(parent, user_id)

    if _dup_alias_exists(parent_id, name):
        raise HTTPException(status_code=409, detail="A node with the same name already exists")

    is_top_category = parent.get("parent_id") is None
    item_type_code = next_item_type_code() if is_top_category else int(parent.get("item_type_code") or 10)
    node_id = execute(
        """
        INSERT INTO tree_nodes(
            parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
            name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
        )
        VALUES(%s,%s,%s,%s,NULL,%s,%s,TRUE,%s,%s,%s,NOW(),NOW())
        """,
        (
            parent_id,
            parent["kb_root_id"],
            parent.get("kb_type") or "admin",
            parent.get("kb_owner_user_id"),
            name,
            name,
            item_type_code,
            next_sort_order(parent_id),
            user_id,
        ),
        True,
    )
    policy_code = "PRIVATE" if str(parent.get("kb_type") or "").lower() == "personal" else "PUBLIC"
    ensure_acl(node_id, policy_code, owner_user_id=user_id)
    return {"node_id": node_id, "id": node_id, "message": "Created"}


def patch_kb_node(
    node_id: int,
    data: KBNodePatch,
    user_id: int = Depends(get_current_user_id),
):
    row = get_node(node_id)
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    _require_node_edit_access(row, user_id)

    fields_set = getattr(data, "model_fields_set", set())
    sets: List[str] = []
    params: List[Any] = []
    new_parent = as_int(row.get("parent_id"))
    item_type_code = as_int(row.get("item_type_code"), 10) or 10
    acl_changed = False

    if "name" in fields_set and data.name is not None:
        alias = clean_node_name(data.name)
        if not alias:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        if _dup_alias_exists(new_parent, alias, exclude_node_id=node_id):
            raise HTTPException(status_code=409, detail="A node with the same name already exists")
        if bool(row.get("is_folder")) and str(row.get("kb_type") or "").lower() == "personal":
            sets.extend(["name=%s", "alias=%s"])
            params.extend([alias, alias])
        else:
            sets.append("alias=%s")
            params.append(alias)

    if "parent_id" in fields_set:
        target_parent = data.parent_id
        if target_parent == node_id:
            raise HTTPException(status_code=400, detail="A node cannot be its own parent")
        if target_parent is None:
            raise HTTPException(status_code=400, detail="Root move is not supported")
        parent = get_node(target_parent)
        if not parent or not bool(parent.get("is_folder")):
            raise HTTPException(status_code=404, detail="Parent folder not found")
        _require_node_edit_access(parent, user_id)
        if (
            as_int(parent.get("kb_root_id")) != as_int(row.get("kb_root_id"))
            or str(parent.get("kb_type") or "").lower() != str(row.get("kb_type") or "").lower()
            or as_int(parent.get("kb_owner_user_id")) != as_int(row.get("kb_owner_user_id"))
        ):
            raise HTTPException(status_code=403, detail="A node can only be moved within the same KB")
        descendants = collect_descendants([node_id])
        if int(target_parent) in descendants:
            raise HTTPException(status_code=400, detail="Cannot move a node into its own descendant")
        alias_for_dup = str(row.get("alias") or row.get("name") or "")
        if _dup_alias_exists(target_parent, alias_for_dup, exclude_node_id=node_id):
            raise HTTPException(status_code=409, detail="A node with the same name already exists")
        new_parent = int(target_parent)
        item_type_code = int(parent.get("item_type_code") or 10)
        sets.extend(["parent_id=%s", "kb_root_id=%s", "item_type_code=%s"])
        params.extend([new_parent, parent["kb_root_id"], item_type_code])

    if "item_type_code" in fields_set and data.item_type_code is not None:
        _require_admin(user_id)
        if not bool(row.get("is_folder")):
            raise HTTPException(status_code=400, detail="item_type_code can only be changed on top-level category folders")
        if "parent_id" in fields_set:
            raise HTTPException(status_code=400, detail="Move and item_type_code change must be handled separately")
        row_node_id = as_int(row.get("node_id"))
        row_parent_id = as_int(row.get("parent_id"))
        row_kb_root_id = as_int(row.get("kb_root_id"))
        if (
            row_node_id is None
            or row_parent_id is None
            or row_kb_root_id is None
            or row_node_id == row_kb_root_id
            or row_parent_id != row_kb_root_id
        ):
            raise HTTPException(status_code=400, detail="item_type_code can only be changed on top-level category folders")
        item_type_code = int(data.item_type_code)
        duplicate_code = fetch_one(
            """
            SELECT node_id
              FROM tree_nodes
             WHERE kb_root_id=%s
               AND parent_id=kb_root_id
               AND node_id<>%s
               AND item_type_code=%s
             LIMIT 1
            """,
            (row_kb_root_id, row_node_id, item_type_code),
        )
        if duplicate_code:
            raise HTTPException(status_code=409, detail="item_type_code already exists in this KB top-level category")
        sets.append("item_type_code=%s")
        params.append(item_type_code)

    if "sort_order" in fields_set and data.sort_order is not None:
        sets.append("sort_order=%s")
        params.append(int(data.sort_order))

    if "policy_code" in fields_set and data.policy_code is not None:
        _require_admin(user_id)
        if bool(row.get("is_folder")):
            raise HTTPException(status_code=400, detail="Folder ACL is not supported")
        if str(row.get("kb_type") or "").strip().lower() == "personal":
            raise HTTPException(status_code=400, detail="Personal KB documents always use PRIVATE policy")
        policy_code = str(data.policy_code or "").strip().upper()
        policy = fetch_one(
            """
            SELECT policy_code, group_r, group_w, group_x, other_r, other_w, other_x
              FROM perm_policies
             WHERE policy_code=%s
            """,
            (policy_code,),
        )
        if not policy:
            raise HTTPException(status_code=400, detail="Unknown policy_code")
        binding = fetch_one(
            "SELECT owner_user_id, target_group_id FROM acl_bindings WHERE node_id=%s",
            (int(node_id),),
        ) or {}
        if "target_group_id" in fields_set:
            target_group_id = as_int(data.target_group_id)
        else:
            target_group_id = as_int(binding.get("target_group_id"))
        group_required = policy_requires_target_group(policy)
        if group_required and target_group_id is not None:
            group = fetch_one("SELECT group_id FROM org_groups WHERE group_id=%s AND is_active=TRUE", (target_group_id,))
            if not group:
                raise HTTPException(status_code=400, detail="Unknown active target_group_id")
        if group_required and target_group_id is None:
            raise HTTPException(status_code=400, detail="target_group_id is required for GROUP policy")
        owner_user_id = as_int(binding.get("owner_user_id")) or as_int(row.get("created_by")) or as_int(row.get("kb_owner_user_id")) or user_id
        ensure_acl(int(node_id), policy_code, owner_user_id=owner_user_id, target_group_id=target_group_id if group_required else None)
        acl_changed = True

    if not sets:
        if acl_changed:
            object_id = as_int(row.get("doc_id"))
            if object_id is not None:
                ensure_ai_document_info(object_id)
                enqueue_ai_metadata_job(object_id, user_id)
            return {"message": "Updated"}
        return {"message": "No changes"}

    params.append(int(node_id))
    execute(f"UPDATE tree_nodes SET {', '.join(sets)}, updated_at=NOW() WHERE node_id=%s", tuple(params))

    descendants = collect_descendants([node_id])
    if "parent_id" in fields_set or "item_type_code" in fields_set:
        update_subtree_item_type(node_id, item_type_code, created_by=user_id)
    if "parent_id" in fields_set or "name" in fields_set:
        _refresh_storage_paths_for_nodes(descendants, enqueue_jobs=True)
    if acl_changed:
        object_id = as_int(row.get("doc_id"))
        if object_id is not None:
            ensure_ai_document_info(object_id)
            enqueue_ai_metadata_job(object_id, user_id)

    return {"message": "Updated"}


def _bulk_delete_nodes(kb_id: int, node_ids: List[int], user_id: int) -> Tuple[int, List[str]]:
    ids = [int(x) for x in dict.fromkeys(node_ids or []) if x is not None]
    if not ids:
        return 0, []
    all_ids = collect_descendants(ids)
    if not all_ids:
        return 0, []
    placeholders = ",".join(["%s"] * len(all_ids))
    docs = fetch_all(
        f"SELECT node_id, doc_id FROM tree_nodes WHERE doc_id IS NOT NULL AND node_id IN ({placeholders})",
        tuple(all_ids),
    )
    for doc in docs or []:
        if doc.get("doc_id") is not None:
            _queue_delete_for_doc(int(doc["node_id"]), int(doc["doc_id"]), user_id)
    if not docs:
        empty_folder_ids = [
            int(row["node_id"])
            for row in fetch_all(
                f"SELECT node_id FROM tree_nodes WHERE doc_id IS NULL AND node_id IN ({placeholders})",
                tuple(all_ids),
            )
        ]
        if empty_folder_ids:
            ph = ",".join(["%s"] * len(empty_folder_ids))
            execute(f"DELETE FROM tree_nodes WHERE node_id IN ({ph})", tuple(empty_folder_ids))
    return len(all_ids), []


def delete_kb_nodes_bulk(
    kb_id: int,
    body: KBNodesBulkDeleteIn,
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    _require_kb_edit_access(root, user_id)
    for node_id in body.node_ids:
        if int(node_id) == int(root["node_id"]) or not _node_is_within_kb(int(node_id), int(root["node_id"])):
            raise HTTPException(status_code=403, detail="Only descendants of the selected KB can be deleted")
    deleted, removed = _bulk_delete_nodes(kb_id, body.node_ids, user_id)
    return {"deleted": deleted, "files_removed": removed}


def delete_kb_nodes_bulk_safe(
    kb_id: int,
    body: KBNodesBulkDeleteIn,
    user_id: int = Depends(get_current_user_id),
):
    return delete_kb_nodes_bulk(kb_id=kb_id, body=body, user_id=user_id)


def delete_kb_nodes_bulk_post(
    kb_id: int,
    body: KBNodesBulkDeleteIn,
    user_id: int = Depends(get_current_user_id),
):
    return delete_kb_nodes_bulk(kb_id=kb_id, body=body, user_id=user_id)


def reorder_kb_nodes(
    kb_id: int,
    body: KBNodesReorderIn,
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    _require_kb_edit_access(root, user_id)
    parent_id = body.parent_id if body.parent_id is not None else int(root["node_id"])
    if not _node_is_within_kb(int(parent_id), int(root["node_id"])):
        raise HTTPException(status_code=403, detail="Reorder parent must belong to the selected KB")
    if not body.orders:
        return {"updated": 0}
    count = 0
    for node_id, sort_order in body.orders:
        row = get_node(int(node_id))
        if not row:
            continue
        _require_node_edit_access(row, user_id)
        if not _node_is_within_kb(int(node_id), int(root["node_id"])):
            raise HTTPException(status_code=403, detail="Only nodes in the selected KB can be reordered")
        if as_int(row.get("parent_id")) != body.parent_id:
            continue
        execute("UPDATE tree_nodes SET sort_order=%s, updated_at=NOW() WHERE node_id=%s", (int(sort_order), int(node_id)))
        count += 1
    return {"updated": count}


def create_kb_tree(
    body: KbCreateIn,
    user_id: int = Depends(get_current_user_id),
):
    _require_admin(user_id)
    name = clean_node_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    se_root = fetch_one(
        """
        SELECT node_id, kb_root_id, kb_type, kb_owner_user_id
          FROM tree_nodes
         WHERE parent_id IS NULL
         ORDER BY CASE WHEN name='SE KB' THEN 0 ELSE 1 END, node_id
         LIMIT 1
        """
    )
    if not se_root:
        raise HTTPException(status_code=500, detail="SE KB root not found")
    if _dup_alias_exists(int(se_root["node_id"]), name):
        raise HTTPException(status_code=409, detail="A KB with the same name already exists")
    item_type_code = next_item_type_code()
    node_id = execute(
        """
        INSERT INTO tree_nodes(
            parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
            name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
        )
        VALUES(%s,%s,'admin',%s,NULL,%s,%s,TRUE,%s,%s,%s,NOW(),NOW())
        """,
        (
            int(se_root["node_id"]),
            int(se_root["kb_root_id"]),
            user_id,
            name,
            name,
            item_type_code,
            next_sort_order(int(se_root["node_id"])),
            user_id,
        ),
        True,
    )
    ensure_acl(node_id, "PUBLIC", owner_user_id=user_id)
    return {"kb_id": node_id, "id": node_id, "name": name, "kb_type": body.kb_type, "is_user_kb": body.is_user_kb, "kind": body.kind}


def delete_kb_tree(
    kb_id: int,
    user_id: int = Depends(get_current_user_id),
):
    _require_admin(user_id)
    root = get_kb_entry(kb_id)
    if root.get("parent_id") is None:
        raise HTTPException(status_code=400, detail="Root KB cannot be deleted")
    ids = collect_descendants([kb_id])
    ph = ",".join(["%s"] * len(ids))
    docs = fetch_all(f"SELECT node_id, doc_id FROM tree_nodes WHERE doc_id IS NOT NULL AND node_id IN ({ph})", tuple(ids))
    for doc in docs or []:
        if doc.get("doc_id") is not None:
            _queue_delete_for_doc(int(doc["node_id"]), int(doc["doc_id"]), user_id)
    if not docs:
        execute(f"DELETE FROM tree_nodes WHERE node_id IN ({ph})", tuple(ids))
        return {"ok": True, "kb_id": kb_id, "deleted": "physical_empty_tree"}
    return {"ok": True, "kb_id": kb_id, "deleted": "soft_pending"}
