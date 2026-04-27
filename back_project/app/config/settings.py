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
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "obsidian_db")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

DB_CONFIG = {
    "host": DB_HOST,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": DB_NAME,
}

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "43200"))

_default_cors = os.getenv("CORS_ORIGINS", "http://localhost:3000")
CORS_ORIGINS = _split_csv(_default_cors)

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

