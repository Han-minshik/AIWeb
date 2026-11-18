from __future__ import annotations

import asyncio
from typing import Any, Dict

from app.dao import notifications_dao


_subscribers: dict[int, list[asyncio.Queue]] = {}


def _broadcast(user_id: int, event: dict[str, Any]) -> None:
    listeners = _subscribers.get(int(user_id), [])
    dead: list[asyncio.Queue] = []
    for q in listeners:
        try:
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    for q in dead:
        if q in listeners:
            listeners.remove(q)
    if not listeners and int(user_id) in _subscribers:
        _subscribers.pop(int(user_id), None)


def _normalize_severity(severity: str | None) -> str:
    v = str(severity or "info").strip().lower()
    if v not in {"info", "success", "warning", "error"}:
        return "info"
    return v


def _normalize_kind(kind: str | None) -> str:
    v = str(kind or "system").strip().lower()
    if v not in {"upload", "download", "transfer", "system"}:
        return "system"
    return v


def _cleanup_expired() -> None:
    notifications_dao.cleanup_expired()


def list_notifications(user_id: int, unread_only: bool, limit: int, offset: int) -> Dict[str, Any]:
    _cleanup_expired()
    safe_limit = max(1, min(int(limit or 10), 100))
    safe_offset = max(0, int(offset or 0))
    total = notifications_dao.count_notifications(user_id=user_id, unread_only=unread_only)
    rows = notifications_dao.list_notifications(
        user_id=user_id,
        unread_only=unread_only,
        limit=safe_limit,
        offset=safe_offset,
    )
    return {
        "items": rows or [],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "has_more": (safe_offset + len(rows or [])) < total,
        "retention_hours": 24,
    }


def unread_count(user_id: int) -> int:
    _cleanup_expired()
    return notifications_dao.unread_count(user_id=user_id)


def peek_unread_count(user_id: int) -> int:
    """Read the count without running retention cleanup on every SSE tick."""
    return notifications_dao.unread_count(user_id=user_id)


def mark_read(user_id: int, ids: list[int]) -> dict:
    if not ids:
        return {"ok": True, "count": 0}
    notifications_dao.mark_read(user_id=user_id, ids=ids)
    _broadcast(user_id, {"type": "read", "ids": ids})
    return {"ok": True, "count": len(ids)}


def mark_all_read(user_id: int) -> dict:
    count = notifications_dao.mark_all_read(user_id=user_id)
    _broadcast(user_id, {"type": "read_all"})
    return {"ok": True, "count": count}


def delete_all(user_id: int) -> dict:
    count = notifications_dao.delete_all(user_id=user_id)
    _broadcast(user_id, {"type": "delete_all"})
    return {"ok": True, "count": count}


def register_subscriber(user_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(int(user_id), []).append(q)
    return q


def unregister_subscriber(user_id: int, q: asyncio.Queue) -> None:
    listeners = _subscribers.get(int(user_id), [])
    if q in listeners:
        listeners.remove(q)
    if not listeners:
        _subscribers.pop(int(user_id), None)


def emit_notification(
    title: str,
    body: str | None,
    severity: str,
    user_id: int,
    kind: str = "system",
    event_data: dict[str, Any] | None = None,
) -> dict:
    _cleanup_expired()
    safe_kind = _normalize_kind(kind)
    safe_severity = _normalize_severity(severity)
    safe_title = str(title or "").strip()[:200] or "Notification"
    safe_body = None if body is None else str(body).strip()

    dup_id = notifications_dao.find_recent_duplicate(
        user_id=user_id,
        kind=safe_kind,
        title=safe_title,
        body=safe_body,
        severity=safe_severity,
    )
    if dup_id is not None:
        return {"id": dup_id, "message": "duplicate_skipped", "deduped": True}

    nid = notifications_dao.insert_notification(
        user_id=user_id,
        kind=safe_kind,
        title=safe_title,
        body=safe_body,
        severity=safe_severity,
    )
    ev = {
        "type": "notify",
        "id": nid,
        "title": safe_title,
        "body": safe_body,
        "severity": safe_severity,
        "kind": safe_kind,
    }
    if event_data:
        reserved = set(ev)
        ev.update({key: value for key, value in event_data.items() if key not in reserved})
    _broadcast(user_id, ev)
    return {"id": nid, "message": "ok", "deduped": False}
