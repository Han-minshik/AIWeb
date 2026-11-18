import os
import json
import asyncio
import contextlib
import re
from typing import Optional, Any, Dict, Tuple, List, Set, Union, AsyncGenerator
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from fastapi import Request, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.utils.qa_streamer import enrich_stream_payload
from app.core.config import (
    RAG_SSE_URL,
    RAG_SSE_TOKEN,
    RAG_SSE_TIMEOUT,
    RAG_SSE_EXTRA_HEADERS,
)

from app.core.auth import get_current_user
from app.repositories.common import fetch_all, fetch_one
from app.services.common.naming_service import build_display_name, coerce_int
from app.services.common.rag_token_service import create_rag_user_token
from app.services.features.document_db import (
    accessible_object_ids_for_categories,
    filter_object_ids_by_permission,
    has_object_permission,
)


QA_STREAM_MOCK = os.getenv("QA_STREAM_MOCK", "").strip().lower() in ("1", "true", "yes")
ALL_DOCUMENTS_ITEM_TYPE_CODE = 10
try:
    QA_STREAM_HEARTBEAT_SEC = max(1, int(os.getenv("QA_STREAM_HEARTBEAT_SEC", "15")))
except Exception:
    QA_STREAM_HEARTBEAT_SEC = 15


def _sse_response_headers() -> Dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }


def _resolve_upstream_url(mode: Optional[str] = None) -> str:
    m = (mode or "").strip().lower()
    if m != "general":
        return RAG_SSE_URL
    p = urlsplit(RAG_SSE_URL)
    base_path = p.path or ""
    if base_path.endswith("/rag/agentic"):
        new_path = base_path[: -len("/rag/agentic")] + "/chat/general"
    elif base_path.endswith("/rag/sse"):
        new_path = base_path[: -len("/rag/sse")] + "/chat/general"
    else:
        new_path = "/chat/general"
    return urlunsplit((p.scheme, p.netloc, new_path, "", ""))


