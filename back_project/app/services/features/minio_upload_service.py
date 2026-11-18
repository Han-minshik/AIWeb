from __future__ import annotations

import asyncio
import hashlib
import queue
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import PurePath
from typing import Any, AsyncIterator, Dict

from fastapi import HTTPException
from dateutil import parser as date_parser

from app.config import settings
from app.repositories.transaction import db_transaction
from app.repositories.common import fetch_one
from app.services.common.minio_key_builder import KbStorageScope, original_location
from app.services.common.minio_storage_service import (
    MinioObjectNotFoundError,
    MinioStorageError,
    StoredObject,
    get_minio_storage_service,
)
from app.services.features.document_db import require_object_permission, split_filename
from app.services.features.document_metadata_service import (
    DocumentMetadataResolutionError,
    resolve_general_schema_for_parent,
)
from app.utils.db import get_connection


_END = object()
ALLOWED_DOCUMENT_EXTENSIONS = frozenset({
    "pdf", "doc", "docx", "ppt", "pptx", "hwp", "hwpx",
    "xls", "xlsx", "csv", "txt", "md",
})
_COPY_SOURCE_SQL = '''
SELECT so.*,dr.revision_id AS source_revision_id,dr.minio_version_id,
       COALESCE(dr.checksum_sha256,so.checksum_sha256) AS source_checksum
  FROM storage_objects so
  JOIN doc_revisions dr ON dr.doc_id=so.object_id
 WHERE so.object_id=%s AND so.doc_status='active'
   AND so.storage_backend='minio' AND dr.storage_status='READY'
   AND dr.minio_version_id IS NOT NULL
 ORDER BY dr.rev_no DESC,dr.revision_id DESC LIMIT 1
'''
_UPLOAD_SEMAPHORE = asyncio.Semaphore(max(1, settings.MINIO_API_UPLOAD_CONCURRENCY))


def _parse_action_for_revision(row: Dict[str, Any]) -> str:
    return 'update' if int(row.get('rev_no') or 1) > 1 else 'add'


def is_kiwix_preparsed_markdown(branch_type: Any, file_name: str) -> bool:
    _, extension = split_filename(str(file_name or ""))
    return str(branch_type or "").strip().lower() == "kiwix" and extension.lower() == "md"


def normalize_kiwix_metadata(value: Any) -> Dict[str, Any]:
    if value in (None, {}):
        raise HTTPException(status_code=422, detail="KIWIX_METADATA_REQUIRED")
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="INVALID_KIWIX_METADATA")
    limits = {
        "section": 255,
        "platform": 20,
        "prjCode": 100,
        "prjName": 255,
        "itemtypeName": 255,
        "docCode": 100,
        "fileName": 275,
        "fileExt": 20,
        "fileCreate": 100,
    }
    normalized_keys = {
        re.sub(r"[^a-z0-9]", "", str(key).lower()): raw_value
        for key, raw_value in value.items()
    }
    aliases = {
        "prjCode": ("prjcode", "foldername"),
        "fileName": ("filename",),
        "fileCreate": ("filecreate",),
    }
    metadata: Dict[str, Any] = {
        key: str(next(
            (normalized_keys[name] for name in aliases.get(
                key, (re.sub(r"[^a-z0-9]", "", key.lower()),)
            ) if name in normalized_keys),
            "",
        ) or "").strip()
        for key in limits
    }
    for key in ("schema", "stage"):
        normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
        raw_value = normalized_keys.get(normalized_key)
        if raw_value is not None and not isinstance(raw_value, str):
            raise HTTPException(status_code=422, detail="INVALID_KIWIX_DOCUMENT_METADATA")
        metadata[key] = raw_value
    if any(len(metadata[key]) > limit for key, limit in limits.items()):
        raise HTTPException(status_code=422, detail="KIWIX_METADATA_VALUE_TOO_LONG")
    if metadata["platform"] and metadata["platform"].upper() != "KIWIX":
        raise HTTPException(status_code=422, detail="INVALID_KIWIX_PLATFORM")
    if not metadata["prjCode"] or not metadata["fileName"] or not metadata["fileCreate"]:
        raise HTTPException(status_code=422, detail="KIWIX_METADATA_REQUIRED")
    metadata["platform"] = "KIWIX"
    metadata["fileExt"] = metadata["fileExt"].lstrip(".").lower()
    effective_ext = metadata["fileExt"] or "md"
    suffix = f".{effective_ext}".lower()
    if metadata["fileName"].lower().endswith(suffix):
        metadata["fileName"] = metadata["fileName"][:-len(suffix)]
    return metadata


