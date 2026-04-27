from __future__ import annotations

import hashlib
import os
import mimetypes
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal, Dict, Any, List

from fastapi import Depends, HTTPException, Body, Query

from app.deps.auth_dep import get_current_user_id
from app.repositories.common import fetch_one as _fetch_one, fetch_all as _fetch_all, execute as _execute
from app.core.config import (
    SE_KB_ID,
    SYNC_ALLOWED_BRANCH_TYPES as CFG_SYNC_ALLOWED_BRANCH_TYPES,
    SYNC_KIWIX_DOCUMENTS_JSON as CFG_SYNC_KIWIX_DOCUMENTS_JSON,
    SYNC_LOCAL_BASE_DIR_GENERAL,
    SYNC_LOCAL_BASE_DIR_KIWIX,
    SYNC_TRIGGER_BRANCH_TYPES as CFG_SYNC_TRIGGER_BRANCH_TYPES,
)
from app.services.common.naming_service import build_display_name as _build_display_name
from app.utils.db import get_connection


SYNC_ALLOWED_BRANCH_TYPES = set(CFG_SYNC_ALLOWED_BRANCH_TYPES or {"GENERAL", "KIWIX"})
SYNC_TRIGGER_BRANCH_TYPES = set(CFG_SYNC_TRIGGER_BRANCH_TYPES or {"GENERAL", "KIWIX"})
SYNC_LOCAL_BASE_DIRS = {
    "GENERAL": Path(
        SYNC_LOCAL_BASE_DIR_GENERAL
    ).resolve(),
    "KIWIX": Path(
        SYNC_LOCAL_BASE_DIR_KIWIX
    ).resolve(),
}
SYNC_KIWIX_DOCUMENTS_JSON = Path(
    CFG_SYNC_KIWIX_DOCUMENTS_JSON
).resolve()


_SYNC_TX_CONN: ContextVar[Optional[Any]] = ContextVar("kb_sync_tx_conn", default=None)


def fetch_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    conn = _SYNC_TX_CONN.get()
    if conn is None:
        return _fetch_one(sql, params)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()


def fetch_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = _SYNC_TX_CONN.get()
    if conn is None:
        return _fetch_all(sql, params)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return list(rows) if rows else []
    finally:
        cur.close()


def execute(sql: str, params: tuple = (), return_last_id: bool = False) -> Optional[int]:
    conn = _SYNC_TX_CONN.get()
    if conn is None:
        return _execute(sql, params, return_last_id)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        last_id = cur.lastrowid
        return int(last_id) if return_last_id else None
    finally:
        cur.close()


@contextmanager
def _sync_item_transaction():
    existing = _SYNC_TX_CONN.get()
    if existing is not None:
        yield
        return

    conn = get_connection(autocommit=False)
    token = _SYNC_TX_CONN.set(conn)
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _SYNC_TX_CONN.reset(token)
        conn.close()


class SyncIn:
    scope: Literal["KB", "PROJECT", "NODE", "OBJECT"] = "KB"


def _norm_path(v: Optional[str]) -> str:
    return str(v or "").strip().replace("\\", "/").strip("/")


def _join_path(*parts: str) -> str:
    out: List[str] = []
    for p in parts:
        n = _norm_path(p)
        if n:
            out.append(n)
    return "/".join(out)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _dir_path_from_file_path(rel_path: Optional[str]) -> str:
    norm = _norm_path(rel_path)
    if not norm:
        return ""
    parts = [p for p in norm.split("/") if p]
    if len(parts) <= 1:
        return ""
    return "/".join(parts[:-1])


def _folder_ancestors(rel_path: Optional[str]) -> List[str]:
    norm = _norm_path(rel_path)
    if not norm:
        return []
    parts = [p for p in norm.split("/") if p]
    if len(parts) <= 1:
        return []
    out: List[str] = []
    for i in range(1, len(parts)):
        out.append("/".join(parts[:i]))
    return out


def _scan_local_files() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    # GENERAL: compare by local filesystem scan.
    if "GENERAL" in SYNC_ALLOWED_BRANCH_TYPES:
        root = SYNC_LOCAL_BASE_DIRS.get("GENERAL")
        if root and root.exists() and root.is_dir():
            for p in root.rglob("*"):
                rel = p.relative_to(root).as_posix()
                if not rel:
                    continue
                if p.is_dir():
                    out.append(
                        {
                            "abs_path": str(p),
                            "rel_path": rel,
                            "path_key": _norm_path(rel).lower(),
                            "branch_type": "GENERAL",
                            "checksum": "",
                            "entry_type": "folder",
                        }
                    )
                    continue
                if not p.is_file():
                    continue
                out.append(
                    {
                        "abs_path": str(p),
                        "rel_path": rel,
                        "path_key": _norm_path(rel).lower(),
                        "branch_type": "GENERAL",
                        "checksum": None,
                        "entry_type": "file",
                    }
                )

    # KIWIX: always compare by documents.json values (not by filesystem tree).
    if "KIWIX" in SYNC_ALLOWED_BRANCH_TYPES and SYNC_KIWIX_DOCUMENTS_JSON.exists():
        try:
            raw = json.loads(SYNC_KIWIX_DOCUMENTS_JSON.read_text(encoding="utf-8"))
            rows = raw if isinstance(raw, list) else []
        except Exception:
            rows = []

        for r in rows:
            if not isinstance(r, dict):
                continue
            platform = str(r.get("platform") or "").strip().upper()
            if platform and platform != "KIWIX":
                continue

            prj_name = str(r.get("prjName") or "").strip()
            prj_code = str(r.get("prjCode") or "").strip()
            doc_code = str(r.get("docCode") or "").strip()
            file_name = str(r.get("fileName") or "").strip()
            file_ext = str(r.get("fileExt") or "").strip().lstrip(".")
            if not prj_code or not file_name:
                continue

            display_name = _build_display_name(file_name, file_ext)
            sub_path = f"{doc_code}_{file_name}" if doc_code else file_name
            rel = _join_path(prj_code, sub_path, display_name)

            out.append(
                {
                    "abs_path": "",
                    "rel_path": rel,
                    "path_key": _norm_path(rel).lower(),
                    "branch_type": "KIWIX",
                    "checksum": "",
                    "section": r.get("section"),
                    "prj_name": prj_name,
                    "prj_code": prj_code,
                    "doc_code": doc_code,
                    "file_name": file_name,
                    "file_ext": file_ext,
                    "file_create": r.get("fileCreate"),
                    "entry_type": "file",
                }
            )

    return out


def _local_meta_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "section": item.get("section"),
        "prj_name": item.get("prj_name"),
        "prj_code": item.get("prj_code"),
        "doc_code": item.get("doc_code"),
        "file_name": item.get("file_name"),
        "file_ext": item.get("file_ext"),
        "file_create": item.get("file_create"),
    }


