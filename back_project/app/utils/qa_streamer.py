import re
import asyncio
from typing import AsyncGenerator, Dict, List, Any, Optional


def _normalize_md(text: str) -> List[str]:
    if not isinstance(text, str):
        text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [ln.rstrip() for ln in text.split("\n")]


async def iter_markdown_blocks(data: dict) -> AsyncGenerator[str, None]:
    answer_md = (
        data.get("answer_md")
        or data.get("answer")
        or data.get("content")
        or ""
    )
    lines = _normalize_md(answer_md)

    i, n = 0, len(lines)
    fence_re = re.compile(r"^```")
    heading_re = re.compile(r"^#{1,6}\s+")
    ulist_re = re.compile(r"^\s*[-*+]\s+")
    olist_re = re.compile(r"^\s*\d+\.\s+")
    table_re = re.compile(r"^\s*\|.*\|\s*$")
    image_re = re.compile(r"^\s*!\[.*?\]\(.*?\)\s*$")

    def flush_block(buf: List[str]) -> str:
        while buf and buf[-1] == "":
            buf.pop()
        return "\n".join(buf).strip()

    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue

        if fence_re.match(ln):
            fence = ln
            buf = [ln]
            i += 1
            while i < n and not fence_re.match(lines[i]):
                buf.append(lines[i])
                i += 1
            if i < n:
                buf.append(lines[i])
                i += 1
            block = flush_block(buf)
            if block:
                yield block
                await asyncio.sleep(0.03)
            continue

        if table_re.match(ln):
            buf = [ln]
            i += 1
            while i < n and table_re.match(lines[i]):
                buf.append(lines[i])
                i += 1
            block = flush_block(buf)
            if block:
                yield block
                await asyncio.sleep(0.03)
            continue

        if heading_re.match(ln):
            block = ln.strip()
            i += 1
            yield block
            await asyncio.sleep(0.02)
            continue

        if image_re.match(ln):
            block = ln.strip()
            i += 1
            yield block
            await asyncio.sleep(0.02)
            continue

        if ulist_re.match(ln) or olist_re.match(ln):
            buf = [ln]
            i += 1
            while i < n and (ulist_re.match(lines[i]) or olist_re.match(lines[i])):
                buf.append(lines[i])
                i += 1
            block = flush_block(buf)
            if block:
                yield block
                await asyncio.sleep(0.025)
            continue

        buf = [ln]
        i += 1
        while i < n and lines[i].strip() and not any((
            fence_re.match(lines[i]),
            table_re.match(lines[i]),
            heading_re.match(lines[i]),
            image_re.match(lines[i]),
            ulist_re.match(lines[i]),
            olist_re.match(lines[i]),
        )):
            buf.append(lines[i])
            i += 1
        block = flush_block(buf)
        if block:
            yield block
            await asyncio.sleep(0.02)


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


def _first_thumb_from_refs(refs: Any) -> Dict[str, str]:
    try:
        if isinstance(refs, dict):
            b64 = refs.get("thumb_b64") or refs.get("thumbnail_b64")
            if b64:
                return {"thumb_b64": b64, "thumb_mime": _pick_mime(refs)}
        elif isinstance(refs, list):
            for r in refs:
                if not isinstance(r, dict):
                    continue
                b64 = r.get("thumb_b64") or r.get("thumbnail_b64")
                if b64:
                    return {"thumb_b64": b64, "thumb_mime": _pick_mime(r)}
    except Exception:
        pass
    return {}


def _first_thumb_from_chunks(chunks: Any) -> Dict[str, str]:
    try:
        if isinstance(chunks, dict):
            b64 = chunks.get("thumb_b64") or chunks.get("thumbnail_b64")
            if b64:
                return {"thumb_b64": b64, "thumb_mime": _pick_mime(chunks)}
            hit = _first_thumb_from_refs(chunks.get("image_refs"))
            if hit:
                return hit
        elif isinstance(chunks, list):
            for ch in chunks:
                if isinstance(ch, dict):
                    b64 = ch.get("thumb_b64") or ch.get("thumbnail_b64")
                    if b64:
                        return {"thumb_b64": b64, "thumb_mime": _pick_mime(ch)}
                    hit = _first_thumb_from_refs(ch.get("image_refs"))
                    if hit:
                        return hit
    except Exception:
        pass
    return {}


