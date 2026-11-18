import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.services.features import docs_service


class DocumentRevisionReorderTests(unittest.TestCase):
    def _transaction(self, existing_ids):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"revision_id": revision_id} for revision_id in existing_ids
        ]
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection = MagicMock()
        connection.cursor.return_value = cursor_context
        transaction = MagicMock()
        transaction.__enter__.return_value = connection
        return transaction, cursor

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "_doc_node", return_value={"node_id": 9, "kb_type": "admin"})
    @patch.object(docs_service, "_storage_row", return_value={"object_id": 5319})
    def test_reorder_uses_collision_free_two_phase_update(
        self, _storage, _node, _access
    ):
        transaction, cursor = self._transaction([1580, 1579, 1578])
        with patch.object(docs_service, "db_transaction", return_value=transaction):
            result = docs_service.reorder_doc_revisions(
                5319,
                docs_service.RevisionsReorderIn(
                    revision_ids=[1579, 1580, 1578]
                ),
                user_id=1,
            )

        self.assertEqual(result, {"ok": True, "count": 3})
        self.assertEqual(
            cursor.execute.call_args_list[1].args,
            (
                "UPDATE doc_revisions SET rev_no=-rev_no WHERE doc_id=%s",
                (5319,),
            ),
        )
        cursor.executemany.assert_called_once_with(
            "UPDATE doc_revisions SET rev_no=%s WHERE doc_id=%s AND revision_id=%s",
            [(3, 5319, 1579), (2, 5319, 1580), (1, 5319, 1578)],
        )

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "_doc_node", return_value={"node_id": 9, "kb_type": "admin"})
    @patch.object(docs_service, "_storage_row", return_value={"object_id": 5319})
    def test_reorder_rejects_stale_revision_list(self, _storage, _node, _access):
        transaction, cursor = self._transaction([1580, 1579, 1578])
        with patch.object(docs_service, "db_transaction", return_value=transaction):
            with self.assertRaises(HTTPException) as raised:
                docs_service.reorder_doc_revisions(
                    5319,
                    docs_service.RevisionsReorderIn(revision_ids=[1580, 1579]),
                    user_id=1,
                )

        self.assertEqual(raised.exception.status_code, 409)
        cursor.executemany.assert_not_called()

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "_doc_node", return_value={"node_id": 9, "kb_type": "admin"})
    @patch.object(docs_service, "_storage_row", return_value={"object_id": 5319})
    def test_reorder_rejects_duplicate_revision_ids(self, _storage, _node, _access):
        with self.assertRaises(HTTPException) as raised:
            docs_service.reorder_doc_revisions(
                5319,
                docs_service.RevisionsReorderIn(
                    revision_ids=[1580, 1580, 1578]
                ),
                user_id=1,
            )

        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
