import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.services.features import docs_service


def transaction_with(cursor):
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    transaction = MagicMock()
    transaction.__enter__.return_value = connection
    return transaction


class DocumentRevisionDeleteTests(unittest.TestCase):
    def revision(self, **overrides):
        row = {
            "revision_id": 1578,
            "doc_id": 5319,
            "rev_no": 1,
            "storage_status": "READY",
            "bucket_name": "spaceai-se-kb",
            "object_key": "documents/5319",
            "minio_version_id": "source-v1",
            "upload_mode": "admin_kb",
            "uploader_user_id": 1,
            "size_bytes": 100,
            "source_revision_id": 1580,
            "indexed_revision_id": None,
        }
        row.update(overrides)
        return row

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "_doc_node", return_value={"node_id": 9, "kb_type": "admin"})
    @patch.object(
        docs_service,
        "_storage_row",
        return_value={"object_id": 5319, "storage_backend": "minio"},
    )
    def test_deletes_old_revision_and_exact_minio_version(
        self, _storage_row, _node, _access
    ):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            self.revision(),
            {
                "revision_id": 1580,
                "minio_version_id": "source-v3",
                "checksum_sha256": "a" * 64,
                "size_bytes": 120,
                "source_created_at": None,
                "source_updated_at": None,
            },
        ]
        cursor.fetchall.side_effect = [
            [
                {"revision_id": 1580, "rev_no": 3},
                {"revision_id": 1579, "rev_no": 2},
                {"revision_id": 1578, "rev_no": 1},
            ],
            [
                {"revision_id": 1579},
                {"revision_id": 1580},
            ],
        ]
        storage = MagicMock()
        storage.permanent_delete_prefix.return_value = 2
        with (
            patch.object(docs_service, "db_transaction", return_value=transaction_with(cursor)),
            patch.object(docs_service, "get_minio_storage_service", return_value=storage),
        ):
            with patch.object(docs_service, "enqueue_ai_job") as enqueue:
                result = docs_service.delete_doc_revision(5319, 1578, user_id=1)

        storage.remove_version.assert_called_once_with(
            "spaceai-se-kb", "documents/5319", "source-v1"
        )
        self.assertEqual(result["remaining_count"], 2)
        self.assertEqual(result["deleted_artifact_versions"], 2)
        enqueue.assert_called_once_with(5319, "update", 1)

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "_doc_node", return_value={"node_id": 9, "kb_type": "admin"})
    @patch.object(
        docs_service,
        "_storage_row",
        return_value={"object_id": 5319, "storage_backend": "minio"},
    )
    def test_last_revision_is_protected(self, _storage_row, _node, _access):
        cursor = MagicMock()
        cursor.fetchone.return_value = self.revision(
            revision_id=1580, rev_no=1, source_revision_id=1580
        )
        cursor.fetchall.return_value = [{"revision_id": 1580, "rev_no": 1}]
        storage = MagicMock()
        with (
            patch.object(docs_service, "db_transaction", return_value=transaction_with(cursor)),
            patch.object(docs_service, "get_minio_storage_service", return_value=storage),
        ):
            with self.assertRaises(HTTPException) as raised:
                docs_service.delete_doc_revision(5319, 1580, user_id=1)

        self.assertEqual(raised.exception.status_code, 409)
        storage.remove_version.assert_not_called()

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "_doc_node", return_value={"node_id": 9, "kb_type": "admin"})
    @patch.object(
        docs_service,
        "_storage_row",
        return_value={"object_id": 5319, "storage_backend": "minio"},
    )
    def test_current_revision_is_deleted_and_previous_is_promoted(
        self, _storage_row, _node, _access
    ):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            self.revision(revision_id=1580, rev_no=3, source_revision_id=1580),
            {
                "revision_id": 1579,
                "minio_version_id": "source-v2",
                "checksum_sha256": "b" * 64,
                "size_bytes": 90,
                "source_created_at": None,
                "source_updated_at": None,
            },
        ]
        cursor.fetchall.side_effect = [
            [
                {"revision_id": 1580, "rev_no": 3},
                {"revision_id": 1579, "rev_no": 2},
            ],
            [{"revision_id": 1579}],
        ]
        storage = MagicMock()
        storage.permanent_delete_prefix.return_value = 1
        with (
            patch.object(docs_service, "db_transaction", return_value=transaction_with(cursor)),
            patch.object(docs_service, "get_minio_storage_service", return_value=storage),
            patch.object(docs_service, "enqueue_ai_job") as enqueue,
        ):
            result = docs_service.delete_doc_revision(5319, 1580, user_id=1)

        storage.remove_version.assert_called_once_with(
            "spaceai-se-kb", "documents/5319", "source-v1"
        )
        enqueue.assert_called_once_with(5319, "update", 1)
        self.assertEqual(result["current_revision_id"], 1579)
        self.assertEqual(result["remaining_count"], 1)


if __name__ == "__main__":
    unittest.main()
