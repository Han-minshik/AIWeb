from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence, Tuple, Optional

from app.utils.db import get_connection 

__all__ = [
    "fetch_all",
    "fetch_one",
    "execute",
    "executemany",
    "dumps_json_or_none",
    "loads_json_or_none",
]

def dumps_json_or_none(val: Any, *, ensure_ascii: bool = False) -> Optional[str]:
    if val is None:
        return None
    try:
        return json.dumps(val, ensure_ascii=ensure_ascii)
    except TypeError:
        return json.dumps(str(val), ensure_ascii=ensure_ascii)

def loads_json_or_none(s: Any) -> Any:
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    if isinstance(s, (bytes, bytearray)):
        s = s.decode("utf-8", errors="ignore")
    s = str(s).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s

def fetch_all(sql: str, params: Sequence[Any] | Tuple[Any, ...] = ()) -> list[dict]:
    conn = get_connection(autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return list(rows) if rows else []
    finally:
        try:
            cur.close()
        finally:
            conn.close()

def fetch_one(sql: str, params: Sequence[Any] | Tuple[Any, ...] = ()) -> dict | None:
    conn = get_connection(autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        try:
            cur.close()
        finally:
            conn.close()

def execute(sql: str, params: Sequence[Any] | Tuple[Any, ...] = (), return_last_id: bool = False) -> int | None:
    conn = get_connection(autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        last_id = cur.lastrowid
        return last_id if return_last_id else None
    finally:
        try:
            cur.close()
        finally:
            conn.close()

def executemany(sql: str, param_list: Iterable[Sequence[Any] | Mapping[str, Any]]) -> None:
    conn = get_connection(autocommit=True)
    cur = conn.cursor()
    try:
        cur.executemany(sql, list(param_list))
    finally:
        try:
            cur.close()
        finally:
            conn.close()
