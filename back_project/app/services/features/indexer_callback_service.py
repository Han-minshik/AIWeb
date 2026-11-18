from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

import psycopg
from fastapi import HTTPException

from app.repositories.common import fetch_one
from app.repositories.transaction import db_transaction
from app.services.common.pipeline_jwt_service import verify_callback_token
from app.utils.db import get_connection


SUCCESS_STATUSES = {"success", "succeeded", "completed", "indexed", "deleted", "done", "ok"}
FAILED_STATUSES = {"failed", "fail", "error"}
TERMINAL_EVENT_RESULTS = {"applied", "ignored", "rejected"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any, label: str, *, required: bool = True) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise HTTPException(status_code=422, detail=f"{label} is required")
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{label} must be an integer") from exc


def _result_status(value: Any) -> str:
    normalized = _norm(value).lower()
    if normalized in SUCCESS_STATUSES:
        return "success"
    if normalized in FAILED_STATUSES:
        return "failed"
    raise HTTPException(status_code=422, detail="status must be success or failed")


def _payload_hash(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_indexer_token(authorization: Optional[str]) -> Dict[str, Any]:
    value = _norm(authorization)
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Indexer callback Bearer token is required")
    try:
        return verify_callback_token(token.strip(), "indexer")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid indexer callback token") from exc


def _reserve_event(event_id: str, payload_hash: str) -> Dict[str, Any]:
    connection = get_connection(autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT callback_event_id,payload_hash,processing_result,result_reason
                  FROM ai_callback_events
                 WHERE source_service='indexer' AND event_id=%s
                 FOR UPDATE
                """,
                (event_id,),
            )
            existing = cursor.fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    connection.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail="Callback event_id was already used with a different payload",
                    )
                if existing["processing_result"] in TERMINAL_EVENT_RESULTS:
                    connection.commit()
                    return {**existing, "duplicate": True}
                cursor.execute(
                    """
                    UPDATE ai_callback_events
                       SET processing_result='received',result_reason=NULL,
                           processed_at=NULL,updated_at=NOW()
                     WHERE callback_event_id=%s
                    """,
                    (int(existing["callback_event_id"]),),
                )
                connection.commit()
                return {**existing, "processing_result": "received", "duplicate": False}
            try:
                cursor.execute(
                    """
                    INSERT INTO ai_callback_events(
                        source_service,event_id,job_id,payload_hash,processing_result,
                        received_at,created_at,updated_at
                    ) VALUES('indexer',%s,NULL,%s,'received',NOW(),NOW(),NOW())
                    RETURNING callback_event_id
                    """,
                    (event_id, payload_hash),
                )
                callback_event_id = int(cursor.fetchone()["callback_event_id"])
            except psycopg.IntegrityError:
                connection.rollback()
                return _reserve_event(event_id, payload_hash)
        connection.commit()
        return {
            "callback_event_id": callback_event_id,
            "processing_result": "received",
            "result_reason": None,
            "duplicate": False,
        }
    except Exception:
        if not connection.closed:
            connection.rollback()
        raise
    finally:
        connection.close()


def _update_event(callback_event_id: int, result: str, reason: str = "") -> None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_callback_events
                   SET processing_result=%s,result_reason=%s,processed_at=NOW(),updated_at=NOW()
                 WHERE callback_event_id=%s
                """,
                (result, str(reason or "")[:255] or None, int(callback_event_id)),
            )
    finally:
        connection.close()


def _link_event_job(callback_event_id: int, job_id: int) -> None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE ai_callback_events SET job_id=%s,updated_at=NOW() WHERE callback_event_id=%s",
                (int(job_id), int(callback_event_id)),
            )
    finally:
        connection.close()


