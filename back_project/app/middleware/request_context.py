from __future__ import annotations

import time
import uuid
import logging

from fastapi import Request


log = logging.getLogger("app.request")


async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = request_id

    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    response.headers["X-Request-Id"] = request_id
    log.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        getattr(response, "status_code", "unknown"),
        elapsed_ms,
    )
    return response

