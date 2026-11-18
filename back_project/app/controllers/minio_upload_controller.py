from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.deps.auth_dep import get_current_user_id
from app.services import notifications_service
from app.services.features import minio_upload_service
from app.services.features import kiwix_preparsed_service


router = APIRouter(prefix="/docs/uploads", tags=["docs-minio-upload"])
logger = logging.getLogger(__name__)
_KIWIX_UPLOAD_SEMAPHORE = asyncio.Semaphore(1)


class VersionUploadInitIn(BaseModel):
    upload_request_id: str = Field(..., min_length=36, max_length=36)
    file_name: str = Field(..., min_length=1, max_length=275)
    file_size: int = Field(..., ge=0)
    content_type: str = Field(default='application/octet-stream', max_length=100)


def _notify_upload(**kwargs) -> None:
    try:
        notifications_service.emit_notification(**kwargs)
    except Exception:
        logger.exception("Failed to emit upload notification")


class UploadInitIn(BaseModel):
    upload_request_id: str = Field(..., min_length=36, max_length=36)
    parent_node_id: int = Field(..., ge=1)
    file_name: str = Field(..., min_length=1, max_length=275)
    file_size: int = Field(..., ge=0)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    branch_type: str = Field(default="general", max_length=20)
    kiwix_metadata: Optional[dict] = None


@router.post("/init")
def initialize_upload(body: UploadInitIn, user_id: int = Depends(get_current_user_id)):
    return minio_upload_service.initialize_upload(
        upload_request_id=body.upload_request_id,
        parent_node_id=body.parent_node_id,
        file_name=body.file_name,
        file_size=body.file_size,
        content_type=body.content_type,
        branch_type=body.branch_type,
        user_id=user_id,
        kiwix_metadata=body.kiwix_metadata,
    )


@router.post('/kiwix/preparsed')
async def upload_kiwix_preparsed(
    upload_request_id: str = Form(...),
    parent_node_id: int = Form(...),
    markdown: UploadFile = File(...),
    assets: list[UploadFile] = File(default=[]),
    asset_paths_json: str = Form(default='[]'),
    metadata_json: str = Form(default='{}'),
    user_id: int = Depends(get_current_user_id),
):
    source_file_name = str(markdown.filename or '')
    try:
        paths = json.loads(asset_paths_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail='INVALID_KIWIX_ASSET_PATHS_JSON') from exc
    if not isinstance(paths, list) or len(paths) != len(assets):
        raise HTTPException(status_code=422, detail='KIWIX_ASSET_PATH_COUNT_MISMATCH')
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail='INVALID_KIWIX_METADATA_JSON') from exc
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail='INVALID_KIWIX_METADATA_JSON')
    async with _KIWIX_UPLOAD_SEMAPHORE:
        markdown_bytes = await markdown.read()
        asset_payloads = {}
        for path, upload in zip(paths, assets):
            normalized = kiwix_preparsed_service.normalize_asset_path(str(path))
            if normalized in asset_payloads:
                raise HTTPException(status_code=409, detail='DUPLICATE_KIWIX_ASSET_PATH')
            asset_payloads[normalized] = await upload.read()
        return await kiwix_preparsed_service.ingest_markdown(
            upload_request_id=upload_request_id,
            parent_node_id=parent_node_id,
            source_file_name=source_file_name,
            markdown_bytes=markdown_bytes,
            assets=asset_payloads,
            user_id=user_id,
            kiwix_metadata=metadata,
        )


@router.post('/{object_id}/versions/init')
def initialize_version_upload(
    object_id: int,
    body: VersionUploadInitIn,
    user_id: int = Depends(get_current_user_id),
):
    return minio_upload_service.initialize_version_upload(
        upload_request_id=body.upload_request_id,
        object_id=object_id,
        file_name=body.file_name,
        file_size=body.file_size,
        content_type=body.content_type,
        user_id=user_id,
    )


@router.put("/{revision_id}/content")
async def upload_content(
    revision_id: int,
    request: Request,
    content_length: Optional[int] = Header(default=None, alias="Content-Length"),
    user_id: int = Depends(get_current_user_id),
):
    if content_length is None:
        raise HTTPException(status_code=411, detail="CONTENT_LENGTH_REQUIRED")
    if content_length < 0:
        raise HTTPException(status_code=400, detail="INVALID_CONTENT_LENGTH")
    try:
        result = await minio_upload_service.upload_content(
            revision_id=revision_id,
            user_id=user_id,
            content_length=content_length,
            chunks=request.stream(),
        )
        if not result.get("idempotent_replay"):
            _notify_upload(
                user_id=user_id,
                kind="upload",
                severity="success",
                title="문서 업로드 완료",
                body=(
                    f"object_id={result.get('object_id')}, "
                    f"revision_id={result.get('revision_id')}, "
                    f"size_bytes={result.get('size_bytes')}"
                ),
            )
        return result
    except Exception as exc:
        _notify_upload(
            user_id=user_id,
            kind="upload",
            severity="error",
            title="문서 업로드 실패",
            body=f"revision_id={revision_id}, reason={getattr(exc, 'detail', None) or str(exc)}",
        )
        raise


__all__ = ["router"]
