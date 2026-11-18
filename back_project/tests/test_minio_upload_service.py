from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.config import settings
from app.services.common.minio_storage_service import StoredObject
from app.services.features import minio_upload_service


class _ConflictCursor:
    def __init__(self, row=None) -> None:
        self.row = row
        self.calls = []

    def execute(self, query, params=None) -> None:
        self.calls.append((query, params))

    def fetchone(self):
        return self.row


class _FakeStorage:
    def __init__(self) -> None:
        self.payload = b""
        self.calls = 0

    def upload_stream(self, bucket, key, stream, *, length, content_type, metadata):
        self.calls += 1
        chunks = []
        while True:
            chunk = stream.read(4)
            if not chunk:
                break
            chunks.append(chunk)
        self.payload = b"".join(chunks)
        if len(self.payload) != length:
            raise OSError("length mismatch")
        return StoredObject(
            bucket_name=bucket,
            object_key=key,
            version_id="version-test",
            etag="etag-test",
            size_bytes=len(self.payload),
            sha256=hashlib.sha256(self.payload).hexdigest(),
        )


def _row(status: str = "PENDING"):
    return {
        "revision_id": 101,
        "object_id": 348,
        "upload_request_id": "03541a3f-d4e5-4d7a-9675-13148d46fe98",
        "bucket_name": settings.MINIO_PERSONAL_BUCKET,
        "object_key": "users/15/documents/348",
        "mime_type": "text/plain",
        "size_bytes": 11,
        "storage_status": status,
        "upload_mode": "personal",
        "uploader_user_id": 15,
        "ai_document_id": "aid_test",
        "idempotent_replay": status == "READY",
        "minio_version_id": "version-ready" if status == "READY" else None,
        "checksum_sha256": "abc" if status == "READY" else None,
    }


class MinioUploadServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_without_local_file_and_finalizes(self) -> None:
        fake_storage = _FakeStorage()

        async def chunks():
            yield b"hello "
            yield b"world"

        expected = {
            "revision_id": 101,
            "object_id": 348,
            "storage_status": "READY",
        }
        with (
            patch.object(minio_upload_service, "_begin_content_upload", return_value=_row()),
            patch.object(minio_upload_service, "get_minio_storage_service", return_value=fake_storage),
            patch.object(minio_upload_service, "_record_and_finalize", return_value=expected) as finalize,
        ):
            result = await minio_upload_service.upload_content(
                revision_id=101,
                user_id=15,
                content_length=11,
                chunks=chunks(),
            )

        self.assertEqual(result, expected)
        self.assertEqual(fake_storage.payload, b"hello world")
        finalize.assert_called_once()

    async def test_body_length_mismatch_marks_failed(self) -> None:
        fake_storage = _FakeStorage()

        async def chunks():
            yield b"short"

        with (
            patch.object(minio_upload_service, "_begin_content_upload", return_value=_row()),
            patch.object(minio_upload_service, "get_minio_storage_service", return_value=fake_storage),
            patch.object(minio_upload_service, "_mark_upload_failed") as failed,
        ):
            with self.assertRaises(HTTPException) as caught:
                await minio_upload_service.upload_content(
                    revision_id=101,
                    user_id=15,
                    content_length=11,
                    chunks=chunks(),
                )

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail, "CONTENT_LENGTH_MISMATCH")
        failed.assert_called_once()

    async def test_ready_retry_is_idempotent(self) -> None:
        async def no_chunks():
            if False:
                yield b""

        with (
            patch.object(minio_upload_service, "_begin_content_upload", return_value=_row("READY")),
            patch.object(minio_upload_service, "get_minio_storage_service") as storage,
        ):
            result = await minio_upload_service.upload_content(
                revision_id=101,
                user_id=15,
                content_length=11,
                chunks=no_chunks(),
            )

        self.assertTrue(result["idempotent_replay"])
        self.assertEqual(result["storage_status"], "READY")
        storage.assert_not_called()

    def test_filename_rejects_paths(self) -> None:
        self.assertEqual(minio_upload_service._clean_filename("report.pdf"), "report.pdf")
        for value in ("../report.pdf", "folder/report.pdf", "folder\\report.pdf", ""):
            with self.assertRaises(HTTPException):
                minio_upload_service._clean_filename(value)

    def test_document_extension_allowlist(self) -> None:
        for extension in minio_upload_service.ALLOWED_DOCUMENT_EXTENSIONS:
            self.assertEqual(
                minio_upload_service._validate_document_extension(f"report.{extension.upper()}"),
                extension,
            )
        for file_name in ("report.exe", "report", "report.pdf.exe"):
            with self.assertRaises(HTTPException) as caught:
                minio_upload_service._validate_document_extension(file_name)
            self.assertEqual(caught.exception.status_code, 415)
            self.assertEqual(caught.exception.detail["code"], "UNSUPPORTED_DOCUMENT_TYPE")

    def test_existing_document_revision_uses_update_action(self) -> None:
        self.assertEqual(minio_upload_service._parse_action_for_revision({'rev_no': 1}), 'add')
        self.assertEqual(minio_upload_service._parse_action_for_revision({'rev_no': 2}), 'update')

    def test_only_kiwix_markdown_uses_preparsed_flow(self) -> None:
        self.assertTrue(minio_upload_service.is_kiwix_preparsed_markdown("KIWIX", "manual.MD"))
        self.assertFalse(minio_upload_service.is_kiwix_preparsed_markdown("kiwix", "manual.pdf"))
        self.assertFalse(minio_upload_service.is_kiwix_preparsed_markdown("general", "manual.md"))

    def test_normalizes_documents_json_metadata(self) -> None:
        result = minio_upload_service.normalize_kiwix_metadata({
            "platform": "kiwix",
            "prjCode": "JOMO",
            "docCode": "1_523",
            "fileName": "sample",
            "fileExt": ".MD",
            "fileCreate": "2022-10-03",
        })
        self.assertEqual(result["platform"], "KIWIX")
        self.assertEqual(result["docCode"], "1_523")
        self.assertEqual(result["fileExt"], "md")
        self.assertEqual(
            minio_upload_service._kiwix_source_date(result).strftime("%Y-%m-%d"),
            "2022-10-03",
        )

    def test_kiwix_metadata_allows_missing_doc_code(self) -> None:
        result = minio_upload_service.normalize_kiwix_metadata({
            "platform": "KIWIX",
            "prjCode": "JOMO",
            "fileName": "sample",
            "fileCreate": "2024-01-02 03:04",
        })
        self.assertEqual(result["docCode"], "")

    def test_kiwix_document_metadata_is_preserved_verbatim(self) -> None:
        result = minio_upload_service.normalize_kiwix_metadata({
            "folderName": "JOMO",
            "fileName": "sample",
            "fileCreate": "2024-01-02 03:04",
            "schema": "  LAW Directive / Ver.2  ",
            "stage": "PDR-검토 단계",
        })
        self.assertEqual(result["schema"], "  LAW Directive / Ver.2  ")
        self.assertEqual(result["stage"], "PDR-검토 단계")

    def test_kiwix_document_metadata_allows_null_and_empty_string(self) -> None:
        result = minio_upload_service.normalize_kiwix_metadata({
            "folderName": "JOMO",
            "fileName": "sample",
            "fileCreate": "2024-01-02 03:04",
            "schema": None,
            "stage": "",
        })
        self.assertIsNone(result["schema"])
        self.assertEqual(result["stage"], "")

    def test_kiwix_document_metadata_rejects_non_strings(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            minio_upload_service.normalize_kiwix_metadata({
                "folderName": "JOMO",
                "fileName": "sample",
                "fileCreate": "2024-01-02 03:04",
                "schema": ["LAW"],
            })
        self.assertEqual(caught.exception.detail, "INVALID_KIWIX_DOCUMENT_METADATA")

    def test_kiwix_file_name_does_not_duplicate_extension(self) -> None:
        result = minio_upload_service.normalize_kiwix_metadata({
            "folderName": "JOMO",
            "fileName": "sample.md",
            "fileExt": "md",
            "fileCreate": "2024-01-02 03:04",
        })
        self.assertEqual(result["fileName"], "sample")

    def test_kiwix_metadata_keys_are_case_and_separator_insensitive(self) -> None:
        result = minio_upload_service.normalize_kiwix_metadata({
            "FOLDER_NAME": "XOKA",
            "file_name": "sample",
            "FILE_CREATE": "2024-01-02T03:04:05Z",
        })
        self.assertEqual(result["prjCode"], "XOKA")
        self.assertEqual(result["fileName"], "sample")

    def test_kiwix_source_date_accepts_common_source_formats(self) -> None:
        samples = {
            "2024-01-02T03:04:05Z": (2024, 1, 2, 3, 4, 5),
            "2024-01-02T03:04:05+09:00": (2024, 1, 2, 3, 4, 5),
            "20240102": (2024, 1, 2, 0, 0, 0),
            "2024년 1월 2일 03:04:05": (2024, 1, 2, 3, 4, 5),
            "Tue, 02 Jan 2024 03:04:05 GMT": (2024, 1, 2, 3, 4, 5),
        }
        for raw, expected in samples.items():
            with self.subTest(raw=raw):
                parsed = minio_upload_service._kiwix_source_date({"fileCreate": raw})
                self.assertEqual(
                    (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second),
                    expected,
                )

    def test_kiwix_metadata_still_requires_project_and_file_name(self) -> None:
        for metadata in (
            {"platform": "KIWIX", "fileName": "sample"},
            {"platform": "KIWIX", "prjCode": "JOMO"},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(HTTPException) as caught:
                minio_upload_service.normalize_kiwix_metadata(metadata)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(caught.exception.detail, "KIWIX_METADATA_REQUIRED")

    def test_kiwix_metadata_object_is_required(self) -> None:
        for metadata in (None, {}):
            with self.subTest(metadata=metadata), self.assertRaises(HTTPException) as caught:
                minio_upload_service.normalize_kiwix_metadata(metadata)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(caught.exception.detail, "KIWIX_METADATA_REQUIRED")

    def test_kiwix_conflict_uses_doc_code_instead_of_file_name(self) -> None:
        cursor = _ConflictCursor()
        result = minio_upload_service._active_document_conflict(
            cursor,
            parent_node_id=10,
            file_name="duplicate.pdf",
            branch_type="kiwix",
            doc_code="31_72",
        )
        self.assertIsNone(result)
        self.assertEqual(len(cursor.calls), 2)
        self.assertIn("pg_advisory_xact_lock", cursor.calls[0][0])
        self.assertIn("so.doc_code=%s", cursor.calls[1][0])
        self.assertNotIn("tn.alias=%s", cursor.calls[1][0])
        self.assertEqual(cursor.calls[1][1], ("31_72",))

    def test_general_conflict_still_uses_file_name(self) -> None:
        cursor = _ConflictCursor()
        minio_upload_service._active_document_conflict(
            cursor,
            parent_node_id=10,
            file_name="duplicate.pdf",
            branch_type="general",
            doc_code="",
        )
        self.assertEqual(len(cursor.calls), 1)
        self.assertIn("tn.alias=%s", cursor.calls[0][0])
        self.assertNotIn("so.doc_code=%s", cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1], (10, "duplicate.pdf"))

    def test_generic_init_rejects_kiwix_markdown_before_db_access(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            minio_upload_service.initialize_upload(
                upload_request_id="03541a3f-d4e5-4d7a-9675-13148d46fe98",
                parent_node_id=1,
                file_name="manual.md",
                file_size=10,
                content_type="text/markdown",
                branch_type="kiwix",
                user_id=15,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "KIWIX_PREPARSED_UPLOAD_REQUIRED")
        self.assertTrue(caught.exception.detail["retryable"])

    def test_version_upload_requires_same_extension(self) -> None:
        minio_upload_service._validate_version_extension({'file_right': 'pdf'}, 'revision.PDF')
        with self.assertRaises(HTTPException) as caught:
            minio_upload_service._validate_version_extension({'file_right': 'pdf'}, 'revision.docx')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, 'VERSION_FILE_TYPE_MISMATCH')

    def test_version_init_rejects_invalid_request_id_before_db_access(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            minio_upload_service.initialize_version_upload(
                upload_request_id='not-a-uuid',
                object_id=348,
                file_name='revision.pdf',
                file_size=10,
                content_type='application/pdf',
                user_id=15,
            )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail, 'INVALID_UPLOAD_REQUEST_ID')


if __name__ == "__main__":
    unittest.main()
