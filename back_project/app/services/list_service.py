from __future__ import annotations

import csv
import io
import re

from fastapi import HTTPException
from fastapi.responses import Response

from app.repositories.common import fetch_all, fetch_one


_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_table_name(table_name: str) -> str:
    name = (table_name or "").strip()
    if not name or not _TABLE_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid table name")
    return name


def _ensure_table_exists(table_name: str) -> None:
    row = fetch_one(
        """
        SELECT 1 AS ok
          FROM information_schema.tables
         WHERE table_schema=current_schema() AND table_name=%s
         LIMIT 1
        """,
        (table_name,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Table not found")


def list_storage() -> list[dict]:
    return fetch_all("SELECT * FROM storage_objects")


def list_storage_csv() -> Response:
    rows = fetch_all("SELECT * FROM storage_objects")
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
    rows = fetch_all(f'SELECT * FROM "{name}"')
    return {"table": name, "count": len(rows), "rows": rows}
