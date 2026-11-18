from __future__ import annotations

import json
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.services.features import kiwix_preparsed_service


class KiwixPreparsedServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_normalize_asset_path(self) -> None:
        self.assertEqual(
            kiwix_preparsed_service.normalize_asset_path(r'images\folder\sample.png'),
            'images/folder/sample.png',
        )
        self.assertEqual(
            kiwix_preparsed_service.normalize_asset_path('./assets/images/sample.png'),
            'images/sample.png',
        )
        for value in ('', '../sample.png', '/images/sample.png'):
            with self.assertRaises(HTTPException):
                kiwix_preparsed_service.normalize_asset_path(value)

    def test_markdown_asset_paths_are_normalized_once(self) -> None:
        assets = {'images/sample image.png': b'image'}
        result = kiwix_preparsed_service.normalize_markdown_asset_references(
            (
                '![one](images/sample%20image.png)\n'
                '![two](./assets/images/sample%20image.png)\n'
                '<img src="assets/images/sample%20image.png">'
            ).encode('utf-8'),
            assets,
        ).decode('utf-8')
        self.assertEqual(result.count('assets/images/sample%20image.png'), 3)
        self.assertNotIn('assets/assets/', result)

    def test_markdown_converts_to_pdf(self) -> None:
        result = kiwix_preparsed_service.convert_markdown_to_pdf(
            '# 제목\n본문입니다.\n<!-- PAGE:2 -->\n두 번째 페이지'.encode('utf-8')
        )
        self.assertTrue(result.startswith(b'%PDF-'))

    def test_missing_relative_image_fails_conversion(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'KIWIX_ASSET_NOT_FOUND:images/missing.png'):
            kiwix_preparsed_service.convert_markdown_to_pdf(
                b'![sample](images/missing.png)', {}
            )

    def test_manifest_keeps_only_effective_document_metadata(self) -> None:
        payload = kiwix_preparsed_service.build_manifest(
            content_key="documents/1/content.md",
            content_version="content-version",
            viewer={"object_key": "documents/1"},
            assets=[],
            document_metadata={
                "schema": "  LAW Directive / Ver.2  ",
                "stage": "PDR-검토 단계",
            },
        )
        manifest = json.loads(payload.decode("utf-8"))
        self.assertEqual(manifest["source_metadata"], {})
        self.assertEqual(
            manifest["document_metadata"],
            {"schema": "  LAW Directive / Ver.2  ", "stage": "PDR-검토 단계"},
        )

    async def test_ingest_does_not_run_html_or_pdf_conversion(self) -> None:
        with (
            patch.object(kiwix_preparsed_service, '_validate_target'),
            patch.object(
                kiwix_preparsed_service,
                'convert_markdown_to_html',
                side_effect=AssertionError('HTML conversion must not run'),
            ) as html_conversion,
            patch.object(
                kiwix_preparsed_service,
                'convert_html_to_pdf',
                side_effect=AssertionError('PDF conversion must not run'),
            ) as pdf_conversion,
            patch.object(
                kiwix_preparsed_service.minio_upload_service,
                'initialize_upload',
                side_effect=HTTPException(status_code=409, detail='stop after preparation'),
            ) as initialize,
        ):
            with self.assertRaises(HTTPException) as caught:
                await kiwix_preparsed_service.ingest_markdown(
                    upload_request_id='03541a3f-d4e5-4d7a-9675-13148d46fe98',
                    parent_node_id=1,
                    source_file_name='manual.md',
                    markdown_bytes=b'# broken',
                    assets={},
                    user_id=15,
                    kiwix_metadata={
                        'folderName': 'JOMO',
                        'fileName': 'manual',
                        'fileCreate': '2024-01-02',
                    },
                )

        self.assertEqual(caught.exception.status_code, 409)
        html_conversion.assert_not_called()
        pdf_conversion.assert_not_called()
        initialize.assert_called_once()
        call = initialize.call_args.kwargs
        self.assertEqual(call['file_name'], 'manual.md')
        self.assertEqual(call['content_type'], 'text/markdown')
        self.assertTrue(call['allow_kiwix_preparsed_markdown'])

    async def test_source_and_content_artifact_use_same_normalized_markdown(self) -> None:
        source_chunks = []
        artifact_payloads = {}

        async def upload_content(**kwargs):
            async for chunk in kwargs['chunks']:
                source_chunks.append(chunk)
            return {'minio_version_id': 'source-version'}

        def upload_bytes(_storage, bucket, key, payload, _content_type, _metadata):
            artifact_payloads[key] = payload
            return SimpleNamespace(
                bucket_name=bucket,
                object_key=key,
                version_id=f'version-{len(artifact_payloads)}',
                sha256='a' * 64,
                size_bytes=len(payload),
            )

        with (
            patch.object(kiwix_preparsed_service, '_validate_target'),
            patch.object(
                kiwix_preparsed_service.minio_upload_service,
                'initialize_upload',
                return_value={
                    'object_id': 42,
                    'revision_id': 7,
                    'bucket_name': 'source-bucket',
                    'object_key': 'documents/42',
                    'storage_status': 'PENDING',
                    'idempotent_replay': False,
                },
            ),
            patch.object(
                kiwix_preparsed_service.minio_upload_service,
                'upload_content',
                side_effect=upload_content,
            ),
            patch.object(kiwix_preparsed_service, 'get_minio_storage_service', return_value=object()),
            patch.object(kiwix_preparsed_service, '_upload_bytes', side_effect=upload_bytes),
            patch.object(
                kiwix_preparsed_service,
                'complete_preparsed_jobs',
                return_value={'parse_job_id': 1, 'artifact_id': 2, 'index_job_id': 3},
            ),
        ):
            await kiwix_preparsed_service.ingest_markdown(
                upload_request_id='03541a3f-d4e5-4d7a-9675-13148d46fe98',
                parent_node_id=1,
                source_file_name='manual.md',
                markdown_bytes=b'![sample](./assets/images/sample.png)',
                assets={'assets/images/sample.png': b'image'},
                user_id=15,
                kiwix_metadata={
                    'platform': 'KIWIX',
                    'prjCode': 'JOMO',
                    'docCode': '1_523',
                    'fileName': 'manual',
                    'fileCreate': '2024-01-02 03:04',
                    'schema': 'LAW DIRECTIVE',
                    'stage': 'DESIGN REVIEW',
                },
            )

        source_payload = b''.join(source_chunks)
        content_payload = next(
            payload for key, payload in artifact_payloads.items() if key.endswith('/content.md')
        )
        self.assertEqual(source_payload, content_payload)
        self.assertIn(b'assets/images/sample.png', source_payload)
        self.assertNotIn(b'assets/assets/', source_payload)
        manifest_payload = next(
            payload for key, payload in artifact_payloads.items() if key.endswith('/manifest.json')
        )
        manifest = json.loads(manifest_payload.decode('utf-8'))
        self.assertEqual(manifest['source_metadata'], {})
        self.assertEqual(
            manifest['document_metadata'],
            {'schema': 'LAW DIRECTIVE', 'stage': 'DESIGN REVIEW'},
        )

    def test_markdown_is_rendered_as_html_before_pdf(self) -> None:
        rendered = kiwix_preparsed_service.convert_markdown_to_html(
            b'# Heading\n\n- first\n- second\n\n**bold**'
        )
        self.assertIn('<h1>Heading</h1>', rendered)
        self.assertIn('<ul>', rendered)
        self.assertIn('<strong>bold</strong>', rendered)

    def test_relative_image_is_rendered_into_pdf(self) -> None:
        png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        )
        result = kiwix_preparsed_service.convert_markdown_to_pdf(
            b'# Image\n\nBefore ![sample](images/sample.png) after',
            {'images/sample.png': png},
        )
        self.assertTrue(result.startswith(b'%PDF-'))

    def test_html_embeds_relative_image_as_data_uri(self) -> None:
        png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        )
        rendered = kiwix_preparsed_service.convert_markdown_to_html(
            b'![sample](./images/sample.png)'
        )
        embedded = kiwix_preparsed_service.embed_html_assets(
            rendered, {'images/sample.png': png}
        )
        self.assertIn('src="data:image/png;base64,', embedded)
        self.assertNotIn('src="./images/sample.png"', embedded)


if __name__ == '__main__':
    unittest.main()