def _kiwix_source_date(metadata: Dict[str, Any]) -> datetime | None:
    raw = str(metadata.get("fileCreate") or "").strip()
    if not raw:
        return None
    normalized = raw.replace("년", "-").replace("월", "-").replace("일", " ").strip()
    normalized = re.sub(r"\s+", " ", normalized).replace("- ", "-")
    iso_value = normalized[:-1] + "+00:00" if normalized.upper().endswith("Z") else normalized
    try:
        return datetime.fromisoformat(iso_value)
    except ValueError:
        pass
    for fmt in (
        "%Y%m%d", "%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y.%m.%d", "%Y.%m.%d %H:%M:%S", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S",
        "%d-%m-%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        pass
    if normalized.isdigit():
        try:
            numeric = int(normalized)
            if numeric >= 10_000_000_000:
                numeric /= 1000
            if numeric >= 1_000_000_000:
                return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    try:
        return date_parser.parse(normalized, yearfirst=True, fuzzy=False)
    except (TypeError, ValueError, OverflowError):
        pass
    raise HTTPException(status_code=422, detail="INVALID_KIWIX_FILE_CREATE")


class _StreamFailure:
    def __init__(self, error: BaseException) -> None:
        self.error = error


class _QueuedReader:
    """Expose an async request body to the synchronous MinIO SDK without disk spooling."""

    def __init__(self, max_chunks: int) -> None:
        self._queue: queue.Queue[bytes | object | _StreamFailure] = queue.Queue(
            maxsize=max(1, max_chunks)
        )
        self._buffer = bytearray()
        self._ended = False

    def put_nowait(self, value: bytes | object | _StreamFailure) -> None:
        self._queue.put_nowait(value)

    def read(self, size: int = -1) -> bytes:
        target = size if size is not None and size >= 0 else None
        while not self._ended and (target is None or len(self._buffer) < target):
            item = self._queue.get()
            if item is _END:
                self._ended = True
                break
            if isinstance(item, _StreamFailure):
                self._ended = True
                raise OSError("request stream was interrupted") from item.error
            self._buffer.extend(item)

        if target is None:
            result = bytes(self._buffer)
            self._buffer.clear()
            return result
        result = bytes(self._buffer[:target])
        del self._buffer[:target]
        return result


async def _queue_put(
    reader: _QueuedReader,
    value: bytes | object | _StreamFailure,
    upload_task: asyncio.Task[StoredObject],
) -> None:
    while True:
        if upload_task.done():
            await upload_task
        try:
            reader.put_nowait(value)
            return
        except queue.Full:
            await asyncio.sleep(0.01)


def _clean_filename(value: str) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 275 or PurePath(name).name != name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="INVALID_FILE_NAME")
    return name


def _validate_document_extension(file_name: str) -> str:
    _, extension = split_filename(file_name)
    normalized = str(extension or "").strip().lstrip(".").lower()
    if normalized not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={
                "code": "UNSUPPORTED_DOCUMENT_TYPE",
                "allowed_extensions": sorted(ALLOWED_DOCUMENT_EXTENSIONS),
            },
        )
    return normalized


def _validate_version_extension(document: Dict[str, Any], file_name: str) -> None:
    _, extension = split_filename(file_name)
    current_extension = str(document.get('file_right') or '').lstrip('.').lower()
    if extension.lower() != current_extension:
        raise HTTPException(status_code=409, detail='VERSION_FILE_TYPE_MISMATCH')


def _cursor_row(cursor: Any) -> Dict[str, Any] | None:
    row = cursor.fetchone()
    return dict(row) if row else None


def _logical_parent_path(cursor: Any, parent_id: int) -> tuple[str, str]:
    chain: list[Dict[str, Any]] = []
    current: int | None = int(parent_id)
    for _ in range(256):
        if current is None:
            break
        cursor.execute(
            "SELECT node_id,parent_id,name,kb_type FROM tree_nodes WHERE node_id=%s",
            (current,),
        )
        row = _cursor_row(cursor)
        if not row:
            break
        chain.append(row)
        current = int(row["parent_id"]) if row.get("parent_id") is not None else None
    chain.reverse()
    parts = [str(row.get("name") or "").strip() for row in chain]
    parts = [part for part in parts if part]
    return (parts[0], "/".join(parts[1:])) if parts else ("", "")


def _existing_init(cursor: Any, user_id: int, upload_request_id: str) -> Dict[str, Any] | None:
    cursor.execute(
        """
        SELECT dr.revision_id, dr.doc_id AS object_id, dr.storage_status,
               dr.size_bytes, so.bucket_name, so.object_key, tn.node_id,
               CONCAT(so.doc_name, CASE WHEN so.file_right='' THEN '' ELSE CONCAT('.', so.file_right) END) AS file_name
          FROM doc_revisions dr
          JOIN storage_objects so ON so.object_id=dr.doc_id
          LEFT JOIN tree_nodes tn ON tn.doc_id=dr.doc_id
         WHERE dr.created_by=%s AND dr.upload_request_id=%s
         LIMIT 1
        """,
        (int(user_id), upload_request_id),
    )
    return _cursor_row(cursor)


def _active_document_conflict(
    cursor: Any,
    *,
    parent_node_id: int,
    file_name: str,
    branch_type: str,
    doc_code: str,
) -> Dict[str, Any] | None:
    if branch_type == "kiwix" and doc_code:
        # KIWIX identifies a source document by docCode. Serialize checks for the
        # same identity so concurrent requests cannot register it twice.
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"kiwix:{doc_code}",),
        )
        cursor.execute(
            """
            SELECT tn.node_id,tn.doc_id AS object_id,tn.alias AS file_name
              FROM tree_nodes tn
              JOIN storage_objects so ON so.object_id=tn.doc_id
             WHERE tn.is_folder=FALSE
               AND so.branch_type='kiwix'
               AND so.doc_code=%s
               AND so.doc_status='active'
             ORDER BY tn.node_id
             LIMIT 1
             FOR UPDATE
            """,
            (doc_code,),
        )
    else:
        cursor.execute(
            """
            SELECT tn.node_id,tn.doc_id AS object_id,tn.alias AS file_name
              FROM tree_nodes tn
              JOIN storage_objects so ON so.object_id=tn.doc_id
             WHERE tn.parent_id=%s
               AND tn.is_folder=FALSE
               AND tn.alias=%s
               AND so.doc_status='active'
             ORDER BY tn.node_id
             LIMIT 1
             FOR UPDATE
            """,
            (int(parent_node_id), file_name),
        )
    return _cursor_row(cursor)


