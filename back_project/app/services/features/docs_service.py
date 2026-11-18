from __future__ import annotations

import json
import mimetypes
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import Body, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.deps.auth_dep import get_current_user_id
from app.repositories.common import execute, fetch_all, fetch_one
from app.repositories.transaction import db_transaction
from app.services.common.minio_storage_service import (
    MinioObjectNotFoundError,
    get_minio_storage_service,
)
from app.services.common.minio_key_builder import KbStorageScope, artifact_prefix
from app.services.features.document_db import (
    as_int,
    base_dir_for_document_parent,
    collect_descendants,
    ensure_acl,
    ensure_ai_document_info,
    enqueue_ai_job,
    enqueue_ai_metadata_job,
    file_meta,
    get_kb_entry,
    get_node,
    next_sort_order,
    object_permission_exists_clause,
    require_node_edit_access,
    require_object_permission,
    resolve_storage_path,
    split_filename,
    storage_rel_sub_for_parent,
    stored_filename,
    update_subtree_item_type,
)
from app.services.features import minio_upload_service


class RevisionsReorderIn(BaseModel):
    revision_ids: List[int] = Field(default_factory=list)


class DocDeleteIn(BaseModel):
    mode: str = "all"


def _attachment_content_disposition(filename: str) -> str:
    original = str(filename or 'file').replace('\r', '').replace('\n', '')
    fallback = ''.join(
        char if 32 <= ord(char) < 127 and char not in {'"', '\\'} else '_'
        for char in original
    ) or 'file'
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(original, safe="")}'


class NodesReorderIn(BaseModel):
    parent: Optional[int] = None
    orders: List[Dict[str, int]] = Field(..., description="[{node_id, sort_order}, ...]")


class AiDocumentIdsIn(BaseModel):
    object_ids: List[int] = Field(default_factory=list)


class DocCopyToPersonalIn(BaseModel):
    parent_node_id: int = Field(..., ge=1)
    new_name: str = Field(..., min_length=1, max_length=275)


def _storage_row(object_id: int) -> Dict[str, Any]:
    row = fetch_one("SELECT * FROM storage_objects WHERE object_id=%s", (int(object_id),))
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return row


