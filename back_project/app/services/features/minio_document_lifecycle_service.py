from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.config import settings

from app.repositories.transaction import db_transaction
from app.services.common.minio_key_builder import KbStorageScope, artifact_prefix
from app.services.common.minio_storage_service import (
    MinioObjectNotFoundError,
    get_minio_storage_service,
)
from app.utils.db import get_connection


_ACTIVE_JOB_STATUSES = ("pending", "running", "sent")


def _row(cursor: Any) -> Dict[str, Any] | None:
    value = cursor.fetchone()
    return dict(value) if value else None


def _fetch_deletion(deletion_id: int) -> Dict[str, Any] | None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dd.*,so.*,tn.parent_id,tn.kb_root_id,tn.alias,
                       tn.item_type_code,tn.sort_order,
                       ab.owner_user_id,ab.target_group_id,ab.policy_code
                  FROM document_deletions dd
                  JOIN storage_objects so ON so.object_id=dd.object_id
                  LEFT JOIN tree_nodes tn ON tn.node_id=dd.node_id
                  LEFT JOIN acl_bindings ab ON ab.node_id=dd.node_id
                 WHERE dd.deletion_id=%s
                 LIMIT 1
                """,
                (int(deletion_id),),
            )
            return _row(cursor)
    finally:
        connection.close()


def is_minio_deletion(deletion_id: int) -> bool:
    row = _fetch_deletion(deletion_id)
    return bool(row and str(row.get("storage_backend") or "").lower() == "minio")


def _scope(row: Dict[str, Any]) -> KbStorageScope:
    return (
        KbStorageScope.PERSONAL
        if str(row.get("upload_mode") or "").lower() == "personal"
        else KbStorageScope.SE_KB
    )


def _latest_ready_revision(cursor: Any, object_id: int) -> Dict[str, Any] | None:
    cursor.execute(
        """
        SELECT revision_id,rev_no,minio_version_id,size_bytes
          FROM doc_revisions
         WHERE doc_id=%s
           AND storage_status='READY'
           AND minio_version_id IS NOT NULL
         ORDER BY rev_no DESC,revision_id DESC
         LIMIT 1
        """,
        (int(object_id),),
    )
    return _row(cursor)


def _enqueue_restore_job(cursor: Any, row: Dict[str, Any], revision_id: int, actor_user_id: Optional[int]) -> int:
    ai_document_id = str(row.get("ai_document_id") or "").strip()
    if not ai_document_id:
        raise HTTPException(status_code=409, detail="AI document metadata is missing")

    cursor.execute(
        """
        SELECT artifact_id
          FROM ai_parse_artifacts
         WHERE revision_id=%s AND artifact_status='READY'
         ORDER BY artifact_id DESC LIMIT 1
        """,
        (int(revision_id),),
    )
    artifact = _row(cursor)
    action = "reindex" if artifact else "update"
    stage = "index" if artifact else "parse"
    artifact_id = int(artifact["artifact_id"]) if artifact else None
    cursor.execute(
        """
        INSERT INTO ai_index_jobs(
            ai_document_id,object_id,revision_id,artifact_id,action,stage,status,
            retry_count,scheduled_at,created_by,created_at,updated_at
        ) VALUES(%s,%s,%s,%s,%s,%s,'pending',0,NOW(),%s,NOW(),NOW())
        RETURNING job_id
        """,
        (
            ai_document_id,
            int(row["object_id"]),
            int(revision_id),
            artifact_id,
            action,
            stage,
            actor_user_id,
        ),
    )
    return int(cursor.fetchone()["job_id"])


def restore_minio_document(deletion_id: int, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    row = _fetch_deletion(deletion_id)
    if not row or row.get("status") != "pending":
        raise HTTPException(status_code=404, detail="restorable deletion not found")
    if str(row.get("storage_backend") or "").lower() != "minio":
        raise HTTPException(status_code=409, detail="document is not stored in MinIO")

    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            revision = _latest_ready_revision(cursor, int(row["object_id"]))
    finally:
        connection.close()
    if not revision:
        raise HTTPException(status_code=409, detail="ready MinIO revision is missing")

    storage = get_minio_storage_service()
    try:
        storage.stat_object(
            str(row.get("bucket_name") or ""),
            str(row.get("object_key") or ""),
            version_id=str(revision["minio_version_id"]),
        )
    except MinioObjectNotFoundError as exc:
        raise HTTPException(status_code=409, detail="MinIO source version is missing") from exc

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,object_id FROM document_deletions WHERE deletion_id=%s FOR UPDATE",
                (int(deletion_id),),
            )
            current = _row(cursor)
            if not current or current.get("status") != "pending":
                raise HTTPException(status_code=409, detail="deletion state changed during restore")
            object_id = int(current["object_id"])
            cursor.execute(
                "UPDATE storage_objects SET doc_status='active',db_updated_at=NOW() WHERE object_id=%s",
                (object_id,),
            )
            cursor.execute(
                "UPDATE ai_document_info SET index_status='pending',updated_at=NOW() WHERE object_id=%s",
                (object_id,),
            )
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status='cancelled',cancel_requested_at=NOW(),finished_at=NOW(),updated_at=NOW()
                 WHERE object_id=%s AND action='delete' AND status IN ('pending','running','sent')
                """,
                (object_id,),
            )
            cursor.execute(
                """
                UPDATE document_deletions
                   SET status='restored',restored_at=NOW(),restored_by=%s,
                       purge_error=NULL,updated_at=NOW()
                 WHERE deletion_id=%s
                """,
                (actor_user_id, int(deletion_id)),
            )
            job_id = _enqueue_restore_job(cursor, row, int(revision["revision_id"]), actor_user_id)

    return {
        "ok": True,
        "object_id": int(row["object_id"]),
        "deletion_id": int(deletion_id),
        "restored": True,
        "revision_id": int(revision["revision_id"]),
        "job_id": job_id,
    }


