from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from app.config import settings


class KbStorageScope(str, Enum):
    SE_KB = "se"
    PERSONAL = "personal"


@dataclass(frozen=True)
class MinioLocation:
    bucket_name: str
    object_key: str


def _identifier(value: object, label: str) -> str:
    text = str(value).strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"Invalid {label}")
    return text


def _artifact_relative_path(value: str) -> str:
    raw_value = str(value or "").strip()
    if not raw_value or "\\" in raw_value:
        raise ValueError("Invalid artifact path")

    path = PurePosixPath(raw_value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Invalid artifact path")
    return path.as_posix()


def original_location(
    scope: KbStorageScope,
    object_id: object,
    *,
    user_id: object | None = None,
) -> MinioLocation:
    object_token = _identifier(object_id, "object_id")
    scope = KbStorageScope(scope)

    if scope is KbStorageScope.SE_KB:
        return MinioLocation(settings.MINIO_SE_KB_BUCKET, f"documents/{object_token}")

    if user_id is None:
        raise ValueError("user_id is required for personal KB objects")
    user_token = _identifier(user_id, "user_id")
    return MinioLocation(
        settings.MINIO_PERSONAL_BUCKET,
        f"users/{user_token}/documents/{object_token}",
    )


def artifact_prefix(
    scope: KbStorageScope,
    object_id: object,
    revision_id: object,
    *,
    user_id: object | None = None,
) -> MinioLocation:
    object_token = _identifier(object_id, "object_id")
    revision_token = _identifier(revision_id, "revision_id")
    scope = KbStorageScope(scope)

    if scope is KbStorageScope.SE_KB:
        return MinioLocation(
            settings.MINIO_SE_KB_ARTIFACT_BUCKET,
            f"documents/{object_token}/revisions/{revision_token}/",
        )

    if user_id is None:
        raise ValueError("user_id is required for personal KB artifacts")
    user_token = _identifier(user_id, "user_id")
    return MinioLocation(
        settings.MINIO_PERSONAL_ARTIFACT_BUCKET,
        f"users/{user_token}/documents/{object_token}/revisions/{revision_token}/",
    )


def artifact_location(
    scope: KbStorageScope,
    object_id: object,
    revision_id: object,
    artifact_path: str,
    *,
    user_id: object | None = None,
) -> MinioLocation:
    prefix = artifact_prefix(
        scope,
        object_id,
        revision_id,
        user_id=user_id,
    )
    return MinioLocation(
        prefix.bucket_name,
        prefix.object_key + _artifact_relative_path(artifact_path),
    )
