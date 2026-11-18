from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.services.common.minio_storage_service import ObjectMetadata
from app.services.features import parser_callback_service as service


class _Cursor:
    def __init__(self, existing_index_job=None) -> None:
        self.executions = []
        self._fetchone = existing_index_job

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.executions.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO ai_parse_artifacts"):
            self._fetchone = {"artifact_id": 55}
        elif normalized.startswith("INSERT INTO ai_index_jobs"):
            self._fetchone = {"job_id": 88}

    def fetchone(self):
        value = self._fetchone
        self._fetchone = None
        return value


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class ParserCallbackServiceTests(unittest.TestCase):
    @staticmethod
    def _job(**overrides):
        job = {
            "job_id": 900,
            "stage": "parse",
            "status": "sent",
            "action": "add",
            "ai_document_id": "aid_abcd1234",
            "object_id": 348,
            "revision_id": 101,
            "current_revision_id": 101,
            "doc_status": "active",
            "upload_mode": "personal",
            "uploader_user_id": 12,
            "kb_type": "personal",
            "created_by": 12,
        }
        job.update(overrides)
        return job

    @staticmethod
    def _artifact(**overrides):
        artifact = {
            "artifact_type": "manifest",
            "primary": True,
            "bucket_name": "spaceai-personal-artifacts",
            "object_key": "users/12/documents/348/revisions/101/manifest.json",
            "minio_version_id": "artifact-version-1",
            "size_bytes": 1820,
            "checksum_sha256": "a" * 64,
        }
        artifact.update(overrides)
        return artifact

    @patch("app.services.features.pipeline_notification_service.notify_pipeline_result")
    @patch.object(service, "_apply_success_result")
    @patch.object(service, "_verify_artifacts")
    @patch.object(service, "_find_parse_job")
    @patch.object(service, "_reserve_event")
    @patch.object(service, "_verify_parser_token", return_value={"sub": "parser"})
    def test_success_callback_verifies_and_applies_once(
        self,
        _verify_token,
        reserve_event,
        find_job,
        verify_artifacts,
        apply_success,
        notify_result,
    ) -> None:
        reserve_event.return_value = {"callback_event_id": 3, "duplicate": False}
        find_job.return_value = self._job()
        verified = [self._artifact()]
        verify_artifacts.return_value = verified
        apply_success.return_value = {"ok": True, "index_job_id": 88}
        payload = {
            "event_id": "parser-event-1",
            "job_id": 900,
            "status": "success",
            "object_id": 348,
            "ai_document_id": "aid_abcd1234",
            "revision_id": 101,
            "parser_version": "parser-2.1",
            "artifacts": [self._artifact()],
        }

        result = service.apply_parser_callback(payload, authorization="Bearer token")

        self.assertEqual(result["index_job_id"], 88)
        verify_artifacts.assert_called_once()
        apply_success.assert_called_once_with(3, find_job.return_value, verified, "parser-2.1", "")
        notify_result.assert_called_once()

    @patch.object(service, "_reserve_event")
    @patch.object(service, "_verify_parser_token", return_value={"sub": "parser"})
    def test_duplicate_applied_event_returns_without_reprocessing(self, _verify, reserve_event) -> None:
        reserve_event.return_value = {
            "callback_event_id": 3,
            "duplicate": True,
            "processing_result": "applied",
            "result_reason": None,
        }
        result = service.apply_parser_callback(
            {"event_id": "event-1", "job_id": 900},
            authorization="Bearer token",
        )
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["event_result"], "applied")

    def test_missing_bearer_token_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            service.apply_parser_callback({}, authorization=None)
        self.assertEqual(raised.exception.status_code, 401)

    @patch.object(service, "get_minio_storage_service")
    def test_artifact_must_be_inside_assigned_revision_prefix(self, get_storage) -> None:
        with self.assertRaises(HTTPException) as raised:
            service._verify_artifacts(
                self._job(),
                [self._artifact(object_key="users/12/documents/999/revisions/101/manifest.json")],
            )
        self.assertEqual(raised.exception.status_code, 422)
        get_storage.return_value.stat_object.assert_not_called()

    @patch.object(service, "get_minio_storage_service")
    def test_artifact_exact_version_and_size_are_verified(self, get_storage) -> None:
        get_storage.return_value.stat_object.return_value = ObjectMetadata(
            bucket_name="spaceai-personal-artifacts",
            object_key="users/12/documents/348/revisions/101/manifest.json",
            version_id="artifact-version-1",
            etag="etag",
            size_bytes=1820,
            content_type="application/json",
            metadata={"x-amz-meta-sha256": "a" * 64},
        )
        result = service._verify_artifacts(self._job(), [self._artifact()])
        self.assertEqual(result[0]["size_bytes"], 1820)
        get_storage.return_value.stat_object.assert_called_once_with(
            "spaceai-personal-artifacts",
            "users/12/documents/348/revisions/101/manifest.json",
            version_id="artifact-version-1",
        )

    @patch.object(service, "db_transaction")
    def test_success_transaction_completes_parse_and_creates_index_job(self, transaction) -> None:
        cursor = _Cursor(existing_index_job=None)
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service._apply_success_result(
            3,
            self._job(),
            [self._artifact()],
            "parser-2.1",
            "parser-task-7",
        )

        sql = [" ".join(statement.split()) for statement, _params in cursor.executions]
        self.assertTrue(any("INSERT INTO ai_parse_artifacts" in statement for statement in sql))
        self.assertTrue(any("SET status='completed'" in statement for statement in sql))
        self.assertTrue(any("INSERT INTO ai_index_jobs" in statement for statement in sql))
        self.assertEqual(result["primary_artifact_id"], 55)
        self.assertEqual(result["index_job_id"], 88)

    @patch.object(service, "db_transaction")
    def test_superseded_revision_keeps_artifact_but_does_not_index(self, transaction) -> None:
        cursor = _Cursor(existing_index_job=None)
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service._apply_success_result(
            3,
            self._job(current_revision_id=102),
            [self._artifact()],
            "parser-2.1",
            "parser-task-8",
        )

        sql = [" ".join(statement.split()) for statement, _params in cursor.executions]
        self.assertTrue(any("INSERT INTO ai_parse_artifacts" in statement for statement in sql))
        self.assertFalse(any("INSERT INTO ai_index_jobs" in statement for statement in sql))
        self.assertTrue(result["ignored"])
        self.assertIsNone(result["index_job_id"])


if __name__ == "__main__":
    unittest.main()
