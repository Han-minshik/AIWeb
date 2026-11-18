from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from app.services.features import purged_document_cleanup_service as service


class _Cursor:
    def __init__(self, archived=True) -> None:
        self.archived = archived
        self.executions = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.executions.append((sql, params))
        if "DELETE FROM ai_index_jobs" in sql:
            self.rowcount = 3
        elif "DELETE FROM ai_document_info" in sql:
            self.rowcount = 1
        else:
            self.rowcount = 0

    def fetchone(self):
        if self.archived:
            self.archived = False
            return {"ai_document_id": "aid_test"}
        return None


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class PurgedDocumentCleanupServiceTests(unittest.TestCase):
    @patch.object(service, "db_transaction")
    def test_cleanup_keeps_callback_audit_but_removes_visible_history(self, transaction):
        cursor = _Cursor()
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service.cleanup_purged_document_history("aid_test")

        self.assertTrue(result["cleaned"])
        self.assertEqual(result["jobs_deleted"], 3)
        self.assertEqual(result["metadata_deleted"], 1)
        statements = [sql for sql, _ in cursor.executions]
        callback_index = next(i for i, sql in enumerate(statements) if "UPDATE ai_callback_events" in sql)
        jobs_index = next(i for i, sql in enumerate(statements) if "DELETE FROM ai_index_jobs" in sql)
        metadata_index = next(i for i, sql in enumerate(statements) if "DELETE FROM ai_document_info" in sql)
        self.assertLess(callback_index, jobs_index)
        self.assertLess(jobs_index, metadata_index)

    @patch.object(service, "db_transaction")
    def test_cleanup_refuses_document_without_purge_archive(self, transaction):
        cursor = _Cursor(archived=False)
        transaction.return_value = nullcontext(_Connection(cursor))

        result = service.cleanup_purged_document_history("aid_test")

        self.assertFalse(result["cleaned"])
        self.assertFalse(any("DELETE FROM ai_index_jobs" in sql for sql, _ in cursor.executions))


if __name__ == "__main__":
    unittest.main()
