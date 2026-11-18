from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Optional

import psycopg
from fastapi import HTTPException

from app.repositories.common import fetch_one
from app.repositories.transaction import db_transaction
from app.services.common.minio_key_builder import KbStorageScope, artifact_prefix
from app.services.common.minio_storage_service import (
    MinioStorageError,
    get_minio_storage_service,
)
from app.services.common.pipeline_jwt_service import verify_callback_token
from app.utils.db import get_connection


SUCCESS_STATUSES = {"success", "succeeded", "completed", "done", "ok"}
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


def _extract_bearer_token(authorization: Optional[str]) -> str:
    value = _norm(authorization)
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Parser callback Bearer token is required")
    return token.strip()


def _verify_parser_token(authorization: Optional[str]) -> Dict[str, Any]:
    token = _extract_bearer_token(authorization)
    try:
        return verify_callback_token(token, "parser")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid parser callback token") from exc


def _reserve_event(event_id: str, job_id: int, payload_hash: str) -> Dict[str, Any]:
    connection = get_connection(autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT callback_event_id,payload_hash,processing_result,result_reason
                  FROM ai_callback_events
                 WHERE source_service='parser' AND event_id=%s
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
                    ) VALUES('parser',%s,%s,%s,'received',NOW(),NOW(),NOW())
                    RETURNING callback_event_id
                    """,
                    (event_id, None, payload_hash),
                )
                callback_event_id = int(cursor.fetchone()["callback_event_id"])
            except psycopg.IntegrityError:
                connection.rollback()
                return _reserve_event(event_id, job_id, payload_hash)
        connection.commit()
        return {
            "callback_event_id": callback_event_id,
            "payload_hash": payload_hash,
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


def _find_parse_job(job_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT
            j.*,
            ai.object_id AS current_object_id,
            ai.source_revision_id AS current_revision_id,
            ai.index_status,
            ai.doc_name,
            ai.user_id,
            so.doc_status,
            so.upload_mode,
            so.uploader_user_id,
            tn.kb_type,
            tn.kb_owner_user_id
          FROM ai_index_jobs j
          JOIN ai_document_info ai ON ai.ai_document_id=j.ai_document_id
          LEFT JOIN storage_objects so ON so.object_id=COALESCE(j.object_id,ai.object_id)
          LEFT JOIN tree_nodes tn ON tn.node_id=(
              SELECT candidate.node_id
                FROM tree_nodes candidate
               WHERE candidate.doc_id=COALESCE(j.object_id,ai.object_id)
                 AND candidate.is_folder=FALSE
               ORDER BY candidate.node_id
               LIMIT 1
          )
         WHERE j.job_id=%s
        """,
        (int(job_id),),
    )


def _validate_job_identity(job: Dict[str, Any], payload: Dict[str, Any]) -> None:
    if _norm(job.get("stage")).lower() != "parse":
        raise HTTPException(status_code=409, detail="Callback job is not a parse job")
    for field in ("object_id", "revision_id"):
        callback_value = _as_int(payload.get(field), field)
        job_value = _as_int(job.get(field), f"job.{field}")
        if callback_value != job_value:
            raise HTTPException(status_code=409, detail=f"Callback {field} does not match the job")
    callback_ai_document_id = _norm(payload.get("ai_document_id"))
    if callback_ai_document_id and callback_ai_document_id != _norm(job.get("ai_document_id")):
        raise HTTPException(status_code=409, detail="Callback ai_document_id does not match the job")


def _expected_artifact_prefix(job: Dict[str, Any]):
    personal = (
        _norm(job.get("upload_mode")).lower() == "personal"
        or _norm(job.get("kb_type")).lower() in {"personal", "person"}
    )
    scope = KbStorageScope.PERSONAL if personal else KbStorageScope.SE_KB
    owner_user_id = job.get("uploader_user_id") or job.get("kb_owner_user_id")
    return artifact_prefix(
        scope,
        int(job["object_id"]),
        int(job["revision_id"]),
        user_id=owner_user_id if personal else None,
    )


