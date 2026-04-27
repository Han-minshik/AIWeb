from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.utils.db import fetch_all, fetch_one
from app.deps.auth_dep import get_current_user_id

log = logging.getLogger(__name__)


def _table_exists(table: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS ok
          FROM INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = %s
         LIMIT 1
        """,
        (table,),
    )
    return bool(row and int(row.get("ok", 0)) == 1)


def _get_table_columns(table: str) -> Set[str]:
    rows = fetch_all(
        """
        SELECT COLUMN_NAME
          FROM INFORMATION_SCHEMA.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = %s
        """,
        (table,),
    )
    return {r["COLUMN_NAME"].lower() for r in (rows or [])}


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, datetime):
        return v.isoformat()
    try:
        return str(v)
    except Exception:
        return None


def _get_user_org_admin(user_id: int) -> Dict[str, Any]:
    row = fetch_one("SELECT org_id, is_admin FROM users WHERE user_id=%s", (user_id,))
    if not row:
        raise HTTPException(status_code=401, detail="인증 사용자 없음")
    return {"org_id": int(row["org_id"]), "is_admin": int(row["is_admin"] or 0)}


def _has_any_read_perm(tree_id: int, user_id: int) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS ok
          FROM tree_nodes t
         WHERE t.tree_id=%s
           AND fn_acl_can('TREE_NODE', t.node_id, %s, 'r') = 1
         LIMIT 1
        """,
        (tree_id, user_id),
    )
    return bool(row and int(row.get("ok", 0)) == 1)


def _ensure_kb_tree(kb_id: int, user_id: int) -> Dict[str, Any]:
    u = _get_user_org_admin(user_id)
    tree = fetch_one(
        "SELECT tree_id AS kb_id, org_id, name FROM trees WHERE tree_id=%s AND kind='KB'",
        (kb_id,),
    )
    if not tree:
        raise HTTPException(status_code=404, detail="KB 트리 없음")

    if int(tree["org_id"]) != u["org_id"]:
        raise HTTPException(status_code=403, detail="다른 조직의 KB")

    if u["is_admin"]:
        return {"kb_id": int(tree["kb_id"]), "name": tree["name"], "org_id": int(tree["org_id"]), "is_admin": True}

    if not _has_any_read_perm(kb_id, user_id):
        raise HTTPException(status_code=403, detail="읽기 권한 없음")

    return {"kb_id": int(tree["kb_id"]), "name": tree["name"], "org_id": int(tree["org_id"]), "is_admin": False}


def _detect_so_size_expr() -> str:
    if not _table_exists("storage_objects"):
        return "0"
    have = _get_table_columns("storage_objects")
    for c in ["size_bytes", "bytes", "size", "file_size", "length"]:
        if c in have:
            return f"so.{c}"
    return "0"


def _detect_so_updated_col() -> Optional[str]:
    if not _table_exists("storage_objects"):
        return None
    have = _get_table_columns("storage_objects")
    for c in ["updated_at", "modified_at", "mtime", "last_modified_at"]:
        if c in have:
            return c
    return None


def _detect_so_created_col() -> Optional[str]:
    if not _table_exists("storage_objects"):
        return None
    have = _get_table_columns("storage_objects")
    for c in ["created_at", "ctime", "uploaded_at", "inserted_at"]:
        if c in have:
            return c
    return None


class SummaryOut(BaseModel):
    kb_id: int
    total_docs: int = 0
    total_bytes: int = 0
    last_content_update: Optional[str] = None
    last_kb_sync: Optional[str] = None
    active_categories: Optional[int] = None
    health: str = "UNKNOWN"


class GrowthPoint(BaseModel):
    month: str
    docs_created: int = 0
    bytes_created: int = 0