def _load_db_folder_items(kb_id: int) -> List[Dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT
            node_id,
            parent_id,
            name,
            UPPER(COALESCE(branch_type, 'GENERAL')) AS branch_type,
            updated_at
          FROM tree_nodes
         WHERE tree_id=%s
           AND is_folder=1
        """,
        (kb_id,),
    )

    by_id: Dict[int, Dict[str, Any]] = {}
    for r in rows or []:
        nid = r.get("node_id")
        try:
            if nid is not None:
                by_id[int(nid)] = r
        except Exception:
            continue

    path_cache: Dict[int, str] = {}
    visiting: set[int] = set()

    def _resolve_folder_path(node_id: int) -> str:
        if node_id in path_cache:
            return path_cache[node_id]
        if node_id in visiting:
            return ""
        visiting.add(node_id)
        try:
            row = by_id.get(node_id) or {}
            name = str(row.get("name") or "").strip()
            if not name:
                path_cache[node_id] = ""
                return ""

            parent_id = row.get("parent_id")
            parent_path = ""
            try:
                if parent_id is not None:
                    parent_int = int(parent_id)
                    if parent_int in by_id:
                        parent_path = _resolve_folder_path(parent_int)
            except Exception:
                parent_path = ""

            path_cache[node_id] = _join_path(parent_path, name)
            return path_cache[node_id]
        finally:
            visiting.discard(node_id)

    out: List[Dict[str, Any]] = []
    for node_id, row in by_id.items():
        branch_type = str(row.get("branch_type") or "GENERAL").upper()
        # Folder compare is only enabled for GENERAL branch.
        if branch_type != "GENERAL":
            continue
        rel_path = _resolve_folder_path(node_id)
        if not rel_path:
            continue
        out.append(
            {
                "node_id": int(node_id),
                "doc_id": None,
                "object_id": None,
                "branch_type": branch_type,
                "rel_path": rel_path,
                "path_key": _norm_path(rel_path).lower(),
                "checksum": "",
                "updated_at": row.get("updated_at"),
                "entry_type": "folder",
            }
        )
    return out


def _load_db_items(kb_id: int) -> List[Dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT
            n.node_id,
            UPPER(COALESCE(n.branch_type, 'GENERAL')) AS branch_type,
            pfn.doc_master_id,
            dr.storage_object_id AS object_id,
            so.name,
            so.file_right,
            so.relative_path,
            so.sub_path,
            so.checksum_sha256,
            so.updated_at
          FROM tree_nodes n
          LEFT JOIN pf_node_props pfn ON pfn.node_id = n.node_id
          LEFT JOIN doc_revisions dr
            ON dr.revision_id = (
              SELECT r.revision_id
                FROM doc_revisions r
               WHERE r.doc_master_id=pfn.doc_master_id AND r.deleted_at IS NULL
               ORDER BY r.rev_no DESC
               LIMIT 1
            )
          LEFT JOIN storage_objects so ON so.object_id = dr.storage_object_id
         WHERE n.tree_id=%s
           AND n.is_folder=0
        """,
        (kb_id,),
    )

    out: List[Dict[str, Any]] = []
    derived_general_folders: Dict[str, Any] = {}
    for r in rows or []:
        file_name = _build_display_name(r.get("name"), r.get("file_right"))
        rel_path = _join_path(r.get("relative_path") or "", r.get("sub_path") or "", file_name)
        if not rel_path:
            # Conservative: skip rows without concrete path evidence.
            continue
        branch_type = str(r.get("branch_type") or "GENERAL").upper()
        out.append(
            {
                "node_id": r.get("node_id"),
                "doc_id": r.get("doc_master_id"),
                "object_id": r.get("object_id"),
                "branch_type": branch_type,
                "rel_path": rel_path,
                "path_key": _norm_path(rel_path).lower(),
                "checksum": (r.get("checksum_sha256") or "").strip().lower(),
                "updated_at": r.get("updated_at"),
                "entry_type": "file",
            }
        )
        if branch_type == "GENERAL":
            for folder_path in _folder_ancestors(rel_path):
                if folder_path and folder_path not in derived_general_folders:
                    derived_general_folders[folder_path] = r.get("updated_at")

    folder_items_by_key: Dict[str, Dict[str, Any]] = {}
    for folder_path in sorted(derived_general_folders):
        key = _norm_path(folder_path).lower()
        if not key:
            continue
        folder_items_by_key[key] = {
            "node_id": None,
            "doc_id": None,
            "object_id": None,
            "branch_type": "GENERAL",
            "rel_path": folder_path,
            "path_key": key,
            "checksum": "",
            "updated_at": derived_general_folders.get(folder_path),
            "entry_type": "folder",
        }

    # Prefer actual DB folder nodes when present (covers empty folders too).
    for item in _load_db_folder_items(kb_id):
        key = str(item.get("path_key") or "").lower()
        if not key:
            continue
        folder_items_by_key[key] = item

    for key in sorted(folder_items_by_key):
        out.append(folder_items_by_key[key])
    return out


def _find_by_checksum(local_items: List[Dict[str, Any]], checksum: str) -> Optional[int]:
    csum = str(checksum or "").strip().lower()
    if not csum:
        return None
    for i, item in enumerate(local_items):
        if item.get("checksum") is None:
            try:
                item["checksum"] = _sha256_file(Path(item["abs_path"]))
            except Exception:
                item["checksum"] = ""
        if item.get("checksum") == csum:
            return i
    return None


def _is_sync_allowed_node(kb_id: int, node_id: int) -> bool:
    allowed = tuple(sorted(SYNC_TRIGGER_BRANCH_TYPES))
    placeholders = ",".join(["%s"] * len(allowed))
    row = fetch_one(
        f"""
        SELECT 1 AS ok
          FROM tree_nodes
         WHERE tree_id=%s
           AND node_id=%s
           AND COALESCE(UPPER(branch_type), 'GENERAL') IN ({placeholders})
         LIMIT 1
        """,
        (kb_id, node_id, *allowed),
    )
    return bool(row and int(row.get("ok") or 0) == 1)


def _is_sync_allowed_object(kb_id: int, object_id: int) -> bool:
    allowed = tuple(sorted(SYNC_TRIGGER_BRANCH_TYPES))
    placeholders = ",".join(["%s"] * len(allowed))
    row = fetch_one(
        f"""
        SELECT 1 AS ok
          FROM tree_nodes n
          JOIN pf_node_props pfn ON pfn.node_id = n.node_id
          JOIN doc_revisions dr ON dr.doc_master_id = pfn.doc_master_id
         WHERE n.tree_id=%s
           AND dr.storage_object_id=%s
           AND dr.deleted_at IS NULL
           AND COALESCE(UPPER(n.branch_type), 'GENERAL') IN ({placeholders})
         LIMIT 1
        """,
        (kb_id, object_id, *allowed),
    )
    return bool(row and int(row.get("ok") or 0) == 1)


