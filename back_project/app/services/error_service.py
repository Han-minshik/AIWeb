from __future__ import annotations

import logging
from typing import Any, Dict

from app.services import notifications_service


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
    user_id = context.get("user_id")

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

    try:
        if user_id is not None:
            notifications_service.emit_notification(
                user_id=int(user_id),
                kind="system",
                severity="error",
                title="Client error reported",
                body=f"source={source}, path={path}, message={message}, request_id={request_id or '-'}",
            )
    except Exception:
        pass

    return {"ok": True, "logged": True, "request_id": request_id}
