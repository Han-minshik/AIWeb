from __future__ import annotations

import hashlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.config import settings
from app.services.common.minio_storage_service import (
    MinioIntegrityError,
    MinioStorageService,
    ObjectVersion,
)


class _FakeResponse(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.closed_by_service = False
        self.released_by_service = False

    def stream(self, amt: int):
        while True:
            chunk = self.read(amt)
            if not chunk:
                break
            yield chunk

    def close(self) -> None:
        self.closed_by_service = True
        super().close()

    def release_conn(self) -> None:
        self.released_by_service = True


class MinioStorageServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.service = MinioStorageService(client=self.client)

    def test_endpoint_parser(self) -> None:
        self.assertEqual(
            MinioStorageService._parse_endpoint("http://127.0.0.1:9100"),
            ("127.0.0.1:9100", False),
        )
        self.assertEqual(
            MinioStorageService._parse_endpoint("https://minio.internal:9100"),
            ("minio.internal:9100", True),
        )
        with self.assertRaises(ValueError):
            MinioStorageService._parse_endpoint("http://localhost:9100/path")

    def test_upload_stream_hashes_and_verifies_size(self) -> None:
        payload = b"spaceai-minio"

        def put_object(bucket, key, data, length, **kwargs):
            uploaded = bytearray()
            while True:
                chunk = data.read(4)
                if not chunk:
                    break
                uploaded.extend(chunk)
            self.assertEqual(bytes(uploaded), payload)
            self.assertEqual(length, len(payload))
            self.assertEqual(kwargs["num_parallel_uploads"], 1)
            return SimpleNamespace(version_id="version-1", etag="etag-1")

        self.client.put_object.side_effect = put_object
        self.client.stat_object.return_value = SimpleNamespace(size=len(payload))

        result = self.service.upload_stream(
            settings.MINIO_PERSONAL_BUCKET,
            "users/15/documents/348",
            io.BytesIO(payload),
            length=len(payload),
            content_type="text/plain",
        )

        self.assertEqual(result.version_id, "version-1")
        self.assertEqual(result.size_bytes, len(payload))
        self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())
        self.client.stat_object.assert_called_once_with(
            settings.MINIO_PERSONAL_BUCKET,
            "users/15/documents/348",
            version_id="version-1",
        )

    def test_upload_size_mismatch_removes_new_version(self) -> None:
        payload = b"abc"

        def put_object(_bucket, _key, data, _length, **_kwargs):
            while data.read(4):
                pass
            return SimpleNamespace(version_id="bad-version", etag="etag")

        self.client.put_object.side_effect = put_object
        self.client.stat_object.return_value = SimpleNamespace(size=99)

        with self.assertRaises(MinioIntegrityError):
            self.service.upload_stream(
                settings.MINIO_PERSONAL_BUCKET,
                "users/15/documents/348",
                io.BytesIO(payload),
                length=len(payload),
            )

        self.client.remove_object.assert_called_once_with(
            settings.MINIO_PERSONAL_BUCKET,
            "users/15/documents/348",
            version_id="bad-version",
        )

    def test_download_iterator_closes_and_releases_connection(self) -> None:
        response = _FakeResponse(b"abcdefgh")
        self.client.get_object.return_value = response

        chunks = list(
            self.service.iter_object(
                settings.MINIO_PERSONAL_BUCKET,
                "users/15/documents/348",
                version_id="version-1",
                chunk_size=3,
            )
        )

        self.assertEqual(b"".join(chunks), b"abcdefgh")
        self.assertTrue(response.closed_by_service)
        self.assertTrue(response.released_by_service)

    def test_copy_object_uses_exact_source_version(self) -> None:
        checksum = 'a' * 64
        self.client.stat_object.side_effect = [
            SimpleNamespace(size=12, version_id='source-version', etag='source-etag', content_type='application/pdf', metadata={}),
            SimpleNamespace(size=12, version_id='copy-version', etag='copy-etag', content_type='application/pdf', metadata={}),
        ]
        self.client.copy_object.return_value = SimpleNamespace(version_id='copy-version', etag='copy-etag')

        result = self.service.copy_object(
            settings.MINIO_SE_KB_BUCKET,
            'documents/5307',
            settings.MINIO_PERSONAL_BUCKET,
            'users/7/documents/60',
            source_version_id='source-version',
            sha256=checksum,
        )

        args = self.client.copy_object.call_args.args
        self.assertEqual(args[:2], (settings.MINIO_PERSONAL_BUCKET, 'users/7/documents/60'))
        self.assertEqual(args[2].bucket_name, settings.MINIO_SE_KB_BUCKET)
        self.assertEqual(args[2].object_name, 'documents/5307')
        self.assertEqual(args[2].version_id, 'source-version')
        self.assertEqual(result.version_id, 'copy-version')
        self.assertEqual(result.sha256, checksum)

    def test_permanent_delete_object_filters_exact_key(self) -> None:
        key = "users/15/documents/348"
        versions = [
            ObjectVersion(key, "v2", 3, "e2", True, False),
            ObjectVersion(key, "v1", 3, "e1", False, False),
            ObjectVersion(key + "/unexpected", "v1", 1, "e3", False, False),
        ]
        self.service.list_versions = MagicMock(return_value=versions)

        captured = []

        def remove_objects(_bucket, delete_objects):
            captured.extend(delete_objects)
            return iter(())

        self.client.remove_objects.side_effect = remove_objects

        deleted = self.service.permanent_delete_object(
            settings.MINIO_PERSONAL_BUCKET,
            key,
        )

        self.assertEqual(deleted, 2)
        self.assertEqual([(item.name, item.version_id) for item in captured], [(key, "v2"), (key, "v1")])

    def test_permanent_delete_prefix_requires_specific_prefix(self) -> None:
        invalid_prefixes = ("", "documents/", "documents/348/revisions", "../documents/348/")
        for prefix in invalid_prefixes:
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                self.service.permanent_delete_prefix(
                    settings.MINIO_SE_KB_ARTIFACT_BUCKET,
                    prefix,
                )

    def test_unknown_bucket_is_rejected_before_sdk_call(self) -> None:
        with self.assertRaises(ValueError):
            self.service.stat_object("unexpected-bucket", "documents/348")
        self.client.stat_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
