from __future__ import annotations

import asyncio
import json
from typing import List

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import StreamingResponse

from app.deps.auth_dep import get_current_user_id
from app.services import notifications_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    user_id: int = Depends(get_current_user_id),
    unread_only: bool = Query(False),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return notifications_service.list_notifications(
        user_id=user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count")
def unread_count(user_id: int = Depends(get_current_user_id)):
    return {"unread": notifications_service.unread_count(user_id=user_id)}


@router.post("/read")
def mark_read(ids: List[int] = Body(...), user_id: int = Depends(get_current_user_id)):
    return notifications_service.mark_read(user_id=user_id, ids=ids)


@router.post("/read-all")
def mark_read_all(user_id: int = Depends(get_current_user_id)):
    return notifications_service.mark_all_read(user_id=user_id)


@router.delete("/all")
def delete_all(user_id: int = Depends(get_current_user_id)):
    return notifications_service.delete_all(user_id=user_id)


@router.get("/stream")
async def stream(user_id: int = Depends(get_current_user_id)):
    queue = notifications_service.register_subscriber(user_id)

    async def events():
        previous_unread = await asyncio.to_thread(
            notifications_service.peek_unread_count, user_id=user_id
        )
        heartbeat = 0
        yield f"data: {json.dumps({'type': 'hello', 'unread': previous_unread})}\n\n"
        try:
            while True:
                event = None
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2)
                except asyncio.TimeoutError:
                    pass

                unread = await asyncio.to_thread(
                    notifications_service.peek_unread_count, user_id=user_id
                )
                if event is not None:
                    event = {**event, "unread": unread}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    previous_unread = unread
                    heartbeat = 0
                elif unread != previous_unread:
                    previous_unread = unread
                    yield f"data: {json.dumps({'type': 'count', 'unread': unread})}\n\n"
                    heartbeat = 0
                else:
                    heartbeat += 1
                    if heartbeat >= 8:
                        yield ": keep-alive\n\n"
                        heartbeat = 0
        finally:
            notifications_service.unregister_subscriber(user_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/emit")
def emit_notification(
    title: str = Body(...),
    body: str | None = Body(None),
    severity: str = Body("info"),
    kind: str = Body("system"),
    user_id: int = Body(..., embed=True),
):
    return notifications_service.emit_notification(
        title=title,
        body=body,
        severity=severity,
        user_id=user_id,
        kind=kind,
    )