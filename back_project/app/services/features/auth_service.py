from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import HTTPException, Body, Response, Request, Depends
from pydantic import BaseModel

from app.repositories.common import fetch_one, execute
from app.core.auth import (
    create_access_token,
    get_current_user,
    set_access_cookie,
    clear_access_cookie,
)


class LoginIn(BaseModel):
    id: Optional[str] = None       
    username: Optional[str] = None
    password: str

def _client_ip(req: Request) -> str:
    xf = req.headers.get("x-forwarded-for") or req.headers.get("X-Forwarded-For")
    if xf:
        return xf.split(",")[0].strip()
    return (req.client.host if req.client else "") or ""

def _user_agent(req: Request) -> str:
    return (req.headers.get("user-agent") or "")[:255]

def _log_access(
    *,
    org_id: Optional[int],
    user_id: Optional[int],
    action: str,
    req: Request,
    target_type: Optional[str] = None,
    target_pk: Optional[int] = None,
) -> None:
    try:
        execute(
            """
            INSERT INTO access_logs (org_id, user_id, action, target_type, target_pk, ip_addr, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                org_id,
                user_id,
                action,
                target_type,
                target_pk,
                _client_ip(req),
                _user_agent(req),
            ),
        )
    except Exception:
        pass

def login(payload: LoginIn = Body(...), response: Response = None, request: Request = None):
    user_key = (payload.id or payload.username or "").strip()
    if not user_key:
        raise HTTPException(status_code=422, detail="id 또는 username 필드가 필요합니다.")

    row = fetch_one(
        """
        SELECT user_id, org_id, username, password_hash, full_name, email, is_admin, is_active
          FROM users
         WHERE username=%s
        """,
        (user_key,),
    )
    if not row:
        raise HTTPException(status_code=401, detail="존재하지 않는 사용자")
    if not bool(row.get("is_active", 0)):
        raise HTTPException(status_code=403, detail="비활성화된 사용자입니다.")

    hashed = hashlib.sha256(payload.password.encode()).hexdigest()
    if (row["password_hash"] or "").lower() != hashed.lower():
        raise HTTPException(status_code=401, detail="비밀번호 불일치")

    token = create_access_token(str(row["user_id"]))

    if response is not None:
        set_access_cookie(response, token)

    if request is not None:
        _log_access(
            org_id=row.get("org_id"),
            user_id=row.get("user_id"),
            action="LOGIN",
            req=request,
            target_type="AUTH",
            target_pk=None,
        )

    return {
        "access_token": token,
        "user": {
            "user_id": row["user_id"],
            "org_id": row["org_id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "email": row["email"],
            "is_admin": bool(row.get("is_admin", 0)),
        },
    }


def logout(response: Response, request: Request):
    try:
        current = get_current_user(request)  
        _log_access(
            org_id=current.get("org_id"),
            user_id=current.get("user_id"),
            action="LOGOUT",
            req=request,
            target_type="AUTH",
            target_pk=None,
        )
    except Exception:
        pass

    clear_access_cookie(response)
    return {"ok": True}


def me(current_user: dict = Depends(get_current_user)):
    return {
        "user_id": current_user["user_id"],
        "org_id": current_user["org_id"],
        "username": current_user["username"],
        "full_name": current_user["full_name"],
        "email": current_user["email"],
        "is_admin": bool(current_user.get("is_admin", 0)),
    }


def admin_check(current_user: dict = Depends(get_current_user)):
    return {"is_admin": bool(current_user.get("is_admin", 0))}


def users_me_alias(current_user: dict = Depends(get_current_user)):
    return me(current_user)
