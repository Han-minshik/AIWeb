from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.dao import admin_dao, notifications_dao
from app.services import admin_service
from app.services.features import chat_service, kb_service
from app.utils.db import fetch_all, fetch_one, get_connection


EXPECTED_TABLE_COUNT = 25


def verify_transactional_insert() -> None:
    connection = get_connection(autocommit=False)
    try:
        with connection.cursor() as cursor:
            name = f"__postgresql_smoke_{uuid4().hex}"
            cursor.execute(
                """
                INSERT INTO positions(name)
                VALUES(%s)
                ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name
                RETURNING position_id
                """,
                (name,),
            )
            position_id = int(cursor.fetchone()["position_id"])
            cursor.execute(
                "SELECT name FROM positions WHERE position_id=%s",
                (position_id,),
            )
            if (cursor.fetchone() or {}).get("name") != name:
                raise RuntimeError("Transactional INSERT/RETURNING smoke test failed")
    finally:
        connection.rollback()
        connection.close()


def main() -> None:
    identity = fetch_one(
        "SELECT current_database() AS database_name, current_user AS user_name"
    )
    table_count = fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.tables
        WHERE table_schema=current_schema()
          AND table_type='BASE TABLE'
        """
    )
    counts = {
        table: int(
            (fetch_one(f"SELECT COUNT(*) AS count FROM {table}") or {}).get("count")
            or 0
        )
        for table in ("users", "tree_nodes", "storage_objects", "chat_sessions")
    }

    actual_table_count = int((table_count or {}).get("count") or 0)
    if actual_table_count != EXPECTED_TABLE_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_TABLE_COUNT} tables, found {actual_table_count}"
        )

    admin_dao.list_users(q=None, page=1, page_size=5)
    admin_dao.list_departments()
    admin_dao.list_positions()
    admin_service.list_groups()

    users = fetch_all("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    if users:
        user_id = int(users[0]["user_id"])
        notifications_dao.count_notifications(user_id, unread_only=False)
        notifications_dao.unread_count(user_id)
        kb_service.list_kb_trees(user_id=user_id)
        chat_service.list_sessions(user_id=user_id)

    verify_transactional_insert()

    print(
        {
            "connection": identity,
            "table_count": actual_table_count,
            "row_counts": counts,
            "status": "ok",
        }
    )


if __name__ == "__main__":
    main()
