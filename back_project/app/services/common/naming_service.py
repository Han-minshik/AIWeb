from __future__ import annotations

import re
from typing import Any, Optional


def normalize_folder_slug(name: str) -> str:
    raw = (name or "").strip()
    raw = re.sub(r"[^A-Za-z가-힣0-9 .-]+", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw or "folder"


def build_display_name(name: Optional[str], file_right: Optional[str], fallback: str = "file") -> str:
    base = (name or "").strip() or fallback
    ext = (file_right or "").strip().lstrip(".")
    if ext and not base.lower().endswith("." + ext.lower()):
        return f"{base}.{ext}"
    return base


def coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip():
            return int(float(value.strip()))
    except Exception:
        return default
    return default

