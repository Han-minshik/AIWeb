from __future__ import annotations

from fastapi import APIRouter

from app.controllers import (
    admin_controller,
    auth_controller,
    bookmarks_controller,
    chat_controller,
    docs_controller,
    docs_embed_controller,
    docs_tree_controller,
    kb_controller,
    kb_dashboard_controller,
    kb_sync_controller,
    notifications_controller,
    error_controller,
    qa_controller,
    rag_controller,
    uploads_controller,
)

api_router = APIRouter()

api_router.include_router(kb_controller.router)
api_router.include_router(docs_tree_controller.router)
api_router.include_router(docs_controller.router)
api_router.include_router(bookmarks_controller.router)
api_router.include_router(chat_controller.router)
api_router.include_router(admin_controller.router)
api_router.include_router(rag_controller.router)
api_router.include_router(notifications_controller.router)
api_router.include_router(error_controller.router)
api_router.include_router(kb_sync_controller.router)
api_router.include_router(auth_controller.router)
api_router.include_router(docs_embed_controller.router)
api_router.include_router(qa_controller.router)
api_router.include_router(uploads_controller.router)
api_router.include_router(kb_dashboard_controller.router)
