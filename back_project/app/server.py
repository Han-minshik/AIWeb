from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import (
    APP_NAME,
    APP_VERSION,
    CORS_ORIGIN_REGEX,
    CORS_ORIGINS,
    UPLOAD_DIR,
    UPLOAD_DIR_GENERAL,
    UPLOAD_DIR_KIWIX,
)
from app.config.logging import setup_logging
from app.middleware.exception_handlers import register_exception_handlers
from app.middleware.request_context import request_context_middleware
from app.routes import api_router
from app.utils.db import close_pool, open_pool


@asynccontextmanager
async def lifespan(_: FastAPI):
    open_pool(wait=True)
    try:
        yield
    finally:
        close_pool()


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    app.middleware("http")(request_context_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS or ["http://localhost:9000"],
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
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
