from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.services import admin_service


class AdminUserDeletionTests(unittest.TestCase):
    def test_delete_deactivates_user_without_removing_owned_data(self):
        target = {'id': 6, 'is_active': 1, 'is_admin': 0}
        with (
            patch.object(admin_service.admin_dao, 'get_user_detail', return_value=target),
            patch.object(admin_service.admin_dao, 'delete_user') as deactivate,
        ):
            result = admin_service.delete_user(6, actor_user_id=1)

        deactivate.assert_called_once_with(6)
        self.assertEqual(result['message'], 'User deactivated')
        self.assertFalse(result['is_active'])

    def test_actor_cannot_deactivate_self(self):
        target = {'id': 1, 'is_active': 1, 'is_admin': 1}
        with patch.object(admin_service.admin_dao, 'get_user_detail', return_value=target):
            with self.assertRaises(HTTPException) as caught:
                admin_service.delete_user(1, actor_user_id=1)
        self.assertEqual(caught.exception.status_code, 409)

    def test_last_active_admin_is_protected(self):
        target = {'id': 2, 'is_active': 1, 'is_admin': 1}
        with (
            patch.object(admin_service.admin_dao, 'get_user_detail', return_value=target),
            patch.object(admin_service, 'fetch_one', return_value={'cnt': 1}),
            patch.object(admin_service.admin_dao, 'delete_user') as deactivate,
        ):
            with self.assertRaises(HTTPException) as caught:
                admin_service.delete_user(2, actor_user_id=1)
        self.assertEqual(caught.exception.status_code, 409)
        deactivate.assert_not_called()

    def test_permanent_delete_removes_inactive_user_without_personal_documents(self):
        target = {'id': 6, 'is_active': 0, 'is_admin': 0}
        with (
            patch.object(admin_service.admin_dao, 'get_user_detail', return_value=target),
            patch.object(admin_service, 'fetch_all', return_value=[]),
            patch.object(admin_service, 'fetch_one', return_value={'cnt': 0}),
            patch.object(admin_service, 'execute') as execute,
            patch.object(admin_service.admin_dao, 'hard_delete_user') as hard_delete,
        ):
            result = admin_service.permanently_delete_user(6, actor_user_id=1)

        self.assertTrue(result['deleted'])
        hard_delete.assert_called_once_with(6)
        self.assertEqual(execute.call_count, 4)
        executed_sql = [' '.join(call.args[0].split()) for call in execute.call_args_list]
        self.assertFalse(any('SET kb_root_id=NULL' in sql for sql in executed_sql))
        self.assertIn('node_id<>kb_root_id', executed_sql[2])
        self.assertIn('node_id=kb_root_id', executed_sql[3])

    def test_permanent_delete_waits_for_personal_document_purge(self):
        target = {'id': 6, 'is_active': 0, 'is_admin': 0}
        objects = [{
            'object_id': 99,
            'storage_backend': 'minio',
            'doc_status': 'deleted_pending',
            'node_id': 77,
        }]
        with (
            patch.object(admin_service.admin_dao, 'get_user_detail', return_value=target),
            patch.object(admin_service, 'fetch_all', return_value=objects),
            patch.object(
                admin_service,
                'fetch_one',
                side_effect=[{'deletion_id': 50}, {'cnt': 1}],
            ),
            patch.object(
                admin_service.minio_document_lifecycle_service,
                'request_minio_purge',
                return_value={'purged': False, 'status': 'purging'},
            ) as request_purge,
            patch.object(admin_service.admin_dao, 'hard_delete_user') as hard_delete,
        ):
            result = admin_service.permanently_delete_user(6, actor_user_id=1)

        self.assertFalse(result['deleted'])
        self.assertEqual(result['remaining_documents'], 1)
        request_purge.assert_called_once_with(50, actor_user_id=1)
        hard_delete.assert_not_called()


if __name__ == '__main__':
    unittest.main()
