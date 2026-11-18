from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Set, Tuple

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _split_csv(v: str | None) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _as_bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_project_path(value: str | None) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    path = Path(raw_value)
    return str((path if path.is_absolute() else _PROJECT_ROOT / path).resolve())

def _read_single_line_secret_file(path_value: str, label: str) -> str:
    """Read a UTF-8 secret without logging or silently normalizing bad input."""
    if not path_value:
        raise RuntimeError(f"{label} file path is not configured")

    path = Path(path_value)
    if not path.is_file():
        raise RuntimeError(f"{label} file does not exist: {path}")

    try:
        raw_value = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Unable to read {label} file: {path}") from exc

    if not raw_value:
        raise RuntimeError(f"{label} file is empty: {path}")
    if raw_value != raw_value.strip():
        raise RuntimeError(f"{label} file contains outer whitespace: {path}")
    if "\n" in raw_value or "\r" in raw_value:
        raise RuntimeError(f"{label} file must contain exactly one line: {path}")

    return raw_value

def _parse_key_int_map(v: str | None) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in _split_csv(v):
        if ":" not in item:
            continue
        key, raw_val = item.split(":", 1)
        key = key.strip().lower()
        if not key:
            continue
        try:
            out[key] = int(raw_val.strip())
        except Exception:
            continue
    return out


def _to_upper_set(values: list[str]) -> Set[str]:
    return {v.strip().upper() for v in values if v and v.strip()}


APP_NAME = os.getenv("APP_NAME", "Obsidian-Back API")
APP_VERSION = os.getenv("APP_VERSION", "1.4")

DELETE_UPLOADS_ON_RECORD_DELETE = _as_bool(os.getenv("DELETE_UPLOADS_ON_RECORD_DELETE"), True)

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_USER = os.getenv("DB_USER", "spaceai")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "obsidian_db")
DB_PORT = int(os.getenv("DB_PORT", "6432"))

DB_CONFIG = {
    "host": DB_HOST,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": DB_NAME,
    "port": DB_PORT,
}

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "43200"))

AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "access_token").strip() or "access_token"
AUTH_COOKIE_SECURE = _as_bool(os.getenv("AUTH_COOKIE_SECURE"), False)
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
if AUTH_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    AUTH_COOKIE_SAMESITE = "lax"
AUTH_COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN", "").strip() or None
AUTH_COOKIE_PATH = os.getenv("AUTH_COOKIE_PATH", "/").strip() or "/"

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://127.0.0.1:9100").strip().rstrip("/")
MINIO_REGION = os.getenv("MINIO_REGION", "us-east-1").strip() or "us-east-1"

MINIO_SE_KB_BUCKET = os.getenv("MINIO_SE_KB_BUCKET", "spaceai-se-kb").strip()
MINIO_PERSONAL_BUCKET = os.getenv("MINIO_PERSONAL_BUCKET", "spaceai-personal").strip()
MINIO_SE_KB_ARTIFACT_BUCKET = os.getenv(
    "MINIO_SE_KB_ARTIFACT_BUCKET", "spaceai-se-kb-artifacts"
).strip()
MINIO_PERSONAL_ARTIFACT_BUCKET = os.getenv(
    "MINIO_PERSONAL_ARTIFACT_BUCKET", "spaceai-personal-artifacts"
).strip()

MINIO_BACKEND_ACCESS_KEY_FILE = _resolve_project_path(
    os.getenv("MINIO_BACKEND_ACCESS_KEY_FILE")
)
MINIO_BACKEND_SECRET_KEY_FILE = _resolve_project_path(
    os.getenv("MINIO_BACKEND_SECRET_KEY_FILE")
)
MINIO_ADMIN_ACCESS_KEY_FILE = _resolve_project_path(
    os.getenv("MINIO_ADMIN_ACCESS_KEY_FILE")
)
MINIO_ADMIN_SECRET_KEY_FILE = _resolve_project_path(
    os.getenv("MINIO_ADMIN_SECRET_KEY_FILE")
)