def _find_job(job_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT
            j.*,
            ai.source_revision_id AS current_revision_id,
            ai.indexed_revision_id,
            ai.index_status,
            ai.doc_name,
            ai.user_id,
            so.doc_status,
            so.uploader_user_id,
            dd.deletion_id,
            dd.status AS deletion_status
          FROM ai_index_jobs j
          JOIN ai_document_info ai ON ai.ai_document_id=j.ai_document_id
          LEFT JOIN storage_objects so ON so.object_id=COALESCE(j.object_id,ai.object_id)
          LEFT JOIN document_deletions dd ON dd.deletion_id=(
              SELECT candidate.deletion_id
                FROM document_deletions candidate
               WHERE candidate.ai_document_id=j.ai_document_id
               ORDER BY candidate.deletion_id DESC
               LIMIT 1
          )
         WHERE j.job_id=%s
        """,
        (int(job_id),),
    )


def _validate_job_identity(job: Dict[str, Any], payload: Dict[str, Any]) -> None:
    stage = _norm(job.get("stage")).lower()
    if stage not in {"index", "delete"}:
        raise HTTPException(status_code=409, detail="Callback job is not an indexer job")
    callback_object_id = _as_int(payload.get("object_id"), "object_id")
    if callback_object_id != _as_int(job.get("object_id"), "job.object_id"):
        raise HTTPException(status_code=409, detail="Callback object_id does not match the job")
    callback_ai_document_id = _norm(payload.get("ai_document_id"))
    if callback_ai_document_id and callback_ai_document_id != _norm(job.get("ai_document_id")):
        raise HTTPException(status_code=409, detail="Callback ai_document_id does not match the job")
    if stage == "index":
        callback_revision_id = _as_int(payload.get("revision_id"), "revision_id")
        if callback_revision_id != _as_int(job.get("revision_id"), "job.revision_id"):
            raise HTTPException(status_code=409, detail="Callback revision_id does not match the job")
        callback_artifact_id = _as_int(payload.get("artifact_id"), "artifact_id")
        if callback_artifact_id != _as_int(job.get("artifact_id"), "job.artifact_id"):
            raise HTTPException(status_code=409, detail="Callback artifact_id does not match the job")
    callback_action = _norm(payload.get("action")).lower()
    if callback_action and callback_action != _norm(job.get("action")).lower():
        raise HTTPException(status_code=409, detail="Callback action does not match the job")


def _is_current_active_revision(job: Dict[str, Any]) -> bool:
    return (
        _norm(job.get("doc_status")).lower() == "active"
        and _as_int(job.get("current_revision_id"), "current_revision_id", required=False)
        == _as_int(job.get("revision_id"), "revision_id", required=False)
    )


def _late_delete(job: Dict[str, Any]) -> bool:
    return (
        _norm(job.get("stage")).lower() == "delete"
        and (
            _norm(job.get("doc_status")).lower() == "active"
            or _norm(job.get("deletion_status")).lower() == "restored"
        )
    )


def _message(payload: Dict[str, Any], fallback: str) -> str:
    return _norm(
        payload.get("message")
        or payload.get("error")
        or payload.get("error_message")
        or payload.get("error_code")
    ) or fallback


def _apply_failure(
    event_db_id: int,
    job: Dict[str, Any],
    message: str,
) -> Dict[str, Any]:
    stage = _norm(job.get("stage")).lower()
    current = False
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM ai_index_jobs WHERE job_id=%s FOR UPDATE",
                (int(job["job_id"]),),
            )
            locked = cursor.fetchone()
            if locked and _norm(locked.get("status")).lower() == "completed":
                cursor.execute(
                    """
                    UPDATE ai_callback_events
                       SET processing_result='ignored',result_reason='Indexer job was already completed',
                           processed_at=NOW(),updated_at=NOW()
                     WHERE callback_event_id=%s
                    """,
                    (event_db_id,),
                )
                return {
                    "ok": True,
                    "job_id": int(job["job_id"]),
                    "job_status": "completed",
                    "ignored": True,
                }
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status='failed',finished_at=NOW(),last_error=%s,updated_at=NOW()
                 WHERE job_id=%s AND stage IN ('index','delete')
                """,
                (message[:4000], int(job["job_id"])),
            )
            if stage == "index":
                cursor.execute(
                    """
                    UPDATE ai_document_info ai
                       SET index_status='failed',updated_at=NOW()
                      FROM storage_objects so
                     WHERE so.object_id=ai.object_id
                       AND ai.ai_document_id=%s
                       AND ai.source_revision_id=%s
                       AND so.doc_status='active'
                    """,
                    (job["ai_document_id"], int(job["revision_id"])),
                )
                current = cursor.rowcount == 1
            cursor.execute(
                """
                UPDATE ai_callback_events
                   SET processing_result='applied',result_reason=%s,
                       processed_at=NOW(),updated_at=NOW()
                 WHERE callback_event_id=%s
                """,
                (message[:255], event_db_id),
            )
    if stage == "delete" and job.get("object_id") is not None:
        from app.services.features import minio_document_lifecycle_service

        minio_document_lifecycle_service.mark_minio_purge_index_failed(
            int(job["object_id"]),
            message,
        )
    return {
        "ok": True,
        "job_id": int(job["job_id"]),
        "job_status": "failed",
        "index_status": "failed" if current else job.get("index_status"),
    }