def _ensure_delete_job(cursor: Any, row: Dict[str, Any], actor_user_id: Optional[int]) -> Dict[str, Any]:
    ai_document_id = str(row.get("ai_document_id") or "").strip()
    if not ai_document_id:
        raise HTTPException(status_code=409, detail="AI document metadata is missing")
    cursor.execute(
        """
        SELECT job_id,status
          FROM ai_index_jobs
         WHERE ai_document_id=%s AND action='delete'
         ORDER BY job_id DESC LIMIT 1
        """,
        (ai_document_id,),
    )
    job = _row(cursor)
    if job and job.get("status") == "completed":
        return job
    if job and job.get("status") in _ACTIVE_JOB_STATUSES:
        return job
    cursor.execute(
        """
        INSERT INTO ai_index_jobs(
            ai_document_id,object_id,action,stage,status,retry_count,scheduled_at,
            created_by,created_at,updated_at
        ) VALUES(%s,%s,'delete','delete','pending',0,NOW(),%s,NOW(),NOW())
        RETURNING job_id
        """,
        (ai_document_id, int(row["object_id"]), actor_user_id),
    )
    return {"job_id": int(cursor.fetchone()["job_id"]), "status": "pending"}


def _index_delete_required(cursor: Any, ai_document_id: str) -> bool:
    normalized_id = str(ai_document_id or "").strip()
    if not normalized_id:
        return False
    cursor.execute(
        """
        SELECT CASE WHEN
            EXISTS(
                SELECT 1
                  FROM ai_document_info ai
                 WHERE ai.ai_document_id=%s
                   AND ai.indexed_revision_id IS NOT NULL
            )
            OR EXISTS(
                SELECT 1
                  FROM ai_index_jobs j
                 WHERE j.ai_document_id=%s
                   AND j.stage='index'
                   AND j.action<>'delete'
                   AND (
                        j.status IN ('running','sent','completed')
                        OR j.external_task_id IS NOT NULL
                        OR j.started_at IS NOT NULL
                   )
            )
            THEN 1 ELSE 0 END AS required
        """,
        (normalized_id, normalized_id),
    )
    return bool(int((_row(cursor) or {}).get("required") or 0))