def initialize_upload(
    *,
    upload_request_id: str,
    parent_node_id: int,
    file_name: str,
    file_size: int,
    content_type: str,
    branch_type: str,
    user_id: int,
    kiwix_metadata: Any = None,
    allow_kiwix_preparsed_markdown: bool = False,
) -> Dict[str, Any]:
    try:
        request_uuid = str(uuid.UUID(str(upload_request_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="INVALID_UPLOAD_REQUEST_ID") from exc
    file_name = _clean_filename(file_name)
    _validate_document_extension(file_name)
    file_size = int(file_size)
    if file_size < 0:
        raise HTTPException(status_code=400, detail="INVALID_FILE_SIZE")
    content_type = str(content_type or "application/octet-stream").strip()[:100]
    branch = "kiwix" if str(branch_type or "").lower() == "kiwix" else "general"
    if is_kiwix_preparsed_markdown(branch, file_name) and not allow_kiwix_preparsed_markdown:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "KIWIX_PREPARSED_UPLOAD_REQUIRED",
                "file_name": file_name,
                "reason": "KIWIX Markdown must be registered through the preparsed upload flow",
                "retryable": True,
            },
        )
    metadata = normalize_kiwix_metadata(kiwix_metadata) if branch == "kiwix" else {}
    source_date = _kiwix_source_date(metadata)

    try:
        with db_transaction() as connection:
            with connection.cursor() as cursor:
                existing = _existing_init(cursor, user_id, request_uuid)
                if existing:
                    existing["upload_url"] = f"/docs/uploads/{existing['revision_id']}/content"
                    existing["idempotent_replay"] = True
                    return existing

                cursor.execute(
                    """
                    SELECT node_id,parent_id,kb_root_id,kb_type,kb_owner_user_id,
                           is_folder,item_type_code,name,alias
                      FROM tree_nodes
                     WHERE node_id=%s
                     FOR UPDATE
                    """,
                    (int(parent_node_id),),
                )
                parent = _cursor_row(cursor)
                if not parent or not bool(parent.get("is_folder")):
                    raise HTTPException(status_code=400, detail="INVALID_PARENT_FOLDER")
                if metadata and str(parent.get("alias") or parent.get("name") or "").strip().casefold() != metadata["prjCode"].casefold():
                    raise HTTPException(status_code=409, detail="KIWIX_PROJECT_FOLDER_MISMATCH")

                if branch == "general":
                    try:
                        document_schema = resolve_general_schema_for_parent(
                            cursor, int(parent_node_id)
                        )
                    except DocumentMetadataResolutionError as exc:
                        raise HTTPException(
                            status_code=422,
                            detail={
                                "code": "GENERAL_DOCUMENT_SCHEMA_UNRESOLVED",
                                "reason": str(exc),
                            },
                        ) from exc
                    document_stage = None
                else:
                    document_schema = metadata.get("schema")
                    document_stage = metadata.get("stage")

                scope = (
                    KbStorageScope.PERSONAL
                    if str(parent.get("kb_type") or "").lower() == "personal"
                    else KbStorageScope.SE_KB
                )
                if scope is KbStorageScope.PERSONAL:
                    if int(parent.get("kb_owner_user_id") or 0) != int(user_id):
                        raise HTTPException(status_code=403, detail="PERSONAL_KB_OWNER_REQUIRED")
                    cursor.execute(
                        """
                        INSERT INTO personal_kb_storage_usage(user_id,committed_bytes,reserved_bytes)
                        VALUES(%s,0,0)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        (int(user_id),),
                    )
                    cursor.execute(
                        """
                        SELECT committed_bytes,reserved_bytes
                          FROM personal_kb_storage_usage
                         WHERE user_id=%s FOR UPDATE
                        """,
                        (int(user_id),),
                    )
                    usage = _cursor_row(cursor) or {}
                    projected = int(usage.get("committed_bytes") or 0) + int(
                        usage.get("reserved_bytes") or 0
                    ) + file_size
                    if projected > settings.MINIO_PERSONAL_QUOTA_BYTES:
                        raise HTTPException(status_code=413, detail="PERSONAL_KB_QUOTA_EXCEEDED")
                else:
                    cursor.execute("SELECT is_admin FROM users WHERE user_id=%s", (int(user_id),))
                    user = _cursor_row(cursor)
                    if not user or not bool(user.get("is_admin")):
                        raise HTTPException(status_code=403, detail="SE_KB_ADMIN_REQUIRED")

                document_conflict = _active_document_conflict(
                    cursor,
                    parent_node_id=int(parent_node_id),
                    file_name=file_name,
                    branch_type=branch,
                    doc_code=metadata.get("docCode") or "",
                )
                if document_conflict:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "DOCUMENT_NAME_CONFLICT",
                            "file_name": file_name,
                            "doc_code": metadata.get("docCode") or "",
                            "conflict_field": (
                                "doc_code"
                                if branch == "kiwix" and metadata.get("docCode")
                                else "file_name"
                            ),
                            "object_id": int(document_conflict["object_id"]),
                            "node_id": int(document_conflict["node_id"]),
                            "retryable_as_version": True,
                        },
                    )

                doc_name, extension = split_filename(file_name)
                relative_path, sub_path = _logical_parent_path(cursor, int(parent_node_id))
                upload_mode = "personal" if scope is KbStorageScope.PERSONAL else "admin_kb"
                cursor.execute(
                    """
                    INSERT INTO storage_objects(
                        storage_backend,bucket_name,object_key,doc_code,uploader_user_id,
                        upload_mode,doc_status,doc_name,file_right,relative_path,sub_path,
                        mime_type,size_bytes,source_time_status,uploaded_at,db_updated_at,branch_type
                    ) VALUES('minio',NULL,NULL,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,
                             'unknown',NOW(),NOW(),%s)
                    RETURNING object_id
                    """,
                    (
                        metadata.get("docCode") or "", int(user_id), upload_mode,
                        doc_name, extension, relative_path, sub_path,
                        content_type, file_size, branch,
                    ),
                )
                object_id = int(cursor.fetchone()["object_id"])
                if metadata:
                    cursor.execute(
                        """UPDATE storage_objects
                              SET source_created_at=%s,source_updated_at=%s,
                                  source_time_status=%s
                            WHERE object_id=%s""",
                        (source_date, source_date, "actual" if source_date else "fallback", object_id),
                    )
                location = original_location(
                    scope,
                    object_id,
                    user_id=user_id if scope is KbStorageScope.PERSONAL else None,
                )
                cursor.execute(
                    "UPDATE storage_objects SET bucket_name=%s,object_key=%s WHERE object_id=%s",
                    (location.bucket_name, location.object_key, object_id),
                )
                cursor.execute(
                    """
                    INSERT INTO doc_revisions(
                        doc_id,rev_no,upload_request_id,size_bytes,created_by,storage_status,
                        uploaded_at,created_at
                    ) VALUES(%s,1,%s,%s,%s,'PENDING',NOW(),NOW())
                    RETURNING revision_id
                    """,
                    (object_id, request_uuid, file_size, int(user_id)),
                )
                revision_id = int(cursor.fetchone()["revision_id"])
                cursor.execute(
                    "SELECT COALESCE(MAX(sort_order),-1)+1 AS n FROM tree_nodes WHERE parent_id=%s",
                    (int(parent_node_id),),
                )
                sort_order = int((_cursor_row(cursor) or {}).get("n") or 0)
                cursor.execute(
                    """
                    INSERT INTO tree_nodes(
                        parent_id,kb_root_id,kb_type,kb_owner_user_id,doc_id,is_folder,
                        name,alias,item_type_code,sort_order,created_by,created_at,updated_at
                    ) VALUES(%s,%s,%s,%s,%s,FALSE,%s,%s,%s,%s,%s,NOW(),NOW())
                    RETURNING node_id
                    """,
                    (
                        int(parent_node_id), int(parent["kb_root_id"]),
                        "personal" if scope is KbStorageScope.PERSONAL else "admin",
                        parent.get("kb_owner_user_id"), object_id, doc_name, file_name,
                        int(parent.get("item_type_code") or 10), sort_order, int(user_id),
                    ),
                )
                node_id = int(cursor.fetchone()["node_id"])
                cursor.execute(
                    """
                    INSERT INTO acl_bindings(
                        node_id,policy_code,owner_user_id,target_group_id,created_at,updated_at
                    ) VALUES(%s,%s,%s,NULL,NOW(),NOW())
                    """,
                    (node_id, "PRIVATE" if scope is KbStorageScope.PERSONAL else "PUBLIC", int(user_id)),
                )
                cursor.execute(
                    """
                    INSERT INTO ai_document_info(
                        ai_document_id,object_id,storage_backend,bucket_name,object_key,
                        source_revision_id,user_id,item_type_code,relative_path,sub_path,
                        doc_name,file_right,doc_code,document_schema,document_stage,
                        index_status,created_at,updated_at
                    ) VALUES(CONCAT('aid_',REPLACE(gen_random_uuid()::text,'-','')),%s,'minio',%s,%s,%s,
                             %s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',NOW(),NOW())
                    """,
                    (
                        object_id, location.bucket_name, location.object_key, revision_id,
                        int(user_id), int(parent.get("item_type_code") or 10), relative_path,
                        sub_path, doc_name, extension, metadata.get("docCode") or "",
                        document_schema, document_stage,
                    ),
                )
                if scope is KbStorageScope.PERSONAL:
                    cursor.execute(
                        "UPDATE personal_kb_storage_usage SET reserved_bytes=reserved_bytes+%s WHERE user_id=%s",
                        (file_size, int(user_id)),
                    )
    except HTTPException:
        raise
    except Exception as exc:
        if True:
            connection = get_connection(autocommit=True)
            try:
                with connection.cursor() as cursor:
                    existing = _existing_init(cursor, user_id, request_uuid)
                    if existing:
                        existing["upload_url"] = f"/docs/uploads/{existing['revision_id']}/content"
                        existing["idempotent_replay"] = True
                        return existing
            finally:
                connection.close()
        raise

    return {
        "revision_id": revision_id,
        "object_id": object_id,
        "node_id": node_id,
        "storage_status": "PENDING",
        "file_name": file_name,
        "size_bytes": file_size,
        "bucket_name": location.bucket_name,
        "object_key": location.object_key,
        "upload_url": f"/docs/uploads/{revision_id}/content",
        "idempotent_replay": False,
    }


def _authorize_and_reserve_quota(
    cursor: Any, document: Dict[str, Any], user_id: int, file_size: int, personal: bool,
) -> None:
    if file_size < 0:
        raise HTTPException(status_code=400, detail='INVALID_FILE_SIZE')
    if not personal:
        cursor.execute('SELECT is_admin FROM users WHERE user_id=%s', (int(user_id),))
        user = _cursor_row(cursor)
        if not user or not bool(user.get('is_admin')):
            raise HTTPException(status_code=403, detail='SE_KB_ADMIN_REQUIRED')
        return
    if int(document.get('kb_owner_user_id') or 0) != int(user_id):
        raise HTTPException(status_code=403, detail='PERSONAL_KB_OWNER_REQUIRED')
    cursor.execute(
        '''INSERT INTO personal_kb_storage_usage(user_id,committed_bytes,reserved_bytes)
           VALUES(%s,0,0) ON CONFLICT (user_id) DO NOTHING''',
        (int(user_id),),
    )
    cursor.execute(
        'SELECT committed_bytes,reserved_bytes FROM personal_kb_storage_usage WHERE user_id=%s FOR UPDATE',
        (int(user_id),),
    )
    usage = _cursor_row(cursor) or {}
    projected = int(usage.get('committed_bytes') or 0) + int(usage.get('reserved_bytes') or 0) + file_size
    if projected > settings.MINIO_PERSONAL_QUOTA_BYTES:
        raise HTTPException(status_code=413, detail='PERSONAL_KB_QUOTA_EXCEEDED')
    cursor.execute(
        'UPDATE personal_kb_storage_usage SET reserved_bytes=reserved_bytes+%s WHERE user_id=%s',
        (file_size, int(user_id)),
    )


def initialize_version_upload(
    *, upload_request_id: str, object_id: int, file_name: str, file_size: int,
    content_type: str, user_id: int,
) -> Dict[str, Any]:
    try:
        request_uuid = str(uuid.UUID(str(upload_request_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail='INVALID_UPLOAD_REQUEST_ID') from exc
    file_name = _clean_filename(file_name)
    _validate_document_extension(file_name)
    file_size = int(file_size)
    content_type = str(content_type or 'application/octet-stream').strip()[:100]
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            existing = _existing_init(cursor, user_id, request_uuid)
            if existing:
                if int(existing['object_id']) != int(object_id):
                    raise HTTPException(status_code=409, detail='UPLOAD_REQUEST_ID_CONFLICT')
                existing_revision_id = int(existing['revision_id'])
                existing['upload_url'] = f'/docs/uploads/{existing_revision_id}/content'
                existing['idempotent_replay'] = True
                return existing
            cursor.execute(
                'SELECT so.*,tn.kb_type,tn.kb_owner_user_id FROM storage_objects so '
                'JOIN tree_nodes tn ON tn.doc_id=so.object_id '
                'WHERE so.object_id=%s FOR UPDATE', (int(object_id),),
            )
            document = _cursor_row(cursor)
            if not document:
                raise HTTPException(status_code=404, detail='DOCUMENT_NOT_FOUND')
            if str(document.get('doc_status') or 'active') != 'active':
                raise HTTPException(status_code=409, detail='DOCUMENT_IS_BEING_DELETED')
            _validate_version_extension(document, file_name)
            personal = str(document.get('kb_type') or '').lower() == 'personal'
            _authorize_and_reserve_quota(cursor, document, user_id, file_size, personal)
            scope = KbStorageScope.PERSONAL if personal else KbStorageScope.SE_KB
            location = original_location(
                scope, int(object_id),
                user_id=int(document.get('kb_owner_user_id') or user_id) if personal else None,
            )
            cursor.execute(
                'UPDATE storage_objects SET bucket_name=%s,object_key=%s,mime_type=%s WHERE object_id=%s',
                (location.bucket_name, location.object_key, content_type, int(object_id)),
            )
            cursor.execute('SELECT COALESCE(MAX(rev_no),0)+1 AS n FROM doc_revisions WHERE doc_id=%s', (int(object_id),))
            rev_no = int((_cursor_row(cursor) or {}).get('n') or 1)
            cursor.execute(
                '''INSERT INTO doc_revisions(doc_id,rev_no,upload_request_id,size_bytes,created_by,storage_status,uploaded_at,created_at)
                   VALUES(%s,%s,%s,%s,%s,'PENDING',NOW(),NOW())
                   RETURNING revision_id''',
                (int(object_id), rev_no, request_uuid, file_size, int(user_id)),
            )
            revision_id = int(cursor.fetchone()["revision_id"])
    return {
        'revision_id': revision_id, 'object_id': int(object_id), 'rev_no': rev_no,
        'storage_status': 'PENDING', 'file_name': file_name, 'size_bytes': file_size,
        'bucket_name': location.bucket_name, 'object_key': location.object_key,
        'upload_url': f'/docs/uploads/{revision_id}/content', 'idempotent_replay': False,
    }


def _load_upload_for_update(cursor: Any, revision_id: int, user_id: int) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT dr.*,so.object_id,so.bucket_name,so.object_key,so.mime_type,so.doc_status,
               so.upload_mode,so.uploader_user_id,ai.ai_document_id
          FROM doc_revisions dr
          JOIN storage_objects so ON so.object_id=dr.doc_id
          JOIN ai_document_info ai ON ai.object_id=so.object_id
         WHERE dr.revision_id=%s
         FOR UPDATE
        """,
        (int(revision_id),),
    )
    row = _cursor_row(cursor)
    if not row:
        raise HTTPException(status_code=404, detail="UPLOAD_NOT_FOUND")
    if int(row.get("uploader_user_id") or 0) != int(user_id):
        cursor.execute("SELECT is_admin FROM users WHERE user_id=%s", (int(user_id),))
        admin = _cursor_row(cursor)
        if not admin or not bool(admin.get("is_admin")):
            raise HTTPException(status_code=403, detail="UPLOAD_OWNER_REQUIRED")
    return row


def _begin_content_upload(revision_id: int, user_id: int, content_length: int) -> Dict[str, Any]:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            row = _load_upload_for_update(cursor, revision_id, user_id)
            status = str(row.get("storage_status") or "")
            declared_size = int(row.get("size_bytes") or 0)
            if int(content_length) != declared_size:
                raise HTTPException(status_code=400, detail="CONTENT_LENGTH_MISMATCH")
            if status == "READY":
                row["idempotent_replay"] = True
                return row
            if status == "UPLOADING":
                raise HTTPException(status_code=409, detail="UPLOAD_ALREADY_IN_PROGRESS")
            if str(row.get("doc_status")) != "active":
                raise HTTPException(status_code=409, detail="DOCUMENT_IS_BEING_DELETED")
            if status == "FAILED" and str(row.get("upload_mode")) == "personal":
                cursor.execute(
                    "SELECT committed_bytes,reserved_bytes FROM personal_kb_storage_usage WHERE user_id=%s FOR UPDATE",
                    (int(row["uploader_user_id"]),),
                )
                usage = _cursor_row(cursor) or {}
                projected = int(usage.get("committed_bytes") or 0) + int(
                    usage.get("reserved_bytes") or 0
                ) + declared_size
                if projected > settings.MINIO_PERSONAL_QUOTA_BYTES:
                    raise HTTPException(status_code=413, detail="PERSONAL_KB_QUOTA_EXCEEDED")
                cursor.execute(
                    "UPDATE personal_kb_storage_usage SET reserved_bytes=reserved_bytes+%s WHERE user_id=%s",
                    (declared_size, int(row["uploader_user_id"])),
                )
            cursor.execute(
                """
                UPDATE doc_revisions
                   SET storage_status='UPLOADING',storage_error=NULL
                 WHERE revision_id=%s
                """,
                (int(revision_id),),
            )
            row["storage_status"] = "UPLOADING"
            row["idempotent_replay"] = False
            return row


def _mark_upload_failed(row: Dict[str, Any], message: str) -> None:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT storage_status FROM doc_revisions WHERE revision_id=%s FOR UPDATE",
                (int(row["revision_id"]),),
            )
            current = _cursor_row(cursor)
            if not current or current.get("storage_status") not in {"PENDING", "UPLOADING"}:
                return
            cursor.execute(
                """
                UPDATE doc_revisions
                   SET storage_status='FAILED',storage_error=%s,
                       storage_retry_count=storage_retry_count+1
                 WHERE revision_id=%s
                """,
                (str(message)[:1000], int(row["revision_id"])),
            )
            if str(row.get("upload_mode")) == "personal":
                cursor.execute(
                    """
                    UPDATE personal_kb_storage_usage
                       SET reserved_bytes=GREATEST(reserved_bytes-%s,0)
                     WHERE user_id=%s
                    """,
                    (int(row.get("size_bytes") or 0), int(row["uploader_user_id"])),
                )


