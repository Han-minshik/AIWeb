from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from fastapi import HTTPException, status, Request, Depends
from fastapi.security.utils import get_authorization_scheme_param

from app.core.config import JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_MINUTES
from app.repositories.common import fetch_one

def create_access_token(sub: str):
    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": sub, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_access_token(token: str) -> int:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="토큰에 user id가 없습니다.")
        return int(sub)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰")

def _get_token_from_request(request: Request) -> Optional[str]:
    token = request.cookies.get("access_token")
    if token:
        return token

    auth = request.headers.get("Authorization")
    if auth:
        scheme, credentials = get_authorization_scheme_param(auth)
        if scheme.lower() == "bearer" and credentials:
            return credentials

    token = request.query_params.get("access_token")
    if token:
        return token

    return None

def get_current_user(request: Request):
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다.")

    user_id = verify_access_token(token)
    row = fetch_one(
        """
        SELECT user_id, org_id, username, full_name, email, is_admin, is_active
          FROM users
         WHERE user_id=%s AND is_active=1
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
        key="access_token",
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=False,  
        path="/",
    )

def clear_access_cookie(response):
    response.delete_cookie("access_token", path="/")
