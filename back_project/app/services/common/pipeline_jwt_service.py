from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from jose import JWTError, jwt

from app.core.config import (
    BACKEND_SERVICE_JWT_ALGORITHM,
    BACKEND_SERVICE_JWT_AUDIENCE,
    BACKEND_SERVICE_JWT_INDEXER_SCOPE,
    BACKEND_SERVICE_JWT_ISSUER,
    BACKEND_SERVICE_JWT_KEY_ID,
    BACKEND_SERVICE_JWT_PARSER_SCOPE,
    BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE,
    BACKEND_SERVICE_JWT_TTL_SECONDS,
    INDEXER_CALLBACK_JWT_AUDIENCE,
    INDEXER_CALLBACK_JWT_ISSUER,
    INDEXER_CALLBACK_JWT_SCOPE,
    INDEXER_CALLBACK_PUBLIC_KEY_FILE,
    PARSER_CALLBACK_JWT_AUDIENCE,
    PARSER_CALLBACK_JWT_ISSUER,
    PARSER_CALLBACK_JWT_SCOPE,
    PARSER_CALLBACK_PUBLIC_KEY_FILE,
)


PipelineService = Literal["parser", "indexer"]


def _read_key(path_value: str, *, label: str, private: bool) -> str:
    if not path_value:
        raise RuntimeError(f"{label} key file is not configured")
    try:
        key_value = Path(path_value).read_text(encoding="ascii").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read {label} key file") from exc

    expected_marker = "PRIVATE KEY" if private else "PUBLIC KEY"
    if f"BEGIN {expected_marker}" not in key_value:
        raise RuntimeError(f"{label} key file is not a PEM {expected_marker.lower()}")
    return key_value


@lru_cache(maxsize=1)
def _backend_private_key() -> str:
    return _read_key(
        BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE,
        label="backend service JWT private",
        private=True,
    )


@lru_cache(maxsize=2)
def _callback_public_key(source_service: PipelineService) -> str:
    path_value = (
        PARSER_CALLBACK_PUBLIC_KEY_FILE
        if source_service == "parser"
        else INDEXER_CALLBACK_PUBLIC_KEY_FILE
    )
    return _read_key(
        path_value,
        label=f"{source_service} callback JWT public",
        private=False,
    )


def _request_scope(target_service: PipelineService) -> str:
    return (
        BACKEND_SERVICE_JWT_PARSER_SCOPE
        if target_service == "parser"
        else BACKEND_SERVICE_JWT_INDEXER_SCOPE
    )


def _callback_config(source_service: PipelineService) -> tuple[str, str, str]:
    if source_service == "parser":
        return (
            PARSER_CALLBACK_JWT_ISSUER,
            PARSER_CALLBACK_JWT_AUDIENCE,
            PARSER_CALLBACK_JWT_SCOPE,
        )
    return (
        INDEXER_CALLBACK_JWT_ISSUER,
        INDEXER_CALLBACK_JWT_AUDIENCE,
        INDEXER_CALLBACK_JWT_SCOPE,
    )


def _scope_values(claim_value: Any) -> set[str]:
    if isinstance(claim_value, str):
        return {value for value in claim_value.split() if value}
    if isinstance(claim_value, (list, tuple, set)):
        return {str(value) for value in claim_value if str(value)}
    return set()


def create_pipeline_request_token(
    target_service: PipelineService,
    *,
    additional_claims: Mapping[str, Any] | None = None,
) -> str:
    if target_service not in {"parser", "indexer"}:
        raise ValueError("target_service must be parser or indexer")
    if BACKEND_SERVICE_JWT_ALGORITHM != "RS256":
        raise RuntimeError("BACKEND_SERVICE_JWT_ALGORITHM must be RS256")

    ttl_seconds = max(30, min(int(BACKEND_SERVICE_JWT_TTL_SECONDS), 900))
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "iss": BACKEND_SERVICE_JWT_ISSUER,
        "aud": BACKEND_SERVICE_JWT_AUDIENCE,
        "sub": "spaceai-backend",
        "target_service": target_service,
        "scope": [_request_scope(target_service)],
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "jti": str(uuid4()),
    }
    if additional_claims:
        protected_claims = {"iss", "aud", "sub", "scope", "iat", "nbf", "exp", "jti"}
        conflicts = protected_claims.intersection(additional_claims)
        if conflicts:
            raise ValueError(
                f"additional_claims cannot replace protected JWT claims: {sorted(conflicts)}"
            )
        claims.update(additional_claims)

    headers = {"kid": BACKEND_SERVICE_JWT_KEY_ID, "typ": "JWT"}
    return jwt.encode(
        claims,
        _backend_private_key(),
        algorithm="RS256",
        headers=headers,
    )


def verify_callback_token(
    token: str,
    source_service: PipelineService,
) -> dict[str, Any]:
    if source_service not in {"parser", "indexer"}:
        raise ValueError("source_service must be parser or indexer")

    issuer, audience, required_scope = _callback_config(source_service)
    try:
        claims = jwt.decode(
            token,
            _callback_public_key(source_service),
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={
                "require_exp": True,
                "require_iat": True,
                "require_nbf": True,
                "require_jti": True,
            },
        )
    except JWTError as exc:
        raise ValueError(f"Invalid {source_service} callback token") from exc

    required_claims = {"iss", "aud", "scope", "iat", "nbf", "exp", "jti"}
    missing_claims = sorted(claim for claim in required_claims if claim not in claims)
    if missing_claims:
        raise ValueError(
            f"Invalid {source_service} callback token: missing claims {missing_claims}"
        )
    if required_scope not in _scope_values(claims.get("scope")):
        raise ValueError(f"Invalid {source_service} callback token scope")
    return claims


def clear_key_caches() -> None:
    _backend_private_key.cache_clear()
    _callback_public_key.cache_clear()


__all__ = [
    "clear_key_caches",
    "create_pipeline_request_token",
    "verify_callback_token",
]