def kb_dashboard_summary(
    kb_id: int = Path(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    _ensure_kb_tree(kb_id, user_id)

    total_docs_row = fetch_one(
        """
        SELECT COUNT(DISTINCT pfn.doc_master_id) AS cnt
          FROM tree_nodes n
          JOIN pf_node_props pfn ON pfn.node_id = n.node_id
         WHERE n.tree_id=%s
           AND COALESCE(n.is_folder, 0) = 0
           AND pfn.doc_master_id IS NOT NULL
        """,
        (kb_id,),
    )
    total_docs = int((total_docs_row or {}).get("cnt", 0) or 0)

    total_bytes = 0
    last_content_update = None

    if _table_exists("pf_node_props") and _table_exists("storage_objects"):
        size_expr = _detect_so_size_expr()
        so_updated_col = _detect_so_updated_col()

        bytes_row = fetch_one(
            f"""
            SELECT COALESCE(SUM(x.sz), 0) AS total_bytes
              FROM (
                    SELECT DISTINCT dr.storage_object_id, COALESCE({size_expr}, 0) AS sz
                      FROM tree_nodes n
                      JOIN pf_node_props pfn ON pfn.node_id = n.node_id
                      JOIN doc_revisions dr
                        ON dr.revision_id = (
                          SELECT r.revision_id
                            FROM doc_revisions r
                           WHERE r.doc_master_id=pfn.doc_master_id AND r.deleted_at IS NULL
                           ORDER BY r.rev_no DESC
                           LIMIT 1
                        )
                      JOIN storage_objects so ON so.object_id = dr.storage_object_id
                     WHERE n.tree_id=%s
                       AND COALESCE(n.is_folder, 0) = 0
                       AND pfn.doc_master_id IS NOT NULL
                   ) x
            """,
            (kb_id,),
        )
        total_bytes = int((bytes_row or {}).get("total_bytes", 0) or 0)

        if so_updated_col:
            upd_row = fetch_one(
                f"""
                SELECT MAX(so.{so_updated_col}) AS last_content_update
                  FROM tree_nodes n
                  JOIN pf_node_props pfn ON pfn.node_id = n.node_id
                  JOIN doc_revisions dr
                    ON dr.revision_id = (
                      SELECT r.revision_id
                        FROM doc_revisions r
                       WHERE r.doc_master_id=pfn.doc_master_id AND r.deleted_at IS NULL
                       ORDER BY r.rev_no DESC
                       LIMIT 1
                    )
                  JOIN storage_objects so ON so.object_id = dr.storage_object_id
                 WHERE n.tree_id=%s
                   AND COALESCE(n.is_folder, 0) = 0
                """,
                (kb_id,),
            )
            last_content_update = _iso((upd_row or {}).get("last_content_update"))

    if not last_content_update:
        upd_row2 = fetch_one(
            "SELECT MAX(updated_at) AS last_content_update FROM tree_nodes WHERE tree_id=%s",
            (kb_id,),
        )
        last_content_update = _iso((upd_row2 or {}).get("last_content_update"))

    last_kb_sync = None
    health = "UNKNOWN"
    if _table_exists("kb_sync_jobs"):
        have = _get_table_columns("kb_sync_jobs")
        has_finished = "finished_at" in have
        has_started = "started_at" in have
        has_status = "status" in have

        time_col = "finished_at" if has_finished else ("started_at" if has_started else None)

        if time_col and has_status:
            row = fetch_one(
                f"""
                SELECT {time_col} AS t, status
                  FROM kb_sync_jobs
                 WHERE kb_id=%s
                 ORDER BY COALESCE({time_col}, NOW()) DESC
                 LIMIT 1
                """,
                (kb_id,),
            )
            last_kb_sync = _iso((row or {}).get("t"))
            st = str((row or {}).get("status") or "").upper()

            if st in {"SUCCESS", "SUCCEEDED", "OK", "DONE"}:
                health = "OPTIMAL"
            elif st in {"RUNNING", "STARTED", "IN_PROGRESS"}:
                health = "UPDATING"
            elif st in {"FAILED", "ERROR"}:
                health = "DEGRADED"
            elif st:
                health = "UNKNOWN"
        elif time_col:
            row = fetch_one(
                f"""
                SELECT MAX({time_col}) AS t
                  FROM kb_sync_jobs
                 WHERE kb_id=%s
                """,
                (kb_id,),
            )
            last_kb_sync = _iso((row or {}).get("t"))

    active_categories = None
    if _table_exists("doc_category_map"):
        have = _get_table_columns("doc_category_map")
        if {"kb_id", "category_code"} <= have:
            row = fetch_one(
                "SELECT COUNT(DISTINCT category_code) AS cnt FROM doc_category_map WHERE kb_id=%s",
                (kb_id,),
            )
            active_categories = int((row or {}).get("cnt", 0) or 0)
        elif {"object_id", "category_code"} <= have and _table_exists("pf_node_props"):
            row = fetch_one(
                """
                SELECT COUNT(DISTINCT m.category_code) AS cnt
                  FROM tree_nodes n
                  JOIN pf_node_props pfn ON pfn.node_id = n.node_id
                  JOIN doc_revisions dr
                    ON dr.revision_id = (
                      SELECT r.revision_id
                        FROM doc_revisions r
                       WHERE r.doc_master_id=pfn.doc_master_id AND r.deleted_at IS NULL
                       ORDER BY r.rev_no DESC
                       LIMIT 1
                    )
                  JOIN doc_category_map m ON m.object_id = dr.storage_object_id
                 WHERE n.tree_id=%s
                   AND COALESCE(n.is_folder, 0) = 0
                """,
                (kb_id,),
            )
            active_categories = int((row or {}).get("cnt", 0) or 0)

    return SummaryOut(
        kb_id=kb_id,
        total_docs=total_docs,
        total_bytes=total_bytes,
        last_content_update=last_content_update,
        last_kb_sync=last_kb_sync,
        active_categories=active_categories,
        health=health,
    )


def kb_dashboard_growth(
    kb_id: int = Path(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    _ensure_kb_tree(kb_id, user_id)

    rows = fetch_all(
        """
        SELECT
          DATE_FORMAT(n.created_at, '%%Y-%%m-01') AS month,
          COUNT(DISTINCT pfn.doc_master_id) AS docs_created
        FROM tree_nodes n
        JOIN pf_node_props pfn ON pfn.node_id = n.node_id
        WHERE n.tree_id=%s
          AND COALESCE(n.is_folder, 0) = 0
          AND n.created_at IS NOT NULL
        GROUP BY month
        ORDER BY month
        """,
        (kb_id,),
    )

    month_to_docs: Dict[str, int] = {str(r["month"]): int(r.get("docs_created") or 0) for r in (rows or [])}

    month_to_bytes: Dict[str, int] = {}

    if _table_exists("pf_node_props") and _table_exists("storage_objects"):
        so_created = _detect_so_created_col()
        size_expr = _detect_so_size_expr()

        if so_created:
            b_rows = fetch_all(
                f"""
                SELECT
                  DATE_FORMAT(so.{so_created}, '%%Y-%%m-01') AS month,
                  COALESCE(SUM(z.sz), 0) AS bytes_created
                FROM (
                      SELECT DISTINCT
                             dr.storage_object_id AS object_id,
                             COALESCE({size_expr}, 0) AS sz,
                             so.{so_created} AS created_at,
                             n.tree_id
                        FROM tree_nodes n
                        JOIN pf_node_props pfn ON pfn.node_id = n.node_id
                        JOIN doc_revisions dr
                          ON dr.revision_id = (
                            SELECT r.revision_id
                              FROM doc_revisions r
                             WHERE r.doc_master_id=pfn.doc_master_id AND r.deleted_at IS NULL
                             ORDER BY r.rev_no DESC
                             LIMIT 1
                          )
                        JOIN storage_objects so ON so.object_id = dr.storage_object_id
                       WHERE n.tree_id=%s
                         AND COALESCE(n.is_folder, 0) = 0
                         AND pfn.doc_master_id IS NOT NULL
                         AND so.{so_created} IS NOT NULL
                     ) z
                JOIN storage_objects so ON so.object_id = z.object_id
                WHERE z.tree_id=%s
                GROUP BY month
                ORDER BY month
                """,
                (kb_id, kb_id),
            )
            month_to_bytes = {str(r["month"]): int(r.get("bytes_created") or 0) for r in (b_rows or [])}
        else:
            month_to_bytes = {}

    months = sorted(set(month_to_docs.keys()) | set(month_to_bytes.keys()))
    out: List[GrowthPoint] = []
    for m in months:
        out.append(
            GrowthPoint(
                month=m,
                docs_created=int(month_to_docs.get(m, 0)),
                bytes_created=int(month_to_bytes.get(m, 0)),
            )
        )
    return out
