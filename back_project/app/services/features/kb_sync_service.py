from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, Query

from app.config.settings import (
    PERSONAL_STORAGE_BASE_DIR,
    SYNC_KIWIX_DOCUMENTS_JSON,
    SYNC_LOCAL_BASE_DIR_GENERAL,
    SYNC_LOCAL_BASE_DIR_KIWIX,
)
from app.deps.auth_dep import get_current_user_id
from app.repositories.common import execute, fetch_all, fetch_one
from app.services.common.naming_service import build_display_name
from app.services.features.document_db import (
    as_int,
    collect_descendants,
    clean_node_name,
    file_meta,
    find_child_node_by_alias,
    get_kb_entry,
    get_node,
    join_rel_path,
    next_item_type_code,
    next_sort_order,
    node_name_key,
    normalize_rel,
    ensure_acl,
    ensure_ai_document_info,
    enqueue_ai_job,
    split_filename,
)

KIWIX_TREE_ROOT_NAME = "0. KIWIX"


def _split_target_path(target_path: str) -> Tuple[List[str], str, str]:
    clean = normalize_rel(target_path)
    parts = [p for p in clean.split("/") if p]
    if not parts:
        return [], "file", ""
    filename = parts[-1]
    doc_name, file_right = split_filename(filename)
    return parts[:-1], doc_name, file_right


def _path_from_parts(relative_path: str, sub_path: str, doc_name: str, file_right: str) -> str:
    filename = f"{doc_name}.{file_right}" if file_right else doc_name
    return join_rel_path(relative_path, sub_path, filename)


