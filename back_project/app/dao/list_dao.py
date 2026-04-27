from __future__ import annotations

from app.repositories.common import fetch_all, fetch_one
from app.xml.query_loader import get_sql


_MAPPER = "list"


def fetch_storage_rows() -> list[dict]:
    return fetch_all(get_sql(_MAPPER, "storage_all"))


def table_exists(db_name: str, table_name: str) -> bool:
    row = fetch_one(get_sql(_MAPPER, "table_exists"), (db_name, table_name))
    return bool(row)


def fetch_table_all(table_name: str) -> list[dict]:
    # table_name should be validated before this call
    return fetch_all(f"SELECT * FROM `{table_name}`")

