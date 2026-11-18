from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from fastapi import HTTPException

from app.services.features import indexer_callback_service as service


class _Cursor:
    def __init__(self, responses=None) -> None:
        self.executions = []
        self._responses = list(responses or [])
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.executions.append((sql, params))
        self.rowcount = 1 if "UPDATE ai_document_info ai" in sql else 0

    def fetchone(self):
        return self._responses.pop(0) if self._responses else None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class IndexerCallbackServiceTests(unittest.TestCase):
    @staticmethod
    def _index_job(**overrides):
        job = {
            "job_id": 901,
            "stage": "index",
            "status": "sent",
            "action": "add",
            "ai_document_id": "aid_abcd1234",
            "object_id": 348,
            "revision_id": 101,
            "artifact_id": 55,
            "current_revision_id": 101,
            "indexed_revision_id": 100,
            "index_status": "pending",
            "doc_status": "active",
            "deletion_status": None,
        }
        job.update(overrides)
        return job

    @staticmethod
    def _delete_job(**overrides):
        job = {
            "job_id": 950,
            "stage": "delete",
            "status": "sent",
            "action": "delete",
            "ai_document_id": "aid_delete",
            "object_id": 349,
            "revision_id": None,
            "artifact_id": None,
            "current_revision_id": 103,
            "indexed_revision_id": 103,
            "index_status": "indexed",
            "doc_status": "deleted_pending",
            "deletion_status": "purging",
        }
        job.update(overrides)
        return job

    @patch.object(service, "_apply_success")
    @patch.object(service, "_find_job")
    @patch.object(service, "_link_event_job")
    @patch.object(service, "_reserve_event")
    @patch.object(service, "_verify_indexer_token", return_value={"sub": "indexer"})
    def test_success_callback_is_authenticated_and_applied(
        self,
        _verify,
        reserve,
        _link,
        find_job,
        apply_success,
    ) -> None:
        reserve.return_value = {"callback_event_id": 7, "duplicate": False}
        find_job.return_value = self._index_job()
        apply_success.return_value = {"ok": True, "indexed_revision_id": 101}
        payload = {
            "event_id": "indexer-event-1",
            "job_id": 901,
            "status": "success",
            "action": "add",
            "object_id": 348,
            "ai_document_id": "aid_abcd1234",
            "revision_id": 101,
            "artifact_id": 55,
        }

        result = service.apply_indexer_callback(payload, authorization="Bearer token")

        self.assertEqual(result["indexed_revision_id"], 101)
        apply_success.assert_called_once_with(7, find_job.return_value, "")

    @patch.object(service, "_reserve_event")
    @patch.object(service, "_verify_indexer_token", return_value={"sub": "indexer"})
    def test_duplicate_event_returns_previous_result(self, _verify, reserve) -> None:
        reserve.return_value = {
            "callback_event_id": 7,
            "duplicate": True,
            "processing_result": "applied",
            "result_reason": None,
        }
        result = service.apply_indexer_callback(
            {"event_id": "indexer-event-1", "job_id": 901},
            authorization="Bearer token",
        )
        self.assertTrue(result["duplicate"])

    def test_missing_bearer_token_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            service.apply_indexer_callback({}, authorization=None)
        self.assertEqual(raised.exception.status_code, 401)

    @patch.object(service, "db_transaction")
    def test_current_revision_success_updates_indexed_revision(self, transaction) -> None:
        cursor = _Cursor([{"status": "sent"}])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service._apply_success(7, self._index_job(), "index-task-1")

        sql = [" ".join(statement.split()) for statement, _params in cursor.executions]
        self.assertTrue(any("SET status='completed'" in statement for statement in sql))
        self.assertTrue(any("indexed_revision_id=%s" in statement for statement in sql))
        self.assertTrue(any("FOR UPDATE OF ai" in statement for statement in sql))
        self.assertEqual(result["index_status"], "indexed")
        self.assertEqual(result["indexed_revision_id"], 101)

    @patch.object(service, "db_transaction")
    def test_old_revision_success_does_not_replace_current_index(self, transaction) -> None:
        cursor = _Cursor([{"status": "sent"}])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service._apply_success(
            7,
            self._index_job(current_revision_id=102, indexed_revision_id=102),
            "index-task-old",
        )

        sql = [" ".join(statement.split()) for statement, _params in cursor.executions]
        self.assertFalse(any("indexed_revision_id=%s" in statement for statement in sql))
        self.assertTrue(result["ignored"])
        self.assertEqual(result["indexed_revision_id"], 102)

    @patch("app.services.features.minio_document_lifecycle_service.finalize_minio_purge_for_object")
    @patch.object(service, "db_transaction")
    def test_delete_success_resumes_backend_purge(self, transaction, finalize) -> None:
        cursor = _Cursor([{"status": "sent"}])
        transaction.return_value = nullcontext(_Connection(cursor))
        finalize.return_value = {"ok": True, "purged": True}

        result = service._apply_success(8, self._delete_job(), "delete-task-1")

        finalize.assert_called_once_with(349)
        self.assertEqual(result["purge"], {"ok": True, "purged": True})
        self.assertEqual(result["index_status"], "deleted")

    @patch("app.services.features.minio_document_lifecycle_service.finalize_minio_purge_for_object")
    @patch.object(service, "db_transaction")
    def test_late_delete_after_restore_is_ignored(self, transaction, finalize) -> None:
        cursor = _Cursor([{"status": "sent"}])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service._apply_success(
            8,
            self._delete_job(doc_status="active", deletion_status="restored"),
            "delete-task-late",
        )

        finalize.assert_not_called()
        self.assertTrue(result["ignored"])
        self.assertEqual(result["index_status"], "indexed")

    @patch.object(service, "db_transaction")
    def test_index_failure_uses_postgresql_update_from(self, transaction) -> None:
        cursor = _Cursor([{"status": "sent"}])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service._apply_failure(7, self._index_job(), "OpenSearch failed")

        sql = [" ".join(statement.split()) for statement, _params in cursor.executions]
        self.assertTrue(
            any(
                "UPDATE ai_document_info ai SET index_status='failed'" in statement
                and "FROM storage_objects so" in statement
                for statement in sql
            )
        )
        self.assertFalse(any("UPDATE ai_document_info ai JOIN" in statement for statement in sql))
        self.assertEqual(result["job_status"], "failed")
    @patch("app.services.features.minio_document_lifecycle_service.mark_minio_purge_index_failed")
    @patch.object(service, "db_transaction")
    def test_delete_failure_marks_purge_failed(self, transaction, mark_failed) -> None:
        cursor = _Cursor([{"status": "sent"}])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service._apply_failure(8, self._delete_job(), "OpenSearch failed")

        mark_failed.assert_called_once_with(349, "OpenSearch failed")
        self.assertEqual(result["job_status"], "failed")


if __name__ == "__main__":
    unittest.main()