def _doc_node(object_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM tree_nodes WHERE doc_id=%s ORDER BY node_id LIMIT 1", (int(object_id),))


def _file_response_path(object_id: int) -> tuple[Dict[str, Any], str]:
    row = _storage_row(object_id)
    path = resolve_storage_path(row)
    if not os.path.exists(path):
        execute("UPDATE storage_objects SET doc_status='inactive_path_missing', db_updated_at=NOW() WHERE object_id=%s", (int(object_id),))
        raise HTTPException(status_code=404, detail="Stored file not found")
    return row, path


def _read_range(path: str, range_header: str | None, chunk_size: int = 1024 * 512):
    file_size = os.path.getsize(path)

    def full_iter():
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data

    if not range_header:
        return full_iter(), 0, file_size - 1, file_size, False

    try:
        _, rng = range_header.split("=", 1)
        start_s, end_s = (rng.split("-", 1) + [""])[:2]
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start < 0:
            raise ValueError()
    except Exception:
        return full_iter(), 0, file_size - 1, file_size, False

    def partial_iter():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return partial_iter(), start, end, file_size, True


def _parent_for_upload(tree_id: Optional[int], parent_node_id: Optional[int]) -> Optional[Dict[str, Any]]:
    parent_id = parent_node_id if parent_node_id is not None else tree_id
    if parent_id is None:
        root = fetch_one(
            """
            SELECT *
              FROM tree_nodes
             WHERE parent_id IS NULL
             ORDER BY CASE WHEN name='SE KB' THEN 0 ELSE 1 END, node_id
             LIMIT 1
            """
        )
        return root
    parent = get_node(int(parent_id))
    if not parent or not bool(parent.get("is_folder")):
        raise HTTPException(status_code=400, detail="Invalid parent folder")
    return parent


def _insert_storage_for_file(
    *,
    filename: str,
    parent: Dict[str, Any],
    user_id: int,
    branch_type: str,
    upload_mode: str,
    checksum_sha256: Optional[str],
    size_bytes: Optional[int],
    mime_type: Optional[str],
    source_created_at: Optional[datetime],
    source_updated_at: Optional[datetime],
    doc_code: str = "",
) -> int:
    doc_name, file_right = split_filename(filename)
    rel, sub = storage_rel_sub_for_parent(as_int(parent.get("node_id")))
    safe_upload_mode = "personal" if str(upload_mode or "").lower() == "personal" else "admin_kb"
    return execute(
        """
        INSERT INTO storage_objects(
            doc_code, uploader_user_id, upload_mode, doc_status, doc_name, file_right,
            relative_path, sub_path, mime_type, size_bytes, checksum_sha256,
            source_created_at, source_updated_at, source_time_status,
            uploaded_at, db_updated_at, branch_type
        )
        VALUES(%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s,%s,'actual',NOW(),NOW(),%s)
        """,
        (
            doc_code or "",
            user_id,
            safe_upload_mode,
            doc_name,
            file_right,
            rel,
            sub,
            mime_type or "application/octet-stream",
            size_bytes,
            checksum_sha256,
            source_created_at,
            source_updated_at,
            branch_type,
        ),
        True,
    )


def _is_personal_parent(parent: Dict[str, Any]) -> bool:
    return str(parent.get("kb_type") or "").lower() == "personal"


def _upload_mode_for_parent(parent: Dict[str, Any], requested: Optional[str] = None) -> str:
    if _is_personal_parent(parent):
        return "personal"
    return "personal" if str(requested or "").lower() == "personal" else "admin_kb"


def _acl_for_parent(parent: Dict[str, Any], user_id: int) -> tuple[str, int]:
    if _is_personal_parent(parent):
        return "PRIVATE", as_int(parent.get("kb_owner_user_id")) or int(user_id)
    return "PUBLIC", int(user_id)


def _create_doc_node(object_id: int, parent: Dict[str, Any], user_id: int) -> int:
    so = _storage_row(object_id)
    node_id = execute(
        """
        INSERT INTO tree_nodes(
            parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
            name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s,NOW(),NOW())
        """,
        (
            parent.get("node_id"),
            parent.get("kb_root_id"),
            parent.get("kb_type") or "admin",
            parent.get("kb_owner_user_id"),
            object_id,
            so.get("doc_name"),
            so.get("doc_name"),
            parent.get("item_type_code") or 10,
            next_sort_order(as_int(parent.get("node_id"))),
            user_id,
        ),
        True,
    )
    policy_code, owner_user_id = _acl_for_parent(parent, user_id)
    ensure_acl(node_id, policy_code, owner_user_id=owner_user_id)
    ensure_ai_document_info(object_id)
    enqueue_ai_job(object_id, "add", user_id)
    return node_id


def _insert_revision(object_id: int, user_id: Optional[int]) -> int:
    so = _storage_row(object_id)
    row = fetch_one("SELECT COALESCE(MAX(rev_no),0)+1 AS rev_no FROM doc_revisions WHERE doc_id=%s", (int(object_id),))
    rev_no = int((row or {}).get("rev_no") or 1)
    return execute(
        """
        INSERT INTO doc_revisions(
            doc_id, rev_no, checksum_sha256, source_created_at, source_updated_at,
            size_bytes, uploaded_at, created_by, created_at
        )
        VALUES(%s,%s,%s,%s,%s,%s,NOW(),%s,NOW())
        """,
        (
            int(object_id),
            rev_no,
            so.get("checksum_sha256"),
            so.get("source_created_at"),
            so.get("source_updated_at"),
            so.get("size_bytes"),
            user_id,
        ),
        True,
    )


def list_docs(
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_current_user_id),
):
    params: List[Any] = []
    conds = ["so.doc_status <> 'deleted_pending'"]
    acl_cond, acl_params = object_permission_exists_clause(user_id, "r", object_alias="so")
    conds.append(acl_cond)
    params.extend(acl_params)
    if q:
        conds.append("(so.doc_name LIKE %s OR tn.alias LIKE %s OR so.relative_path LIKE %s OR so.sub_path LIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like, like])
    params.extend([int(limit), int(offset)])
    rows = fetch_all(
        f"""
        SELECT so.*, tn.node_id, tn.alias, tn.item_type_code, tn.kb_type, tn.kb_owner_user_id
          FROM storage_objects so
          LEFT JOIN tree_nodes tn ON tn.doc_id = so.object_id
         WHERE {" AND ".join(conds)}
         ORDER BY so.db_updated_at DESC, so.object_id DESC
         LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [
        {
            "doc_id": r["object_id"],
            "object_id": r["object_id"],
            "node_id": r.get("node_id"),
            "title": r.get("alias") or r.get("doc_name"),
            "name": r.get("alias") or r.get("doc_name"),
            "doc_name": r.get("doc_name"),
            "file_right": r.get("file_right"),
            "relative_path": r.get("relative_path"),
            "sub_path": r.get("sub_path"),
            "mime_type": r.get("mime_type"),
            "branch_type": str(r.get("branch_type") or "general").upper(),
            "storage_scope": "personal"
            if str(r.get("upload_mode") or "").lower() == "personal"
            or str(r.get("kb_type") or "").lower() in {"personal", "person"}
            else "kiwix"
            if str(r.get("branch_type") or "").upper() == "KIWIX"
            else "admin",
            "upload_mode": r.get("upload_mode"),
            "kb_type": r.get("kb_type"),
            "kb_owner_user_id": r.get("kb_owner_user_id"),
            "doc_status": r.get("doc_status"),
            "created_at": r.get("uploaded_at"),
            "updated_at": r.get("db_updated_at"),
            "item_type_code": r.get("item_type_code"),
        }
        for r in rows or []
    ]


def resolve_ai_document_ids(data: AiDocumentIdsIn, user_id: int = Depends(get_current_user_id)):
    seen = set()
    object_ids: List[int] = []
    for raw in data.object_ids or []:
        object_id = as_int(raw)
        if object_id is None or object_id in seen:
            continue
        seen.add(object_id)
        object_ids.append(object_id)

    if not object_ids:
        return {"items": [], "missing": []}

    placeholders = ",".join(["%s"] * len(object_ids))
    acl_cond, acl_params = object_permission_exists_clause(user_id, "x", object_alias="so")
    rows = fetch_all(
        f"""
        SELECT
            so.object_id,
            ai.ai_document_id
          FROM storage_objects so
          JOIN ai_document_info ai ON ai.object_id = so.object_id
         WHERE so.object_id IN ({placeholders})
           AND so.doc_status = 'active'
           AND ai.ai_document_id IS NOT NULL
           AND ai.ai_document_id <> ''
           AND {acl_cond}
        """,
        tuple(object_ids + list(acl_params)),
    )
    by_object_id: Dict[int, str] = {}
    for row in rows or []:
        object_id = as_int(row.get("object_id"))
        ai_document_id = str(row.get("ai_document_id") or "").strip()
        if object_id is not None and ai_document_id:
            by_object_id[object_id] = ai_document_id

    return {
        "items": [
            {"object_id": object_id, "ai_document_id": by_object_id[object_id]}
            for object_id in object_ids
            if object_id in by_object_id
        ],
        "missing": [object_id for object_id in object_ids if object_id not in by_object_id],
    }


def get_doc(doc_id: int, user_id: int = Depends(get_current_user_id)):
    row = _storage_row(doc_id)
    require_object_permission(user_id, doc_id, "r")
    node = _doc_node(doc_id) or {}
    return {
        "doc_id": int(row["object_id"]),
        "object_id": int(row["object_id"]),
        "node_id": node.get("node_id"),
        "title": node.get("alias") or row.get("doc_name"),
        "name": node.get("alias") or row.get("doc_name"),
        "doc_name": row.get("doc_name"),
        "file_right": row.get("file_right"),
        "relative_path": row.get("relative_path"),
        "sub_path": row.get("sub_path"),
        "mime_type": row.get("mime_type"),
        "size_bytes": row.get("size_bytes"),
        "checksum_sha256": row.get("checksum_sha256"),
        "branch_type": str(row.get("branch_type") or "general").upper(),
        "doc_status": row.get("doc_status"),
        "created_at": row.get("uploaded_at"),
        "updated_at": row.get("db_updated_at"),
    }


def list_doc_revisions(doc_id: int, user_id: int = Depends(get_current_user_id)):
    _storage_row(doc_id)
    require_object_permission(user_id, doc_id, "r")
    return fetch_all(
        """
        SELECT dr.revision_id, dr.doc_id, dr.rev_no, dr.minio_version_id,
               dr.checksum_sha256, dr.source_created_at, dr.source_updated_at,
               dr.size_bytes, dr.uploaded_at, dr.created_by, dr.created_at,
               dr.storage_status,
               (dr.rev_no = (
                    SELECT MAX(latest.rev_no) FROM doc_revisions latest
                     WHERE latest.doc_id=dr.doc_id
                )) AS is_current,
               (ai.source_revision_id=dr.revision_id) AS is_source_revision,
               (ai.indexed_revision_id=dr.revision_id) AS is_indexed_revision
          FROM doc_revisions dr
          LEFT JOIN ai_document_info ai ON ai.object_id=dr.doc_id
         WHERE dr.doc_id=%s
         ORDER BY dr.rev_no DESC, dr.revision_id DESC
        """,
        (int(doc_id),),
    )


def delete_doc_revision(
    doc_id: int,
    revision_id: int,
    user_id: int = Depends(get_current_user_id),
):
    storage_row = _storage_row(doc_id)
    node = _doc_node(doc_id)
    require_node_edit_access(user_id, node)
    if str(storage_row.get("storage_backend") or "").lower() != "minio":
        raise HTTPException(status_code=409, detail="Only MinIO document versions can be deleted")

    storage = get_minio_storage_service()
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dr.*,so.bucket_name,so.object_key,so.upload_mode,so.uploader_user_id,
                       ai.source_revision_id,ai.indexed_revision_id
                  FROM doc_revisions dr
                  JOIN storage_objects so ON so.object_id=dr.doc_id
                  LEFT JOIN ai_document_info ai ON ai.object_id=dr.doc_id
                 WHERE dr.doc_id=%s AND dr.revision_id=%s
                   FOR UPDATE
                """,
                (int(doc_id), int(revision_id)),
            )
            revision = cursor.fetchone()
            revision = dict(revision) if revision else None
            if not revision:
                raise HTTPException(status_code=404, detail="Document revision not found")
            if str(revision.get("storage_status") or "").upper() != "READY":
                raise HTTPException(status_code=409, detail="Only READY revisions can be deleted")

            cursor.execute(
                """
                SELECT revision_id,rev_no
                  FROM doc_revisions
                 WHERE doc_id=%s
                 ORDER BY rev_no DESC,revision_id DESC
                   FOR UPDATE
                """,
                (int(doc_id),),
            )
            locked_revisions = [dict(row) for row in cursor.fetchall() or []]
            if len(locked_revisions) <= 1:
                raise HTTPException(status_code=409, detail="The last document revision cannot be deleted")
            cursor.execute(
                """
                SELECT revision_id,minio_version_id,checksum_sha256,size_bytes,
                       source_created_at,source_updated_at
                  FROM doc_revisions
                 WHERE doc_id=%s AND revision_id<>%s
                   AND storage_status='READY' AND minio_version_id IS NOT NULL
                 ORDER BY rev_no DESC,revision_id DESC
                 LIMIT 1
                """,
                (int(doc_id), int(revision_id)),
            )
            promoted = dict(cursor.fetchone() or {})
            if not promoted:
                raise HTTPException(
                    status_code=409,
                    detail="At least one READY document revision must remain",
                )

            scope = (
                KbStorageScope.PERSONAL
                if str(revision.get("upload_mode") or "").lower() == "personal"
                else KbStorageScope.SE_KB
            )
            artifact = artifact_prefix(
                scope,
                int(doc_id),
                int(revision_id),
                user_id=revision.get("uploader_user_id")
                if scope is KbStorageScope.PERSONAL
                else None,
            )
            try:
                deleted_artifact_versions = storage.permanent_delete_prefix(
                    artifact.bucket_name,
                    artifact.object_key,
                )
                storage.remove_version(
                    str(revision["bucket_name"]),
                    str(revision["object_key"]),
                    str(revision["minio_version_id"]),
                )
            except Exception as exc:
                raise HTTPException(status_code=502, detail="MinIO revision deletion failed") from exc

            cursor.execute(
                "DELETE FROM ai_parse_artifacts WHERE revision_id=%s",
                (int(revision_id),),
            )
            cursor.execute(
                """
                UPDATE ai_index_jobs
                   SET status=CASE
                         WHEN status IN ('pending','running','sent') THEN 'superseded'
                         ELSE 'cancelled'
                       END,
                       finished_at=COALESCE(finished_at,NOW()),
                       last_error='Source revision deleted',updated_at=NOW()
                 WHERE revision_id=%s
                   AND status IN ('pending','running','sent','failed')
                """,
                (int(revision_id),),
            )
            cursor.execute(
                "DELETE FROM doc_revisions WHERE doc_id=%s AND revision_id=%s",
                (int(doc_id), int(revision_id)),
            )
            cursor.execute(
                """
                SELECT revision_id
                  FROM doc_revisions
                 WHERE doc_id=%s
                 ORDER BY rev_no ASC,revision_id ASC
                """,
                (int(doc_id),),
            )
            remaining_ids = [int(row["revision_id"]) for row in cursor.fetchall() or []]
            cursor.execute(
                "UPDATE doc_revisions SET rev_no=-rev_no WHERE doc_id=%s",
                (int(doc_id),),
            )
            cursor.executemany(
                "UPDATE doc_revisions SET rev_no=%s WHERE doc_id=%s AND revision_id=%s",
                [
                    (index, int(doc_id), remaining_id)
                    for index, remaining_id in enumerate(remaining_ids, start=1)
                ],
            )
            promoted_revision_id = int(promoted["revision_id"])
            indexed_revision_id = revision.get("indexed_revision_id")
            if int(indexed_revision_id or 0) == int(revision_id):
                indexed_revision_id = None
            index_is_current = int(indexed_revision_id or 0) == promoted_revision_id
            cursor.execute(
                """
                UPDATE ai_document_info
                   SET source_revision_id=%s,source_minio_version_id=%s,
                       indexed_revision_id=%s,index_status=%s,updated_at=NOW()
                 WHERE object_id=%s
                """,
                (
                    promoted_revision_id,
                    promoted.get("minio_version_id"),
                    indexed_revision_id,
                    "indexed" if index_is_current else "pending",
                    int(doc_id),
                ),
            )
            cursor.execute(
                """
                UPDATE storage_objects
                   SET checksum_sha256=%s,size_bytes=%s,source_created_at=%s,
                       source_updated_at=%s,db_updated_at=NOW()
                 WHERE object_id=%s
                """,
                (
                    promoted.get("checksum_sha256"),
                    promoted.get("size_bytes"),
                    promoted.get("source_created_at"),
                    promoted.get("source_updated_at"),
                    int(doc_id),
                ),
            )
            if scope is KbStorageScope.PERSONAL:
                cursor.execute(
                    """
                    UPDATE personal_kb_storage_usage
                       SET committed_bytes=GREATEST(committed_bytes-%s,0)
                     WHERE user_id=%s
                    """,
                    (
                        int(revision.get("size_bytes") or 0),
                        int(revision["uploader_user_id"]),
                    ),
                )

    if not index_is_current:
        enqueue_ai_job(int(doc_id), "update", int(user_id))

    return {
        "ok": True,
        "doc_id": int(doc_id),
        "revision_id": int(revision_id),
        "current_revision_id": promoted_revision_id,
        "remaining_count": len(remaining_ids),
        "deleted_artifact_versions": int(deleted_artifact_versions),
    }


def reorder_doc_revisions(
    doc_id: int,
    body: RevisionsReorderIn,
    user_id: int = Depends(get_current_user_id),
):
    _storage_row(doc_id)
    node = _doc_node(doc_id)
    require_node_edit_access(user_id, node)
    requested_ids = [int(revision_id) for revision_id in body.revision_ids or []]
    if len(requested_ids) != len(set(requested_ids)):
        raise HTTPException(status_code=409, detail="Duplicate revision_id in reorder request")

    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT revision_id
                  FROM doc_revisions
                 WHERE doc_id=%s
                 ORDER BY rev_no DESC, revision_id DESC
                   FOR UPDATE
                """,
                (int(doc_id),),
            )
            existing_ids = [int(row["revision_id"]) for row in cursor.fetchall() or []]
            if len(requested_ids) != len(existing_ids) or set(requested_ids) != set(existing_ids):
                raise HTTPException(
                    status_code=409,
                    detail="Revision list changed; refresh and try again",
                )
            if not requested_ids:
                return {"ok": True, "count": 0}

            # rev_no is unique per document. Move every row into a collision-free
            # temporary range before assigning the final top-to-bottom order.
            cursor.execute(
                "UPDATE doc_revisions SET rev_no=-rev_no WHERE doc_id=%s",
                (int(doc_id),),
            )
            total = len(requested_ids)
            cursor.executemany(
                "UPDATE doc_revisions SET rev_no=%s WHERE doc_id=%s AND revision_id=%s",
                [
                    (total - index, int(doc_id), revision_id)
                    for index, revision_id in enumerate(requested_ids)
                ],
            )
    return {"ok": True, "count": len(requested_ids)}


def upload_doc(
    file: UploadFile = File(...),
    tree_id: Optional[int] = None,
    parent_node_id: Optional[int] = None,
    status: Optional[str] = None,
    doc_type: Optional[str] = None,
    branch_type: Optional[str] = "general",
    user_id: int = Depends(get_current_user_id),
):
    parent = _parent_for_upload(tree_id, parent_node_id)
    if not parent:
        raise HTTPException(status_code=400, detail="Parent folder not found")
    require_node_edit_access(user_id, parent)
    branch = "kiwix" if str(branch_type or "").lower() == "kiwix" else "general"
    doc_name, file_right = split_filename(file.filename or "file")
    tmp_path = None
    try:
        base = base_dir_for_document_parent(parent, branch)
        rel, sub = storage_rel_sub_for_parent(as_int(parent.get("node_id")))
        dst_dir = os.path.join(base, rel.replace("/", os.sep), sub.replace("/", os.sep))
        os.makedirs(dst_dir, exist_ok=True)
        dst_path = os.path.join(dst_dir, stored_filename(doc_name, file_right))
        with open(dst_path, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        meta = file_meta(dst_path)
        object_id = _insert_storage_for_file(
            filename=file.filename or stored_filename(doc_name, file_right),
            parent=parent,
            user_id=user_id,
            branch_type=branch,
            upload_mode=_upload_mode_for_parent(parent),
            checksum_sha256=meta["checksum_sha256"],
            size_bytes=meta["size_bytes"],
            mime_type=meta["mime_type"],
            source_created_at=meta["source_created_at"],
            source_updated_at=meta["source_updated_at"],
        )
        _insert_revision(object_id, user_id)
        node_id = _create_doc_node(object_id, parent, user_id)
        return {"doc_id": object_id, "object_id": object_id, "node_id": node_id, "title": doc_name}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def upload_doc_version(
    doc_id: int,
    file: UploadFile = File(...),
    status: Optional[str] = None,
    doc_type: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    row = _storage_row(doc_id)
    require_node_edit_access(user_id, _doc_node(doc_id))
    path = resolve_storage_path(row)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    meta = file_meta(path)
    execute(
        """
        UPDATE storage_objects
           SET mime_type=%s,
               size_bytes=%s,
               checksum_sha256=%s,
               source_created_at=%s,
               source_updated_at=%s,
               source_time_status='actual',
               doc_status='active',
               db_updated_at=NOW()
         WHERE object_id=%s
        """,
        (
            meta["mime_type"],
            meta["size_bytes"],
            meta["checksum_sha256"],
            meta["source_created_at"],
            meta["source_updated_at"],
            int(doc_id),
        ),
    )
    revision_id = _insert_revision(doc_id, user_id)
    ensure_ai_document_info(doc_id)
    enqueue_ai_job(doc_id, "update", user_id)
    rev = fetch_one("SELECT rev_no FROM doc_revisions WHERE revision_id=%s", (revision_id,))
    return {"doc_id": doc_id, "object_id": doc_id, "revision_id": revision_id, "rev_no": (rev or {}).get("rev_no")}


def delete_doc(doc_id: int, body: DocDeleteIn, user_id: int = Depends(get_current_user_id)):
    _storage_row(doc_id)
    node = _doc_node(doc_id)
    require_node_edit_access(user_id, node)
    ai = fetch_one("SELECT ai_document_id FROM ai_document_info WHERE object_id=%s", (int(doc_id),))
    execute("UPDATE storage_objects SET doc_status='deleted_pending', db_updated_at=NOW() WHERE object_id=%s", (int(doc_id),))
    if node:
        execute(
            """
            INSERT INTO document_deletions(
                object_id, node_id, ai_document_id, status, detected_at, remain_until,
                detected_by_user_id, detected_source, reason, created_at, updated_at
            )
            VALUES(%s,%s,%s,'pending',NOW(),NOW() + INTERVAL '7 days',%s,'manual_confirm','manual delete',NOW(),NOW())
            ON CONFLICT (object_id, active_deletion_marker) DO UPDATE SET
                status='pending',
                detected_at=NOW(),
                remain_until=NOW() + INTERVAL '7 days',
                updated_at=NOW()
            """,
            (int(doc_id), int(node["node_id"]), (ai or {}).get("ai_document_id"), user_id),
        )
    ensure_ai_document_info(doc_id, index_status="deleted")
    enqueue_ai_job(doc_id, "delete", user_id)
    return {"ok": True, "mode": body.mode, "deleted_doc_id": doc_id}


def update_doc(
    doc_id: int,
    payload: Dict[str, Any],
    user_id: int = Depends(get_current_user_id),
):
    _storage_row(doc_id)
    require_node_edit_access(user_id, _doc_node(doc_id))
    if "name" in payload:
        alias = str(payload.get("name") or "").strip()
        if not alias:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        execute("UPDATE tree_nodes SET alias=%s, updated_at=NOW() WHERE doc_id=%s", (alias, int(doc_id)))
        ensure_ai_document_info(doc_id)
        enqueue_ai_metadata_job(doc_id, user_id)
    return get_doc(doc_id, user_id=user_id)


def download_doc(
    doc_id: int,
    range: Optional[str] = Header(None),
    as_attachment: bool = False,
    user_id: int = Depends(get_current_user_id),
):
    require_object_permission(user_id, doc_id, "r")
    row = _storage_row(doc_id)
    if str(row.get("storage_backend") or "").lower() == "minio":
        revision = fetch_one(
            """
            SELECT revision_id,minio_version_id,size_bytes
              FROM doc_revisions
             WHERE doc_id=%s AND storage_status='READY' AND minio_version_id IS NOT NULL
             ORDER BY rev_no DESC,revision_id DESC LIMIT 1
            """,
            (int(doc_id),),
        )
        if not revision:
            raise HTTPException(status_code=409, detail="DOCUMENT_UPLOAD_NOT_READY")
        storage = get_minio_storage_service()
        try:
            metadata = storage.stat_object(
                row["bucket_name"], row["object_key"], version_id=revision["minio_version_id"]
            )
        except MinioObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="MINIO_DOCUMENT_VERSION_NOT_FOUND") from exc
        size = int(metadata.size_bytes)
        start, end, is_partial = 0, max(size - 1, 0), False
        if range and size > 0:
            try:
                units, value = range.split("=", 1)
                if units.strip().lower() != "bytes" or "," in value:
                    raise ValueError()
                start_text, end_text = value.split("-", 1)
                if not start_text:
                    suffix = int(end_text)
                    if suffix <= 0:
                        raise ValueError()
                    start, end = max(size - suffix, 0), size - 1
                else:
                    start = int(start_text)
                    end = int(end_text) if end_text else size - 1
                    end = min(end, size - 1)
                    if start < 0 or start > end:
                        raise ValueError()
                is_partial = True
            except Exception as exc:
                raise HTTPException(
                    status_code=416,
                    detail="INVALID_BYTE_RANGE",
                    headers={"Content-Range": f"bytes */{size}"},
                ) from exc
        length = end - start + 1 if is_partial else 0
        iterator = storage.iter_object(
            row["bucket_name"], row["object_key"],
            version_id=revision["minio_version_id"], offset=start, length=length,
        )
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(length if is_partial else size)}
        if is_partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        stream = StreamingResponse(
            iterator,
            media_type=row.get("mime_type") or "application/octet-stream",
            status_code=206 if is_partial else 200,
            headers=headers,
        )
    else:
        row, path = _file_response_path(doc_id)
        iterator, start, end, size, is_partial = _read_range(path, range)
        headers = {"Accept-Ranges": "bytes"}
        if is_partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        stream = StreamingResponse(
            iterator,
            media_type=row.get("mime_type") or "application/octet-stream",
            status_code=206 if is_partial else 200,
            headers=headers,
        )
    if as_attachment:
        stream.headers["Content-Disposition"] = _attachment_content_disposition(
            stored_filename(row.get("doc_name"), row.get("file_right"))
        )
    return stream


def _normalize_artifact_asset_path(asset_path: str) -> str:
    value = str(asset_path or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = value.strip("/")
    if value.startswith("assets/"):
        value = value[len("assets/"):]
    parts = value.split("/") if value else []
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=400, detail="INVALID_ARTIFACT_ASSET_PATH")
    return "/".join(parts)


def _read_manifest(storage, row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        metadata = storage.stat_object(
            row["bucket_name"], row["object_key"], version_id=row["minio_version_id"]
        )
        if int(metadata.size_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=409, detail="ARTIFACT_MANIFEST_TOO_LARGE")
        payload = b"".join(
            storage.iter_object(
                row["bucket_name"], row["object_key"], version_id=row["minio_version_id"]
            )
        )
    except MinioObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="ARTIFACT_MANIFEST_NOT_FOUND") from exc
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="INVALID_ARTIFACT_MANIFEST") from exc
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=409, detail="INVALID_ARTIFACT_MANIFEST")
    return manifest


def stream_doc_artifact_asset(
    doc_id: int,
    asset_path: str,
    user_id: int = Depends(get_current_user_id),
):
    """Stream an asset listed by the indexed revision's immutable MinIO manifest."""
    require_object_permission(user_id, doc_id, "r")
    source_path = _normalize_artifact_asset_path(asset_path)
    artifact = fetch_one(
        """
        SELECT a.bucket_name,a.object_key,a.minio_version_id,a.revision_id
          FROM ai_document_info ai
          JOIN ai_parse_artifacts a
            ON a.revision_id=COALESCE(ai.indexed_revision_id,ai.source_revision_id)
           AND a.artifact_type='manifest'
           AND a.artifact_status='READY'
         WHERE ai.object_id=%s
         ORDER BY a.artifact_id DESC
         LIMIT 1
        """,
        (int(doc_id),),
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="ARTIFACT_MANIFEST_NOT_FOUND")

    storage = get_minio_storage_service()
    manifest = _read_manifest(storage, artifact)
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise HTTPException(status_code=409, detail="INVALID_ARTIFACT_MANIFEST")

    selected = None
    for item in assets:
        if not isinstance(item, dict):
            continue
        try:
            item_path = _normalize_artifact_asset_path(item.get("source_path"))
        except HTTPException:
            continue
        if item_path == source_path:
            selected = item
            break
    if not selected:
        raise HTTPException(status_code=404, detail="ARTIFACT_ASSET_NOT_FOUND")

    manifest_prefix = str(artifact["object_key"]).rsplit("/", 1)[0]
    expected_key = f"{manifest_prefix}/assets/{source_path}"
    object_key = str(selected.get("object_key") or "")
    version_id = str(selected.get("version_id") or "").strip() or None
    if object_key != expected_key or not version_id:
        raise HTTPException(status_code=409, detail="INVALID_ARTIFACT_ASSET_LOCATION")

    try:
        metadata = storage.stat_object(
            artifact["bucket_name"], object_key, version_id=version_id
        )
        iterator = storage.iter_object(
            artifact["bucket_name"], object_key, version_id=version_id
        )
    except MinioObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="ARTIFACT_ASSET_NOT_FOUND") from exc

    media_type = metadata.content_type or mimetypes.guess_type(source_path)[0] or "application/octet-stream"
    headers = {
        "Content-Length": str(int(metadata.size_bytes)),
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }
    if metadata.etag:
        headers["ETag"] = f'"{metadata.etag}"'
    return StreamingResponse(iterator, media_type=media_type, headers=headers)


