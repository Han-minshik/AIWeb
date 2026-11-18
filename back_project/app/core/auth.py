from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from fastapi import HTTPException, status, Request, Depends
from fastapi.security.utils import get_authorization_scheme_param

from app.core.config import (
    AUTH_COOKIE_DOMAIN,
    AUTH_COOKIE_NAME,
    AUTH_COOKIE_PATH,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    JWT_ALGORITHM,
    JWT_EXPIRE_MINUTES,
    JWT_SECRET_KEY,
)
from app.repositories.common import fetch_one

def create_access_token(
    sub: str,
    *,
    is_admin: bool = False,
    group_id: Optional[int] = None,
    group_name: str = "",
    is_active: bool = True,
    position_id: Optional[int] = None,
    position: str = "",
):
    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {
        "sub": sub,
        "is_admin": bool(is_admin),
        "group_id": group_id,
        "group_name": group_name,
        "is_active": bool(is_active),
        "position_id": position_id,
        "position": position,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_access_token(token: str) -> int:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="토큰에 user id가 없습니다.")
        return int(sub)
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰")

def get_token_from_request(request: Request) -> Optional[str]:
    """Return a legacy Bearer token first, then the browser session cookie.

    Header support is intentionally kept for existing API clients and sessions
    while browser logins use the HttpOnly cookie.
    """
    auth = request.headers.get("Authorization")
    if auth:
        scheme, credentials = get_authorization_scheme_param(auth)
        if scheme.lower() == "bearer" and credentials:
            return credentials

    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        return token

    token = request.query_params.get("access_token")
    if token:
        return token

    return None


# Backward-compatible alias for code that imported the old private helper.
_get_token_from_request = get_token_from_request

def get_current_user(request: Request):
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다.")

    user_id = verify_access_token(token)
    row = fetch_one(
        """
        SELECT user_id, username, full_name, email, is_admin, is_active,
               group_id, position_id
          FROM users
         WHERE user_id=%s AND is_active=TRUE
        """,
        (user_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="사용자 정보를 찾을 수 없습니다.")
    return row

def require_admin_user(user: dict = Depends(get_current_user)):
    if not bool(user.get("is_admin")):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    return user

def set_access_cookie(response, token: str):
    max_age = int(JWT_EXPIRE_MINUTES * 60)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite=AUTH_COOKIE_SAMESITE,
        secure=AUTH_COOKIE_SECURE,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
    )

def clear_access_cookie(response):
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
        secure=AUTH_COOKIE_SECURE,
        httponly=True,
        samesite=AUTH_COOKIE_SAMESITE,
    )
