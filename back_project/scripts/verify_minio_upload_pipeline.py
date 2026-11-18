from __future__ import annotations

import asyncio
import io
import uuid

from app.repositories.transaction import db_transaction
from app.services.common.minio_key_builder import KbStorageScope, artifact_location
from app.services.common.minio_storage_service import (
    MinioConnectionError,
    get_minio_storage_service,
)
from app.services.features import docs_embed_service, docs_service
from app.services.features.kiwix_preparsed_service import ingest_markdown
from starlette.requests import Request
from app.services.features.minio_upload_service import (
    initialize_upload,
    initialize_version_upload,
    upload_content,
)
from app.utils.db import get_connection


async def _chunks(payload: bytes):
    midpoint = max(1, len(payload) // 2)
    yield payload[:midpoint]
    yield payload[midpoint:]


def _personal_test_parent() -> tuple[int, int]:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tn.node_id,tn.kb_owner_user_id
                  FROM tree_nodes tn
                  JOIN users u ON u.user_id=tn.kb_owner_user_id
                 WHERE tn.kb_type='personal' AND tn.is_folder=TRUE
                   AND tn.kb_owner_user_id IS NOT NULL
                   AND COALESCE(u.is_active,TRUE)=TRUE
                 ORDER BY tn.node_id LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError("No personal KB folder is available for integration testing")
            return int(row["node_id"]), int(row["kb_owner_user_id"])
    finally:
        connection.close()


def _admin_test_parent() -> tuple[int, int]:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT tn.node_id,u.user_id
                     FROM tree_nodes tn
                     JOIN users u ON u.is_admin=TRUE AND COALESCE(u.is_active,TRUE)=TRUE
                    WHERE tn.kb_type='admin' AND tn.is_folder=TRUE
                    ORDER BY CASE WHEN tn.parent_id IS NULL THEN 1 ELSE 0 END,tn.node_id,u.user_id
                    LIMIT 1'''
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError('No admin KB folder and active admin are available for integration testing')
            return int(row['node_id']), int(row['user_id'])
    finally:
        connection.close()


def _cleanup(
    object_id: int, revision_id: int, bucket: str, key: str, user_id: int,
    *, adjust_personal_usage: bool = True,
) -> None:
    storage = get_minio_storage_service()
    minio_available = True
    try:
        storage.permanent_delete_object(bucket, key)
    except MinioConnectionError:
        minio_available = False
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT
                       COALESCE(SUM(CASE WHEN storage_status='READY' THEN size_bytes ELSE 0 END),0) AS committed,
                       COALESCE(SUM(CASE WHEN storage_status IN ('PENDING','UPLOADING') THEN size_bytes ELSE 0 END),0) AS reserved
                     FROM doc_revisions WHERE doc_id=%s''',
                (object_id,),
            )
            usage = cursor.fetchone() or {}
            cursor.execute(
                "DELETE FROM ai_callback_events WHERE job_id IN (SELECT job_id FROM ai_index_jobs WHERE object_id=%s)",
                (object_id,),
            )
            cursor.execute("DELETE FROM ai_index_jobs WHERE object_id=%s", (object_id,))
            cursor.execute("DELETE FROM ai_parse_artifacts WHERE revision_id=%s", (revision_id,))
            cursor.execute("DELETE FROM tree_nodes WHERE doc_id=%s", (object_id,))
            cursor.execute("DELETE FROM ai_document_info WHERE object_id=%s", (object_id,))
            cursor.execute("DELETE FROM doc_revisions WHERE doc_id=%s", (object_id,))
            cursor.execute("DELETE FROM storage_objects WHERE object_id=%s", (object_id,))
            if adjust_personal_usage:
                cursor.execute(
                    '''UPDATE personal_kb_storage_usage
                          SET committed_bytes=GREATEST(committed_bytes-%s, 0),
                              reserved_bytes=GREATEST(reserved_bytes-%s, 0)
                        WHERE user_id=%s''',
                    (
                        int(usage.get('committed') or 0),
                        int(usage.get('reserved') or 0), user_id,
                    ),
                )
    if minio_available:
        remaining = [item for item in storage.list_versions(bucket, key) if item.object_key == key]
        if remaining:
            raise RuntimeError("MinIO pipeline test versions remain after cleanup")
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM storage_objects WHERE object_id=%s) +
                  (SELECT COUNT(*) FROM doc_revisions WHERE doc_id=%s) +
                  (SELECT COUNT(*) FROM tree_nodes WHERE doc_id=%s) +
                  (SELECT COUNT(*) FROM ai_document_info WHERE object_id=%s) +
                  (SELECT COUNT(*) FROM ai_index_jobs WHERE object_id=%s) AS remaining
                """,
                (object_id, object_id, object_id, object_id, object_id),
            )
            if int((cursor.fetchone() or {}).get("remaining") or 0) != 0:
                raise RuntimeError("DB pipeline test records remain after cleanup")
    finally:
        connection.close()


async def run() -> None:
    parent_id, user_id = _personal_test_parent()
    payload = b"SpaceAI MinIO pipeline integration test"
    initialized = None
    try:
        request_id = str(uuid.uuid4())
        initialized = initialize_upload(
            upload_request_id=request_id,
            parent_node_id=parent_id,
            file_name=f"__minio_pipeline_test_{uuid.uuid4().hex}.txt",
            file_size=len(payload),
            content_type="text/plain; charset=utf-8",
            branch_type="general",
            user_id=user_id,
        )
        replayed = initialize_upload(
            upload_request_id=request_id,
            parent_node_id=parent_id,
            file_name=initialized["file_name"],
            file_size=len(payload),
            content_type="text/plain; charset=utf-8",
            branch_type="general",
            user_id=user_id,
        )
        if replayed["revision_id"] != initialized["revision_id"] or not replayed["idempotent_replay"]:
            raise RuntimeError("upload initialization idempotency failed")
        completed = await upload_content(
            revision_id=initialized["revision_id"],
            user_id=user_id,
            content_length=len(payload),
            chunks=_chunks(payload),
        )
        if completed.get("storage_status") != "READY" or not completed.get("parse_job_id"):
            raise RuntimeError("pipeline integration did not reach READY with a parse job")
        print("MinIO upload pipeline: DB init/stream/finalize/job passed")
        response = docs_service.download_doc(
            int(initialized["object_id"]), range="bytes=0-4", user_id=user_id
        )
        downloaded = bytearray()
        async for chunk in response.body_iterator:
            downloaded.extend(chunk)
        if bytes(downloaded) != payload[:5] or response.status_code != 206:
            raise RuntimeError("MinIO document Range download failed")
        print("MinIO upload pipeline: idempotency/download range passed")
        replacement = b'SpaceAI MinIO replacement version'
        print('MinIO upload pipeline: replacement revision init starting')
        version_request_id = str(uuid.uuid4())
        version = initialize_version_upload(
            upload_request_id=version_request_id,
            object_id=int(initialized['object_id']),
            file_name=initialized['file_name'],
            file_size=len(replacement),
            content_type='text/plain; charset=utf-8',
            user_id=user_id,
        )
        version_replay = initialize_version_upload(
            upload_request_id=version_request_id,
            object_id=int(initialized['object_id']),
            file_name=initialized['file_name'],
            file_size=len(replacement),
            content_type='text/plain; charset=utf-8',
            user_id=user_id,
        )
        if version_replay['revision_id'] != version['revision_id'] or not version_replay['idempotent_replay']:
            raise RuntimeError('replacement revision initialization idempotency failed')
        print('MinIO upload pipeline: replacement revision stream starting')
        version_completed = await upload_content(
            revision_id=int(version['revision_id']),
            user_id=user_id,
            content_length=len(replacement),
            chunks=_chunks(replacement),
        )
        print('MinIO upload pipeline: replacement revision stream completed')
        connection = get_connection(autocommit=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT action FROM ai_index_jobs WHERE job_id=%s', (version_completed['parse_job_id'],))
                version_job = cursor.fetchone() or {}
        finally:
            connection.close()
        if version_job.get('action') != 'update':
            raise RuntimeError('replacement revision did not create an update parse job')
        latest = docs_service.download_doc(int(initialized['object_id']), range=None, user_id=user_id)
        latest_bytes = bytearray()
        async for chunk in latest.body_iterator:
            latest_bytes.extend(chunk)
        if bytes(latest_bytes) != replacement:
            raise RuntimeError('latest MinIO revision download failed')
        print('MinIO upload pipeline: replacement revision/update job passed')
        preview_request = Request({
            "type": "http",
            "method": "GET",
            "path": "/docs-embed/test",
            "headers": [(b"range", b"bytes=5-9")],
        })
        preview = docs_embed_service._stream_minio_object(
            int(initialized["object_id"]), preview_request
        )
        preview_bytes = bytearray()
        async for chunk in preview.body_iterator:
            preview_bytes.extend(chunk)
        if bytes(preview_bytes) != payload[5:10] or preview.status_code != 206:
            raise RuntimeError("MinIO signed preview Range stream failed")
        print("MinIO upload pipeline: signed preview range passed")
    finally:
        if initialized:
            _cleanup(
                int(initialized["object_id"]),
                int(initialized["revision_id"]),
                str(initialized["bucket_name"]),
                str(initialized["object_key"]),
                user_id,
            )
            print("MinIO upload pipeline: DB/MinIO test records cleanup passed")


async def run_kiwix() -> None:
    parent_id, user_id = _admin_test_parent()
    payload = b'SpaceAI KIWIX file upload integration test'
    initialized = None
    try:
        initialized = initialize_upload(
            upload_request_id=str(uuid.uuid4()),
            parent_node_id=parent_id,
            file_name=f'__minio_pipeline_test_kiwix_{uuid.uuid4().hex}.pdf',
            file_size=len(payload),
            content_type='application/pdf',
            branch_type='kiwix',
            user_id=user_id,
        )
        completed = await upload_content(
            revision_id=int(initialized['revision_id']), user_id=user_id,
            content_length=len(payload), chunks=_chunks(payload),
        )
        connection = get_connection(autocommit=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT storage_backend,branch_type FROM storage_objects WHERE object_id=%s',
                    (initialized['object_id'],),
                )
                document = cursor.fetchone() or {}
        finally:
            connection.close()
        if completed.get('storage_status') != 'READY':
            raise RuntimeError('KIWIX upload did not reach READY')
        if str(document.get('storage_backend')).lower() != 'minio' or str(document.get('branch_type')).lower() != 'kiwix':
            raise RuntimeError('KIWIX upload metadata was not stored as MinIO/KIWIX')
        response = docs_service.download_doc(int(initialized['object_id']), range=None, user_id=user_id)
        downloaded = bytearray()
        async for chunk in response.body_iterator:
            downloaded.extend(chunk)
        if bytes(downloaded) != payload:
            raise RuntimeError('KIWIX common document download failed')
        print('MinIO upload pipeline: KIWIX file/common viewer source passed')
    finally:
        if initialized:
            _cleanup(
                int(initialized['object_id']), int(initialized['revision_id']),
                str(initialized['bucket_name']), str(initialized['object_key']), user_id,
                adjust_personal_usage=False,
            )
            print('MinIO upload pipeline: KIWIX test records cleanup passed')


async def run_kiwix_preparsed() -> None:
    from PIL import Image

    parent_id, user_id = _admin_test_parent()
    source_name = f'__kiwix_preparsed_test_{uuid.uuid4().hex}.md'
    image_buffer = io.BytesIO()
    Image.new('RGB', (8, 8), color=(35, 120, 210)).save(image_buffer, format='PNG')
    markdown = '# KIWIX 사전 파싱 시험\n\n![sample](images/sample.png)\n\n<!-- PAGE:2 -->\n두 번째 페이지'.encode('utf-8')
    result = None
    try:
        result = await ingest_markdown(
            upload_request_id=str(uuid.uuid4()),
            parent_node_id=parent_id,
            source_file_name=source_name,
            markdown_bytes=markdown,
            assets={'images/sample.png': image_buffer.getvalue()},
            user_id=user_id,
        )
        if result.get('parse_status') != 'completed' or result.get('index_status') != 'pending':
            raise RuntimeError('KIWIX Markdown did not produce completed parse and pending index states')
        response = docs_service.download_doc(int(result['object_id']), range=None, user_id=user_id)
        pdf_bytes = bytearray()
        async for chunk in response.body_iterator:
            pdf_bytes.extend(chunk)
        if not bytes(pdf_bytes).startswith(b'%PDF-'):
            raise RuntimeError('KIWIX Markdown viewer source is not a PDF')
        connection = get_connection(autocommit=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    '''SELECT j.stage,j.status,a.artifact_type,a.artifact_status
                         FROM ai_index_jobs j
                         LEFT JOIN ai_parse_artifacts a ON a.artifact_id=j.artifact_id
                        WHERE j.object_id=%s ORDER BY j.job_id''',
                    (int(result['object_id']),),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        states = {(row['stage'], row['status']) for row in rows}
        if ('parse', 'completed') not in states or ('index', 'pending') not in states:
            raise RuntimeError(f'Unexpected KIWIX Markdown pipeline states: {states}')
        if not any(row.get('artifact_type') == 'manifest' and row.get('artifact_status') == 'READY' for row in rows):
            raise RuntimeError('KIWIX Markdown READY manifest was not recorded')
        print('MinIO upload pipeline: KIWIX Markdown PDF/artifact/job states passed')
    finally:
        if result:
            connection = get_connection(autocommit=True)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT bucket_name,object_key FROM storage_objects WHERE object_id=%s',
                        (int(result['object_id']),),
                    )
                    source_location = cursor.fetchone() or {}
            finally:
                connection.close()
            location = artifact_location(
                KbStorageScope.SE_KB,
                int(result['object_id']),
                int(result['revision_id']),
                'manifest.json',
            )
            prefix = location.object_key.rsplit('/', 1)[0] + '/'
            get_minio_storage_service().permanent_delete_prefix(location.bucket_name, prefix)
            _cleanup(
                int(result['object_id']), int(result['revision_id']),
                str(source_location['bucket_name']), str(source_location['object_key']), user_id,
                adjust_personal_usage=False,
            )
            print('MinIO upload pipeline: KIWIX Markdown test records cleanup passed')


async def run_all() -> None:
    await run()
    await run_kiwix()
    await run_kiwix_preparsed()


def main() -> None:
    asyncio.run(run_all())


if __name__ == "__main__":
    main()
