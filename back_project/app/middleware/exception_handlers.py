from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


log = logging.getLogger("app.error")


def _request_id(request: Request) -> str:
    rid = getattr(getattr(request, "state", None), "request_id", None)
    return str(rid or "n/a")


def _error_payload(
    *,
    request_id: str,
    status_code: int,
    message: str,
    path: str,
    detail: Any = None,
) -> dict:
    payload = {
        "ok": False,
        "error": {
            "status_code": status_code,
            "message": message,
            "path": path,
            "request_id": request_id,
        },
    }
    if detail is not None:
        payload["error"]["detail"] = detail
    return payload


def register_exception_handlers(app: FastAPI) -> None:
    async def _handle_http_exception(request: Request, exc: HTTPException | StarletteHTTPException):
        rid = _request_id(request)
        log.warning(
            "request_id=%s status=%s path=%s detail=%s",
            rid,
            exc.status_code,
            request.url.path,
            exc.detail,
        )
        payload = _error_payload(
            request_id=rid,
            status_code=exc.status_code,
            message="Request failed",
            path=request.url.path,
            detail=exc.detail,
        )
        return JSONResponse(status_code=exc.status_code, content=payload, headers={"X-Request-Id": rid})

    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)

    @app.exception_handler(Exception)
    async def on_unhandled_exception(request: Request, exc: Exception):
        rid = _request_id(request)
        log.exception(
            "request_id=%s status=500 path=%s unhandled_exception=%s",
            rid,
            request.url.path,
            exc,
        )
        payload = _error_payload(
            request_id=rid,
            status_code=500,
            message="Internal server error",
            path=request.url.path,
            detail=str(exc),
        )
        return JSONResponse(status_code=500, content=payload, headers={"X-Request-Id": rid})