def _apply_success(
    event_db_id: int,
    job: Dict[str, Any],
    external_task_id: str,
) -> Dict[str, Any]:
    stage = _norm(job.get("stage")).lower()
    current = False
    late_delete = False
    event_result = "applied"
    event_reason: Optional[str] = None

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM ai_index_jobs WHERE job_id=%s FOR UPDATE",
                (int(job["job_id"]),),
            )
            locked = cursor.fetchone()
            cursor.execute(
                """
                SELECT ai.source_revision_id AS current_revision_id,
                       ai.indexed_revision_id,ai.index_status,so.doc_status,
                       dd.status AS deletion_status
                  FROM ai_document_info ai
                  LEFT JOIN storage_objects so ON so.object_id=ai.object_id
                  LEFT JOIN document_deletions dd
                    ON dd.deletion_id=(
                        SELECT MAX(dd2.deletion_id)
                          FROM document_deletions dd2
                         WHERE dd2.object_id=ai.object_id
                    )
                 WHERE ai.ai_document_id=%s
                 FOR UPDATE OF ai
                """,
                (job["ai_document_id"],),
            )
            current_state = cursor.fetchone() or job
            live_job = {**job, **current_state}
            current = stage == "index" and _is_current_active_revision(live_job)
            late_delete = stage == "delete" and _late_delete(live_job)
            if stage == "index" and not current:
                event_result = "ignored"
                event_reason = "Indexed revision is no longer current and active"
            elif late_delete:
                event_result = "ignored"
                event_reason = "Delete callback arrived after document restoration"
            already_completed = bool(
                locked and _norm(locked.get("status")).lower() == "completed"
            )
            if not already_completed:
                cursor.execute(
                    """
                    UPDATE ai_index_jobs
                       SET status='completed',external_task_id=COALESCE(%s,external_task_id),
                           finished_at=NOW(),last_error=NULL,updated_at=NOW()
                     WHERE job_id=%s AND stage IN ('index','delete')
                    """,
                    (external_task_id or None, int(job["job_id"])),
                )
            if not already_completed and current:
                cursor.execute(
                    """
                    UPDATE ai_document_info
                       SET index_status='indexed',indexed_revision_id=%s,updated_at=NOW()
                     WHERE ai_document_id=%s AND source_revision_id=%s
                    """,
                    (
                        int(job["revision_id"]),
                        job["ai_document_id"],
                        int(job["revision_id"]),
                    ),
                )
            elif not already_completed and stage == "delete" and not late_delete:
                cursor.execute(
                    """
                    UPDATE ai_document_info
                       SET index_status='deleted',updated_at=NOW()
                     WHERE ai_document_id=%s
                    """,
                    (job["ai_document_id"],),
                )
            if already_completed and event_result == "applied":
                event_result = "ignored"
                event_reason = "Indexer job was already completed"
            cursor.execute(
                """
                UPDATE ai_callback_events
                   SET processing_result=%s,result_reason=%s,
                       processed_at=NOW(),updated_at=NOW()
                 WHERE callback_event_id=%s
                """,
                (event_result, event_reason, event_db_id),
            )

    purge_result = None
    if (
        stage == "delete"
        and not late_delete
        and event_result == "applied"
        and job.get("object_id") is not None
    ):
        from app.services.features import minio_document_lifecycle_service

        purge_result = minio_document_lifecycle_service.finalize_minio_purge_for_object(
            int(job["object_id"])
        )
        from app.services.features.purged_document_cleanup_service import (
            cleanup_purged_document_history,
        )

        cleanup_purged_document_history(str(job.get("ai_document_id") or ""))
    result: Dict[str, Any] = {
        "ok": True,
        "job_id": int(job["job_id"]),
        "job_status": "completed",
        "index_status": (
            "indexed"
            if current
            else "deleted"
            if stage == "delete" and not late_delete
            else job.get("index_status")
        ),
        "indexed_revision_id": int(job["revision_id"]) if current else job.get("indexed_revision_id"),
        "ignored": event_result == "ignored",
    }
    if event_reason:
        result["reason"] = event_reason
    if purge_result is not None:
        result["purge"] = purge_result
    return result


