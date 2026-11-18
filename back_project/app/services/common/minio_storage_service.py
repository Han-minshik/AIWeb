from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, BinaryIO, Iterator, Mapping
from urllib.parse import urlsplit

import certifi
import urllib3
from minio import Minio
from minio.commonconfig import CopySource
from minio.deleteobjects import DeleteObject
from minio.error import InvalidResponseError, S3Error, ServerError
from urllib3 import Retry, Timeout

from app.config import settings

_MIN_PART_SIZE = 5 * 1024 * 1024
_MAX_PART_SIZE = 5 * 1024 * 1024 * 1024
_DEFAULT_CHUNK_SIZE = 1024 * 1024


class MinioStorageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        operation: str,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.provider_code = provider_code


class MinioAuthenticationError(MinioStorageError):
    pass


class MinioPermissionError(MinioStorageError):
    pass


class MinioBucketNotFoundError(MinioStorageError):
    pass


class MinioObjectNotFoundError(MinioStorageError):
    pass


class MinioConnectionError(MinioStorageError):
    pass


class MinioIntegrityError(MinioStorageError):
    pass


class MinioDeleteError(MinioStorageError):
    pass


@dataclass(frozen=True)
class StoredObject:
    bucket_name: str
    object_key: str
    version_id: str | None
    etag: str | None
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ObjectMetadata:
    bucket_name: str
    object_key: str
    version_id: str | None
    etag: str | None
    size_bytes: int
    content_type: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ObjectVersion:
    object_key: str
    version_id: str | None
    size_bytes: int
    etag: str | None
    is_latest: bool
    is_delete_marker: bool


@dataclass(frozen=True)
class BucketStatus:
    bucket_name: str
    exists: bool
    versioning_status: str | None


class _HashingReader:
    def __init__(self, source: BinaryIO) -> None:
        self._source = source
        self._digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        if chunk is None:
            return b""
        if not isinstance(chunk, bytes):
            raise TypeError("MinIO upload stream must return bytes")
        if chunk:
            self._digest.update(chunk)
            self.bytes_read += len(chunk)
        return chunk

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