def _pick_mime(obj: Any, default: str = "image/jpeg") -> str:
    if isinstance(obj, dict):
        for k in ("thumb_mime", "thumbMime", "mime", "content_type", "contentType", "type"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                v = v.strip()
                return v if v.startswith("image/") else default
    if isinstance(obj, str) and obj.startswith("image/"):
        return obj
    return default


def _find_thumb_b64(obj: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(obj, dict):
        b64 = obj.get("thumb_b64") or obj.get("thumbnail_b64")
        if b64:
            return str(b64), _pick_mime(obj)
        for key in ("image_refs", "imageRefs", "images", "chunks", "refs"):
            if key in obj:
                tb64, tmime = _find_thumb_b64(obj[key])
                if tb64:
                    return tb64, tmime
        for v in obj.values():
            tb64, tmime = _find_thumb_b64(v)
            if tb64:
                return tb64, tmime
    elif isinstance(obj, (list, tuple)):
        for it in obj:
            tb64, tmime = _find_thumb_b64(it)
            if tb64:
                return tb64, tmime
    return None, None


def _normalize_sources(data: Any) -> List[dict]:
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("sources"), list):
            return [s for s in data["sources"] if isinstance(s, dict)]
        if isinstance(data.get("source"), dict):
            return [data["source"]]
    return []


def _normalize_status_stage(stage: Any) -> str:
    raw = str(stage or "").strip().lower()
    if not raw:
        return "analyzing"
    raw = raw.replace("-", "_").replace(" ", "_")
    if "no_result" in raw or "noresult" in raw or "empty" in raw:
        return "no_results"
    if "done" in raw or "complete" in raw or "finished" in raw:
        return "done"
    if "answer" in raw or "respond" in raw or "final" in raw:
        return "answering"
    if "summar" in raw or "reason" in raw or "think" in raw:
        return "summarizing"
    if "search" in raw or "retriev" in raw or "index" in raw:
        return "searching"
    if "analy" in raw or "question" in raw or "plan" in raw:
        return "analyzing"
    return "analyzing"


def _status_progress(stage: Any) -> int:
    normalized = _normalize_status_stage(stage)
    if normalized in ("searching", "no_results"):
        return 2
    if normalized == "summarizing":
        return 3
    if normalized in ("answering", "done"):
        return 4
    return 1


def _build_status_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    src = payload if isinstance(payload, dict) else {}
    raw_stage = str(src.get("stage") or "").strip()
    stage = _normalize_status_stage(raw_stage)
    message = src.get("message")
    if isinstance(message, str):
        message = message.strip()
    else:
        message = str(message or "").strip()

    out: Dict[str, Any] = dict(src)
    out["stage"] = stage
    out["progress"] = _status_progress(stage)
    if message:
        out["message"] = message
    elif "message" in out:
        out["message"] = ""
    if raw_stage and raw_stage.lower() != stage:
        out["raw_stage"] = raw_stage
    return out



def _parse_doc_ids(raw: Any) -> List[int]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out = []
        for v in raw:
            try:
                n = int(v)
            except Exception:
                continue
            out.append(n)
        return list(dict.fromkeys(out))
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith('[') and s.endswith(']'):
            try:
                arr = json.loads(s)
                return _parse_doc_ids(arr)
            except Exception:
                pass
        parts = re.split(r"[\s,;]+", s)
        out = []
        for p in parts:
            if not p:
                continue
            try:
                out.append(int(p))
            except Exception:
                continue
        return list(dict.fromkeys(out))
    return []


def _parse_doc_id_tokens(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: List[str] = []
        for v in raw:
            out.extend(_parse_doc_id_tokens(v))
        return list(dict.fromkeys(out))
    s = str(raw or "").strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            return _parse_doc_id_tokens(json.loads(s))
        except Exception:
            pass
    return list(dict.fromkeys([x.strip() for x in re.split(r"[\s,;]+", s) if x.strip()]))


def _parse_categories(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            arr = json.loads(s)
            return _parse_categories(arr)
        except Exception:
            pass
    return [x.strip() for x in re.split(r"[,;]+", s) if x.strip()]


def _parse_item_type_codes(raw: Any) -> List[int]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: List[int] = []
        for v in raw:
            out.extend(_parse_item_type_codes(v))
        return list(dict.fromkeys(out))
    s = str(raw or "").strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            return _parse_item_type_codes(json.loads(s))
        except Exception:
            pass
    out: List[int] = []
    for part in re.split(r"[\s,;]+", s):
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            continue
    return list(dict.fromkeys(out))


def _current_user_id_from_request(request: Request) -> int:
    user = get_current_user(request)
    user_id = user.get("user_id") or user.get("id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return int(user_id)


def _rag_user_token_from_request(request: Request) -> str:
    if not RAG_SSE_TOKEN:
        raise HTTPException(status_code=503, detail="RAG_SSE_TOKEN is not configured")
    try:
        return create_rag_user_token(get_current_user(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _apply_qa_acl(
    user_id: int,
    doc_ids_list: List[int],
    cat: Any,
    *,
    source: Optional[str] = None,
    item_type_codes: Optional[List[int]] = None,
) -> List[int]:
    filtered = filter_object_ids_by_permission(user_id, doc_ids_list, "x") if doc_ids_list else []
    if doc_ids_list and not filtered:
        raise HTTPException(status_code=403, detail="No AI-usable documents are available.")
    if filtered:
        return filtered

    source_norm = str(source or "").strip().lower()
    if source_norm == "bookmark":
        return filtered

    if item_type_codes:
        if ALL_DOCUMENTS_ITEM_TYPE_CODE in item_type_codes:
            return []
        object_ids = _object_ids_for_item_type_codes(item_type_codes)
        allowed = filter_object_ids_by_permission(user_id, object_ids, "x") if object_ids else []
        if not allowed:
            raise HTTPException(status_code=403, detail="No AI-usable documents are available for this KB.")
        return allowed

    categories = _parse_categories(cat)
    if categories:
        allowed = accessible_object_ids_for_categories(user_id, categories, "x")
        if not allowed:
            raise HTTPException(status_code=403, detail="No AI-usable documents are available for this KB.")
        return allowed
    return []


def _object_ids_for_item_type_codes(item_type_codes: List[int]) -> List[int]:
    codes = [int(x) for x in item_type_codes if x is not None]
    if not codes:
        return []
    placeholders = ",".join(["%s"] * len(codes))
    rows = fetch_all(
        f"""
        SELECT DISTINCT tn.doc_id AS object_id
          FROM tree_nodes tn
          JOIN storage_objects so ON so.object_id = tn.doc_id
         WHERE tn.doc_id IS NOT NULL
           AND tn.item_type_code IN ({placeholders})
           AND so.doc_status = 'active'
        """,
        tuple(codes),
    )
    out: List[int] = []
    for row in rows or []:
        try:
            oid = int(row.get("object_id"))
        except Exception:
            continue
        if oid not in out:
            out.append(oid)
    return out


def _ai_document_ids_for_object_ids(object_ids: List[int]) -> List[str]:
    ids = [int(x) for x in object_ids if x is not None]
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT object_id, ai_document_id
          FROM ai_document_info
         WHERE object_id IN ({placeholders})
           AND ai_document_id IS NOT NULL
           AND ai_document_id <> ''
        """,
        tuple(ids),
    )
    by_object_id: Dict[int, str] = {}
    for row in rows or []:
        try:
            oid = int(row.get("object_id"))
        except Exception:
            continue
        aid = str(row.get("ai_document_id") or "").strip()
        if aid:
            by_object_id[oid] = aid
    out: List[str] = []
    for oid in ids:
        aid = by_object_id.get(oid)
        if aid and aid not in out:
            out.append(aid)
    return out


def _object_ids_for_ai_document_ids(ai_document_ids: List[str]) -> List[int]:
    ids = [str(x or "").strip() for x in ai_document_ids if str(x or "").strip()]
    if not ids:
        return []
    ids = list(dict.fromkeys(ids))
    placeholders = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT object_id, ai_document_id
          FROM ai_document_info
         WHERE ai_document_id IN ({placeholders})
           AND object_id IS NOT NULL
        """,
        tuple(ids),
    )
    by_ai_document_id: Dict[str, int] = {}
    for row in rows or []:
        aid = str(row.get("ai_document_id") or "").strip()
        try:
            oid = int(row.get("object_id"))
        except Exception:
            continue
        if aid:
            by_ai_document_id[aid] = oid
    out: List[int] = []
    for aid in ids:
        oid = by_ai_document_id.get(aid)
        if oid is not None and oid not in out:
            out.append(oid)
    return out


def _object_ids_for_personal_kb(user_id: int, kb_id: Optional[int]) -> Optional[List[int]]:
    if kb_id is None:
        return None
    root = fetch_one(
        """
        SELECT node_id, kb_root_id, kb_type, kb_owner_user_id
        FROM tree_nodes
        WHERE node_id=%s AND is_folder=TRUE
        LIMIT 1
        """,
        (int(kb_id),),
    )
    if not root:
        return None
    if str(root.get("kb_type") or "").strip().lower() not in {"personal", "person"}:
        return None
    if int(root.get("kb_owner_user_id") or 0) != int(user_id):
        raise HTTPException(status_code=403, detail="Personal KB permission denied.")

    rows = fetch_all(
        """
        SELECT DISTINCT tn.doc_id AS object_id
        FROM tree_nodes tn
        JOIN storage_objects so ON so.object_id = tn.doc_id
        WHERE tn.kb_root_id=%s
          AND tn.doc_id IS NOT NULL
          AND tn.is_folder=FALSE
          AND so.doc_status='active'
          AND so.upload_mode='personal'
          AND so.uploader_user_id=%s
        ORDER BY tn.doc_id
        """,
        (int(root.get("kb_root_id") or root.get("node_id")), int(user_id)),
    )
    out: List[int] = []
    for row in rows or []:
        oid = _coerce_int(row.get("object_id"))
        if oid is not None and oid not in out:
            out.append(oid)
    return out





def _object_ids_are_personal_for_user(user_id: int, object_ids: List[int]) -> bool:
    ids = list(dict.fromkeys([int(x) for x in object_ids if x is not None]))
    if not ids:
        return False
    placeholders = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT DISTINCT so.object_id
          FROM storage_objects so
          LEFT JOIN ai_document_info ai ON ai.object_id = so.object_id
          LEFT JOIN tree_nodes tn ON tn.doc_id = so.object_id
         WHERE so.object_id IN ({placeholders})
           AND so.doc_status = 'active'
           AND (
                COALESCE(so.upload_mode, '') = 'personal'
                OR COALESCE(tn.kb_type, '') IN ('personal', 'person')
           )
           AND (
                so.uploader_user_id = %s
                OR tn.kb_owner_user_id = %s
                OR ai.user_id = %s
           )
        """,
        tuple(ids + [int(user_id), int(user_id), int(user_id)]),
    )
    found = set()
    for row in rows or []:
        oid = _coerce_int(row.get("object_id"))
        if oid is not None:
            found.add(int(oid))
    return found == set(ids)


def _coerce_int(x: Any) -> Optional[int]:
    return coerce_int(x, None)


def _resolve_object_id_from_doc_master(doc_master_id: int) -> Optional[int]:
    row = fetch_one(
        "SELECT object_id FROM storage_objects WHERE object_id=%s LIMIT 1",
        (int(doc_master_id),),
    )
    if not row or row.get("object_id") is None:
        return None
    try:
        return int(row["object_id"])
    except Exception:
        return None

def _filter_bookmark_doc_ids(
    user_id: int,
    doc_ids: List[int],
    *,
    ai_document_ids: Optional[List[str]] = None,
) -> List[int]:
    ids = [int(x) for x in doc_ids if x is not None]
    for oid in _object_ids_for_ai_document_ids(ai_document_ids or []):
        if oid not in ids:
            ids.append(oid)
    if not ids:
        return []

    placeholders = ",".join(["%s"] * len(ids))

    rows_obj = fetch_all(
        f"""
        SELECT DISTINCT tn.doc_id AS object_id
          FROM bookmarks b
          JOIN tree_nodes tn ON tn.node_id = b.node_id
         WHERE b.user_id=%s
           AND tn.doc_id IN ({placeholders})
        """,
        tuple([user_id] + ids),
    )

    out = []
    for r in rows_obj or []:
        oid = r.get("object_id") if isinstance(r, dict) else None
        if oid is None:
            continue
        try:
            out.append(int(oid))
        except Exception:
            continue

    return list(dict.fromkeys(out))




def _pick_first_non_empty(*vals: Any) -> Any:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _normalize_ext(v: Any) -> Optional[str]:
    s = str(v or "").strip().lstrip(".").lower()
    return s or None


def _normalize_branch_type(v: Any) -> Optional[str]:
    s = str(v or "").strip().upper()
    return s or None


def _build_display_name(name: Any, file_right: Any) -> str:
    return build_display_name(
        str(name or "").strip(),
        str(file_right or "").strip().lstrip("."),
        fallback="",
    )


def _join_rel_sub(rel: Any, sub: Any, filename: Any) -> Optional[str]:
    rel_s = str(rel or "").strip().replace("\\", "/").strip("/")
    sub_s = str(sub or "").strip().replace("\\", "/").strip("/")
    file_s = str(filename or "").strip().replace("\\", "/").strip("/")
    parts = [p for p in (rel_s, sub_s, file_s) if p]
    return "/".join(parts) if parts else None


def _extract_source_object_id(src: Dict[str, Any]) -> Optional[int]:
    meta = src.get("meta") if isinstance(src.get("meta"), dict) else {}
    raw = _pick_first_non_empty(
        src.get("object_id"),
        src.get("objectId"),
        src.get("storage_object_id"),
        src.get("storageObjectId"),
        src.get("id"),
        meta.get("object_id"),
        meta.get("objectId"),
        meta.get("storage_object_id"),
        meta.get("storageObjectId"),
        meta.get("id"),
    )
    object_id = _coerce_int(raw)
    if object_id is not None:
        return object_id

    ai_document_id = str(
        _pick_first_non_empty(
            src.get("ai_document_id"),
            src.get("aiDocumentId"),
            src.get("aid"),
            meta.get("ai_document_id"),
            meta.get("aiDocumentId"),
            meta.get("aid"),
        )
        or ""
    ).strip()
    if not ai_document_id:
        return None
    row = fetch_one(
        """
        SELECT object_id
          FROM ai_document_info
         WHERE ai_document_id=%s
           AND object_id IS NOT NULL
         LIMIT 1
        """,
        (ai_document_id,),
    )
    return _coerce_int((row or {}).get("object_id"))


def _extract_source_title(src: Dict[str, Any]) -> Optional[str]:
    meta = src.get("meta") if isinstance(src.get("meta"), dict) else {}
    raw = _pick_first_non_empty(
        src.get("doc_title"),
        src.get("docTitle"),
        src.get("title"),
        src.get("name"),
        src.get("label"),
        meta.get("doc_title"),
        meta.get("docTitle"),
        meta.get("title"),
        meta.get("name"),
    )
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _row_to_source_hint(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    object_id = _coerce_int(row.get("object_id"))
    if object_id is None:
        return None
    file_right = _normalize_ext(row.get("file_right"))
    rel = str(row.get("relative_path") or "").strip().replace("\\", "/").strip("/") or None
    sub = str(row.get("sub_path") or "").strip().replace("\\", "/").strip("/") or None
    doc_code = str(row.get("doc_code") or "").strip() or None
    branch = _normalize_branch_type(row.get("branch_type")) or "GENERAL"
    display_name = _build_display_name(row.get("name"), file_right)
    stored = _join_rel_sub(rel, sub, display_name)
    itemtype_name = sub.split("/", 1)[0] if branch == "KIWIX" and sub else None
    return {
        "object_id": object_id,
        "ai_document_id": str(row.get("ai_document_id") or "").strip() or None,
        "doc_title": display_name or str(row.get("name") or "").strip() or None,
        "doc_code": doc_code,
        "file_right": file_right,
        "relative_path": rel,
        "sub_path": sub,
        "branch_type": branch,
        "stored_path": stored,
        "itemtypeName": itemtype_name,
    }


def _lookup_source_hint_by_object_id(object_id: int) -> Optional[Dict[str, Any]]:
    if object_id is None:
        return None
    row = fetch_one(
        """
        SELECT
            so.object_id,
            ai.ai_document_id,
            so.doc_code,
            so.doc_name AS name,
            so.file_right,
            so.relative_path,
            so.sub_path,
            UPPER(COALESCE(so.branch_type, 'general')) AS branch_type
        FROM storage_objects so
        LEFT JOIN ai_document_info ai ON ai.object_id = so.object_id
        WHERE so.object_id=%s
        LIMIT 1
        """,
        (int(object_id),),
    )
    return _row_to_source_hint(row)


def _lookup_source_hint_by_title(title: str) -> Optional[Dict[str, Any]]:
    name = str(title or "").strip()
    if not name:
        return None
    base, ext = os.path.splitext(name)
    base = (base or name).strip()

    def _lookup_exact(candidate: str) -> Optional[Dict[str, Any]]:
        row = fetch_one(
            """
            SELECT
                so.object_id,
                ai.ai_document_id,
                so.doc_code,
                so.doc_name AS name,
                so.file_right,
                so.relative_path,
                so.sub_path,
                UPPER(COALESCE(so.branch_type, 'general')) AS branch_type
            FROM tree_nodes tn
            JOIN storage_objects so ON so.object_id = tn.doc_id
            LEFT JOIN ai_document_info ai ON ai.object_id = so.object_id
            WHERE tn.is_folder = FALSE
              AND (
                    LOWER(tn.name) = LOWER(%s)
                 OR LOWER(tn.alias) = LOWER(%s)
                 OR LOWER(so.doc_name) = LOWER(%s)
                 OR LOWER(
                        CONCAT(
                            so.doc_name,
                            CASE
                                WHEN COALESCE(so.file_right, '') = '' THEN ''
                                ELSE CONCAT('.', so.file_right)
                            END
                        )
                    ) = LOWER(%s)
                  )
            ORDER BY
                CASE WHEN UPPER(COALESCE(so.branch_type, 'general')) = 'KIWIX' THEN 0 ELSE 1 END,
                so.object_id DESC
            LIMIT 1
            """,
            (candidate, candidate, candidate, candidate),
        )
        return _row_to_source_hint(row)

    tried: List[str] = []
    for cand in (name, base):
        c = str(cand or "").strip()
        if not c or c.lower() in tried:
            continue
        tried.append(c.lower())
        hit = _lookup_exact(c)
        if hit:
            return hit

    if not ext:
        row_like = fetch_one(
            """
            SELECT
                so.object_id,
                so.doc_code,
                so.doc_name AS name,
                so.file_right,
                so.relative_path,
                so.sub_path,
                UPPER(COALESCE(so.branch_type, 'general')) AS branch_type
            FROM tree_nodes tn
            JOIN storage_objects so ON so.object_id = tn.doc_id
            WHERE tn.is_folder = FALSE
              AND (
                    LOWER(tn.name) LIKE LOWER(CONCAT(%s, '.%%'))
                 OR LOWER(tn.alias) LIKE LOWER(CONCAT(%s, '.%%'))
                 OR LOWER(so.doc_name) = LOWER(%s)
                  )
            ORDER BY
                CASE WHEN UPPER(COALESCE(so.branch_type, 'general')) = 'KIWIX' THEN 0 ELSE 1 END,
                so.object_id DESC
            LIMIT 1
            """,
            (base, base, base),
        )
        return _row_to_source_hint(row_like)

    return None


def _apply_source_hint(src: Dict[str, Any], hint: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(src, dict) or not isinstance(hint, dict):
        return src
    out = dict(src)
    meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
    meta = dict(meta)

    object_id = _coerce_int(hint.get("object_id"))
    if object_id is not None:
        out["object_id"] = object_id
        if out.get("id") is None:
            out["id"] = object_id
        meta["object_id"] = object_id
        meta["storage_object_id"] = object_id

    for key in ("doc_code", "file_right", "relative_path", "sub_path", "branch_type", "stored_path"):
        val = hint.get(key)
        if val is None:
            continue
        out[key] = val
        meta[key] = val

    if "itemtypeName" in hint:
        out["itemtypeName"] = hint.get("itemtypeName")
        meta["itemtypeName"] = hint.get("itemtypeName")

    doc_title = str(hint.get("doc_title") or "").strip() or None
    if doc_title:
        out.setdefault("doc_title", doc_title)
        meta.setdefault("doc_title", doc_title)

    out["meta"] = meta
    if object_id is not None:
        out = _rewrite_source_image_urls(out, object_id)
    return out


def _artifact_source_path(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    value = unquote(raw.strip()).replace("\\", "/")
    if not value or re.match(r"^(?:https?:|data:|blob:)", value, flags=re.IGNORECASE):
        return None
    if re.match(r"^/(?:api/)?docs/\d+/artifacts/", value, flags=re.IGNORECASE):
        return None
    value = value.split("?", 1)[0].split("#", 1)[0]
    while value.startswith("./"):
        value = value[2:]
    value = value.strip("/")
    lower = value.lower()
    for marker in ("assets/", "images/"):
        index = lower.rfind(marker)
        if index >= 0:
            value = value[index + (len("assets/") if marker == "assets/" else 0):]
            break
    parts = value.split("/") if value else []
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _artifact_asset_url(object_id: int, raw: Any) -> Optional[str]:
    source_path = _artifact_source_path(raw)
    if not source_path:
        return None
    return f"/docs/{int(object_id)}/artifacts/{quote(source_path, safe='/')}"


def _rewrite_image_ref(ref: Any, object_id: int) -> Any:
    if isinstance(ref, str):
        return _artifact_asset_url(object_id, ref) or ref
    if not isinstance(ref, dict):
        return ref
    out = dict(ref)
    raw = next(
        (
            value for value in (
                out.get("url"), out.get("href"), out.get("path"), out.get("src"),
                out.get("relpath", {}).get("path") if isinstance(out.get("relpath"), dict) else out.get("relpath"),
            )
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    url = _artifact_asset_url(object_id, raw)
    if url:
        out["url"] = url
    return out


def _rewrite_source_image_urls(src: Dict[str, Any], object_id: int) -> Dict[str, Any]:
    out = dict(src)
    for key in ("images", "image_refs"):
        values = out.get(key)
        if isinstance(values, list):
            out[key] = [_rewrite_image_ref(item, object_id) for item in values]
    chunks = out.get("chunks")
    if isinstance(chunks, list):
        rewritten_chunks = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                rewritten_chunks.append(chunk)
                continue
            updated = dict(chunk)
            for key in ("images", "image_refs"):
                values = updated.get(key)
                if isinstance(values, list):
                    updated[key] = [_rewrite_image_ref(item, object_id) for item in values]
            rewritten_chunks.append(updated)
        out["chunks"] = rewritten_chunks
    meta = out.get("meta")
    if isinstance(meta, dict):
        updated_meta = dict(meta)
        for key in ("images", "image_refs"):
            values = updated_meta.get(key)
            if isinstance(values, list):
                updated_meta[key] = [_rewrite_image_ref(item, object_id) for item in values]
        meta_chunks = updated_meta.get("chunks")
        if isinstance(meta_chunks, list):
            updated_meta["chunks"] = [
                _rewrite_source_image_urls(chunk, object_id) if isinstance(chunk, dict) else chunk
                for chunk in meta_chunks
            ]
        out["meta"] = updated_meta
    return out


def _enrich_sources_with_db(
    srcs: List[dict],
    cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
    *,
    user_id: Optional[int] = None,
    permission: str = "x",
) -> List[dict]:
    arr = [s for s in (srcs or []) if isinstance(s, dict)]
    if not arr:
        return []
    local_cache = cache if isinstance(cache, dict) else {}
    out: List[dict] = []
    for src in arr:
        hint: Optional[Dict[str, Any]] = None

        object_id = _extract_source_object_id(src)
        if object_id is not None:
            key = f"oid:{int(object_id)}"
            if key not in local_cache:
                local_cache[key] = _lookup_source_hint_by_object_id(int(object_id))
            hint = local_cache.get(key)

        if hint is None:
            title = _extract_source_title(src)
            if title:
                key = f"title:{title.lower()}"
                if key not in local_cache:
                    local_cache[key] = _lookup_source_hint_by_title(title)
                hint = local_cache.get(key)

        if hint and user_id is not None:
            object_id_for_acl = _coerce_int(hint.get("object_id"))
            if object_id_for_acl is not None and not has_object_permission(int(user_id), int(object_id_for_acl), permission):
                continue
        out.append(_apply_source_hint(src, hint) if hint else src)
    return out


async def _proxy_rag_sse(
    request: Request,
    question: str,
    rag_user_token: str,
    cat: Optional[str] = None,
    doc_id: Optional[str] = None,
    doc_ids: Optional[List[int]] = None,
    item_type_codes: Optional[List[int]] = None,
    item_type_code_is_scalar: bool = False,
    ai_document_ids: Optional[List[str]] = None,
    rag_user_id: Optional[int] = None,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    mode: Optional[str] = None,
    session_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    sources_buf: Optional[List[dict]] = None
    source_hint_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    thumb_payload: Dict[str, str] = {}
    current_event: Optional[str] = None
    data_lines: List[str] = []
    current_user_id = _current_user_id_from_request(request)

    yield "event: meta\ndata: " + json.dumps({"status": "streaming"}, ensure_ascii=False) + "\n\n"

    headers = {"Accept": "text/event-stream"}
    if RAG_SSE_EXTRA_HEADERS:
        headers.update(RAG_SSE_EXTRA_HEADERS)
    headers["Authorization"] = f"Bearer {RAG_SSE_TOKEN}"
    headers["X-SpaceAI-User-Token"] = f"Bearer {rag_user_token}"

    params = {"question": question}
    if session_id is not None:
        params["session_id"] = str(int(session_id))
    if history:
        params["history"] = history
    if item_type_codes:
        serialized_item_type_codes = [str(x) for x in item_type_codes if x is not None]
        if item_type_code_is_scalar and len(serialized_item_type_codes) == 1:
            params["item_type_code"] = serialized_item_type_codes[0]
        else:
            params["item_type_code"] = serialized_item_type_codes
    if cat:
        if isinstance(cat, (list, tuple, set)):
            cat_list = [str(x).strip() for x in cat if str(x).strip()]
        else:
            cat_list = [str(cat).strip()] if str(cat).strip() else []
        if cat_list:
            params["cat"] = cat_list
    has_ai_document_ids = False
    if ai_document_ids:
        aid_list = [str(x).strip() for x in ai_document_ids if str(x).strip()]
        if aid_list:
            params["ai_document_id"] = aid_list
            has_ai_document_ids = True
    if rag_user_id is not None:
        params["user_id"] = int(rag_user_id)
    if not has_ai_document_ids and doc_ids:
        doc_id_list = [str(x).strip() for x in doc_ids if x is not None and str(x).strip()]
        if doc_id_list:
            params["doc_id"] = doc_id_list
    elif not has_ai_document_ids and doc_id is not None:
        parsed_doc_ids = _parse_doc_ids(doc_id)
        if parsed_doc_ids:
            params["doc_id"] = [str(x) for x in parsed_doc_ids]
        elif str(doc_id).strip():
            params["doc_id"] = [str(doc_id).strip()]
    if top_k is not None:
        params["top_k"] = str(top_k)

    pass  # debug output removed

    timeout = httpx.Timeout(RAG_SSE_TIMEOUT)

    req = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream_url = _resolve_upstream_url(mode)
            pass  # debug output removed
            pass  # debug output removed

            def _mask_headers(h: Dict[str, str]) -> Dict[str, str]:
                sensitive = {"authorization", "x-spaceai-user-token"}
                return {k: ("***" if k.lower() in sensitive else v) for k, v in h.items()}

            query_parts = []
            for k, v in params.items():
                if k == "history":
                    continue
                if k == "doc_id" and isinstance(v, (list, tuple, set)):
                    vals = [str(item).strip() for item in v if str(item).strip()]
                    if not vals:
                        continue
                    if len(vals) > 1:
                        joined = "[" + ", ".join(vals) + "]"
                        query_parts.append(f"{quote(str(k), safe='')}={quote(joined, safe='')}")
                    else:
                        query_parts.append(f"{quote(str(k), safe='')}={quote(vals[0], safe='')}")
                elif isinstance(v, (list, tuple, set)):
                    for item in v:
                        query_parts.append(f"{quote(str(k), safe='')}={quote(str(item), safe='')}")
                else:
                    query_parts.append(f"{quote(str(k), safe='')}={quote(str(v), safe='')}")
            request_url = f"{upstream_url}?{'&'.join(query_parts)}" if query_parts else upstream_url
            req = client.build_request("POST", request_url, headers=headers, json=params)
            pass  # debug output removed
            try:
                pass  # debug output removed
            except Exception:
                pass
            pass  # debug output removed

            r = await client.send(req, stream=True)
            try:
                if r.status_code >= 400:
                    pass  # debug output removed
                    pass  # debug output removed
                    try:
                        pass  # debug output removed
                    except Exception:
                        pass
                    try:
                        err_body = (await r.aread()).decode("utf-8", errors="replace")
                        pass  # debug output removed
                    except Exception:
                        pass
                    raise HTTPException(status_code=502, detail=f"LLM SSE 호출 실패: {r.status_code}")

                line_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
                upstream_error: Optional[Exception] = None

                async def _pump_upstream_lines() -> None:
                    nonlocal upstream_error
                    try:
                        async for upstream_line in r.aiter_lines():
                            await line_queue.put(upstream_line)
                    except Exception as pump_err:
                        upstream_error = pump_err
                    finally:
                        await line_queue.put(None)

                pump_task = asyncio.create_task(_pump_upstream_lines())
                try:
                    while True:
                        if await request.is_disconnected():
                            pump_task.cancel()
                            break

                        try:
                            line = await asyncio.wait_for(line_queue.get(), timeout=QA_STREAM_HEARTBEAT_SEC)
                        except asyncio.TimeoutError:
                            # Keep the downstream SSE connection active through HTTP proxies.
                            yield ": keep-alive\n\n"
                            continue

                        if line is None:
                            if upstream_error is not None:
                                raise upstream_error
                            break

                        line = line.strip("\n")
                        if not line:
                            if current_event or data_lines:
                                raw = "\n".join(data_lines).strip()
                                try:
                                    payload = json.loads(raw) if raw else {}
                                except Exception:
                                    payload = {"text": raw}
                                ev = (current_event or "").lower()
                                if ev == "status":
                                    status_payload = _build_status_payload(payload if isinstance(payload, dict) else {})
                                    yield "event: status\ndata: " + json.dumps(status_payload, ensure_ascii=False) + "\n\n"
                                    yield "event: step\ndata: " + json.dumps(
                                        {
                                            "progress": status_payload.get("progress", 1),
                                            "stage": status_payload.get("stage", "analyzing"),
                                            "message": status_payload.get("message", ""),
                                        },
                                        ensure_ascii=False,
                                    ) + "\n\n"
                                elif ev == "token":
                                    txt = payload.get("text")
                                    if isinstance(txt, str) and txt:
                                        yield "event: chunk\ndata: " + json.dumps({"text": txt}, ensure_ascii=False) + "\n\n"
                                elif ev == "sources":
                                    norm_sources = _enrich_sources_with_db(
                                        _normalize_sources(payload),
                                        source_hint_cache,
                                        user_id=current_user_id,
                                        permission="x",
                                    )
                                    sources_buf = norm_sources or sources_buf
                                    meta_now: Dict[str, Any] = {}
                                    if norm_sources:
                                        meta_now["sources"] = norm_sources
                                    enriched = enrich_stream_payload({"sources": norm_sources} if norm_sources else {})
                                    tb64, tmime = _find_thumb_b64(enriched)
                                    if tb64:
                                        thumb_payload = {"thumb_b64": tb64, "thumb_mime": tmime or "image/jpeg"}
                                        meta_now.update(thumb_payload)
                                    if meta_now:
                                        yield "event: meta\ndata: " + json.dumps(meta_now, ensure_ascii=False) + "\n\n"
                                current_event = None
                                data_lines = []
                            continue
                        if line.startswith(":"):
                            # Forward upstream keep-alive comments instead of dropping them.
                            yield f"{line}\n\n"
                            continue
                        if line.startswith("event:"):
                            current_event = line.split("event:", 1)[1].strip()
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line.split("data:", 1)[1].lstrip())
                            continue
                        data_lines.append(line)
                finally:
                    if not pump_task.done():
                        pump_task.cancel()
                    with contextlib.suppress(Exception):
                        await pump_task
            finally:
                await r.aclose()
    except asyncio.CancelledError:
        return
    except Exception as e:
        if req is not None:
            pass  # debug output removed
            pass  # debug output removed
            try:
                pass  # debug output removed
            except Exception:
                pass
        yield "event: error\ndata: " + json.dumps(f"업스트림 오류: {str(e)}", ensure_ascii=False) + "\n\n"

    done_payload: Dict[str, Any] = {"status": "completed"}
    if sources_buf:
        done_payload["sources"] = sources_buf
    if thumb_payload:
        done_payload.update(thumb_payload)
    yield "event: done\ndata: " + json.dumps(done_payload, ensure_ascii=False) + "\n\n"


async def _mock_rag_sse(
    question: str,
    cat: Optional[str] = None,
    doc_id: Optional[str] = None,
    doc_ids: Optional[List[int]] = None,
    item_type_codes: Optional[List[int]] = None,
    ai_document_ids: Optional[List[str]] = None,
    rag_user_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    yield "event: meta\ndata: " + json.dumps({"status": "streaming", "mock": True}, ensure_ascii=False) + "\n\n"
    await asyncio.sleep(0.05)
    yield "event: step\ndata: " + json.dumps({"progress": 2}, ensure_ascii=False) + "\n\n"
    await asyncio.sleep(0.05)
    prefix = "[MOCK] "
    ctx = {
        "cat": cat,
        "doc_id": doc_id,
        "doc_ids": doc_ids or [],
        "item_type_code": item_type_codes or [],
        "ai_document_id": ai_document_ids or [],
        "user_id": rag_user_id,
    }
    msg = (
        f"{prefix}LLM server disabled. Received question: {question}\\n"
        f"Context: {json.dumps(ctx, ensure_ascii=False)}\\n"
    )
    for chunk in (msg[i : i + 80] for i in range(0, len(msg), 80)):
        yield "event: chunk\ndata: " + json.dumps({"text": chunk}, ensure_ascii=False) + "\n\n"
        await asyncio.sleep(0.02)
    yield "event: done\ndata: " + json.dumps({"status": "completed", "mock": True}, ensure_ascii=False) + "\n\n"


async def stream_get(
    request: Request,
    key: Optional[str] = Query(default=None, description="질문 키워드"),
    cat: Optional[str] = Query(default=None),
    item_type_code: Optional[str] = Query(default=None),
    kb_id: Optional[int] = Query(default=None),
    doc_id: Optional[str] = Query(default=None),
    doc_master_id: Optional[str] = Query(default=None),
    doc_ids: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    top_k: Optional[int] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
):
    pass  # debug output removed
    user_id_for_acl = _current_user_id_from_request(request)
    if doc_id is None and doc_master_id is not None:
        dm = _coerce_int(doc_master_id)
        if dm is not None:
            oid = _resolve_object_id_from_doc_master(dm)
            if oid is not None:
                doc_id = str(oid)
            else:
                doc_id = str(dm)
    doc_id_tokens = _parse_doc_id_tokens(doc_ids)
    for token in _parse_doc_id_tokens(doc_id):
        if token not in doc_id_tokens:
            doc_id_tokens.append(token)
    doc_ids_list = _parse_doc_ids(doc_ids)
    doc_ids_from_doc_id = _parse_doc_ids(doc_id)
    for did in doc_ids_from_doc_id:
        if did not in doc_ids_list:
            doc_ids_list.append(did)
    if doc_id is not None and not doc_ids_from_doc_id:
        try:
            did = int(doc_id)
            if did not in doc_ids_list:
                doc_ids_list.append(did)
        except Exception:
            pass
    source_norm = str(source or "").strip().lower()
    item_type_codes = _parse_item_type_codes(item_type_code)
    item_type_code_is_scalar = item_type_code is not None and len(item_type_codes) == 1
    bookmark_mode = source_norm == "bookmark"
    rag_user_id: Optional[int] = None
    if bookmark_mode:
        doc_ids_list = _filter_bookmark_doc_ids(
            int(user_id_for_acl),
            doc_ids_list,
            ai_document_ids=doc_id_tokens,
        )
        if not doc_ids_list:
            raise HTTPException(status_code=403, detail="No bookmark documents available.")
        doc_id = ",".join(str(x) for x in doc_ids_list)
    elif source_norm == "personal":
        if user_id is not None and int(user_id) != int(user_id_for_acl):
            raise HTTPException(status_code=403, detail="Personal KB permission denied.")
        rag_user_id = int(user_id_for_acl)
        doc_ids_list = []
        doc_id = None
        cat = None
    elif source_norm == "vault":
        personal_object_ids = _object_ids_for_personal_kb(user_id_for_acl, kb_id)
        if personal_object_ids is not None:
            if not personal_object_ids:
                raise HTTPException(status_code=403, detail="No AI-usable documents are available for this personal KB.")
            rag_user_id = int(user_id_for_acl)
            doc_ids_list = []
            doc_id = None
            item_type_codes = []
            cat = None
    doc_ids_list = _apply_qa_acl(
        user_id_for_acl,
        doc_ids_list,
        cat,
        source=source,
        item_type_codes=item_type_codes,
    )
    if doc_ids_list:
        doc_id = ",".join(str(x) for x in doc_ids_list)
    if (
        rag_user_id is None
        and (doc_ids_list or doc_id_tokens)
        and _object_ids_are_personal_for_user(
            user_id_for_acl,
            doc_ids_list or _object_ids_for_ai_document_ids(doc_id_tokens),
        )
    ):
        if user_id is not None and int(user_id) != int(user_id_for_acl):
            raise HTTPException(status_code=403, detail="Personal KB permission denied.")
        rag_user_id = int(user_id_for_acl)
    ai_document_ids = [] if item_type_codes else _ai_document_ids_for_object_ids(doc_ids_list)
    if doc_ids_list and not item_type_codes and not ai_document_ids:
        raise HTTPException(status_code=409, detail="No AI document mapping is available.")
    pass  # debug output removed
    session_id_num = _coerce_int(session_id)
    if not key or not str(key).strip():
        raise HTTPException(status_code=400, detail="key(question)가 필요합니다.")
    headers = _sse_response_headers()
    if QA_STREAM_MOCK:
        return StreamingResponse(
            _mock_rag_sse(
                question=str(key).strip(),
                cat=None if item_type_codes else cat,
                doc_id=None if item_type_codes else doc_id,
                doc_ids=[] if item_type_codes else doc_ids_list,
                item_type_codes=item_type_codes,
                ai_document_ids=ai_document_ids,
                rag_user_id=rag_user_id,
            ),
            media_type="text/event-stream",
            headers=headers,
        )
    rag_user_token = _rag_user_token_from_request(request)
    return StreamingResponse(
        _proxy_rag_sse(
            request,
            question=str(key).strip(),
            cat=None if item_type_codes else cat,
            doc_id=None if item_type_codes else doc_id,
            doc_ids=[] if item_type_codes else doc_ids_list,
            item_type_codes=item_type_codes,
            item_type_code_is_scalar=item_type_code_is_scalar,
            ai_document_ids=ai_document_ids,
            rag_user_id=rag_user_id,
            top_k=top_k,
            mode=mode,
            session_id=session_id_num,
            rag_user_token=rag_user_token,
        ),
        media_type="text/event-stream",
        headers=headers,
    )


async def stream_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    question = (body.get("key") or body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="key 또는 question 필드가 필요합니다.")
    cat = body.get("cat")
    raw_item_type_code = body.get("item_type_code")
    if raw_item_type_code is None:
        raw_item_type_code = body.get("itemTypeCode")
    item_type_codes = _parse_item_type_codes(raw_item_type_code)
    item_type_code_is_scalar = (
        raw_item_type_code is not None
        and not isinstance(raw_item_type_code, (list, tuple, set))
        and len(item_type_codes) == 1
    )
    kb_id = _coerce_int(body.get("kb_id") or body.get("kbId"))
    doc_id = body.get("doc_id")
    if doc_id is None:
        dm = _coerce_int(body.get("doc_master_id"))
        if dm is not None:
            oid = _resolve_object_id_from_doc_master(dm)
            if oid is not None:
                doc_id = str(oid)
            else:
                doc_id = str(dm)
    doc_id_tokens = _parse_doc_id_tokens(doc_id)
    doc_ids_list = _parse_doc_ids(doc_id)
    # backward compatibility: merge old doc_ids field if provided
    for token in _parse_doc_id_tokens(body.get("doc_ids")):
        if token not in doc_id_tokens:
            doc_id_tokens.append(token)
    doc_ids_legacy = _parse_doc_ids(body.get("doc_ids"))
    for did in doc_ids_legacy:
        if did not in doc_ids_list:
            doc_ids_list.append(did)
    if doc_id is not None and not _parse_doc_ids(doc_id):
        try:
            did = int(doc_id)
            if did not in doc_ids_list:
                doc_ids_list.append(did)
        except Exception:
            pass
    source = body.get("source")
    session_id = _coerce_int(body.get("session_id"))
    history = body.get("history")
    if isinstance(history, list):
        cleaned_history = []
        for item in history:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            a = str(item.get("answer") or "").strip()
            if q and a:
                cleaned_history.append({"question": q, "answer": a})
        history = cleaned_history[-2:]
    else:
        history = None
    source_norm = str(source or "").strip().lower()
    bookmark_mode = source_norm == "bookmark"
    user_id_for_acl = _current_user_id_from_request(request)
    requested_user_id = _coerce_int(body.get("user_id") or body.get("userId"))
    rag_user_id: Optional[int] = None
    if bookmark_mode:
        doc_ids_list = _filter_bookmark_doc_ids(
            int(user_id_for_acl),
            doc_ids_list,
            ai_document_ids=doc_id_tokens,
        )
        if not doc_ids_list:
            raise HTTPException(status_code=403, detail="No bookmark documents available.")
        doc_id = ",".join(str(x) for x in doc_ids_list)
    elif source_norm == "personal":
        if requested_user_id is not None and int(requested_user_id) != int(user_id_for_acl):
            raise HTTPException(status_code=403, detail="Personal KB permission denied.")
        rag_user_id = int(user_id_for_acl)
        doc_ids_list = []
        doc_id = None
        cat = None
    elif source_norm == "vault":
        personal_object_ids = _object_ids_for_personal_kb(user_id_for_acl, kb_id)
        if personal_object_ids is not None:
            if not personal_object_ids:
                raise HTTPException(status_code=403, detail="No AI-usable documents are available for this personal KB.")
            rag_user_id = int(user_id_for_acl)
            doc_ids_list = []
            doc_id = None
            item_type_codes = []
            cat = None
    doc_ids_list = _apply_qa_acl(
        user_id_for_acl,
        doc_ids_list,
        cat,
        source=source,
        item_type_codes=item_type_codes,
    )
    if doc_ids_list:
        doc_id = ",".join(str(x) for x in doc_ids_list)
    if (
        rag_user_id is None
        and (doc_ids_list or doc_id_tokens)
        and _object_ids_are_personal_for_user(
            user_id_for_acl,
            doc_ids_list or _object_ids_for_ai_document_ids(doc_id_tokens),
        )
    ):
        if requested_user_id is not None and int(requested_user_id) != int(user_id_for_acl):
            raise HTTPException(status_code=403, detail="Personal KB permission denied.")
        rag_user_id = int(user_id_for_acl)
    ai_document_ids = [] if item_type_codes else _ai_document_ids_for_object_ids(doc_ids_list)
    if doc_ids_list and not item_type_codes and not ai_document_ids:
        raise HTTPException(status_code=409, detail="No AI document mapping is available.")
    top_k = body.get("top_k")
    mode = body.get("mode")

    headers = _sse_response_headers()
    if QA_STREAM_MOCK:
        return StreamingResponse(
            _mock_rag_sse(
                question=question,
                cat=None if item_type_codes else cat,
                doc_id=None if item_type_codes else doc_id,
                doc_ids=[] if item_type_codes else doc_ids_list,
                item_type_codes=item_type_codes,
                ai_document_ids=ai_document_ids,
                rag_user_id=rag_user_id,
            ),
            media_type="text/event-stream",
            headers=headers,
        )
    rag_user_token = _rag_user_token_from_request(request)
    return StreamingResponse(
        _proxy_rag_sse(
            request,
            question=question,
            cat=None if item_type_codes else cat,
            doc_id=None if item_type_codes else doc_id,
            doc_ids=[] if item_type_codes else doc_ids_list,
            item_type_codes=item_type_codes,
            item_type_code_is_scalar=item_type_code_is_scalar,
            ai_document_ids=ai_document_ids,
            rag_user_id=rag_user_id,
            history=history,
            top_k=top_k,
            mode=mode,
            session_id=session_id,
            rag_user_token=rag_user_token,
        ),
        media_type="text/event-stream",
        headers=headers,
    )
