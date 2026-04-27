from __future__ import annotations

import os
import pymysql
from typing import Any, Dict, List, Optional, Tuple

try:
    from app.core.config import DB_CONFIG  
except Exception:
    DB_CONFIG: Dict[str, str] = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "app"),
    }

_CONN: Optional[pymysql.connections.Connection] = None

def _new_connection(autocommit: bool = True) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DB_CONFIG.get("host", "127.0.0.1"),
        user=DB_CONFIG.get("user", "root"),
        password=DB_CONFIG.get("password", ""),
        database=DB_CONFIG.get("database", "app"),
        port=int(os.getenv("DB_PORT", "3306")),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
        charset="utf8mb4",
    )

def get_connection(autocommit: bool = True) -> pymysql.connections.Connection:
    return _new_connection(autocommit=autocommit)

def _ensure_conn() -> pymysql.connections.Connection:
    global _CONN
    try:
        if _CONN is None or not _CONN.open:
            _CONN = _new_connection(autocommit=True)
    except Exception:
        _CONN = _new_connection(autocommit=True)
    return _CONN

def get_cursor() -> pymysql.cursors.Cursor:
    conn = _ensure_conn()
    return conn.cursor()

def commit() -> None:
    try:
        if _CONN and _CONN.open and not _CONN.get_autocommit():
            _CONN.commit()
    except Exception:
        pass

def rollback() -> None:
    try:
        if _CONN and _CONN.open and not _CONN.get_autocommit():
            _CONN.rollback()
    except Exception:
        pass

def close_connection() -> None:
    global _CONN
    try:
        if _CONN and _CONN.open:
            _CONN.close()
    finally:
        _CONN = None

def fetch_one(sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    conn = _new_connection(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def fetch_all(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    conn = _new_connection(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return list(rows) if rows else []
    finally:
        conn.close()

def execute(sql: str, params: Tuple[Any, ...] = (), return_id: Optional[str] = None):
    conn = _new_connection(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if return_id:
                return cur.lastrowid
            return True
    finally:
        conn.close()
