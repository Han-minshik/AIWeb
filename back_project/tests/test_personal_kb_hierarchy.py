from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.services.features import kb_service


class PersonalKbHierarchyTests(unittest.TestCase):
    def test_personal_kb_payload_includes_storage_quota(self):
        row = {
            'node_id': 10, 'kb_root_id': 10, 'kb_type': 'personal',
            'kb_owner_user_id': 1, 'name': 'trial', 'alias': 'trial',
        }
        user = {'user_id': 1, 'username': 'trial'}
        with (
            patch.object(
                kb_service,
                'fetch_one',
                return_value={'committed_bytes': 1024, 'reserved_bytes': 512},
            ),
            patch.object(kb_service.settings, 'MINIO_PERSONAL_QUOTA_BYTES', 4096),
        ):
            result = kb_service._personal_kb_payload(row, user)

        self.assertEqual(result['storage']['committed_bytes'], 1024)
        self.assertEqual(result['storage']['reserved_bytes'], 512)
        self.assertEqual(result['storage']['accounted_bytes'], 1536)
        self.assertEqual(result['storage']['available_bytes'], 2560)
        self.assertEqual(result['storage']['quota_bytes'], 4096)
        self.assertEqual(result['storage']['usage_percent'], 37.5)

    def test_owner_creates_private_folder(self):
        root = {
            'node_id': 10, 'parent_id': None, 'kb_root_id': 10,
            'kb_type': 'personal', 'kb_owner_user_id': 1,
            'is_folder': 1, 'item_type_code': 10,
        }
        with (
            patch.object(kb_service, 'get_kb_entry', return_value=root),
            patch.object(kb_service, 'get_node', return_value=root),
            patch.object(kb_service, '_dup_alias_exists', return_value=False),
            patch.object(kb_service, 'next_item_type_code', return_value=11),
            patch.object(kb_service, 'next_sort_order', return_value=0),
            patch.object(kb_service, 'execute', return_value=20),
            patch.object(kb_service, 'ensure_acl') as ensure_acl,
        ):
            result = kb_service.create_kb_node(
                10, kb_service.KBNodeIn(name='Private folder'), user_id=1,
            )
        self.assertEqual(result['node_id'], 20)
        ensure_acl.assert_called_once_with(20, 'PRIVATE', owner_user_id=1)

    def test_non_owner_cannot_create_folder(self):
        root = {
            'node_id': 10, 'kb_type': 'personal', 'kb_owner_user_id': 2,
        }
        with patch.object(kb_service, 'get_kb_entry', return_value=root):
            with self.assertRaises(HTTPException) as caught:
                kb_service.create_kb_node(
                    10, kb_service.KBNodeIn(name='Private folder'), user_id=1,
                )
        self.assertEqual(caught.exception.status_code, 403)

    def test_non_owner_cannot_rename_folder(self):
        row = {
            'node_id': 20, 'parent_id': 10, 'kb_root_id': 10,
            'kb_type': 'personal', 'kb_owner_user_id': 2, 'is_folder': 1,
        }
        with patch.object(kb_service, 'get_node', return_value=row):
            with self.assertRaises(HTTPException) as caught:
                kb_service.patch_kb_node(
                    20, kb_service.KBNodePatch(name='Renamed'), user_id=1,
                )
        self.assertEqual(caught.exception.status_code, 403)

    def test_folder_cannot_move_to_another_personal_kb(self):
        row = {
            'node_id': 20, 'parent_id': 10, 'kb_root_id': 10,
            'kb_type': 'personal', 'kb_owner_user_id': 1, 'is_folder': 1,
            'alias': 'Folder', 'name': 'Folder', 'item_type_code': 10,
        }
        other_parent = {
            'node_id': 99, 'parent_id': 90, 'kb_root_id': 90,
            'kb_type': 'personal', 'kb_owner_user_id': 1, 'is_folder': 1,
        }
        with patch.object(kb_service, 'get_node', side_effect=[row, other_parent]):
            with self.assertRaises(HTTPException) as caught:
                kb_service.patch_kb_node(
                    20, kb_service.KBNodePatch(parent_id=99), user_id=1,
                )
        self.assertEqual(caught.exception.status_code, 403)

    def test_folder_rename_refreshes_descendant_paths_and_jobs(self):
        row = {
            'node_id': 20, 'parent_id': 10, 'kb_root_id': 10,
            'kb_type': 'personal', 'kb_owner_user_id': 1, 'is_folder': 1,
            'alias': 'Folder', 'name': 'Folder', 'item_type_code': 10,
        }
        with (
            patch.object(kb_service, 'get_node', return_value=row),
            patch.object(kb_service, '_dup_alias_exists', return_value=False),
            patch.object(kb_service, 'execute') as execute,
            patch.object(kb_service, 'collect_descendants', return_value={20, 21}),
            patch.object(kb_service, '_refresh_storage_paths_for_nodes') as refresh,
        ):
            result = kb_service.patch_kb_node(
                20, kb_service.KBNodePatch(name='Renamed'), user_id=1,
            )
        self.assertEqual(result['message'], 'Updated')
        sql, params = execute.call_args.args[:2]
        self.assertIn('name=%s', sql)
        self.assertEqual(params[:2], ('Renamed', 'Renamed'))
        refresh.assert_called_once_with({20, 21}, enqueue_jobs=True)

    def test_personal_root_and_foreign_nodes_cannot_be_deleted(self):
        root = {
            'node_id': 10, 'kb_type': 'personal', 'kb_owner_user_id': 1,
        }
        with patch.object(kb_service, 'get_kb_entry', return_value=root):
            with self.assertRaises(HTTPException) as caught:
                kb_service.delete_kb_nodes_bulk(
                    10, kb_service.KBNodesBulkDeleteIn(node_ids=[10]), user_id=1,
                )
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
