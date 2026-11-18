from __future__ import annotations

import unittest

from app.services.common.minio_key_builder import (
    KbStorageScope,
    artifact_location,
    artifact_prefix,
    original_location,
)


class MinioKeyBuilderTest(unittest.TestCase):
    def test_se_original_location(self) -> None:
        location = original_location(KbStorageScope.SE_KB, 348)

        self.assertEqual(location.bucket_name, "spaceai-se-kb")
        self.assertEqual(location.object_key, "documents/348")

    def test_personal_original_location(self) -> None:
        location = original_location(KbStorageScope.PERSONAL, 348, user_id=15)

        self.assertEqual(location.bucket_name, "spaceai-personal")
        self.assertEqual(location.object_key, "users/15/documents/348")

    def test_artifact_locations_are_revision_scoped(self) -> None:
        prefix = artifact_prefix(KbStorageScope.SE_KB, 348, 101)
        artifact = artifact_location(
            KbStorageScope.PERSONAL,
            348,
            101,
            "images/page-1.png",
            user_id=15,
        )

        self.assertEqual(
            prefix.object_key,
            "documents/348/revisions/101/",
        )
        self.assertEqual(
            artifact.object_key,
            "users/15/documents/348/revisions/101/images/page-1.png",
        )

    def test_personal_location_requires_user_id(self) -> None:
        with self.assertRaises(ValueError):
            original_location(KbStorageScope.PERSONAL, 348)

    def test_path_traversal_and_separators_are_rejected(self) -> None:
        invalid_values = ("../secret", "/absolute", "folder\\file", "a/../b")
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                artifact_location(KbStorageScope.SE_KB, 348, 101, value)

        with self.assertRaises(ValueError):
            original_location(KbStorageScope.SE_KB, "348/other")


if __name__ == "__main__":
    unittest.main()
