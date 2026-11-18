from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.services.features import docs_service, qa_service


class ArtifactAssetStreamTest(unittest.TestCase):
    def test_qa_source_image_is_rewritten_to_document_artifact_url(self) -> None:
        source = {
            "chunks": [{"image_refs": [{"path": "manual/images/page_11_figure_4.jpg"}]}]
        }
        result = qa_service._apply_source_hint(source, {"object_id": 5304})
        ref = result["chunks"][0]["image_refs"][0]
        self.assertEqual(ref["url"], "/docs/5304/artifacts/images/page_11_figure_4.jpg")

    def test_absolute_image_url_is_not_rewritten(self) -> None:
        source = {"images": ["https://images.example.test/a.jpg"]}
        result = qa_service._apply_source_hint(source, {"object_id": 5304})
        self.assertEqual(result["images"], ["https://images.example.test/a.jpg"])

    def test_streams_only_asset_registered_in_manifest(self) -> None:
        manifest = {
            "assets": [{
                "source_path": "images/a.jpg",
                "object_key": "documents/5304/revisions/1582/assets/images/a.jpg",
                "version_id": "asset-version",
            }]
        }
        storage = Mock()
        storage.stat_object.side_effect = [
            SimpleNamespace(size_bytes=200, content_type="application/json", etag="manifest-etag"),
            SimpleNamespace(size_bytes=3, content_type="image/jpeg", etag="asset-etag"),
        ]
        storage.iter_object.side_effect = [
            iter([json.dumps(manifest).encode("utf-8")]),
            iter([b"jpg"]),
        ]
        artifact = {
            "bucket_name": "spaceai-se-kb-artifacts",
            "object_key": "documents/5304/revisions/1582/manifest.json",
            "minio_version_id": "manifest-version",
            "revision_id": 1582,
        }
        with (
            patch.object(docs_service, "require_object_permission") as require_permission,
            patch.object(docs_service, "fetch_one", return_value=artifact),
            patch.object(docs_service, "get_minio_storage_service", return_value=storage),
        ):
            response = docs_service.stream_doc_artifact_asset(5304, "images/a.jpg", user_id=1)

        require_permission.assert_called_once_with(1, 5304, "r")
        self.assertEqual(response.media_type, "image/jpeg")
        self.assertEqual(response.headers["content-length"], "3")

    def test_rejects_asset_not_registered_in_manifest(self) -> None:
        storage = Mock()
        storage.stat_object.return_value = SimpleNamespace(
            size_bytes=14, content_type="application/json", etag="manifest-etag"
        )
        storage.iter_object.return_value = iter([b'{"assets":[]}'])
        artifact = {
            "bucket_name": "spaceai-se-kb-artifacts",
            "object_key": "documents/5304/revisions/1582/manifest.json",
            "minio_version_id": "manifest-version",
            "revision_id": 1582,
        }
        with (
            patch.object(docs_service, "require_object_permission"),
            patch.object(docs_service, "fetch_one", return_value=artifact),
            patch.object(docs_service, "get_minio_storage_service", return_value=storage),
        ):
            with self.assertRaises(HTTPException) as caught:
                docs_service.stream_doc_artifact_asset(5304, "images/missing.jpg", user_id=1)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "ARTIFACT_ASSET_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
