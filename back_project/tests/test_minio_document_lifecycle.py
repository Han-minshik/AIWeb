from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from app.services.features import minio_document_lifecycle_service as service


class MinioDocumentLifecycleTest(unittest.TestCase):
    def test_index_delete_not_required_when_no_index_request_was_sent(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = {"required": 0}

        required = service._index_delete_required(cursor, "aid_test")

        self.assertFalse(required)
        sql, params = cursor.execute.call_args.args
        self.assertIn("j.stage='index'", sql)
        self.assertIn("j.status IN ('running','sent','completed')", sql)
        self.assertIn("j.started_at IS NOT NULL", sql)
        self.assertEqual(params, ("aid_test", "aid_test"))

    def test_index_delete_required_when_index_request_or_revision_exists(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = {"required": 1}

        self.assertTrue(service._index_delete_required(cursor, "aid_test"))

    @patch.object(service, "finalize_minio_purge_for_object")
    @patch.object(service, "_fetch_deletion")
    @patch.object(service, "db_transaction")
    def test_purge_without_index_history_finalizes_immediately(
        self,
        db_transaction,
        fetch_deletion,
        finalize,
    ) -> None:
        fetch_deletion.return_value = {
            "deletion_id": 47,
            "object_id": 5260,
            "ai_document_id": "aid_test",
            "status": "pending",
            "storage_backend": "minio",
        }
        transaction = MagicMock()
        connection = MagicMock()
        cursor = MagicMock()
        db_transaction.return_value = transaction
        transaction.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = [{"status": "pending"}, {"required": 0}]
        finalize.return_value = {"ok": True, "object_id": 5260, "purged": True}

        result = service.request_minio_purge(47, actor_user_id=1)

        self.assertTrue(result["purged"])
        finalize.assert_called_once_with(5260, actor_user_id=1, require_index_delete=False)
        executed_sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertNotIn("INSERT INTO ai_index_jobs", executed_sql)
        self.assertIn("action='delete'", executed_sql)

    @patch.object(service, "_fetch_deletion")
    @patch.object(service, "db_transaction")
    def test_purge_in_progress_is_an_idempotent_replay(
        self,
        db_transaction,
        fetch_deletion,
    ) -> None:
        fetch_deletion.return_value = {
            "deletion_id": 47,
            "object_id": 5260,
            "status": "purging",
            "storage_backend": "minio",
        }
        transaction = MagicMock()
        connection = MagicMock()
        cursor = MagicMock()
        db_transaction.return_value = transaction
        transaction.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {"status": "purging"}

        result = service.request_minio_purge(47, actor_user_id=1)

        self.assertTrue(result["idempotent_replay"])
        self.assertEqual(result["status"], "purging")
        self.assertEqual(cursor.execute.call_count, 1)

    def test_deleted_document_archive_sql_parameters_match(self) -> None:
        cursor = MagicMock()
        row = {
            "object_id": 348,
            "node_id": 901,
            "parent_id": 900,
            "kb_root_id": 800,
            "ai_document_id": "aid_test",
            "doc_name": "sample",
            "alias": "sample.pdf",
            "relative_path": "folder",
            "sub_path": "",
            "file_right": "pdf",
            "doc_code": "",
            "item_type_code": 10,
            "branch_type": "general",
            "checksum_sha256": "a" * 64,
            "source_created_at": None,
            "source_updated_at": None,
            "size_bytes": 100,
            "object_key": "users/15/documents/348",
            "upload_mode": "personal",
            "uploader_user_id": 15,
            "owner_user_id": 15,
            "target_group_id": None,
            "policy_code": "PRIVATE",
            "sort_order": 0,
            "detected_at": "2026-08-28 10:00:00",
            "reason": "manual delete",
        }

        service._archive(cursor, row, actor_user_id=15)

        sql, params = cursor.execute.call_args.args
        self.assertEqual(sql.count("%s"), len(params))
    @patch.object(service, "get_minio_storage_service")
    def test_personal_purge_deletes_revision_prefixes_before_source(self, get_storage) -> None:
        storage = MagicMock()
        storage.permanent_delete_prefix.side_effect = [3, 2]
        storage.permanent_delete_object.return_value = 4
        get_storage.return_value = storage

        result = service._delete_minio_payload(
            {
                "object_id": 348,
                "upload_mode": "personal",
                "uploader_user_id": 15,
                "bucket_name": "spaceai-personal",
                "object_key": "users/15/documents/348",
            },
            [101, 102],
        )

        self.assertEqual(result, {"source_versions": 4, "artifact_versions": 5})
        self.assertEqual(
            storage.method_calls,
            [
                call.permanent_delete_prefix(
                    "spaceai-personal-artifacts",
                    "users/15/documents/348/revisions/101/",
                ),
                call.permanent_delete_prefix(
                    "spaceai-personal-artifacts",
                    "users/15/documents/348/revisions/102/",
                ),
                call.permanent_delete_object(
                    "spaceai-personal",
                    "users/15/documents/348",
                ),
            ],
        )

    @patch.object(service, "get_minio_storage_service")
    def test_se_purge_uses_se_artifact_bucket(self, get_storage) -> None:
        storage = MagicMock()
        storage.permanent_delete_prefix.return_value = 1
        storage.permanent_delete_object.return_value = 1
        get_storage.return_value = storage

        service._delete_minio_payload(
            {
                "object_id": 9,
                "upload_mode": "admin_kb",
                "uploader_user_id": 1,
                "bucket_name": "spaceai-se-kb",
                "object_key": "documents/9",
            },
            [33],
        )

        storage.permanent_delete_prefix.assert_called_once_with(
            "spaceai-se-kb-artifacts",
            "documents/9/revisions/33/",
        )


if __name__ == "__main__":
    unittest.main()