def _artifact_rows(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise HTTPException(status_code=422, detail="Successful parser callback requires artifacts")
    if len(artifacts) > 100:
        raise HTTPException(status_code=422, detail="Parser callback accepts at most 100 artifact records")
    normalized: list[Dict[str, Any]] = []
    primary_count = 0
    for index, raw in enumerate(artifacts):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail=f"artifacts[{index}] must be an object")
        artifact_type = _norm(raw.get("artifact_type"))
        bucket_name = _norm(raw.get("bucket_name"))
        object_key = _norm(raw.get("object_key"))
        version_id = _norm(raw.get("minio_version_id") or raw.get("version_id"))
        if not artifact_type or len(artifact_type) > 32:
            raise HTTPException(status_code=422, detail=f"artifacts[{index}].artifact_type is invalid")
        if not bucket_name or not object_key or not version_id:
            raise HTTPException(
                status_code=422,
                detail=f"artifacts[{index}] requires bucket_name, object_key and minio_version_id",
            )
        checksum = _norm(raw.get("checksum_sha256")).lower()
        if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
            raise HTTPException(status_code=422, detail=f"artifacts[{index}].checksum_sha256 is invalid")
        size_bytes = _as_int(raw.get("size_bytes"), f"artifacts[{index}].size_bytes")
        if size_bytes is None or size_bytes < 0:
            raise HTTPException(status_code=422, detail=f"artifacts[{index}].size_bytes is invalid")
        primary = bool(raw.get("primary"))
        primary_count += int(primary)
        normalized.append(
            {
                "artifact_type": artifact_type,
                "bucket_name": bucket_name,
                "object_key": object_key,
                "minio_version_id": version_id,
                "size_bytes": size_bytes,
                "checksum_sha256": checksum,
                "primary": primary,
            }
        )
    if len(normalized) == 1 and primary_count == 0:
        normalized[0]["primary"] = True
        primary_count = 1
    if primary_count != 1:
        raise HTTPException(status_code=422, detail="Exactly one parser artifact must be primary")
    primary = next(artifact for artifact in normalized if artifact["primary"])
    if primary["artifact_type"] != "manifest":
        raise HTTPException(status_code=422, detail="The primary parser artifact must be a manifest")
    return normalized


def _metadata_checksum(metadata: Dict[str, Any]) -> str:
    lowered = {str(key).lower(): _norm(value).lower() for key, value in metadata.items()}
    return (
        lowered.get("x-amz-meta-sha256")
        or lowered.get("x-amz-meta-checksum-sha256")
        or lowered.get("sha256")
        or ""
    )


