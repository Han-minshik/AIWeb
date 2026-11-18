from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.features import document_metadata_service as service


class _Cursor:
    def __init__(self, rows):
        self.rows = {int(key): dict(value) for key, value in rows.items()}
        self.current = None

    def execute(self, _query, params=None):
        self.current = self.rows.get(int(params[0])) if params else None

    def fetchone(self):
        return self.current


class DocumentMetadataServiceTests(unittest.TestCase):
    def test_normalize_general_schema(self) -> None:
        self.assertEqual(service.normalize_general_schema("1. Standard"), "standard")
        self.assertEqual(
            service.normalize_general_schema("3. Law Directive"), "law_directive"
        )
        self.assertEqual(
            service.normalize_general_schema("4. Research-Paper"), "research_paper"
        )

    def test_empty_general_schema_is_rejected(self) -> None:
        with self.assertRaises(service.DocumentMetadataResolutionError):
            service.normalize_general_schema("1. ---")

    def test_overlong_general_schema_is_rejected(self) -> None:
        with self.assertRaises(service.DocumentMetadataResolutionError):
            service.normalize_general_schema("A" * 101)

    def test_resolves_kb_root_child_from_nested_parent(self) -> None:
        cursor = _Cursor({
            30: {
                "node_id": 30,
                "parent_id": 20,
                "kb_root_id": 10,
                "name": "Nested",
                "alias": "Nested",
                "is_folder": True,
            },
            20: {
                "node_id": 20,
                "parent_id": 10,
                "kb_root_id": 10,
                "name": "1. Standard",
                "alias": "1. Standard",
                "is_folder": True,
            },
        })
        self.assertEqual(service.resolve_general_schema_for_parent(cursor, 30), "standard")


    def test_root_documents_use_kb_type_not_display_name(self) -> None:
        for kb_type, expected in (("admin", "general"), ("personal", "personal"), ("person", "personal")):
            with self.subTest(kb_type=kb_type):
                cursor = _Cursor({8: {
                    "node_id": 8, "parent_id": None, "kb_root_id": 8,
                    "kb_type": kb_type, "name": "admin", "alias": "임의 루트 이름",
                    "is_folder": True,
                }})
                self.assertEqual(service.resolve_general_schema_for_parent(cursor, 8), expected)

    def test_personal_subfolder_uses_top_level_folder_schema(self) -> None:
        cursor = _Cursor({
            12: {"node_id": 12, "parent_id": 11, "kb_root_id": 8,
                 "kb_type": "personal", "name": "Nested", "is_folder": True},
            11: {"node_id": 11, "parent_id": 8, "kb_root_id": 8,
                 "kb_type": "personal", "name": "7. Research Paper",
                 "alias": "7. 연구 자료 / Ver.2", "is_folder": True},
        })
        self.assertEqual(service.resolve_general_schema_for_parent(cursor, 12), "연구_자료_ver_2")

    def test_cross_kb_hierarchy_is_rejected(self) -> None:
        cursor = _Cursor({
            12: {"node_id": 12, "parent_id": 11, "kb_root_id": 8, "is_folder": True},
            11: {"node_id": 11, "parent_id": 8, "kb_root_id": 99, "name": "Wrong KB", "is_folder": True},
        })
        with self.assertRaises(service.DocumentMetadataResolutionError):
            service.resolve_general_schema_for_parent(cursor, 12)

    def test_missing_or_non_folder_parent_is_rejected(self) -> None:
        for rows in ({}, {8: {"node_id": 8, "kb_root_id": 8, "is_folder": False}}):
            with self.subTest(rows=rows), self.assertRaises(service.DocumentMetadataResolutionError):
                service.resolve_general_schema_for_parent(_Cursor(rows), 8)

    def test_cycle_is_rejected(self) -> None:
        cursor = _Cursor({
            12: {"node_id": 12, "parent_id": 11, "kb_root_id": 8, "is_folder": True},
            11: {"node_id": 11, "parent_id": 12, "kb_root_id": 8, "is_folder": True},
        })
        with self.assertRaises(service.DocumentMetadataResolutionError):
            service.resolve_general_schema_for_parent(cursor, 12)

    def test_refresh_after_root_folder_and_root_moves(self) -> None:
        for kb_type, root_schema in (("admin", "general"), ("personal", "personal")):
            for parent_id, expected in ((8, root_schema), (11, "research_paper"), (8, root_schema)):
                with self.subTest(kb_type=kb_type, parent_id=parent_id):
                    cursor = MagicMock()
                    cursor.fetchall.return_value = [{"branch_type": "general", "parent_id": parent_id}]
                    cursor.fetchone.return_value = {
                        "node_id": parent_id, "parent_id": None if parent_id == 8 else 8,
                        "kb_root_id": 8, "kb_type": kb_type, "name": "4. Research Paper", "is_folder": True,
                    }
                    with patch.object(service, "db_transaction") as transaction:
                        transaction.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
                        self.assertEqual(service.refresh_general_document_metadata(1127), expected)
                    sql, params = cursor.execute.call_args.args
                    self.assertIn("document_stage=NULL", sql)
                    self.assertEqual(params, (expected, 1127))

    def test_kiwix_refresh_does_not_overwrite_json_metadata(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"branch_type": "kiwix", "parent_id": 8}]
        with patch.object(service, "db_transaction") as transaction:
            transaction.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
            self.assertIsNone(service.refresh_general_document_metadata(1127))
        self.assertEqual(cursor.execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
