from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import Depends, HTTPException, Path, Query, status
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.config import (
    RAG_SSE_EXTRA_HEADERS,
    RAG_SSE_TIMEOUT,
    RAG_SSE_TOKEN,
    RAG_SSE_URL,
)
from app.deps.auth_dep import get_current_user_id
from app.repositories.common import fetch_all, fetch_one
from app.services.common.rag_token_service import create_rag_user_token
from app.services.features.document_db import collect_descendants, get_kb_entry


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class SummaryOut(BaseModel):
    kb_id: int
    total_docs: int = 0
    total_bytes: int = 0
    last_content_update: Optional[str] = None
    last_kb_sync: Optional[str] = None
    active_categories: Optional[int] = None
    health: str = "OK"


class GrowthPoint(BaseModel):
    month: str
    docs_created: int = 0
    bytes_created: int = 0


def _stats_url(path: str) -> str:
    parsed = urlsplit(RAG_SSE_URL)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


async def _proxy_stats(
    path: str,
    user: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
):
    if not RAG_SSE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG_SSE_TOKEN is not configured",
        )

    try:
        rag_user_token = create_rag_user_token(user)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    headers = {"Accept": "application/json"}
    if RAG_SSE_EXTRA_HEADERS:
        headers.update(RAG_SSE_EXTRA_HEADERS)
    headers["Authorization"] = f"Bearer {RAG_SSE_TOKEN}"
    headers["X-SpaceAI-User-Token"] = f"Bearer {rag_user_token}"

    try:
        timeout = httpx.Timeout(min(max(int(RAG_SSE_TIMEOUT), 1), 30))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _stats_url(path),
                headers=headers,
                params=params,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="통계 서버 응답 시간이 초과되었습니다.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="통계 서버에 연결할 수 없습니다.",
        ) from exc

    if response.status_code >= 400:
        # An upstream 401 is not a Space AI login failure. Returning 502 avoids
        # the frontend's global 401 handler from redirecting to /login.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"통계 서버 요청에 실패했습니다. (upstream: {response.status_code})",
        )

    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="통계 서버가 올바른 JSON을 반환하지 않았습니다.",
        ) from exc


async def get_opensearch_stats(
    user: Dict[str, Any] = Depends(get_current_user),
):
    return await _proxy_stats("/stats/opensearch", user)


async def get_activity_stats(
    period: str = Query("week", pattern="^(day|week|month|year)$"),
    user: Dict[str, Any] = Depends(get_current_user),
):
    return await _proxy_stats("/stats/activity", user, {"period": period})


async def get_recent_stats(
    limit: int = Query(10, ge=1, le=100),
    user: Dict[str, Any] = Depends(get_current_user),
):
    return await _proxy_stats("/stats/recent", user, {"limit": limit})


def _subtree_ids(kb_id: int) -> List[int]:
    ids = collect_descendants([int(kb_id)])
    return list(ids) if ids else [int(kb_id)]


def kb_dashboard_summary(
    kb_id: int = Path(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    get_kb_entry(kb_id)
    ids = _subtree_ids(kb_id)
    placeholders = ",".join(["%s"] * len(ids))
    row = fetch_one(
        f"""
        SELECT
            COUNT(DISTINCT so.object_id) AS total_docs,
            COALESCE(SUM(COALESCE(so.size_bytes, 0)), 0) AS total_bytes,
            MAX(COALESCE(so.db_updated_at, so.uploaded_at)) AS last_content_update
          FROM tree_nodes tn
          JOIN storage_objects so ON so.object_id = tn.doc_id
         WHERE tn.node_id IN ({placeholders})
           AND so.doc_status <> 'deleted_pending'
        """,
        tuple(ids),
    )
    cat = fetch_one(
        f"""
        SELECT COUNT(*) AS cnt
          FROM tree_nodes
         WHERE node_id IN ({placeholders})
           AND is_folder=TRUE
        """,
        tuple(ids),
    )
    return SummaryOut(
        kb_id=kb_id,
        total_docs=int((row or {}).get("total_docs") or 0),
        total_bytes=int((row or {}).get("total_bytes") or 0),
        last_content_update=_iso((row or {}).get("last_content_update")),
        last_kb_sync=None,
        active_categories=int((cat or {}).get("cnt") or 0),
        health="OK",
    )


def kb_dashboard_growth(
    kb_id: int = Path(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    get_kb_entry(kb_id)
    ids = _subtree_ids(kb_id)
    placeholders = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT
            TO_CHAR(DATE_TRUNC('month', COALESCE(so.uploaded_at, tn.created_at)), 'YYYY-MM-DD') AS month,
            COUNT(DISTINCT so.object_id) AS docs_created,
            COALESCE(SUM(COALESCE(so.size_bytes, 0)), 0) AS bytes_created
          FROM tree_nodes tn
          JOIN storage_objects so ON so.object_id = tn.doc_id
         WHERE tn.node_id IN ({placeholders})
           AND so.doc_status <> 'deleted_pending'
         GROUP BY month
         ORDER BY month
        """,
        tuple(ids),
    )
    return [
        GrowthPoint(
            month=str(row.get("month")),
            docs_created=int(row.get("docs_created") or 0),
            bytes_created=int(row.get("bytes_created") or 0),
        )
        for row in rows or []
    ]
