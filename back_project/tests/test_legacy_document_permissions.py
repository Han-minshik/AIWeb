import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.services.features import docs_service, docs_tree_service
from app.services.features.document_db import require_node_edit_access


class LegacyDocumentPermissionTests(unittest.TestCase):
    def test_personal_owner_can_edit(self):
        require_node_edit_access(7, {"kb_type": "personal", "kb_owner_user_id": 7})

    def test_other_user_cannot_edit_personal_node(self):
        with self.assertRaises(HTTPException) as raised:
            require_node_edit_access(8, {"kb_type": "personal", "kb_owner_user_id": 7})
        self.assertEqual(raised.exception.status_code, 403)

    @patch("app.services.features.document_db.is_admin", return_value=False)
    def test_non_admin_cannot_edit_admin_kb(self, _is_admin):
        with self.assertRaises(HTTPException) as raised:
            require_node_edit_access(8, {"kb_type": "admin", "kb_owner_user_id": None})
        self.assertEqual(raised.exception.status_code, 403)

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "_doc_node", return_value={"node_id": 9, "kb_type": "admin"})
    @patch.object(docs_service, "_storage_row", return_value={"object_id": 4})
    def test_update_document_checks_node_edit_access(self, _storage, _node, require_access):
        with patch.object(docs_service, "get_doc", return_value={"object_id": 4}):
            docs_service.update_doc(4, {}, user_id=3)
        require_access.assert_called_once_with(3, {"node_id": 9, "kb_type": "admin"})

    @patch.object(docs_tree_service, "execute")
    @patch.object(
        docs_tree_service,
        "get_node",
        return_value={"node_id": 9, "kb_type": "personal", "kb_owner_user_id": 7},
    )
    def test_legacy_batch_rename_rejects_other_personal_user(self, _node, execute):
        with self.assertRaises(HTTPException) as raised:
            docs_tree_service.batch_rename([{"node_id": 9, "name": "blocked"}], user_id=8)
        self.assertEqual(raised.exception.status_code, 403)
        execute.assert_not_called()

    @patch.object(docs_service, "require_node_edit_access")
    @patch.object(docs_service, "get_node", return_value={"node_id": 20, "kb_type": "admin"})
    def test_legacy_reorder_checks_parent_access(self, _node, require_access):
        docs_service.reorder_tree_nodes(
            {"parent_id": 20, "orders": [{"node_id": None, "sort_order": 1}]},
            user_id=3,
        )
        require_access.assert_called_once_with(3, {"node_id": 20, "kb_type": "admin"})


if __name__ == "__main__":
    unittest.main()
