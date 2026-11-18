from __future__ import annotations

import io
import uuid

from app.config import settings
from app.services.common.minio_storage_service import get_minio_storage_service


def main() -> None:
    storage = get_minio_storage_service()
    object_key = f"users/0/documents/integration-test-{uuid.uuid4().hex}"
    payload = "SpaceAI MinIO UTF-8 통합 테스트".encode("utf-8")
    created = False
    try:
        stored = storage.upload_stream(
            settings.MINIO_PERSONAL_BUCKET,
            object_key,
            io.BytesIO(payload),
            length=len(payload),
            content_type="text/plain; charset=utf-8",
            metadata={"purpose": "integration-test"},
        )
        created = True
        downloaded = b"".join(
            storage.iter_object(
                settings.MINIO_PERSONAL_BUCKET,
                object_key,
                version_id=stored.version_id,
            )
        )
        if downloaded != payload:
            raise RuntimeError("downloaded integration payload does not match")
        print("MinIO stream integration: upload/download passed")
    finally:
        if created:
            storage.permanent_delete_object(settings.MINIO_PERSONAL_BUCKET, object_key)
        remaining = [
            item
            for item in storage.list_versions(settings.MINIO_PERSONAL_BUCKET, object_key)
            if item.object_key == object_key
        ]
        if remaining:
            raise RuntimeError("integration test object cleanup failed")
        print("MinIO stream integration: test object cleanup passed")


if __name__ == "__main__":
    main()
