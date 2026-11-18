from __future__ import annotations

import asyncio
import hashlib
import json
from typing import List

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import StreamingResponse

from app.deps.auth_dep import get_current_user
from app.services.features import pipeline_job_service


router = APIRouter(prefix="/pipeline/jobs", tags=["pipeline-jobs"])


@router.get("")
def list_pipeline_jobs(
    status: str = Query("all"),
    scope: str = Query("auto", pattern="^(auto|personal|se)$"),
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
):
    return pipeline_job_service.list_jobs(
        actor_user_id=int(user["id"]),
        is_admin=bool(user.get("is_admin")),
        status=status,
        scope=scope,
        limit=limit,
    )


@router.post("/retry")
def retry_pipeline_jobs(
    job_ids: List[int] = Body(..., embed=True),
    user: dict = Depends(get_current_user),
):
    return pipeline_job_service.retry_jobs(
        job_ids,
        actor_user_id=int(user["id"]),
        is_admin=bool(user.get("is_admin")),
    )


@router.get("/stream")
async def stream_pipeline_jobs(
    scope: str = Query("auto", pattern="^(auto|personal|se)$"),
    user: dict = Depends(get_current_user),
):
    actor_user_id = int(user["id"])
    is_admin = bool(user.get("is_admin"))

    async def events():
        previous_digest = ""
        heartbeat = 0
        while True:
            snapshot = await asyncio.to_thread(
                pipeline_job_service.list_jobs,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
                status="all",
                scope=scope,
                limit=1000,
            )
            payload = json.dumps(
                snapshot,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if digest != previous_digest:
                previous_digest = digest
                yield f"event: pipeline\ndata: {payload}\n\n"
                heartbeat = 0
            else:
                heartbeat += 1
                if heartbeat >= 8:
                    yield ": keep-alive\n\n"
                    heartbeat = 0
            await asyncio.sleep(2)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]