def _record_and_finalize(
    row: Dict[str, Any],
    stored: StoredObject,
    *,
    create_parse_job: bool = True,
) -> Dict[str, Any]:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            current = _load_upload_for_update(cursor, int(row["revision_id"]), int(row["uploader_user_id"]))
            if current.get("storage_status") == "READY":
                return current
            cursor.execute(
                """
                UPDATE doc_revisions
                   SET minio_version_id=%s,checksum_sha256=%s,size_bytes=%s,
                       storage_uploaded_at=NOW(),storage_error=NULL
                 WHERE revision_id=%s
                """,
                (stored.version_id, stored.sha256, stored.size_bytes, int(row["revision_id"])),
            )
            if str(current.get("doc_status")) != "active":
                raise HTTPException(status_code=409, detail="DOCUMENT_IS_BEING_DELETED")
            cursor.execute(
                """
                UPDATE doc_revisions SET storage_status='READY' WHERE revision_id=%s
                """,
                (int(row["revision_id"]),),
            )
            cursor.execute(
                """
                UPDATE storage_objects
                   SET size_bytes=%s,checksum_sha256=%s,storage_backend='minio',
                       uploaded_at=NOW(),db_updated_at=NOW()
                 WHERE object_id=%s
                """,
                (stored.size_bytes, stored.sha256, int(row["object_id"])),
            )
            cursor.execute(
                """
                UPDATE ai_document_info
                   SET storage_backend='minio',bucket_name=%s,object_key=%s,
                       source_revision_id=%s,source_minio_version_id=%s,index_status='pending',
                       updated_at=NOW()
                 WHERE object_id=%s
                """,
                (
                    stored.bucket_name, stored.object_key, int(row["revision_id"]),
                    stored.version_id, int(row["object_id"]),
                ),
            )
            if str(row.get("upload_mode")) == "personal":
                cursor.execute(
                    """
                    UPDATE personal_kb_storage_usage
                       SET reserved_bytes=GREATEST(reserved_bytes-%s,0),
                           committed_bytes=committed_bytes+%s
                     WHERE user_id=%s
                    """,
                    (
                        int(row.get("size_bytes") or 0), stored.size_bytes,
                        int(row["uploader_user_id"]),
                    ),
                )
            job_id = None
            if create_parse_job:
                cursor.execute(
                    """
                    SELECT job_id FROM ai_index_jobs
                     WHERE revision_id=%s AND stage='parse'
                       AND status IN ('pending','running','sent','completed')
                     LIMIT 1
                    """,
                    (int(row["revision_id"]),),
                )
                job = _cursor_row(cursor)
                if not job:
                    action = _parse_action_for_revision(row)
                    cursor.execute(
                        """
                        INSERT INTO ai_index_jobs(
                            ai_document_id,object_id,revision_id,action,stage,status,retry_count,
                            scheduled_at,created_by,created_at,updated_at
                        ) VALUES(%s,%s,%s,'add','parse','pending',0,NOW(),%s,NOW(),NOW())
                        RETURNING job_id
                        """,
                        (
                            row["ai_document_id"], int(row["object_id"]), int(row["revision_id"]),
                            int(row["uploader_user_id"]),
                        ),
                    )
                    job_id = int(cursor.fetchone()["job_id"])
                    if action != 'add':
                        cursor.execute(
                            'UPDATE ai_index_jobs SET action=%s WHERE job_id=%s',
                            (action, job_id),
                        )
                else:
                    job_id = int(job["job_id"])
    return {
        "revision_id": int(row["revision_id"]),
        "object_id": int(row["object_id"]),
        "storage_status": "READY",
        "minio_version_id": stored.version_id,
        "size_bytes": stored.size_bytes,
        "checksum_sha256": stored.sha256,
        "parse_job_id": job_id,
        "idempotent_replay": False,
    }


