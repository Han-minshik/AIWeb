from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from app.services.features import pipeline_job_service as service


class _Cursor:
    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])
        self.executions = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.executions.append((sql, params))
        self.rowcount = 1 if (
            "UPDATE ai_index_jobs" in sql or "INSERT INTO ai_index_jobs" in sql
        ) else 0

    def fetchone(self):
        return self.responses.pop(0) if self.responses else None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class PipelineJobServiceTests(unittest.TestCase):
    @staticmethod
    def _job(**overrides):
        row = {
            "job_id": 10,
            "ai_document_id": "aid_10",
            "object_id": 20,
            "revision_id": 30,
            "artifact_id": None,
            "action": "update",
            "stage": "parse",
            "status": "failed",
            "current_revision_id": 30,
            "doc_status": "active",
            "revision_storage_status": "READY",
            "artifact_status": None,
            "deletion_status": None,
            "peer_job_count": 0,
            "upload_mode": "admin_kb",
            "kb_type": "admin",
            "uploader_user_id": 1,
            "kb_owner_user_id": None,
            "user_id": 1,
        }
        row.update(overrides)
        return row

    def test_current_ready_revision_can_be_retried(self):
        allowed, next_status, reason = service.retry_eligibility(self._job())
        self.assertTrue(allowed)
        self.assertEqual(next_status, "pending")
        self.assertEqual(reason, "")

    def test_stale_sent_job_can_be_retried(self):
        allowed, next_status, reason = service.retry_eligibility(
            self._job(status="sent", status_age_seconds=service.SENT_RETRY_AFTER_SECONDS)
        )
        self.assertTrue(allowed)
        self.assertEqual(next_status, "pending")
        self.assertEqual(reason, "")

    def test_recent_sent_job_waits_for_callback(self):
        allowed, next_status, reason = service.retry_eligibility(
            self._job(status="sent", status_age_seconds=10)
        )
        self.assertFalse(allowed)
        self.assertEqual(next_status, "sent")
        self.assertIn("Waiting for callback", reason)

    def test_old_revision_is_superseded(self):
        allowed, next_status, _ = service.retry_eligibility(
            self._job(current_revision_id=31)
        )
        self.assertFalse(allowed)
        self.assertEqual(next_status, "superseded")

    def test_restored_delete_is_cancelled(self):
        allowed, next_status, _ = service.retry_eligibility(
            self._job(
                stage="delete",
                action="delete",
                revision_id=None,
                current_revision_id=30,
                deletion_status="restored",
                doc_status="active",
            )
        )
        self.assertFalse(allowed)
        self.assertEqual(next_status, "cancelled")

    @patch.object(service, "db_transaction")
    def test_retry_is_atomic_and_returns_job_to_queue(self, transaction):
        cursor = _Cursor([self._job(), {"job_id": 11}])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service.retry_jobs([10], actor_user_id=1, is_admin=True)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["items"][0]["job_id"], 10)
        self.assertEqual(result["items"][0]["retry_job_id"], 11)
        statements = [sql for sql, _ in cursor.executions]
        self.assertTrue(any("FOR UPDATE OF j" in sql for sql in statements))
        self.assertTrue(any("status='superseded'" in sql for sql in statements))
        self.assertTrue(any("INSERT INTO ai_index_jobs" in sql for sql in statements))
        self.assertTrue(any("UPDATE ai_document_info" in sql for sql in statements))

    @patch.object(service, "db_transaction")
    def test_stale_sent_retry_supersedes_old_job_and_creates_new_job(self, transaction):
        cursor = _Cursor([
            self._job(status="sent", status_age_seconds=service.SENT_RETRY_AFTER_SECONDS + 1),
            {"job_id": 12},
        ])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service.retry_jobs([10], actor_user_id=1, is_admin=True)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["items"][0]["retry_job_id"], 12)
        supersede = next(
            (sql, params) for sql, params in cursor.executions
            if "last_error='Replaced by a manual retry'" in sql
        )
        self.assertEqual(supersede[1], (10, "sent"))

    @patch.object(service, "db_transaction")
    def test_admin_cannot_retry_another_users_personal_job(self, transaction):
        cursor = _Cursor([
            self._job(upload_mode="personal", kb_type="personal", uploader_user_id=9, user_id=9)
        ])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service.retry_jobs([10], actor_user_id=1, is_admin=True)

        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["items"][0]["status_code"], 404)

    def test_archived_personal_job_keeps_personal_scope_and_owner(self):
        row = self._job(
            upload_mode=None,
            kb_type=None,
            uploader_user_id=None,
            kb_owner_user_id=None,
            user_id=None,
            archived_personal=True,
            archived_owner_user_id=9,
        )

        self.assertTrue(service._is_personal(row))
        self.assertTrue(service._can_access(row, actor_user_id=9, is_admin=False))
        self.assertFalse(service._can_access(row, actor_user_id=1, is_admin=True))

    @patch.object(service, "db_transaction")
    @patch.object(service, "fetch_all")
    def test_restart_recovers_only_valid_running_job(self, fetch_all, transaction):
        fetch_all.return_value = [{"job_id": 10}]
        cursor = _Cursor([self._job(status="running")])
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service.recover_interrupted_jobs()

        self.assertEqual(result["pending"], 1)
        self.assertTrue(any("status='pending'" in sql for sql, _ in cursor.executions))

    @patch.object(service, "fetch_all", return_value=[])
    def test_sent_jobs_are_not_selected_for_automatic_recovery(self, fetch_all):
        service.recover_interrupted_jobs()
        sql = fetch_all.call_args.args[0]
        self.assertIn("status='running'", sql)
        self.assertNotIn("status='sent'", sql)


    @patch.object(service, "fetch_all", return_value=[])
    def test_personal_scope_filters_to_owner(self, fetch_all):
        service.list_jobs(
            actor_user_id=7,
            is_admin=True,
            status="all",
            scope="personal",
        )
        sql, params = fetch_all.call_args.args
        self.assertIn("upload_mode,'')='personal'", sql)
        self.assertIn("FROM deleted_documents archived", sql)
        self.assertEqual(params[-2:], (7, 200))

    @patch.object(service, "fetch_all", return_value=[])
    def test_se_scope_requires_admin_and_excludes_personal(self, fetch_all):
        service.list_jobs(
            actor_user_id=7,
            is_admin=True,
            status="all",
            scope="se",
        )
        sql, params = fetch_all.call_args.args
        self.assertIn("NOT", sql)
        self.assertIn("upload_mode,'')='personal'", sql)
        self.assertIn("FROM deleted_documents archived", sql)
        self.assertEqual(params, (200,))


if __name__ == "__main__":
    unittest.main()
