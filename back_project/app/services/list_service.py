from __future__ import annotations

import csv
import io
import re

from fastapi import HTTPException
from fastapi.responses import Response

from app.core.config import DB_CONFIG
from app.dao import list_dao


_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_table_name(table_name: str) -> str:
    name = (table_name or "").strip()
    if not name or not _TABLE_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid table name")
    return name


def _ensure_table_exists(table_name: str) -> None:
    db_name = DB_CONFIG.get("database")
    if not list_dao.table_exists(db_name=db_name, table_name=table_name):
        raise HTTPException(status_code=404, detail="Table not found")


def list_storage() -> list[dict]:
    return list_dao.fetch_storage_rows()


def list_storage_csv() -> Response:
    rows = list_dao.fetch_storage_rows()
    if not rows:
        return Response(content="\ufeff", media_type="text/csv; charset=utf-8")

    headers = list(rows[0].keys())
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8")


def list_table(table_name: str) -> dict:
    name = _validate_table_name(table_name)
    _ensure_table_exists(name)
    rows = list_dao.fetch_table_all(name)
    return {"table": name, "count": len(rows), "rows": rows}