def _copy_source(object_id: int) -> Dict[str, Any]:
    row = fetch_one(_COPY_SOURCE_SQL, (int(object_id),))
    if not row:
        raise HTTPException(status_code=409, detail='MINIO_SOURCE_VERSION_NOT_READY')
    return row


def copy_document_to_personal(*, source_object_id: int, parent_node_id: int, file_name: str, user_id: int) -> Dict[str, Any]:
    require_object_permission(user_id, source_object_id, 'r')
    source = _copy_source(source_object_id)
    checksum = str(source.get('source_checksum') or '').strip().lower()
    if len(checksum) != 64:
        raise HTTPException(status_code=409, detail='MINIO_SOURCE_CHECKSUM_MISSING')
    file_name = _clean_filename(file_name)
    _, copied_extension = split_filename(file_name)
    source_extension = str(source.get('file_right') or '').lstrip('.').lower()
    if copied_extension.lower() != source_extension:
        raise HTTPException(status_code=409, detail='COPY_FILE_TYPE_MISMATCH')
    initialized = initialize_upload(
        upload_request_id=str(uuid.uuid4()),
        parent_node_id=parent_node_id,
        file_name=file_name,
        file_size=int(source.get('size_bytes') or 0),
        content_type=source.get('mime_type') or 'application/octet-stream',
        branch_type=source.get('branch_type') or 'general',
        user_id=user_id,
    )
    row = _begin_content_upload(
        int(initialized['revision_id']),
        user_id,
        int(source.get('size_bytes') or 0),
    )
    storage = get_minio_storage_service()
    stored = None
    try:
        stored = storage.copy_object(
            str(source['bucket_name']),
            str(source['object_key']),
            str(initialized['bucket_name']),
            str(initialized['object_key']),
            source_version_id=str(source['minio_version_id']),
            sha256=checksum,
        )
        result = _record_and_finalize(row, stored)
    except Exception as exc:
        if stored and stored.version_id:
            try:
                storage.remove_version(stored.bucket_name, stored.object_key, stored.version_id)
            except Exception:
                pass
        _mark_upload_failed(row, str(exc))
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, MinioStorageError):
            raise HTTPException(status_code=502, detail='MINIO_COPY_FAILED') from exc
        raise
    return {**result, 'node_id': int(initialized['node_id'])}


