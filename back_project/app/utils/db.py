from __future__ import annotations

import os
from contextlib import contextmanager
from threading import Lock
from typing import Any, Dict, Iterator, List, Optional, Tuple

import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

try:
    from app.core.config import DB_CONFIG  
except Exception:
    DB_CONFIG: Dict[str, str] = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "user": os.getenv("DB_USER", "spaceai"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "obsidian_db"),
        "port": os.getenv("DB_PORT", "6432"),
    }

_CONN: Optional[psycopg.Connection[Dict[str, Any]]] = None

_POOL_MIN_SIZE = max(1, int(os.getenv("DB_POOL_MIN_SIZE", "2")))
_POOL_MAX_SIZE = max(
    _POOL_MIN_SIZE,
    int(os.getenv("DB_POOL_MAX_SIZE", "20")),
)
_POOL_TIMEOUT = max(1.0, float(os.getenv("DB_POOL_TIMEOUT", "10")))

_POOL = ConnectionPool(
    conninfo="",
    kwargs={
        "host": DB_CONFIG.get("host", "127.0.0.1"),
        "user": DB_CONFIG.get("user", "spaceai"),
        "password": DB_CONFIG.get("password", ""),
        "dbname": DB_CONFIG.get("database", "obsidian_db"),
        "port": int(DB_CONFIG.get("port", os.getenv("DB_PORT", "6432"))),
        "autocommit": True,
        "row_factory": dict_row,
    },
    min_size=_POOL_MIN_SIZE,
    max_size=_POOL_MAX_SIZE,
    timeout=_POOL_TIMEOUT,
    check=ConnectionPool.check_connection,
    open=False,
    name="spaceai-db",
)
_POOL_LOCK = Lock()
_POOL_OPEN = False


def open_pool(*, wait: bool = True) -> None:
    """Open the process-local PostgreSQL pool once."""
    global _POOL_OPEN
    with _POOL_LOCK:
        if _POOL_OPEN:
            return
        _POOL.open(wait=wait, timeout=_POOL_TIMEOUT)
        _POOL_OPEN = True


def close_pool() -> None:
    """Close all pooled PostgreSQL connections during process shutdown."""
    global _POOL_OPEN
    with _POOL_LOCK:
        if not _POOL_OPEN:
            return
        _POOL.close()
        _POOL_OPEN = False


@contextmanager
def pooled_connection(
    autocommit: bool = True,
) -> Iterator[psycopg.Connection[Dict[str, Any]]]:
    """Borrow a connection and return it to the pool after use."""
    open_pool()
    with _POOL.connection(timeout=_POOL_TIMEOUT) as connection:
        if connection.autocommit != autocommit:
            connection.autocommit = autocommit
        yield connection

def _new_connection(autocommit: bool = True) -> psycopg.Connection[Dict[str, Any]]:
    return psycopg.connect(
        host=DB_CONFIG.get("host", "127.0.0.1"),
        user=DB_CONFIG.get("user", "spaceai"),
        password=DB_CONFIG.get("password", ""),
        dbname=DB_CONFIG.get("database", "obsidian_db"),
        port=int(DB_CONFIG.get("port", os.getenv("DB_PORT", "6432"))),
        autocommit=autocommit,
        row_factory=dict_row,
    )

def get_connection(autocommit: bool = True) -> psycopg.Connection[Dict[str, Any]]:
    return _new_connection(autocommit=autocommit)

def _ensure_conn() -> psycopg.Connection[Dict[str, Any]]:
    global _CONN
    try:
        if _CONN is None or _CONN.closed:
            _CONN = _new_connection(autocommit=True)
    except Exception:
        _CONN = _new_connection(autocommit=True)
    return _CONN

def get_cursor() -> psycopg.Cursor[Dict[str, Any]]:
    conn = _ensure_conn()
    return conn.cursor()

def commit() -> None:
    try:
        if _CONN and not _CONN.closed and not _CONN.autocommit:
            _CONN.commit()
    except Exception:
        pass

def rollback() -> None:
    try:
        if _CONN and not _CONN.closed and not _CONN.autocommit:
            _CONN.rollback()
    except Exception:
        pass

def close_connection() -> None:
    global _CONN
    try:
        if _CONN and not _CONN.closed:
            _CONN.close()
    finally:
        _CONN = None

def fetch_one(sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    with pooled_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

def fetch_all(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with pooled_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return list(rows) if rows else []

def execute(sql: str, params: Tuple[Any, ...] = (), return_id: Optional[str] = None):
    with pooled_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            statement = sql
            if return_id:
                statement = f"{sql.rstrip().rstrip(';')} RETURNING {return_id}"
            cur.execute(statement, params)
            if return_id:
                row = cur.fetchone()
                return row[return_id] if row else None
            return True
