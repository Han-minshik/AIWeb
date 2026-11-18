from __future__ import annotations

import re
import os
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from app.config.settings import (
    AI_INDEX_CALLBACK_TOKEN,
    AI_INDEX_CALLBACK_URL,
    PARSER_API_TOKEN,
    PARSER_DISPATCH_URL,
    PARSER_MAX_ATTEMPTS,
    PARSER_TIMEOUT,
    PERSONAL_STORAGE_BASE_DIR,
    SYNC_KIWIX_DOCUMENTS_JSON,
)
from app.dao import admin_dao
from app.repositories.common import execute, fetch_all, fetch_one
from app.services import notifications_service
from app.services.features import ai_index_service
from app.services.features import minio_document_lifecycle_service
from app.services.features.document_db import (
    base_dir_for_storage_row,
    enqueue_ai_job,
    resolve_storage_path,
)


_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(v) for v in value]
    return value


def _json_safe_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k): _json_safe_value(v) for k, v in payload.items()}


def _parser_base_dir(base_dir: str, storage_scope: str, row: Dict[str, Any]) -> str:
    scope = str(storage_scope or "").strip().lower()
    if scope == "personal":
        personal_base = os.path.abspath(PERSONAL_STORAGE_BASE_DIR)
        relative_path = str(row.get("relative_path") or "").replace("\\", "/").strip("/")
        owner_folder = relative_path.split("/", 1)[0] if relative_path else ""
        if owner_folder and os.path.abspath(str(base_dir or "")) == personal_base:
            return str(Path(personal_base) / owner_folder)
    return base_dir


def list_users(
    q: Optional[str],
    page: int,
    page_size: int,
    group_id: Optional[int] = None,
    group_empty: bool = False,
    position_id: Optional[int] = None,
    position_empty: bool = False,
    is_admin: Optional[bool] = None,
) -> Dict[str, Any]:
    return admin_dao.list_users(
        q=q,
        page=page,
        page_size=page_size,
        group_id=group_id,
        group_empty=group_empty,
        position_id=position_id,
        position_empty=position_empty,
        is_admin=is_admin,
    )


def _to_optional_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return int(v)
    except Exception:
        return None


def _validate_email_or_422(email: str) -> str:
    safe = (email or "").strip()
    if not safe:
        raise HTTPException(status_code=422, detail="email is required")
    if len(safe) > 254 or not _EMAIL_RE.fullmatch(safe):
        raise HTTPException(status_code=422, detail="invalid email format")
    return safe


def create_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    if not username or not password:
        raise HTTPException(status_code=422, detail="username and password are required")

    raw_org = payload.get("org_id", payload.get("orgId", 1))
    try:
        org_id = int(raw_org)
    except Exception:
        org_id = 1

    group_id = _to_optional_int(
        payload.get("group_id", payload.get("department_id", payload.get("departmentId")))
    )
    position_id = _to_optional_int(payload.get("position_id", payload.get("positionId")))
    full_name = (payload.get("full_name") or payload.get("fullName") or "").strip() or username
    raw_email = (payload.get("email") or "").strip()
    email = raw_email if raw_email else f"{username}@local.invalid"
    email = _validate_email_or_422(email)

    user_id = admin_dao.create_user(
        org_id=org_id,
        username=username,
        email=email,
        password=password,
        full_name=full_name,
        group_id=group_id,
        position_id=position_id,
        is_admin=bool(payload.get("is_admin")),
        is_active=bool(payload.get("is_active", True)),
    )
    _sync_representative_group(user_id, group_id)
    return {"user_id": user_id}