def _verify_artifacts(job: Dict[str, Any], artifacts: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    expected = _expected_artifact_prefix(job)
    storage = get_minio_storage_service()
    verified: list[Dict[str, Any]] = []
    for artifact in artifacts:
        if artifact["bucket_name"] != expected.bucket_name:
            raise HTTPException(status_code=422, detail="Artifact bucket does not match the assigned target")
        if not artifact["object_key"].startswith(expected.object_key):
            raise HTTPException(status_code=422, detail="Artifact object key is outside the assigned revision prefix")
        if artifact["object_key"] == expected.object_key:
            raise HTTPException(status_code=422, detail="Artifact object key must identify a file")
        try:
            stat = storage.stat_object(
                artifact["bucket_name"],
                artifact["object_key"],
                version_id=artifact["minio_version_id"],
            )
        except MinioStorageError as exc:
            raise HTTPException(status_code=503, detail="Unable to verify parser artifact in MinIO") from exc
        if stat.version_id and stat.version_id != artifact["minio_version_id"]:
            raise HTTPException(status_code=409, detail="Artifact MinIO version does not match")
        if artifact["size_bytes"] is not None and int(artifact["size_bytes"]) != stat.size_bytes:
            raise HTTPException(status_code=409, detail="Artifact size does not match MinIO")
        stored_checksum = _metadata_checksum(dict(stat.metadata))
        if artifact["checksum_sha256"] and stored_checksum and artifact["checksum_sha256"] != stored_checksum:
            raise HTTPException(status_code=409, detail="Artifact checksum does not match MinIO metadata")
        verified.append(
            {
                **artifact,
                "size_bytes": stat.size_bytes,
            }
        )
    return verified


def _is_current_active_revision(job: Dict[str, Any]) -> bool:
    return (
        _norm(job.get("doc_status")).lower() == "active"
        and _as_int(job.get("current_revision_id"), "current_revision_id", required=False)
        == _as_int(job.get("revision_id"), "revision_id")
    )


def _apply_failed_result(
    event_db_id: int,
    job: Dict[str, Any],
    message: str,
) -> Dict[str, Any]:
    current = _is_current_active_revision(job)
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM ai_index_jobs WHERE job_id=%s FOR UPDATE",
                (int(job["job_id"]),),
            )
            locked_job = cursor.fetchone()
            if locked_job and _norm(locked_job.get("status")).lower() == "completed":
                cursor.execute(
                    """
                    UPDATE ai_callback_events
                       SET processing_result='ignored',result_reason='Parse job was already completed',
                           processed_at=NOW(),updated_at=NOW()
                     WHERE callback_event_id=%s
                    """,
                    (event_db_id,),
                )
                return {
                    "ok": True,
                    "job_id": int(job["job_id"]),
                    "job_status": "completed",
                    "index_job_id": None,
                    "ignored": True,
                }
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status='failed',finished_at=NOW(),last_error=%s,updated_at=NOW()
                 WHERE job_id=%s AND stage='parse'
                """,
                (message[:4000] or "Parser failed", int(job["job_id"])),
            )
            if current:
                cursor.execute(
                    """
                    UPDATE ai_document_info
                       SET index_status='failed',updated_at=NOW()
                     WHERE ai_document_id=%s
                    """,
                    (job["ai_document_id"],),
                )
            cursor.execute(
                """
                UPDATE ai_callback_events
                   SET processing_result='applied',result_reason=%s,
                       processed_at=NOW(),updated_at=NOW()
                 WHERE callback_event_id=%s
                """,
                ((message or "Parser failed")[:255], event_db_id),
            )
    return {
        "ok": True,
        "job_id": int(job["job_id"]),
        "job_status": "failed",
        "index_job_id": None,
    }


def _insert_artifact(cursor, job: Dict[str, Any], artifact: Dict[str, Any], parser_version: str) -> int:
    cursor.execute(
        """
        INSERT INTO ai_parse_artifacts(
            revision_id,producer_job_id,artifact_type,bucket_name,object_key,
            minio_version_id,artifact_status,checksum_sha256,size_bytes,
            parser_version,verified_at,created_at,updated_at
        ) VALUES(%s,%s,%s,%s,%s,%s,'READY',%s,%s,%s,NOW(),NOW(),NOW())
        ON CONFLICT (revision_id, artifact_type, minio_version_id) DO UPDATE SET
            producer_job_id=EXCLUDED.producer_job_id,
            bucket_name=EXCLUDED.bucket_name,object_key=EXCLUDED.object_key,
            artifact_status='READY',checksum_sha256=EXCLUDED.checksum_sha256,
            size_bytes=EXCLUDED.size_bytes,parser_version=EXCLUDED.parser_version,
            verified_at=NOW(),updated_at=NOW()
        RETURNING artifact_id
        """,
        (
            int(job["revision_id"]),
            int(job["job_id"]),
            artifact["artifact_type"],
            artifact["bucket_name"],
            artifact["object_key"],
            artifact["minio_version_id"],
            artifact.get("checksum_sha256"),
            artifact.get("size_bytes"),
            parser_version or None,
        ),
    )
    return int(cursor.fetchone()["artifact_id"])


def _apply_success_result(
    event_db_id: int,
    job: Dict[str, Any],
    artifacts: list[Dict[str, Any]],
    parser_version: str,
    external_task_id: str,
) -> Dict[str, Any]:
    current = _is_current_active_revision(job)
    primary_artifact_id: Optional[int] = None
    artifact_ids: list[int] = []
    index_job_id: Optional[int] = None
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM ai_index_jobs WHERE job_id=%s FOR UPDATE",
                (int(job["job_id"]),),
            )
            cursor.fetchone()
            for artifact in artifacts:
                artifact_id = _insert_artifact(cursor, job, artifact, parser_version)
                artifact_ids.append(artifact_id)
                if artifact["primary"]:
                    primary_artifact_id = artifact_id
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status='completed',external_task_id=COALESCE(%s,external_task_id),
                       finished_at=NOW(),last_error=NULL,updated_at=NOW()
                 WHERE job_id=%s AND stage='parse'
                """,
                (external_task_id or None, int(job["job_id"])),
            )
            if current and primary_artifact_id is not None:
                cursor.execute(
                    """
                    SELECT job_id
                      FROM ai_index_jobs
                     WHERE revision_id=%s AND stage='index'
                       AND status IN ('pending','running','sent','completed','failed')
                     ORDER BY job_id DESC LIMIT 1
                     FOR UPDATE
                    """,
                    (int(job["revision_id"]),),
                )
                existing_index_job = cursor.fetchone()
                if existing_index_job:
                    index_job_id = int(existing_index_job["job_id"])
                else:
                    action = _norm(job.get("action")).lower()
                    if action not in {"add", "update", "reindex"}:
                        action = "update"
                    cursor.execute(
                        """
                        INSERT INTO ai_index_jobs(
                            ai_document_id,object_id,revision_id,artifact_id,action,stage,
                            status,retry_count,scheduled_at,created_by,created_at,updated_at
                        ) VALUES(%s,%s,%s,%s,%s,'index','pending',0,NOW(),%s,NOW(),NOW())
                        RETURNING job_id
                        """,
                        (
                            job["ai_document_id"],
                            int(job["object_id"]),
                            int(job["revision_id"]),
                            primary_artifact_id,
                            action,
                            job.get("created_by"),
                        ),
                    )
                    index_job_id = int(cursor.fetchone()["job_id"])
                cursor.execute(
                    """
                    UPDATE ai_document_info
                       SET index_status='pending',updated_at=NOW()
                     WHERE ai_document_id=%s
                    """,
                    (job["ai_document_id"],),
                )
            event_result = "applied" if current else "ignored"
            event_reason = None if current else "Parsed revision is no longer current and active"
            cursor.execute(
                """
                UPDATE ai_callback_events
                   SET processing_result=%s,result_reason=%s,
                       processed_at=NOW(),updated_at=NOW()
                 WHERE callback_event_id=%s
                """,
                (event_result, event_reason, event_db_id),
            )
    return {
        "ok": True,
        "job_id": int(job["job_id"]),
        "job_status": "completed",
        "artifact_ids": artifact_ids,
        "primary_artifact_id": primary_artifact_id,
        "index_job_id": index_job_id,
        "ignored": not current,
    }


