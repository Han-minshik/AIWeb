from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path

from tools import document_metadata_migration as migration


class DocumentMetadataMigrationTests(unittest.TestCase):
    def test_manifest_patch_encoding_preserves_other_fields(self) -> None:
        original = {
            "schema_version": "1",
            "content": {"object_key": "content.md"},
            "viewer": {"mode": "markdown"},
            "assets": [{"path": "image.png"}],
        }
        patched = dict(original)
        patched["document_metadata"] = {"schema": "standard", "stage": None}

        decoded = migration._decode_manifest(migration._encode_manifest(patched))

        for key, value in original.items():
            self.assertEqual(decoded[key], value)
        self.assertEqual(
            decoded["document_metadata"],
            {"schema": "standard", "stage": None},
        )

    def test_resume_uses_only_latest_pending_artifact_records(self) -> None:
        records = [
            {"artifact_id": 1, "result": "DB_UPDATE_PENDING"},
            {"artifact_id": 1, "result": "UPDATED"},
            {"artifact_id": 2, "result": "DB_UPDATE_PENDING"},
            {"result": "FAILED", "error": "unrelated preflight error"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.jsonl"
            path.write_text(
                "\n".join(json.dumps(record) for record in records),
                encoding="utf-8",
            )

            pending = migration._load_resume_records(path)

        self.assertEqual(pending, [{"artifact_id": 2, "result": "DB_UPDATE_PENDING"}])


    def test_root_documents_resolve_in_migration_without_moving(self) -> None:
        for kb_type, expected in (("admin", "general"), ("personal", "personal")):
            with self.subTest(kb_type=kb_type):
                cursor = MagicMock()
                cursor.fetchall.return_value = [{"parent_id": 8}]
                cursor.fetchone.return_value = {
                    "node_id": 8, "parent_id": None, "kb_root_id": 8,
                    "kb_type": kb_type, "name": "admin", "is_folder": True,
                }
                self.assertEqual(migration._schema_for_object(cursor, 1127), expected)


if __name__ == "__main__":
    unittest.main()