MINIO_CONNECT_TIMEOUT_SECONDS = int(os.getenv("MINIO_CONNECT_TIMEOUT_SECONDS", "10"))
MINIO_READ_TIMEOUT_SECONDS = int(os.getenv("MINIO_READ_TIMEOUT_SECONDS", "300"))
MINIO_WRITE_TIMEOUT_SECONDS = int(os.getenv("MINIO_WRITE_TIMEOUT_SECONDS", "3600"))
MINIO_MULTIPART_PART_SIZE_MB = int(os.getenv("MINIO_MULTIPART_PART_SIZE_MB", "64"))
MINIO_UPLOAD_PARALLELISM = int(os.getenv("MINIO_UPLOAD_PARALLELISM", "1"))
MINIO_HTTP_POOL_MAXSIZE = int(os.getenv("MINIO_HTTP_POOL_MAXSIZE", "32"))
MINIO_MAX_RETRIES = int(os.getenv("MINIO_MAX_RETRIES", "3"))
MINIO_TLS_VERIFY = _as_bool(os.getenv("MINIO_TLS_VERIFY"), True)
MINIO_CA_CERT_FILE = _resolve_project_path(os.getenv("MINIO_CA_CERT_FILE"))
MINIO_PERSONAL_QUOTA_BYTES = int(os.getenv("MINIO_PERSONAL_QUOTA_BYTES", "5368709120"))
MINIO_API_UPLOAD_CONCURRENCY = int(os.getenv("MINIO_API_UPLOAD_CONCURRENCY", "3"))
MINIO_UPLOAD_QUEUE_CHUNKS = int(os.getenv("MINIO_UPLOAD_QUEUE_CHUNKS", "8"))
MINIO_UPLOAD_STALE_MINUTES = int(os.getenv("MINIO_UPLOAD_STALE_MINUTES", "60"))
FAILED_UPLOAD_RETENTION_DAYS = int(os.getenv("FAILED_UPLOAD_RETENTION_DAYS", "7"))
PAST_ARTIFACT_RETENTION_DAYS = int(os.getenv("PAST_ARTIFACT_RETENTION_DAYS", "30"))
FAILED_ARTIFACT_RETENTION_DAYS = int(os.getenv("FAILED_ARTIFACT_RETENTION_DAYS", "7"))
COMPLETED_JOB_RETENTION_DAYS = int(os.getenv("COMPLETED_JOB_RETENTION_DAYS", "30"))
FAILED_JOB_RETENTION_DAYS = int(os.getenv("FAILED_JOB_RETENTION_DAYS", "90"))
CALLBACK_EVENT_RETENTION_DAYS = int(os.getenv("CALLBACK_EVENT_RETENTION_DAYS", "30"))
INCOMPLETE_MULTIPART_RETENTION_DAYS = int(os.getenv("INCOMPLETE_MULTIPART_RETENTION_DAYS", "7"))
MINIO_PURGE_RETRY_MINUTES = int(os.getenv("MINIO_PURGE_RETRY_MINUTES", "15"))
CLEANUP_BATCH_SIZE = int(os.getenv("CLEANUP_BATCH_SIZE", "200"))
CLEANUP_DRY_RUN = _as_bool(os.getenv("CLEANUP_DRY_RUN"), True)
KIWIX_PDF_FONT_PATH = _resolve_project_path(os.getenv("KIWIX_PDF_FONT_PATH"))


def load_minio_backend_credentials() -> Tuple[str, str]:
    """Load the least-privileged credentials used by the backend runtime."""
    return (
        _read_single_line_secret_file(
            MINIO_BACKEND_ACCESS_KEY_FILE, "MinIO backend access key"
        ),
        _read_single_line_secret_file(
            MINIO_BACKEND_SECRET_KEY_FILE, "MinIO backend secret key"
        ),
    )


def load_minio_admin_credentials() -> Tuple[str, str]:
    """Load administrative credentials only for explicit operations tooling."""
    return (
        _read_single_line_secret_file(
            MINIO_ADMIN_ACCESS_KEY_FILE, "MinIO admin access key"
        ),
        _read_single_line_secret_file(
            MINIO_ADMIN_SECRET_KEY_FILE, "MinIO admin secret key"
        ),
    )

_default_cors = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:9000,http://127.0.0.1:9000,http://localhost:3000,http://127.0.0.1:3000",
)
CORS_ORIGINS = _split_csv(_default_cors)
CORS_ORIGIN_REGEX = (
    os.getenv("CORS_ORIGIN_REGEX", r"https?://(localhost|127\.0\.0\.1)(:\d+)?").strip()
    or None
)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", str((_PROJECT_ROOT / "uploads").resolve()))
UPLOAD_DIR_GENERAL = os.getenv("UPLOAD_DIR_GENERAL", UPLOAD_DIR)
UPLOAD_DIR_KIWIX = os.getenv("UPLOAD_DIR_KIWIX", os.getenv("SYNC_LOCAL_BASE_DIR_KIWIX", UPLOAD_DIR))

SE_KB_ID = int(os.getenv("SE_KB_ID", "0") or 0)

DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "").rstrip("/")


def _load_agents_from_env() -> dict:
    agents: dict[str, dict] = {}
    for k, v in os.environ.items():
        if not (k.endswith("_API_KEY") or k.endswith("_APP_ID")):
            continue
        prefix = k.rsplit("_", 2)[0]
        entry = agents.setdefault(prefix.lower(), {})
        if k.endswith("_API_KEY"):
            entry["api_key"] = v
        elif k.endswith("_APP_ID"):
            entry["app_id"] = v
    return agents


AGENTS = _load_agents_from_env()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

