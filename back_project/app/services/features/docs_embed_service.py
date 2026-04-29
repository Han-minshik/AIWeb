from __future__ import annotations

import os
import json
import logging
import mimetypes
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import httpx
from fastapi import HTTPException, Request, Response, status, Path, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit, urlunsplit, parse_qsl
from jose import jwt, JWTError, ExpiredSignatureError

from app.utils.db import fetch_one
from app.core.config import UPLOAD_DIR as CONFIG_UPLOAD_DIR
from app.core.auth import get_current_user as core_get_current_user

JWT_SECRET = os.getenv("EMBEDPDF_JWT_SECRET", "change-this-in-prod")
JWT_ALG = "HS256"
URL_TTL_MINUTES = int(os.getenv("EMBEDPDF_URL_TTL_MINUTES", "20"))
SYNAP_BASE_URL = os.getenv("SYNAP_BASE_URL", "http://localhost:8080/SynapDocViewServer").rstrip("/")
DOCS_EMBED_PUBLIC_BASE_URL = os.getenv("DOCS_EMBED_PUBLIC_BASE_URL", "http://localhost:8001").rstrip("/")
DOCS_EMBED_PREFIX = "/docs-embed"
SYNAP_JOB_RESOLVE_TIMEOUT_SEC = int(os.getenv("SYNAP_JOB_RESOLVE_TIMEOUT_SEC", "90"))
SYNAP_WATERMARK_ENABLED = str(os.getenv("SYNAP_WATERMARK_ENABLED", "true")).strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SYNAP_WATERMARK_TEXT = str(os.getenv("SYNAP_WATERMARK_TEXT", "\ub808\ub4dc\uc2a4\ud53c\uc5b4"))
SYNAP_WATERMARK_COMPANY = str(
    os.getenv("SYNAP_WATERMARK_COMPANY", SYNAP_WATERMARK_TEXT)
)
SYNAP_WATERMARK_INCLUDE_OBJECT_ID = str(
    os.getenv("SYNAP_WATERMARK_INCLUDE_OBJECT_ID", "true")
).strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SYNAP_WATERMARK_COLOR = str(os.getenv("SYNAP_WATERMARK_COLOR", "C62828")).strip().lstrip("#")
SYNAP_WATERMARK_ALPHA = int(os.getenv("SYNAP_WATERMARK_ALPHA", "75"))
SYNAP_WATERMARK_ROTATE = int(os.getenv("SYNAP_WATERMARK_ROTATE", "315"))
SYNAP_WATERMARK_TEXT_SIZE = int(os.getenv("SYNAP_WATERMARK_TEXT_SIZE", "30"))
SYNAP_WATERMARK_TILE = str(os.getenv("SYNAP_WATERMARK_TILE", "true")).strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SYNAP_WATERMARK_TILE_SPACING = int(os.getenv("SYNAP_WATERMARK_TILE_SPACING", "220"))


