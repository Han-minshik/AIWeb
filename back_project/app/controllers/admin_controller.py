from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.deps.auth_dep import admin_required, get_current_user_id
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
def list_users(
    q: Optional[str] = Query(None, description="Search by name/email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    group_id: Optional[int] = Query(None),
    group_empty: bool = Query(False),
    position_id: Optional[int] = Query(None),
    position_empty: bool = Query(False),
    is_admin: Optional[bool] = Query(None),
    _=Depends(admin_required),
):
    return admin_service.list_users(
        q=q,
        page=page,
        page_size=page_size,
        group_id=group_id,
        group_empty=group_empty,
        position_id=position_id,
        position_empty=position_empty,
        is_admin=is_admin,
    )


@router.post("/users")
def add_user(payload: Dict[str, Any], _=Depends(admin_required)):
    return admin_service.create_user(payload)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    patch: Dict[str, Any],
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.update_user(
        user_id=user_id,
        patch=patch,
        actor_user_id=actor_user_id,
    )


@router.post("/users/{user_id}/toggle-admin")
def toggle_admin(
    user_id: int,
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.toggle_admin(user_id=user_id, actor_user_id=actor_user_id)


@router.post("/users/{user_id}/reset_password")
def reset_password(user_id: int, password: str = Body(..., embed=True), _=Depends(admin_required)):
    return admin_service.reset_password(user_id=user_id, password=password)


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.delete_user(user_id=user_id, actor_user_id=actor_user_id)


@router.delete("/users/{user_id}/permanent")
def permanently_delete_user(
    user_id: int,
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.permanently_delete_user(
        user_id=user_id,
        actor_user_id=actor_user_id,
    )


@router.get("/access-logs")
def access_logs(
    limit: int = Query(200, ge=1, le=1000),
    user_id: Optional[int] = Query(None, description="Filter by specific user"),
    _=Depends(admin_required),
):
    return admin_service.access_logs(limit=limit, user_id=user_id)


@router.get("/departments")
def list_departments(
    _=Depends(admin_required),
):
    return admin_service.list_departments()


@router.post("/departments")
def create_department(
    name: str = Body(..., embed=True),
    _=Depends(admin_required),
):
    return admin_service.create_department(name=name)


@router.delete("/departments/{group_id}")
def delete_department(
    group_id: int,
    _=Depends(admin_required),
):
    return admin_service.delete_department(group_id=group_id)


@router.get("/groups")
def list_groups(
    q: Optional[str] = Query(None),
    include_inactive: bool = Query(True),
    _=Depends(admin_required),
):
    return admin_service.list_groups(q=q, include_inactive=include_inactive)


@router.post("/groups")
def create_group(
    payload: Dict[str, Any],
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.create_group(payload, actor_user_id=actor_user_id)


@router.patch("/groups/{group_id}")
def update_group(
    group_id: int,
    patch: Dict[str, Any],
    _=Depends(admin_required),
):
    return admin_service.update_group(group_id=group_id, patch=patch)


@router.delete("/groups/{group_id}")
def deactivate_group(
    group_id: int,
    _=Depends(admin_required),
):
    return admin_service.deactivate_group(group_id=group_id)


@router.get("/groups/{group_id}/members")
def list_group_members(
    group_id: int,
    include_inactive: bool = Query(True),
    _=Depends(admin_required),
):
    return admin_service.list_group_members(group_id=group_id, include_inactive=include_inactive)


@router.get("/groups/{group_id}/usage")
def get_group_usage(
    group_id: int,
    _=Depends(admin_required),
):
    return admin_service.get_group_usage(group_id=group_id)


@router.post("/groups/{group_id}/members")
def add_group_member(
    group_id: int,
    payload: Dict[str, Any],
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.add_group_member(group_id=group_id, payload=payload, actor_user_id=actor_user_id)


@router.patch("/groups/{group_id}/members/{user_id}")
def update_group_member(
    group_id: int,
    user_id: int,
    patch: Dict[str, Any],
    _=Depends(admin_required),
):
    return admin_service.update_group_member(group_id=group_id, user_id=user_id, patch=patch)


@router.delete("/groups/{group_id}/members/{user_id}")
def deactivate_group_member(
    group_id: int,
    user_id: int,
    _=Depends(admin_required),
):
    return admin_service.deactivate_group_member(group_id=group_id, user_id=user_id)


@router.get("/deleted-documents")
def list_deleted_documents(
    status: str = Query("pending"),
    _=Depends(admin_required),
):
    return admin_service.list_deleted_documents(status=status)


@router.get("/deleted-documents/history")
def list_deleted_document_history(
    _=Depends(admin_required),
):
    return admin_service.list_deleted_document_history()


@router.post("/deleted-documents/{deletion_id}/restore")
def restore_deleted_document(
    deletion_id: int,
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.restore_deleted_document(deletion_id=deletion_id, actor_user_id=actor_user_id)


@router.post("/deleted-documents/{deletion_id}/purge")
def purge_deleted_document(
    deletion_id: int,
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.purge_deleted_document(deletion_id=deletion_id, actor_user_id=actor_user_id)


@router.get("/ai-index/jobs")
def list_ai_index_jobs(
    status: str = Query("failed"),
    _=Depends(admin_required),
):
    return admin_service.list_ai_index_jobs(status=status)


@router.post("/ai-index/jobs/retry")
def retry_ai_index_jobs(
    job_ids: list[int] = Body(..., embed=True),
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    return admin_service.retry_ai_index_jobs(job_ids=job_ids, actor_user_id=actor_user_id)


@router.post("/ai-index/jobs/dispatch")
def dispatch_ai_index_jobs(
    limit: int = Body(20, embed=True),
    actor_user_id: int = Depends(get_current_user_id),
    _=Depends(admin_required),
):
    raise HTTPException(
        status_code=410,
        detail="Direct dispatch was removed; retry the job and let the Pipeline Dispatcher process it",
    )


@router.get("/positions")
def list_positions(
    _=Depends(admin_required),
):
    return admin_service.list_positions()


@router.post("/positions")
def create_position(
    name: str = Body(..., embed=True),
    _=Depends(admin_required),
):
    return admin_service.create_position(name=name)


@router.delete("/positions/{position_id}")
def delete_position(
    position_id: int,
    _=Depends(admin_required),
):
    return admin_service.delete_position(position_id=position_id)