def requires_index_delete(ai_document_id: str) -> bool:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            return _index_delete_required(cursor, ai_document_id)
    finally:
        connection.close()


def request_minio_purge(deletion_id: int, actor_user_id: Optional[int] = None) -> Dict[str, Any]:
    row = _fetch_deletion(deletion_id)
    if not row or row.get("status") not in {"pending", "purging", "purge_failed"}:
        raise HTTPException(status_code=404, detail="purgeable deletion not found")
    if str(row.get("storage_backend") or "").lower() != "minio":
        raise HTTPException(status_code=409, detail="document is not stored in MinIO")

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM document_deletions WHERE deletion_id=%s FOR UPDATE",
                (int(deletion_id),),
            )
            current = _row(cursor)
            if not current or current.get("status") not in {"pending", "purging", "purge_failed"}:
                raise HTTPException(status_code=409, detail="deletion state changed during purge")
            if current.get("status") == "purging":
                return {
                    "ok": True,
                    "object_id": int(row["object_id"]),
                    "deletion_id": int(deletion_id),
                    "purged": False,
                    "status": "purging",
                    "idempotent_replay": True,
                }
            cursor.execute(
                """
                UPDATE document_deletions
                   SET status='purging',purge_started_at=COALESCE(purge_started_at,NOW()),
                       last_purge_attempt_at=NOW(),purge_retry_count=purge_retry_count+1,
                       purge_error=NULL,purged_by=%s,updated_at=NOW()
                 WHERE deletion_id=%s
                """,
                (actor_user_id, int(deletion_id)),
            )
            index_delete_required = _index_delete_required(
                cursor,
                str(row.get("ai_document_id") or ""),
            )
            if index_delete_required:
                job = _ensure_delete_job(cursor, row, actor_user_id)
            else:
                cursor.execute(
                    """
                    UPDATE ai_index_jobs
                       SET status='cancelled',cancel_requested_at=NOW(),finished_at=NOW(),updated_at=NOW()
                     WHERE ai_document_id=%s
                       AND action='delete'
                       AND status IN ('pending','running','sent')
                    """,
                    (str(row.get("ai_document_id") or ""),),
                )
                job = None

    if not index_delete_required:
        return finalize_minio_purge_for_object(
            int(row["object_id"]),
            actor_user_id=actor_user_id,
            require_index_delete=False,
        )
    if job and job.get("status") == "completed":
        return finalize_minio_purge_for_object(int(row["object_id"]), actor_user_id=actor_user_id)
    return {
        "ok": True,
        "object_id": int(row["object_id"]),
        "deletion_id": int(deletion_id),
        "purged": False,
        "status": "purging",
        "delete_job_id": int(job["job_id"]),
    }


def _delete_minio_payload(row: Dict[str, Any], revision_ids: list[int]) -> Dict[str, int]:
    storage = get_minio_storage_service()
    scope = _scope(row)
    deleted_artifact_versions = 0
    for revision_id in revision_ids:
        location = artifact_prefix(
            scope,
            int(row["object_id"]),
            revision_id,
            user_id=row.get("uploader_user_id") if scope is KbStorageScope.PERSONAL else None,
        )
        deleted_artifact_versions += storage.permanent_delete_prefix(
            location.bucket_name,
            location.object_key,
        )
    deleted_source_versions = storage.permanent_delete_object(
        str(row.get("bucket_name") or ""),
        str(row.get("object_key") or ""),
    )
    return {
        "source_versions": deleted_source_versions,
        "artifact_versions": deleted_artifact_versions,
    }


