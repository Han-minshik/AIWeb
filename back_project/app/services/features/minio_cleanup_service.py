from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import HTTPException

from app.config import settings
from app.repositories.transaction import db_transaction
from app.services.common.minio_storage_service import (
    MinioObjectNotFoundError,
    get_minio_storage_service,
)
from app.services.features import minio_document_lifecycle_service
from app.utils.db import get_connection


def _fetch_candidates(sql: str, params: tuple[Any, ...]) -> list[Dict[str, Any]]:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _delete_failed_upload(revision_id: int) -> bool:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dr.revision_id,dr.doc_id,dr.minio_version_id,dr.storage_status,
                       (SELECT COUNT(*) FROM doc_revisions r2 WHERE r2.doc_id=dr.doc_id) AS revision_count,
                       (SELECT COUNT(*) FROM ai_index_jobs j WHERE j.revision_id=dr.revision_id) AS job_count,
                       (SELECT COUNT(*) FROM ai_parse_artifacts a WHERE a.revision_id=dr.revision_id) AS artifact_count
                  FROM doc_revisions dr
                 WHERE dr.revision_id=%s FOR UPDATE
                """,
                (int(revision_id),),
            )
            row = cursor.fetchone()
            if (
                not row
                or row.get("storage_status") != "FAILED"
                or row.get("minio_version_id")
                or int(row.get("revision_count") or 0) != 1
                or int(row.get("job_count") or 0) != 0
                or int(row.get("artifact_count") or 0) != 0
            ):
                return False
            object_id = int(row["doc_id"])
            cursor.execute("DELETE FROM tree_nodes WHERE doc_id=%s", (object_id,))
            cursor.execute("DELETE FROM ai_document_info WHERE object_id=%s", (object_id,))
            cursor.execute("DELETE FROM doc_revisions WHERE revision_id=%s", (int(revision_id),))
            cursor.execute("DELETE FROM storage_objects WHERE object_id=%s", (object_id,))
            return True


def _delete_artifact(row: Dict[str, Any]) -> bool:
    storage = get_minio_storage_service()
    try:
        storage.remove_version(row["bucket_name"], row["object_key"], row["minio_version_id"])
    except MinioObjectNotFoundError:
        pass
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM ai_parse_artifacts
                 WHERE artifact_id=%s AND minio_version_id=%s
                """,
                (int(row["artifact_id"]), row["minio_version_id"]),
            )
            return bool(cursor.rowcount)


def _active_deletion_clause(alias: str = "dd") -> str:
    return f"{alias}.status IN ('pending','purging','purge_failed')"


