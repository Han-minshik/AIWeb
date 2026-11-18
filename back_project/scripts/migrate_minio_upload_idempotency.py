from __future__ import annotations

import argparse

from app.utils.db import get_connection


TABLE = "doc_revisions"
COLUMN = "upload_request_id"
UNIQUE_INDEX = "uq_doc_revisions_creator_upload_request"
STATUS_INDEX = "idx_doc_revisions_storage_status_created"


def _column_exists(cursor, name: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA=current_schema() AND TABLE_NAME=%s AND COLUMN_NAME=%s
        """,
        (TABLE, name),
    )
    return cursor.fetchone() is not None


def _index_exists(cursor, name: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM pg_indexes
         WHERE schemaname=current_schema() AND tablename=%s AND indexname=%s
        """,
        (TABLE, name),
    )
    return cursor.fetchone() is not None


def apply() -> None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            if not _column_exists(cursor, COLUMN):
                cursor.execute(
                    """
                    ALTER TABLE doc_revisions
                      ADD COLUMN upload_request_id CHAR(36) COLLATE "C"
                    """
                )
            if not _index_exists(cursor, UNIQUE_INDEX):
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX uq_doc_revisions_creator_upload_request
                      ON doc_revisions(created_by,upload_request_id)
                    """
                )
            if not _index_exists(cursor, STATUS_INDEX):
                cursor.execute(
                    """
                    CREATE INDEX idx_doc_revisions_storage_status_created
                      ON doc_revisions(storage_status,created_at)
                    """
                )
        print("MinIO upload idempotency migration: applied")
    finally:
        connection.close()


def rollback() -> None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            if _index_exists(cursor, STATUS_INDEX):
                cursor.execute(f"DROP INDEX {STATUS_INDEX}")
            if _index_exists(cursor, UNIQUE_INDEX):
                cursor.execute(f"DROP INDEX {UNIQUE_INDEX}")
            if _column_exists(cursor, COLUMN):
                cursor.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
        print("MinIO upload idempotency migration: rolled back")
    finally:
        connection.close()


def status() -> None:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            print(
                {
                    "upload_request_id": _column_exists(cursor, COLUMN),
                    UNIQUE_INDEX: _index_exists(cursor, UNIQUE_INDEX),
                    STATUS_INDEX: _index_exists(cursor, STATUS_INDEX),
                }
            )
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "rollback", "status"), default="status", nargs="?")
    args = parser.parse_args()
    globals()[args.action]()


if __name__ == "__main__":
    main()