def _build_preview_items(kb_id: int) -> List[Dict[str, Any]]:
    db_items = [d for d in _load_db_items(kb_id) if str(d.get("branch_type") or "").upper() in SYNC_ALLOWED_BRANCH_TYPES]
    local_items = [l for l in _scan_local_files() if str(l.get("branch_type") or "").upper() in SYNC_ALLOWED_BRANCH_TYPES]

    db_file_items = [d for d in db_items if str(d.get("entry_type") or "file").lower() != "folder"]
    db_folder_items = [
        d
        for d in db_items
        if str(d.get("entry_type") or "").lower() == "folder"
        and str(d.get("branch_type") or "").upper() == "GENERAL"
    ]
    local_file_items = [l for l in local_items if str(l.get("entry_type") or "file").lower() != "folder"]
    local_folder_items = [
        l
        for l in local_items
        if str(l.get("entry_type") or "").lower() == "folder"
        and str(l.get("branch_type") or "").upper() == "GENERAL"
    ]

    local_by_key: Dict[str, Dict[str, Any]] = {}
    for l in local_file_items:
        composite = f"{l['branch_type']}|{l['path_key']}"
        if composite not in local_by_key:
            local_by_key[composite] = l

    used_local_keys = set()
    preview: List[Dict[str, Any]] = []
    folder_rename_votes: Dict[tuple[str, str], int] = {}
    folder_rename_samples: Dict[tuple[str, str], tuple[str, str]] = {}

    # Pass 1: exact key match, only metadata mismatch becomes edit.
    for d in db_file_items:
        composite = f"{d['branch_type']}|{d['path_key']}"
        l = local_by_key.get(composite)
        if not l:
            continue
        used_local_keys.add(composite)
        # Exact path+branch matched: treat as unchanged in conservative mode.

    # Pass 2: db-only entries -> try checksum rename detection, else delete.
    remaining_local = [l for l in local_file_items if f"{l['branch_type']}|{l['path_key']}" not in used_local_keys]

    for d in db_file_items:
        composite = f"{d['branch_type']}|{d['path_key']}"
        if composite in local_by_key:
            continue

        idx = _find_by_checksum(remaining_local, d.get("checksum") or "")
        if idx is not None:
            l = remaining_local.pop(idx)
            used_local_keys.add(f"{l['branch_type']}|{l['path_key']}")
            preview.append(
                {
                    "id": d.get("object_id") or d.get("doc_id") or d.get("node_id"),
                    "object_id": d.get("object_id"),
                    "doc_id": d.get("doc_id"),
                    "node_id": d.get("node_id"),
                    "action": "edit",
                    "entry_type": "file",
                    "apply_supported": True,
                    "branch_type": d.get("branch_type") or l.get("branch_type") or "GENERAL",
                    "target_path": l.get("rel_path") or "-",
                    "source_path": d.get("rel_path") or "-",
                    "change": "path_mismatch",
                    "reason": f"checksum_match: DB({d.get('rel_path')}) -> LOCAL({l.get('rel_path')})",
                    "updated_at": d.get("updated_at"),
                    **_local_meta_fields(l),
                }
            )
            if str(d.get("branch_type") or "").upper() == "GENERAL":
                src_dir = _dir_path_from_file_path(d.get("rel_path"))
                dst_dir = _dir_path_from_file_path(l.get("rel_path"))
                if src_dir and dst_dir and src_dir.lower() != dst_dir.lower():
                    vote_key = (src_dir.lower(), dst_dir.lower())
                    folder_rename_votes[vote_key] = folder_rename_votes.get(vote_key, 0) + 1
                    if vote_key not in folder_rename_samples:
                        folder_rename_samples[vote_key] = (src_dir, dst_dir)
        else:
            preview.append(
                {
                    "id": d.get("object_id") or d.get("doc_id") or d.get("node_id"),
                    "object_id": d.get("object_id"),
                    "doc_id": d.get("doc_id"),
                    "node_id": d.get("node_id"),
                    "action": "delete",
                    "entry_type": "file",
                    "apply_supported": True,
                    "branch_type": d.get("branch_type") or "GENERAL",
                    "target_path": d.get("rel_path") or "-",
                    "change": "remove",
                    "reason": "missing_in_local",
                    "updated_at": d.get("updated_at"),
                }
            )

    # Pass 3: local-only entries -> duplicate-path or add.
    db_by_checksum: Dict[str, List[Dict[str, Any]]] = {}
    for d in db_file_items:
        csum = str(d.get("checksum") or "").strip().lower()
        if not csum:
            continue
        db_by_checksum.setdefault(csum, []).append(d)

    for l in remaining_local:
        if l.get("checksum") is None:
            try:
                l["checksum"] = _sha256_file(Path(l["abs_path"]))
            except Exception:
                l["checksum"] = ""

        csum = str(l.get("checksum") or "").strip().lower()
        same_content = db_by_checksum.get(csum, []) if csum else []

        if same_content:
            ref_paths = [str(x.get("rel_path") or "-") for x in same_content[:3]]
            ref_doc_ids = [x.get("doc_id") for x in same_content if x.get("doc_id") is not None]
            preview.append(
                {
                    "id": l.get("rel_path"),
                    "action": "duplicate_path",
                    "entry_type": "file",
                    "apply_supported": True,
                    "branch_type": l.get("branch_type") or "GENERAL",
                    "target_path": l.get("rel_path") or "-",
                    "change": "same_content_multi_path",
                    "reason": "same_checksum_in_db",
                    "same_checksum": csum,
                    "same_doc_ids": sorted(list(set(ref_doc_ids))),
                    "db_paths": ref_paths,
                    "updated_at": "-",
                    **_local_meta_fields(l),
                }
            )
        else:
            preview.append(
                {
                    "id": l.get("rel_path"),
                    "action": "add",
                    "entry_type": "file",
                    "apply_supported": True,
                    "branch_type": l.get("branch_type") or "GENERAL",
                    "target_path": l.get("rel_path") or "-",
                    "change": "insert",
                    "reason": "missing_in_db",
                    "updated_at": "-",
                    **_local_meta_fields(l),
                }
            )

    # Pass 4: GENERAL folder-level comparison.
    db_folder_by_key: Dict[str, Dict[str, Any]] = {}
    for d in db_folder_items:
        composite = f"{d['branch_type']}|{d['path_key']}"
        if composite not in db_folder_by_key:
            db_folder_by_key[composite] = d

    local_folder_by_key: Dict[str, Dict[str, Any]] = {}
    for l in local_folder_items:
        composite = f"{l['branch_type']}|{l['path_key']}"
        if composite not in local_folder_by_key:
            local_folder_by_key[composite] = l

    db_only_folder_keys = set(db_folder_by_key.keys()) - set(local_folder_by_key.keys())
    local_only_folder_keys = set(local_folder_by_key.keys()) - set(db_folder_by_key.keys())

    for vote_key, vote_count in sorted(folder_rename_votes.items(), key=lambda x: x[1], reverse=True):
        src_dir, dst_dir = folder_rename_samples.get(vote_key, (vote_key[0], vote_key[1]))
        src_comp = f"GENERAL|{_norm_path(src_dir).lower()}"
        dst_comp = f"GENERAL|{_norm_path(dst_dir).lower()}"
        if src_comp not in db_only_folder_keys or dst_comp not in local_only_folder_keys:
            continue
        db_folder = db_folder_by_key.get(src_comp)
        local_folder = local_folder_by_key.get(dst_comp)
        if not db_folder or not local_folder:
            continue
        preview.append(
            {
                "id": f"folder:{db_folder.get('rel_path')}",
                "action": "edit",
                "entry_type": "folder",
                "apply_supported": False,
                "branch_type": "GENERAL",
                "target_path": local_folder.get("rel_path") or "-",
                "source_path": db_folder.get("rel_path") or "-",
                "change": "folder_path_mismatch",
                "reason": f"folder_rename_by_children: DB({db_folder.get('rel_path')}) -> LOCAL({local_folder.get('rel_path')}), matched_files={vote_count}",
                "updated_at": db_folder.get("updated_at"),
            }
        )
        db_only_folder_keys.discard(src_comp)
        local_only_folder_keys.discard(dst_comp)

    for comp in sorted(db_only_folder_keys):
        d = db_folder_by_key[comp]
        preview.append(
            {
                "id": f"folder:{d.get('rel_path')}",
                "action": "delete",
                "entry_type": "folder",
                "apply_supported": False,
                "branch_type": "GENERAL",
                "target_path": d.get("rel_path") or "-",
                "change": "remove",
                "reason": "missing_in_local_folder",
                "updated_at": d.get("updated_at"),
            }
        )

    for comp in sorted(local_only_folder_keys):
        l = local_folder_by_key[comp]
        preview.append(
            {
                "id": f"folder:{l.get('rel_path')}",
                "action": "add",
                "entry_type": "folder",
                "apply_supported": False,
                "branch_type": "GENERAL",
                "target_path": l.get("rel_path") or "-",
                "change": "insert",
                "reason": "missing_in_db_folder",
                "updated_at": "-",
            }
        )

    # De-duplicate by (action, branch, target_path)
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for it in preview:
        key = (
            it.get("action"),
            it.get("branch_type"),
            str(it.get("entry_type") or "file").lower(),
            _norm_path(it.get("target_path") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    # Stable order
    uniq.sort(
        key=lambda x: (
            str(x.get("action") or ""),
            str(x.get("branch_type") or ""),
            str(x.get("entry_type") or ""),
            str(x.get("target_path") or ""),
        )
    )
    return uniq


def _item_key(item: Dict[str, Any]) -> tuple:
    return (
        str(item.get("action") or "").strip().lower(),
        str(item.get("branch_type") or "").strip().upper(),
        _norm_path(str(item.get("target_path") or "")).lower(),
        str(item.get("change") or "").strip().lower(),
    )


def _affected_tables_for_change(change: str) -> List[str]:
    c = str(change or "").strip().lower()
    if c in {"path_mismatch", "edit", "update", "folder_path_mismatch"}:
        return ["storage_objects", "doc_revisions", "doc_master", "pf_node_props", "tree_nodes"]
    if c in {"remove", "delete", "missing_in_local_folder"}:
        return ["tree_nodes", "pf_node_props", "doc_revisions", "doc_master", "storage_objects"]
    if c in {"insert", "add", "missing_in_db_folder"}:
        return ["tree_nodes", "pf_node_props", "doc_master", "doc_revisions", "storage_objects"]
    if c in {"same_content_multi_path", "duplicate_path"}:
        return ["tree_nodes", "pf_node_props", "doc_master", "doc_revisions"]
    return ["tree_nodes", "pf_node_props", "doc_master", "doc_revisions", "storage_objects"]


def _normalize_title(title: str) -> str:
    return " ".join(str(title or "").strip().lower().split())


def _split_target_path(target_path: str) -> tuple[List[str], str, str, Optional[str]]:
    norm = _norm_path(target_path)
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return [], "file", "file", None
    filename = parts[-1]
    dirs = parts[:-1]
    if "." in filename:
        base, ext = filename.rsplit(".", 1)
        base = base.strip() or filename
        ext = ext.strip().lower() or None
        return dirs, filename, base, ext
    return dirs, filename, filename, None


def _parse_file_create(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None

    candidates = [
        raw,
        raw.replace("/", "-"),
    ]
    for c in candidates:
        if "T" in c:
            try:
                dt = datetime.fromisoformat(c.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except Exception:
                pass

        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(c, fmt)
            except Exception:
                continue
    return None


def _kiwix_tree_dirs(storage_dirs: List[str], item: Dict[str, Any]) -> List[str]:
    if not storage_dirs:
        return []
    prj_name = str(item.get("prj_name") or "").replace("\\", " ").replace("/", " ").strip()
    prj_code = str(item.get("prj_code") or "").replace("\\", " ").replace("/", " ").strip()
    if not prj_code and storage_dirs:
        prj_code = str(storage_dirs[0] or "").strip()

    if prj_name and prj_code:
        top = f"{prj_name}_{prj_code}"
    elif prj_name:
        top = prj_name
    elif prj_code:
        top = prj_code
    else:
        top = ""

    if not top:
        return list(storage_dirs)
    return [top, *storage_dirs[1:]]


def _kiwix_storage_root(storage_dirs: List[str], item: Dict[str, Any]) -> Optional[str]:
    prj_code = str(item.get("prj_code") or "").strip()
    if prj_code:
        return prj_code
    if storage_dirs:
        return storage_dirs[0]
    return None


def _safe_general_local_abs(target_path: str) -> Optional[Path]:
    root = SYNC_LOCAL_BASE_DIRS.get("GENERAL")
    if not root:
        return None
    rel = _norm_path(target_path)
    if not rel:
        return None
    candidate = (root / Path(rel)).resolve()
    root_abs = root.resolve()
    if str(candidate).lower() == str(root_abs).lower() or not str(candidate).lower().startswith(str(root_abs).lower() + os.sep.lower()):
        return None
    return candidate


def _kiwix_tree_ids_for_mirror(kb_id: int) -> List[int]:
    ids = {int(kb_id)}
    rows = fetch_all(
        """
        SELECT tree_id
          FROM trees
         WHERE kind='KB'
           AND LOWER(TRIM(name)) LIKE '%%kiwix%%'
        """,
    )
    for r in rows or []:
        tid = r.get("tree_id")
        if tid is None:
            continue
        try:
            ids.add(int(tid))
        except Exception:
            continue
    if SE_KB_ID:
        ids.add(int(SE_KB_ID))
    return sorted(ids)


def _apply_local_general_mirror(action: str, target_path: str) -> Dict[str, Any]:
    abs_path = _safe_general_local_abs(target_path)
    if not abs_path:
        return {"applied": False, "reason": "invalid_target_path"}
    act = str(action or "").strip().lower()
    if act in {"add", "duplicate_path", "edit", "update"}:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.touch(exist_ok=True)
        return {"applied": True, "op": "ensure_file", "path": str(abs_path)}
    if act in {"delete", "remove"}:
        if abs_path.exists() and abs_path.is_file():
            abs_path.unlink()
            return {"applied": True, "op": "remove_file", "path": str(abs_path)}
        return {"applied": False, "op": "remove_file", "reason": "file_not_found", "path": str(abs_path)}
    return {"applied": False, "reason": "unsupported_action"}


def _ensure_tree_node_acl_binding(
    node_id: int,
    tree_id: int,
    parent_id: Optional[int],
    user_id: int,
) -> None:
    node_id = int(node_id)
    exists = fetch_one(
        """
        SELECT acl_id
          FROM acl_bindings
         WHERE resource_type='TREE_NODE'
           AND resource_pk=%s
         LIMIT 1
        """,
        (node_id,),
    )
    if exists:
        return

    parent_acl: Optional[Dict[str, Any]] = None
    if parent_id is not None:
        parent_acl = fetch_one(
            """
            SELECT org_id, policy_code, owner_user_id, owner_group_id
              FROM acl_bindings
             WHERE resource_type='TREE_NODE'
               AND resource_pk=%s
             LIMIT 1
            """,
            (int(parent_id),),
        )

    if parent_acl:
        execute(
            """
            INSERT IGNORE INTO acl_bindings
                (resource_type, resource_pk, org_id, policy_code, owner_user_id, owner_group_id)
            VALUES ('TREE_NODE', %s, %s, %s, %s, %s)
            """,
            (
                node_id,
                int(parent_acl["org_id"]),
                str(parent_acl["policy_code"]),
                parent_acl.get("owner_user_id"),
                parent_acl.get("owner_group_id"),
            ),
        )
        return

    tree = fetch_one("SELECT org_id FROM trees WHERE tree_id=%s LIMIT 1", (int(tree_id),))
    if not tree or tree.get("org_id") is None:
        return
    execute(
        """
        INSERT IGNORE INTO acl_bindings
            (resource_type, resource_pk, org_id, policy_code, owner_user_id, owner_group_id)
        VALUES ('TREE_NODE', %s, %s, 'S3', %s, NULL)
        """,
        (node_id, int(tree["org_id"]), int(user_id)),
    )


def _ensure_folder_chain(
    tree_id: int,
    dirs: List[str],
    user_id: int,
    branch_type: str = "GENERAL",
) -> Optional[int]:
    parent_id: Optional[int] = None
    bt = str(branch_type or "GENERAL").strip().upper()
    for name in dirs:
        prev_parent = parent_id
        row = fetch_one(
            """
            SELECT node_id
              FROM tree_nodes
             WHERE tree_id=%s
               AND is_folder=1
               AND parent_id <=> %s
               AND name=%s
               AND COALESCE(UPPER(branch_type), 'GENERAL')=%s
             LIMIT 1
            """,
            (tree_id, parent_id, name, bt),
        )
        if row and row.get("node_id") is not None:
            parent_id = int(row["node_id"])
            _ensure_tree_node_acl_binding(parent_id, tree_id, prev_parent, user_id)
            continue
        parent_id = execute(
            """
            INSERT INTO tree_nodes(tree_id, parent_id, is_folder, node_type, name, sort_order, created_by, created_at, updated_at, branch_type)
            VALUES(%s,%s,1,'KB_FOLDER',%s,0,%s,NOW(),NOW(),%s)
            """,
            (tree_id, prev_parent, name, user_id, bt),
            True,
        )
        _ensure_tree_node_acl_binding(int(parent_id), tree_id, prev_parent, user_id)
    return parent_id


def _find_kiwix_root_node_id(tree_id: int) -> Optional[int]:
    # KIWIX branch is anchored under a root folder whose name contains "kiwix".
    row = fetch_one(
        """
        SELECT node_id
          FROM tree_nodes
         WHERE tree_id=%s
            AND is_folder=1
           AND parent_id IS NULL
           AND LOWER(TRIM(name)) LIKE '%%kiwix%%'
         ORDER BY node_id
         LIMIT 1
        """,
        (tree_id,),
    )
    if not row:
        # Backward-compatible fallback: allow nested "kiwix" folders when no root-level match exists.
        row = fetch_one(
            """
            SELECT node_id
              FROM tree_nodes
             WHERE tree_id=%s
               AND is_folder=1
               AND LOWER(TRIM(name)) LIKE '%%kiwix%%'
             ORDER BY node_id
             LIMIT 1
            """,
            (tree_id,),
        )
    if row and row.get("node_id") is not None:
        return int(row["node_id"])
    return None


def _ensure_folder_chain_from_parent(
    tree_id: int,
    parent_id: Optional[int],
    dirs: List[str],
    user_id: int,
    branch_type: str = "GENERAL",
) -> Optional[int]:
    cur_parent = parent_id
    bt = str(branch_type or "GENERAL").strip().upper()
    for name in dirs:
        prev_parent = cur_parent
        row = fetch_one(
            """
            SELECT node_id
              FROM tree_nodes
             WHERE tree_id=%s
               AND is_folder=1
               AND parent_id <=> %s
               AND name=%s
               AND COALESCE(UPPER(branch_type), 'GENERAL')=%s
             LIMIT 1
            """,
            (tree_id, cur_parent, name, bt),
        )
        if row and row.get("node_id") is not None:
            cur_parent = int(row["node_id"])
            _ensure_tree_node_acl_binding(cur_parent, tree_id, prev_parent, user_id)
            continue
        cur_parent = execute(
            """
            INSERT INTO tree_nodes(tree_id, parent_id, is_folder, node_type, name, sort_order, created_by, created_at, updated_at, branch_type)
            VALUES(%s,%s,1,'KB_FOLDER',%s,0,%s,NOW(),NOW(),%s)
            """,
            (tree_id, prev_parent, name, user_id, bt),
            True,
        )
        _ensure_tree_node_acl_binding(int(cur_parent), tree_id, prev_parent, user_id)
    return cur_parent


def _ensure_tree_file_node(
    tree_id: int,
    parent_id: Optional[int],
    name: str,
    branch_type: str,
    user_id: int,
    created_at: Optional[datetime] = None,
) -> int:
    row = fetch_one(
        """
        SELECT node_id
          FROM tree_nodes
         WHERE tree_id=%s
           AND is_folder=0
           AND parent_id <=> %s
           AND name=%s
           AND COALESCE(UPPER(branch_type), 'GENERAL')=%s
         LIMIT 1
        """,
        (tree_id, parent_id, name, branch_type),
    )
    if row and row.get("node_id") is not None:
        node_id = int(row["node_id"])
        _ensure_tree_node_acl_binding(node_id, tree_id, parent_id, user_id)
        return node_id
    if created_at is None:
        node_id = int(
            execute(
                """
                INSERT INTO tree_nodes(tree_id, parent_id, is_folder, node_type, name, sort_order, created_by, created_at, updated_at, branch_type)
                VALUES(%s,%s,0,'file',%s,0,%s,NOW(),NOW(),%s)
                """,
                (tree_id, parent_id, name, user_id, branch_type),
                True,
            )
        )
        _ensure_tree_node_acl_binding(node_id, tree_id, parent_id, user_id)
        return node_id

    node_id = int(
        execute(
            """
            INSERT INTO tree_nodes(tree_id, parent_id, is_folder, node_type, name, sort_order, created_by, created_at, updated_at, branch_type)
            VALUES(%s,%s,0,'file',%s,0,%s,%s,NOW(),%s)
            """,
            (tree_id, parent_id, name, user_id, created_at, branch_type),
            True,
        )
    )
    _ensure_tree_node_acl_binding(node_id, tree_id, parent_id, user_id)
    return node_id


def _resolve_doc_master_from_node(node_id: int) -> Optional[int]:
    row = fetch_one("SELECT doc_master_id FROM pf_node_props WHERE node_id=%s LIMIT 1", (node_id,))
    if not row or row.get("doc_master_id") is None:
        return None
    return int(row["doc_master_id"])


def _resolve_latest_object_from_doc(doc_id: int) -> Optional[int]:
    row = fetch_one(
        """
        SELECT storage_object_id
          FROM doc_revisions
         WHERE doc_master_id=%s
           AND deleted_at IS NULL
         ORDER BY rev_no DESC, revision_id DESC
         LIMIT 1
        """,
        (doc_id,),
    )
    if not row or row.get("storage_object_id") is None:
        return None
    return int(row["storage_object_id"])


def _resolve_doc_by_storage_path(
    root_rel: Optional[str],
    sub_rel: Optional[str],
    base_name: str,
    ext: Optional[str],
) -> Optional[int]:
    row = fetch_one(
        """
        SELECT r.doc_master_id
          FROM storage_objects so
          JOIN doc_revisions r
            ON r.storage_object_id = so.object_id
           AND r.deleted_at IS NULL
         WHERE so.relative_path <=> %s
           AND so.sub_path <=> %s
           AND so.name=%s
           AND so.file_right <=> %s
         ORDER BY r.rev_no DESC, r.revision_id DESC
         LIMIT 1
        """,
        (root_rel, sub_rel, base_name, ext),
    )
    if not row or row.get("doc_master_id") is None:
        return None
    return int(row["doc_master_id"])


def _upsert_node_doc_link(node_id: int, doc_id: int) -> None:
    execute(
        """
        INSERT INTO pf_node_props(node_id, doc_master_id, alias_name)
        VALUES(%s,%s,NULL)
        ON DUPLICATE KEY UPDATE doc_master_id=VALUES(doc_master_id), alias_name=VALUES(alias_name)
        """,
        (node_id, doc_id),
    )


def _cleanup_orphan_doc(doc_id: int) -> None:
    ref = fetch_one("SELECT 1 AS ok FROM pf_node_props WHERE doc_master_id=%s LIMIT 1", (doc_id,))
    if ref:
        return
    objs = fetch_all("SELECT storage_object_id FROM doc_revisions WHERE doc_master_id=%s", (doc_id,))
    execute("DELETE FROM doc_revisions WHERE doc_master_id=%s", (doc_id,))
    execute("DELETE FROM doc_master WHERE doc_master_id=%s", (doc_id,))
    object_ids = [int(r["storage_object_id"]) for r in objs or [] if r.get("storage_object_id") is not None]
    if object_ids:
        ph = ",".join(["%s"] * len(object_ids))
        execute(f"DELETE FROM doc_props WHERE object_id IN ({ph})", tuple(object_ids))
        execute(f"DELETE FROM storage_objects WHERE object_id IN ({ph})", tuple(object_ids))


def _node_in_tree(kb_id: int, node_id: int, branch_type: str) -> bool:
    row = fetch_one(
        """
        SELECT node_id
          FROM tree_nodes
         WHERE tree_id=%s
           AND node_id=%s
           AND is_folder=0
           AND COALESCE(UPPER(branch_type), 'GENERAL')=%s
         LIMIT 1
        """,
        (kb_id, node_id, branch_type),
    )
    return bool(row and row.get("node_id") is not None)


def _resolve_node_by_target_path(kb_id: int, item: Dict[str, Any], branch_type: str) -> Optional[int]:
    target_path = str(item.get("target_path") or "")
    dirs, display_name, _, _ = _split_target_path(target_path)

    candidates: List[List[str]] = []
    if branch_type == "KIWIX":
        mapped = _kiwix_tree_dirs(dirs, item)
        candidates.append(mapped)
        if mapped != dirs:
            candidates.append(list(dirs))
    else:
        candidates.append(list(dirs))

    seen = set()
    for cand in candidates:
        key = tuple(cand)
        if key in seen:
            continue
        seen.add(key)

        parent_id: Optional[int] = None
        if branch_type == "KIWIX":
            kiwix_root_id = _find_kiwix_root_node_id(kb_id)
            if kiwix_root_id is None:
                continue
            parent_id = int(kiwix_root_id)

        ok = True
        for name in cand:
            row = fetch_one(
                """
                SELECT node_id
                  FROM tree_nodes
                 WHERE tree_id=%s
                   AND is_folder=1
                   AND parent_id <=> %s
                   AND name=%s
                   AND COALESCE(UPPER(branch_type), 'GENERAL')=%s
                 LIMIT 1
                """,
                (kb_id, parent_id, name, branch_type),
            )
            if not row or row.get("node_id") is None:
                ok = False
                break
            parent_id = int(row["node_id"])

        if not ok:
            continue

        row = fetch_one(
            """
            SELECT node_id
              FROM tree_nodes
             WHERE tree_id=%s
               AND is_folder=0
               AND parent_id <=> %s
               AND name=%s
               AND COALESCE(UPPER(branch_type), 'GENERAL')=%s
             LIMIT 1
            """,
            (kb_id, parent_id, display_name, branch_type),
        )
        if row and row.get("node_id") is not None:
            return int(row["node_id"])

    return None


def _resolve_delete_node_ids(kb_id: int, item: Dict[str, Any], doc_id: Optional[int], branch_type: str) -> List[int]:
    out: List[int] = []
    raw_node_id = item.get("node_id")
    if raw_node_id is not None:
        try:
            nid = int(raw_node_id)
            if _node_in_tree(kb_id, nid, branch_type):
                out.append(nid)
        except Exception:
            pass

    node_by_path = _resolve_node_by_target_path(kb_id, item, branch_type)
    if node_by_path is not None and node_by_path not in out:
        out.append(node_by_path)

    # Fallback: if explicit/path-based node lookup failed, delete all matching doc links in this tree.
    if not out and doc_id is not None:
        rows = fetch_all(
            """
            SELECT n.node_id
              FROM tree_nodes n
              JOIN pf_node_props p ON p.node_id = n.node_id
             WHERE n.tree_id=%s
               AND n.is_folder=0
               AND p.doc_master_id=%s
               AND COALESCE(UPPER(n.branch_type), 'GENERAL')=%s
            """,
            (kb_id, int(doc_id), branch_type),
        )
        for r in rows or []:
            nid = r.get("node_id")
            if nid is None:
                continue
            try:
                nid_int = int(nid)
            except Exception:
                continue
            if nid_int not in out:
                out.append(nid_int)

    return out


def _doc_type_from_ext(ext: Optional[str]) -> str:
    e = str(ext or "").lower()
    if e == "pdf":
        return "PDF"
    if e in {"doc", "docx"}:
        return "DOC"
    if e in {"ppt", "pptx"}:
        return "PPT"
    if e in {"xls", "xlsx", "csv"}:
        return "XLS"
    if e == "md":
        return "MD"
    return "OTHER"


def _apply_edit_item(kb_id: int, item: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    item_id = item.get("id")
    doc_id = item.get("doc_id")
    node_id = item.get("node_id")
    object_id = item.get("object_id")

    if object_id is None and isinstance(item_id, int):
        object_id = item_id
    if object_id is None and doc_id is not None:
        object_id = _resolve_latest_object_from_doc(int(doc_id))
    if object_id is None and node_id is not None:
        linked_doc = _resolve_doc_master_from_node(int(node_id))
        if linked_doc is not None:
            object_id = _resolve_latest_object_from_doc(linked_doc)

    if object_id is None:
        raise HTTPException(status_code=400, detail=f"Unable to resolve object_id for edit item: {item.get('target_path')}")

    branch_type = str(item.get("branch_type") or "GENERAL").upper()
    dirs, display_name, base_name, ext = _split_target_path(str(item.get("target_path") or ""))
    file_created_at = _parse_file_create(item.get("file_create"))
    tree_dirs = list(dirs)
    root_rel = dirs[0] if dirs else None
    if branch_type == "KIWIX":
        tree_dirs = _kiwix_tree_dirs(dirs, item)
        root_rel = _kiwix_storage_root(dirs, item)
    sub_rel = "/".join(dirs[1:]) if len(dirs) > 1 else None

    if branch_type == "KIWIX":
        kiwix_root_id = _find_kiwix_root_node_id(kb_id)
        if kiwix_root_id is None:
            raise HTTPException(status_code=400, detail='Cannot find a root folder containing "KIWIX" for KIWIX sync.')
        parent_id = _ensure_folder_chain_from_parent(kb_id, kiwix_root_id, tree_dirs, user_id, branch_type=branch_type)
    else:
        parent_id = _ensure_folder_chain(kb_id, tree_dirs, user_id, branch_type=branch_type)

    if branch_type == "KIWIX":
        doc_code = str(item.get("prj_code") or "").strip() or str(root_rel or "")
        execute(
            """
            UPDATE storage_objects
               SET name=%s,
                   file_right=%s,
                   doc_code=%s,
                   relative_path=%s,
                   sub_path=%s,
                   mime_type=%s
             WHERE object_id=%s
            """,
            (
                base_name,
                ext,
                doc_code,
                root_rel,
                sub_rel,
                mimetypes.guess_type(display_name)[0] or "application/octet-stream",
                int(object_id),
            ),
        )
    else:
        execute(
            """
            UPDATE storage_objects
               SET name=%s,
                   file_right=%s,
                   relative_path=%s,
                   sub_path=%s,
                   mime_type=%s
             WHERE object_id=%s
            """,
            (
                base_name,
                ext,
                root_rel,
                sub_rel,
                mimetypes.guess_type(display_name)[0] or "application/octet-stream",
                int(object_id),
            ),
        )

    target_node_id = int(node_id) if node_id is not None else None
    if target_node_id is None:
        target_node_id = _ensure_tree_file_node(
            kb_id,
            parent_id,
            display_name,
            branch_type,
            user_id,
            created_at=file_created_at,
        )
        linked_doc = int(doc_id) if doc_id is not None else _resolve_doc_master_from_node(target_node_id)
        if linked_doc is not None:
            _upsert_node_doc_link(target_node_id, int(linked_doc))
    else:
        execute(
            """
            UPDATE tree_nodes
               SET parent_id=%s, name=%s, branch_type=%s, updated_at=NOW()
             WHERE node_id=%s AND tree_id=%s
            """,
            (parent_id, display_name, branch_type, target_node_id, kb_id),
        )

    return {"action": "edit", "target_path": item.get("target_path"), "object_id": int(object_id), "node_id": target_node_id}


def _apply_delete_item(kb_id: int, item: Dict[str, Any]) -> Dict[str, Any]:
    branch_type = str(item.get("branch_type") or "GENERAL").upper()
    raw_node_id = item.get("node_id")
    raw_doc_id = item.get("doc_id")
    raw_object_id = item.get("object_id")
    raw_item_id = item.get("id")

    node_id: Optional[int] = None
    doc_id: Optional[int] = None
    object_id: Optional[int] = None

    try:
        if raw_node_id is not None:
            node_id = int(raw_node_id)
    except Exception:
        node_id = None
    try:
        if raw_doc_id is not None:
            doc_id = int(raw_doc_id)
    except Exception:
        doc_id = None
    try:
        if raw_object_id is not None:
            object_id = int(raw_object_id)
    except Exception:
        object_id = None

    if doc_id is None and node_id is not None and _node_in_tree(kb_id, node_id, branch_type):
        row = fetch_one("SELECT doc_master_id FROM pf_node_props WHERE node_id=%s LIMIT 1", (node_id,))
        if row and row.get("doc_master_id") is not None:
            doc_id = int(row["doc_master_id"])

    if doc_id is None and object_id is None and raw_item_id is not None:
        try:
            object_id = int(raw_item_id)
        except Exception:
            object_id = None

    if doc_id is None and object_id is not None:
        row = fetch_one(
            """
            SELECT doc_master_id
              FROM doc_revisions
             WHERE storage_object_id=%s
             ORDER BY rev_no DESC, revision_id DESC
             LIMIT 1
            """,
            (object_id,),
        )
        if row and row.get("doc_master_id") is not None:
            doc_id = int(row["doc_master_id"])

    if doc_id is None and raw_item_id is not None:
        try:
            item_doc_id = int(raw_item_id)
        except Exception:
            item_doc_id = None
        if item_doc_id is not None:
            row = fetch_one(
                """
                SELECT 1 AS ok
                  FROM pf_node_props p
                  JOIN tree_nodes n ON n.node_id = p.node_id
                 WHERE n.tree_id=%s
                   AND p.doc_master_id=%s
                 LIMIT 1
                """,
                (kb_id, item_doc_id),
            )
            if row:
                doc_id = item_doc_id

    delete_item = dict(item)
    if node_id is not None:
        delete_item["node_id"] = node_id
    node_ids = _resolve_delete_node_ids(kb_id, delete_item, doc_id, branch_type)

    deleted_node_ids: List[int] = []
    for nid in node_ids:
        link = fetch_one("SELECT doc_master_id FROM pf_node_props WHERE node_id=%s LIMIT 1", (nid,))
        if doc_id is None and link and link.get("doc_master_id") is not None:
            doc_id = int(link["doc_master_id"])
        execute("DELETE FROM pf_node_props WHERE node_id=%s", (nid,))
        execute("DELETE FROM tree_nodes WHERE node_id=%s AND tree_id=%s", (nid, kb_id))
        deleted_node_ids.append(nid)

    if doc_id is not None:
        _cleanup_orphan_doc(int(doc_id))

    return {
        "action": "delete",
        "target_path": item.get("target_path"),
        "node_id": deleted_node_ids[0] if deleted_node_ids else node_id,
        "node_ids": deleted_node_ids,
        "doc_id": doc_id,
    }


def _apply_add_or_duplicate_item(kb_id: int, item: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    branch_type = str(item.get("branch_type") or "GENERAL").upper()
    target_path = str(item.get("target_path") or "")
    dirs, display_name, base_name, ext = _split_target_path(target_path)
    file_created_at = _parse_file_create(item.get("file_create"))
    tree_dirs = list(dirs)
    root_rel = dirs[0] if dirs else None
    if branch_type == "KIWIX":
        tree_dirs = _kiwix_tree_dirs(dirs, item)
        root_rel = _kiwix_storage_root(dirs, item)
    sub_rel = "/".join(dirs[1:]) if len(dirs) > 1 else None
    storage_doc_code = ""
    if branch_type == "KIWIX":
        storage_doc_code = str(item.get("prj_code") or "").strip() or str(root_rel or "")

    if branch_type == "KIWIX":
        kiwix_root_id = _find_kiwix_root_node_id(kb_id)
        if kiwix_root_id is None:
            raise HTTPException(status_code=400, detail='Cannot find a root folder containing "KIWIX" for KIWIX sync.')
        parent_id = _ensure_folder_chain_from_parent(kb_id, kiwix_root_id, tree_dirs, user_id, branch_type=branch_type)
    else:
        parent_id = _ensure_folder_chain(kb_id, tree_dirs, user_id, branch_type=branch_type)

    # Reuse existing node/doc in the same tree-path first.
    existing_node = fetch_one(
        """
        SELECT node_id
          FROM tree_nodes
         WHERE tree_id=%s
           AND is_folder=0
           AND parent_id <=> %s
           AND name=%s
           AND COALESCE(UPPER(branch_type), 'GENERAL')=%s
         LIMIT 1
        """,
        (kb_id, parent_id, display_name, branch_type),
    )
    if existing_node and existing_node.get("node_id") is not None:
        ex_node_id = int(existing_node["node_id"])
        ex_doc_id = _resolve_doc_master_from_node(ex_node_id)
        if ex_doc_id is not None:
            return {
                "action": "add",
                "target_path": target_path,
                "node_id": ex_node_id,
                "doc_id": int(ex_doc_id),
                "reused": True,
            }

    doc_id: Optional[int] = None
    ref_doc_ids = item.get("same_doc_ids")
    if isinstance(ref_doc_ids, list):
        for d in ref_doc_ids:
            try:
                if d is not None:
                    doc_id = int(d)
                    break
            except Exception:
                continue

    # Reuse logical doc by storage path + filename if available.
    if doc_id is None:
        doc_id = _resolve_doc_by_storage_path(root_rel, sub_rel, base_name, ext)

    if doc_id is None:
        local_root = SYNC_LOCAL_BASE_DIRS.get(branch_type)
        local_abs = local_root / Path(_norm_path(target_path)) if local_root else None
        checksum = ""
        if local_abs and local_abs.exists() and local_abs.is_file():
            try:
                checksum = _sha256_file(local_abs)
            except Exception:
                checksum = ""
        mime = mimetypes.guess_type(display_name)[0] or "application/octet-stream"
        if file_created_at is None:
            object_id = execute(
                """
                INSERT INTO storage_objects(org_id, owner_user_id, doc_code, name, file_right, mime_type, relative_path, sub_path, checksum_sha256)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (1, user_id, storage_doc_code, base_name, ext, mime, root_rel, sub_rel, checksum),
                True,
            )
        else:
            object_id = execute(
                """
                INSERT INTO storage_objects(org_id, owner_user_id, doc_code, name, file_right, mime_type, relative_path, sub_path, checksum_sha256, created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (1, user_id, storage_doc_code, base_name, ext, mime, root_rel, sub_rel, checksum, file_created_at),
                True,
            )
        execute(
            """
            INSERT INTO doc_props(object_id, doc_type, status)
            VALUES(%s,%s,'DRAFT')
            ON DUPLICATE KEY UPDATE doc_type=VALUES(doc_type), status=VALUES(status)
            """,
            (int(object_id), _doc_type_from_ext(ext)),
        )
        if file_created_at is None:
            doc_id = int(
                execute(
                    """
                    INSERT INTO doc_master(org_id, title, title_norm, content_hash)
                    VALUES(%s,%s,%s,%s)
                    """,
                    (1, display_name, _normalize_title(display_name), checksum),
                    True,
                )
            )
            rev_id = int(
                execute(
                    """
                    INSERT INTO doc_revisions(doc_master_id, rev_no, storage_object_id, created_by)
                    VALUES(%s,1,%s,%s)
                    """,
                    (doc_id, int(object_id), user_id),
                    True,
                )
            )
        else:
            doc_id = int(
                execute(
                    """
                    INSERT INTO doc_master(org_id, title, title_norm, content_hash, created_at)
                    VALUES(%s,%s,%s,%s,%s)
                    """,
                    (1, display_name, _normalize_title(display_name), checksum, file_created_at),
                    True,
                )
            )
            rev_id = int(
                execute(
                    """
                    INSERT INTO doc_revisions(doc_master_id, rev_no, storage_object_id, created_by, created_at)
                    VALUES(%s,1,%s,%s,%s)
                    """,
                    (doc_id, int(object_id), user_id, file_created_at),
                    True,
                )
            )
        execute("UPDATE doc_master SET current_revision_id=%s WHERE doc_master_id=%s", (rev_id, doc_id))

    node_id = _ensure_tree_file_node(
        kb_id,
        parent_id,
        display_name,
        branch_type,
        user_id,
        created_at=file_created_at,
    )
    _upsert_node_doc_link(node_id, int(doc_id))
    execute(
        """
        UPDATE doc_master
           SET origin_kb_id=COALESCE(origin_kb_id, %s),
               origin_node_id=COALESCE(origin_node_id, %s)
         WHERE doc_master_id=%s
        """,
        (kb_id, node_id, int(doc_id)),
    )
    return {"action": "add", "target_path": target_path, "node_id": node_id, "doc_id": int(doc_id)}


def preview(
    kb_id: int = Query(..., ge=1),
    user_id: int = Depends(get_current_user_id),
):
    kb = fetch_one(
        """
        SELECT tree_id
          FROM trees
         WHERE tree_id=%s
           AND kind='KB'
         LIMIT 1
        """,
        (kb_id,),
    )
    if not kb:
        raise HTTPException(status_code=404, detail="KB not found")

    # Conservative output: only verified comparison result.
    items = _build_preview_items(kb_id)
    return {
        "kb_id": kb_id,
        "basis": "local",
        "local_roots": {k: str(v) for k, v in SYNC_LOCAL_BASE_DIRS.items()},
        "kiwix_documents_json": str(SYNC_KIWIX_DOCUMENTS_JSON),
        "local_mutation": False,
        "items": items,
        "affected_tables_by_action": {
            "edit/path_mismatch": _affected_tables_for_change("path_mismatch"),
            "delete/remove": _affected_tables_for_change("remove"),
            "add/insert": _affected_tables_for_change("insert"),
            "duplicate_path": _affected_tables_for_change("duplicate_path"),
        },
        "counts": {
            "add": sum(1 for i in items if i.get("action") == "add"),
            "edit": sum(1 for i in items if i.get("action") == "edit"),
            "delete": sum(1 for i in items if i.get("action") == "delete"),
            "duplicate": sum(1 for i in items if i.get("action") == "duplicate_path"),
        },
    }


def apply_selected(
    kb_id: int = Body(...),
    selected_items: List[Dict[str, Any]] = Body(...),
    sync_direction: str = Body("LOCAL_TO_WEB"),
    local_apply: bool = Body(False),
    user_id: int = Depends(get_current_user_id),
):
    kb = fetch_one(
        """
        SELECT tree_id
          FROM trees
         WHERE tree_id=%s
           AND kind='KB'
         LIMIT 1
        """,
        (kb_id,),
    )
    if not kb:
        raise HTTPException(status_code=404, detail="KB not found")

    latest_preview = _build_preview_items(kb_id)
    preview_map = {_item_key(it): it for it in latest_preview}

    validated_selected: List[Dict[str, Any]] = []
    for raw in selected_items or []:
        if not isinstance(raw, dict):
            continue
        bt = str(raw.get("branch_type") or "").strip().upper()
        if bt not in SYNC_TRIGGER_BRANCH_TYPES:
            continue
        key = _item_key(raw)
        src = preview_map.get(key)
        if not src:
            continue
        validated_selected.append(src)

    if not validated_selected:
        raise HTTPException(status_code=400, detail="No valid selected items to apply.")

    direction = str(sync_direction or "LOCAL_TO_WEB").strip().upper()
    if direction not in {"LOCAL_TO_WEB", "WEB_TO_LOCAL"}:
        direction = "LOCAL_TO_WEB"

    do_local_apply = bool(local_apply) and direction == "WEB_TO_LOCAL"

    applied: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    partial_failed_count = 0

    for it in validated_selected:
        action = str(it.get("action") or "").lower()
        bt = str(it.get("branch_type") or "").upper()
        target_tree_ids = [kb_id]
        if bt == "KIWIX":
            target_tree_ids = _kiwix_tree_ids_for_mirror(kb_id)

        try:
            results_by_tree: List[Dict[str, Any]] = []
            with _sync_item_transaction():
                shared_doc_id: Optional[int] = None
                for target_kb_id in target_tree_ids:
                    # KIWIX edit is kept on requested KB context; delete/add can mirror to KIWIX trees.
                    if bt == "KIWIX" and action in {"edit"} and int(target_kb_id) != int(kb_id):
                        continue

                    item_for_tree = it
                    # Reuse first created doc for mirrored KIWIX trees to avoid duplicate object/doc inserts.
                    if bt == "KIWIX" and action in {"add", "duplicate_path"} and shared_doc_id is not None:
                        item_for_tree = {**it, "same_doc_ids": [int(shared_doc_id)]}

                    if action == "edit":
                        result = _apply_edit_item(int(target_kb_id), item_for_tree, user_id)
                    elif action == "delete":
                        result = _apply_delete_item(int(target_kb_id), item_for_tree)
                    elif action in {"add", "duplicate_path"}:
                        result = _apply_add_or_duplicate_item(int(target_kb_id), item_for_tree, user_id)
                    else:
                        raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")

                    if bt == "KIWIX" and action in {"add", "duplicate_path"}:
                        doc_id_from_result = (result or {}).get("doc_id")
                        if doc_id_from_result is not None:
                            try:
                                shared_doc_id = int(doc_id_from_result)
                            except Exception:
                                pass

                    results_by_tree.append({"tree_id": int(target_kb_id), "result": result})

                if not results_by_tree:
                    raise HTTPException(status_code=400, detail="No applicable target tree for selected item.")

            local_result: Dict[str, Any] = {"applied": False}
            if do_local_apply:
                if bt == "GENERAL":
                    local_result = _apply_local_general_mirror(action, str(it.get("target_path") or ""))
                elif bt == "KIWIX":
                    local_result = {"applied": False, "reason": "kiwix_local_mutation_blocked"}

            applied.append(
                {
                    "target_path": it.get("target_path"),
                    "branch_type": it.get("branch_type"),
                    "action": action,
                    "change": it.get("change"),
                    "affected_tables": _affected_tables_for_change(it.get("change") or action),
                    "sync_direction": direction,
                    "local_result": local_result,
                    "result": results_by_tree,
                    "mirror_errors": [],
                }
            )
        except Exception as e:
            failed.append(
                {
                    "target_path": it.get("target_path"),
                    "branch_type": it.get("branch_type"),
                    "action": it.get("action"),
                    "change": it.get("change"),
                    "error": str(e),
                }
            )

    return {
        "kb_id": kb_id,
        "basis": "local",
        "sync_direction": direction,
        "local_mutation": do_local_apply,
        "validated_selected_count": len(validated_selected),
        "applied_count": len(applied),
        "failed_count": len(failed),
        "partial_failed_count": partial_failed_count,
        "applied": applied,
        "failed": failed,
    }
