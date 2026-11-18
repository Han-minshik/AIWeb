from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.features import document_db


class AiMetadataJobTests(unittest.TestCase):
    def test_ready_manifest_creates_index_only_job(self):
        with (
            patch.object(
                document_db,
                "fetch_one",
                side_effect=[
                    {
                        "ai_document_id": "aid_1",
                        "source_revision_id": 7,
                        "index_status": "indexed",
                        "doc_status": "active",
                    },
                    {"artifact_id": 11},
                    None,
                ],
            ),
            patch.object(document_db, "execute") as execute,
            patch.object(document_db, "enqueue_ai_job") as parse_job,
        ):
            document_db.enqueue_ai_metadata_job(3, created_by=5)

        parse_job.assert_not_called()
        insert_sql, insert_params = execute.call_args_list[0].args
        self.assertIn("'index'", insert_sql)
        self.assertEqual(insert_params, ("aid_1", 3, 7, 11, 5))
        self.assertIn("index_status='pending'", execute.call_args_list[1].args[0])

    def test_pending_index_job_is_coalesced(self):
        with (
            patch.object(
                document_db,
                "fetch_one",
                side_effect=[
                    {
                        "ai_document_id": "aid_1",
                        "source_revision_id": 7,
                        "index_status": "indexed",
                        "doc_status": "active",
                    },
                    {"artifact_id": 12},
                    {"job_id": 19},
                ],
            ),
            patch.object(document_db, "execute") as execute,
        ):
            document_db.enqueue_ai_metadata_job(3, created_by=5)

        self.assertIn("UPDATE ai_index_jobs", execute.call_args_list[0].args[0])
        self.assertEqual(execute.call_args_list[0].args[1], (12, 5, 19))

    def test_active_parse_job_avoids_duplicate_fallback(self):
        with (
            patch.object(
                document_db,
                "fetch_one",
                side_effect=[
                    {
                        "ai_document_id": "aid_1",
                        "source_revision_id": 7,
                        "index_status": "pending",
                        "doc_status": "active",
                    },
                    None,
                    {"job_id": 20},
                ],
            ),
            patch.object(document_db, "enqueue_ai_job") as parse_job,
        ):
            document_db.enqueue_ai_metadata_job(3, created_by=5)

        parse_job.assert_not_called()

    def test_admin_subtree_code_change_enqueues_metadata_update(self):
        with (
            patch.object(document_db, "collect_descendants", return_value=[10, 11]),
            patch.object(
                document_db,
                "fetch_all",
                return_value=[
                    {
                        "doc_id": 3,
                        "old_item_type_code": 20,
                        "kb_type": "admin",
                        "index_status": "indexed",
                    }
                ],
            ),
            patch.object(document_db, "execute"),
            patch.object(document_db, "ensure_ai_document_info") as ensure_info,
            patch.object(document_db, "enqueue_ai_metadata_job") as enqueue_metadata,
        ):
            document_db.update_subtree_item_type(10, 30, created_by=5)

        ensure_info.assert_called_once_with(3, index_status="indexed")
        enqueue_metadata.assert_called_once_with(3, 5)

    def test_subtree_code_change_skips_unneeded_index_updates(self):
        with (
            patch.object(document_db, "collect_descendants", return_value=[10, 11, 12]),
            patch.object(
                document_db,
                "fetch_all",
                return_value=[
                    {
                        "doc_id": 3,
                        "old_item_type_code": 30,
                        "kb_type": "admin",
                        "index_status": "indexed",
                    },
                    {
                        "doc_id": 4,
                        "old_item_type_code": 20,
                        "kb_type": "personal",
                        "index_status": "indexed",
                    },
                    {
                        "doc_id": 5,
                        "old_item_type_code": 20,
                        "kb_type": "admin",
                        "index_status": "pending",
                    },
                ],
            ),
            patch.object(document_db, "execute"),
            patch.object(document_db, "ensure_ai_document_info"),
            patch.object(document_db, "enqueue_ai_metadata_job") as enqueue_metadata,
        ):
            document_db.update_subtree_item_type(10, 30, created_by=5)

        enqueue_metadata.assert_not_called()

if __name__ == "__main__":
    unittest.main()
