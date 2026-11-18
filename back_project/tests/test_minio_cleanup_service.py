from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.features import minio_cleanup_service as service


class MinioCleanupServiceTest(unittest.TestCase):
    @patch.object(service.minio_document_lifecycle_service, "list_resumable_minio_purges")
    @patch.object(service, "_fetch_candidates")
    def test_dry_run_only_counts_and_protects_active_deletions(self, fetch_candidates, list_purges) -> None:
        fetch_candidates.side_effect = [
            [{"revision_id": 1}],
            [{"artifact_id": 2}],
            [{"callback_event_id": 3}],
            [{"job_id": 4}],
        ]
        list_purges.return_value = [99]

        result = service.run_cleanup(dry_run=True, batch_size=10)

        self.assertEqual(
            result["candidates"],
            {"failed_uploads": 1, "artifacts": 1, "purges": 1, "callbacks": 1, "jobs": 1},
        )
        self.assertEqual(sum(result["deleted"].values()), 0)
        artifact_sql = fetch_candidates.call_args_list[1].args[0]
        callback_sql = fetch_candidates.call_args_list[2].args[0]
        job_sql = fetch_candidates.call_args_list[3].args[0]
        self.assertIn("so.doc_status='active'", artifact_sql)
        self.assertIn("'pending','purging','purge_failed'", artifact_sql)
        self.assertIn("'pending','purging','purge_failed'", callback_sql)
        self.assertIn("'pending','purging','purge_failed'", job_sql)


if __name__ == "__main__":
    unittest.main()