def _archive(cursor: Any, row: Dict[str, Any], actor_user_id: Optional[int]) -> None:
    cursor.execute(
        """
        INSERT INTO deleted_documents(
            old_object_id,old_node_id,old_parent_id,old_kb_root_id,ai_document_id,
            doc_name,alias,relative_path,sub_path,file_right,doc_code,item_type_code,
            branch_type,checksum_sha256,source_created_at,source_updated_at,size_bytes,s3_key,
            upload_mode,uploader_user_id,owner_user_id,target_group_id,policy_code,sort_order,
            deleted_at,purged_at,purged_by,delete_reason,created_at
        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                 %s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,NOW())
        """,
        (
            int(row["object_id"]), int(row.get("node_id") or 0), row.get("parent_id"),
            row.get("kb_root_id"), row.get("ai_document_id"), row.get("doc_name") or "",
            row.get("alias") or row.get("doc_name") or "", row.get("relative_path") or "",
            row.get("sub_path") or "", row.get("file_right") or "", row.get("doc_code") or "",
            int(row.get("item_type_code") or 10), row.get("branch_type") or "general",
            row.get("checksum_sha256"), row.get("source_created_at"), row.get("source_updated_at"),
            row.get("size_bytes"), row.get("object_key") or "", row.get("upload_mode") or "admin_kb",
            row.get("uploader_user_id"), row.get("owner_user_id"), row.get("target_group_id"),
            row.get("policy_code"), int(row.get("sort_order") or 0),
            row.get("detected_at"), actor_user_id, row.get("reason"),
        ),
    )


def _mark_purge_failed(deletion_id: int, message: str) -> None:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_deletions
                   SET status='purge_failed',purge_error=%s,last_purge_attempt_at=NOW(),updated_at=NOW()
                 WHERE deletion_id=%s AND status IN ('purging','purge_failed')
                """,
                (message[:4000], int(deletion_id)),
            )


def finalize_minio_purge_for_object(
    object_id: int,
    actor_user_id: Optional[int] = None,
    *,
    require_index_delete: Optional[bool] = None,
) -> Dict[str, Any]:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dd.*,so.*,tn.parent_id,tn.kb_root_id,tn.alias,tn.item_type_code,tn.sort_order,
                       ab.owner_user_id,ab.target_group_id,ab.policy_code
                  FROM document_deletions dd
                  JOIN storage_objects so ON so.object_id=dd.object_id
                  LEFT JOIN tree_nodes tn ON tn.node_id=dd.node_id
                  LEFT JOIN acl_bindings ab ON ab.node_id=dd.node_id
                 WHERE dd.object_id=%s AND so.storage_backend='minio'
                   AND dd.status IN ('purging','purge_failed')
                 ORDER BY dd.deletion_id DESC LIMIT 1
                """,
                (int(object_id),),
            )
            row = _row(cursor)
            if not row:
                cursor.execute(
                    "SELECT status FROM document_deletions WHERE object_id=%s ORDER BY deletion_id DESC LIMIT 1",
                    (int(object_id),),
                )
                deletion = _row(cursor)
                if deletion:
                    return {
                        "ok": True,
                        "object_id": int(object_id),
                        "purged": False,
                        "status": "purge_not_requested",
                    }
                return {"ok": True, "object_id": int(object_id), "purged": True, "idempotent": True}
            should_require_index_delete = (
                _index_delete_required(cursor, str(row.get("ai_document_id") or ""))
                if require_index_delete is None
                else bool(require_index_delete)
            )
            if should_require_index_delete:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS n FROM ai_index_jobs
                     WHERE ai_document_id=%s AND action='delete' AND status='completed'
                    """,
                    (row.get("ai_document_id"),),
                )
                if int((_row(cursor) or {}).get("n") or 0) == 0:
                    return {"ok": True, "object_id": int(object_id), "purged": False, "status": "waiting_for_index_delete"}
            cursor.execute(
                "SELECT revision_id FROM doc_revisions WHERE doc_id=%s ORDER BY revision_id",
                (int(object_id),),
            )
            revision_ids = [int(item["revision_id"]) for item in cursor.fetchall()]
    finally:
        connection.close()

    try:
        deleted = _delete_minio_payload(row, revision_ids)
    except Exception as exc:
        _mark_purge_failed(int(row["deletion_id"]), str(exc))
        raise HTTPException(status_code=502, detail="MinIO permanent deletion failed") from exc

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM document_deletions WHERE deletion_id=%s FOR UPDATE",
                (int(row["deletion_id"]),),
            )
            current = _row(cursor)
            if not current or current.get("status") not in {"purging", "purge_failed"}:
                raise HTTPException(status_code=409, detail="deletion state changed after MinIO purge")
            _archive(cursor, row, actor_user_id or row.get("purged_by"))
            cursor.execute(
                "SELECT COALESCE(SUM(size_bytes),0) AS total FROM doc_revisions WHERE doc_id=%s AND storage_status='READY'",
                (int(object_id),),
            )
            released_bytes = int((_row(cursor) or {}).get("total") or 0)
            cursor.execute(
                "DELETE FROM ai_parse_artifacts WHERE revision_id IN (SELECT revision_id FROM doc_revisions WHERE doc_id=%s)",
                (int(object_id),),
            )
            cursor.execute("DELETE FROM doc_revisions WHERE doc_id=%s", (int(object_id),))
            cursor.execute("DELETE FROM tree_nodes WHERE doc_id=%s", (int(object_id),))
            cursor.execute(
                "UPDATE ai_document_info SET object_id=NULL,index_status='deleted',updated_at=NOW() WHERE object_id=%s",
                (int(object_id),),
            )
            cursor.execute("DELETE FROM storage_objects WHERE object_id=%s", (int(object_id),))
            if str(row.get("upload_mode") or "").lower() == "personal":
                cursor.execute(
                    """
                    UPDATE personal_kb_storage_usage
                       SET committed_bytes=GREATEST(committed_bytes-%s,0)
                     WHERE user_id=%s
                    """,
                    (released_bytes, int(row["uploader_user_id"])),
                )

    from app.services.features.purged_document_cleanup_service import (
        cleanup_purged_document_history,
    )

    history_cleanup = cleanup_purged_document_history(str(row.get("ai_document_id") or ""))

    return {
        "ok": True,
        "object_id": int(object_id),
        "purged": True,
        "released_bytes": released_bytes,
        "deleted_source_versions": deleted["source_versions"],
        "deleted_artifact_versions": deleted["artifact_versions"],
        "history_cleanup": history_cleanup,
    }


def mark_minio_purge_index_failed(object_id: int, message: str) -> None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT deletion_id FROM document_deletions
                 WHERE object_id=%s AND status='purging'
                 ORDER BY deletion_id DESC LIMIT 1
                """,
                (int(object_id),),
            )
            row = _row(cursor)
    finally:
        connection.close()
    if row:
        _mark_purge_failed(int(row["deletion_id"]), message or "Index deletion failed")


