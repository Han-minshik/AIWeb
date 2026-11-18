from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from jose import jwt

from app.core.config import (
    ENTRY_USER_JWT_TTL_SECONDS,
    RAG_USER_JWT_ALGORITHM,
    RAG_USER_JWT_AUDIENCE,
    RAG_USER_JWT_ISSUER,
    RAG_USER_JWT_KEY_ID,
    RAG_USER_JWT_PRIVATE_KEY_B64,
    RAG_USER_JWT_PRIVATE_KEY_B64_FILE,
    RAG_USER_JWT_TTL_SECONDS,
)


ENTRY_TOKEN_AUDIENCES = frozenset({"deep-research"})


@lru_cache(maxsize=1)
def _private_key_pem() -> str:
    if RAG_USER_JWT_ALGORITHM != "RS256":
        raise RuntimeError("RAG_USER_JWT_ALGORITHM must be RS256")

    encoded = "".join(RAG_USER_JWT_PRIVATE_KEY_B64.split())
    if not encoded and RAG_USER_JWT_PRIVATE_KEY_B64_FILE:
        try:
            file_value = Path(RAG_USER_JWT_PRIVATE_KEY_B64_FILE).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Cannot read RAG user JWT private key file") from exc
        if file_value.startswith("RAG_USER_JWT_PRIVATE_KEY_B64="):
            file_value = file_value.partition("=")[2].strip()
        encoded = "".join(file_value.split())
    if not encoded:
        raise RuntimeError("RAG user JWT private key is not configured")

    try:
        private_key = base64.b64decode(encoded, validate=True).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("RAG_USER_JWT_PRIVATE_KEY_B64 is not valid Base64 PEM") from exc

    if "-----BEGIN PRIVATE KEY-----" not in private_key and "-----BEGIN RSA PRIVATE KEY-----" not in private_key:
        raise RuntimeError("RAG user JWT private key is not a PEM private key")
    return private_key


def create_rag_user_token(user: Mapping[str, Any]) -> str:
    user_id = user.get("user_id") or user.get("id")
    if user_id is None:
        raise RuntimeError("Cannot create a RAG user token without a user ID")

    ttl_seconds = max(30, min(int(RAG_USER_JWT_TTL_SECONDS), 900))
    now = datetime.now(timezone.utc)
    payload = {
        "iss": RAG_USER_JWT_ISSUER,
        "aud": RAG_USER_JWT_AUDIENCE,
        "sub": str(user_id),
        "group_id": user.get("group_id"),
        "position_id": user.get("position_id"),
        "is_admin": bool(user.get("is_admin", False)),
        "scope": ["rag:query"],
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "jti": str(uuid4()),
    }
    headers = {"kid": RAG_USER_JWT_KEY_ID, "typ": "JWT"}
    return jwt.encode(payload, _private_key_pem(), algorithm="RS256", headers=headers)


def create_entry_user_token(user: Mapping[str, Any], audience: str) -> str:
    if audience not in ENTRY_TOKEN_AUDIENCES:
        raise RuntimeError("Unsupported entry token audience")

    user_id = user.get("user_id") or user.get("id")
    if user_id is None:
        raise RuntimeError("Cannot create an entry token without a user ID")

    ttl_seconds = max(3600, min(int(ENTRY_USER_JWT_TTL_SECONDS), 43200))
    now = datetime.now(timezone.utc)
    payload = {
        "iss": RAG_USER_JWT_ISSUER,
        "aud": audience,
        "sub": str(user_id),
        "user_id": int(user_id),
        "user_name": str(user.get("username") or user.get("user_name") or ""),
        "groups": list(user.get("groups") or []),
        "scope": [f"{audience}:entry"],
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "jti": str(uuid4()),
    }
    headers = {"kid": RAG_USER_JWT_KEY_ID, "typ": "JWT"}
    return jwt.encode(payload, _private_key_pem(), algorithm="RS256", headers=headers)

__all__ = ["create_entry_user_token", "create_rag_user_token"]
