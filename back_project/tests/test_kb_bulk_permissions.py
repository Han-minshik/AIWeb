from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.services.features import kb_service


def folder(node_id: int, parent_id: int, name: str) -> dict:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "kb_root_id": 1,
        "kb_type": "admin",
        "is_folder": True,
        "name": name,
        "alias": name,
    }


def document(node_id: int, parent_id: int, name: str) -> dict:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "kb_root_id": 1,
        "kb_type": "admin",
        "is_folder": False,
        "doc_id": node_id + 100,
        "name": name,
        "alias": name,
        "doc_status": "active",
    }


class KbFolderVisibilityTests(unittest.TestCase):
    def _tree(self, rows: list[dict], visible_ids: set[int]) -> dict:
        root = {"node_id": 1, "kb_root_id": 1, "kb_type": "admin"}
        with (
            patch.object(kb_service, "get_kb_entry", return_value=root),
            patch.object(kb_service, "_fetch_kb_rows", return_value=rows),
            patch.object(kb_service, "is_admin", return_value=False),
            patch.object(kb_service, "active_group_ids_for_user", return_value=set()),
            patch.object(
                kb_service,
                "row_has_document_permission",
                side_effect=lambda row, *_args, **_kwargs: int(row["node_id"]) in visible_ids,
            ),
        ):
            return kb_service.get_kb_tree(1, include_empty_folders=False, admin_view=False, user_id=7)

    def test_genuinely_empty_folder_is_visible(self):
        result = self._tree([folder(10, 1, "Empty")], set())
        self.assertEqual([node["name"] for node in result["nodes"]], ["Empty"])

    def test_folder_with_only_inaccessible_documents_is_hidden(self):
        result = self._tree(
            [folder(10, 1, "Restricted"), document(11, 10, "Secret")],
            set(),
        )
        self.assertEqual(result["nodes"], [])

    def test_folder_with_visible_document_keeps_only_visible_content(self):
        result = self._tree(
            [
                folder(10, 1, "Mixed"),
                document(11, 10, "Visible"),
                document(12, 10, "Secret"),
            ],
            {11},
        )
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(
            [node["name"] for node in result["nodes"][0]["children"]],
            ["Visible"],
        )

    def test_restricted_document_hides_its_ancestor_even_with_empty_child(self):
        result = self._tree(
            [
                folder(10, 1, "Restricted parent"),
                folder(11, 10, "Empty child"),
                document(12, 10, "Secret"),
            ],
            set(),
        )
        self.assertEqual(result["nodes"], [])

    def test_empty_nested_folder_path_is_visible(self):
        result = self._tree(
            [
                folder(10, 1, "Empty parent"),
                folder(11, 10, "Empty child"),
            ],
            set(),
        )
        self.assertEqual(result["nodes"][0]["children"][0]["name"], "Empty child")

    def test_kb_list_hides_restricted_only_folder_and_keeps_empty_folder(self):
        entries = [
            {"kb_id": 10, "kb_root_id": 1, "item_type_codes": [10]},
            {"kb_id": 20, "kb_root_id": 1, "item_type_codes": []},
        ]
        rows = [
            folder(10, 1, "Restricted"),
            document(11, 10, "Secret"),
            folder(20, 1, "Empty"),
        ]
        with (
            patch.object(kb_service, "is_admin", return_value=False),
            patch.object(kb_service, "active_group_ids_for_user", return_value=set()),
            patch.object(kb_service, "_fetch_kb_rows", return_value=rows) as fetch_rows,
            patch.object(kb_service, "row_has_document_permission", return_value=False) as permission,
        ):
            result = kb_service._filter_kb_entries_by_document_visibility(entries, 7)

        self.assertEqual([entry["kb_id"] for entry in result], [20])
        self.assertEqual(fetch_rows.call_count, 1)
        self.assertEqual(permission.call_count, 1)

    def test_kb_list_exposes_only_readable_document_type_codes(self):
        entries = [{"kb_id": 10, "kb_root_id": 1, "item_type_codes": [10, 20]}]
        rows = [
            folder(10, 1, "Mixed"),
            {**document(11, 10, "Visible"), "item_type_code": 10},
            {**document(12, 10, "Secret"), "item_type_code": 20},
        ]
        with (
            patch.object(kb_service, "is_admin", return_value=False),
            patch.object(kb_service, "active_group_ids_for_user", return_value=set()),
            patch.object(kb_service, "_fetch_kb_rows", return_value=rows),
            patch.object(
                kb_service,
                "row_has_document_permission",
                side_effect=lambda row, *_args, **_kwargs: row["node_id"] == 11,
            ),
        ):
            result = kb_service._filter_kb_entries_by_document_visibility(entries, 7)

        self.assertEqual(result[0]["item_type_codes"], [10])


