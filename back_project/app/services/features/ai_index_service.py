from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.config.settings import AI_INDEX_CALLBACK_TOKEN
from app.repositories.common import execute, fetch_one


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _job_result_status(value: Any) -> str:
    raw = _norm(value).lower()
    if raw in {"success", "succeeded", "indexed", "deleted", "done", "ok"}:
        return "success"
    if raw in {"failed", "fail", "error"}:
        return "failed"
    raise HTTPException(status_code=422, detail="status must be success/indexed/deleted/failed")


def _index_status(action: Any, result_status: str) -> str:
    if _norm(action).lower() == "delete":
        return "deleted"
    if result_status == "failed":
        return "failed"
    return "indexed"


def _find_job(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    job_id = _as_int(payload.get("job_id"))
    if job_id is not None:
        return fetch_one("SELECT * FROM ai_index_jobs WHERE job_id=%s", (job_id,))

    ai_document_id = _norm(payload.get("ai_document_id"))
    object_id = _as_int(payload.get("object_id"))
    action = _norm(payload.get("action")).lower()

    conds = []
    params: list[Any] = []
    if ai_document_id:
        conds.append("ai_document_id=%s")
        params.append(ai_document_id)
    if object_id is not None:
        conds.append("object_id=%s")
        params.append(object_id)
    if action:
        conds.append("action=%s")
        params.append(action)
    if not conds:
        return None

    return fetch_one(
        f"""
        SELECT *
          FROM ai_index_jobs
         WHERE {' AND '.join(conds)}
         ORDER BY
             CASE status
                 WHEN 'running' THEN 0
                 WHEN 'pending' THEN 1
                 WHEN 'sent' THEN 2
                 WHEN 'failed' THEN 3
                 ELSE 4
             END,
             job_id DESC
         LIMIT 1
        """,
        tuple(params),
    )


def apply_ai_index_result(payload: Dict[str, Any], callback_token: Optional[str] = None) -> Dict[str, Any]:
    if AI_INDEX_CALLBACK_TOKEN and callback_token != AI_INDEX_CALLBACK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid AI callback token")

    result_status = _job_result_status(payload.get("status") or payload.get("index_status"))
    job = _find_job(payload)
    if not job:
        raise HTTPException(status_code=404, detail="AI index job not found")

    job_id = int(job["job_id"])
    object_id = _as_int(payload.get("object_id")) or _as_int(job.get("object_id"))
    ai_document_id = _norm(payload.get("ai_document_id")) or _norm(job.get("ai_document_id"))
    action = _norm(payload.get("action")) or _norm(job.get("action"))
    message = _norm(
        payload.get("message")
        or payload.get("error")
        or payload.get("error_message")
        or payload.get("last_error")
    )
    next_index_status = _index_status(action, result_status)

    document = None
    if object_id is not None:
        document = fetch_one(
            "SELECT storage_backend,doc_status FROM storage_objects WHERE object_id=%s",
            (object_id,),
        )
    late_delete_callback = (
        _norm(action).lower() == "delete"
        and bool(document)
        and _norm(document.get("doc_status")).lower() == "active"
    )

    if result_status == "success":
        execute(
            """
            UPDATE ai_index_jobs
               SET status='completed',
                   finished_at=NOW(),
                   last_error=NULL,
                   updated_at=NOW()
             WHERE job_id=%s
            """,
            (job_id,),
        )
    else:
        execute(
            """
            UPDATE ai_index_jobs
               SET status='failed',
                   finished_at=NOW(),
                   last_error=%s,
                   updated_at=NOW()
             WHERE job_id=%s
            """,
            (message or "AI indexing failed", job_id),
        )

    if late_delete_callback:
        return {
            "ok": True,
            "job_id": job_id,
            "object_id": object_id,
            "ai_document_id": ai_document_id,
            "job_status": result_status,
            "index_status": "pending",
            "ignored": True,
            "reason": "late delete callback for restored document",
        }

    if _norm(action).lower() == "delete" and ai_document_id:
        execute(
            "UPDATE ai_document_info SET index_status=%s, updated_at=NOW() WHERE ai_document_id=%s",
            (next_index_status, ai_document_id),
        )
    elif object_id is not None:
        execute(
            "UPDATE ai_document_info SET index_status=%s, updated_at=NOW() WHERE object_id=%s",
            (next_index_status, object_id),
        )
    elif ai_document_id:
        execute(
            "UPDATE ai_document_info SET index_status=%s, updated_at=NOW() WHERE ai_document_id=%s",
            (next_index_status, ai_document_id),
        )

    purge_result = None
    if _norm(action).lower() == "delete" and object_id is not None:
        from app.services.features import minio_document_lifecycle_service

        if result_status == "success":
            purge_result = minio_document_lifecycle_service.finalize_minio_purge_for_object(
                object_id
            )
        else:
            minio_document_lifecycle_service.mark_minio_purge_index_failed(
                object_id,
                message or "Index deletion failed",
            )

    if _norm(action).lower() == "delete" and result_status == "success":
        from app.services.features.purged_document_cleanup_service import (
            cleanup_purged_document_history,
        )

        cleanup_purged_document_history(ai_document_id)

    result = {
        "ok": True,
        "job_id": job_id,
        "object_id": object_id,
        "ai_document_id": ai_document_id,
        "job_status": result_status,
        "index_status": next_index_status,
    }
    if purge_result is not None:
        result["purge"] = purge_result
    return result
