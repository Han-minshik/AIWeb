from __future__ import annotations

from typing import Any, Dict, Optional

from app.repositories.common import execute, fetch_all, fetch_one
from app.xml.query_loader import get_sql


_MAPPER = "admin"


def list_users(q: Optional[str], page: int, page_size: int) -> Dict[str, Any]:
    count_sql = get_sql(_MAPPER, "users_count_base")
    list_sql = get_sql(_MAPPER, "users_list_base")
    params: list[Any] = []

    if q:
        like = f"%{q}%"
        where = " AND (username LIKE %s OR email LIKE %s OR COALESCE(full_name,'') LIKE %s)"
        count_sql += where
        list_sql += where
        params.extend([like, like, like])

    total_row = fetch_one(count_sql, tuple(params)) or {"cnt": 0}
    total = int(total_row["cnt"])

    offset = (page - 1) * page_size
    list_sql += " ORDER BY username LIMIT %s OFFSET %s"
    rows = fetch_all(list_sql, tuple(params + [page_size, offset]))
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


def create_user(
    org_id: int,
    username: str,
    email: str,
    password: str,
    full_name: str,
    is_admin: bool,
) -> int:
    return execute(
        get_sql(_MAPPER, "insert_user"),
        (org_id, username, email, password, full_name, 1 if is_admin else 0),
        True,
    )


def get_user_detail(user_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(get_sql(_MAPPER, "user_detail_by_id"), (user_id,))


def update_user_fields(user_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    set_parts: list[str] = []
    params: list[Any] = []
    for k, v in fields.items():
        if k == "password":
            set_parts.append("password_hash=SHA2(%s,256)")
            params.append(v)
            continue
        set_parts.append(f"{k}=%s")
        params.append(v)
    params.append(user_id)
    sql = f"UPDATE users SET {', '.join(set_parts)} WHERE user_id=%s"
    execute(sql, tuple(params))


def toggle_admin(user_id: int) -> None:
    execute(get_sql(_MAPPER, "toggle_admin"), (user_id,))


def reset_password(user_id: int, password: str) -> None:
    execute(get_sql(_MAPPER, "reset_password"), (password, user_id))


def delete_user(user_id: int) -> None:
    execute(get_sql(_MAPPER, "delete_user"), (user_id,))


def list_access_logs(limit: int, user_id: Optional[int]) -> list[dict]:
    sql = get_sql(_MAPPER, "access_logs_base")
    params: list[Any] = []
    if user_id is not None:
        sql += " WHERE user_id=%s"
        params.append(user_id)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    return fetch_all(sql, tuple(params))