class KbBulkPermissionTests(unittest.TestCase):
    def test_single_permission_change_queues_metadata_index(self):
        row = {
            "node_id": 11,
            "doc_id": 101,
            "kb_type": "admin",
            "is_folder": False,
            "created_by": 2,
        }
        with (
            patch.object(kb_service, "get_node", return_value=row),
            patch.object(kb_service, "_require_node_edit_access"),
            patch.object(kb_service, "_require_admin"),
            patch.object(
                kb_service,
                "fetch_one",
                side_effect=[
                    {
                        "policy_code": "PUBLIC",
                        "group_r": False,
                        "group_w": False,
                        "group_x": False,
                        "other_r": True,
                        "other_w": False,
                        "other_x": False,
                    },
                    {"owner_user_id": 2, "target_group_id": None},
                ],
            ),
            patch.object(kb_service, "ensure_acl") as ensure_acl,
            patch.object(kb_service, "ensure_ai_document_info") as ensure_info,
            patch.object(kb_service, "enqueue_ai_metadata_job") as enqueue_metadata,
        ):
            result = kb_service.patch_kb_node(
                11,
                kb_service.KBNodePatch(policy_code="PUBLIC"),
                user_id=1,
            )

        self.assertEqual(result["message"], "Updated")
        ensure_acl.assert_called_once_with(
            11,
            "PUBLIC",
            owner_user_id=2,
            target_group_id=None,
        )
        ensure_info.assert_called_once_with(101)
        enqueue_metadata.assert_called_once_with(101, 1)

    def test_bulk_permission_updates_selected_documents_in_one_transaction(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"node_id": 11, "doc_id": 101, "created_by": 2, "owner_user_id": None},
            {"node_id": 12, "doc_id": 102, "created_by": 3, "owner_user_id": 9},
        ]
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        @contextmanager
        def transaction():
            yield connection

        with (
            patch.object(
                kb_service,
                "get_kb_entry",
                return_value={"node_id": 1, "kb_type": "admin"},
            ),
            patch.object(kb_service, "_require_admin"),
            patch.object(
                kb_service,
                "fetch_one",
                return_value={
                    "policy_code": "PUBLIC",
                    "group_r": False,
                    "group_w": False,
                    "group_x": False,
                    "other_r": True,
                    "other_w": False,
                    "other_x": False,
                },
            ),
            patch.object(kb_service, "db_transaction", transaction),
            patch.object(kb_service, "ensure_ai_document_info") as ensure_info,
            patch.object(kb_service, "enqueue_ai_metadata_job") as enqueue_metadata,
        ):
            result = kb_service.update_kb_permissions_bulk(
                1,
                kb_service.KBPermissionsBulkIn(
                    node_ids=[11, 12],
                    policy_code="public",
                ),
                user_id=1,
            )

        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(cursor.execute.call_count, 3)
        self.assertEqual(ensure_info.call_count, 2)
        self.assertEqual(enqueue_metadata.call_count, 2)
        enqueue_metadata.assert_any_call(101, 1)
        enqueue_metadata.assert_any_call(102, 1)

    def test_group_policy_requires_target_group(self):
        with (
            patch.object(
                kb_service,
                "get_kb_entry",
                return_value={"node_id": 1, "kb_type": "admin"},
            ),
            patch.object(kb_service, "_require_admin"),
            patch.object(
                kb_service,
                "fetch_one",
                return_value={
                    "policy_code": "GROUP",
                    "group_r": True,
                    "group_w": False,
                    "group_x": False,
                    "other_r": False,
                    "other_w": False,
                    "other_x": False,
                },
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                kb_service.update_kb_permissions_bulk(
                    1,
                    kb_service.KBPermissionsBulkIn(
                        node_ids=[11],
                        policy_code="GROUP",
                    ),
                    user_id=1,
                )
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
