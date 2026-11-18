from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator

import psycopg

from app.utils.db import pooled_connection


@contextmanager
def db_transaction() -> Iterator[psycopg.Connection[Dict[str, Any]]]:
    """Provide one explicit PostgreSQL transaction for multi-table state changes."""
    with pooled_connection(autocommit=False) as connection:
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