def copy_doc(
    doc_id: int,
    new_name: Optional[str] = Body(None, embed=True),
    parent_node_id: Optional[int] = Body(None, embed=True),
    tree_id: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(get_current_user_id),
):
    src = _storage_row(doc_id)
    require_object_permission(user_id, doc_id, "r")
    parent = _parent_for_upload(tree_id, parent_node_id)
    if not parent:
        raise HTTPException(status_code=400, detail="Parent folder not found")
    require_node_edit_access(user_id, parent)
    src_path = resolve_storage_path(src)
    name = str(new_name or f"{src.get('doc_name')}_copy").strip()
    filename = stored_filename(name, src.get("file_right"))
    rel, sub = storage_rel_sub_for_parent(as_int(parent.get("node_id")))
    dst_dir = os.path.join(
        base_dir_for_document_parent(parent, src.get("branch_type")),
        rel.replace("/", os.sep),
        sub.replace("/", os.sep),
    )
    os.makedirs(dst_dir, exist_ok=True)
    dst_path = os.path.join(dst_dir, filename)
    if os.path.exists(src_path):
        shutil.copy2(src_path, dst_path)
        meta = file_meta(dst_path)
    else:
        meta = {
            "checksum_sha256": src.get("checksum_sha256"),
            "size_bytes": src.get("size_bytes"),
            "mime_type": src.get("mime_type"),
            "source_created_at": src.get("source_created_at"),
            "source_updated_at": src.get("source_updated_at"),
        }
    object_id = _insert_storage_for_file(
        filename=filename,
        parent=parent,
        user_id=user_id,
        branch_type=src.get("branch_type") or "general",
        upload_mode=_upload_mode_for_parent(parent, src.get("upload_mode")),
        checksum_sha256=meta.get("checksum_sha256"),
        size_bytes=meta.get("size_bytes"),
        mime_type=meta.get("mime_type"),
        source_created_at=meta.get("source_created_at"),
        source_updated_at=meta.get("source_updated_at"),
        doc_code=src.get("doc_code") or "",
    )
    _insert_revision(object_id, user_id)
    node_id = _create_doc_node(object_id, parent, user_id)
    return {"doc_id": object_id, "object_id": object_id, "node_id": node_id, "message": "Copied"}


