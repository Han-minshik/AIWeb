from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.features import ai_index_service as service


class AiIndexServiceTest(unittest.TestCase):
    @patch.object(service, "execute")
    @patch.object(service, "fetch_one")
    @patch.object(service, "_find_job")
    def test_late_delete_callback_for_restored_document_is_ignored(
        self,
        find_job,
        fetch_one,
        execute,
    ) -> None:
        find_job.return_value = {
            "job_id": 7,
            "object_id": 348,
            "ai_document_id": "aid_test",
            "action": "delete",
        }
        fetch_one.return_value = {"storage_backend": "minio", "doc_status": "active"}

        result = service.apply_ai_index_result({"job_id": 7, "status": "success"})

        self.assertTrue(result["ignored"])
        self.assertEqual(result["index_status"], "pending")
        self.assertIn("status='completed'", execute.call_args_list[0].args[0])
        self.assertEqual(execute.call_count, 1)

    @patch("app.services.features.minio_document_lifecycle_service.finalize_minio_purge_for_object")
    @patch.object(service, "execute")
    @patch.object(service, "fetch_one")
    @patch.object(service, "_find_job")
    def test_delete_success_completes_job_and_resumes_purge(
        self,
        find_job,
        fetch_one,
        execute,
        finalize,
    ) -> None:
        find_job.return_value = {
            "job_id": 8,
            "object_id": 349,
            "ai_document_id": "aid_delete",
            "action": "delete",
        }
        fetch_one.return_value = {"storage_backend": "minio", "doc_status": "deleted_pending"}
        finalize.return_value = {"ok": True, "purged": True}

        result = service.apply_ai_index_result({"job_id": 8, "status": "deleted"})

        self.assertEqual(result["job_status"], "success")
        self.assertEqual(result["purge"], {"ok": True, "purged": True})
        finalize.assert_called_once_with(349)
        self.assertIn("status='completed'", execute.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