RAG_SSE_URL = os.getenv("RAG_SSE_URL", "http://host.docker.internal:8000").rstrip("/")
RAG_SSE_TOKEN = os.getenv("RAG_SSE_TOKEN", "")
RAG_SSE_TIMEOUT = int(os.getenv("RAG_SSE_TIMEOUT", "3600"))
RAG_USER_JWT_PRIVATE_KEY_B64 = os.getenv("RAG_USER_JWT_PRIVATE_KEY_B64", "").strip()
RAG_USER_JWT_PRIVATE_KEY_B64_FILE = _resolve_project_path(
    os.getenv("RAG_USER_JWT_PRIVATE_KEY_B64_FILE")
)
RAG_USER_JWT_ALGORITHM = os.getenv("RAG_USER_JWT_ALGORITHM", "RS256").strip().upper()
RAG_USER_JWT_ISSUER = os.getenv("RAG_USER_JWT_ISSUER", "space-ai").strip()
RAG_USER_JWT_AUDIENCE = os.getenv("RAG_USER_JWT_AUDIENCE", "external-rag").strip()
RAG_USER_JWT_TTL_SECONDS = int(os.getenv("RAG_USER_JWT_TTL_SECONDS", "300"))
RAG_USER_JWT_KEY_ID = os.getenv("RAG_USER_JWT_KEY_ID", "space-ai-rag-01").strip()
ENTRY_USER_JWT_TTL_SECONDS = int(os.getenv("ENTRY_USER_JWT_TTL_SECONDS", "7200"))

BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE = _resolve_project_path(
    os.getenv("BACKEND_SERVICE_JWT_PRIVATE_KEY_FILE")
)
BACKEND_SERVICE_JWT_PUBLIC_KEY_FILE = _resolve_project_path(
    os.getenv("BACKEND_SERVICE_JWT_PUBLIC_KEY_FILE")
)
BACKEND_SERVICE_JWT_ALGORITHM = os.getenv(
    "BACKEND_SERVICE_JWT_ALGORITHM", "RS256"
).strip().upper()
BACKEND_SERVICE_JWT_ISSUER = os.getenv(
    "BACKEND_SERVICE_JWT_ISSUER", "spaceai-backend"
).strip()
BACKEND_SERVICE_JWT_AUDIENCE = os.getenv(
    "BACKEND_SERVICE_JWT_AUDIENCE", "spaceai-pipeline"
).strip()
BACKEND_SERVICE_JWT_TTL_SECONDS = int(
    os.getenv("BACKEND_SERVICE_JWT_TTL_SECONDS", "300")
)
BACKEND_SERVICE_JWT_KEY_ID = os.getenv(
    "BACKEND_SERVICE_JWT_KEY_ID", "spaceai-backend-01"
).strip()
BACKEND_SERVICE_JWT_PARSER_SCOPE = os.getenv(
    "BACKEND_SERVICE_JWT_PARSER_SCOPE", "parser:jobs"
).strip()
BACKEND_SERVICE_JWT_INDEXER_SCOPE = os.getenv(
    "BACKEND_SERVICE_JWT_INDEXER_SCOPE", "indexer:jobs"
).strip()

PARSER_CALLBACK_PUBLIC_KEY_FILE = _resolve_project_path(
    os.getenv("PARSER_CALLBACK_PUBLIC_KEY_FILE")
)
PARSER_CALLBACK_JWT_ISSUER = os.getenv(
    "PARSER_CALLBACK_JWT_ISSUER", "spaceai-parser"
).strip()
PARSER_CALLBACK_JWT_AUDIENCE = os.getenv(
    "PARSER_CALLBACK_JWT_AUDIENCE", "spaceai-backend"
).strip()
PARSER_CALLBACK_JWT_SCOPE = os.getenv(
    "PARSER_CALLBACK_JWT_SCOPE", "parser:callback"
).strip()

INDEXER_CALLBACK_PUBLIC_KEY_FILE = _resolve_project_path(
    os.getenv("INDEXER_CALLBACK_PUBLIC_KEY_FILE")
)
INDEXER_CALLBACK_JWT_ISSUER = os.getenv(
    "INDEXER_CALLBACK_JWT_ISSUER", "spaceai-indexer"
).strip()
INDEXER_CALLBACK_JWT_AUDIENCE = os.getenv(
    "INDEXER_CALLBACK_JWT_AUDIENCE", "spaceai-backend"
).strip()
INDEXER_CALLBACK_JWT_SCOPE = os.getenv(
    "INDEXER_CALLBACK_JWT_SCOPE", "indexer:callback"
).strip()


def _parse_extra_headers(v: str | None) -> dict[str, str]:
    if not v:
        return {}
    items = [x for x in v.split(",") if x.strip()]
    pairs: dict[str, str] = {}
    for it in items:
        if ":" in it:
            k, val = it.split(":", 1)
            pairs[k.strip()] = val.strip()
    return pairs