def copy_doc_to_personal(
    doc_id: int,
    data: DocCopyToPersonalIn,
    user_id: int = Depends(get_current_user_id),
):
    return minio_upload_service.copy_document_to_personal(
        source_object_id=doc_id,
        parent_node_id=data.parent_node_id,
        file_name=data.new_name,
        user_id=user_id,
    )


def move_doc(
    doc_id: int,
    new_parent_node: Optional[int] = Body(None, embed=True),
    new_tree: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(get_current_user_id),
):
    node = _doc_node(doc_id)
    if not node:
        raise HTTPException(status_code=404, detail="Document node not found")
    require_node_edit_access(user_id, node)
    parent = _parent_for_upload(new_tree, new_parent_node)
    if not parent:
        raise HTTPException(status_code=400, detail="Parent folder not found")
    require_node_edit_access(user_id, parent)
    if (
        as_int(node.get("kb_root_id")) != as_int(parent.get("kb_root_id"))
        or str(node.get("kb_type") or "").lower() != str(parent.get("kb_type") or "").lower()
        or as_int(node.get("kb_owner_user_id")) != as_int(parent.get("kb_owner_user_id"))
    ):
        raise HTTPException(status_code=403, detail="A document can only be moved within the same KB")
    execute(
        """
        UPDATE tree_nodes
           SET parent_id=%s,
               kb_root_id=%s,
               item_type_code=%s,
               updated_at=NOW()
         WHERE node_id=%s
        """,
        (parent["node_id"], parent["kb_root_id"], parent["item_type_code"], node["node_id"]),
    )
    rel, sub = storage_rel_sub_for_parent(as_int(parent.get("node_id")))
    execute("UPDATE storage_objects SET relative_path=%s, sub_path=%s, db_updated_at=NOW() WHERE object_id=%s", (rel, sub, int(doc_id)))
    ensure_ai_document_info(doc_id)
    enqueue_ai_metadata_job(doc_id, user_id)
    return {"doc_id": doc_id, "moved_node_id": node["node_id"], "message": "Moved successfully"}


