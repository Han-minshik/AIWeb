from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence, Tuple, Optional

from app.utils.db import pooled_connection

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
    with pooled_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return list(rows) if rows else []

def fetch_one(sql: str, params: Sequence[Any] | Tuple[Any, ...] = ()) -> dict | None:
    with pooled_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

def execute(sql: str, params: Sequence[Any] | Tuple[Any, ...] = (), return_last_id: bool = False) -> int | None:
    with pooled_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            statement = sql
            if return_last_id and "RETURNING" not in sql.upper():
                statement = f"{sql.rstrip().rstrip(';')} RETURNING *"
            cur.execute(statement, params)
            if not return_last_id:
                return None
            row = cur.fetchone()
            return int(next(iter(row.values()))) if row else None

def executemany(sql: str, param_list: Iterable[Sequence[Any] | Mapping[str, Any]]) -> None:
    with pooled_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, list(param_list))