UPLOADS_DIR = os.path.abspath(CONFIG_UPLOAD_DIR)
log = logging.getLogger(__name__)
print(f"[docs-embed] loaded from {__file__}, UPLOADS_DIR={UPLOADS_DIR}")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _issue_token(object_id: str, user_id: Optional[str] = None, ttl_minutes: int = URL_TTL_MINUTES):
    exp = _now_utc() + timedelta(minutes=ttl_minutes)
    payload = {
        "oid": object_id,
        "sub": user_id or "anonymous",
        "scp": "read",
        "exp": int(exp.timestamp()),
        "iat": int(_now_utc().timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    return token, exp


def _verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="URL expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _format_http_datetime(ts: float) -> str:
    from email.utils import formatdate
    return formatdate(timeval=ts, usegmt=True)


def _safe_join_uploads(filename: str) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="Invalid file id")

    filename = unquote(filename)

    path = os.path.normpath(os.path.join(UPLOADS_DIR, filename))

    uploads_root = os.path.abspath(UPLOADS_DIR)
    if not path.startswith(uploads_root + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")

    return path


def _stat_file(path: str) -> os.stat_result:
    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return os.stat(path)


def _guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def _parse_range(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    try:
        units, rng = range_header.split("=")
        if units.strip().lower() != "bytes":
            return None
        start_str, end_str = rng.split("-", 1)
        if start_str == "" and end_str == "":
            return None

        if start_str == "":
            length = int(end_str)
            if length <= 0:
                return None
            start = max(file_size - length, 0)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str != "" else file_size - 1
            if start > end or start < 0:
                return None
            if end >= file_size:
                end = file_size - 1

        return (start, end)
    except Exception:
        return None


def _latest_object_id_from_doc_id(doc_id: int) -> int:
    row = fetch_one(
        """
        SELECT dr.storage_object_id
          FROM doc_revisions dr
         WHERE dr.doc_master_id=%s AND dr.deleted_at IS NULL
         ORDER BY dr.rev_no DESC
         LIMIT 1
        """,
        (doc_id,),
    )
    if not row or not row.get("storage_object_id"):
        raise HTTPException(status_code=404, detail="Document not found")
    return int(row["storage_object_id"])


def _normalize_rel_prefix(rel: str) -> str:
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    base = os.path.basename(os.path.abspath(UPLOADS_DIR)).lower()
    if base and rel.lower() == base:
        return ""
    prefix = base + "/"
    if base and rel.lower().startswith(prefix):
        return rel[len(prefix):]
    return rel


def _stored_filename_from_object_id(object_id_int: int) -> str:
    row = fetch_one("SELECT name, file_right, relative_path, sub_path FROM storage_objects WHERE object_id=%s", (object_id_int,))
    if not row or not row.get("name"):
        raise HTTPException(status_code=404, detail="Document not found")
    rel_root = _normalize_rel_prefix((row.get("relative_path") or ""))
    sub = (row.get("sub_path") or "").strip().replace("\\", "/").strip("/")
    rel = rel_root
    if rel_root and sub:
        rel = f"{rel_root}/{sub}"
    elif sub and not rel_root:
        rel = sub
    name = str(row["name"])
    file_right = (row.get("file_right") or "").strip().lstrip(".")
    display = f"{name}.{file_right}" if file_right else name
    stored = display
    if rel:
        if rel.lower() == stored.lower() or rel.lower().endswith("/" + stored.lower()):
            return rel
        return f"{rel}/{stored}"
    return stored


def _ascii_fallback(name: str) -> str:
    fallback = (
        name.encode("ascii", "ignore").decode("ascii").replace('"', "").replace("\\", "")
    )
    if not fallback:
        fallback = "file"
    return fallback


def _content_disposition_inline(filename: str) -> str:
    original = os.path.basename(unquote(filename))
    fallback = _ascii_fallback(original)
    utf8_star = f"UTF-8''{quote(original)}"
    return f'inline; filename="{fallback}"; filename*={utf8_star}'


class FileMeta(BaseModel):
    name: str
    mime: str
    size: int
    can_read: bool
    signed_url: Optional[str] = None
    expires_at: Optional[str] = None


class SynapJobMeta(BaseModel):
    object_id: int
    fid: str
    mime: str
    file_url: str
    job_url: str
    expires_at: Optional[str] = None


def _norm_path_token(v: Optional[str]) -> Optional[str]:
    s = str(v or "").strip().replace("\\", "/").strip("/")
    return s or None


def _normalize_theme(v: Optional[str]) -> Optional[str]:
    s = str(v or "").strip().lower()
    if s in ("dark", "light"):
        return s
    return None


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def _normalize_hex_color(v: str, default: str = "C62828") -> str:
    s = str(v or "").strip().lstrip("#")
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return s.upper()
    return default.upper()


def _build_default_watermark_json() -> Optional[str]:
    if not SYNAP_WATERMARK_ENABLED:
        return None

    text = str(SYNAP_WATERMARK_TEXT or "").strip()
    if not text:
        return None

    payload = [
        {
            "pages": "F-L",
            "rotate": _clamp_int(SYNAP_WATERMARK_ROTATE, 0, 360),
            "alpha": _clamp_int(SYNAP_WATERMARK_ALPHA, 0, 100),
            "tile": bool(SYNAP_WATERMARK_TILE),
            "tileSpacing": max(-1, int(SYNAP_WATERMARK_TILE_SPACING)),
            "text": text,
            "textFont": "sans serif",
            "textColor": _normalize_hex_color(SYNAP_WATERMARK_COLOR, default="C62828"),
            "textSize": max(1, int(SYNAP_WATERMARK_TEXT_SIZE)),
            "textAlign": "center",
        }
    ]
    # Synap API expects watermarkJson as a JSON string.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _try_get_current_user_for_watermark(request: Optional[Request]) -> Optional[dict]:
    """
    Watermark enrich should not break viewer open flow.
    If user context is unavailable, return None and fallback to anonymous marks.
    """
    if request is None:
        return None
    try:
        return core_get_current_user(request)
    except Exception:
        return None


def _format_kst_now() -> str:
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S")


def _build_watermark_text(
    object_id: int,
    current_user: Optional[dict],
) -> str:
    company = str(SYNAP_WATERMARK_COMPANY or "").strip() or "레드스피어"
    uid = None
    username = None
    if isinstance(current_user, dict):
        uid = current_user.get("user_id") or current_user.get("id")
        username = current_user.get("username")

    uid_s = str(uid).strip() if uid is not None else "-"
    uname_s = str(username).strip() if username else "-"
    kst_s = _format_kst_now()

    parts = [
        f"UID:{uid_s}",
        company,
        f"USERNAME:{uname_s}",
        f"KST:{kst_s}",
    ]
    if SYNAP_WATERMARK_INCLUDE_OBJECT_ID:
        parts.append(f"DOC:{int(object_id)}")
    # Repeat UID at the tail so it remains visible even when diagonal tiling is partially clipped.
    parts.append(f"UID:{uid_s}")
    return " | ".join(parts)


def _build_watermark_json_for_request(
    object_id: int,
    current_user: Optional[dict],
) -> Optional[str]:
    """
    Build a red diagonal semi-transparent watermark payload with
    company/user/time/object metadata in text.
    """
    if not SYNAP_WATERMARK_ENABLED:
        return None
    wm_text = _build_watermark_text(object_id=object_id, current_user=current_user)
    if not wm_text:
        return None

    payload = [
        {
            "pages": "F-L",
            "rotate": _clamp_int(SYNAP_WATERMARK_ROTATE, 0, 360),
            "alpha": _clamp_int(SYNAP_WATERMARK_ALPHA, 0, 100),
            "tile": bool(SYNAP_WATERMARK_TILE),
            "tileSpacing": max(-1, int(SYNAP_WATERMARK_TILE_SPACING)),
            "text": wm_text,
            "textFont": "sans serif",
            "textColor": _normalize_hex_color(SYNAP_WATERMARK_COLOR, default="C62828"),
            "textSize": max(1, int(SYNAP_WATERMARK_TEXT_SIZE)),
            "textAlign": "center",
        }
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _split_name_and_ext(display_name: str) -> Tuple[str, Optional[str]]:
    raw = str(display_name or "").strip()
    if not raw:
        return "", None
    base, ext = os.path.splitext(raw)
    if not base:
        base = raw
    ext_norm = ext.lstrip(".").strip().lower() or None
    return base.strip(), ext_norm


def _normalize_stored_path(stored_path: str) -> str:
    p = str(stored_path or "").strip().replace("\\", "/").strip("/")
    if not p:
        raise HTTPException(status_code=400, detail="Invalid stored path")
    lower = p.lower()
    for prefix in ("uploads-general/", "uploads-kiwix/", "uploads/"):
        if lower.startswith(prefix):
            return p[len(prefix):] or ""
    return p


def _object_id_from_name_with_fallback(
    display_name: str,
    relative_path: Optional[str] = None,
    sub_path: Optional[str] = None,
    file_right: Optional[str] = None,
) -> int:
    try:
        return _object_id_from_name(
            display_name,
            relative_path=relative_path,
            sub_path=sub_path,
            file_right=file_right,
        )
    except HTTPException:
        base, _ = _split_name_and_ext(display_name)
        if not base or base == str(display_name or "").strip():
            raise
        return _object_id_from_name(
            base,
            relative_path=relative_path,
            sub_path=sub_path,
            file_right=file_right,
        )


def _object_id_from_stored_path(stored_path: str) -> int:
    norm = _normalize_stored_path(stored_path)
    parts = [p for p in norm.split("/") if p]
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid stored path")

    filename = parts[-1]
    base, ext = _split_name_and_ext(filename)
    if not base:
        raise HTTPException(status_code=400, detail="Invalid stored path")

    rel = parts[0] if len(parts) >= 2 else None
    sub = "/".join(parts[1:-1]) if len(parts) >= 3 else None
    conds = ["name=%s"]
    params: list = [base]
    if ext:
        conds.append("LOWER(file_right)=%s")
        params.append(ext)
    if rel:
        conds.append("relative_path=%s")
        params.append(rel)
    if sub:
        conds.append("sub_path=%s")
        params.append(sub)

    row = fetch_one(
        f"""
        SELECT object_id
          FROM storage_objects
         WHERE {" AND ".join(conds)}
         ORDER BY object_id DESC
         LIMIT 1
        """,
        tuple(params),
    )
    if row and row.get("object_id"):
        return int(row["object_id"])

    # Fallback to name/ext-only lookup for legacy rows that do not keep rel/sub consistently.
    conds = ["name=%s"]
    params = [base]
    if ext:
        conds.append("LOWER(file_right)=%s")
        params.append(ext)
    row = fetch_one(
        f"""
        SELECT object_id
          FROM storage_objects
         WHERE {" AND ".join(conds)}
         ORDER BY object_id DESC
         LIMIT 1
        """,
        tuple(params),
    )
    if not row or not row.get("object_id"):
        raise HTTPException(status_code=404, detail="Document not found")
    return int(row["object_id"])


def _object_exists(object_id: int) -> bool:
    row = fetch_one("SELECT object_id FROM storage_objects WHERE object_id=%s", (int(object_id),))
    return bool(row and row.get("object_id") is not None)


def _resolve_object_id_for_synap(
    doc_id: Optional[int],
    object_id: Optional[int],
    name: Optional[str],
    stored_path: Optional[str],
    relative_path: Optional[str],
    sub_path: Optional[str],
    file_right: Optional[str],
) -> int:
    rel_norm = _norm_path_token(relative_path)
    sub_norm = _norm_path_token(sub_path)
    right_norm = str(file_right or "").strip().lstrip(".").lower() or None
    name_norm = str(name or "").strip()
    has_path_hint = bool(rel_norm or sub_norm or right_norm)
    stored_norm = str(stored_path or "").strip()

    if object_id is not None:
        oid = int(object_id)
        if _object_exists(oid):
            return oid

    if name_norm and has_path_hint:
        try:
            return _object_id_from_name_with_fallback(
                name_norm,
                relative_path=rel_norm,
                sub_path=sub_norm,
                file_right=right_norm,
            )
        except HTTPException:
            pass

    if stored_norm:
        try:
            return _object_id_from_stored_path(stored_norm)
        except HTTPException:
            pass

    if doc_id is not None:
        try:
            return _latest_object_id_from_doc_id(int(doc_id))
        except HTTPException:
            pass

    if name_norm:
        return _object_id_from_name_with_fallback(name_norm)

    raise HTTPException(status_code=400, detail="No valid document identifier was provided")


def _build_fid_for_synap(
    object_id: int,
    stored_filename: str,
    branch_type: Optional[str] = None,
    relative_path: Optional[str] = None,
    sub_path: Optional[str] = None,
    display_name: Optional[str] = None,
) -> str:
    branch = str(branch_type or "").strip().upper()
    rel = _norm_path_token(relative_path)
    sub = _norm_path_token(sub_path)
    name = str(display_name or "").strip() or os.path.basename(str(stored_filename or ""))

    parts = [p for p in (branch, rel, sub, name) if p]
    path_key = "/".join(parts) if parts else (_norm_path_token(stored_filename) or str(object_id))
    digest = hashlib.sha256(path_key.encode("utf-8")).hexdigest()[:12]
    return f"{int(object_id)}_{digest}"[:255]


def _make_signed_stream_url(object_id: int) -> Tuple[str, str]:
    token, exp = _issue_token(object_id=str(int(object_id)))
    rel_url = DOCS_EMBED_PREFIX + f"/by-object/{int(object_id)}/stream?token={token}"
    abs_url = f"{DOCS_EMBED_PUBLIC_BASE_URL}{rel_url}" if DOCS_EMBED_PUBLIC_BASE_URL else rel_url
    return abs_url, exp.isoformat()


def _build_synap_job_params(
    file_url: str,
    fid: str,
    page: Optional[int],
    watermark_json: Optional[str],
) -> dict:
    params = {
        "fid": fid,
        "filePath": file_url,
        "fileType": "URL",
        "sync": "true",
    }
    if page is not None and int(page) > 0:
        params["pn"] = str(int(page))
    if watermark_json:
        params["watermarkJson"] = str(watermark_json)
    return params


def _make_synap_job_url(
    file_url: str,
    fid: str,
    page: Optional[int],
    watermark_json: Optional[str] = None,
) -> str:
    params = _build_synap_job_params(
        file_url=file_url,
        fid=fid,
        page=page,
        watermark_json=watermark_json,
    )
    qs = urlencode(params)
    return f"{SYNAP_BASE_URL}/job?{qs}"


def _with_query_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(str(url))
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    pairs.append((key, value))
    query = urlencode(pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _to_abs_synap_view_url(view_url_path: str) -> str:
    raw = str(view_url_path or "").strip()
    if not raw:
        raise ValueError("Empty view URL path")
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    base_parts = urlsplit(SYNAP_BASE_URL)
    origin = urlunsplit((base_parts.scheme, base_parts.netloc, "", "", "")).rstrip("/")
    ctx = (base_parts.path or "").rstrip("/")

    if raw.startswith("/"):
        return f"{origin}{raw}"

    if ctx:
        return f"{origin}{ctx}/{raw.lstrip('/')}"
    return f"{origin}/{raw.lstrip('/')}"


def _resolve_synap_view_url_via_jobjson(
    file_url: str,
    fid: str,
    watermark_json: Optional[str] = None,
) -> str:
    """
    Build a final viewer URL via jobJson response(viewUrlPath), so page hints can be
    attached to the final viewer URL instead of relying on /job redirect propagation.
    """
    endpoint = f"{SYNAP_BASE_URL}/jobJson"
    params = _build_synap_job_params(
        file_url=file_url,
        fid=fid,
        page=None,
        watermark_json=watermark_json,
    )
    timeout = httpx.Timeout(float(SYNAP_JOB_RESOLVE_TIMEOUT_SEC))
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(endpoint, params=params, headers={"Accept": "application/json"})
    if int(resp.status_code) >= 400:
        raise RuntimeError(f"jobJson failed: {resp.status_code}")

    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("jobJson response is not an object")

    view_url_path = data.get("viewUrlPath") or data.get("view_url_path")
    if not view_url_path:
        key = data.get("key")
        if key:
            view_url_path = f"view/{key}"
    if not view_url_path:
        raise RuntimeError("jobJson response has no viewUrlPath/key")

    return _to_abs_synap_view_url(str(view_url_path))


def _resolve_synap_view_chain_url(view_url: str, page: Optional[int], max_hops: int = 5) -> str:
    """
    Resolve view URL redirects manually and keep page hints(pn/page) on each hop.
    This mitigates cases where /view redirects to skin/doc.html and drops custom params.
    """
    current = str(view_url or "").strip()
    if not current:
        return current

    page_num = int(page) if page is not None else 0
    if page_num > 0:
        page_str = str(page_num)
        current = _with_query_param(current, "pn", page_str)
        current = _with_query_param(current, "page", page_str)

    try:
        timeout = httpx.Timeout(float(SYNAP_JOB_RESOLVE_TIMEOUT_SEC))
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            for _ in range(max(1, int(max_hops))):
                resp = client.get(current, headers={"Accept": "text/html,*/*"})
                if 300 <= int(resp.status_code) < 400:
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    nxt = urljoin(current, loc)
                    if page_num > 0:
                        page_str = str(page_num)
                        nxt = _with_query_param(nxt, "pn", page_str)
                        nxt = _with_query_param(nxt, "page", page_str)
                    log.info("[docs-embed] view redirect hop status=%s -> %s", resp.status_code, nxt)
                    current = nxt
                    continue
                break
    except Exception:
        log.exception("[docs-embed] synap view chain resolution failed")

    return current


def _resolve_synap_redirect_url(job_url: str, page: Optional[int]) -> str:
    """
    Resolve /job redirect target once and preserve page hint on the final view URL.
    If resolution fails, fallback to the original /job URL.
    """
    try:
        timeout = httpx.Timeout(float(SYNAP_JOB_RESOLVE_TIMEOUT_SEC))
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            resp = client.get(job_url, headers={"Accept": "text/html,*/*"})
        if 300 <= int(resp.status_code) < 400:
            loc = resp.headers.get("location")
            if loc:
                resolved = urljoin(job_url, loc)
                if page is not None and int(page) > 0:
                    resolved = _with_query_param(resolved, "pn", str(int(page)))
                log.info("[docs-embed] resolved synap redirect url page=%s -> %s", page, resolved)
                return resolved
        log.info("[docs-embed] synap redirect resolution skipped status=%s", resp.status_code)
    except Exception:
        log.exception("[docs-embed] synap redirect resolution failed")
        return job_url
    return job_url


def _object_id_from_name(
    display_name: str,
    relative_path: Optional[str] = None,
    sub_path: Optional[str] = None,
    file_right: Optional[str] = None,
) -> int:
    name = (display_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Invalid file name")
    base, ext = os.path.splitext(name)
    base = base or name
    ext_right = ext.lstrip(".").lower()
    fr = (file_right or ext_right or "").strip().lstrip(".").lower() or None

    rel = (relative_path or "").strip().replace("\\", "/").strip("/")
    sub = (sub_path or "").strip().replace("\\", "/").strip("/")
    rel = rel or None
    sub = sub or None

    conds = ["name=%s"]
    params: list = [base]
    if fr:
        conds.append("LOWER(file_right)=%s")
        params.append(fr)
    if rel:
        conds.append("relative_path=%s")
        params.append(rel)
    if sub:
        conds.append("sub_path=%s")
        params.append(sub)

    row = fetch_one(
        f"""
        SELECT object_id
          FROM storage_objects
         WHERE {" AND ".join(conds)}
         ORDER BY object_id DESC
         LIMIT 1
        """,
        tuple(params),
    )
    if not row or not row.get("object_id"):
        raise HTTPException(status_code=404, detail="Document not found")
    return int(row["object_id"])


def debug_path(
    name: str = Query(..., min_length=1),
):
    object_id = _object_id_from_name(name)
    stored_filename = _stored_filename_from_object_id(object_id)
    path = _safe_join_uploads(stored_filename)
    return {
        "uploads_dir": UPLOADS_DIR,
        "object_id": object_id,
        "stored": stored_filename,
        "path": path,
        "exists": os.path.exists(path),
    }




def get_file_meta_by_id(
    doc_id: int = Path(..., ge=1),
    request: Request = None,
):
    object_id = _latest_object_id_from_doc_id(doc_id)
    stored_filename = _stored_filename_from_object_id(object_id)
    path = _safe_join_uploads(stored_filename)
    st = _stat_file(path)

    mime = _guess_mime(stored_filename)
    meta = FileMeta(
        name=unquote(stored_filename),
        mime=mime,
        size=st.st_size,
        can_read=True,
    )

    token, exp = _issue_token(object_id=unquote(stored_filename))
    encoded = quote(unquote(stored_filename), safe="")
    meta.signed_url = DOCS_EMBED_PREFIX + f"/{encoded}/stream?token={token}"
    meta.expires_at = exp.isoformat()
    return meta




def get_file_meta_by_object_id(
    object_id: int = Path(..., ge=1),
    request: Request = None,
):
    stored_filename = _stored_filename_from_object_id(object_id)
    path = _safe_join_uploads(stored_filename)
    st = _stat_file(path)

    mime = _guess_mime(stored_filename)
    meta = FileMeta(
        name=unquote(stored_filename),
        mime=mime,
        size=st.st_size,
        can_read=True,
    )

    token, exp = _issue_token(object_id=unquote(stored_filename))
    encoded = quote(unquote(stored_filename), safe="")
    meta.signed_url = DOCS_EMBED_PREFIX + f"/{encoded}/stream?token={token}"
    meta.expires_at = exp.isoformat()
    return meta


def get_file_meta_by_name(
    name: str = Query(..., min_length=1),
    relative_path: Optional[str] = Query(None),
    sub_path: Optional[str] = Query(None),
    file_right: Optional[str] = Query(None),
    request: Request = None,
):
    object_id = _object_id_from_name(
        name,
        relative_path=relative_path,
        sub_path=sub_path,
        file_right=file_right,
    )
    stored_filename = _stored_filename_from_object_id(object_id)
    log.info("[docs-embed] by-name lookup name=%s object_id=%s stored=%s", name, object_id, stored_filename)
    print(f"[docs-embed] by-name lookup file={__file__} name={name} object_id={object_id} stored={stored_filename}")
    path = _safe_join_uploads(stored_filename)
    log.info("[docs-embed] resolved path=%s exists=%s", path, os.path.exists(path))
    print(f"[docs-embed] resolved path={path} exists={os.path.exists(path)}")
    st = _stat_file(path)

    mime = _guess_mime(stored_filename)
    meta = FileMeta(
        name=unquote(stored_filename),
        mime=mime,
        size=st.st_size,
        can_read=True,
    )

    token, exp = _issue_token(object_id=unquote(stored_filename))
    encoded = quote(unquote(stored_filename), safe="")
    meta.signed_url = DOCS_EMBED_PREFIX + f"/{encoded}/stream?token={token}"
    meta.expires_at = exp.isoformat()
    return meta


def get_synap_job(
    request: Request,
    doc_id: Optional[int] = Query(None, ge=1),
    object_id: Optional[int] = Query(None, ge=1),
    name: Optional[str] = Query(None),
    stored_path: Optional[str] = Query(None),
    relative_path: Optional[str] = Query(None),
    sub_path: Optional[str] = Query(None),
    file_right: Optional[str] = Query(None),
    branch_type: Optional[str] = Query(None),
    page: Optional[int] = Query(None, ge=1),
    theme: Optional[str] = Query(None),
):
    oid = _resolve_object_id_for_synap(
        doc_id=doc_id,
        object_id=object_id,
        name=name,
        stored_path=stored_path,
        relative_path=relative_path,
        sub_path=sub_path,
        file_right=file_right,
    )
    stored_filename = _stored_filename_from_object_id(oid)
    path = _safe_join_uploads(stored_filename)
    _ = _stat_file(path)
    mime = _guess_mime(stored_filename)

    file_url, expires_at = _make_signed_stream_url(oid)
    display_name = str(name or "").strip() or os.path.basename(stored_filename)
    fid = _build_fid_for_synap(
        object_id=oid,
        stored_filename=stored_filename,
        branch_type=branch_type,
        relative_path=relative_path,
        sub_path=sub_path,
        display_name=display_name,
    )
    current_user = _try_get_current_user_for_watermark(request)
    watermark_json = _build_watermark_json_for_request(
        object_id=oid,
        current_user=current_user,
    )
    if watermark_json is None:
        # Backward-compatible fallback.
        watermark_json = _build_default_watermark_json()
    job_url = _make_synap_job_url(
        file_url=file_url,
        fid=fid,
        page=page,
        watermark_json=watermark_json,
    )
    if page is not None and int(page) > 0:
        page_str = str(int(page))
        try:
            job_url = _resolve_synap_view_url_via_jobjson(
                file_url=file_url,
                fid=fid,
                watermark_json=watermark_json,
            )
            job_url = _resolve_synap_view_chain_url(job_url, page=int(page))
            log.info("[docs-embed] resolved synap view url via jobJson page=%s -> %s", page, job_url)
        except Exception:
            log.exception("[docs-embed] jobJson view url resolution failed; fallback to /job")
            if int(page) > 1:
                job_url = _resolve_synap_redirect_url(job_url, page)
            job_url = _with_query_param(job_url, "pn", page_str)
            job_url = _with_query_param(job_url, "page", page_str)
    theme_norm = _normalize_theme(theme)
    if theme_norm:
        job_url = _with_query_param(job_url, "theme", theme_norm)
    print(f"[docs-embed] synap job_url page={page} -> {job_url}")

    return SynapJobMeta(
        object_id=oid,
        fid=fid,
        mime=mime,
        file_url=file_url,
        job_url=job_url,
        expires_at=expires_at,
    )


def renew_signed_url_by_object_id(object_id: int = Path(..., ge=1)):
    stored_filename = _stored_filename_from_object_id(object_id)
    token, exp = _issue_token(object_id=unquote(stored_filename))
    encoded = quote(unquote(stored_filename), safe="")
    return {
        "signed_url": DOCS_EMBED_PREFIX + f"/{encoded}/stream?token={token}",
        "expires_at": exp.isoformat(),
    }

def renew_signed_url_by_id(doc_id: int = Path(..., ge=1)):
    object_id = _latest_object_id_from_doc_id(doc_id)
    stored_filename = _stored_filename_from_object_id(object_id)
    token, exp = _issue_token(object_id=unquote(stored_filename))
    encoded = quote(unquote(stored_filename), safe="")
    return {
        "signed_url": DOCS_EMBED_PREFIX + f"/{encoded}/stream?token={token}",
        "expires_at": exp.isoformat(),
    }


def renew_signed_url(stored_filename: str, request: Request):
    _ = _safe_join_uploads(stored_filename)
    token, exp = _issue_token(object_id=unquote(stored_filename))
    encoded = quote(unquote(stored_filename), safe="")
    return {
        "signed_url": DOCS_EMBED_PREFIX + f"/{encoded}/stream?token={token}",
        "expires_at": exp.isoformat(),
    }


def _stream_file_from_stored(stored_filename: str, request: Request):
    path = _safe_join_uploads(stored_filename)
    print(f"[docs-embed] stream path={path} exists={os.path.exists(path)}")
    st = _stat_file(path)
    file_size = st.st_size
    mime = _guess_mime(stored_filename)

    last_mod = _format_http_datetime(st.st_mtime)
    etag = f'W/"{st.st_mtime}-{file_size}"'

    if (inm := request.headers.get("if-none-match")) and inm == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)
    if (ims := request.headers.get("if-modified-since")) and ims == last_mod:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    content_disposition = _content_disposition_inline(stored_filename)

    range_header = request.headers.get("range")
    if range_header:
        byte_range = _parse_range(range_header, file_size)
        if not byte_range:
            return Response(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start, end = byte_range
        length = end - start + 1

        def range_iter(file_path: str, start_pos: int, bytes_len: int, chunk_size: int = 1024 * 512):
            with open(file_path, "rb") as f:
                f.seek(start_pos)
                remaining = bytes_len
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Type": mime,
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Disposition": content_disposition,  # ASCII-only, RFC5987
            "ETag": etag,
            "Last-Modified": last_mod,
        }
        return StreamingResponse(
            range_iter(path, start, length),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            headers=headers,
        )

    def file_iter(file_path: str, chunk_size: int = 1024 * 512):
        with open(file_path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data

    headers = {
        "Content-Type": mime,
        "Content-Length": str(file_size),
        "Accept-Ranges": "bytes",
        "Content-Disposition": content_disposition,  # ASCII-only, RFC5987
        "ETag": etag,
        "Last-Modified": last_mod,
    }
    return StreamingResponse(file_iter(path), headers=headers)


def stream_file_by_object(
    object_id: int = Path(..., ge=1),
    request: Request = None,
    token: str = Query(...),
):
    claims = _verify_token(token)
    if claims.get("oid") != str(int(object_id)) or claims.get("scp") != "read":
        raise HTTPException(status_code=403, detail="Forbidden")
    stored_filename = _stored_filename_from_object_id(int(object_id))
    return _stream_file_from_stored(stored_filename, request)


def stream_file(stored_filename: str, request: Request, token: str = Query(...)):
    raw_stored = stored_filename
    stored_filename = unquote(stored_filename)
    print(f"[docs-embed] stream raw={raw_stored}")
    print(f"[docs-embed] stream unquoted={stored_filename}")
    claims = _verify_token(token)
    if claims.get("oid") != unquote(stored_filename) or claims.get("scp") != "read":
        raise HTTPException(status_code=403, detail="Forbidden")
    return _stream_file_from_stored(stored_filename, request)


def get_file_meta(stored_filename: str, request: Request):
    stored_filename = unquote(stored_filename)
    if stored_filename == "by-name":
        name = request.query_params.get("name", "")
        rel = request.query_params.get("relative_path", "") or None
        sub = request.query_params.get("sub_path", "") or None
        fr = request.query_params.get("file_right", "") or None
        object_id = _object_id_from_name(name, relative_path=rel, sub_path=sub, file_right=fr)
        stored_filename = _stored_filename_from_object_id(object_id)
    elif stored_filename.startswith("by-id/"):
        raw = stored_filename.split("/", 1)[-1]
        if raw.isdigit():
            object_id = _latest_object_id_from_doc_id(int(raw))
            stored_filename = _stored_filename_from_object_id(object_id)
    elif stored_filename.startswith("by-object/"):
        raw = stored_filename.split("/", 1)[-1]
        if raw.isdigit():
            stored_filename = _stored_filename_from_object_id(int(raw))

    path = _safe_join_uploads(stored_filename)
    st = _stat_file(path)

    mime = _guess_mime(stored_filename)
    can_read = True

    meta = FileMeta(
        name=unquote(stored_filename),
        mime=mime,
        size=st.st_size,
        can_read=can_read,
    )

    if can_read:
        token, exp = _issue_token(object_id=unquote(stored_filename))
        encoded = quote(unquote(stored_filename), safe="")
        meta.signed_url = DOCS_EMBED_PREFIX + f"/{encoded}/stream?token={token}"
        meta.expires_at = exp.isoformat()

    return meta