def create_note(
    title: str = Body(..., embed=True),
    content: Optional[str] = Body("", embed=True),
    parent_node_id: Optional[int] = Body(None, embed=True),
    tree_id: Optional[int] = Body(None, embed=True),
    user_id: int = Depends(get_current_user_id),
):
    parent = _parent_for_upload(tree_id, parent_node_id)
    if not parent:
        raise HTTPException(status_code=400, detail="Parent folder not found")
    require_node_edit_access(user_id, parent)
    filename = f"{title}.txt"
    base = base_dir_for_document_parent(parent, "general")
    rel, sub = storage_rel_sub_for_parent(as_int(parent.get("node_id")))
    dst_dir = os.path.join(base, rel.replace("/", os.sep), sub.replace("/", os.sep))
    os.makedirs(dst_dir, exist_ok=True)
    dst_path = os.path.join(dst_dir, filename)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(content or "")
    meta = file_meta(dst_path)
    object_id = _insert_storage_for_file(
        filename=filename,
        parent=parent,
        user_id=user_id,
        branch_type="general",
        upload_mode=_upload_mode_for_parent(parent),
        checksum_sha256=meta["checksum_sha256"],
        size_bytes=meta["size_bytes"],
        mime_type="text/plain",
        source_created_at=meta["source_created_at"],
        source_updated_at=meta["source_updated_at"],
    )
    _insert_revision(object_id, user_id)
    node_id = _create_doc_node(object_id, parent, user_id)
    return {"doc_id": object_id, "object_id": object_id, "title": title, "tree_node_id": node_id, "message": "Created successfully"}


