from __future__ import annotations

from fastapi import APIRouter, HTTPException
from app.services.features import docs_service as service

router = APIRouter(prefix="/docs", tags=["docs"])


@router.post("/upload")
def upload_doc_legacy_disabled():
    raise HTTPException(status_code=410, detail='USE_MINIO_UPLOAD_API')


@router.post("/{doc_id}/upload-version")
def upload_doc_version_legacy_disabled(doc_id: int):
    raise HTTPException(status_code=410, detail='USE_MINIO_VERSION_UPLOAD_API')


router.get("")(service.list_docs)
router.get("/bookmarks/search")(service.search_bookmarked_docs)
router.post("/ai-document-ids")(service.resolve_ai_document_ids)
router.get("/{doc_id}/artifacts/{asset_path:path}")(service.stream_doc_artifact_asset)
router.get("/{doc_id}")(service.get_doc)
router.get("/{doc_id}/revisions")(service.list_doc_revisions)
router.delete("/{doc_id}/revisions/{revision_id}")(service.delete_doc_revision)
router.post("/{doc_id}/revisions/reorder")(service.reorder_doc_revisions)
router.post("/{doc_id}/delete")(service.delete_doc)
router.patch("/{doc_id}")(service.update_doc)
router.get("/{doc_id}/download")(service.download_doc)
router.post("/{doc_id}/copy-to-personal")(service.copy_doc_to_personal)
router.post("/{doc_id}/copy")(service.copy_doc)
router.post("/{doc_id}/move")(service.move_doc)
router.post("/create-note")(service.create_note)
router.post("/reorder")(service.reorder_tree_nodes)

__all__ = ["router"]
