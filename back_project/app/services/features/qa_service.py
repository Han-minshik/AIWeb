import os
import json
import asyncio
import contextlib
import re
from typing import Optional, Any, Dict, Tuple, List, Set, Union, AsyncGenerator
from urllib.parse import quote, urlsplit, urlunsplit

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
from app.utils.kiwix_itemtype import resolve_kiwix_itemtype_name


QA_STREAM_MOCK = os.getenv("QA_STREAM_MOCK", "").strip().lower() in ("1", "true", "yes")
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





def _coerce_int(x: Any) -> Optional[int]:
    return coerce_int(x, None)


def _resolve_object_id_from_doc_master(doc_master_id: int) -> Optional[int]:
    row = fetch_one(
        """
        SELECT r.storage_object_id
          FROM doc_revisions r
         WHERE r.doc_master_id=%s AND r.deleted_at IS NULL
         ORDER BY r.rev_no DESC
         LIMIT 1
        """,
        (doc_master_id,),
    )
    if not row or row.get("storage_object_id") is None:
        return None
    try:
        return int(row["storage_object_id"])
    except Exception:
        return None

def _filter_bookmark_doc_ids(user_id: int, doc_ids: List[int]) -> List[int]:
    if not doc_ids:
        return []
    ids = [int(x) for x in doc_ids if x is not None]
    if not ids:
        return []

    placeholders = ",".join(["%s"] * len(ids))

    rows_dm = fetch_all(
        f"""
        SELECT r.storage_object_id AS object_id
          FROM bookmarks b
          JOIN doc_revisions r
            ON r.revision_id = (
              SELECT r2.revision_id
                FROM doc_revisions r2
               WHERE r2.doc_master_id = b.doc_master_id AND r2.deleted_at IS NULL
               ORDER BY r2.rev_no DESC
               LIMIT 1
            )
         WHERE b.user_id=%s AND b.doc_master_id IN ({placeholders})
        """,
        tuple([user_id] + ids),
    )

    rows_obj = fetch_all(
        f"""
        SELECT DISTINCT r.storage_object_id AS object_id
          FROM doc_revisions r
          JOIN bookmarks b ON b.user_id=%s AND b.doc_master_id = r.doc_master_id
         WHERE r.storage_object_id IN ({placeholders})
        """,
        tuple([user_id] + ids),
    )

    rows_so = fetch_all(
        f"""
        SELECT DISTINCT b.target_pk AS object_id
          FROM bookmarks b
         WHERE b.user_id=%s AND b.target_type='STORAGE_OBJECT' AND b.target_pk IN ({placeholders})
        """,
        tuple([user_id] + ids),
    )

    out = []
    for rows in (rows_dm, rows_obj, rows_so):
        for r in rows:
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
    return _coerce_int(raw)


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
    itemtype_name: Optional[str] = None
    if branch == "KIWIX":
        itemtype_name = resolve_kiwix_itemtype_name(
            doc_code,
            sub,
            row.get("name"),
            file_right,
        )
    return {
        "object_id": object_id,
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
            so.doc_code,
            so.name,
            so.file_right,
            so.relative_path,
            so.sub_path,
            CASE
                WHEN MAX(
                    CASE
                        WHEN UPPER(COALESCE(tn.branch_type, 'GENERAL')) = 'KIWIX' THEN 1
                        ELSE 0
                    END
                ) = 1 THEN 'KIWIX'
                ELSE 'GENERAL'
            END AS branch_type
        FROM storage_objects so
        LEFT JOIN doc_revisions dr
               ON dr.storage_object_id = so.object_id
              AND dr.deleted_at IS NULL
        LEFT JOIN pf_node_props p
               ON p.doc_master_id = dr.doc_master_id
        LEFT JOIN tree_nodes tn
               ON tn.node_id = p.node_id
        WHERE so.object_id=%s
        GROUP BY so.object_id, so.doc_code, so.name, so.file_right, so.relative_path, so.sub_path
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
                so.doc_code,
                so.name,
                so.file_right,
                so.relative_path,
                so.sub_path,
                UPPER(COALESCE(tn.branch_type, 'GENERAL')) AS branch_type
            FROM tree_nodes tn
            JOIN pf_node_props p ON p.node_id = tn.node_id
            JOIN doc_revisions dr
              ON dr.doc_master_id = p.doc_master_id
             AND dr.deleted_at IS NULL
            JOIN storage_objects so ON so.object_id = dr.storage_object_id
            WHERE tn.is_folder = 0
              AND (
                    LOWER(tn.name) = LOWER(%s)
                 OR LOWER(so.name) = LOWER(%s)
                 OR LOWER(
                        CONCAT(
                            so.name,
                            CASE
                                WHEN COALESCE(so.file_right, '') = '' THEN ''
                                ELSE CONCAT('.', so.file_right)
                            END
                        )
                    ) = LOWER(%s)
                  )
            ORDER BY
                CASE
                    WHEN UPPER(COALESCE(tn.branch_type, 'GENERAL')) = 'KIWIX' THEN 0
                    ELSE 1
                END,
                dr.rev_no DESC,
                so.object_id DESC
            LIMIT 1
            """,
            (candidate, candidate, candidate),
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
                so.name,
                so.file_right,
                so.relative_path,
                so.sub_path,
                UPPER(COALESCE(tn.branch_type, 'GENERAL')) AS branch_type
            FROM tree_nodes tn
            JOIN pf_node_props p ON p.node_id = tn.node_id
            JOIN doc_revisions dr
              ON dr.doc_master_id = p.doc_master_id
             AND dr.deleted_at IS NULL
            JOIN storage_objects so ON so.object_id = dr.storage_object_id
            WHERE tn.is_folder = 0
              AND (
                    LOWER(tn.name) LIKE LOWER(CONCAT(%s, '.%%'))
                 OR LOWER(so.name) = LOWER(%s)
                  )
            ORDER BY
                CASE
                    WHEN UPPER(COALESCE(tn.branch_type, 'GENERAL')) = 'KIWIX' THEN 0
                    ELSE 1
                END,
                dr.rev_no DESC,
                so.object_id DESC
            LIMIT 1
            """,
            (base, base),
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
    return out


def _enrich_sources_with_db(
    srcs: List[dict], cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None
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

        out.append(_apply_source_hint(src, hint) if hint else src)
    return out


async def _proxy_rag_sse(
    request: Request,
    question: str,
    cat: Optional[str] = None,
    doc_id: Optional[str] = None,
    doc_ids: Optional[List[int]] = None,
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

    yield "event: meta\ndata: " + json.dumps({"status": "streaming"}, ensure_ascii=False) + "\n\n"

    headers = {"Accept": "text/event-stream"}
    if RAG_SSE_TOKEN:
        headers["Authorization"] = f"Bearer {RAG_SSE_TOKEN}"
    if RAG_SSE_EXTRA_HEADERS:
        headers.update(RAG_SSE_EXTRA_HEADERS)

    params = {"question": question}
    if session_id is not None:
        params["session_id"] = str(int(session_id))
    if history:
        params["history"] = history
    if cat:
        if isinstance(cat, (list, tuple, set)):
            cat_list = [str(x).strip() for x in cat if str(x).strip()]
        else:
            cat_list = [str(cat).strip()] if str(cat).strip() else []
        if cat_list:
            params["cat"] = cat_list
    if doc_ids:
        doc_id_list = [str(x).strip() for x in doc_ids if x is not None and str(x).strip()]
        if doc_id_list:
            params["doc_id"] = doc_id_list
    elif doc_id is not None:
        parsed_doc_ids = _parse_doc_ids(doc_id)
        if parsed_doc_ids:
            params["doc_id"] = [str(x) for x in parsed_doc_ids]
        elif str(doc_id).strip():
            params["doc_id"] = [str(doc_id).strip()]
    if top_k is not None:
        params["top_k"] = str(top_k)

    print(params)

    timeout = httpx.Timeout(RAG_SSE_TIMEOUT)

    req = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream_url = _resolve_upstream_url(mode)
            print(f"[Step 2] 업스트림 호출 URL = {upstream_url}")
            print(f"[Step 3] 요청 파라미터 = {params}")

            def _mask_headers(h: Dict[str, str]) -> Dict[str, str]:
                return {k: ('***' if k.lower() == 'authorization' else v) for k, v in h.items()}

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
            print(f"[Step 3] 최종 URL(쿼리 포함) = {str(req.url)}")
            try:
                print(f"[Step 3] 요청 바디(JSON) = {params}")
            except Exception:
                pass
            print(f"[Step 3] 요청 헤더(민감정보 마스킹) = {_mask_headers(dict(req.headers))}")

            r = await client.send(req, stream=True)
            try:
                if r.status_code >= 400:
                    print(f"[Step 3][ERROR] 업스트림 상태코드 = {r.status_code}")
                    print(f"[Step 3][ERROR] 업스트림 요청 URL = {str(r.request.url)}")
                    try:
                        print(f"[Step 3][ERROR] 업스트림 요청 쿼리 = {dict(r.request.url.params)}")
                    except Exception:
                        pass
                    try:
                        err_body = (await r.aread()).decode("utf-8", errors="replace")
                        print(f"[Step 3][ERROR] 업스트림 오류 본문 = {err_body[:1000]}")
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
            print(f"[Step 3][EXC] 예외 발생: {repr(e)}")
            print(f"[Step 3][EXC] 요청 URL = {str(req.url)}")
            try:
                print(f"[Step 3][EXC] 요청 쿼리 = {dict(req.url.params)}")
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
) -> AsyncGenerator[str, None]:
    yield "event: meta\ndata: " + json.dumps({"status": "streaming", "mock": True}, ensure_ascii=False) + "\n\n"
    await asyncio.sleep(0.05)
    yield "event: step\ndata: " + json.dumps({"progress": 2}, ensure_ascii=False) + "\n\n"
    await asyncio.sleep(0.05)
    prefix = "[MOCK] "
    ctx = {"cat": cat, "doc_id": doc_id, "doc_ids": doc_ids or []}
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
    doc_id: Optional[str] = Query(default=None),
    doc_master_id: Optional[str] = Query(default=None),
    doc_ids: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    top_k: Optional[int] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
):
    print("[Step 1] /qa/stream 진입 URL:", str(request.url))
    if doc_id is None and doc_master_id is not None:
        dm = _coerce_int(doc_master_id)
        if dm is not None:
            oid = _resolve_object_id_from_doc_master(dm)
            if oid is not None:
                doc_id = str(oid)
            else:
                doc_id = str(dm)
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
    bookmark_mode = source_norm == "bookmark"
    if bookmark_mode:
        user = get_current_user(request)
        user_id = user.get("user_id") or user.get("id")
        doc_ids_list = _filter_bookmark_doc_ids(int(user_id), doc_ids_list)
        if not doc_ids_list:
            raise HTTPException(status_code=403, detail="No bookmark documents available.")
        doc_id = ",".join(str(x) for x in doc_ids_list)
    print(doc_id)
    session_id_num = _coerce_int(session_id)
    if not key or not str(key).strip():
        raise HTTPException(status_code=400, detail="key(question)가 필요합니다.")
    headers = _sse_response_headers()
    if QA_STREAM_MOCK:
        return StreamingResponse(
            _mock_rag_sse(question=str(key).strip(), cat=cat, doc_id=doc_id, doc_ids=doc_ids_list),
            media_type="text/event-stream",
            headers=headers,
        )
    return StreamingResponse(
        _proxy_rag_sse(
            request,
            question=str(key).strip(),
            cat=cat,
            doc_id=doc_id,
            doc_ids=doc_ids_list,
            top_k=top_k,
            mode=mode,
            session_id=session_id_num,
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
    doc_id = body.get("doc_id")
    if doc_id is None:
        dm = _coerce_int(body.get("doc_master_id"))
        if dm is not None:
            oid = _resolve_object_id_from_doc_master(dm)
            if oid is not None:
                doc_id = str(oid)
            else:
                doc_id = str(dm)
    doc_ids_list = _parse_doc_ids(doc_id)
    # backward compatibility: merge old doc_ids field if provided
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
    if bookmark_mode:
        user = get_current_user(request)
        user_id = user.get("user_id") or user.get("id")
        doc_ids_list = _filter_bookmark_doc_ids(int(user_id), doc_ids_list)
        if not doc_ids_list:
            raise HTTPException(status_code=403, detail="No bookmark documents available.")
        doc_id = ",".join(str(x) for x in doc_ids_list)
    top_k = body.get("top_k")
    mode = body.get("mode")

    headers = _sse_response_headers()
    if QA_STREAM_MOCK:
        return StreamingResponse(
            _mock_rag_sse(question=question, cat=cat, doc_id=doc_id, doc_ids=doc_ids_list),
            media_type="text/event-stream",
            headers=headers,
        )
    return StreamingResponse(
        _proxy_rag_sse(
            request,
            question=question,
            cat=cat,
            doc_id=doc_id,
            doc_ids=doc_ids_list,
            history=history,
            top_k=top_k,
            mode=mode,
            session_id=session_id,
        ),
        media_type="text/event-stream",
        headers=headers,
    )
