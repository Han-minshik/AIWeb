from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Request

from app.services import error_service

router = APIRouter(prefix="/errors", tags=["errors"])


@router.post("/client")
def report_client_error(
    request: Request,
    payload: Dict[str, Any] = Body(default={}),
):
    context = {
        "request_id": getattr(request.state, "request_id", None),
        "path": request.url.path,
        "method": request.method,
        "client_ip": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""),
    }
    return error_service.report_client_error(payload=payload, context=context)