async def upload_content(
    *,
    revision_id: int,
    user_id: int,
    content_length: int,
    chunks: AsyncIterator[bytes],
    create_parse_job: bool = True,
) -> Dict[str, Any]:
    row = _begin_content_upload(revision_id, user_id, content_length)
    if row.get("idempotent_replay"):
        return {
            "revision_id": int(row["revision_id"]),
            "object_id": int(row["object_id"]),
            "storage_status": "READY",
            "minio_version_id": row.get("minio_version_id"),
            "size_bytes": int(row.get("size_bytes") or 0),
            "checksum_sha256": row.get("checksum_sha256"),
            "idempotent_replay": True,
        }

    reader = _QueuedReader(settings.MINIO_UPLOAD_QUEUE_CHUNKS)
    storage = get_minio_storage_service()
    metadata = {
        "object-id": str(row["object_id"]),
        "revision-id": str(row["revision_id"]),
        "upload-request-id": str(row.get("upload_request_id") or ""),
        "declared-size": str(content_length),
    }

    async with _UPLOAD_SEMAPHORE:
        upload_task = asyncio.create_task(
            asyncio.to_thread(
                storage.upload_stream,
                str(row["bucket_name"]),
                str(row["object_key"]),
                reader,
                length=int(content_length),
                content_type=str(row.get("mime_type") or "application/octet-stream"),
                metadata=metadata,
            )
        )
        supplied = 0
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                supplied += len(chunk)
                if supplied > int(content_length):
                    raise HTTPException(status_code=400, detail="CONTENT_LENGTH_MISMATCH")
                await _queue_put(reader, bytes(chunk), upload_task)
            if supplied != int(content_length):
                raise HTTPException(status_code=400, detail="CONTENT_LENGTH_MISMATCH")
            await _queue_put(reader, _END, upload_task)
            stored = await upload_task
        except Exception as exc:
            if not upload_task.done():
                try:
                    await _queue_put(reader, _StreamFailure(exc), upload_task)
                except Exception:
                    pass
            try:
                await upload_task
            except Exception:
                pass
            _mark_upload_failed(row, "UPLOAD_STREAM_FAILED")
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, MinioStorageError):
                raise HTTPException(status_code=503, detail="MINIO_UPLOAD_FAILED") from exc
            raise HTTPException(status_code=500, detail="UPLOAD_STREAM_FAILED") from exc

    try:
        return _record_and_finalize(row, stored, create_parse_job=create_parse_job)
    except HTTPException as exc:
        if exc.detail == "DOCUMENT_IS_BEING_DELETED" and stored.version_id:
            try:
                storage.remove_version(stored.bucket_name, stored.object_key, stored.version_id)
            finally:
                _mark_upload_failed(row, "DOCUMENT_DELETED_DURING_UPLOAD")
        raise
    except Exception as exc:
        # Keep the valid MinIO version. Reconciliation can recover it from object metadata.
        raise HTTPException(status_code=500, detail="UPLOAD_FINALIZATION_PENDING_RECOVERY") from exc


