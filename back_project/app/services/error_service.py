from __future__ import annotations

import logging
from typing import Any, Dict


log = logging.getLogger("app.client_error")


def report_client_error(payload: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    message = str(payload.get("message") or "Client error")
    source = str(payload.get("source") or "frontend")
    stack = str(payload.get("stack") or "")
    meta = payload.get("meta")
    request_id = context.get("request_id")
    path = context.get("path")
    method = context.get("method")
    client_ip = context.get("client_ip")
    user_agent = context.get("user_agent")

    log.error(
        "client_error request_id=%s method=%s path=%s ip=%s source=%s message=%s meta=%s stack=%s ua=%s",
        request_id,
        method,
        path,
        client_ip,
        source,
        message,
        meta,
        stack,
        user_agent,
    )
    return {"ok": True, "logged": True, "request_id": request_id}

