from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header

from app.services.features import indexer_callback_service, parser_callback_service


router = APIRouter(prefix="/pipeline", tags=["pipeline-callback"])


@router.post("/parser/callback")
def parser_callback(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return parser_callback_service.apply_parser_callback(
        payload,
        authorization=authorization,
    )

@router.post("/indexer/callback")
def indexer_callback(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return indexer_callback_service.apply_indexer_callback(
        payload,
        authorization=authorization,
    )