def reconcile_stale_uploads(*, dry_run: bool = True, limit: int = 200) -> Dict[str, Any]:
    cutoff = datetime.now() - timedelta(minutes=settings.MINIO_UPLOAD_STALE_MINUTES)
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dr.*,so.object_id,so.bucket_name,so.object_key,so.mime_type,so.doc_status,
                       so.upload_mode,so.uploader_user_id,ai.ai_document_id
                  FROM doc_revisions dr
                  JOIN storage_objects so ON so.object_id=dr.doc_id
                  JOIN ai_document_info ai ON ai.object_id=so.object_id
                 WHERE so.storage_backend='minio'
                   AND dr.upload_request_id IS NOT NULL
                   AND dr.storage_status IN ('PENDING','UPLOADING')
                   AND dr.created_at < %s
                 ORDER BY dr.created_at,dr.revision_id
                 LIMIT %s
                """,
                (cutoff, max(1, int(limit))),
            )
            rows = [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()

    result = {"examined": len(rows), "recovered": 0, "failed": 0, "dry_run": bool(dry_run)}
    storage = get_minio_storage_service()
    for row in rows:
        if dry_run:
            continue
        stored: StoredObject | None = None
        if row.get("minio_version_id"):
            try:
                stat = storage.stat_object(
                    row["bucket_name"], row["object_key"], version_id=row["minio_version_id"]
                )
                stored = StoredObject(
                    bucket_name=stat.bucket_name, object_key=stat.object_key,
                    version_id=stat.version_id, etag=stat.etag, size_bytes=stat.size_bytes,
                    sha256=str(row.get("checksum_sha256") or ""),
                )
            except MinioObjectNotFoundError:
                stored = None
        else:
            for version in storage.list_versions(row["bucket_name"], row["object_key"]):
                if version.object_key != row["object_key"] or version.is_delete_marker:
                    continue
                try:
                    stat = storage.stat_object(
                        row["bucket_name"], row["object_key"], version_id=version.version_id
                    )
                except MinioObjectNotFoundError:
                    continue
                metadata = {str(key).lower(): str(value) for key, value in stat.metadata.items()}
                revision_token = metadata.get("x-amz-meta-revision-id") or metadata.get("revision-id")
                request_token = metadata.get("x-amz-meta-upload-request-id") or metadata.get("upload-request-id")
                if revision_token != str(row["revision_id"]):
                    continue
                if request_token and request_token != str(row.get("upload_request_id") or ""):
                    continue
                digest = hashlib.sha256()
                for chunk in storage.iter_object(
                    row["bucket_name"], row["object_key"], version_id=version.version_id
                ):
                    digest.update(chunk)
                stored = StoredObject(
                    bucket_name=stat.bucket_name, object_key=stat.object_key,
                    version_id=stat.version_id, etag=stat.etag, size_bytes=stat.size_bytes,
                    sha256=digest.hexdigest(),
                )
                break
        if stored and stored.sha256:
            _record_and_finalize(row, stored)
            result["recovered"] += 1
        else:
            _mark_upload_failed({**row, "storage_status": "UPLOADING"}, "STALE_UPLOAD_NOT_RECOVERABLE")
            result["failed"] += 1
    return result
