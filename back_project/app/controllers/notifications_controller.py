from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse

from app.deps.auth_dep import get_current_user_id
from app.services import notifications_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(user_id: int = Depends(get_current_user_id), unread_only: bool = False):
    return notifications_service.list_notifications(user_id=user_id, unread_only=unread_only)


@router.get("/unread-count")
def unread_count(user_id: int = Depends(get_current_user_id)):
    return {"unread": notifications_service.unread_count(user_id=user_id)}


@router.post("/read")
def mark_read(ids: List[int] = Body(..., embed=True), user_id: int = Depends(get_current_user_id)):
    return notifications_service.mark_read(user_id=user_id, ids=ids)


@router.get("/stream")
async def stream(user_id: int = Depends(get_current_user_id)):
    q = notifications_service.register_subscriber()

    async def gen():
        try:
            unread = notifications_service.unread_count(user_id=user_id)
            yield f"data: {json.dumps({'type': 'hello', 'unread': unread})}\n\n"
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            notifications_service.unregister_subscriber(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/emit")
def emit_notification(
    title: str = Body(...),
    body: str | None = Body(None),
    severity: str = Body("info"),
    user_id: int = Body(..., embed=True),
):
    return notifications_service.emit_notification(
        title=title,
        body=body,
        severity=severity,
        user_id=user_id,
    )

