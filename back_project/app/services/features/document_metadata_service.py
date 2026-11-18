from __future__ import annotations

import re
from typing import Any

from app.repositories.transaction import db_transaction


class DocumentMetadataResolutionError(ValueError):
    """Raised when a GENERAL document's KB placement cannot be resolved."""


def normalize_general_schema(value: Any) -> str:
    """Normalize an internally controlled GENERAL top-level folder name."""
    text = str(value or "").strip()
    text = re.sub(r"^\s*\d+\s*\.\s*", "", text, count=1)
    text = text.casefold()
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        raise DocumentMetadataResolutionError("GENERAL document schema is empty after normalization")
    if len(text) > 100:
        raise DocumentMetadataResolutionError(
            "GENERAL document schema exceeds the 100-character limit"
        )
    return text


def resolve_general_schema_for_parent(cursor: Any, parent_node_id: int) -> str:
    """Use a root default or normalize the first folder below the KB root."""
    cursor.execute(
        """
        SELECT node_id,parent_id,kb_root_id,kb_type,name,alias,is_folder
          FROM tree_nodes
         WHERE node_id=%s
        """,
        (int(parent_node_id),),
    )
    current = cursor.fetchone()
    if not current or not bool(current.get("is_folder")):
        raise DocumentMetadataResolutionError("GENERAL document parent folder was not found")

    root_id = int(current.get("kb_root_id") or 0)
    if root_id <= 0:
        raise DocumentMetadataResolutionError("GENERAL document KB root was not found")

    visited: set[int] = set()
    while current:
        node_id = int(current["node_id"])
        if node_id in visited:
            raise DocumentMetadataResolutionError("GENERAL document folder hierarchy contains a cycle")
        visited.add(node_id)
        if int(current.get("kb_root_id") or 0) != root_id:
            raise DocumentMetadataResolutionError("GENERAL document folder hierarchy crosses KB roots")
        if node_id == root_id:
            kb_type = str(current.get("kb_type") or "").strip().lower()
            return "personal" if kb_type in {"personal", "person"} else "general"
        parent_id = current.get("parent_id")
        if parent_id is not None and int(parent_id) == root_id:
            return normalize_general_schema(current.get("alias") or current.get("name"))
        if parent_id is None:
            break
        cursor.execute(
            """
            SELECT node_id,parent_id,kb_root_id,kb_type,name,alias,is_folder
              FROM tree_nodes
             WHERE node_id=%s
            """,
            (int(parent_id),),
        )
        current = cursor.fetchone()

    raise DocumentMetadataResolutionError(
        "GENERAL document is not placed below a top-level category folder"
    )


def refresh_general_document_metadata(object_id: int) -> str | None:
    """Refresh GENERAL metadata without touching KIWIX JSON-derived values."""
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT LOWER(COALESCE(so.branch_type,'general')) AS branch_type,
                       tn.parent_id
                  FROM storage_objects so
                  JOIN tree_nodes tn ON tn.doc_id=so.object_id
                 WHERE so.object_id=%s
                 ORDER BY tn.node_id
                """,
                (int(object_id),),
            )
            rows = cursor.fetchall() or []
            if not rows or str(rows[0].get("branch_type") or "") != "general":
                return None
            parent_ids = {int(row["parent_id"]) for row in rows if row.get("parent_id") is not None}
            if len(rows) != 1 or len(parent_ids) != 1:
                raise DocumentMetadataResolutionError(
                    "GENERAL document has an ambiguous tree placement"
                )
            schema = resolve_general_schema_for_parent(cursor, next(iter(parent_ids)))
            cursor.execute(
                """
                UPDATE ai_document_info
                   SET document_schema=%s,document_stage=NULL,updated_at=NOW()
                 WHERE object_id=%s
                """,
                (schema, int(object_id)),
            )
            return schema


__all__ = [
    "DocumentMetadataResolutionError",
    "normalize_general_schema",
    "refresh_general_document_metadata",
    "resolve_general_schema_for_parent",
]
