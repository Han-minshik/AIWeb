from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Query

from app.deps.auth_dep import admin_required
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
def list_users(
    q: Optional[str] = Query(None, description="Search by name/email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _=Depends(admin_required),
):
    return admin_service.list_users(q=q, page=page, page_size=page_size)


@router.post("/users")
def add_user(payload: Dict[str, Any], _=Depends(admin_required)):
    return admin_service.create_user(payload)


@router.patch("/users/{user_id}")
def update_user(user_id: int, patch: Dict[str, Any], _=Depends(admin_required)):
    return admin_service.update_user(user_id=user_id, patch=patch)


@router.post("/users/{user_id}/toggle-admin")
def toggle_admin(user_id: int, _=Depends(admin_required)):
    return admin_service.toggle_admin(user_id=user_id)


@router.post("/users/{user_id}/reset_password")
def reset_password(user_id: int, password: str = Body(..., embed=True), _=Depends(admin_required)):
    return admin_service.reset_password(user_id=user_id, password=password)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, _=Depends(admin_required)):
    return admin_service.delete_user(user_id=user_id)


@router.get("/access-logs")
def access_logs(
    limit: int = Query(200, ge=1, le=1000),
    user_id: Optional[int] = Query(None, description="Filter by specific user"),
    _=Depends(admin_required),
):
    return admin_service.access_logs(limit=limit, user_id=user_id)

