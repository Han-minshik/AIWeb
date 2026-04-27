from __future__ import annotations

import asyncio
from typing import Any

from app.dao import notifications_dao


_subscribers: list[asyncio.Queue] = []


def _broadcast(event: dict[str, Any]) -> None:
    dead: list[asyncio.Queue] = []
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    for q in dead:
        if q in _subscribers:
            _subscribers.remove(q)


def list_notifications(user_id: int, unread_only: bool) -> list[dict]:
    return notifications_dao.list_notifications(user_id=user_id, unread_only=unread_only)


def unread_count(user_id: int) -> int:
    return notifications_dao.unread_count(user_id=user_id)


def mark_read(user_id: int, ids: list[int]) -> dict:
    if not ids:
        return {"ok": True, "count": 0}
    notifications_dao.mark_read(user_id=user_id, ids=ids)
    _broadcast({"type": "read", "ids": ids})
    return {"ok": True, "count": len(ids)}


def register_subscriber() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.append(q)
    return q


def unregister_subscriber(q: asyncio.Queue) -> None:
    if q in _subscribers:
        _subscribers.remove(q)


def emit_notification(title: str, body: str | None, severity: str, user_id: int) -> dict:
    nid = notifications_dao.insert_notification(
        user_id=user_id,
        title=title,
        body=body,
        severity=severity,
    )
    ev = {"type": "notify", "id": nid, "title": title, "body": body, "severity": severity}
    _broadcast(ev)
    return {"id": nid, "message": "ok"}

