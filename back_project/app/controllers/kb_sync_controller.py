from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException

from app.deps.auth_dep import get_current_user_id
from app.services import notifications_service
from app.services.features import kb_sync_service as service

router = APIRouter(prefix="/kb/sync", tags=["kb-sync"])


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) or exc.__class__.__name__

router.get("/preview")(service.preview)


@router.post("/apply")
def apply_selected(
    kb_id: int = Body(...),
    selected_items: List[Dict[str, Any]] = Body(...),
    sync_direction: str = Body("LOCAL_TO_WEB"),
    local_apply: bool = Body(False),
    user_id: int = Depends(get_current_user_id),
):
    try:
        result = service.apply_selected(
            kb_id=kb_id,
            selected_items=selected_items,
            sync_direction=sync_direction,
            local_apply=local_apply,
            user_id=user_id,
        )
        failed_count = int((result or {}).get("failed_count") or 0)
        applied_count = int((result or {}).get("applied_count") or 0)
        if failed_count > 0:
            failures = (result or {}).get("failed") or []
            fail_preview = "; ".join(
                [
                    f"{str(x.get('target_path') or '-')} -> {str(x.get('error') or '-')}"
                    for x in failures[:3]
                ]
            )
            notifications_service.emit_notification(
                user_id=user_id,
                kind="transfer",
                severity="warning",
                title="KB sync finished with failures",
                body=f"applied={applied_count}, failed={failed_count}, detail={fail_preview}",
            )
        else:
            notifications_service.emit_notification(
                user_id=user_id,
                kind="transfer",
                severity="success",
                title="KB sync completed",
                body=f"applied={applied_count}, failed=0",
            )
        return result
    except Exception as exc:
        notifications_service.emit_notification(
            user_id=user_id,
            kind="transfer",
            severity="error",
            title="KB sync failed",
            body=f"reason={_error_text(exc)}",
        )
        raise


__all__ = ["router"]
