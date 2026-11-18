from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.features import docs_tree_service


class DocsTreeSearchTests(unittest.TestCase):
    def test_document_name_search_is_case_insensitive(self):
        root = {"node_id": 1, "kb_root_id": 1, "kb_type": "admin"}
        with (
            patch.object(docs_tree_service, "get_kb_entry", return_value=root),
            patch.object(docs_tree_service, "require_kb_read_access"),
            patch.object(docs_tree_service, "collect_descendants", return_value={1}),
            patch.object(docs_tree_service, "fetch_all", return_value=[]) as fetch_all,
            patch.object(docs_tree_service, "is_admin", return_value=False),
            patch.object(docs_tree_service, "active_group_ids_for_user", return_value=set()),
        ):
            result = docs_tree_service.list_file_nodes(
                kb_id=1,
                parent_id=None,
                q="ecss",
                admin_view=False,
                user_id=7,
            )

        query, params = fetch_all.call_args.args
        self.assertIn(
            "(n.alias ILIKE %s OR n.name ILIKE %s OR so.doc_name ILIKE %s)",
            query,
        )
        self.assertEqual(params[-3:], ("%ecss%", "%ecss%", "%ecss%"))
        self.assertEqual(result["items"], [])


if __name__ == "__main__":
    unittest.main()