def list_resumable_minio_purges(limit: int) -> list[int]:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT dd.object_id
                  FROM document_deletions dd
                  JOIN storage_objects so ON so.object_id=dd.object_id AND so.storage_backend='minio'
                  LEFT JOIN ai_document_info ai ON ai.ai_document_id=dd.ai_document_id
                 WHERE dd.status IN ('purging','purge_failed')
                   AND (dd.last_purge_attempt_at IS NULL OR dd.last_purge_attempt_at < NOW() - (%s * INTERVAL '1 minute'))
                   AND (
                        EXISTS(
                            SELECT 1 FROM ai_index_jobs j
                             WHERE j.ai_document_id=dd.ai_document_id
                               AND j.action='delete' AND j.status='completed'
                        )
                        OR (
                            ai.indexed_revision_id IS NULL
                            AND NOT EXISTS(
                                SELECT 1 FROM ai_index_jobs j
                                 WHERE j.ai_document_id=dd.ai_document_id
                                   AND j.stage='index'
                                   AND j.action<>'delete'
                                   AND (
                                        j.status IN ('running','sent','completed')
                                        OR j.external_task_id IS NOT NULL
                                        OR j.started_at IS NOT NULL
                                   )
                            )
                        )
                   )
                 ORDER BY dd.object_id LIMIT %s
                """,
                (settings.MINIO_PURGE_RETRY_MINUTES, max(1, int(limit))),
            )
            return [int(item["object_id"]) for item in cursor.fetchall()]
    finally:
        connection.close()
