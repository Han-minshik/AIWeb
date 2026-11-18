from __future__ import annotations

from typing import Optional

from app.repositories.common import execute, fetch_all


def cleanup_expired() -> int:
    execute(
        """
        DELETE FROM notifications
         WHERE expires_at IS NOT NULL
           AND expires_at < CURRENT_TIMESTAMP
        """
    )
    return 0


def list_notifications(user_id: int, unread_only: bool, limit: int, offset: int) -> list[dict]:
    return fetch_all(
        """
        SELECT
            notification_id AS id,
            notification_id,
            user_id,
            type AS kind,
            type,
            severity,
            title,
            message AS body,
            message,
            resource_type,
            resource_id,
            is_read,
            read_at,
            created_at,
            expires_at
          FROM notifications
         WHERE user_id=%s
           AND (%s=FALSE OR (is_read=FALSE AND read_at IS NULL))
         ORDER BY created_at DESC, notification_id DESC
         LIMIT %s OFFSET %s
        """,
        (int(user_id), bool(unread_only), int(limit), int(offset)),
    )


def count_notifications(user_id: int, unread_only: bool) -> int:
    rows = fetch_all(
        """
        SELECT COUNT(*) AS cnt
          FROM notifications
         WHERE user_id=%s
           AND (%s=FALSE OR (is_read=FALSE AND read_at IS NULL))
        """,
        (int(user_id), bool(unread_only)),
    )
    return int(rows[0]["cnt"]) if rows else 0


def unread_count(user_id: int) -> int:
    rows = fetch_all(
        """
        SELECT COUNT(*) AS cnt
          FROM notifications
         WHERE user_id=%s
           AND is_read=FALSE
           AND read_at IS NULL
        """,
        (int(user_id),),
    )
    return int(rows[0]["cnt"]) if rows else 0


def mark_read(user_id: int, ids: list[int]) -> None:
    if not ids:
        return
    placeholders = ",".join(["%s"] * len(ids))
    execute(
        f"""
        UPDATE notifications
           SET is_read=TRUE,
               read_at=CURRENT_TIMESTAMP
         WHERE user_id=%s
           AND notification_id IN ({placeholders})
        """,
        tuple([int(user_id)] + [int(x) for x in ids]),
    )


def mark_all_read(user_id: int) -> int:
    before = unread_count(user_id)
    execute(
        """
        UPDATE notifications
           SET is_read=TRUE,
               read_at=CURRENT_TIMESTAMP
         WHERE user_id=%s
           AND is_read=FALSE
           AND read_at IS NULL
        """,
        (int(user_id),),
    )
    return before


def delete_all(user_id: int) -> int:
    before = count_notifications(user_id=user_id, unread_only=False)
    execute("DELETE FROM notifications WHERE user_id=%s", (int(user_id),))
    return before


def find_recent_duplicate(
    user_id: int,
    *,
    kind: str,
    title: str,
    body: str | None,
    severity: str,
) -> Optional[int]:
    rows = fetch_all(
        """
        SELECT notification_id AS id
          FROM notifications
         WHERE user_id=%s
           AND type=%s
           AND severity=%s
           AND title=%s
           AND COALESCE(message, '') = COALESCE(%s, '')
           AND created_at >= CURRENT_TIMESTAMP - INTERVAL '1 day'
         ORDER BY notification_id DESC
         LIMIT 1
        """,
        (int(user_id), kind, severity, title, body),
    )
    if not rows:
        return None
    return int(rows[0]["id"])


def insert_notification(
    user_id: int,
    *,
    kind: str,
    title: str,
    body: str | None,
    severity: str,
) -> int:
    return execute(
        """
        INSERT INTO notifications(user_id, type, title, message, severity, is_read, created_at)
        VALUES(%s,%s,%s,%s,%s,FALSE,CURRENT_TIMESTAMP)
        """,
        (int(user_id), kind, title, body, severity),
        True,
    )