class MinioStorageService:
    def __init__(self, client: Minio | None = None) -> None:
        self._allowed_buckets = frozenset(
            {
                settings.MINIO_SE_KB_BUCKET,
                settings.MINIO_PERSONAL_BUCKET,
                settings.MINIO_SE_KB_ARTIFACT_BUCKET,
                settings.MINIO_PERSONAL_ARTIFACT_BUCKET,
            }
        )
        self._part_size = self._positive_int(
            settings.MINIO_MULTIPART_PART_SIZE_MB,
            "MINIO_MULTIPART_PART_SIZE_MB",
        ) * 1024 * 1024
        if not _MIN_PART_SIZE <= self._part_size <= _MAX_PART_SIZE:
            raise ValueError("MINIO_MULTIPART_PART_SIZE_MB must be between 5 and 5120")

        self._upload_parallelism = self._positive_int(
            settings.MINIO_UPLOAD_PARALLELISM,
            "MINIO_UPLOAD_PARALLELISM",
        )
        self._client = client or self._build_client()

    @staticmethod
    def _positive_int(value: object, label: str) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError(f"{label} must be greater than zero")
        return parsed

    @staticmethod
    def _parse_endpoint(endpoint: str) -> tuple[str, bool]:
        parsed = urlsplit(str(endpoint or "").strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("MINIO_ENDPOINT must use http or https")
        if not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("MINIO_ENDPOINT must contain only host and optional port")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("MINIO_ENDPOINT must not contain a path, query, or fragment")
        return parsed.netloc, parsed.scheme == "https"

    def _build_client(self) -> Minio:
        endpoint, secure = self._parse_endpoint(settings.MINIO_ENDPOINT)
        access_key, secret_key = settings.load_minio_backend_credentials()

        connect_timeout = self._positive_int(
            settings.MINIO_CONNECT_TIMEOUT_SECONDS,
            "MINIO_CONNECT_TIMEOUT_SECONDS",
        )
        response_timeout = max(
            self._positive_int(
                settings.MINIO_READ_TIMEOUT_SECONDS,
                "MINIO_READ_TIMEOUT_SECONDS",
            ),
            self._positive_int(
                settings.MINIO_WRITE_TIMEOUT_SECONDS,
                "MINIO_WRITE_TIMEOUT_SECONDS",
            ),
        )
        pool_size = self._positive_int(
            settings.MINIO_HTTP_POOL_MAXSIZE,
            "MINIO_HTTP_POOL_MAXSIZE",
        )
        retry_count = max(0, int(settings.MINIO_MAX_RETRIES))

        ca_cert_file = settings.MINIO_CA_CERT_FILE or certifi.where()
        http_client = urllib3.PoolManager(
            timeout=Timeout(connect=connect_timeout, read=response_timeout),
            maxsize=pool_size,
            cert_reqs="CERT_REQUIRED" if settings.MINIO_TLS_VERIFY else "CERT_NONE",
            ca_certs=ca_cert_file if settings.MINIO_TLS_VERIFY else None,
            retries=Retry(
                total=retry_count,
                connect=retry_count,
                read=0,
                status=retry_count,
                backoff_factor=0.2,
                status_forcelist=(500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
                raise_on_status=False,
            ),
        )
        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=settings.MINIO_REGION,
            http_client=http_client,
            cert_check=settings.MINIO_TLS_VERIFY,
        )
        client.set_app_info("spaceai-backend", settings.APP_VERSION)
        return client

    def _validate_bucket(self, bucket_name: str) -> None:
        if bucket_name not in self._allowed_buckets:
            raise ValueError("Bucket is not part of the configured SpaceAI storage layout")

    @staticmethod
    def _validate_object_key(object_key: str) -> str:
        key = str(object_key or "").strip()
        if not key or key.startswith("/") or "\\" in key:
            raise ValueError("Invalid MinIO object key")
        if any(part in {"", ".", ".."} for part in key.split("/")):
            raise ValueError("Invalid MinIO object key")
        return key

    def _raise_translated(self, exc: Exception, operation: str) -> None:
        if isinstance(exc, MinioStorageError):
            raise exc
        if isinstance(exc, S3Error):
            code = exc.code
            error_type: type[MinioStorageError]
            if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidToken"}:
                error_type = MinioAuthenticationError
            elif code in {"AccessDenied", "AllAccessDisabled"}:
                error_type = MinioPermissionError
            elif code == "NoSuchBucket":
                error_type = MinioBucketNotFoundError
            elif code in {"NoSuchKey", "NoSuchObject", "NoSuchVersion"}:
                error_type = MinioObjectNotFoundError
            else:
                error_type = MinioStorageError
            raise error_type(
                f"MinIO {operation} failed",
                operation=operation,
                provider_code=code,
            ) from exc
        if isinstance(exc, (urllib3.exceptions.HTTPError, OSError)):
            raise MinioConnectionError(
                f"MinIO {operation} connection failed",
                operation=operation,
            ) from exc
        if isinstance(exc, (InvalidResponseError, ServerError)):
            raise MinioConnectionError(
                f"MinIO {operation} returned an invalid response",
                operation=operation,
            ) from exc
        raise MinioStorageError(
            f"MinIO {operation} failed",
            operation=operation,
        ) from exc

    def upload_stream(
        self,
        bucket_name: str,
        object_key: str,
        stream: BinaryIO,
        *,
        length: int | None,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        self._validate_bucket(bucket_name)
        object_key = self._validate_object_key(object_key)
        if not callable(getattr(stream, "read", None)):
            raise TypeError("stream must provide read()")
        if length is not None and int(length) < 0:
            raise ValueError("length must be non-negative or None")

        hashing_stream = _HashingReader(stream)
        sdk_length = -1 if length is None else int(length)
        try:
            result = self._client.put_object(
                bucket_name,
                object_key,
                hashing_stream,
                sdk_length,
                content_type=content_type or "application/octet-stream",
                metadata=dict(metadata or {}),
                part_size=self._part_size,
                num_parallel_uploads=self._upload_parallelism,
            )
            version_id = getattr(result, "version_id", None)
            stat = self._client.stat_object(
                bucket_name,
                object_key,
                version_id=version_id,
            )
        except Exception as exc:
            self._raise_translated(exc, "upload")

        actual_size = int(getattr(stat, "size", hashing_stream.bytes_read))
        if actual_size != hashing_stream.bytes_read or (
            length is not None and actual_size != int(length)
        ):
            try:
                self._client.remove_object(
                    bucket_name,
                    object_key,
                    version_id=version_id,
                )
            except Exception:
                pass
            raise MinioIntegrityError(
                "MinIO upload size verification failed",
                operation="upload",
            )

        return StoredObject(
            bucket_name=bucket_name,
            object_key=object_key,
            version_id=version_id,
            etag=getattr(result, "etag", None),
            size_bytes=actual_size,
            sha256=hashing_stream.sha256,
        )

    def stat_object(
        self,
        bucket_name: str,
        object_key: str,
        *,
        version_id: str | None = None,
    ) -> ObjectMetadata:
        self._validate_bucket(bucket_name)
        object_key = self._validate_object_key(object_key)
        try:
            stat = self._client.stat_object(
                bucket_name,
                object_key,
                version_id=version_id,
            )
        except Exception as exc:
            self._raise_translated(exc, "stat")

        return ObjectMetadata(
            bucket_name=bucket_name,
            object_key=object_key,
            version_id=getattr(stat, "version_id", version_id),
            etag=getattr(stat, "etag", None),
            size_bytes=int(getattr(stat, "size", 0)),
            content_type=getattr(stat, "content_type", None),
            metadata=dict(getattr(stat, "metadata", {}) or {}),
        )

    def copy_object(
        self,
        source_bucket_name: str,
        source_object_key: str,
        destination_bucket_name: str,
        destination_object_key: str,
        *,
        source_version_id: str | None = None,
        sha256: str,
    ) -> StoredObject:
        self._validate_bucket(source_bucket_name)
        source_object_key = self._validate_object_key(source_object_key)
        self._validate_bucket(destination_bucket_name)
        destination_object_key = self._validate_object_key(destination_object_key)
        checksum = str(sha256 or '').strip().lower()
        if len(checksum) != 64 or any(char not in '0123456789abcdef' for char in checksum):
            raise ValueError('A valid source SHA-256 checksum is required')
        source = self.stat_object(
            source_bucket_name,
            source_object_key,
            version_id=source_version_id,
        )
        try:
            result = self._client.copy_object(
                destination_bucket_name,
                destination_object_key,
                CopySource(
                    source_bucket_name,
                    source_object_key,
                    version_id=source_version_id,
                ),
            )
            version_id = getattr(result, 'version_id', None)
            copied = self.stat_object(
                destination_bucket_name,
                destination_object_key,
                version_id=version_id,
            )
        except Exception as exc:
            self._raise_translated(exc, 'copy')
        if copied.size_bytes != source.size_bytes:
            try:
                self._client.remove_object(
                    destination_bucket_name,
                    destination_object_key,
                    version_id=version_id,
                )
            except Exception:
                pass
            raise MinioIntegrityError('MinIO copy size verification failed', operation='copy')
        return StoredObject(
            destination_bucket_name,
            destination_object_key,
            version_id,
            copied.etag,
            copied.size_bytes,
            checksum,
        )

    def iter_object(
        self,
        bucket_name: str,
        object_key: str,
        *,
        version_id: str | None = None,
        offset: int = 0,
        length: int = 0,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ) -> Iterator[bytes]:
        self._validate_bucket(bucket_name)
        object_key = self._validate_object_key(object_key)
        if offset < 0 or length < 0 or chunk_size <= 0:
            raise ValueError("Invalid object streaming range")
        try:
            response = self._client.get_object(
                bucket_name,
                object_key,
                offset=offset,
                length=length,
                version_id=version_id,
            )
        except Exception as exc:
            self._raise_translated(exc, "download")

        def generate() -> Iterator[bytes]:
            try:
                for chunk in response.stream(amt=chunk_size):
                    if chunk:
                        yield chunk
            except Exception as exc:
                self._raise_translated(exc, "download")
            finally:
                response.close()
                response.release_conn()

        return generate()

    def restore_version(
        self,
        bucket_name: str,
        object_key: str,
        source_version_id: str,
        *,
        content_type: str | None = None,
    ) -> StoredObject:
        source = self.stat_object(
            bucket_name,
            object_key,
            version_id=source_version_id,
        )
        try:
            response = self._client.get_object(
                bucket_name,
                object_key,
                version_id=source_version_id,
            )
        except Exception as exc:
            self._raise_translated(exc, "restore")

        try:
            return self.upload_stream(
                bucket_name,
                object_key,
                response,
                length=source.size_bytes,
                content_type=content_type or source.content_type or "application/octet-stream",
            )
        finally:
            response.close()
            response.release_conn()

    def list_versions(self, bucket_name: str, prefix: str) -> list[ObjectVersion]:
        self._validate_bucket(bucket_name)
        prefix = str(prefix or "").strip()
        if not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
            raise ValueError("A non-empty safe prefix is required")
        try:
            objects = self._client.list_objects(
                bucket_name,
                prefix=prefix,
                recursive=True,
                include_version=True,
            )
            return [
                ObjectVersion(
                    object_key=obj.object_name,
                    version_id=getattr(obj, "version_id", None),
                    size_bytes=int(getattr(obj, "size", 0) or 0),
                    etag=getattr(obj, "etag", None),
                    is_latest=bool(getattr(obj, "is_latest", False)),
                    is_delete_marker=bool(getattr(obj, "is_delete_marker", False)),
                )
                for obj in objects
            ]
        except Exception as exc:
            self._raise_translated(exc, "list versions")

    def remove_version(
        self,
        bucket_name: str,
        object_key: str,
        version_id: str,
    ) -> None:
        self._validate_bucket(bucket_name)
        object_key = self._validate_object_key(object_key)
        if not str(version_id or "").strip():
            raise ValueError("version_id is required")
        try:
            self._client.remove_object(
                bucket_name,
                object_key,
                version_id=version_id,
            )
        except Exception as exc:
            self._raise_translated(exc, "remove version")

    def _delete_listed_versions(
        self,
        bucket_name: str,
        versions: list[ObjectVersion],
    ) -> int:
        if not versions:
            return 0
        delete_objects = (
            DeleteObject(item.object_key, item.version_id)
            for item in versions
        )
        try:
            errors = list(self._client.remove_objects(bucket_name, delete_objects))
        except Exception as exc:
            self._raise_translated(exc, "permanent delete")
        if errors:
            first_error = errors[0]
            raise MinioDeleteError(
                "MinIO permanent delete was only partially completed",
                operation="permanent delete",
                provider_code=getattr(first_error, "code", None),
            )
        return len(versions)

    def permanent_delete_object(self, bucket_name: str, object_key: str) -> int:
        self._validate_bucket(bucket_name)
        object_key = self._validate_object_key(object_key)
        versions = [
            item
            for item in self.list_versions(bucket_name, object_key)
            if item.object_key == object_key
        ]
        return self._delete_listed_versions(bucket_name, versions)

    def permanent_delete_prefix(self, bucket_name: str, prefix: str) -> int:
        self._validate_bucket(bucket_name)
        prefix = str(prefix or "").strip()
        parts = [part for part in prefix.split("/") if part]
        if not prefix.endswith("/") or len(parts) < 3 or any(part in {".", ".."} for part in parts):
            raise ValueError("A specific trailing-slash prefix is required")
        return self._delete_listed_versions(
            bucket_name,
            self.list_versions(bucket_name, prefix),
        )

    def check_required_buckets(self) -> list[BucketStatus]:
        statuses: list[BucketStatus] = []
        for bucket_name in sorted(self._allowed_buckets):
            try:
                exists = self._client.bucket_exists(bucket_name)
                versioning_status = None
                if exists:
                    config = self._client.get_bucket_versioning(bucket_name)
                    versioning_status = getattr(config, "status", None)
                statuses.append(
                    BucketStatus(
                        bucket_name=bucket_name,
                        exists=exists,
                        versioning_status=versioning_status,
                    )
                )
            except Exception as exc:
                self._raise_translated(exc, "bucket check")
        return statuses


@lru_cache(maxsize=1)
def get_minio_storage_service() -> MinioStorageService:
    return MinioStorageService()


def clear_minio_storage_service_cache() -> None:
    get_minio_storage_service.cache_clear()
