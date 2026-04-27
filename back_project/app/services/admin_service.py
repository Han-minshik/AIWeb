from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.dao import admin_dao


def list_users(q: Optional[str], page: int, page_size: int) -> Dict[str, Any]:
    return admin_dao.list_users(q=q, page=page, page_size=page_size)


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

    user_id = admin_dao.create_user(
        org_id=org_id,
        username=username,
        email=payload.get("email") or "",
        password=password,
        full_name=payload.get("full_name") or username,
        is_admin=bool(payload.get("is_admin")),
    )
    return {"user_id": user_id}


def update_user(user_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    fields: dict[str, Any] = {}

    if "org_id" in patch or "orgId" in patch:
        try:
            fields["org_id"] = int(patch.get("org_id", patch.get("orgId")))
        except Exception:
            pass

    for col in ("username", "email", "full_name"):
        if col in patch and patch[col] is not None:
            fields[col] = str(patch[col]).strip()

    if "is_admin" in patch:
        fields["is_admin"] = 1 if patch["is_admin"] else 0
    if "is_active" in patch:
        fields["is_active"] = 1 if patch["is_active"] else 0

    if "password" in patch and (patch["password"] or "").strip():
        fields["password"] = patch["password"]

    if fields:
        admin_dao.update_user_fields(user_id, fields)

    row = admin_dao.get_user_detail(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row


def toggle_admin(user_id: int) -> Dict[str, Any]:
    admin_dao.toggle_admin(user_id)
    row = admin_dao.get_user_detail(user_id)
    return {"id": user_id, "is_admin": row.get("is_admin") if row else None}


def reset_password(user_id: int, password: str) -> Dict[str, Any]:
    if not (password or "").strip():
        raise HTTPException(status_code=422, detail="password is required")
    admin_dao.reset_password(user_id=user_id, password=password)
    return {"user_id": user_id, "message": "Password reset completed"}


def delete_user(user_id: int) -> Dict[str, Any]:
    admin_dao.delete_user(user_id)
    return {"message": "User deleted", "user_id": user_id}


def access_logs(limit: int, user_id: Optional[int]) -> list[dict]:
    return admin_dao.list_access_logs(limit=limit, user_id=user_id)

