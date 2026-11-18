from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any, Dict, Optional

from app.repositories.common import execute, fetch_all, fetch_one
from app.xml.query_loader import get_sql


_MAPPER = "admin"


@lru_cache(maxsize=1)
def _has_users_group_id() -> bool:
    row = fetch_one(
        """
        SELECT 1 AS ok
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = current_schema()
          AND TABLE_NAME = 'users'
          AND COLUMN_NAME = 'group_id'
        LIMIT 1
        """
    )
    return bool(row)


@lru_cache(maxsize=16)
def _table_has_org_id(table_name: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS ok
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = current_schema()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = 'org_id'
        LIMIT 1
        """,
        (table_name,),
    )
    return bool(row)


@lru_cache(maxsize=64)
def _table_has_column(table_name: str, column_name: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS ok
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = current_schema()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    return bool(row)


def list_users(
    q: Optional[str],
    page: int,
    page_size: int,
    group_id: Optional[int] = None,
    group_empty: bool = False,
    position_id: Optional[int] = None,
    position_empty: bool = False,
    is_admin: Optional[bool] = None,
) -> Dict[str, Any]:
    count_sql = "SELECT COUNT(1) AS cnt FROM users u WHERE 1=1"
    has_group = _has_users_group_id()
    if has_group:
        list_sql = """
        SELECT
          u.user_id AS id,
          u.username,
          u.email,
          COALESCE(u.full_name, '') AS full_name,
          u.group_id AS group_id,
          COALESCE(g.name, '') AS department,
          u.position_id AS position_id,
          COALESCE(p.name, '') AS position,
          COALESCE(u.is_admin, FALSE) AS is_admin,
          COALESCE(u.is_active, TRUE) AS is_active
        FROM users u
        LEFT JOIN org_groups g ON g.group_id = u.group_id
        LEFT JOIN positions p ON p.position_id = u.position_id
        WHERE 1=1
        """
    else:
        list_sql = """
        SELECT
          u.user_id AS id,
          u.username,
          u.email,
          COALESCE(u.full_name, '') AS full_name,
          NULL AS group_id,
          '' AS department,
          u.position_id AS position_id,
          COALESCE(p.name, '') AS position,
          COALESCE(u.is_admin, FALSE) AS is_admin,
          COALESCE(u.is_active, TRUE) AS is_active
        FROM users u
        LEFT JOIN positions p ON p.position_id = u.position_id
        WHERE 1=1
        """
    params: list[Any] = []

    if q:
        like = f"%{q}%"
        where = " AND (u.username LIKE %s OR u.email LIKE %s OR COALESCE(u.full_name,'') LIKE %s)"
        count_sql += where
        list_sql += where
        params.extend([like, like, like])

    if has_group:
        if group_empty:
            where = " AND u.group_id IS NULL"
            count_sql += where
            list_sql += where
        elif group_id is not None:
            where = " AND u.group_id = %s"
            count_sql += where
            list_sql += where
            params.append(int(group_id))
    elif group_id is not None:
        # A schema without users.group_id can only match an empty department filter.
        where = " AND 1=0"
        count_sql += where
        list_sql += where

    if position_empty:
        where = " AND u.position_id IS NULL"
        count_sql += where
        list_sql += where
    elif position_id is not None:
        where = " AND u.position_id = %s"
        count_sql += where
        list_sql += where
        params.append(int(position_id))

    if is_admin is not None:
        where = " AND COALESCE(u.is_admin, FALSE) = %s"
        count_sql += where
        list_sql += where
        params.append(bool(is_admin))

    total_row = fetch_one(count_sql, tuple(params)) or {"cnt": 0}
    total = int(total_row["cnt"])

    offset = (page - 1) * page_size
    list_sql += " ORDER BY username LIMIT %s OFFSET %s"
    rows = fetch_all(list_sql, tuple(params + [page_size, offset]))
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


def create_user(
    org_id: Optional[int],
    username: str,
    email: str,
    password: str,
    full_name: str,
    group_id: Optional[int],
    position_id: Optional[int],
    is_admin: bool,
    is_active: bool,
) -> int:
    has_group = _has_users_group_id()
    has_org = _table_has_org_id("users")
    safe_org_id = int(org_id or 1)
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if has_group and has_org:
        sql = """
        INSERT INTO users (
          org_id, group_id, position_id, username, email, password_hash, full_name, is_admin, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            safe_org_id,
            group_id,
            position_id,
            username,
            email,
            password_hash,
            full_name,
            bool(is_admin),
            bool(is_active),
        )
    elif has_group:
        sql = """
        INSERT INTO users (
          group_id, position_id, username, email, password_hash, full_name, is_admin, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            group_id,
            position_id,
            username,
            email,
            password_hash,
            full_name,
            bool(is_admin),
            bool(is_active),
        )
    elif has_org:
        sql = """
        INSERT INTO users (
          org_id, position_id, username, email, password_hash, full_name, is_admin, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            safe_org_id,
            position_id,
            username,
            email,
            password_hash,
            full_name,
            bool(is_admin),
            bool(is_active),
        )
    else:
        sql = """
        INSERT INTO users (
          position_id, username, email, password_hash, full_name, is_admin, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            position_id,
            username,
            email,
            password_hash,
            full_name,
            bool(is_admin),
            bool(is_active),
        )
    return execute(sql, params, True)


def get_user_detail(user_id: int) -> Optional[Dict[str, Any]]:
    has_group = _has_users_group_id()
    if has_group:
        sql = """
        SELECT
          u.user_id AS id,
          u.username,
          u.email,
          u.full_name,
          u.group_id,
          COALESCE(g.name, '') AS department,
          u.position_id,
          COALESCE(p.name, '') AS position,
          u.is_admin,
          u.is_active
        FROM users u
        LEFT JOIN org_groups g ON g.group_id = u.group_id
        LEFT JOIN positions p ON p.position_id = u.position_id
        WHERE u.user_id=%s
        """
    else:
        sql = """
        SELECT
          u.user_id AS id,
          u.username,
          u.email,
          u.full_name,
          NULL AS group_id,
          '' AS department,
          u.position_id,
          COALESCE(p.name, '') AS position,
          u.is_admin,
          u.is_active
        FROM users u
        LEFT JOIN positions p ON p.position_id = u.position_id
        WHERE u.user_id=%s
        """
    return fetch_one(sql, (user_id,))


def update_user_fields(user_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    set_parts: list[str] = []
    params: list[Any] = []
    for k, v in fields.items():
        if k == "group_id" and not _has_users_group_id():
            continue
        if k == "password":
            set_parts.append("password_hash=%s")
            params.append(hashlib.sha256(str(v).encode()).hexdigest())
            continue
        set_parts.append(f"{k}=%s")
        params.append(v)
    if not set_parts:
        return
    params.append(user_id)
    sql = f"UPDATE users SET {', '.join(set_parts)} WHERE user_id=%s"
    execute(sql, tuple(params))


def toggle_admin(user_id: int) -> None:
    execute(get_sql(_MAPPER, "toggle_admin"), (user_id,))


def reset_password(user_id: int, password: str) -> None:
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    execute(get_sql(_MAPPER, "reset_password"), (password_hash, user_id))


def delete_user(user_id: int) -> None:
    execute(
        "UPDATE users SET is_active=FALSE, is_admin=FALSE WHERE user_id=%s",
        (user_id,),
    )


def hard_delete_user(user_id: int) -> None:
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


def list_departments() -> list[dict]:
    has_org = _table_has_org_id("org_groups")
    if has_org:
        return fetch_all(
            """
            SELECT group_id, name
            FROM org_groups
            WHERE org_id=%s
            ORDER BY name
            """,
            (1,),
        )
    return fetch_all(
        """
        SELECT group_id, name
        FROM org_groups
        ORDER BY name
        """
    )


def create_department(name: str) -> dict:
    has_org = _table_has_org_id("org_groups")
    if has_org:
        gid = execute(
            """
            INSERT INTO org_groups (org_id, name)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name
            """,
            (1, name),
            True,
        )
    else:
        gid = execute(
            """
            INSERT INTO org_groups (name)
            VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name
            """,
            (name,),
            True,
        )
    row = fetch_one(
        """
        SELECT group_id, name
        FROM org_groups
        WHERE group_id=%s
        """,
        (gid,),
    )
    return row or {"group_id": gid, "name": name}


def delete_department(group_id: int) -> bool:
    has_org = _table_has_org_id("org_groups")
    exists = fetch_one(
        """
        SELECT group_id
        FROM org_groups
        WHERE group_id=%s
        """
        + (" AND org_id=%s" if has_org else "")
        + "\nLIMIT 1",
        (group_id, 1) if has_org else (group_id,),
    )
    if not exists:
        return False

    if _has_users_group_id():
        ref_row = fetch_one(
            """
            SELECT COUNT(1) AS cnt
            FROM users
            WHERE group_id=%s
            """,
            (group_id,),
        ) or {"cnt": 0}
        if int(ref_row.get("cnt") or 0) > 0:
            raise ValueError("department_in_use")

    execute(
        """
        DELETE FROM org_groups
        WHERE group_id=%s
        """
        + (" AND org_id=%s" if has_org else ""),
        (group_id, 1) if has_org else (group_id,),
    )
    return True


def list_positions() -> list[dict]:
    has_org = _table_has_org_id("positions")
    if has_org:
        return fetch_all(
            """
            SELECT position_id, name
            FROM positions
            WHERE org_id=%s
            ORDER BY name
            """,
            (1,),
        )
    return fetch_all(
        """
        SELECT position_id, name
        FROM positions
        ORDER BY name
        """
    )


def create_position(name: str) -> dict:
    has_org = _table_has_org_id("positions")
    if has_org:
        pid = execute(
            """
            INSERT INTO positions (org_id, name)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name
            """,
            (1, name),
            True,
        )
    else:
        pid = execute(
            """
            INSERT INTO positions (name)
            VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name
            """,
            (name,),
            True,
        )
    row = fetch_one(
        """
        SELECT position_id, name
        FROM positions
        WHERE position_id=%s
        """,
        (pid,),
    )
    return row or {"position_id": pid, "name": name}


def delete_position(position_id: int) -> bool:
    has_org = _table_has_org_id("positions")
    exists = fetch_one(
        """
        SELECT position_id
        FROM positions
        WHERE position_id=%s
        """
        + (" AND org_id=%s" if has_org else "")
        + "\nLIMIT 1",
        (position_id, 1) if has_org else (position_id,),
    )
    if not exists:
        return False

    if _table_has_column("users", "position_id"):
        execute(
            """
            UPDATE users
            SET position_id = NULL
            WHERE position_id = %s
            """,
            (position_id,),
        )

    execute(
        """
        DELETE FROM positions
        WHERE position_id=%s
        """
        + (" AND org_id=%s" if has_org else ""),
        (position_id, 1) if has_org else (position_id,),
    )
    return True
