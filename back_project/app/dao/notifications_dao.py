from __future__ import annotations

from app.repositories.common import execute, fetch_all
from app.xml.query_loader import get_sql


_MAPPER = "notifications"


def list_notifications(user_id: int, unread_only: bool) -> list[dict]:
    return fetch_all(
        get_sql(_MAPPER, "list_notifications"),
        (user_id, 1 if unread_only else 0),
    )


def unread_count(user_id: int) -> int:
    rows = fetch_all(get_sql(_MAPPER, "unread_count"), (user_id,))
    return int(rows[0]["cnt"]) if rows else 0


def mark_read(user_id: int, ids: list[int]) -> None:
    if not ids:
        return
    prefix = get_sql(_MAPPER, "mark_read_prefix")
    sql = prefix + " (" + ",".join(["%s"] * len(ids)) + ")"
    execute(sql, tuple([user_id] + ids))


def insert_notification(user_id: int, title: str, body: str | None, severity: str) -> int:
    return execute(
        get_sql(_MAPPER, "insert_notification"),
        (user_id, title, body, severity),
        True,
    )

