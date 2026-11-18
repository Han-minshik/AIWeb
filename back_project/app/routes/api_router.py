from __future__ import annotations

from fastapi import APIRouter

from app.controllers import (
    admin_controller,
    ai_index_controller,
    auth_controller,
    bookmarks_controller,
    chat_controller,
    docs_controller,
    docs_embed_controller,
    docs_tree_controller,
    kb_controller,
    kb_dashboard_controller,
    list_controller,
    minio_upload_controller,
    notifications_controller,
    pipeline_callback_controller,
    pipeline_jobs_controller,
    error_controller,
    qa_controller,
    uploads_controller,
)

api_router = APIRouter()

api_router.include_router(kb_controller.router)
api_router.include_router(ai_index_controller.router)
api_router.include_router(pipeline_callback_controller.router)
api_router.include_router(pipeline_jobs_controller.router)
api_router.include_router(docs_tree_controller.router)
api_router.include_router(minio_upload_controller.router)
api_router.include_router(docs_controller.router)
api_router.include_router(bookmarks_controller.router)
api_router.include_router(chat_controller.router)
api_router.include_router(admin_controller.router)
api_router.include_router(notifications_controller.router)
api_router.include_router(error_controller.router)
api_router.include_router(auth_controller.router)
api_router.include_router(docs_embed_controller.router)
api_router.include_router(qa_controller.router)
api_router.include_router(uploads_controller.router)
api_router.include_router(kb_dashboard_controller.router)
api_router.include_router(list_controller.router)
