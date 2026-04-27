from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import (
    APP_NAME,
    APP_VERSION,
    CORS_ORIGINS,
    UPLOAD_DIR,
    UPLOAD_DIR_GENERAL,
    UPLOAD_DIR_KIWIX,
)
from app.config.logging import setup_logging
from app.middleware.exception_handlers import register_exception_handlers
from app.middleware.request_context import request_context_middleware
from app.routes import api_router


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title=APP_NAME, version=APP_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS or ["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(request_context_middleware)
    register_exception_handlers(app)

    app.include_router(api_router)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR_GENERAL, exist_ok=True)
    os.makedirs(UPLOAD_DIR_KIWIX, exist_ok=True)

    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
    app.mount("/uploads-general", StaticFiles(directory=UPLOAD_DIR_GENERAL), name="uploads_general")
    app.mount("/uploads-kiwix", StaticFiles(directory=UPLOAD_DIR_KIWIX), name="uploads_kiwix")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
