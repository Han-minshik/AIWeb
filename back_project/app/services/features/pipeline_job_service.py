from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Optional

from app.repositories.common import fetch_all
from app.repositories.transaction import db_transaction


RETRYABLE_STAGES = {"parse", "index", "delete"}
try:
    SENT_RETRY_AFTER_SECONDS = max(
        60, int(os.getenv("PIPELINE_SENT_RETRY_AFTER_SECONDS", "1800"))
    )
except (TypeError, ValueError):
    SENT_RETRY_AFTER_SECONDS = 1800

VISIBLE_STATUSES = {
    "pending", "running", "sent", "completed", "failed", "cancelled", "superseded"
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _row(cursor: Any) -> Optional[Dict[str, Any]]:
    value = cursor.fetchone()
    return dict(value) if value else None


def _job_select(*, for_update: bool = False) -> str:
    # PostgreSQL rejects locking the nullable side of the LEFT JOINs below.
    # Only the queue row is mutated by retry/recovery, so lock that row alone.
    lock = " FOR UPDATE OF j" if for_update else ""
    return f"""
        SELECT
            j.job_id,j.ai_document_id,j.object_id,j.revision_id,j.artifact_id,
            j.external_task_id,j.action,j.stage,j.status,j.retry_count,
            j.scheduled_at,j.started_at,j.finished_at,j.cancel_requested_at,
            j.last_error,j.created_by,j.created_at,j.updated_at,
            EXTRACT(EPOCH FROM (
                NOW()::timestamp - COALESCE(j.updated_at,j.scheduled_at,j.created_at)
            ))::double precision AS status_age_seconds,
            ai.source_revision_id AS current_revision_id,
            ai.indexed_revision_id,ai.index_status,ai.doc_name,ai.user_id,
            so.doc_status,so.upload_mode,so.uploader_user_id,
            dr.storage_status AS revision_storage_status,
            pa.artifact_status,
            tn.kb_type,tn.kb_owner_user_id,
            EXISTS(
                SELECT 1
                  FROM deleted_documents archived
                 WHERE archived.ai_document_id=j.ai_document_id
                   AND COALESCE(archived.upload_mode,'')='personal'
            ) AS archived_personal,
            (
                SELECT archived.uploader_user_id
                  FROM deleted_documents archived
                 WHERE archived.ai_document_id=j.ai_document_id
                   AND archived.uploader_user_id IS NOT NULL
                 ORDER BY archived.purged_at DESC,archived.deleted_doc_id DESC
                 LIMIT 1
            ) AS archived_owner_user_id,
            dd.status AS deletion_status,
            (
                SELECT COUNT(*)
                  FROM ai_index_jobs peer
                 WHERE peer.job_id<>j.job_id
                   AND peer.ai_document_id=j.ai_document_id
                   AND peer.stage=j.stage
                   AND COALESCE(peer.revision_id,0)=COALESCE(j.revision_id,0)
                   AND peer.status IN ('pending','running','sent','completed')
            ) AS peer_job_count
          FROM ai_index_jobs j
          LEFT JOIN ai_document_info ai ON ai.ai_document_id=j.ai_document_id
          LEFT JOIN storage_objects so ON so.object_id=COALESCE(j.object_id,ai.object_id)
          LEFT JOIN doc_revisions dr ON dr.revision_id=j.revision_id
          LEFT JOIN ai_parse_artifacts pa ON pa.artifact_id=j.artifact_id
          LEFT JOIN tree_nodes tn ON tn.node_id=(
              SELECT candidate.node_id
                FROM tree_nodes candidate
               WHERE candidate.doc_id=COALESCE(j.object_id,ai.object_id)
               ORDER BY candidate.node_id LIMIT 1
          )
          LEFT JOIN document_deletions dd ON dd.deletion_id=(
              SELECT MAX(dd2.deletion_id)
                FROM document_deletions dd2
               WHERE dd2.object_id=COALESCE(j.object_id,ai.object_id)
          )
         WHERE j.job_id=%s{lock}
    """


def _is_personal(row: Dict[str, Any]) -> bool:
    return (
        _norm(row.get("upload_mode")) == "personal"
        or _norm(row.get("kb_type")) in {"personal", "person"}
        or bool(row.get("archived_personal"))
    )


def _can_access(row: Dict[str, Any], actor_user_id: int, is_admin: bool) -> bool:
    if is_admin and not _is_personal(row):
        return True
    if not _is_personal(row):
        return False
    owners = {
        row.get("uploader_user_id"), row.get("kb_owner_user_id"), row.get("user_id"),
        row.get("archived_owner_user_id"),
    }
    return int(actor_user_id) in {int(value) for value in owners if value is not None}


def retry_eligibility(row: Dict[str, Any]) -> tuple[bool, str, str]:
    stage = _norm(row.get("stage"))
    if stage not in RETRYABLE_STAGES:
        return False, "cancelled", "This job stage is managed by the backend"
    status = _norm(row.get("status"))
    if status == "sent":
        try:
            age_seconds = float(row.get("status_age_seconds"))
        except (TypeError, ValueError):
            age_seconds = -1
        if age_seconds < SENT_RETRY_AFTER_SECONDS:
            remaining = max(0, SENT_RETRY_AFTER_SECONDS - max(0, int(age_seconds)))
            return False, "sent", f"Waiting for callback; retry is available in {remaining} seconds"
    elif status != "failed":
        return False, status, "Only failed or stale sent jobs can be retried"
    if int(row.get("peer_job_count") or 0) > 0:
        return False, "superseded", "Another active or completed job already covers this work"

    if stage in {"parse", "index"}:
        if _norm(row.get("doc_status")) != "active":
            return False, "cancelled", "The document is no longer active"
        revision_id = row.get("revision_id")
        current_revision_id = row.get("current_revision_id")
        if revision_id is None or current_revision_id is None or int(revision_id) != int(current_revision_id):
            return False, "superseded", "The job revision is no longer current"
        if _norm(row.get("revision_storage_status")) != "ready":
            return False, "cancelled", "The source revision is not ready"
        if stage == "index" and _norm(row.get("artifact_status")) != "ready":
            return False, "cancelled", "The parse artifact is not ready"
        return True, "pending", ""

    if _norm(row.get("deletion_status")) not in {"pending", "purging", "purge_failed"}:
        return False, "cancelled", "The deletion was restored or is no longer purgeable"
    if _norm(row.get("doc_status")) == "active":
        return False, "cancelled", "The document was restored"
    return True, "pending", ""


def _set_document_pending(cursor: Any, row: Dict[str, Any]) -> None:
    stage = _norm(row.get("stage"))
    if stage in {"parse", "index"}:
        cursor.execute(
            """
            UPDATE ai_document_info
               SET index_status='pending',updated_at=NOW()
             WHERE ai_document_id=%s AND source_revision_id=%s
            """,
            (row["ai_document_id"], int(row["revision_id"])),
        )
    elif stage == "delete":
        cursor.execute(
            "UPDATE ai_document_info SET index_status='pending',updated_at=NOW() WHERE ai_document_id=%s",
            (row["ai_document_id"],),
        )


def retry_jobs(
    job_ids: Iterable[int], *, actor_user_id: int, is_admin: bool
) -> Dict[str, Any]:
    ids = list(dict.fromkeys(int(value) for value in job_ids if int(value) > 0))
    results: list[Dict[str, Any]] = []
    for job_id in ids:
        with db_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(_job_select(for_update=True), (job_id,))
                row = _row(cursor)
                if not row or not _can_access(row, int(actor_user_id), bool(is_admin)):
                    results.append({
                        "job_id": job_id, "success": False, "status_code": 404,
                        "error": "Pipeline job not found or access denied",
                    })
                    continue
                allowed, rejected_status, reason = retry_eligibility(row)
                if not allowed:
                    if rejected_status in {"cancelled", "superseded"} and _norm(row.get("status")) == "failed":
                        cursor.execute(
                            """
                            UPDATE ai_index_jobs
                               SET status=%s,finished_at=COALESCE(finished_at,NOW()),
                                   last_error=%s,updated_at=NOW()
                             WHERE job_id=%s AND status='failed'
                            """,
                            (rejected_status, reason[:4000], job_id),
                        )
                    results.append({
                        "job_id": job_id, "stage": row.get("stage"), "action": row.get("action"),
                        "success": False, "status_code": 409, "result": rejected_status,
                        "error": reason,
                    })
                    continue
                source_status = _norm(row.get("status"))
                cursor.execute(
                    """
                    UPDATE ai_index_jobs
                       SET status='superseded',finished_at=COALESCE(finished_at,NOW()),
                           last_error='Replaced by a manual retry',updated_at=NOW()
                     WHERE job_id=%s AND status=%s
                    """,
                    (job_id, source_status),
                )
                if cursor.rowcount != 1:
                    results.append({
                        "job_id": job_id, "success": False, "status_code": 409,
                        "error": "Pipeline job state changed during retry",
                    })
                    continue
                cursor.execute(
                    """
                    INSERT INTO ai_index_jobs (
                        ai_document_id,object_id,revision_id,artifact_id,
                        action,stage,status,retry_count,scheduled_at,
                        started_at,finished_at,cancel_requested_at,
                        external_task_id,last_error,created_by,created_at,updated_at
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,'pending',0,NOW(),
                            NULL,NULL,NULL,NULL,NULL,%s,NOW(),NOW())
                    RETURNING job_id
                    """,
                    (
                        row["ai_document_id"], row.get("object_id"),
                        row.get("revision_id"), row.get("artifact_id"),
                        row.get("action"), row.get("stage"), int(actor_user_id),
                    ),
                )
                retry_row = _row(cursor)
                if not retry_row or retry_row.get("job_id") is None:
                    raise RuntimeError("Failed to create a retry pipeline job")
                retry_job_id = int(retry_row["job_id"])
                _set_document_pending(cursor, row)
                results.append({
                    "job_id": job_id, "stage": row.get("stage"), "action": row.get("action"),
                    "retry_job_id": retry_job_id,
                    "success": True, "result": "pending", "error": None,
                })
    succeeded = sum(1 for item in results if item.get("success") is True)
    return {
        "ok": succeeded == len(results), "requested": len(results),
        "succeeded": succeeded, "failed": len(results) - succeeded,
        "updated": succeeded, "items": results,
    }


def list_jobs(
    *,
    actor_user_id: int,
    is_admin: bool,
    status: str = "all",
    scope: str = "auto",
    limit: int = 200,
) -> Dict[str, Any]:
    safe_status = _norm(status) or "all"
    if safe_status != "all" and safe_status not in VISIBLE_STATUSES:
        safe_status = "all"
    safe_limit = max(1, min(int(limit), 1000))
    safe_scope = _norm(scope) or "auto"
    if safe_scope not in {"auto", "personal", "se"}:
        safe_scope = "auto"
    conditions = []
    params: list[Any] = []
    if safe_status != "all":
        conditions.append("j.status=%s")
        params.append(safe_status)
    personal_condition = """
        (
            COALESCE(so.upload_mode,'')='personal'
            OR COALESCE(tn.kb_type,'') IN ('personal','person')
            OR EXISTS(
                SELECT 1
                  FROM deleted_documents archived
                 WHERE archived.ai_document_id=j.ai_document_id
                   AND COALESCE(archived.upload_mode,'')='personal'
            )
        )
    """
    owner_condition = """
        %s IN (
            COALESCE(so.uploader_user_id,-1),
            COALESCE(tn.kb_owner_user_id,-1),
            COALESCE(ai.user_id,-1),
            COALESCE((
                SELECT archived.uploader_user_id
                  FROM deleted_documents archived
                 WHERE archived.ai_document_id=j.ai_document_id
                   AND archived.uploader_user_id IS NOT NULL
                 ORDER BY archived.purged_at DESC,archived.deleted_doc_id DESC
                 LIMIT 1
            ),-1)
        )
    """
    if safe_scope == "se":
        if not is_admin:
            conditions.append("1=0")
        else:
            conditions.append(f"NOT {personal_condition}")
    elif safe_scope == "personal":
        conditions.extend([personal_condition, owner_condition])
        params.append(int(actor_user_id))
    elif is_admin:
        conditions.append(f"(NOT {personal_condition} OR ({personal_condition} AND {owner_condition}))")
        params.append(int(actor_user_id))
    else:
        conditions.extend([personal_condition, owner_condition])
        params.append(int(actor_user_id))
    where_sql = " AND ".join(conditions) if conditions else "1=1"
    params.append(safe_limit)
    rows = fetch_all(
        f"""
        SELECT DISTINCT
            j.job_id,j.ai_document_id,j.object_id,j.revision_id,j.artifact_id,
            j.external_task_id,j.action,j.stage,j.status,j.retry_count,
            j.scheduled_at,j.started_at,j.finished_at,j.last_error,
            j.created_at,j.updated_at,
            EXTRACT(EPOCH FROM (
                NOW()::timestamp - COALESCE(j.updated_at,j.scheduled_at,j.created_at)
            ))::double precision AS status_age_seconds,
            ai.doc_name,ai.relative_path,ai.sub_path,
            ai.index_status,
            ai.indexed_revision_id,ai.source_revision_id AS current_revision_id,
            so.doc_status,dr.storage_status AS revision_storage_status,
            pa.artifact_status,dd.status AS deletion_status,
            (
                SELECT COUNT(*) FROM ai_index_jobs peer
                 WHERE peer.job_id<>j.job_id
                   AND peer.ai_document_id=j.ai_document_id
                   AND peer.stage=j.stage
                   AND COALESCE(peer.revision_id,0)=COALESCE(j.revision_id,0)
                   AND peer.status IN ('pending','running','sent','completed')
            ) AS peer_job_count
          FROM ai_index_jobs j
          LEFT JOIN ai_document_info ai ON ai.ai_document_id=j.ai_document_id
          LEFT JOIN storage_objects so ON so.object_id=COALESCE(j.object_id,ai.object_id)
          LEFT JOIN doc_revisions dr ON dr.revision_id=j.revision_id
          LEFT JOIN ai_parse_artifacts pa ON pa.artifact_id=j.artifact_id
          LEFT JOIN tree_nodes tn ON tn.doc_id=COALESCE(j.object_id,ai.object_id)
          LEFT JOIN document_deletions dd ON dd.deletion_id=(
              SELECT MAX(dd2.deletion_id) FROM document_deletions dd2
               WHERE dd2.object_id=COALESCE(j.object_id,ai.object_id)
          )
         WHERE {where_sql}
         ORDER BY j.updated_at DESC,j.job_id DESC
         LIMIT %s
        """,
        tuple(params),
    )
    for item in rows:
        allowed, _, reason = retry_eligibility(item)
        item["retry_allowed"] = allowed
        item["retry_block_reason"] = "" if allowed else reason
        item["waiting_for_callback"] = _norm(item.get("status")) == "sent"
    return {"items": rows, "total": len(rows)}


def recover_interrupted_jobs() -> Dict[str, int]:
    """Recover only unacknowledged requests; sent jobs can legitimately run for hours."""
    rows = fetch_all(
        "SELECT job_id FROM ai_index_jobs WHERE status='running' AND stage IN ('parse','index','delete') ORDER BY job_id"
    )
    summary = {"pending": 0, "cancelled": 0, "superseded": 0, "skipped": 0}
    for candidate in rows:
        job_id = int(candidate["job_id"])
        with db_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(_job_select(for_update=True), (job_id,))
                row = _row(cursor)
                if not row or _norm(row.get("status")) != "running":
                    summary["skipped"] += 1
                    continue
                probe = dict(row)
                probe["status"] = "failed"
                allowed, next_status, reason = retry_eligibility(probe)
                if allowed:
                    next_status = "pending"
                    reason = "Dispatcher restarted before request acknowledgement"
                    cursor.execute(
                        """
                        UPDATE ai_index_jobs
                           SET status='pending',started_at=NULL,external_task_id=NULL,
                               scheduled_at=NOW(),last_error=%s,updated_at=NOW()
                         WHERE job_id=%s AND status='running'
                        """,
                        (reason, job_id),
                    )
                else:
                    next_status = next_status if next_status in {"cancelled", "superseded"} else "cancelled"
                    cursor.execute(
                        """
                        UPDATE ai_index_jobs
                           SET status=%s,finished_at=NOW(),last_error=%s,updated_at=NOW()
                         WHERE job_id=%s AND status='running'
                        """,
                        (next_status, reason[:4000], job_id),
                    )
                summary[next_status] += 1
    return summary


__all__ = ["list_jobs", "recover_interrupted_jobs", "retry_eligibility", "retry_jobs"]
