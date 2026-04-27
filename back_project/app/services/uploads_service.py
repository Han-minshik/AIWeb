from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from fastapi import HTTPException

from app.core.config import UPLOAD_DIR as CONFIG_UPLOAD_DIR


UPLOAD_DIR = Path(CONFIG_UPLOAD_DIR).resolve()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def lookup_upload(title: str) -> dict:
    if not UPLOAD_DIR.exists():
        raise HTTPException(status_code=500, detail="uploads dir not found")
    key = norm(title.replace(".pdf", ""))
    best = None
    best_score = -1.0
    for p in UPLOAD_DIR.rglob("*.pdf"):
        k = norm(p.stem)
        score = SequenceMatcher(a=key, b=k).ratio()
        if key in k or k in key:
            score += 0.5
        if score > best_score:
            best, best_score = p, score
    if not best:
        raise HTTPException(status_code=404, detail="file not found")
    rel = best.relative_to(UPLOAD_DIR).as_posix()
    return {"url": f"/uploads/{rel}"}

