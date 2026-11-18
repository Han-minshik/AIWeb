from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException


router = APIRouter(prefix="/ai-index", tags=["ai-index"])


@router.post("/callback", deprecated=True)
def legacy_ai_index_callback(
    payload: Dict[str, Any] = Body(...),
):
    del payload
    raise HTTPException(
        status_code=410,
        detail="Use /pipeline/indexer/callback with an RS256 Bearer token",
    )