def update_user(user_id: int, patch: Dict[str, Any], actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    before = admin_dao.get_user_detail(user_id)
    if not before:
        raise HTTPException(status_code=404, detail="User not found")

    fields: dict[str, Any] = {}
    group_field_present = "group_id" in patch or "department_id" in patch or "departmentId" in patch

    if group_field_present:
        fields["group_id"] = _to_optional_int(
            patch.get("group_id", patch.get("department_id", patch.get("departmentId")))
        )

    if "position_id" in patch or "positionId" in patch:
        fields["position_id"] = _to_optional_int(patch.get("position_id", patch.get("positionId")))

    if "email" in patch and patch["email"] is not None:
        fields["email"] = _validate_email_or_422(str(patch["email"]))

    for col in ("username", "full_name"):
        if col in patch and patch[col] is not None:
            fields[col] = str(patch[col]).strip()

    if "is_admin" in patch:
        fields["is_admin"] = bool(patch["is_admin"])
    if "is_active" in patch:
        fields["is_active"] = bool(patch["is_active"])

    if "password" in patch and (patch["password"] or "").strip():
        fields["password"] = patch["password"]

    if group_field_present:
        before_group_id = _to_optional_int(before.get("group_id"))
        next_group_id = fields.get("group_id")
        if before_group_id is not None and before_group_id != next_group_id:
            _deactivate_representative_group(user_id, before_group_id)

    if fields:
        admin_dao.update_user_fields(user_id, fields)
        if "group_id" in fields:
            _sync_representative_group(user_id, fields.get("group_id"))

    row = admin_dao.get_user_detail(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    changes: list[str] = []
    if "is_admin" in patch:
        prev_v = int(before.get("is_admin") or 0)
        next_v = int(row.get("is_admin") or 0)
        if prev_v != next_v:
            changes.append(f"is_admin:{prev_v}->{next_v}")
    if "is_active" in patch:
        prev_v = int(before.get("is_active") or 0)
        next_v = int(row.get("is_active") or 0)
        if prev_v != next_v:
            changes.append(f"is_active:{prev_v}->{next_v}")
    if changes:
        summary = ", ".join(changes)
        notifications_service.emit_notification(
            user_id=user_id,
            kind="system",
            severity="warning",
            title="Account permission changed",
            body=f"changes={summary}, by={actor_user_id or '-'}",
        )

    return row


def toggle_admin(user_id: int, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    before = admin_dao.get_user_detail(user_id)
    admin_dao.toggle_admin(user_id)
    row = admin_dao.get_user_detail(user_id)
    prev_v = int((before or {}).get("is_admin") or 0)
    next_v = int((row or {}).get("is_admin") or 0)
    if prev_v != next_v:
        notifications_service.emit_notification(
            user_id=user_id,
            kind="system",
            severity="warning",
            title="Admin permission changed",
            body=f"is_admin:{prev_v}->{next_v}, by={actor_user_id or '-'}",
        )
    return {"id": user_id, "is_admin": row.get("is_admin") if row else None}


def reset_password(user_id: int, password: str) -> Dict[str, Any]:
    if not (password or "").strip():
        raise HTTPException(status_code=422, detail="password is required")
    admin_dao.reset_password(user_id=user_id, password=password)
    return {"user_id": user_id, "message": "Password reset completed"}


def delete_user(user_id: int, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    target = admin_dao.get_user_detail(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if actor_user_id is not None and int(actor_user_id) == int(user_id):
        raise HTTPException(status_code=409, detail="You cannot deactivate your own account")
    if not bool(target.get("is_active")):
        return {"message": "User is already deactivated", "user_id": user_id, "is_active": False}
    if bool(target.get("is_admin")):
        row = fetch_one(
            "SELECT COUNT(*) AS cnt FROM users WHERE is_admin=TRUE AND is_active=TRUE"
        ) or {}
        if int(row.get("cnt") or 0) <= 1:
            raise HTTPException(status_code=409, detail="The last active administrator cannot be deactivated")
    admin_dao.delete_user(user_id)
    return {"message": "User deactivated", "user_id": user_id, "is_active": False}


def permanently_delete_user(user_id: int, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    target = admin_dao.get_user_detail(user_id)
    if not target:
        return {"message": "User already deleted", "user_id": user_id, "deleted": True}
    if actor_user_id is not None and int(actor_user_id) == int(user_id):
        raise HTTPException(status_code=409, detail="You cannot permanently delete your own account")
    if bool(target.get("is_active")):
        raise HTTPException(status_code=409, detail="Deactivate the user before permanent deletion")

    personal_objects = fetch_all(
        """
        SELECT so.object_id, so.storage_backend, so.doc_status, tn.node_id
          FROM storage_objects so
          LEFT JOIN tree_nodes tn ON tn.doc_id=so.object_id
         WHERE so.uploader_user_id=%s
           AND so.upload_mode='personal'
         ORDER BY so.object_id
        """,
        (int(user_id),),
    )
    legacy = [row for row in personal_objects or [] if str(row.get("storage_backend") or "local").lower() != "minio"]
    if legacy:
        raise HTTPException(
            status_code=409,
            detail="Legacy local personal documents must be migrated or removed before deleting the user",
        )

    from app.services.features import kb_service

    requested = 0
    for row in personal_objects or []:
        object_id = int(row["object_id"])
        node_id = row.get("node_id")
        if str(row.get("doc_status") or "active") == "active":
            if node_id is None:
                raise HTTPException(status_code=409, detail=f"Personal document node is missing: object_id={object_id}")
            kb_service._queue_delete_for_doc(int(node_id), object_id, actor_user_id)
        deletion = fetch_one(
            """
            SELECT deletion_id
              FROM document_deletions
             WHERE object_id=%s AND status IN ('pending','purging','purge_failed')
             ORDER BY deletion_id DESC LIMIT 1
            """,
            (object_id,),
        )
        if not deletion:
            raise HTTPException(status_code=409, detail=f"Personal document deletion record is missing: object_id={object_id}")
        minio_document_lifecycle_service.request_minio_purge(
            int(deletion["deletion_id"]),
            actor_user_id=actor_user_id,
        )
        requested += 1

    remaining = fetch_one(
        """
        SELECT COUNT(*) AS cnt
          FROM storage_objects
         WHERE uploader_user_id=%s AND upload_mode='personal'
        """,
        (int(user_id),),
    ) or {}
    remaining_count = int(remaining.get("cnt") or 0)
    if remaining_count:
        return {
            "message": "Personal document purge is in progress",
            "user_id": user_id,
            "deleted": False,
            "purge_requested": requested,
            "remaining_documents": remaining_count,
        }

    execute("DELETE FROM notifications WHERE user_id=%s", (int(user_id),))
    execute(
        "UPDATE storage_objects SET uploader_user_id=NULL WHERE uploader_user_id=%s",
        (int(user_id),),
    )
    execute(
        "DELETE FROM tree_nodes WHERE kb_type='personal' AND kb_owner_user_id=%s AND node_id<>kb_root_id",
        (int(user_id),),
    )
    execute(
        "DELETE FROM tree_nodes WHERE kb_type='personal' AND kb_owner_user_id=%s AND node_id=kb_root_id",
        (int(user_id),),
    )
    admin_dao.hard_delete_user(user_id)
    return {"message": "User permanently deleted", "user_id": user_id, "deleted": True}


def access_logs(limit: int, user_id: Optional[int]) -> list[dict]:
    return admin_dao.list_access_logs(limit=limit, user_id=user_id)


def list_departments() -> list[dict]:
    return admin_dao.list_departments()


def create_department(name: str) -> dict:
    safe_name = (name or "").strip()
    if not safe_name:
        raise HTTPException(status_code=422, detail="department name is required")
    return admin_dao.create_department(name=safe_name)


def delete_department(group_id: int) -> Dict[str, Any]:
    return delete_group(group_id)


def _sync_representative_group(user_id: int, group_id: Optional[int]) -> None:
    if group_id is None:
        return
    execute(
        """
        INSERT INTO group_members(group_id, user_id, role, is_active, created_at, updated_at)
        VALUES(%s,%s,'member',TRUE,NOW(),NOW())
        ON CONFLICT (group_id, user_id) DO UPDATE SET
            is_active=TRUE,
            updated_at=NOW()
        """,
        (int(group_id), int(user_id)),
    )


def _deactivate_representative_group(user_id: int, group_id: int) -> None:
    row = fetch_one(
        """
        SELECT group_member_id
          FROM group_members
         WHERE group_id=%s
           AND user_id=%s
           AND is_active=TRUE
         LIMIT 1
        """,
        (int(group_id), int(user_id)),
    )
    if not row:
        return
    update_group_member(int(group_id), int(user_id), {"is_active": 0})


def _active_manager_count(group_id: int) -> int:
    row = fetch_one(
        """
        SELECT COUNT(*) AS cnt
          FROM group_members
         WHERE group_id=%s
           AND role='manager'
           AND is_active=TRUE
        """,
        (int(group_id),),
    )
    return int((row or {}).get("cnt") or 0)


def _active_group_member_count(group_id: int) -> int:
    row = fetch_one(
        """
        SELECT COUNT(*) AS cnt
          FROM group_members
         WHERE group_id=%s
           AND is_active=TRUE
        """,
        (int(group_id),),
    )
    return int((row or {}).get("cnt") or 0)


def _group_acl_documents(group_id: int, limit: int = 500) -> list[dict]:
    return fetch_all(
        """
        SELECT
            ab.acl_id,
            ab.node_id,
            ab.policy_code,
            ab.target_group_id,
            tn.doc_id AS object_id,
            COALESCE(tn.alias, tn.name, '') AS node_name,
            so.doc_name,
            so.relative_path,
            so.sub_path,
            so.file_right,
            so.branch_type,
            so.doc_status,
            ai.index_status,
            ab.updated_at
          FROM acl_bindings ab
          JOIN tree_nodes tn ON tn.node_id = ab.node_id
          LEFT JOIN storage_objects so ON so.object_id = tn.doc_id
          LEFT JOIN ai_document_info ai ON ai.object_id = so.object_id
         WHERE ab.target_group_id=%s
           AND tn.is_folder=FALSE
         ORDER BY
            COALESCE(so.relative_path, ''),
            COALESCE(so.sub_path, ''),
            COALESCE(so.doc_name, tn.alias, tn.name, ''),
            ab.node_id
         LIMIT %s
        """,
        (int(group_id), max(1, min(int(limit or 500), 2000))),
    ) or []


def _group_acl_document_count(group_id: int) -> int:
    row = fetch_one(
        """
        SELECT COUNT(*) AS cnt
          FROM acl_bindings ab
          JOIN tree_nodes tn ON tn.node_id = ab.node_id
         WHERE ab.target_group_id=%s
           AND tn.is_folder=FALSE
        """,
        (int(group_id),),
    )
    return int((row or {}).get("cnt") or 0)


def _clear_representative_group_users(group_id: int, user_id: Optional[int] = None) -> None:
    if user_id is None:
        execute(
            "UPDATE users SET group_id=NULL WHERE group_id=%s",
            (int(group_id),),
        )
        return
    execute(
        "UPDATE users SET group_id=NULL WHERE user_id=%s AND group_id=%s",
        (int(user_id), int(group_id)),
    )


def _set_representative_group_user(group_id: int, user_id: int) -> None:
    execute(
        "UPDATE users SET group_id=%s WHERE user_id=%s",
        (int(group_id), int(user_id)),
    )


def _deactivate_all_group_members(group_id: int) -> None:
    execute(
        """
        UPDATE group_members
           SET is_active=FALSE,
               updated_at=NOW()
         WHERE group_id=%s
           AND is_active=TRUE
        """,
        (int(group_id),),
    )
    _clear_representative_group_users(group_id)


def _assert_group_can_delete(group_id: int) -> None:
    acl_document_count = _group_acl_document_count(group_id)
    if acl_document_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "group has ACL documents and cannot be deleted",
                "acl_document_count": acl_document_count,
            },
        )


def list_groups(q: Optional[str] = None, include_inactive: bool = True) -> Dict[str, Any]:
    params: list[Any] = []
    conds: list[str] = []
    if not include_inactive:
        conds.append("g.is_active=TRUE")
    if q:
        conds.append("(g.name LIKE %s OR g.description LIKE %s)")
        like = f"%{q}%"
        params.extend([like, like])
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = fetch_all(
        f"""
        SELECT
            g.group_id,
            g.name,
            g.description,
            g.is_active,
            g.created_by,
            g.created_at,
            g.updated_at,
            COALESCE(SUM(CASE WHEN gm.is_active=TRUE THEN 1 ELSE 0 END), 0) AS member_count,
            COALESCE(SUM(CASE WHEN gm.is_active=TRUE AND gm.role='manager' THEN 1 ELSE 0 END), 0) AS manager_count
          FROM org_groups g
          LEFT JOIN group_members gm ON gm.group_id = g.group_id
          {where}
         GROUP BY g.group_id, g.name, g.description, g.is_active, g.created_by, g.created_at, g.updated_at
         ORDER BY g.is_active DESC, g.name
        """,
        tuple(params),
    )
    return {"items": rows or [], "total": len(rows or [])}


def create_group(payload: Dict[str, Any], actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="group name is required")
    description = str(payload.get("description") or "").strip() or None
    manager_user_id = _to_optional_int(payload.get("manager_user_id", payload.get("managerUserId")))
    if manager_user_id is None:
        raise HTTPException(status_code=422, detail="manager_user_id is required")
    if not fetch_one("SELECT user_id FROM users WHERE user_id=%s", (manager_user_id,)):
        raise HTTPException(status_code=404, detail="manager user not found")
    group_id = execute(
        """
        INSERT INTO org_groups(name, description, is_active, created_by, created_at, updated_at)
        VALUES(%s,%s,%s,%s,NOW(),NOW())
        """,
        (name, description, 1, actor_user_id),
        True,
    )
    add_group_member(group_id=int(group_id), payload={"user_id": manager_user_id, "role": "manager"}, actor_user_id=actor_user_id)
    return get_group(int(group_id))


def get_group(group_id: int) -> Dict[str, Any]:
    row = fetch_one(
        """
        SELECT group_id, name, description, is_active, created_by, created_at, updated_at
          FROM org_groups
         WHERE group_id=%s
        """,
        (int(group_id),),
    )
    if not row:
        raise HTTPException(status_code=404, detail="group not found")
    row["manager_count"] = _active_manager_count(int(group_id))
    return row


def update_group(group_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    get_group(group_id)
    sets: list[str] = []
    params: list[Any] = []
    if "name" in patch and patch["name"] is not None:
        name = str(patch["name"]).strip()
        if not name:
            raise HTTPException(status_code=422, detail="group name cannot be empty")
        sets.append("name=%s")
        params.append(name)
    if "description" in patch:
        sets.append("description=%s")
        params.append(str(patch.get("description") or "").strip() or None)
    if "is_active" in patch:
        next_active = bool(patch.get("is_active"))
        if not next_active:
            _assert_group_can_delete(group_id)
        if next_active and _active_manager_count(group_id) <= 0:
            raise HTTPException(status_code=409, detail="active group requires at least one manager")
        sets.append("is_active=%s")
        params.append(next_active)
    if sets:
        params.append(int(group_id))
        execute(f"UPDATE org_groups SET {', '.join(sets)}, updated_at=NOW() WHERE group_id=%s", tuple(params))
        if "is_active" in patch and not bool(patch.get("is_active")):
            _deactivate_all_group_members(group_id)
    return get_group(group_id)


def deactivate_group(group_id: int) -> Dict[str, Any]:
    return delete_group(group_id)


def delete_group(group_id: int) -> Dict[str, Any]:
    get_group(group_id)
    _assert_group_can_delete(group_id)
    _clear_representative_group_users(group_id)
    execute("DELETE FROM group_members WHERE group_id=%s", (int(group_id),))
    execute("DELETE FROM org_groups WHERE group_id=%s", (int(group_id),))
    return {"ok": True, "group_id": int(group_id), "deleted": True}


def list_group_members(group_id: int, include_inactive: bool = True) -> Dict[str, Any]:
    get_group(group_id)
    params: list[Any] = [int(group_id)]
    cond = ""
    if not include_inactive:
        cond = " AND gm.is_active=TRUE"
    rows = fetch_all(
        f"""
        SELECT
            gm.group_member_id,
            gm.group_id,
            gm.user_id,
            gm.role,
            gm.is_active,
            gm.created_at,
            gm.updated_at,
            u.username,
            u.email,
            u.full_name
          FROM group_members gm
          JOIN users u ON u.user_id = gm.user_id
         WHERE gm.group_id=%s
           {cond}
         ORDER BY gm.is_active DESC, gm.role DESC, u.username
        """,
        tuple(params),
    )
    return {"items": rows or [], "total": len(rows or [])}


def get_group_usage(group_id: int) -> Dict[str, Any]:
    group = get_group(group_id)
    members_result = list_group_members(group_id, include_inactive=True)
    acl_documents = _group_acl_documents(group_id)
    active_member_count = sum(1 for row in members_result.get("items", []) if int(row.get("is_active") or 0) == 1)
    return {
        "group": group,
        "members": members_result.get("items", []),
        "member_count": len(members_result.get("items", [])),
        "active_member_count": active_member_count,
        "acl_documents": acl_documents,
        "acl_document_count": len(acl_documents),
        "can_disable": len(acl_documents) == 0,
    }


def add_group_member(group_id: int, payload: Dict[str, Any], actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    get_group(group_id)
    user_id = _to_optional_int(payload.get("user_id", payload.get("userId")))
    if user_id is None:
        raise HTTPException(status_code=422, detail="user_id is required")
    role = str(payload.get("role") or "member").strip().lower()
    if role not in {"member", "manager"}:
        raise HTTPException(status_code=422, detail="invalid group role")
    if not fetch_one("SELECT user_id FROM users WHERE user_id=%s", (user_id,)):
        raise HTTPException(status_code=404, detail="user not found")
    if role != "manager" and _active_manager_count(group_id) <= 0:
        raise HTTPException(status_code=409, detail="group requires at least one active manager before adding members")
    execute(
        """
        INSERT INTO group_members(group_id, user_id, role, is_active, created_at, updated_at)
        VALUES(%s,%s,%s,TRUE,NOW(),NOW())
        ON CONFLICT (group_id, user_id) DO UPDATE SET
            role=EXCLUDED.role,
            is_active=TRUE,
            updated_at=NOW()
        """,
        (int(group_id), int(user_id), role),
    )
    if role == "manager":
        execute("UPDATE org_groups SET is_active=TRUE, updated_at=NOW() WHERE group_id=%s", (int(group_id),))
    _set_representative_group_user(int(group_id), int(user_id))
    return {"ok": True, "group_id": int(group_id), "user_id": int(user_id), "role": role}


def update_group_member(group_id: int, user_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    get_group(group_id)
    row = fetch_one(
        "SELECT group_member_id, role, is_active FROM group_members WHERE group_id=%s AND user_id=%s",
        (int(group_id), int(user_id)),
    )
    if not row:
        raise HTTPException(status_code=404, detail="group member not found")
    next_role = str(patch.get("role", row.get("role")) or "member").strip().lower()
    if next_role not in {"member", "manager"}:
        raise HTTPException(status_code=422, detail="invalid group role")
    next_active = bool(patch.get("is_active", row.get("is_active")))
    usage = fetch_one(
        """
        SELECT
            SUM(CASE WHEN is_active=TRUE AND user_id<>%s THEN 1 ELSE 0 END) AS other_active_members,
            SUM(CASE WHEN is_active=TRUE AND role='manager' AND user_id<>%s THEN 1 ELSE 0 END) AS other_active_managers
          FROM group_members
         WHERE group_id=%s
        """,
        (int(user_id), int(user_id), int(group_id)),
    ) or {}
    proposed_active_members = int(usage.get("other_active_members") or 0) + (1 if next_active else 0)
    proposed_active_managers = int(usage.get("other_active_managers") or 0) + (1 if next_active and next_role == "manager" else 0)
    if proposed_active_managers <= 0 and (proposed_active_members > 0 or _group_acl_document_count(group_id) > 0):
        raise HTTPException(status_code=409, detail="group with active members or ACL documents requires at least one active manager")
    execute(
        """
        UPDATE group_members
           SET role=%s,
               is_active=%s,
               updated_at=NOW()
         WHERE group_id=%s AND user_id=%s
        """,
        (next_role, next_active, int(group_id), int(user_id)),
    )
    if next_active and next_role == "manager":
        execute("UPDATE org_groups SET is_active=TRUE, updated_at=NOW() WHERE group_id=%s", (int(group_id),))
    elif _active_manager_count(group_id) <= 0:
        execute("UPDATE org_groups SET is_active=FALSE, updated_at=NOW() WHERE group_id=%s", (int(group_id),))
    if next_active:
        _set_representative_group_user(int(group_id), int(user_id))
    else:
        _clear_representative_group_users(int(group_id), int(user_id))
    return {"ok": True, "group_id": int(group_id), "user_id": int(user_id), "role": next_role, "is_active": next_active}


def deactivate_group_member(group_id: int, user_id: int) -> Dict[str, Any]:
    return update_group_member(group_id, user_id, {"is_active": 0})


def _normalize_sync_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _kiwix_path_key(relative_path: Any, sub_path: Any, doc_name: Any, file_right: Any) -> str:
    rel = _normalize_sync_path(relative_path)
    sub = _normalize_sync_path(sub_path)
    name = str(doc_name or "").strip()
    ext = str(file_right or "").strip().lstrip(".")
    filename = f"{name}.{ext}" if ext else name
    return "/".join([p for p in (rel, sub, filename) if p]).lower()


def _load_kiwix_json_keys() -> Optional[set[str]]:
    path = Path(SYNC_KIWIX_DOCUMENTS_JSON)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = data if isinstance(data, list) else data.get("documents") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return None
    keys: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        rel = raw.get("relative_path") or raw.get("category") or "0. KIWIX"
        sub = raw.get("sub_path") or raw.get("path") or ""
        doc_name = str(raw.get("doc_name") or raw.get("name") or raw.get("title") or "").strip()
        if not doc_name:
            continue
        file_right = str(raw.get("file_right") or raw.get("ext") or raw.get("extension") or "html").strip().lstrip(".")
        keys.add(_kiwix_path_key(rel, sub, doc_name, file_right))
    return keys


def _detect_kiwix_deleted_candidates(actor_user_id: Optional[int] = None) -> int:
    kiwix_keys = _load_kiwix_json_keys()
    if kiwix_keys is None:
        return 0
    rows = fetch_all(
        """
        SELECT
            so.object_id,
            so.relative_path,
            so.sub_path,
            so.doc_name,
            so.file_right,
            tn.node_id,
            ai.ai_document_id
          FROM storage_objects so
          JOIN tree_nodes tn ON tn.doc_id = so.object_id
          LEFT JOIN ai_document_info ai ON ai.object_id = so.object_id
         WHERE LOWER(COALESCE(so.branch_type, '')) = 'kiwix'
           AND COALESCE(so.doc_status, 'active') = 'active'
        """
    )
    detected = 0
    for row in rows or []:
        key = _kiwix_path_key(row.get("relative_path"), row.get("sub_path"), row.get("doc_name"), row.get("file_right"))
        if key in kiwix_keys:
            continue
        object_id = int(row["object_id"])
        node_id = int(row["node_id"])
        exists = fetch_one(
            "SELECT deletion_id FROM document_deletions WHERE object_id=%s AND status='pending' LIMIT 1",
            (object_id,),
        )
        execute("UPDATE storage_objects SET doc_status='deleted_pending', db_updated_at=NOW() WHERE object_id=%s", (object_id,))
        execute(
            """
            INSERT INTO document_deletions(
                object_id, node_id, ai_document_id, status, detected_at, remain_until,
                detected_by_user_id, detected_source, reason, created_at, updated_at
            )
            VALUES(%s,%s,%s,'pending',NOW(),NOW() + INTERVAL '7 days',%s,'kiwix_documents_json','KIWIX document is missing from documents.json',NOW(),NOW())
            ON CONFLICT (object_id, active_deletion_marker) DO UPDATE SET
                status='pending',
                detected_at=NOW(),
                remain_until=NOW() + INTERVAL '7 days',
                detected_by_user_id=EXCLUDED.detected_by_user_id,
                detected_source=EXCLUDED.detected_source,
                reason=EXCLUDED.reason,
                updated_at=NOW()
            """,
            (object_id, node_id, row.get("ai_document_id"), actor_user_id),
        )
        execute("UPDATE ai_document_info SET index_status='deleted', updated_at=NOW() WHERE object_id=%s", (object_id,))
        if not exists:
            enqueue_ai_job(object_id, "delete", actor_user_id)
            detected += 1
    return detected


def list_deleted_documents(status: str = "pending") -> Dict[str, Any]:
    safe_status = str(status or "pending").strip().lower()
    if safe_status not in {"pending", "restored", "purged", "all"}:
        safe_status = "pending"
    params: list[Any] = []
    cond = ""
    if safe_status != "all":
        cond = "WHERE dd.status=%s"
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
          {cond}
         ORDER BY dd.detected_at DESC, dd.deletion_id DESC
        """,
        tuple(params),
    )
    return {"items": rows or [], "total": len(rows or [])}


def list_deleted_document_history() -> Dict[str, Any]:
    rows = fetch_all(
        """
        SELECT *
          FROM deleted_documents
         ORDER BY purged_at DESC, deleted_doc_id DESC
        """
    )
    return {"items": rows or [], "total": len(rows or [])}


def restore_deleted_document(deletion_id: int, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    if minio_document_lifecycle_service.is_minio_deletion(deletion_id):
        return minio_document_lifecycle_service.restore_minio_document(
            deletion_id,
            actor_user_id=actor_user_id,
        )
    row = fetch_one(
        """
        SELECT dd.deletion_id, dd.object_id, dd.node_id, so.*
          FROM document_deletions dd
          JOIN storage_objects so ON so.object_id = dd.object_id
         WHERE dd.deletion_id=%s
           AND dd.status='pending'
        """,
        (int(deletion_id),),
    )
    if not row:
        raise HTTPException(status_code=404, detail="pending deletion not found")
    path = resolve_storage_path(row)
    if not os.path.exists(path):
        execute(
            "UPDATE storage_objects SET doc_status='inactive_path_missing', db_updated_at=NOW() WHERE object_id=%s",
            (int(row["object_id"]),),
        )
        raise HTTPException(status_code=409, detail="local file is missing; restore requires the file at original path")
    object_id = int(row["object_id"])
    execute("UPDATE storage_objects SET doc_status='active', db_updated_at=NOW() WHERE object_id=%s", (object_id,))
    execute("UPDATE ai_document_info SET index_status='pending', updated_at=NOW() WHERE object_id=%s", (object_id,))
    enqueue_ai_job(object_id, "update", actor_user_id)
    execute("DELETE FROM document_deletions WHERE deletion_id=%s", (int(deletion_id),))
    execute("DELETE FROM deleted_documents WHERE old_object_id=%s", (object_id,))
    return {"ok": True, "object_id": object_id, "restored": True}


def purge_deleted_document(deletion_id: int, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    if minio_document_lifecycle_service.is_minio_deletion(deletion_id):
        return minio_document_lifecycle_service.request_minio_purge(
            deletion_id,
            actor_user_id=actor_user_id,
        )
    row = fetch_one(
        """
        SELECT
            dd.deletion_id,
            dd.object_id,
            dd.node_id,
            dd.ai_document_id,
            dd.reason,
            so.doc_name,
            so.file_right,
            so.relative_path,
            so.sub_path,
            so.doc_code,
            so.branch_type,
            so.checksum_sha256,
            so.source_created_at,
            so.source_updated_at,
            so.size_bytes,
            so.upload_mode,
            so.uploader_user_id,
            tn.parent_id,
            tn.kb_root_id,
            tn.alias,
            tn.item_type_code,
            tn.sort_order,
            ab.owner_user_id,
            ab.target_group_id,
            ab.policy_code
          FROM document_deletions dd
          JOIN storage_objects so ON so.object_id = dd.object_id
          LEFT JOIN tree_nodes tn ON tn.node_id = dd.node_id
          LEFT JOIN acl_bindings ab ON ab.node_id = dd.node_id
         WHERE dd.deletion_id=%s
           AND dd.status='pending'
        """,
        (int(deletion_id),),
    )
    if not row:
        raise HTTPException(status_code=404, detail="pending deletion not found")
    object_id = int(row["object_id"])
    execute(
        """
        INSERT INTO deleted_documents(
            old_object_id, old_node_id, old_parent_id, old_kb_root_id, ai_document_id,
            doc_name, alias, relative_path, sub_path, file_right, doc_code, item_type_code,
            branch_type, checksum_sha256, source_created_at, source_updated_at, size_bytes, s3_key,
            upload_mode, uploader_user_id, owner_user_id, target_group_id, policy_code, sort_order,
            deleted_at, purged_at, purged_by, delete_reason, created_at
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'',%s,%s,%s,%s,%s,%s,NOW(),NOW(),%s,%s,NOW())
        """,
        (
            object_id,
            int(row.get("node_id") or 0),
            row.get("parent_id"),
            row.get("kb_root_id"),
            row.get("ai_document_id"),
            row.get("doc_name") or "",
            row.get("alias") or row.get("doc_name") or "",
            row.get("relative_path") or "",
            row.get("sub_path") or "",
            row.get("file_right") or "",
            row.get("doc_code") or "",
            int(row.get("item_type_code") or 10),
            row.get("branch_type") or "general",
            row.get("checksum_sha256"),
            row.get("source_created_at"),
            row.get("source_updated_at"),
            row.get("size_bytes"),
            row.get("upload_mode") or "admin_kb",
            row.get("uploader_user_id"),
            row.get("owner_user_id"),
            row.get("target_group_id"),
            row.get("policy_code"),
            int(row.get("sort_order") or 0),
            actor_user_id,
            row.get("reason"),
        ),
    )
    enqueue_ai_job(object_id, "delete", actor_user_id)
    execute("DELETE FROM tree_nodes WHERE doc_id=%s", (object_id,))
    execute("DELETE FROM doc_revisions WHERE doc_id=%s", (object_id,))
    execute("UPDATE ai_document_info SET object_id=NULL, index_status='deleted', updated_at=NOW() WHERE object_id=%s", (object_id,))
    execute("DELETE FROM storage_objects WHERE object_id=%s", (object_id,))
    execute("DELETE FROM document_deletions WHERE deletion_id=%s", (int(deletion_id),))
    return {"ok": True, "object_id": object_id, "purged": True}


def list_ai_index_jobs(status: str = "failed") -> Dict[str, Any]:
    safe_status = str(status or "failed").strip().lower()
    params: list[Any] = []
    conds = [
        """
        NOT (
            COALESCE(tn.kb_type, '') IN ('personal', 'person')
         OR COALESCE(so.upload_mode, '') = 'personal'
         OR COALESCE(dd_tn.kb_type, '') IN ('personal', 'person')
         OR EXISTS(
             SELECT 1
               FROM deleted_documents archived
              WHERE archived.ai_document_id=j.ai_document_id
                AND COALESCE(archived.upload_mode, '')='personal'
         )
        )
        """
    ]
    if safe_status != "all":
        conds.append("j.status=%s")
        params.append(safe_status)
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
            j.created_by,
            j.created_at,
            j.updated_at,
            ai.doc_name,
            ai.relative_path,
            ai.sub_path,
            ai.file_right,
            ai.user_id,
            ai.index_status,
            ai.item_type_code
          FROM ai_index_jobs j
          LEFT JOIN ai_document_info ai ON ai.ai_document_id = j.ai_document_id
          LEFT JOIN storage_objects so ON so.object_id = COALESCE(j.object_id, ai.object_id)
          LEFT JOIN tree_nodes tn ON tn.doc_id = COALESCE(j.object_id, ai.object_id)
          LEFT JOIN document_deletions dd ON dd.ai_document_id = j.ai_document_id
          LEFT JOIN tree_nodes dd_tn ON dd_tn.node_id = dd.node_id
         WHERE {' AND '.join(conds)}
         ORDER BY j.updated_at DESC, j.job_id DESC
        """,
        tuple(params),
    )
    return {"items": rows or [], "total": len(rows or [])}


def _ai_dispatch_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    callback: Dict[str, Any] = {}
    if AI_INDEX_CALLBACK_URL:
        callback = {
            "url": AI_INDEX_CALLBACK_URL,
            "method": "POST",
            "token_header": "X-AI-Callback-Token" if AI_INDEX_CALLBACK_TOKEN else "",
        }
    branch_type = str(row.get("branch_type") or "").upper()
    if not branch_type and str(row.get("item_type_code") or "").strip() == "20":
        branch_type = "KIWIX"
    if not branch_type:
        branch_type = "GENERAL"
    upload_mode = str(row.get("upload_mode") or "admin_kb").strip() or "admin_kb"
    is_personal = upload_mode == "personal" or str(row.get("kb_type") or "").lower() in {"personal", "person"}
    storage_scope = "personal" if is_personal else "kiwix" if branch_type == "KIWIX" else "admin"
    base_dir = base_dir_for_storage_row(row)
    parser_base_dir = _parser_base_dir(base_dir, storage_scope, row)
    resolved_path = "" if str(row.get("action") or "").lower() == "delete" else resolve_storage_path(row)
    return _json_safe_dict({
        "job_id": int(row["job_id"]),
        "action": row.get("action"),
        "ai_document_id": row.get("ai_document_id"),
        "object_id": row.get("object_id"),
        "item_type_code": row.get("item_type_code"),
        "storage_scope": storage_scope,
        "base_dir": parser_base_dir,
        "resolved_path": resolved_path,
        "upload_mode": upload_mode,
        "user_id": row.get("user_id"),
        "owner_user_id": row.get("owner_user_id"),
        "relative_path": row.get("relative_path") or "",
        "sub_path": row.get("sub_path") or "",
        "doc_name": row.get("doc_name") or "",
        "file_right": row.get("file_right") or "",
        "doc_code": row.get("doc_code") or "",
        "branch_type": str(row.get("branch_type") or "general").upper(),
        "doc_status": row.get("doc_status") or "active",
        "callback": callback,
    })


def _handle_ai_dispatch_response(row: Dict[str, Any], response_body: Any) -> str:
    if not isinstance(response_body, dict):
        return "running"
    status = response_body.get("status") or response_body.get("index_status")
    if not status:
        return "running"
    normalized = str(status).strip().lower()
    if normalized in {"success", "succeeded", "indexed", "deleted", "done", "ok", "failed", "fail", "error"}:
        ai_index_service.apply_ai_index_result(
            {
                "job_id": row.get("job_id"),
                "ai_document_id": row.get("ai_document_id"),
                "object_id": row.get("object_id"),
                "action": row.get("action"),
                "status": normalized,
                "message": response_body.get("message")
                or response_body.get("error")
                or response_body.get("error_message")
                or response_body.get("last_error"),
            },
            callback_token=AI_INDEX_CALLBACK_TOKEN or None,
        )
        return "completed" if normalized not in {"failed", "fail", "error"} else "failed"
    return "running"


def dispatch_pending_ai_index_jobs(limit: int = 20, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="Direct dispatch was removed; use the standalone Pipeline Dispatcher",
    )


def retry_ai_index_jobs(job_ids: list[int], actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    from app.services.features.pipeline_job_service import retry_jobs

    return retry_jobs(
        job_ids,
        actor_user_id=int(actor_user_id or 0),
        is_admin=True,
    )


def list_positions() -> list[dict]:
    return admin_dao.list_positions()


def create_position(name: str) -> dict:
    safe_name = (name or "").strip()
    if not safe_name:
        raise HTTPException(status_code=422, detail="position name is required")
    return admin_dao.create_position(name=safe_name)


def delete_position(position_id: int) -> Dict[str, Any]:
    try:
        deleted = admin_dao.delete_position(position_id=position_id)
    except ValueError as e:
        if str(e) == "position_in_use":
            raise HTTPException(
                status_code=409,
                detail="position is in use by users and cannot be deleted",
            ) from e
        raise
    except Exception as e:
        msg = str(e)
        if "1451" in msg or "Cannot delete or update a parent row" in msg:
            raise HTTPException(
                status_code=409,
                detail="position is in use by related records and cannot be deleted",
            ) from e
        raise

    if not deleted:
        raise HTTPException(status_code=404, detail="position not found")
    return {"ok": True, "position_id": position_id}