RAG_SSE_EXTRA_HEADERS = _parse_extra_headers(os.getenv("RAG_SSE_EXTRA_HEADERS"))

AI_INDEX_DISPATCH_URL = os.getenv("AI_INDEX_DISPATCH_URL", "").strip()
AI_INDEX_API_TOKEN = os.getenv("AI_INDEX_API_TOKEN", "")
AI_INDEX_CALLBACK_TOKEN = os.getenv("AI_INDEX_CALLBACK_TOKEN", "")
AI_INDEX_CALLBACK_URL = os.getenv("AI_INDEX_CALLBACK_URL", "").strip()
PARSER_CALLBACK_URL = os.getenv(
    "PARSER_CALLBACK_URL", "http://localhost:8001/pipeline/parser/callback"
).strip()
INDEXER_CALLBACK_URL = os.getenv(
    "INDEXER_CALLBACK_URL", "http://localhost:8001/pipeline/indexer/callback"
).strip()
AI_INDEX_TIMEOUT = int(os.getenv("AI_INDEX_TIMEOUT", "30"))
AI_INDEX_MAX_ATTEMPTS = int(os.getenv("AI_INDEX_MAX_ATTEMPTS", "3"))
PARSER_DISPATCH_URL = os.getenv("PARSER_DISPATCH_URL", AI_INDEX_DISPATCH_URL).strip()
PARSER_API_TOKEN = os.getenv("PARSER_API_TOKEN", AI_INDEX_API_TOKEN)
PARSER_TIMEOUT = int(os.getenv("PARSER_TIMEOUT", str(AI_INDEX_TIMEOUT)))
PARSER_MAX_ATTEMPTS = int(os.getenv("PARSER_MAX_ATTEMPTS", str(AI_INDEX_MAX_ATTEMPTS)))
INDEXER_DISPATCH_URL = os.getenv("INDEXER_DISPATCH_URL", AI_INDEX_DISPATCH_URL).strip()
PIPELINE_DISPATCHER_POLL_SECONDS = max(
    1, int(os.getenv("PIPELINE_DISPATCHER_POLL_SECONDS", "2"))
)
PIPELINE_DISPATCHER_FETCH_SIZE = max(
    1, int(os.getenv("PIPELINE_DISPATCHER_FETCH_SIZE", "100"))
)
PIPELINE_DISPATCHER_MAX_CONCURRENCY = max(
    1, int(os.getenv("PIPELINE_DISPATCHER_MAX_CONCURRENCY", "5"))
)
PIPELINE_CONNECT_TIMEOUT_SECONDS = max(
    1, int(os.getenv("PIPELINE_CONNECT_TIMEOUT_SECONDS", "5"))
)
PIPELINE_RESPONSE_TIMEOUT_SECONDS = max(
    1, int(os.getenv("PIPELINE_RESPONSE_TIMEOUT_SECONDS", "30"))
)
PIPELINE_MAX_ATTEMPTS = max(
    1, int(os.getenv("PIPELINE_MAX_ATTEMPTS", "3"))
)

# Shared mappings and options moved to env for easier maintenance.
CHAT_APP_TYPE_MAP = _parse_key_int_map(
    os.getenv("CHAT_APP_TYPE_MAP", "req1:1,req2:2,req3:3,req4:4,req5:5,req8:8")
)

KIWIX_FOLDER_ORDER: Tuple[str, ...] = tuple(
    _split_csv(os.getenv("KIWIX_FOLDER_ORDER", "UR,SR,SBS,OBS,WBS"))
)

SYNC_ALLOWED_BRANCH_TYPES = _to_upper_set(
    _split_csv(os.getenv("SYNC_ALLOWED_BRANCH_TYPES", "GENERAL,KIWIX"))
)
SYNC_TRIGGER_BRANCH_TYPES = _to_upper_set(
    _split_csv(os.getenv("SYNC_TRIGGER_BRANCH_TYPES", "GENERAL,KIWIX"))
)

SYNC_LOCAL_BASE_DIR_GENERAL = os.getenv(
    "SYNC_LOCAL_BASE_DIR_GENERAL",
    os.getenv("SYNC_LOCAL_BASE_DIR", r"D:\storage\base_files"),
)
SYNC_LOCAL_BASE_DIR_KIWIX = os.getenv("SYNC_LOCAL_BASE_DIR_KIWIX", r"D:\storage\extract")
SYNC_KIWIX_DOCUMENTS_JSON = os.getenv(
    "SYNC_KIWIX_DOCUMENTS_JSON",
    r"D:\storage\extract\documents.json",
)
PERSONAL_STORAGE_BASE_DIR = os.getenv("PERSONAL_STORAGE_BASE_DIR", r"D:\storage\personal")
