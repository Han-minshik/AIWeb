from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import HTTPException, Body, Response, Request, Depends
from pydantic import BaseModel

from app.repositories.common import fetch_all, fetch_one, execute
from app.services.common.rag_token_service import create_entry_user_token
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
    user_id: Optional[int],
    action: str,
    req: Request,
) -> None:
    try:
        execute(
            """
            INSERT INTO access_logs (user_id, action, ip_addr, user_agent)
            VALUES (%s, %s, %s, %s)
            """,
            (
                user_id,
                action,
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
        SELECT
            u.user_id,
            u.username,
            u.password_hash,
            u.full_name,
            u.email,
            u.is_admin,
            u.is_active,
            u.group_id,
            g.name AS group_name,
            u.position_id,
            p.name AS position
          FROM users u
          LEFT JOIN org_groups g ON g.group_id = u.group_id
          LEFT JOIN positions p ON p.position_id = u.position_id
         WHERE u.username=%s
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

    token = create_access_token(
        str(row["user_id"]),
        is_admin=bool(row.get("is_admin", 0)),
        group_id=row.get("group_id"),
        group_name=row.get("group_name") or "",
        is_active=bool(row.get("is_active", 0)),
        position_id=row.get("position_id"),
        position=row.get("position") or "",
    )

    if response is not None:
        set_access_cookie(response, token)

    if request is not None:
        _log_access(
            user_id=row.get("user_id"),
            action="LOGIN",
            req=request,
        )

    return {
        "access_token": token,
        "user": {
            "user_id": row["user_id"],
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
            user_id=current.get("user_id"),
            action="LOGOUT",
            req=request,
        )
    except Exception:
        pass

    clear_access_cookie(response)
    return {"ok": True}


def me(current_user: dict = Depends(get_current_user)):
    return {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "full_name": current_user["full_name"],
        "email": current_user["email"],
        "is_admin": bool(current_user.get("is_admin", 0)),
    }


def admin_check(current_user: dict = Depends(get_current_user)):
    return {"is_admin": bool(current_user.get("is_admin", 0))}


def users_me_alias(current_user: dict = Depends(get_current_user)):
    return me(current_user)

def _entry_groups(user_id: int) -> list[str]:
    rows = fetch_all(
        """
        SELECT DISTINCT g.name
          FROM group_members gm
          JOIN org_groups g ON g.group_id = gm.group_id
         WHERE gm.user_id=%s
           AND gm.is_active=TRUE
           AND g.is_active=TRUE
         ORDER BY g.name
        """,
        (int(user_id),),
    )
    return [str(row.get("name")) for row in rows if row.get("name")]


def entry_token(target: str, current_user: dict = Depends(get_current_user)):
    target = str(target or "").strip().lower()
    if target != "deep-research":
        raise HTTPException(status_code=400, detail="Unsupported entry token target")

    token_user = dict(current_user)
    token_user["groups"] = _entry_groups(int(current_user["user_id"]))
    try:
        token = create_entry_user_token(token_user, target)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"token": token, "token_type": "bearer"}
