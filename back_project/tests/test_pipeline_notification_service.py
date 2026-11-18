from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.features import pipeline_notification_service as service


class PipelineNotificationServiceTests(unittest.TestCase):
    @patch.object(service.notifications_service, "emit_notification")
    def test_failure_notification_contains_stage_and_reason(self, emit):
        service.notify_pipeline_result(
            {
                "job_id": 9,
                "object_id": 20,
                "revision_id": 30,
                "doc_name": "manual.pdf",
                "created_by": 7,
            },
            stage="parse",
            success=False,
            message="encrypted file",
        )

        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 7)
        self.assertEqual(kwargs["severity"], "error")
        self.assertEqual(kwargs["title"], "문서 파싱 실패")
        self.assertIn("reason=encrypted file", kwargs["body"])

    @patch.object(service.notifications_service, "emit_notification")
    def test_missing_recipient_does_not_emit(self, emit):
        service.notify_pipeline_result(
            {"job_id": 9}, stage="index", success=True
        )
        emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()