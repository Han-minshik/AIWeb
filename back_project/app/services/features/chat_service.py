from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, validator
from typing import Optional, Literal, Dict, Any, List
from app.deps.auth_dep import get_current_user_id
from app.repositories.common import fetch_all, fetch_one, execute
from app.utils.kiwix_itemtype import resolve_kiwix_itemtype_name
from app.utils.dates import now_str
from datetime import datetime
import json



def _coerce_int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default


def _app_type_to_app_id_static(app_type: Optional[str]) -> Optional[int]:
    if not app_type:
        return None
    u = str(app_type).strip().lower()
    if u == "rag":
        return None
    table = {"req1": 1, "req2": 2, "req3": 3, "req4": 4, "req5": 5, "req8": 8}
    return table.get(u)


def _parse_dt(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            datetime.strptime(s, fmt)
            return s
        except Exception:
            continue
    return s


def _normalize_date_range(start_date: Optional[str], end_date: Optional[str]):
    s = _parse_dt(start_date)
    e = _parse_dt(end_date)
    if s and len(s) == 10:
        s = s + " 00:00:00"
    if e and len(e) == 10:
        e = e + " 23:59:59"
    return s, e


class SessionCreateIn(BaseModel):
    org_id: Optional[int] = None
    app_id: Optional[int] = None
    summary: Optional[str] = None
    title: Optional[str] = None
    app_type: Optional[str] = None

    @validator("summary", always=True)
    def _fill_summary(cls, v, values):
        return v or values.get("title") or "Q&A 세션"

    @validator("org_id", pre=True, always=True)
    def _fill_org_id(cls, v):
        return _coerce_int(v, 1)

    @validator("app_id", always=True)
    def _fill_app_id(cls, v, values):
        if v is not None:
            return v
        mapped = _app_type_to_app_id_static(values.get("app_type"))
        return mapped
    
class SessionFeedbackIn(BaseModel):
    rating: int
    comment: Optional[str] = None

    @validator("rating")
    def _validate_rating(cls, v):
        if v < 1 or v > 5:
            raise ValueError("rating must be between 1 and 5")
        return v

def create_session(data: SessionCreateIn, user_id: int = Depends(get_current_user_id)):
    now = now_str()
    summary = (data.summary or "Q&A 세션")

    if data.app_id is None and data.app_type:
        # Persist selected agent by mapping app_type -> chat_apps.app_id.
        # Use case-insensitive match against both name and display_name.
        norm = str(data.app_type).strip()
        at = norm.lower()
        if at != "rag":
            row_app = fetch_one(
                """
                SELECT app_id, name, display_name
                FROM chat_apps
                WHERE org_id=%s
                  AND (
                    LOWER(name) = LOWER(%s)
                    OR (display_name IS NOT NULL AND LOWER(display_name) = LOWER(%s))
                  )
                LIMIT 1
                """,
                (int(data.org_id), norm, norm),
            )
            if row_app and row_app.get("app_id") is not None:
                data.app_id = row_app["app_id"]

    execute(
        """
        INSERT INTO chat_sessions (org_id, user_id, app_id, first_question_summary, created_at, last_activity_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (data.org_id, user_id, data.app_id, summary, now, now),
    )

    row = fetch_one("SELECT LAST_INSERT_ID() AS session_id")
    sid = (row["session_id"] if row else None)

    if not sid or (isinstance(sid, (str, int)) and int(sid) == 0):
        if data.app_id is None:
            q = """
                SELECT session_id
                FROM chat_sessions
                WHERE org_id = %s
                  AND user_id = %s
                  AND app_id IS NULL
                  AND first_question_summary = %s
                  AND created_at = %s
                ORDER BY session_id DESC
                LIMIT 1
            """
            row2 = fetch_one(q, (int(data.org_id), int(user_id), summary, now))
        else:
            q = """
                SELECT session_id
                FROM chat_sessions
                WHERE org_id = %s
                  AND user_id = %s
                  AND app_id = %s
                  AND first_question_summary = %s
                  AND created_at = %s
                ORDER BY session_id DESC
                LIMIT 1
            """
            row2 = fetch_one(
                q,
                (int(data.org_id), int(user_id), int(data.app_id), summary, now),
            )
        sid = (row2["session_id"] if row2 else None)

    return {
        "session_id": int(sid) if sid is not None else None,
        "org_id": data.org_id,
        "user_id": user_id,
        "app_id": data.app_id,
        "app_type": (
            str(data.app_type).strip()
            if data.app_type
            else ("RAG" if data.app_id is None else None)
        ),
        "first_question_summary": summary,
        "created_at": now,
        "last_activity_at": now,
    }


def list_sessions(user_id: int = Depends(get_current_user_id)) -> List[Dict[str, Any]]:
    return fetch_all(
        """
        SELECT
          s.session_id,
          s.org_id,
          s.user_id,
          s.app_id,
          COALESCE(ca.name, 'RAG') AS app_type,
          s.first_question_summary,
          s.created_at,
          s.last_activity_at,
          s.selected_kb_id AS kb_id,
          s.selected_kb_name AS kb_name,
          s.selected_doc_ids,
          (
            SELECT cm.content
            FROM chat_messages cm
            WHERE cm.session_id = s.session_id AND cm.role = 'USER'
            ORDER BY cm.message_id DESC
            LIMIT 1
          ) AS title,
          (
            SELECT cm.content
            FROM chat_messages cm
            WHERE cm.session_id = s.session_id AND cm.role = 'ASSISTANT'
            ORDER BY cm.message_id DESC
            LIMIT 1
          ) AS last_message
        FROM chat_sessions s
        LEFT JOIN chat_apps ca ON ca.app_id = s.app_id
        WHERE s.user_id=%s
        ORDER BY s.last_activity_at DESC
        """,
        (user_id,),
    )


def get_session(session_id: int, user_id: int = Depends(get_current_user_id)):
    row = fetch_one(
        """
        SELECT s.*,
               COALESCE(ca.name, 'RAG') AS app_type
        FROM chat_sessions s
        LEFT JOIN chat_apps ca ON ca.app_id = s.app_id
        WHERE s.session_id=%s AND s.user_id=%s
        """,
        (session_id, user_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return row

def delete_session(session_id: int, user_id: int = Depends(get_current_user_id)):
    owned = fetch_one(
        "SELECT session_id FROM chat_sessions WHERE session_id=%s AND user_id=%s",
        (session_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")
    execute(
        """
        DELETE f
        FROM chat_feedback f
        JOIN chat_messages m ON m.message_id = f.msg_id
        WHERE m.session_id = %s
        """,
        (session_id,),
    )
    execute(
        "DELETE FROM chat_feedbacks WHERE session_id = %s",
        (session_id,),
    )
    execute("DELETE FROM chat_messages WHERE session_id=%s", (session_id,))
    execute("DELETE FROM chat_sessions WHERE session_id=%s", (session_id,))
    return {"message": "सेशन 삭제 완료"}


class MessageIn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    meta: Optional[Dict[str, Any]] = None

def add_message(
    session_id: int, data: MessageIn, user_id: int = Depends(get_current_user_id)
):
    owned = fetch_one(
        "SELECT session_id FROM chat_sessions WHERE session_id=%s AND user_id=%s",
        (session_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")

    role_upper = data.role.upper()

    meta = data.meta or {}
    sources_in = meta.get("sources") or []
    images_in = meta.get("images") or []
    kb_id = meta.get("kb_id")
    kb_name = meta.get("kb_name")
    selected_doc_ids = meta.get("selected_doc_ids")

    has_image = 1 if (isinstance(images_in, list) and len(images_in) > 0) else 0

    execute(
        """
        INSERT INTO chat_messages (session_id, role, content, has_image, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        """,
        (session_id, role_upper, data.content, has_image),
    )

    row = fetch_one("SELECT LAST_INSERT_ID() AS message_id")
    mid = row["message_id"] if row else None

    if not mid or int(mid) == 0:
        row2 = fetch_one(
            """
            SELECT message_id
            FROM chat_messages
            WHERE session_id=%s AND role=%s
            ORDER BY message_id DESC
            LIMIT 1
            """,
            (session_id, role_upper),
        )
        mid = row2["message_id"] if row2 else None

    def _json(obj: Any) -> Optional[str]:
        try:
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return None

    if mid:
        for src in (sources_in if isinstance(sources_in, list) else []):
            if isinstance(src, dict):
                title = src.get("title")
                url = (
                    src.get("url")
                    or src.get("link")
                    or src.get("href")
                    or src.get("path")
                )
                object_id = (
                    src.get("id") or src.get("object_id") or src.get("doc_id")
                )
                meta_obj = src.get("meta") if isinstance(src.get("meta"), dict) else {}
                doc_name = meta_obj.get("doc_title") or src.get("doc_title")
                doc_path = meta_obj.get("file_url") or src.get("file_url")
                meta_json = _json(src)
            else:
                title, url, object_id, doc_name, doc_path, meta_json = (
                    None,
                    None,
                    None,
                    None,
                    None,
                    _json({"value": src}),
                )
            execute(
                """
                INSERT INTO chat_message_sources
                    (message_id, kind, title, url, object_id, storage_object_id, doc_name, doc_path, meta)
                VALUES (%s, 'CITATION', %s, %s, %s, NULL, %s, %s, %s)
                """,
                (mid, title, url, object_id, doc_name, doc_path, meta_json),
            )

        for img in (images_in if isinstance(images_in, list) else []):
            if isinstance(img, dict):
                url = (
                    img.get("url")
                    or img.get("src")
                    or img.get("href")
                    or img.get("path")
                )
                storage_object_id = img.get("storage_object_id")
                meta_json = _json(img)
            else:
                url, storage_object_id, meta_json = (
                    str(img),
                    None,
                    _json({"url": str(img)}),
                )
            execute(
                """
                INSERT INTO chat_message_sources
                    (message_id, kind, title, url, object_id, storage_object_id, doc_name, doc_path, meta)
                VALUES (%s, 'IMAGE', NULL, %s, NULL, %s, NULL, NULL, %s)
                """,
                (mid, url, storage_object_id, meta_json),
            )

    if kb_id is not None or kb_name is not None or selected_doc_ids is not None:
        execute(
            """
            UPDATE chat_sessions
            SET
              selected_kb_id = COALESCE(%s, selected_kb_id),
              selected_kb_name = COALESCE(%s, selected_kb_name),
              selected_doc_ids = COALESCE(%s, selected_doc_ids),
              last_activity_at = NOW()
            WHERE session_id = %s
            """,
            (
                kb_id,
                kb_name,
                _json(selected_doc_ids)
                if selected_doc_ids is not None
                else None,
                session_id,
            ),
        )
    else:
        execute(
            "UPDATE chat_sessions SET last_activity_at=NOW() WHERE session_id=%s",
            (session_id,),
        )

    return {
        "message_id": mid,
        "session_id": session_id,
        "role": role_upper,
        "content": data.content,
        "meta": meta,
        "created_at": now_str(),
    }


def list_messages(session_id: int, user_id: int = Depends(get_current_user_id)):
    owned = fetch_one(
        "SELECT session_id FROM chat_sessions WHERE session_id=%s AND user_id=%s",
        (session_id, user_id),
    )
    if not owned:
        raise HTTPException(status_code=403, detail="권한 없음")

    rows = fetch_all(
        """
        SELECT message_id, session_id, role, content, created_at
        FROM chat_messages
        WHERE session_id=%s
        ORDER BY message_id ASC
        """,
        (session_id,),
    )

    if not rows:
        return rows

    msg_ids = [r["message_id"] for r in rows]
    placeholders = ",".join(["%s"] * len(msg_ids))
    src_rows = fetch_all(
        f"""
        SELECT source_id, message_id, kind, title, url, object_id, storage_object_id, meta
        FROM chat_message_sources
        WHERE message_id IN ({placeholders})
        ORDER BY source_id ASC
        """,
        tuple(msg_ids),
    )

    mapping: Dict[int, Dict[str, List[Any]]] = {}
    for r in msg_ids:
        mapping[int(r)] = {"sources": [], "images": []}

    doc_id_exists_cache: Dict[int, bool] = {}
    storage_to_doc_cache: Dict[int, Optional[int]] = {}
    node_to_path_cache: Dict[int, str] = {}
    doc_to_paths_cache: Dict[int, List[str]] = {}
    doc_meta_cache: Dict[int, Optional[Dict[str, Any]]] = {}
    object_meta_cache: Dict[int, Optional[Dict[str, Any]]] = {}

    def _normalize_ext(v: Any) -> Optional[str]:
        s = str(v or "").strip().lstrip(".").lower()
        return s or None

    def _build_display_name(name: Any, file_right: Any) -> str:
        base = str(name or "").strip()
        ext = str(file_right or "").strip().lstrip(".")
        if not base:
            return ""
        if not ext:
            return base
        if base.lower().endswith("." + ext.lower()):
            return base
        return f"{base}.{ext}"

    def _join_rel_sub(rel: Any, sub: Any, filename: Any) -> Optional[str]:
        rel_s = str(rel or "").strip().replace("\\", "/").strip("/")
        sub_s = str(sub or "").strip().replace("\\", "/").strip("/")
        file_s = str(filename or "").strip().replace("\\", "/").strip("/")
        parts = [p for p in (rel_s, sub_s, file_s) if p]
        return "/".join(parts) if parts else None

    def _meta_from_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        object_id = _coerce_int(row.get("object_id"))
        if object_id is None:
            return None
        doc_code = str(row.get("doc_code") or "").strip() or None
        file_right = _normalize_ext(row.get("file_right"))
        rel = str(row.get("relative_path") or "").strip().replace("\\", "/").strip("/") or None
        sub = str(row.get("sub_path") or "").strip().replace("\\", "/").strip("/") or None
        branch_type = str(row.get("branch_type") or "GENERAL").strip().upper() or "GENERAL"
        doc_title = _build_display_name(row.get("name"), file_right)
        stored_path = _join_rel_sub(rel, sub, doc_title)
        itemtype_name: Optional[str] = None
        if branch_type == "KIWIX":
            itemtype_name = resolve_kiwix_itemtype_name(
                doc_code,
                sub,
                row.get("name"),
                file_right,
            )
        return {
            "object_id": object_id,
            "storage_object_id": object_id,
            "doc_code": doc_code,
            "doc_title": doc_title or None,
            "file_right": file_right,
            "relative_path": rel,
            "sub_path": sub,
            "stored_path": stored_path,
            "branch_type": branch_type,
            "itemtypeName": itemtype_name,
        }

    def _get_doc_meta(doc_master_id: int) -> Optional[Dict[str, Any]]:
        dm = _coerce_int(doc_master_id)
        if dm is None:
            return None
        if dm in doc_meta_cache:
            return doc_meta_cache[dm]
        row = fetch_one(
            """
            SELECT
                r.storage_object_id AS object_id,
                so.doc_code,
                so.name,
                so.file_right,
                so.relative_path,
                so.sub_path,
                (
                    SELECT
                        CASE
                            WHEN MAX(
                                CASE
                                    WHEN UPPER(COALESCE(n.branch_type, 'GENERAL'))='KIWIX' THEN 1
                                    ELSE 0
                                END
                            ) = 1 THEN 'KIWIX'
                            ELSE 'GENERAL'
                        END
                    FROM pf_node_props p2
                    JOIN tree_nodes n ON n.node_id = p2.node_id
                    WHERE p2.doc_master_id = r.doc_master_id
                ) AS branch_type
            FROM doc_revisions r
            JOIN storage_objects so ON so.object_id = r.storage_object_id
            WHERE r.doc_master_id=%s AND r.deleted_at IS NULL
            ORDER BY r.rev_no DESC
            LIMIT 1
            """,
            (dm,),
        )
        meta_row = _meta_from_row(row)
        doc_meta_cache[dm] = meta_row
        if isinstance(meta_row, dict):
            oid = _coerce_int(meta_row.get("object_id"))
            if oid is not None:
                object_meta_cache[oid] = meta_row
        return meta_row

    def _get_object_meta(object_id: Any) -> Optional[Dict[str, Any]]:
        oid = _coerce_int(object_id)
        if oid is None:
            return None
        if oid in object_meta_cache:
            return object_meta_cache[oid]
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
                            WHEN UPPER(COALESCE(n.branch_type, 'GENERAL'))='KIWIX' THEN 1
                            ELSE 0
                        END
                    ) = 1 THEN 'KIWIX'
                    ELSE 'GENERAL'
                END AS branch_type
            FROM storage_objects so
            LEFT JOIN doc_revisions r
                   ON r.storage_object_id = so.object_id
                  AND r.deleted_at IS NULL
            LEFT JOIN pf_node_props p
                   ON p.doc_master_id = r.doc_master_id
            LEFT JOIN tree_nodes n
                   ON n.node_id = p.node_id
            WHERE so.object_id=%s
            GROUP BY so.object_id, so.doc_code, so.name, so.file_right, so.relative_path, so.sub_path
            LIMIT 1
            """,
            (oid,),
        )
        meta_row = _meta_from_row(row)
        object_meta_cache[oid] = meta_row
        return meta_row

    def _resolve_doc_master_id(src_row: Dict[str, Any], meta_obj: Dict[str, Any]) -> Optional[int]:
        candidates: List[Any] = []
        if isinstance(meta_obj, dict):
            candidates.extend(
                [
                    meta_obj.get("doc_master_id"),
                    meta_obj.get("docMasterId"),
                    meta_obj.get("doc_id"),
                    meta_obj.get("docId"),
                    meta_obj.get("id"),
                    meta_obj.get("object_id"),
                    meta_obj.get("objectId"),
                    meta_obj.get("storage_object_id"),
                    meta_obj.get("storageObjectId"),
                ]
            )
        candidates.extend(
            [
                src_row.get("object_id"),
                src_row.get("storage_object_id"),
            ]
        )

        for raw in candidates:
            n = _coerce_int(raw)
            if n is None:
                continue

            exists = doc_id_exists_cache.get(n)
            if exists is None:
                row_exists = fetch_one(
                    "SELECT doc_master_id FROM doc_master WHERE doc_master_id=%s LIMIT 1",
                    (n,),
                )
                exists = bool(row_exists)
                doc_id_exists_cache[n] = exists
            if exists:
                return n

            if n not in storage_to_doc_cache:
                row_doc = fetch_one(
                    """
                    SELECT doc_master_id
                    FROM doc_revisions
                    WHERE storage_object_id=%s AND deleted_at IS NULL
                    ORDER BY rev_no DESC
                    LIMIT 1
                    """,
                    (n,),
                )
                if row_doc and row_doc.get("doc_master_id") is not None:
                    storage_to_doc_cache[n] = int(row_doc["doc_master_id"])
                else:
                    storage_to_doc_cache[n] = None
            if storage_to_doc_cache.get(n) is not None:
                return storage_to_doc_cache[n]
        return None

    def _node_full_path(node_id: int) -> str:
        cached = node_to_path_cache.get(node_id)
        if cached is not None:
            return cached

        parts: List[str] = []
        cur = int(node_id)
        safety = 0
        while cur and safety < 256:
            safety += 1
            row_node = fetch_one(
                "SELECT node_id, parent_id, name FROM tree_nodes WHERE node_id=%s",
                (cur,),
            )
            if not row_node:
                break
            name = str(row_node.get("name") or "").strip()
            if name:
                parts.append(name)
            cur = _coerce_int(row_node.get("parent_id"), 0)
        parts.reverse()
        out = "/".join(parts)
        node_to_path_cache[node_id] = out
        return out

    def _collect_doc_paths(doc_master_id: int) -> List[str]:
        cached = doc_to_paths_cache.get(doc_master_id)
        if cached is not None:
            return cached

        node_rows = fetch_all(
            "SELECT node_id FROM pf_node_props WHERE doc_master_id=%s ORDER BY node_id ASC",
            (doc_master_id,),
        )
        out: List[str] = []
        seen: set[str] = set()
        for nr in (node_rows or []):
            nid = _coerce_int((nr or {}).get("node_id"))
            if nid is None:
                continue
            p = _node_full_path(int(nid))
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        doc_to_paths_cache[doc_master_id] = out
        return out

    for s in (src_rows or []):
        mid = int(s["message_id"])
        kind = s["kind"]
        try:
            meta_obj = json.loads(s["meta"]) if s.get("meta") else None
        except Exception:
            meta_obj = None

        meta_out = dict(meta_obj) if isinstance(meta_obj, dict) else None
        if kind == "CITATION":
            resolved_doc_id = _resolve_doc_master_id(
                s,
                meta_out if isinstance(meta_out, dict) else {},
            )
            if resolved_doc_id is not None:
                linked_paths = _collect_doc_paths(int(resolved_doc_id))
                if isinstance(meta_out, dict):
                    meta_out.setdefault("doc_master_id", int(resolved_doc_id))
                    if linked_paths:
                        meta_out["linked_paths"] = linked_paths
                else:
                    meta_out = {"doc_master_id": int(resolved_doc_id)}
                    if linked_paths:
                        meta_out["linked_paths"] = linked_paths

            source_hint = _get_doc_meta(int(resolved_doc_id)) if resolved_doc_id is not None else None
            if source_hint is None:
                raw_source_oid = _coerce_int(
                    s.get("object_id")
                    or s.get("storage_object_id")
                )
                if raw_source_oid is None and isinstance(meta_out, dict):
                    raw_source_oid = _coerce_int(
                        meta_out.get("object_id")
                        or meta_out.get("storage_object_id")
                        or meta_out.get("id")
                    )
                if raw_source_oid is not None:
                    source_hint = _get_object_meta(int(raw_source_oid))

            if source_hint:
                if not isinstance(meta_out, dict):
                    meta_out = {}
                for k, v in source_hint.items():
                    if v is not None:
                        meta_out[k] = v

        meta_title = None
        if isinstance(meta_out, dict):
            meta_title = (
                meta_out.get("title")
                or meta_out.get("name")
                or meta_out.get("doc_title")
            )

        if kind == "CITATION":
            source_id = s.get("object_id")
            if source_id is None and isinstance(meta_out, dict):
                source_id = (
                    meta_out.get("object_id")
                    or meta_out.get("storage_object_id")
                    or meta_out.get("id")
                )
            mapping[mid]["sources"].append(
                {
                    "title": s.get("title") or meta_title,
                    "url": s.get("url"),
                    "id": source_id,
                    "meta": meta_out,
                }
            )
        elif kind == "IMAGE":
            mapping[mid]["images"].append(
                {
                    "url": s.get("url"),
                    "storage_object_id": s.get("storage_object_id"),
                    "meta": meta_out,
                }
            )

    fb_rows = fetch_all(
        f"""
        SELECT f.msg_id, f.like_count, f.dislike_count, f.comment
        FROM chat_feedback f
        WHERE f.msg_id IN ({placeholders}) AND f.user_id = %s
        """,
        tuple(msg_ids + [user_id]),
    )
    latest_feedback: Dict[int, Dict[str, Any]] = {}
    for fb in (fb_rows or []):
        mid = int(fb["msg_id"])
        like_cnt = int(fb.get("like_count") or 0)
        dislike_cnt = int(fb.get("dislike_count") or 0)
        has_any = (like_cnt > 0) or (dislike_cnt > 0) or (
            fb.get("comment") not in (None, "")
        )
        if not has_any:
            continue
        score = 1 if like_cnt > 0 else (-1 if dislike_cnt > 0 else 0)
        latest_feedback[mid] = {
            "score": score,
            "comment": fb.get("comment"),
        }

    for r in rows:
        mid = int(r["message_id"])
        bundle = mapping.get(mid, {"sources": [], "images": []})
        r["meta"] = {
            "sources": bundle["sources"],
            "images": bundle["images"],
        }
        if mid in latest_feedback:
            r["feedback"] = latest_feedback[mid]
        else:
            r["feedback"] = None

    return rows


class FeedbackIn(BaseModel):
    chat_message_id: int
    feedback_type: Literal["like", "unlike"]
    comment: Optional[str] = None


def add_feedback(data: FeedbackIn, user_id: int = Depends(get_current_user_id)):
    row = fetch_one(
        """
        SELECT cm.session_id
        FROM chat_messages cm
        JOIN chat_sessions cs ON cs.session_id = cm.session_id
        WHERE cm.message_id=%s AND cs.user_id=%s
        """,
        (data.chat_message_id, user_id),
    )
    if not row:
        raise HTTPException(
            status_code=403, detail="권한 없음 또는 메시지를 찾을 수 없습니다."
        )

    like_cnt = 1 if data.feedback_type == "like" else 0
    dislike_cnt = 1 if data.feedback_type == "unlike" else 0

    execute(
        """
        INSERT INTO chat_feedback (msg_id, user_id, like_count, dislike_count, comment)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          like_count=VALUES(like_count),
          dislike_count=VALUES(dislike_count),
          comment=VALUES(comment)
        """,
        (data.chat_message_id, user_id, like_cnt, dislike_cnt, data.comment or None),
    )
    return {"ok": True}


def list_feedback(
    start_date: Optional[str] = Query(
        default=None, description="시작 날짜 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)"
    ),
    end_date: Optional[str] = Query(
        default=None, description="끝 날짜 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)"
    ),
    app_type: Optional[str] = Query(
        default=None, description="앱 타입(req1, req2, req3, req4, req5, req8, rag)"
    ),
    user_id: int = Depends(get_current_user_id),
):
    start_dt, end_dt = _normalize_date_range(start_date, end_date)
    conditions = ["1=1"]
    params: List[Any] = []

    if start_dt:
        conditions.append("f.created_at >= %s")
        params.append(start_dt)
    if end_dt:
        conditions.append("f.created_at <= %s")
        params.append(end_dt)

    if app_type:
        at = str(app_type).strip().lower()
        if at == "rag":
            conditions.append("s.app_id IS NULL")
        else:
            mapped = _app_type_to_app_id_static(at)
            if mapped is not None:
                conditions.append("s.app_id = %s")
                params.append(mapped)

    where_sql = " AND ".join(conditions)

    q = f"""
        SELECT
          CONCAT(f.msg_id, '-', f.user_id) AS feedback_id,
          m.session_id,
          f.user_id,
          CASE
            WHEN f.like_count > 0 AND f.dislike_count = 0 THEN 5
            WHEN f.dislike_count > 0 AND f.like_count = 0 THEN 1
            ELSE 3
          END AS rating,
          f.comment,
          f.created_at,
          s.org_id,
          s.app_id,
          COALESCE(ca.name, 'RAG') AS app_type,
          s.first_question_summary AS session_summary
        FROM chat_feedback f
        JOIN chat_messages m ON m.message_id = f.msg_id
        JOIN chat_sessions s ON s.session_id = m.session_id
        LEFT JOIN chat_apps ca ON ca.app_id = s.app_id
        WHERE {where_sql}
        ORDER BY f.created_at DESC, feedback_id DESC
    """
    rows = fetch_all(q, tuple(params))
    return rows

def feedback_stats(
    start_date: Optional[str] = Query(
        default=None, description="시작 날짜 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)"
    ),
    end_date: Optional[str] = Query(
        default=None, description="끝 날짜 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)"
    ),
    group_by: Literal["day", "week", "month"] = Query(
        default="month", description="집계 단위: day, week, month"
    ),
    app_type: Optional[str] = Query(
        default=None, description="앱 타입(req1, req2, req3, req4, req5, req8, rag)"
    ),
    user_id: int = Depends(get_current_user_id),
):
    start_dt, end_dt = _normalize_date_range(start_date, end_date)
    if not start_dt or not end_dt:
        raise HTTPException(status_code=400, detail="start_date와 end_date가 필요합니다.")

    if group_by == "day":
        label_expr = "DATE_FORMAT(f.created_at, '%%Y-%%m-%%d')"
    elif group_by == "week":
        label_expr = (
            "DATE_FORMAT(DATE_SUB(f.created_at, INTERVAL (WEEKDAY(f.created_at)) DAY),"
            " '%%Y-%%m-%%d')"
        )
    else:
        label_expr = "DATE_FORMAT(f.created_at, '%%Y-%%m-01')"

    conditions = ["f.created_at >= %s", "f.created_at <= %s"]
    params: List[Any] = [start_dt, end_dt]

    if app_type:
        at = str(app_type).strip().lower()
        if at == "rag":
            conditions.append("s.app_id IS NULL")
        else:
            mapped = _app_type_to_app_id_static(at)
            if mapped is not None:
                conditions.append("s.app_id = %s")
                params.append(mapped)

    where_sql = " AND ".join(conditions)

    q = f"""
        SELECT
          {label_expr} AS label,
          SUM(CASE WHEN f.like_count > 0 AND f.dislike_count = 0 THEN 1 ELSE 0 END) AS up,
          SUM(CASE WHEN f.dislike_count > 0 AND f.like_count = 0 THEN 1 ELSE 0 END) AS down,
          COUNT(*) AS total
        FROM chat_feedback f
        JOIN chat_messages m ON m.message_id = f.msg_id
        JOIN chat_sessions s ON s.session_id = m.session_id
        WHERE {where_sql}
        GROUP BY label
        ORDER BY label ASC
    """
    rows = fetch_all(q, tuple(params))
    return rows

def search_messages(
    q: Optional[str] = Query(
        default=None, description="메시지 본문 검색 키워드(부분일치)"
    ),
    session_id: Optional[int] = Query(
        default=None, description="특정 세션으로 필터링"
    ),
    role: Optional[Literal["user", "assistant", "system"]] = Query(
        default=None, description="메시지 역할"
    ),
    app_type: Optional[str] = Query(
        default=None, description="앱 타입(req1, req2, req3, req4, req5, req8, rag)"
    ),
    app_id: Optional[int] = Query(default=None, description="특정 앱 ID로 필터링"),
    after: Optional[str] = Query(
        default=None,
        description="이 날짜/시각 이후 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)",
    ),
    before: Optional[str] = Query(
        default=None,
        description="이 날짜/시각 이전 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)",
    ),
    order: Optional[Literal["asc", "desc"]] = Query(
        default="desc", description="메시지 시간 정렬"
    ),
    limit: int = Query(default=50, ge=1, le=200, description="페이지 크기(1~200)"),
    offset: int = Query(default=0, ge=0, description="페이지 시작 위치(0-base)"),
    user_id: int = Depends(get_current_user_id),
):
    resolved_app_id = app_id
    if resolved_app_id is None and app_type:
        mapped = _app_type_to_app_id_static(app_type)
        if mapped is not None:
            resolved_app_id = mapped

    conditions = ["s.user_id = %s"]
    params = [user_id]

    if q:
        conditions.append("m.content LIKE %s")
        params.append(f"%{q}%")

    if session_id:
        conditions.append("m.session_id = %s")
        params.append(int(session_id))

    if role:
        conditions.append("m.role = %s")
        params.append(role.upper())

    if app_type and str(app_type).strip().lower() == "rag":
        conditions.append("s.app_id IS NULL")
    elif resolved_app_id is not None:
        conditions.append("s.app_id = %s")
        params.append(int(resolved_app_id))

    aft = _parse_dt(after)
    if aft:
        conditions.append("m.created_at >= %s")
        params.append(aft)
    bef = _parse_dt(before)
    if bef:
        conditions.append("m.created_at <= %s")
        params.append(bef)

    where_sql = " AND ".join(conditions) if conditions else "1=1"
    order_sql = "ASC" if (order or "desc").lower() == "asc" else "DESC"

    count_sql = f"""
        SELECT COUNT(*) AS cnt
        FROM chat_messages m
        JOIN chat_sessions s ON s.session_id = m.session_id
        LEFT JOIN chat_apps ca ON ca.app_id = s.app_id
        WHERE {where_sql}
    """
    row_cnt = fetch_one(count_sql, tuple(params))
    total = int(row_cnt["cnt"]) if row_cnt and "cnt" in row_cnt else 0

    data_sql = f"""
        SELECT
            m.message_id,
            m.session_id,
            m.role,
            m.content,
            m.created_at,
            s.org_id,
            s.user_id,
            s.app_id,
            COALESCE(ca.name, 'RAG') AS app_type,
            s.first_question_summary AS session_summary
        FROM chat_messages m
        JOIN chat_sessions s ON s.session_id = m.session_id
        LEFT JOIN chat_apps ca ON ca.app_id = s.app_id
        WHERE {where_sql}
        ORDER BY m.created_at {order_sql}, m.message_id {order_sql}
        LIMIT %s OFFSET %s
    """
    rows = fetch_all(data_sql, tuple(params + [limit, offset]))

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": rows,
    }

def create_session_feedback(
    session_id: int,
    data: SessionFeedbackIn,
    user_id: int = Depends(get_current_user_id)
):
    row = fetch_one(
        """
        SELECT session_id
        FROM chat_sessions
        WHERE session_id = %s AND user_id = %s
        """,
        (session_id, user_id),
    )
    if not row:
        raise HTTPException(status_code=403, detail="권한 없음 또는 세션을 찾을 수 없습니다.")

    execute(
        """
        INSERT INTO chat_feedbacks (session_id, user_id, rating, comment, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
          rating = VALUES(rating),
          comment = VALUES(comment)
        """,
        (session_id, user_id, data.rating, data.comment),
    )

    return {"ok": True, "session_id": session_id}