def _first_thumb_from_sources(sources: Any) -> Dict[str, str]:
    if not isinstance(sources, list):
        return {}
    for s in sources:
        if not isinstance(s, dict):
            continue
        b64 = s.get("thumb_b64") or s.get("thumbnail_b64")
        if b64:
            return {"thumb_b64": b64, "thumb_mime": _pick_mime(s)}
        hit = _first_thumb_from_refs(s.get("image_refs"))
        if hit:
            return hit
        hit = _first_thumb_from_chunks(s.get("chunks"))
        if hit:
            return hit
    return {}


def extract_thumb_meta(data: dict) -> Dict[str, str]:
    try:
        top_b64 = data.get("thumb_b64") or data.get("thumbnail_b64")
        if top_b64:
            return {"thumb_b64": top_b64, "thumb_mime": _pick_mime(data)}
    except Exception:
        pass
    try:
        hit = _first_thumb_from_sources(data.get("sources"))
        if hit:
            return hit
    except Exception:
        pass
    try:
        imgs = data.get("images")
        if isinstance(imgs, list):
            for it in imgs:
                if isinstance(it, dict):
                    b64 = it.get("thumb_b64") or it.get("thumbnail_b64")
                    if b64:
                        return {"thumb_b64": b64, "thumb_mime": _pick_mime(it)}
    except Exception:
        pass
    return {}


_OBJECT_ID_KEYS = ("object_id", "objectId", "objectID")
_PAGE_KEYS = ("page", "page_no", "pageNo", "pageNumber")


def _coerce_int(x: Any) -> Optional[int]:
    try:
        if isinstance(x, bool):
            return None
        if isinstance(x, int):
            return x
        if isinstance(x, float) and x.is_integer():
            return int(x)
        if isinstance(x, str) and x.strip():
            return int(float(x.strip()))
    except Exception:
        return None
    return None


def _extract_object_id(obj: Dict[str, Any]) -> Optional[int]:
    for k in _OBJECT_ID_KEYS:
        v = obj.get(k)
        oid = _coerce_int(v)
        if oid is not None:
            return oid
    return None


def _extract_page(obj: Dict[str, Any]) -> Optional[int]:
    for k in _PAGE_KEYS:
        v = obj.get(k)
        page = _coerce_int(v)
        if page is not None:
            return page
    refs = obj.get("image_refs")
    if isinstance(refs, list):
        for r in refs:
            if isinstance(r, dict):
                page = _extract_page(r)
                if page is not None:
                    return page
    chunks = obj.get("chunks")
    if isinstance(chunks, list):
        for ch in chunks:
            if isinstance(ch, dict):
                page = _extract_page(ch)
                if page is not None:
                    return page
    return None


def _normalize_source_item(src: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(src or {})
    oid = _extract_object_id(out)
    if oid is None:
        for key in ("chunks", "image_refs"):
            nested = out.get(key)
            if isinstance(nested, list):
                for it in nested:
                    if isinstance(it, dict):
                        oid = _extract_object_id(it)
                        if oid is not None:
                            break
            if oid is not None:
                break
    if oid is not None:
        out["object_id"] = oid
    page = _extract_page(out)
    if page is not None:
        out["page"] = page
    chunks = out.get("chunks")
    if isinstance(chunks, list):
        new_chunks = []
        for ch in chunks:
            if isinstance(ch, dict):
                c = dict(ch)
                if "object_id" not in c and oid is not None:
                    c["object_id"] = oid
                p = _extract_page(c)
                if p is not None:
                    c["page"] = p
                new_chunks.append(c)
            else:
                new_chunks.append(ch)
        out["chunks"] = new_chunks
    return out


def normalize_sources(sources: Any) -> List[Dict[str, Any]]:
    if not isinstance(sources, list):
        return []
    out: List[Dict[str, Any]] = []
    for s in sources:
        if isinstance(s, dict):
            out.append(_normalize_source_item(s))
        else:
            out.append(s)
    return out


def enrich_stream_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    enriched = dict(data)
    if "sources" in enriched:
        enriched["sources"] = normalize_sources(enriched.get("sources"))
    if not (enriched.get("thumb_b64") or enriched.get("thumbnail_b64")):
        thumb = extract_thumb_meta(enriched)
        if thumb:
            enriched.update(thumb)
    return enriched


__all__ = [
    "iter_markdown_blocks",
    "extract_thumb_meta",
    "normalize_sources",
    "enrich_stream_payload",
]