def reorder_tree_nodes(
    payload: Optional[Dict[str, Any]] = Body(None),
    user_id: int = Depends(get_current_user_id),
):
    payload = payload or {}
    orders = payload.get("orders") or []
    parent_id = as_int(payload.get("parent_id"))
    if parent_id is None:
        parent_id = as_int(payload.get("parent"))
    if not orders:
        return {"ok": True, "count": 0}
    if parent_id is None:
        raise HTTPException(status_code=400, detail="parent_id is required")
    parent = get_node(parent_id)
    require_node_edit_access(user_id, parent)
    count = 0
    for item in orders:
        node_id = as_int(item.get("node_id"))
        sort_order = as_int(item.get("sort_order"))
        if node_id is None or sort_order is None:
            continue
        row = get_node(node_id)
        if not row or as_int(row.get("parent_id")) != parent_id:
            continue
        execute("UPDATE tree_nodes SET sort_order=%s, updated_at=NOW() WHERE node_id=%s", (sort_order, node_id))
        count += 1
    return {"ok": True, "count": count}


def search_bookmarked_docs(
    q: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    params: List[Any] = [user_id]
    conds = ["b.user_id=%s"]
    acl_cond, acl_params = object_permission_exists_clause(user_id, "r", object_alias="so")
    conds.append(acl_cond)
    params.extend(acl_params)
    if q:
        conds.append("(b.title LIKE %s OR tn.alias LIKE %s OR so.doc_name LIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    rows = fetch_all(
        f"""
        SELECT
            b.bookmark_id,
            b.node_id,
            tn.doc_id AS object_id,
            COALESCE(b.title, tn.alias, so.doc_name) AS title,
            so.file_right,
            so.relative_path,
            so.sub_path,
            so.mime_type,
            so.branch_type,
            so.upload_mode,
            tn.kb_type,
            tn.kb_owner_user_id,
            so.uploaded_at,
            so.db_updated_at
          FROM bookmarks b
          JOIN tree_nodes tn ON tn.node_id = b.node_id
          JOIN storage_objects so ON so.object_id = tn.doc_id
         WHERE {" AND ".join(conds)}
         ORDER BY title
        """,
        tuple(params),
    )
    return [
        {
            "doc_id": r.get("object_id"),
            "object_id": r.get("object_id"),
            "title": r.get("title"),
            "storage_object_file_right": r.get("file_right"),
            "storage_object_relative_path": r.get("relative_path"),
            "storage_object_sub_path": r.get("sub_path"),
            "branch_type": str(r.get("branch_type") or "general").upper(),
            "storage_scope": "personal"
            if str(r.get("upload_mode") or "").lower() == "personal"
            or str(r.get("kb_type") or "").lower() in {"personal", "person"}
            else "kiwix"
            if str(r.get("branch_type") or "").upper() == "KIWIX"
            else "admin",
            "upload_mode": r.get("upload_mode"),
            "kb_type": r.get("kb_type"),
            "kb_owner_user_id": r.get("kb_owner_user_id"),
            "created_at": r.get("uploaded_at"),
            "updated_at": r.get("db_updated_at"),
            "mime_type": r.get("mime_type"),
        }
        for r in rows or []
    ]
