import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


SYNC_KIWIX_DOCUMENTS_JSON = Path(
    os.getenv("SYNC_KIWIX_DOCUMENTS_JSON", r"D:\storage\extract\documents.json")
).resolve()

_KIWIX_CACHE_MTIME: Optional[float] = None
_KIWIX_CACHE_BY_PRJ: Dict[str, List[Dict[str, str]]] = {}


def _normalize_text(v: Any) -> str:
    return " ".join(str(v or "").strip().split()).lower()


def _normalize_ext(v: Any) -> str:
    return str(v or "").strip().lstrip(".").lower()


def _leaf_segment(path_like: Any) -> str:
    s = str(path_like or "").strip().replace("\\", "/").strip("/")
    if not s:
        return ""
    return s.split("/")[-1]


def _load_kiwix_docs_by_prj() -> Dict[str, List[Dict[str, str]]]:
    global _KIWIX_CACHE_MTIME, _KIWIX_CACHE_BY_PRJ

    path = SYNC_KIWIX_DOCUMENTS_JSON
    if not path.exists():
        return {}

    try:
        mtime = float(path.stat().st_mtime)
    except Exception:
        return _KIWIX_CACHE_BY_PRJ

    if _KIWIX_CACHE_MTIME == mtime and _KIWIX_CACHE_BY_PRJ:
        return _KIWIX_CACHE_BY_PRJ

    rows: List[dict] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else []
    except Exception:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            rows = raw if isinstance(raw, list) else []
        except Exception:
            rows = []

    by_prj: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue

        platform = str(r.get("platform") or "").strip().upper()
        if platform and platform != "KIWIX":
            continue

        prj_code = str(r.get("prjCode") or "").strip()
        doc_code = str(r.get("docCode") or "").strip()
        file_name = str(r.get("fileName") or "").strip()
        file_ext = _normalize_ext(r.get("fileExt"))
        itemtype_name = str(r.get("itemtypeName") or "").strip()

        if not prj_code or not file_name or not itemtype_name:
            continue

        key_prj = _normalize_text(prj_code)
        by_prj.setdefault(key_prj, []).append(
            {
                "prj_code": prj_code,
                "doc_code": doc_code,
                "file_name": file_name,
                "file_name_norm": _normalize_text(file_name),
                "file_ext_norm": file_ext,
                "itemtype_name": itemtype_name,
            }
        )

    _KIWIX_CACHE_MTIME = mtime
    _KIWIX_CACHE_BY_PRJ = by_prj
    return _KIWIX_CACHE_BY_PRJ


def _filter_by_doc_prefix(rows: List[Dict[str, str]], leaf: str) -> List[Dict[str, str]]:
    if not leaf:
        return []
    low = leaf.lower()
    out: List[Dict[str, str]] = []
    for r in rows:
        doc_code = str(r.get("doc_code") or "").strip()
        if not doc_code:
            continue
        if low.startswith((doc_code + "_").lower()):
            out.append(r)
    return out


def _pick_single_itemtype(rows: List[Dict[str, str]]) -> Optional[str]:
    vals: List[str] = []
    for r in rows:
        v = str(r.get("itemtype_name") or "").strip()
        if v and v not in vals:
            vals.append(v)
    if len(vals) == 1:
        return vals[0]
    return None


def resolve_kiwix_itemtype_name(
    prj_code: Any,
    sub_path: Any,
    file_name: Any,
    file_ext: Any,
) -> Optional[str]:
    prj_key = _normalize_text(prj_code)
    if not prj_key:
        return None

    by_prj = _load_kiwix_docs_by_prj()
    candidates = list(by_prj.get(prj_key) or [])
    if not candidates:
        return None

    leaf = _leaf_segment(sub_path)
    name_key = _normalize_text(file_name)
    ext_key = _normalize_ext(file_ext)

    if name_key:
        name_matches = [r for r in candidates if r.get("file_name_norm") == name_key]
        if ext_key:
            ext_matches = [r for r in name_matches if (r.get("file_ext_norm") or "") == ext_key]
            if ext_matches:
                name_matches = ext_matches
        if len(name_matches) == 1:
            return name_matches[0]["itemtype_name"]

        pref_name = _filter_by_doc_prefix(name_matches, leaf)
        chosen = _pick_single_itemtype(pref_name)
        if chosen:
            return chosen

        chosen = _pick_single_itemtype(name_matches)
        if chosen:
            return chosen

    pref_all = _filter_by_doc_prefix(candidates, leaf)
    if pref_all:
        if name_key:
            pref_name = [r for r in pref_all if r.get("file_name_norm") == name_key]
            if ext_key:
                pref_ext = [r for r in pref_name if (r.get("file_ext_norm") or "") == ext_key]
                if pref_ext:
                    pref_name = pref_ext
            chosen = _pick_single_itemtype(pref_name)
            if chosen:
                return chosen
        chosen = _pick_single_itemtype(pref_all)
        if chosen:
            return chosen

    return None