def run_cleanup(*, dry_run: bool | None = None, batch_size: int | None = None) -> Dict[str, Any]:
    """Remove reproducible or terminal data without deleting source document revisions."""
    dry_run = settings.CLEANUP_DRY_RUN if dry_run is None else bool(dry_run)
    limit = max(1, int(batch_size or settings.CLEANUP_BATCH_SIZE))
    now = datetime.now()

    failed_uploads = _fetch_candidates(
        """
        SELECT dr.revision_id
          FROM doc_revisions dr
          JOIN storage_objects so ON so.object_id=dr.doc_id
         WHERE so.storage_backend='minio'
           AND dr.upload_request_id IS NOT NULL
           AND dr.storage_status='FAILED'
           AND dr.minio_version_id IS NULL
           AND dr.created_at < %s
         ORDER BY dr.created_at,dr.revision_id LIMIT %s
        """,
        (now - timedelta(days=settings.FAILED_UPLOAD_RETENTION_DAYS), limit),
    )
    artifacts = _fetch_candidates(
        """
        SELECT a.artifact_id,a.bucket_name,a.object_key,a.minio_version_id,a.artifact_status
          FROM ai_parse_artifacts a
          JOIN doc_revisions dr ON dr.revision_id=a.revision_id
          JOIN storage_objects so ON so.object_id=dr.doc_id
          LEFT JOIN ai_document_info ai ON ai.object_id=dr.doc_id
         WHERE so.doc_status='active'
           AND NOT EXISTS(
                SELECT 1 FROM document_deletions dd
                 WHERE dd.object_id=dr.doc_id
                   AND dd.status IN ('pending','purging','purge_failed')
           )
           AND (
                (a.artifact_status IN ('PENDING','INVALID') AND a.updated_at < %s)
             OR (a.artifact_status='READY' AND a.updated_at < %s
                 AND (ai.indexed_revision_id IS NULL OR ai.indexed_revision_id<>a.revision_id))
           )
           AND NOT EXISTS(
                SELECT 1 FROM ai_index_jobs j
                 WHERE j.artifact_id=a.artifact_id
                   AND j.status IN ('pending','running','sent')
           )
         ORDER BY a.updated_at,a.artifact_id LIMIT %s
        """,
        (
            now - timedelta(days=settings.FAILED_ARTIFACT_RETENTION_DAYS),
            now - timedelta(days=settings.PAST_ARTIFACT_RETENTION_DAYS),
            limit,
        ),
    )
    resumable_purges = minio_document_lifecycle_service.list_resumable_minio_purges(limit)
    callback_cutoff = now - timedelta(days=settings.CALLBACK_EVENT_RETENTION_DAYS)
    completed_cutoff = now - timedelta(days=settings.COMPLETED_JOB_RETENTION_DAYS)
    failed_cutoff = now - timedelta(days=settings.FAILED_JOB_RETENTION_DAYS)

    callback_candidates = _fetch_candidates(
        """
        SELECT e.callback_event_id
          FROM ai_callback_events e
          LEFT JOIN ai_index_jobs j ON j.job_id=e.job_id
         WHERE e.received_at < %s
           AND NOT EXISTS(
                SELECT 1 FROM document_deletions dd
                 WHERE dd.ai_document_id=j.ai_document_id
                   AND dd.status IN ('pending','purging','purge_failed')
           )
         ORDER BY e.callback_event_id LIMIT %s
        """,
        (callback_cutoff, limit),
    )
    job_candidates = _fetch_candidates(
        """
        SELECT j.job_id
          FROM ai_index_jobs j
         WHERE (
                (j.status IN ('completed','cancelled','superseded') AND COALESCE(j.finished_at,j.updated_at)<%s)
             OR (j.status='failed' AND COALESCE(j.finished_at,j.updated_at)<%s)
         )
           AND NOT EXISTS(
                SELECT 1 FROM document_deletions dd
                 WHERE dd.ai_document_id=j.ai_document_id
                   AND dd.status IN ('pending','purging','purge_failed')
           )
           AND NOT EXISTS(SELECT 1 FROM ai_callback_events e WHERE e.job_id=j.job_id)
         ORDER BY j.job_id LIMIT %s
        """,
        (completed_cutoff, failed_cutoff, limit),
    )

    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "candidates": {
            "failed_uploads": len(failed_uploads),
            "artifacts": len(artifacts),
            "purges": len(resumable_purges),
            "callbacks": len(callback_candidates),
            "jobs": len(job_candidates),
        },
        "deleted": {
            "failed_uploads": 0,
            "artifacts": 0,
            "purges": 0,
            "callbacks": 0,
            "jobs": 0,
        },
        "errors": [],
    }
    if dry_run:
        return result

    for row in failed_uploads:
        result["deleted"]["failed_uploads"] += int(_delete_failed_upload(row["revision_id"]))
    for row in artifacts:
        result["deleted"]["artifacts"] += int(_delete_artifact(row))
    for object_id in resumable_purges:
        try:
            purge = minio_document_lifecycle_service.finalize_minio_purge_for_object(object_id)
            result["deleted"]["purges"] += int(bool(purge.get("purged")))
        except HTTPException as exc:
            result["errors"].append({"object_id": object_id, "error": str(exc.detail)})
        except Exception as exc:
            result["errors"].append({"object_id": object_id, "error": str(exc)})

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            if callback_candidates:
                ids = [int(row["callback_event_id"]) for row in callback_candidates]
                placeholders = ",".join(["%s"] * len(ids))
                cursor.execute(
                    f"DELETE FROM ai_callback_events WHERE callback_event_id IN ({placeholders})",
                    tuple(ids),
                )
                result["deleted"]["callbacks"] = int(cursor.rowcount)
            if job_candidates:
                ids = [int(row["job_id"]) for row in job_candidates]
                placeholders = ",".join(["%s"] * len(ids))
                cursor.execute(
                    f"DELETE FROM ai_index_jobs WHERE job_id IN ({placeholders})",
                    tuple(ids),
                )
                result["deleted"]["jobs"] = int(cursor.rowcount)
    return result