def apply_indexer_callback(
    payload: Dict[str, Any],
    *,
    authorization: Optional[str],
) -> Dict[str, Any]:
    _verify_indexer_token(authorization)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Callback payload must be an object")
    event_id = _norm(payload.get("event_id"))
    if not event_id or len(event_id) > 128:
        raise HTTPException(status_code=422, detail="event_id is required and must be at most 128 characters")
    try:
        event_id.encode("ascii")
    except UnicodeEncodeError as exc:
        raise HTTPException(status_code=422, detail="event_id must contain ASCII characters only") from exc
    job_id = _as_int(payload.get("job_id"), "job_id")
    event = _reserve_event(event_id, _payload_hash(payload))
    event_db_id = int(event["callback_event_id"])
    if event.get("duplicate"):
        return {
            "ok": True,
            "duplicate": True,
            "job_id": int(job_id),
            "event_result": event.get("processing_result"),
            "event_reason": event.get("result_reason"),
        }
    try:
        result_status = _result_status(payload.get("status"))
        job = _find_job(int(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Indexer job not found")
        _link_event_job(event_db_id, int(job_id))
        _validate_job_identity(job, payload)
        if _norm(job.get("status")).lower() in {"cancelled", "superseded"}:
            reason = f"Indexer job is {job['status']}"
            _update_event(event_db_id, "ignored", reason)
            return {"ok": True, "ignored": True, "job_id": int(job_id), "reason": reason}
        if result_status == "failed":
            failure_message = _message(payload, "Indexer failed")
            result = _apply_failure(event_db_id, job, failure_message)
            from app.services.features.pipeline_notification_service import notify_pipeline_result

            notify_pipeline_result(job, stage=str(job.get("stage")), success=False, message=failure_message)
            return result
        result = _apply_success(
            event_db_id,
            job,
            _norm(payload.get("external_task_id") or payload.get("task_id")),
        )
        if not result.get("ignored"):
            from app.services.features.pipeline_notification_service import notify_pipeline_result

            notify_pipeline_result(job, stage=str(job.get("stage")), success=True)
        return result
    except HTTPException as exc:
        result = "failed" if exc.status_code >= 500 else "rejected"
        _update_event(event_db_id, result, _norm(exc.detail))
        raise
    except Exception as exc:
        _update_event(event_db_id, "failed", str(exc))
        raise


__all__ = ["apply_indexer_callback"]
