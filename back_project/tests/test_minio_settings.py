from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import settings


class MinioSettingsTest(unittest.TestCase):
    def test_read_single_line_secret_file_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            secret_file = Path(temp_dir) / "secret"
            secret_file.write_bytes(b"\xef\xbb\xbfvalue")

            self.assertEqual(
                settings._read_single_line_secret_file(str(secret_file), "test"),
                "value",
            )

    def test_read_single_line_secret_file_rejects_invalid_content(self) -> None:
        for content in ("", " value", "value ", "value\n", "a\nb"):
            with self.subTest(content=repr(content)), tempfile.TemporaryDirectory() as temp_dir:
                secret_file = Path(temp_dir) / "secret"
                secret_file.write_text(content, encoding="utf-8")

                with self.assertRaises(RuntimeError):
                    settings._read_single_line_secret_file(str(secret_file), "test")

    def test_backend_and_admin_credentials_are_loaded_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend_access = root / "backend-access"
            backend_secret = root / "backend-secret"
            admin_access = root / "admin-access"
            admin_secret = root / "admin-secret"

            backend_access.write_text("backend-id", encoding="utf-8")
            backend_secret.write_text("backend-password", encoding="utf-8")
            admin_access.write_text("admin-id", encoding="utf-8")
            admin_secret.write_text("admin-password", encoding="utf-8")

            with (
                patch.object(settings, "MINIO_BACKEND_ACCESS_KEY_FILE", str(backend_access)),
                patch.object(settings, "MINIO_BACKEND_SECRET_KEY_FILE", str(backend_secret)),
                patch.object(settings, "MINIO_ADMIN_ACCESS_KEY_FILE", str(admin_access)),
                patch.object(settings, "MINIO_ADMIN_SECRET_KEY_FILE", str(admin_secret)),
            ):
                self.assertEqual(
                    settings.load_minio_backend_credentials(),
                    ("backend-id", "backend-password"),
                )
                self.assertEqual(
                    settings.load_minio_admin_credentials(),
                    ("admin-id", "admin-password"),
                )


if __name__ == "__main__":
    unittest.main()
