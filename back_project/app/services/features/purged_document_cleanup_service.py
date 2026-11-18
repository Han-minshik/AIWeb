from __future__ import annotations

from typing import Any, Dict

from app.repositories.common import fetch_all
from app.repositories.transaction import db_transaction


def cleanup_purged_document_history(ai_document_id: str) -> Dict[str, Any]:
    """Remove pipeline metadata only after a document has been durably archived."""
    normalized_id = str(ai_document_id or "").strip()
    if not normalized_id:
        return {"cleaned": False, "jobs_deleted": 0, "metadata_deleted": 0}

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dd.ai_document_id
                  FROM deleted_documents dd
                 WHERE dd.ai_document_id=%s
                   AND NOT EXISTS(
                       SELECT 1
                         FROM storage_objects so
                        WHERE so.object_id=dd.old_object_id
                   )
                 ORDER BY dd.purged_at DESC,dd.deleted_doc_id DESC
                 LIMIT 1
                 FOR UPDATE
                """,
                (normalized_id,),
            )
            if not cursor.fetchone():
                return {"cleaned": False, "jobs_deleted": 0, "metadata_deleted": 0}

            # Callback events provide idempotency and audit information. Keep them, but
            # release their nullable job reference before deleting the visible job rows.
            cursor.execute(
                """
                UPDATE ai_callback_events
                   SET job_id=NULL,updated_at=NOW()
                 WHERE job_id IN (
                     SELECT job_id FROM ai_index_jobs WHERE ai_document_id=%s
                 )
                """,
                (normalized_id,),
            )
            cursor.execute(
                "DELETE FROM ai_index_jobs WHERE ai_document_id=%s",
                (normalized_id,),
            )
            jobs_deleted = max(0, int(cursor.rowcount or 0))
            cursor.execute(
                """
                DELETE FROM ai_document_info
                 WHERE ai_document_id=%s
                   AND object_id IS NULL
                """,
                (normalized_id,),
            )
            metadata_deleted = max(0, int(cursor.rowcount or 0))

    return {
        "cleaned": True,
        "jobs_deleted": jobs_deleted,
        "metadata_deleted": metadata_deleted,
    }


def list_residual_purged_document_ids(limit: int = 1000) -> list[str]:
    rows = fetch_all(
        """
        SELECT DISTINCT dd.ai_document_id
          FROM deleted_documents dd
         WHERE COALESCE(dd.ai_document_id,'')<>''
           AND NOT EXISTS(
               SELECT 1 FROM storage_objects so WHERE so.object_id=dd.old_object_id
           )
           AND (
               EXISTS(
                   SELECT 1 FROM ai_index_jobs j
                    WHERE j.ai_document_id=dd.ai_document_id
               )
               OR EXISTS(
                   SELECT 1 FROM ai_document_info ai
                    WHERE ai.ai_document_id=dd.ai_document_id
                      AND ai.object_id IS NULL
               )
           )
         ORDER BY dd.ai_document_id
         LIMIT %s
        """,
        (max(1, min(int(limit), 10000)),),
    )
    return [str(row["ai_document_id"]) for row in rows]


__all__ = ["cleanup_purged_document_history", "list_residual_purged_document_ids"]