def apply_parser_callback(
    payload: Dict[str, Any],
    *,
    authorization: Optional[str],
) -> Dict[str, Any]:
    claims = _verify_parser_token(authorization)
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
    callback_hash = _payload_hash(payload)
    event = _reserve_event(event_id, int(job_id), callback_hash)
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
        job = _find_parse_job(int(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Parser job not found")
        _link_event_job(event_db_id, int(job_id))
        _validate_job_identity(job, payload)
        if _norm(job.get("status")).lower() in {"cancelled", "superseded"}:
            reason = f"Parse job is {job['status']}"
            _update_event(event_db_id, "ignored", reason)
            return {"ok": True, "ignored": True, "job_id": int(job_id), "reason": reason}
        if _norm(job.get("status")).lower() == "completed":
            _update_event(event_db_id, "ignored", "Parse job was already completed")
            return {"ok": True, "duplicate": True, "job_id": int(job_id)}
        if result_status == "failed":
            message = _norm(
                payload.get("message")
                or payload.get("error")
                or payload.get("error_message")
                or payload.get("error_code")
            )
            failure_message = message or "Parser failed"
            result = _apply_failed_result(event_db_id, job, failure_message)
            from app.services.features.pipeline_notification_service import notify_pipeline_result

            notify_pipeline_result(job, stage="parse", success=False, message=failure_message)
            return result
        artifacts = _artifact_rows(payload)
        verified_artifacts = _verify_artifacts(job, artifacts)
        result = _apply_success_result(
            event_db_id,
            job,
            verified_artifacts,
            _norm(payload.get("parser_version")),
            _norm(payload.get("external_task_id") or payload.get("task_id")),
        )
        from app.services.features.pipeline_notification_service import notify_pipeline_result

        notify_pipeline_result(job, stage="parse", success=True)
        return result
    except HTTPException as exc:
        result = "failed" if exc.status_code >= 500 else "rejected"
        _update_event(event_db_id, result, _norm(exc.detail))
        raise
    except Exception as exc:
        _update_event(event_db_id, "failed", str(exc))
        raise


__all__ = ["apply_parser_callback"]
