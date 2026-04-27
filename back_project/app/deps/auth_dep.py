from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError

from app.core.auth import verify_access_token  
from app.repositories.common import fetch_one


def _require_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다. (Authorization: Bearer <token>)",
        )
    return authorization.split(" ", 1)[1].strip()


def get_current_user_id(authorization: Optional[str] = Header(None)) -> int:
    token = _require_bearer(authorization)
    try:
        user_id = verify_access_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰")
    if not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except Exception:
            raise HTTPException(status_code=401, detail="유효하지 않은 사용자 정보")
    row = fetch_one("SELECT user_id FROM users WHERE user_id=%s", (user_id,))
    if not row:
        raise HTTPException(status_code=401, detail="존재하지 않는 사용자")
    return user_id


def get_current_user(user_id: int = Depends(get_current_user_id)) -> dict:
    row = fetch_one(
        "SELECT user_id, username, email, is_admin FROM users WHERE user_id=%s",
        (user_id,),
    )
    if not row:
        raise HTTPException(status_code=401, detail="사용자 조회 실패")
    return {
        "id": row["user_id"],
        "username": row.get("username"),
        "email": row.get("email"),
        "is_admin": bool(row.get("is_admin")),
    }


def admin_required(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user