def _parse_file_create(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
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


def _kiwix_storage_root(storage_dirs: List[str], item: Dict[str, Any]) -> str:
    prj_code = str(item.get("prj_code") or "").strip()
    if prj_code:
        return prj_code
    if storage_dirs:
        return storage_dirs[0]
    return ""


def _is_personal_root(root: Dict[str, Any]) -> bool:
    return str(root.get("kb_type") or "").strip().lower() == "personal"


def _personal_sync_base(root: Dict[str, Any]) -> Path:
    # The persisted root name is the immutable local folder name created with the user.
    folder_name = clean_node_name(root.get("name") or root.get("alias") or "")
    return Path(PERSONAL_STORAGE_BASE_DIR) / folder_name


def _storage_policy_for_parent(parent: Dict[str, Any], user_id: int) -> Tuple[str, str, int]:
    if str(parent.get("kb_type") or "").strip().lower() == "personal":
        return "personal", "PRIVATE", as_int(parent.get("kb_owner_user_id")) or int(user_id)
    return "admin_kb", "PUBLIC", int(user_id)


def _scan_general_files(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    base = Path(base_dir or SYNC_LOCAL_BASE_DIR_GENERAL)
    if not base.exists():
        return []
    out: List[Dict[str, Any]] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(base).as_posix()
            folder_parts, doc_name, file_right = _split_target_path(rel)
            meta = file_meta(path)
            relative_path = folder_parts[0] if folder_parts else ""
            sub_path = "/".join(folder_parts[1:]) if len(folder_parts) > 1 else ""
            out.append(
                {
                    "branch_type": "GENERAL",
                    "target_path": rel,
                    "relative_path": relative_path,
                    "sub_path": sub_path,
                    "doc_name": doc_name,
                    "file_right": file_right,
                    "checksum_sha256": meta["checksum_sha256"],
                    "size_bytes": meta["size_bytes"],
                    "source_created_at": meta["source_created_at"],
                    "source_updated_at": meta["source_updated_at"],
                    "mime_type": meta["mime_type"],
                    "local_abs_path": str(path),
                }
            )
        except Exception:
            continue
    return out


def _scan_general_folders(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    base = Path(base_dir or SYNC_LOCAL_BASE_DIR_GENERAL)
    if not base.exists():
        return []
    out: List[Dict[str, Any]] = []
    paths = sorted(
        (p for p in base.rglob("*") if p.is_dir()),
        key=lambda p: (len(p.relative_to(base).parts), p.relative_to(base).as_posix().casefold()),
    )
    for path in paths:
        try:
            rel = normalize_rel(path.relative_to(base).as_posix())
            if not rel:
                continue
            parts = [clean_node_name(p) for p in rel.split("/") if clean_node_name(p)]
            if not parts:
                continue
            out.append(
                {
                    "branch_type": "GENERAL",
                    "entry_type": "folder",
                    "target_path": "/".join(parts),
                    "relative_path": parts[0],
                    "sub_path": "/".join(parts[1:]) if len(parts) > 1 else "",
                    "folder_parts": parts,
                    "local_abs_path": str(path),
                }
            )
        except Exception:
            continue
    return out


def _scan_kiwix_json() -> List[Dict[str, Any]]:
    path = Path(SYNC_KIWIX_DOCUMENTS_JSON)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return []
    rows = data if isinstance(data, list) else data.get("documents") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    base = Path(SYNC_LOCAL_BASE_DIR_KIWIX)
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        platform = str(raw.get("platform") or "").strip().upper()
        if platform and platform != "KIWIX":
            continue

        prj_name = str(raw.get("prjName") or "").strip()
        prj_code = str(raw.get("prjCode") or "").strip()
        itemtype_name = str(raw.get("itemtypeName") or "").strip()
        item_doc_code = str(raw.get("docCode") or "").strip()
        file_name = str(raw.get("fileName") or "").strip()
        file_ext = str(raw.get("fileExt") or "").strip().lstrip(".").lower()
        if not prj_code or not file_name:
            continue

        file_right = file_ext or "md"
        display_name = build_display_name(file_name, file_right, fallback="document")
        sub_path = f"{item_doc_code}_{file_name}" if item_doc_code else file_name
        target_path = join_rel_path(prj_code, sub_path, display_name)
        local_path = base / Path(target_path.replace("/", os.sep))
        created_at = _parse_file_create(raw.get("fileCreate"))
        source_created_at = created_at
        source_updated_at = created_at
        size_bytes = None
        checksum_sha256 = ""
        local_abs_path = ""
        if local_path.exists() and local_path.is_file():
            try:
                st = local_path.stat()
                size_bytes = int(st.st_size)
                local_abs_path = str(local_path)
                if source_created_at is None:
                    source_created_at = datetime.fromtimestamp(st.st_ctime)
                if source_updated_at is None:
                    source_updated_at = datetime.fromtimestamp(st.st_mtime)
            except Exception:
                pass

        out.append(
            {
                "branch_type": "KIWIX",
                "target_path": target_path,
                "relative_path": prj_code,
                "sub_path": sub_path,
                "doc_name": file_name,
                "file_right": file_right,
                "checksum_sha256": checksum_sha256,
                "size_bytes": size_bytes,
                "source_created_at": source_created_at,
                "source_updated_at": source_updated_at,
                "mime_type": mimetypes.guess_type(display_name)[0] or "text/markdown",
                "doc_code": prj_code,
                "section": raw.get("section"),
                "prj_name": prj_name,
                "prj_code": prj_code,
                "item_doc_code": item_doc_code,
                "itemtype_name": itemtype_name,
                "file_name": file_name,
                "file_ext": file_right,
                "file_create": raw.get("fileCreate"),
                "local_abs_path": local_abs_path,
            }
        )
    return out


def _load_local_items(root: Dict[str, Any]) -> List[Dict[str, Any]]:
    if _is_personal_root(root):
        base = _personal_sync_base(root)
        root_folder = clean_node_name(root.get("name") or root.get("alias") or "")
        items = _scan_general_folders(base) + _scan_general_files(base)
        for item in items:
            if not _is_file_entry(item):
                continue
            folders, _, _ = _split_target_path(str(item.get("target_path") or ""))
            item["relative_path"] = root_folder
            item["sub_path"] = "/".join(folders)
        return items
    return _scan_general_folders() + _scan_general_files() + _scan_kiwix_json()


def _entry_key(item: Dict[str, Any]) -> str:
    entry_type = str(item.get("entry_type") or "file").strip().lower()
    return f"{entry_type}:{normalize_rel(item.get('target_path')).casefold()}"


def _is_file_entry(item: Dict[str, Any]) -> bool:
    return str(item.get("entry_type") or "file").strip().lower() != "folder"


def _sync_path_key(value: Any) -> str:
    return normalize_rel(value).strip("/").casefold()


def _is_folder_remove_request(item: Dict[str, Any]) -> bool:
    entry_type = str(item.get("entry_type") or "file").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    change = str(item.get("change") or "").strip().lower()
    return entry_type == "folder" and (action in {"delete", "remove"} or change == "folder_remove")


def _split_nested_folder_remove_requests(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    folder_paths = {
        _sync_path_key(item.get("target_path"))
        for item in items
        if _is_folder_remove_request(item) and _sync_path_key(item.get("target_path"))
    }
    if not folder_paths:
        return items, []

    kept: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for item in items:
        path = _sync_path_key(item.get("target_path"))
        if _is_folder_remove_request(item) and path:
            covered_by_parent = any(path.startswith(f"{parent}/") for parent in folder_paths if parent != path)
            if covered_by_parent:
                skipped.append(
                    {
                        "target_path": item.get("target_path"),
                        "action": "folder_remove",
                        "reason": "Covered by selected parent folder removal",
                    }
                )
                continue
        kept.append(item)
    return kept, skipped


def _node_path_from_map(
    node_id: Optional[int],
    nodes_by_id: Dict[int, Dict[str, Any]],
    root_node_id: int,
) -> List[str]:
    parts: List[str] = []
    cur = as_int(node_id)
    safety = 0
    while cur is not None and cur != int(root_node_id) and safety < 256:
        safety += 1
        row = nodes_by_id.get(int(cur))
        if not row:
            break
        name = clean_node_name(row.get("name") or row.get("alias"))
        if name:
            parts.append(name)
        cur = as_int(row.get("parent_id"))
    parts.reverse()
    return parts


def _is_kiwix_folder_path(parts: List[str]) -> bool:
    return bool(parts and "kiwix" in node_name_key(parts[0]))


def _personal_compare_path(root: Dict[str, Any], row: Dict[str, Any], target_path: str) -> str:
    if not _is_personal_root(root):
        return target_path
    owner_folder = clean_node_name(root.get("name") or root.get("alias") or "")
    relative_path = clean_node_name(row.get("relative_path") or "")
    if not owner_folder or node_name_key(relative_path) != node_name_key(owner_folder):
        return target_path
    return _path_from_parts("", row.get("sub_path") or "", row.get("doc_name") or "", row.get("file_right") or "")


def _load_db_items(kb_id: int) -> List[Dict[str, Any]]:
    root = get_kb_entry(kb_id)
    root_node_id = int(root["node_id"])
    node_ids = sorted(collect_descendants([int(root["node_id"])]))
    if not node_ids:
        return []
    placeholders = ",".join(["%s"] * len(node_ids))
    tree_rows = fetch_all(
        f"""
        SELECT node_id, parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
               name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
          FROM tree_nodes
         WHERE node_id IN ({placeholders})
        """,
        tuple(node_ids),
    )
    nodes_by_id = {int(row["node_id"]): row for row in tree_rows or [] if row.get("node_id") is not None}
    rows = fetch_all(
        f"""
        SELECT
            so.object_id,
            so.doc_code,
            so.doc_name,
            so.file_right,
            so.relative_path,
            so.sub_path,
            so.checksum_sha256,
            so.size_bytes,
            so.source_created_at,
            so.source_updated_at,
            so.source_time_status,
            so.mime_type,
            so.branch_type,
            so.doc_status,
            tn.node_id,
            tn.item_type_code
          FROM tree_nodes tn
          JOIN storage_objects so ON so.object_id = tn.doc_id
         WHERE tn.kb_root_id=%s
           AND tn.node_id IN ({placeholders})
        """,
        tuple([int(root["kb_root_id"])] + node_ids),
    )
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        target_path = _path_from_parts(row.get("relative_path") or "", row.get("sub_path") or "", row.get("doc_name") or "", row.get("file_right") or "")
        row["target_path"] = _personal_compare_path(root, row, target_path)
        row["branch_type"] = str(row.get("branch_type") or "general").upper()
        row["entry_type"] = "file"
        out.append(row)
    for row in tree_rows or []:
        if not bool(row.get("is_folder")):
            continue
        if as_int(row.get("node_id")) == root_node_id:
            continue
        parts = _node_path_from_map(as_int(row.get("node_id")), nodes_by_id, root_node_id)
        if not parts:
            continue
        is_kiwix_folder = _is_kiwix_folder_path(parts)
        if is_kiwix_folder and len(parts) <= 1:
            continue
        item = dict(row)
        item["entry_type"] = "folder"
        item["branch_type"] = "KIWIX" if is_kiwix_folder else "GENERAL"
        item["target_path"] = "/".join(parts)
        item["relative_path"] = parts[0]
        item["sub_path"] = "/".join(parts[1:]) if len(parts) > 1 else ""
        item["folder_parts"] = parts
        out.append(item)
    return out


def _preview_item(action: str, change: str, item: Dict[str, Any], reason: str, db: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    db = db or {}
    entry_type = str(item.get("entry_type") or db.get("entry_type") or "file").lower()
    return {
        "id": item.get("object_id") or db.get("object_id") or item.get("target_path"),
        "object_id": db.get("object_id") or item.get("object_id"),
        "doc_id": db.get("object_id") or item.get("object_id"),
        "node_id": db.get("node_id") or item.get("node_id"),
        "branch_type": str(item.get("branch_type") or db.get("branch_type") or "GENERAL").upper(),
        "entry_type": entry_type,
        "target_path": item.get("target_path") or db.get("target_path"),
        "action": action,
        "change": change,
        "reason": reason,
        "preview": reason,
        "apply_supported": True,
        "doc_name": item.get("doc_name") or db.get("doc_name"),
        "file_right": item.get("file_right") or db.get("file_right"),
        "relative_path": item.get("relative_path") or db.get("relative_path"),
        "sub_path": item.get("sub_path") or db.get("sub_path"),
        "mime_type": item.get("mime_type") or db.get("mime_type"),
        "doc_code": item.get("doc_code") or db.get("doc_code") or "",
        "checksum_sha256": item.get("checksum_sha256"),
        "size_bytes": item.get("size_bytes"),
        "source_created_at": item.get("source_created_at"),
        "source_updated_at": item.get("source_updated_at"),
        "local_abs_path": item.get("local_abs_path"),
        "folder_parts": item.get("folder_parts") or db.get("folder_parts"),
        "section": item.get("section"),
        "prj_name": item.get("prj_name"),
        "prj_code": item.get("prj_code"),
        "item_doc_code": item.get("item_doc_code"),
        "itemtype_name": item.get("itemtype_name"),
        "file_name": item.get("file_name"),
        "file_ext": item.get("file_ext"),
        "file_create": item.get("file_create"),
    }


def _is_metadata_backfill_needed(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return False
    if str(row.get("source_time_status") or "").lower() != "actual":
        return True
    if as_int(row.get("size_bytes")) is None:
        return True
    if not str(row.get("checksum_sha256") or "").strip():
        return True
    if row.get("source_created_at") is None or row.get("source_updated_at") is None:
        return True
    return False


def _folder_has_document_descendants(node_id: Optional[int]) -> bool:
    nid = as_int(node_id)
    if nid is None:
        return True
    ids = sorted(collect_descendants([nid]))
    if not ids:
        return False
    placeholders = ",".join(["%s"] * len(ids))
    row = fetch_one(
        f"""
        SELECT 1 AS has_docs
          FROM tree_nodes
         WHERE node_id IN ({placeholders})
           AND doc_id IS NOT NULL
         LIMIT 1
        """,
        tuple(ids),
    )
    return bool(row)


def preview(
    kb_id: int = Query(...),
    user_id: int = Depends(get_current_user_id),
):
    root = get_kb_entry(kb_id)
    local_items = _load_local_items(root)
    db_items = _load_db_items(kb_id)

    local_by_path = {_entry_key(x): x for x in local_items}
    db_by_path = {_entry_key(x): x for x in db_items}
    db_by_checksum: Dict[str, List[Dict[str, Any]]] = {}
    for row in db_items:
        if not _is_file_entry(row):
            continue
        checksum = str(row.get("checksum_sha256") or "")
        if checksum:
            db_by_checksum.setdefault(checksum, []).append(row)

    out: List[Dict[str, Any]] = []
    for key, local in local_by_path.items():
        db = db_by_path.get(key)
        if not db:
            if not _is_file_entry(local):
                out.append(_preview_item("add", "folder_insert", local, "Local folder is not registered in DB"))
                continue
            checksum = str(local.get("checksum_sha256") or "")
            checksum_matches = db_by_checksum.get(checksum, []) if checksum else []
            deleted_match = next((m for m in checksum_matches if str(m.get("doc_status") or "") == "deleted_pending"), None)
            if deleted_match:
                out.append(_preview_item("restore_move", "restore_to_new_path", local, "Deleted document exists at another path; restore it to this local path while preserving ai_document_id", deleted_match))
                continue
            action = "duplicate_path" if checksum_matches else "add"
            change = "same_content_multi_path" if action == "duplicate_path" else "insert"
            out.append(_preview_item(action, change, local, "Local file is not registered in DB", checksum_matches[0] if action == "duplicate_path" else None))
            continue
        if not _is_file_entry(local):
            continue
        if str(db.get("doc_status") or "") == "deleted_pending":
            out.append(
                _preview_item(
                    "restore_move",
                    "restore_to_new_path",
                    local,
                    "Deleted document exists at this path; restore it while preserving ai_document_id",
                    db,
                )
            )
            continue
        if _is_metadata_backfill_needed(db):
            out.append(_preview_item("edit", "metadata_backfill", local, "Migration metadata is missing; local metadata can be backfilled", db))
            continue
        local_checksum = str(local.get("checksum_sha256") or "")
        db_checksum = str(db.get("checksum_sha256") or "")
        if local_checksum and db_checksum and local_checksum != db_checksum:
            out.append(_preview_item("edit", "path_mismatch", local, "Same path exists but checksum differs", db))
        elif as_int(local.get("size_bytes")) is not None and as_int(db.get("size_bytes")) != as_int(local.get("size_bytes")):
            out.append(_preview_item("edit", "path_mismatch", local, "Same path exists but file size differs", db))

    for key, db in db_by_path.items():
        if key in local_by_path:
            continue
        if not _is_file_entry(db):
            if not _folder_has_document_descendants(as_int(db.get("node_id"))):
                out.append(_preview_item("delete", "folder_remove", db, "DB folder is missing from local path and has no document descendants", db))
            continue
        if str(db.get("doc_status") or "active") == "active":
            out.append(_preview_item("delete", "remove", db, "DB document is missing from local path", db))

    return {"items": out}


def _skip_selected_root_prefix(root: Dict[str, Any], folder_parts: List[str]) -> List[str]:
    if not folder_parts:
        return []
    root_names = set()
    cur: Optional[Dict[str, Any]] = root
    safety = 0
    while cur and safety < 64:
        safety += 1
        for field in ("name", "alias"):
            key = node_name_key(cur.get(field))
            if key:
                root_names.add(key)
        parent_id = as_int(cur.get("parent_id"))
        cur = get_node(parent_id) if parent_id is not None else None

    out: List[str] = []
    skipping_selected_root_prefix = True
    previous_key = ""
    for raw in folder_parts:
        clean = clean_node_name(raw)
        if not clean:
            continue
        key = node_name_key(clean)
        if skipping_selected_root_prefix and key in root_names:
            continue
        skipping_selected_root_prefix = False
        if previous_key and key == previous_key:
            continue
        out.append(clean)
        previous_key = key
    return out


def _ensure_folder_chain(kb_id: int, folder_parts: List[str], user_id: int) -> Dict[str, Any]:
    root = get_kb_entry(kb_id)
    parts = _skip_selected_root_prefix(root, folder_parts)
    parent = root
    for name in parts:
        clean = clean_node_name(name)
        if not clean:
            continue
        if node_name_key(clean) == node_name_key(parent.get("alias") or parent.get("name")):
            continue
        existing = find_child_node_by_alias(int(parent["node_id"]), clean, folder_only=True)
        if existing:
            parent = existing
            continue
        is_top = parent.get("parent_id") is None
        item_type_code = next_item_type_code() if is_top else int(parent.get("item_type_code") or 10)
        node_id = execute(
            """
            INSERT INTO tree_nodes(
                parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
                name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
            )
            VALUES(%s,%s,%s,%s,NULL,%s,%s,TRUE,%s,%s,%s,NOW(),NOW())
            """,
            (
                int(parent["node_id"]),
                int(parent["kb_root_id"]),
                parent.get("kb_type") or "admin",
                parent.get("kb_owner_user_id"),
                clean,
                clean,
                item_type_code,
                next_sort_order(int(parent["node_id"])),
                user_id,
            ),
            True,
        )
        parent = get_node(node_id)
    return parent


def _ensure_folder_from_local(kb_id: int, item: Dict[str, Any], user_id: int) -> int:
    parts = item.get("folder_parts")
    if not isinstance(parts, list):
        parts = [p for p in normalize_rel(item.get("target_path")).split("/") if p]
    clean_parts = [clean_node_name(p) for p in parts if clean_node_name(p)]
    if not clean_parts:
        raise ValueError("folder path is required")
    node = _ensure_folder_chain(kb_id, clean_parts, user_id)
    return int(node["node_id"])


def _delete_empty_folder_from_db(item: Dict[str, Any]) -> int:
    node_id = as_int(item.get("node_id"))
    if node_id is None:
        raise ValueError("node_id is required for folder delete")
    row = get_node(node_id)
    if not row or not bool(row.get("is_folder")):
        raise ValueError("folder node was not found")
    if _folder_has_document_descendants(node_id):
        raise ValueError("folder has document descendants and cannot be removed by folder sync")

    ids = sorted(collect_descendants([node_id]))
    if not ids:
        return node_id
    placeholders = ",".join(["%s"] * len(ids))
    rows = fetch_all(
        f"SELECT node_id, parent_id FROM tree_nodes WHERE node_id IN ({placeholders})",
        tuple(ids),
    )
    parent_by_id = {int(r["node_id"]): as_int(r.get("parent_id")) for r in rows or []}

    def depth(nid: int) -> int:
        d = 0
        cur = parent_by_id.get(int(nid))
        while cur in parent_by_id and d < 256:
            d += 1
            cur = parent_by_id.get(int(cur))
        return d

    for nid in sorted(ids, key=depth, reverse=True):
        execute("DELETE FROM acl_bindings WHERE node_id=%s", (int(nid),))
        execute("DELETE FROM tree_nodes WHERE node_id=%s AND is_folder=TRUE AND doc_id IS NULL", (int(nid),))
    return node_id


def _insert_doc_from_local(kb_id: int, item: Dict[str, Any], user_id: int) -> int:
    folders, doc_name, file_right = _split_target_path(str(item.get("target_path") or ""))
    branch_type = str(item.get("branch_type") or "GENERAL").upper()
    if branch_type == "KIWIX":
        tree_folders = [KIWIX_TREE_ROOT_NAME, *_kiwix_tree_dirs(folders, item)]
        rel = _kiwix_storage_root(folders, item)
        sub = "/".join(folders[1:]) if len(folders) > 1 else ""
    else:
        tree_folders = folders
        rel = folders[0] if folders else ""
        sub = "/".join(folders[1:]) if len(folders) > 1 else ""
    parent = _ensure_folder_chain(kb_id, tree_folders, user_id)
    upload_mode, policy_code, owner_user_id = _storage_policy_for_parent(parent, user_id)
    object_id = execute(
        """
        INSERT INTO storage_objects(
            doc_code, uploader_user_id, upload_mode, doc_status, doc_name, file_right,
            relative_path, sub_path, mime_type, size_bytes, checksum_sha256,
            source_created_at, source_updated_at, source_time_status,
            uploaded_at, db_updated_at, branch_type
        )
        VALUES(%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),%s)
        """,
        (
            item.get("doc_code") or "",
            user_id,
            upload_mode,
            item.get("doc_name") or doc_name,
            item.get("file_right") or file_right,
            item.get("relative_path") or rel,
            item.get("sub_path") or sub,
            item.get("mime_type") or "application/octet-stream",
            item.get("size_bytes"),
            item.get("checksum_sha256"),
            item.get("source_created_at"),
            item.get("source_updated_at"),
            "actual" if item.get("local_abs_path") else "fallback",
            branch_type.lower(),
        ),
        True,
    )
    node_id = execute(
        """
        INSERT INTO tree_nodes(
            parent_id, kb_root_id, kb_type, kb_owner_user_id, doc_id,
            name, alias, is_folder, item_type_code, sort_order, created_by, created_at, updated_at
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s,NOW(),NOW())
        """,
        (
            parent["node_id"],
            parent["kb_root_id"],
            parent.get("kb_type") or "admin",
            parent.get("kb_owner_user_id"),
            object_id,
            item.get("doc_name") or doc_name,
            item.get("doc_name") or doc_name,
            parent.get("item_type_code") or 10,
            next_sort_order(int(parent["node_id"])),
            user_id,
        ),
        True,
    )
    ensure_acl(node_id, policy_code, owner_user_id=owner_user_id)
    execute(
        """
        INSERT INTO doc_revisions(
            doc_id, rev_no, checksum_sha256, source_created_at, source_updated_at,
            size_bytes, uploaded_at, created_by, created_at
        )
        VALUES(%s,1,%s,%s,%s,%s,NOW(),%s,NOW())
        """,
        (object_id, item.get("checksum_sha256"), item.get("source_created_at"), item.get("source_updated_at"), item.get("size_bytes"), user_id),
    )
    ensure_ai_document_info(object_id)
    enqueue_ai_job(object_id, "add", user_id)
    return object_id


def _update_doc_from_local(item: Dict[str, Any], user_id: int) -> Tuple[int, str]:
    object_id = as_int(item.get("object_id") or item.get("doc_id"))
    if object_id is None:
        raise ValueError("object_id is required for update")
    before = fetch_one(
        """
        SELECT source_time_status, size_bytes, checksum_sha256, source_created_at, source_updated_at
          FROM storage_objects
         WHERE object_id=%s
        """,
        (object_id,),
    )
    is_backfill = str(item.get("change") or "").lower() == "metadata_backfill" or _is_metadata_backfill_needed(before)
    target_path = str(item.get("target_path") or "")
    folders, doc_name, file_right = _split_target_path(target_path)
    branch_type = str(item.get("branch_type") or "GENERAL").upper()
    if branch_type == "KIWIX":
        rel = _kiwix_storage_root(folders, item)
        sub = "/".join(folders[1:]) if len(folders) > 1 else ""
    else:
        rel = folders[0] if folders else ""
        sub = "/".join(folders[1:]) if len(folders) > 1 else ""
    execute(
        """
        UPDATE storage_objects
           SET doc_name=%s,
               file_right=%s,
               relative_path=%s,
               sub_path=%s,
               mime_type=%s,
               size_bytes=%s,
               checksum_sha256=%s,
               source_created_at=%s,
               source_updated_at=%s,
               source_time_status=%s,
               doc_status='active',
               db_updated_at=NOW()
         WHERE object_id=%s
        """,
        (
            item.get("doc_name") or doc_name,
            item.get("file_right") or file_right,
            item.get("relative_path") or rel,
            item.get("sub_path") or sub,
            item.get("mime_type") or "application/octet-stream",
            item.get("size_bytes"),
            item.get("checksum_sha256"),
            item.get("source_created_at"),
            item.get("source_updated_at"),
            "actual" if item.get("local_abs_path") else "fallback",
            object_id,
        ),
    )
    if is_backfill:
        had_revision = fetch_one("SELECT revision_id FROM doc_revisions WHERE doc_id=%s LIMIT 1", (object_id,))
        had_ai_info = fetch_one("SELECT ai_document_id FROM ai_document_info WHERE object_id=%s LIMIT 1", (object_id,))
        _backfill_base_revision(object_id, item, user_id)
        _sync_ai_document_info_preserving_status(object_id)
        ai_job = fetch_one(
            "SELECT job_id FROM ai_index_jobs WHERE object_id=%s AND action IN ('add','update') LIMIT 1",
            (object_id,),
        )
        if not had_revision or not had_ai_info or not ai_job:
            enqueue_ai_job(object_id, "add", user_id)
        return object_id, "metadata_backfill"

    row = fetch_one("SELECT COALESCE(MAX(rev_no),0)+1 AS rev_no FROM doc_revisions WHERE doc_id=%s", (object_id,))
    execute(
        """
        INSERT INTO doc_revisions(doc_id, rev_no, checksum_sha256, source_created_at, source_updated_at, size_bytes, uploaded_at, created_by, created_at)
        VALUES(%s,%s,%s,%s,%s,%s,NOW(),%s,NOW())
        """,
        (object_id, int((row or {}).get("rev_no") or 1), item.get("checksum_sha256"), item.get("source_created_at"), item.get("source_updated_at"), item.get("size_bytes"), user_id),
    )
    ensure_ai_document_info(object_id)
    enqueue_ai_job(object_id, "update", user_id)
    return object_id, "update"


def _restore_deleted_doc_to_local_path(kb_id: int, item: Dict[str, Any], user_id: int) -> int:
    object_id = as_int(item.get("object_id") or item.get("doc_id"))
    if object_id is None:
        raise ValueError("object_id is required for restore")
    before = fetch_one(
        """
        SELECT so.object_id, so.doc_code, so.doc_name, so.file_right, so.relative_path,
               so.sub_path, so.doc_status, tn.node_id, tn.name, tn.alias, tn.item_type_code
          FROM storage_objects so
          JOIN tree_nodes tn ON tn.doc_id = so.object_id
         WHERE so.object_id=%s
         ORDER BY tn.node_id
         LIMIT 1
        """,
        (object_id,),
    )
    if not before:
        raise ValueError("deleted document was not found")
    if str(before.get("doc_status") or "") != "deleted_pending":
        raise ValueError("document is not in deleted_pending status")

    target_path = str(item.get("target_path") or "")
    folders, doc_name, file_right = _split_target_path(target_path)
    branch_type = str(item.get("branch_type") or "GENERAL").upper()
    if branch_type == "KIWIX":
        tree_folders = [KIWIX_TREE_ROOT_NAME, *_kiwix_tree_dirs(folders, item)]
        rel = _kiwix_storage_root(folders, item)
        sub = "/".join(folders[1:]) if len(folders) > 1 else ""
    else:
        tree_folders = folders
        rel = folders[0] if folders else ""
        sub = "/".join(folders[1:]) if len(folders) > 1 else ""
    parent = _ensure_folder_chain(kb_id, tree_folders, user_id)
    upload_mode, policy_code, owner_user_id = _storage_policy_for_parent(parent, user_id)

    new_doc_name = item.get("doc_name") or doc_name
    new_file_right = item.get("file_right") or file_right
    execute(
        """
        UPDATE storage_objects
           SET doc_name=%s,
               file_right=%s,
               upload_mode=%s,
               relative_path=%s,
               sub_path=%s,
               mime_type=%s,
               size_bytes=%s,
               checksum_sha256=%s,
               source_created_at=%s,
               source_updated_at=%s,
               source_time_status=%s,
               doc_status='active',
               db_updated_at=NOW()
         WHERE object_id=%s
        """,
        (
            new_doc_name,
            new_file_right,
            upload_mode,
            item.get("relative_path") or rel,
            item.get("sub_path") or sub,
            item.get("mime_type") or "application/octet-stream",
            item.get("size_bytes"),
            item.get("checksum_sha256"),
            item.get("source_created_at"),
            item.get("source_updated_at"),
            "actual" if item.get("local_abs_path") else "fallback",
            object_id,
        ),
    )

    node_id = as_int(before.get("node_id"))
    if node_id is None:
        raise ValueError("document node was not found")
    old_name = before.get("name")
    old_alias = before.get("alias")
    new_alias = new_doc_name if node_name_key(old_alias) == node_name_key(old_name) else old_alias
    execute(
        """
        UPDATE tree_nodes
           SET parent_id=%s,
               kb_root_id=%s,
               kb_type=%s,
               kb_owner_user_id=%s,
               name=%s,
               alias=%s,
               item_type_code=%s,
               sort_order=%s,
               updated_at=NOW()
         WHERE node_id=%s
        """,
        (
            int(parent["node_id"]),
            int(parent["kb_root_id"]),
            parent.get("kb_type") or "admin",
            parent.get("kb_owner_user_id"),
            new_doc_name,
            new_alias or new_doc_name,
            int(parent.get("item_type_code") or 10),
            next_sort_order(int(parent["node_id"])),
            node_id,
        ),
    )

    _backfill_base_revision(object_id, item, user_id)
    ensure_ai_document_info(object_id)
    ensure_acl(node_id, policy_code, owner_user_id=owner_user_id)
    execute("DELETE FROM ai_index_jobs WHERE object_id=%s AND action='delete' AND status='pending'", (object_id,))
    execute("DELETE FROM document_deletions WHERE object_id=%s", (object_id,))
    execute("DELETE FROM deleted_documents WHERE old_object_id=%s", (object_id,))
    enqueue_ai_job(object_id, "update", user_id)
    return object_id


def _backfill_base_revision(object_id: int, item: Dict[str, Any], user_id: int) -> None:
    row = fetch_one(
        """
        SELECT revision_id
          FROM doc_revisions
         WHERE doc_id=%s
         ORDER BY rev_no ASC, revision_id ASC
         LIMIT 1
        """,
        (int(object_id),),
    )
    if row:
        execute(
            """
            UPDATE doc_revisions
               SET checksum_sha256=%s,
                   source_created_at=%s,
                   source_updated_at=%s,
                   size_bytes=%s
             WHERE revision_id=%s
            """,
            (
                item.get("checksum_sha256"),
                item.get("source_created_at"),
                item.get("source_updated_at"),
                item.get("size_bytes"),
                int(row["revision_id"]),
            ),
        )
        return
    execute(
        """
        INSERT INTO doc_revisions(
            doc_id, rev_no, checksum_sha256, source_created_at, source_updated_at,
            size_bytes, uploaded_at, created_by, created_at
        )
        VALUES(%s,1,%s,%s,%s,%s,NOW(),%s,NOW())
        """,
        (
            int(object_id),
            item.get("checksum_sha256"),
            item.get("source_created_at"),
            item.get("source_updated_at"),
            item.get("size_bytes"),
            user_id,
        ),
    )


def _sync_ai_document_info_preserving_status(object_id: int) -> None:
    row = fetch_one("SELECT index_status FROM ai_document_info WHERE object_id=%s", (int(object_id),))
    ensure_ai_document_info(int(object_id), index_status=(row or {}).get("index_status") or "pending")


def _mark_deleted(item: Dict[str, Any], user_id: int) -> int:
    object_id = as_int(item.get("object_id") or item.get("doc_id"))
    node_id = as_int(item.get("node_id"))
    if object_id is None:
        raise ValueError("object_id is required for delete")
    if node_id is None:
        row = fetch_one("SELECT node_id FROM tree_nodes WHERE doc_id=%s ORDER BY node_id LIMIT 1", (object_id,))
        node_id = as_int((row or {}).get("node_id"))
    ai = fetch_one("SELECT ai_document_id FROM ai_document_info WHERE object_id=%s", (object_id,))
    execute("UPDATE storage_objects SET doc_status='deleted_pending', db_updated_at=NOW() WHERE object_id=%s", (object_id,))
    if node_id is not None:
        execute(
            """
            INSERT INTO document_deletions(
                object_id, node_id, ai_document_id, status, detected_at, remain_until,
                detected_by_user_id, detected_source, reason, created_at, updated_at
            )
            VALUES(%s,%s,%s,'pending',NOW(),NOW() + INTERVAL '7 days',%s,'sync_scan','local missing',NOW(),NOW())
            ON CONFLICT (object_id, active_deletion_marker) DO UPDATE SET
                status='pending',
                detected_at=NOW(),
                remain_until=NOW() + INTERVAL '7 days',
                detected_by_user_id=EXCLUDED.detected_by_user_id,
                updated_at=NOW()
            """,
            (object_id, node_id, (ai or {}).get("ai_document_id"), user_id),
        )
    ensure_ai_document_info(object_id, index_status="deleted")
    enqueue_ai_job(object_id, "delete", user_id)
    return object_id


def _purge_kiwix_doc_from_sync(item: Dict[str, Any], user_id: int) -> int:
    object_id = as_int(item.get("object_id") or item.get("doc_id"))
    if object_id is None:
        raise ValueError("object_id is required for KIWIX delete")
    row = fetch_one(
        """
        SELECT
            so.object_id,
            so.branch_type,
            tn.node_id,
            ai.ai_document_id
          FROM storage_objects so
          LEFT JOIN tree_nodes tn ON tn.doc_id = so.object_id
          LEFT JOIN ai_document_info ai ON ai.object_id = so.object_id
         WHERE so.object_id=%s
         LIMIT 1
        """,
        (object_id,),
    )
    if not row:
        return object_id
    if str(row.get("branch_type") or "").strip().lower() != "kiwix":
        return _mark_deleted(item, user_id)

    ai_document_id = str(row.get("ai_document_id") or "").strip()
    if ai_document_id:
        execute(
            """
            DELETE FROM ai_index_jobs
             WHERE ai_document_id=%s
               AND action IN ('add', 'update', 'reindex')
               AND status IN ('pending', 'running')
            """,
            (ai_document_id,),
        )
        exists = fetch_one(
            """
            SELECT job_id
              FROM ai_index_jobs
             WHERE ai_document_id=%s
               AND action='delete'
               AND status IN ('pending', 'running')
             LIMIT 1
            """,
            (ai_document_id,),
        )
        if not exists:
            execute(
                """
                INSERT INTO ai_index_jobs(
                    ai_document_id, object_id, action, status, retry_count,
                    scheduled_at, created_by, created_at, updated_at
                )
                VALUES(%s,%s,'delete','pending',0,NOW(),%s,NOW(),NOW())
                """,
                (ai_document_id, object_id, user_id),
            )

    node_id = as_int(row.get("node_id"))
    if node_id is not None:
        execute("DELETE FROM acl_bindings WHERE node_id=%s", (node_id,))
    execute("DELETE FROM document_deletions WHERE object_id=%s", (object_id,))
    execute("DELETE FROM deleted_documents WHERE old_object_id=%s", (object_id,))
    execute("DELETE FROM tree_nodes WHERE doc_id=%s", (object_id,))
    execute("DELETE FROM doc_revisions WHERE doc_id=%s", (object_id,))
    execute("UPDATE ai_document_info SET object_id=NULL, index_status='deleted', updated_at=NOW() WHERE object_id=%s", (object_id,))
    execute("DELETE FROM storage_objects WHERE object_id=%s", (object_id,))
    return object_id


def _hydrate_selected_item(item: Dict[str, Any]) -> Dict[str, Any]:
    target_path = str(item.get("target_path") or "")
    local_abs = item.get("local_abs_path")
    branch_type = str(item.get("branch_type") or "GENERAL").upper()
    entry_type = str(item.get("entry_type") or "file").lower()
    hydrated = dict(item)
    if entry_type == "folder":
        parts = item.get("folder_parts")
        if not isinstance(parts, list):
            parts = [p for p in normalize_rel(target_path).split("/") if p]
        hydrated["folder_parts"] = [clean_node_name(p) for p in parts if clean_node_name(p)]
        hydrated.setdefault("relative_path", hydrated["folder_parts"][0] if hydrated["folder_parts"] else "")
        hydrated.setdefault("sub_path", "/".join(hydrated["folder_parts"][1:]) if len(hydrated["folder_parts"]) > 1 else "")
        return hydrated
    if local_abs and os.path.isfile(local_abs):
        meta = file_meta(local_abs)
    else:
        meta = {}
    folders, doc_name, file_right = _split_target_path(target_path)
    hydrated.setdefault("relative_path", folders[0] if folders else "")
    hydrated.setdefault("sub_path", "/".join(folders[1:]) if len(folders) > 1 else "")
    hydrated.setdefault("doc_name", doc_name)
    hydrated.setdefault("file_right", file_right)
    for key, value in meta.items():
        if value is None:
            continue
        if branch_type == "KIWIX" and key in {"source_created_at", "source_updated_at"} and hydrated.get(key) is not None:
            continue
        hydrated[key] = value
    return hydrated


def apply_selected(
    kb_id: int,
    selected_items: List[Dict[str, Any]],
    sync_direction: str = "LOCAL_TO_WEB",
    local_apply: bool = False,
    user_id: int = Depends(get_current_user_id),
):
    applied: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    if str(sync_direction or "LOCAL_TO_WEB").upper() != "LOCAL_TO_WEB":
        return {
            "applied_count": 0,
            "failed_count": len(selected_items or []),
            "skipped_count": 0,
            "applied": [],
            "failed": [{"target_path": x.get("target_path"), "error": "Only LOCAL_TO_WEB is supported"} for x in selected_items or []],
            "skipped": [],
        }
    selected_items, skipped = _split_nested_folder_remove_requests(selected_items or [])
    for raw in selected_items:
        item = _hydrate_selected_item(raw)
        action = str(item.get("action") or item.get("change") or "").lower()
        change = str(item.get("change") or "").lower()
        entry_type = str(item.get("entry_type") or "file").lower()
        try:
            if entry_type == "folder":
                if action in {"add", "insert"} or change == "folder_insert":
                    node_id = _ensure_folder_from_local(kb_id, item, user_id)
                    applied.append({"target_path": item.get("target_path"), "node_id": node_id, "action": "folder_insert"})
                elif action in {"delete", "remove"} or change == "folder_remove":
                    node_id = _delete_empty_folder_from_db(item)
                    applied.append({"target_path": item.get("target_path"), "node_id": node_id, "action": "folder_remove"})
                else:
                    raise ValueError(f"Unsupported folder action: {action or change}")
            elif action == "restore_move" or change == "restore_to_new_path":
                object_id = _restore_deleted_doc_to_local_path(kb_id, item, user_id)
                applied.append({"target_path": item.get("target_path"), "object_id": object_id, "action": "restore_to_new_path"})
            elif action in {"add", "insert", "duplicate_path", "same_content_multi_path"}:
                object_id = _insert_doc_from_local(kb_id, item, user_id)
                applied.append({"target_path": item.get("target_path"), "object_id": object_id, "action": "add"})
            elif action in {"edit", "update", "path_mismatch"}:
                object_id, applied_action = _update_doc_from_local(item, user_id)
                applied.append({"target_path": item.get("target_path"), "object_id": object_id, "action": applied_action})
            elif action in {"delete", "remove"}:
                if str(item.get("branch_type") or "").upper() == "KIWIX":
                    object_id = _purge_kiwix_doc_from_sync(item, user_id)
                    applied.append({"target_path": item.get("target_path"), "object_id": object_id, "action": "kiwix_delete"})
                else:
                    object_id = _mark_deleted(item, user_id)
                    applied.append({"target_path": item.get("target_path"), "object_id": object_id, "action": "delete"})
            else:
                raise ValueError(f"Unsupported action: {action}")
        except Exception as exc:
            failed.append({"target_path": item.get("target_path"), "error": str(exc)})
    return {
        "applied_count": len(applied),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "applied": applied,
        "failed": failed,
        "skipped": skipped,
    }
