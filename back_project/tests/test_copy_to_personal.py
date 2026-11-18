from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.common.minio_storage_service import StoredObject
from app.services.features import minio_upload_service as service


class CopyToPersonalTest(unittest.TestCase):
    @patch.object(service, '_record_and_finalize')
    @patch.object(service, 'get_minio_storage_service')
    @patch.object(service, '_begin_content_upload')
    @patch.object(service, 'initialize_upload')
    @patch.object(service, '_copy_source')
    @patch.object(service, 'require_object_permission')
    def test_copies_ready_minio_version_and_creates_parse_job(
        self,
        require_permission,
        copy_source,
        initialize_upload,
        begin_upload,
        get_storage,
        finalize,
    ) -> None:
        checksum = 'a' * 64
        copy_source.return_value = {
            'bucket_name': 'spaceai-se-kb',
            'object_key': 'documents/5307',
            'minio_version_id': 'source-version',
            'source_checksum': checksum,
            'size_bytes': 123,
            'mime_type': 'application/pdf',
            'file_right': 'pdf',
            'branch_type': 'kiwix',
        }
        initialize_upload.return_value = {
            'revision_id': 90,
            'object_id': 60,
            'node_id': 70,
            'bucket_name': 'spaceai-personal',
            'object_key': 'users/7/documents/60',
        }
        begin_upload.return_value = {'revision_id': 90, 'object_id': 60, 'uploader_user_id': 7}
        stored = StoredObject('spaceai-personal', 'users/7/documents/60', 'new-version', 'etag', 123, checksum)
        storage = MagicMock()
        storage.copy_object.return_value = stored
        get_storage.return_value = storage
        finalize.return_value = {'object_id': 60, 'revision_id': 90, 'parse_job_id': 91}

        result = service.copy_document_to_personal(
            source_object_id=5307,
            parent_node_id=44,
            file_name='manual_copy.pdf',
            user_id=7,
        )

        require_permission.assert_called_once_with(7, 5307, 'r')
        initialize_upload.assert_called_once()
        storage.copy_object.assert_called_once_with(
            'spaceai-se-kb',
            'documents/5307',
            'spaceai-personal',
            'users/7/documents/60',
            source_version_id='source-version',
            sha256=checksum,
        )
        finalize.assert_called_once_with(begin_upload.return_value, stored)
        self.assertEqual(result['node_id'], 70)
        self.assertEqual(result['parse_job_id'], 91)


if __name__ == '__main__':
    unittest.main()
