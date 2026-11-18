import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { http } from '../../services/http/client';
import { addBookmark, createNode as createBookmarkNode } from '../../services/bookmarks/bookmarkService';
import { dispatchOpenDocTab } from '../../utils/openDocTab';
import useDownload from '../../hooks/useDownload';
import folderIcon from '../../assets/icons/colorFolder.svg';
import openFolderIcon from '../../assets/icons/open-folder.svg';
import arrowRightIcon from '../../assets/icons/arrow-right.svg';
import arrowDownIcon from '../../assets/icons/arrow-down.svg';
import ContextMenuPortal from './projectsTree/ContextMenuPortal';
import FolderPickerModal from '../modals/FolderPickerModal';
import VersionManagerModal from '../admin/kb/VersionManagerModal';
import {
  FOLDER_INDEXING_STATE_BY_PATH,
  INDEXING_STATE_META,
  buildLookupFilename,
  getContextMenuPos,
  getFileIcon,
  inferBranchTypeFromPathParts,
  normalizeFolderKey,
  normalizeNode,
  sameId,
  toId,
  usesGenericTextFileIcon,
} from './projectsTree/treeUtils';

const MODE = {
  KB: 'kb',
};

const DOC_STATUS_META = {
  deleted_pending: {
    label: 'Pending delete',
    title: 'This document is missing locally and is pending deletion. Restore it from Maintenance > Deleted Docs.',
  },
  inactive_path_missing: {
    label: 'Path missing',
    title: 'The local file path is missing. Reconnect or restore it from Maintenance > Deleted Docs.',
  },
};

const getDocStatusMeta = (node) => {
  if (node?.is_folder) return null;
  const status = String(node?.doc_status || node?.meta?.doc_status || '').trim().toLowerCase();
  return DOC_STATUS_META[status] || null;
};

export default function ProjectsTreePanel({ mode = MODE.KB, rootId = null, autoSelectRoot = true }) {
  const { downloadLatest } = useDownload();
  const [roots, setRoots] = useState([]);
  const [activeRootId, setActiveRootId] = useState(rootId ?? null);

  const [tree, setTree] = useState(null);
  const [loading, setLoading] = useState(false);
  const [openMap, setOpenMap] = useState(() => new Set());

  const [selectedIds, setSelectedIds] = useState(new Set());
  const [activePathIds, setActivePathIds] = useState([]);

  const pendingDefaultOpenRef = useRef(false);
  const lastAppliedRootRef = useRef(null);

  const [ctx, setCtx] = useState(null);
  const [hierarchyBusy, setHierarchyBusy] = useState(false);
  const [moveFolderTarget, setMoveFolderTarget] = useState(null);
  const [moveDocumentTarget, setMoveDocumentTarget] = useState(null);
  const [versionDocumentTarget, setVersionDocumentTarget] = useState(null);

  const [bookmarkConfirm, setBookmarkConfirm] = useState(null);
  const [bookmarkSubmitting, setBookmarkSubmitting] = useState(false);

  const nodes = useMemo(() => {
    const raw = tree?.nodes || tree?.items || [];
    return raw.map(normalizeNode);
  }, [tree]);

  const { idToParent, idToNode } = useMemo(() => {
    const parent = new Map();
    const nodeMap = new Map();

    const walk = (n, p = null) => {
      if (!n) return;
      parent.set(n.id, p);
      nodeMap.set(n.id, n);
      if (Array.isArray(n.children)) n.children.forEach((c) => walk(c, n.id));
    };

    nodes.forEach((r) => walk(r, r?.parent ?? null));
    return { idToParent: parent, idToNode: nodeMap };
  }, [nodes]);

  const getFolderPathParts = useCallback(
    (nodeId) => {
      const parts = [];
      let cur = nodeId;
      const guard = new Set();
      while (cur != null && !guard.has(cur)) {
        guard.add(cur);
        const n = idToNode.get(cur);
        if (!n) break;
        if (n.is_folder) parts.push(n.name);
        cur = idToParent.get(cur) ?? null;
      }
      return parts.reverse();
    },
    [idToNode, idToParent]
  );

  const fetchRoots = useCallback(async () => {
    try {
      const items = await http.get('/kb/trees');
      const arr = (Array.isArray(items) ? items : items?.items || []).map((k) => ({
        id: Number(k.kb_id ?? k.id ?? k.tree_id ?? k.kb_tree_id),
        name: k.name ?? k.title ?? `KB ${k.kb_id ?? k.id ?? ''}`,
        kb_type: k.kb_type ?? k.kbType ?? null,
        kb_owner_user_id: k.kb_owner_user_id ?? k.kbOwnerUserId ?? null,
      }));
      setRoots(arr);

      if (rootId == null && autoSelectRoot) {
        const preferred = activeRootId ?? null;
        const exists = arr.some((r) => sameId(r.id, preferred));
        const nextId = exists ? preferred : arr[0]?.id ?? null;
        setActiveRootId(nextId);
      }
    } catch {
      setRoots([]);
      if (rootId == null && autoSelectRoot) {
        setActiveRootId(null);
      }
    }
  }, [rootId, activeRootId, autoSelectRoot]);

  const fetchTree = useCallback(async () => {
    const rid = rootId ?? activeRootId;
    if (!rid) {
      setTree({ nodes: [] });
      return;
    }

    setLoading(true);
    try {
      const res = await http.get(`/kb/trees/${toId(rid)}/tree`, { include_empty_folders: false });
      setTree(res);
      setOpenMap((prev) => (prev.size ? prev : new Set()));
    } catch (err) {
      if (err?.status === 403) {
        setTree({ nodes: [] });
        window.alert('You do not have permission to view this KB.');
      } else {
        setTree(null);
      }
    } finally {
      setLoading(false);
    }
  }, [activeRootId, rootId]);

  useEffect(() => {
    const refreshCurrentTree = () => {
      fetchRoots();
      fetchTree();
    };

    window.addEventListener('kb:personal-created', refreshCurrentTree);
    window.addEventListener('kb:tree-refresh', refreshCurrentTree);
    return () => {
      window.removeEventListener('kb:personal-created', refreshCurrentTree);
      window.removeEventListener('kb:tree-refresh', refreshCurrentTree);
    };
  }, [fetchRoots, fetchTree]);

  useEffect(() => {
    setRoots([]);
    setTree(null);
    setActiveRootId(null);
    setSelectedIds(new Set());
    setActivePathIds([]);
    setOpenMap(new Set());
    pendingDefaultOpenRef.current = false;
    lastAppliedRootRef.current = null;
  }, [mode]);

  useEffect(() => {
    fetchRoots();
  }, [fetchRoots]);

  useEffect(() => {
    if (!roots?.length) return;

    if (rootId == null) {
      if (autoSelectRoot && (activeRootId == null || !roots.some((r) => sameId(r.id, activeRootId)))) {
        setActiveRootId(roots[0]?.id ?? null);
      }
    }

    pendingDefaultOpenRef.current = true;
    lastAppliedRootRef.current = null;

    setOpenMap(new Set());
    setSelectedIds(new Set());
    setActivePathIds([]);
  }, [roots, activeRootId, rootId, autoSelectRoot]);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const handler = (e) => {
      const menuEls = document.querySelectorAll('.ctx-menu');
      let inside = false;
      menuEls.forEach((el) => {
        if (el.contains(e.target)) inside = true;
      });
      if (!inside) {
        setCtx(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    const rid = rootId ?? activeRootId;
    if (!rid) return;
    if (!nodes?.length) return;

    if (!pendingDefaultOpenRef.current && lastAppliedRootRef.current === Number(rid)) return;

    pendingDefaultOpenRef.current = false;
    lastAppliedRootRef.current = Number(rid);

    const toExcludedKey = (raw) => {
      const s = String(raw || '').trim().toLowerCase();
      return s.replace(/^\s*\d+\s*[\.\)\-:]*\s*/, '');
    };

    const nextOpen = new Set();
    for (const n of nodes) {
      const key = toExcludedKey(n?.name);
      if (n?.is_folder) {
        nextOpen.add(n.id);
      }
    }

    setOpenMap(nextOpen);
    setSelectedIds(new Set());
    setActivePathIds([]);
  }, [nodes, rootId, activeRootId]);

  const onContext = (e, node) => {
    e.preventDefault();
    if (node?.disabled && !node?.is_folder) {
      setCtx(null);
      return;
    }
    const pos = getContextMenuPos(e);
    setCtx({ ...pos, node });
  };

  const toSafeTitle = (title, docId) => {
    const t = String(title || '').trim();
    if (t) return t;
    if (docId != null) return `Doc ${docId}`;
    return 'Doc';
  };

  const emitToast = (message, type = 'info') => {
    try {
      window.dispatchEvent(new CustomEvent('ui:toast', { detail: { message, type } }));
    } catch {}
  };

  const getStorageId = (node) =>
    node?.meta?.object_id ||
    node?.meta?.objectId ||
    node?.meta?.storage_object_id ||
    node?.meta?.storageObjectId ||
    null;

  const getDocumentId = (node) =>
    node?.meta?.doc_id ||
    node?.meta?.docId ||
    node?.doc_id ||
    null;

  const getNodeKbType = (node) =>
    String(node?.kb_type || node?.kbType || node?.meta?.kb_type || node?.meta?.kbType || '').trim().toLowerCase();

  const getNodeUploadMode = (node) =>
    String(node?.upload_mode || node?.uploadMode || node?.meta?.upload_mode || node?.meta?.uploadMode || '')
      .trim()
      .toLowerCase();

  const isPersonalKbNode = (node) => {
    let cur = node;
    const guard = new Set();
    while (cur && !guard.has(cur.id)) {
      guard.add(cur.id);
      const kbType = getNodeKbType(cur);
      const uploadMode = getNodeUploadMode(cur);
      if (kbType === 'personal' || kbType === 'person' || uploadMode === 'personal') return true;
      const parentId = cur.parent ?? idToParent.get(cur.id) ?? null;
      cur = parentId == null ? null : idToNode.get(parentId);
    }

    const rid = rootId ?? activeRootId;
    const root = roots.find((r) => sameId(r.id, rid));
    const rootType = String(root?.kb_type || root?.kbType || '').trim().toLowerCase();
    return rootType === 'personal' || rootType === 'person';
  };

  const selectedRootId = toId(rootId ?? activeRootId);
  const selectedRoot = roots.find((root) => sameId(root.id, selectedRootId));
  const canEditPersonalHierarchy = ['personal', 'person'].includes(
    String(selectedRoot?.kb_type || '').trim().toLowerCase()
  );

  const runHierarchyAction = async (action) => {
    if (!canEditPersonalHierarchy || !selectedRootId || hierarchyBusy) return;
    setHierarchyBusy(true);
    try {
      await action();
      await fetchTree();
    } catch (error) {
      window.alert(error?.userMessage || error?.message || 'Personal KB hierarchy update failed.');
    } finally {
      setHierarchyBusy(false);
      setCtx(null);
    }
  };

  const createPersonalFolder = (parentId = selectedRootId) => {
    const name = window.prompt('New folder name');
    if (!name?.trim()) return;
    runHierarchyAction(async () => {
      await http.post(`/kb/trees/${selectedRootId}/nodes`, {
        name: name.trim(),
        parent_id: toId(parentId) || selectedRootId,
        node_type: 'KB_FOLDER',
      });
      emitToast(`Folder "${name.trim()}" created.`, 'success');
    });
  };

  const renamePersonalFolder = (node) => {
    const name = window.prompt('Folder name', node?.name || '');
    if (!name?.trim() || name.trim() === String(node?.name || '').trim()) return;
    runHierarchyAction(async () => {
      await http.patch(`/kb/trees/nodes/${toId(node.id)}`, { name: name.trim() });
      emitToast(`Folder renamed to "${name.trim()}".`, 'success');
    });
  };

  const renamePersonalDocument = (node) => {
    const name = window.prompt('Document display name', node?.name || '');
    if (!name?.trim() || name.trim() === String(node?.name || '').trim()) return;
    runHierarchyAction(async () => {
      await http.patch(`/kb/trees/nodes/${toId(node.id)}`, { name: name.trim() });
      emitToast(`Document renamed to "${name.trim()}".`, 'success');
    });
  };

  const moveFolderExcludedIds = useMemo(() => {
    const excluded = [];
    const walk = (node) => {
      if (!node) return;
      excluded.push(toId(node.id));
      (node.children || []).forEach(walk);
    };
    walk(moveFolderTarget);
    return excluded.filter(Boolean);
  }, [moveFolderTarget]);

  const movePersonalFolder = (node) => {
    setCtx(null);
    setMoveFolderTarget(node);
  };

  const confirmMovePersonalFolder = (pickedFolderId) => {
    if (!moveFolderTarget) return;
    const targetId = pickedFolderId == null ? selectedRootId : toId(pickedFolderId);
    if (!targetId || sameId(targetId, moveFolderTarget.parent)) {
      setMoveFolderTarget(null);
      return;
    }
    const movingNode = moveFolderTarget;
    runHierarchyAction(async () => {
      await http.patch(`/kb/trees/nodes/${toId(movingNode.id)}`, { parent_id: targetId });
      emitToast(`Folder "${movingNode.name}" moved.`, 'success');
    });
    setMoveFolderTarget(null);
  };

  const deletePersonalFolder = (node) => {
    const confirmed = window.confirm(
      `Delete folder "${node?.name || ''}"? Documents below it will follow the existing logical-delete policy.`
    );
    if (!confirmed) return;
    runHierarchyAction(async () => {
      await http.post(`/kb/trees/${selectedRootId}/nodes/bulk-delete`, { node_ids: [toId(node.id)] });
      emitToast(`Folder "${node.name}" delete request completed.`, 'success');
    });
  };

  const deletePersonalDocument = (node) => {
    const nodeId = toId(node?.id);
    if (!nodeId) {
      window.alert('This document cannot be deleted because its node ID is missing.');
      setCtx(null);
      return;
    }
    const confirmed = window.confirm(
      `Delete document "${node?.name || ''}"? You can restore it from Personal KB Management before permanent deletion.`
    );
    if (!confirmed) return;
    runHierarchyAction(async () => {
      await http.post(`/kb/trees/${selectedRootId}/nodes/bulk-delete`, { node_ids: [nodeId] });
      emitToast(`Document "${node?.name || ''}" delete request completed.`, 'success');
      window.dispatchEvent(new CustomEvent('kb:personal-created'));
    });
  };

  const movePersonalDocument = (node) => {
    setCtx(null);
    setMoveDocumentTarget(node);
  };

  const managePersonalDocumentVersions = (node) => {
    const docId = getDocumentId(node);
    setCtx(null);
    if (!docId) {
      window.alert('This document cannot be versioned because its document ID is missing.');
      return;
    }
    setVersionDocumentTarget({
      id: toId(docId),
      title: toSafeTitle(node?.name, docId),
    });
  };

  const confirmMovePersonalDocument = (pickedFolderId) => {
    if (!moveDocumentTarget) return;
    const targetId = pickedFolderId == null ? selectedRootId : toId(pickedFolderId);
    if (!targetId || sameId(targetId, moveDocumentTarget.parent)) {
      setMoveDocumentTarget(null);
      return;
    }

    const movingNode = moveDocumentTarget;
    const documentId =
      getStorageId(movingNode) ||
      movingNode?.meta?.doc_id ||
      movingNode?.meta?.docId ||
      movingNode?.doc_id;

    if (!documentId) {
      window.alert('This document cannot be moved because its document ID is missing.');
      setMoveDocumentTarget(null);
      return;
    }

    runHierarchyAction(async () => {
      await http.post(`/docs/${documentId}/move`, { new_parent_node: targetId });
      emitToast(`Document "${movingNode.name}" moved.`, 'success');
      window.dispatchEvent(new CustomEvent('kb:personal-created'));
    });
    setMoveDocumentTarget(null);
  };
  const requestDownload = async (node) => {
    const docId = node?.meta?.doc_id || node?.meta?.docId || node?.doc_id || null;
    if (!docId) {
      window.alert('This document cannot be downloaded because its document ID is missing.');
      setCtx(null);
      return;
    }
    setCtx(null);
    try {
      await downloadLatest(docId);
      emitToast(`Download started: ${node?.name || `Document ${docId}`}`, 'success');
    } catch (error) {
      window.alert(error?.userMessage || error?.message || 'Download failed.');
    }
  };

  const countBookmarkFiles = (node) => {
    if (!node) return 0;
    if (isPersonalKbNode(node)) return 0;
    if (!node.is_folder) return !node.disabled && getStorageId(node) ? 1 : 0;
    return (node.children || []).reduce((sum, child) => sum + countBookmarkFiles(child), 0);
  };

  const addBookmarkFile = async (node, parentId = null) => {
    if (isPersonalKbNode(node)) return { added: 0, skipped: 1 };
    if (node?.disabled) return { added: 0, skipped: 1 };
    const storageId = getStorageId(node);
    if (!storageId) return { added: 0, skipped: 1 };
    const title = toSafeTitle(node?.name, storageId);
    await addBookmark({
      storage_id: storageId,
      title,
      parent_id: parentId,
    });
    return { added: 1, skipped: 0 };
  };

  const addBookmarkFolderTree = async (node, parentId = null) => {
    if (isPersonalKbNode(node)) return { added: 0, skipped: 1, folders: 0 };
    const folderName = toSafeTitle(node?.name, node?.id);
    const created = await createBookmarkNode({
      name: folderName,
      type: 'FOLDER',
      parent_id: parentId,
    });
    const nextParentId = created?.id ?? created?.node_id;
    let added = 0;
    let skipped = 0;
    let folders = 1;

    for (const child of node.children || []) {
      if (child?.is_folder) {
        const result = await addBookmarkFolderTree(child, nextParentId);
        added += result.added;
        skipped += result.skipped;
        folders += result.folders;
      } else {
        const result = await addBookmarkFile(child, nextParentId);
        added += result.added;
        skipped += result.skipped;
      }
    }

    return { added, skipped, folders };
  };

  const dispatchOpenDoc = (
    docId,
    title,
    objectId = null,
    relPath = null,
    subPath = null,
    fileRight = null,
    fileNameForLookup = null,
    branchType = null,
    isPersonal = false
  ) => {
    if (!docId) return;
    const safeTitle = toSafeTitle(title, docId);
    dispatchOpenDocTab({
      docId,
      objectId: objectId ?? docId,
      title: safeTitle,
      docTitle: fileNameForLookup ?? safeTitle,
      docRelPath: relPath ?? null,
      docSubPath: subPath ?? null,
      docFileRight: fileRight ?? null,
      docBranchType: branchType ?? null,
      isPersonal: !!isPersonal,
      kbType: isPersonal ? 'personal' : null,
      uploadMode: isPersonal ? 'personal' : null,
    });
  };

  const onOpenNode = (node) => {
    if (node?.disabled && !node?.is_folder) return;
    if (node.is_folder) {
      setOpenMap((prev) => {
        const next = new Set(prev);
        if (next.has(node.id)) next.delete(node.id);
        else next.add(node.id);
        return next;
      });
      return;
    }
    const docId = node?.meta?.doc_id || node?.meta?.docId || node?.doc_id || node?.id;
    const objectId =
      node?.meta?.object_id ||
      node?.meta?.objectId ||
      node?.meta?.storage_object_id ||
      node?.meta?.storageObjectId ||
      null;

    if (docId) {
      const title = toSafeTitle(node?.name, docId);
      const baseName = node?.meta?.storage_object_name || node?.name || '';
      const extFromMeta = node?.meta?.storage_object_file_right || '';
      let fileRight = extFromMeta || null;
      if (!fileRight) {
        const m = String(node?.name || '').match(/\.([A-Za-z0-9]+)$/);
        if (m) fileRight = m[1];
      }
      const fileNameForLookup = buildLookupFilename(baseName, fileRight);
      const pathParts = getFolderPathParts(node?.parent ?? null);
      const branchTypeRaw = String(
        node?.meta?.branch_type || node?.meta?.branchType || node?.branch_type || node?.branchType || ''
      ).toUpperCase();
      const branchType = branchTypeRaw || inferBranchTypeFromPathParts(pathParts) || 'GENERAL';

      let relPath = node?.meta?.storage_object_relative_path ?? null;
      let subPath = node?.meta?.storage_object_sub_path ?? null;
      if (!relPath || subPath == null) {
        const parts = getFolderPathParts(node?.parent ?? null);
        if (!relPath && parts.length) relPath = parts[0];
        if (subPath == null && parts.length > 1) subPath = parts.slice(1).join('/');
      }
      if (subPath != null && String(subPath).trim() === '') subPath = null;

      dispatchOpenDoc(docId, title, objectId, relPath, subPath, fileRight, fileNameForLookup, branchType, isPersonalKbNode(node));
    }
  };

  const onOpenInNewTab = (node) => {
    if (node?.disabled && !node?.is_folder) return;
    const docId = node?.meta?.doc_id || node?.meta?.docId || node?.doc_id || node?.id;
    const objectId =
      node?.meta?.object_id ||
      node?.meta?.objectId ||
      node?.meta?.storage_object_id ||
      node?.meta?.storageObjectId ||
      null;
    if (!docId) return;
    const title = toSafeTitle(node?.name, docId);
    const baseName = node?.meta?.storage_object_name || node?.name || '';
    const extFromMeta = node?.meta?.storage_object_file_right || '';
    let fileRight = extFromMeta || null;
    if (!fileRight) {
      const m = String(node?.name || '').match(/\.([A-Za-z0-9]+)$/);
      if (m) fileRight = m[1];
    }
    const fileNameForLookup = buildLookupFilename(baseName, fileRight);
    const pathParts = getFolderPathParts(node?.parent ?? null);
    const branchTypeRaw = String(
      node?.meta?.branch_type || node?.meta?.branchType || node?.branch_type || node?.branchType || ''
    ).toUpperCase();
    const branchType = branchTypeRaw || inferBranchTypeFromPathParts(pathParts) || 'GENERAL';

    let relPath = node?.meta?.storage_object_relative_path ?? null;
    let subPath = node?.meta?.storage_object_sub_path ?? null;
    if (!relPath || subPath == null) {
      const parts = getFolderPathParts(node?.parent ?? null);
      if (!relPath && parts.length) relPath = parts[0];
      if (subPath == null && parts.length > 1) subPath = parts.slice(1).join('/');
    }
    if (subPath != null && String(subPath).trim() === '') subPath = null;

    dispatchOpenDoc(docId, title, objectId, relPath, subPath, fileRight, fileNameForLookup, branchType, isPersonalKbNode(node));
  };

  const requestAddBookmark = (node) => {
    if (isPersonalKbNode(node) && node?.is_folder) {
      window.alert('My KB folders cannot be bookmarked. Select a document instead.');
      return;
    }
    if (node?.disabled && !node?.is_folder) {
      window.alert('This document is unavailable and cannot be bookmarked.');
      return;
    }
    if (node?.is_folder) {
      setBookmarkConfirm({
        type: 'folder',
        node,
        title: toSafeTitle(node?.name, node?.id),
        fileCount: countBookmarkFiles(node),
      });
      return;
    }

    const storageId = getStorageId(node);
    if (!storageId) {
      window.alert('No storage object is linked to this file.');
      return;
    }
    const title = toSafeTitle(node?.name, storageId);
    setBookmarkConfirm({ node, storageId, title });
  };

  const confirmAddBookmark = async () => {
    if (!bookmarkConfirm || bookmarkSubmitting) return;
    setBookmarkSubmitting(true);
    try {
      if (bookmarkConfirm.type === 'folder') {
        const result = await addBookmarkFolderTree(bookmarkConfirm.node);
        emitToast(
          `Added "${bookmarkConfirm.title}" to bookmarks. ${result.folders} folder(s), ${result.added} file(s).`,
          'success'
        );
      } else {
        await addBookmark({
          storage_id: bookmarkConfirm.storageId,
          title: bookmarkConfirm.title,
        });
        emitToast(`Added "${bookmarkConfirm.title}" to bookmarks.`, 'success');
      }
      window.dispatchEvent(new Event('bookmarks:refresh'));
      window.dispatchEvent(new CustomEvent('bookmarks:open-panel', { detail: { storageId: bookmarkConfirm.storageId } }));
      setBookmarkConfirm(null);
    } catch (e) {
      window.alert(`Bookmark failed: ${e?.message || e}`);
    } finally {
      setBookmarkSubmitting(false);
    }
  };

  const getPathIds = useCallback(
    (nodeId) => {
      const path = [];
      let cur = nodeId;
      const guard = new Set();
      while (cur != null && !guard.has(cur)) {
        guard.add(cur);
        path.push(cur);
        cur = idToParent.get(cur) ?? null;
      }
      return path.reverse();
    },
    [idToParent]
  );

  const syncActivePath = useCallback(
    (node) => {
      if (!node?.id) return;
      const path = getPathIds(node.id);
      setActivePathIds(path);

      const folderIds = path.filter((id) => {
        const n = idToNode.get(id);
        return n?.is_folder;
      });

      setOpenMap((prev) => {
        const next = new Set(prev);
        folderIds.forEach((id) => next.add(id));
        return next;
      });
    },
    [getPathIds, idToNode]
  );

  const activePathSet = useMemo(() => new Set(activePathIds), [activePathIds]);

  const getFolderIndexingMeta = useCallback(
    (node) => {
      if (!node?.is_folder) return null;
      const pathKey = getFolderPathParts(node.id).map(normalizeFolderKey).join('/');
      const state = FOLDER_INDEXING_STATE_BY_PATH[pathKey];
      if (!state) return null;
      return INDEXING_STATE_META[state] || null;
    },
    [getFolderPathParts]
  );

  const renderNode = (node) => {
    const fileSelected = !node.is_folder && selectedIds.has(node.id);
    const opened = node.is_folder ? openMap.has(node.id) : false;
    const hasChildren = node.is_folder && Array.isArray(node.children) && node.children.length > 0;
    const statusMeta = getDocStatusMeta(node);
    const unavailable = Boolean(statusMeta) || Boolean(node.disabled && !node.is_folder);

    const folderPathActive = node.is_folder && activePathSet.has(node.id);
    const indexingMeta = getFolderIndexingMeta(node);

    const iconSrc = node.is_folder ? (opened ? openFolderIcon : folderIcon) : getFileIcon(node);

    return (
      <li
        key={node.id}
        className={`t-node ${fileSelected ? 'file-selected' : ''} ${
          folderPathActive ? 'path-active' : ''
        } ${node.is_folder ? 'folder' : 'file'} ${hasChildren ? 'has-children' : 'no-children'} ${
          unavailable ? 'is-unavailable' : ''
        }`}
        data-node-id={node.id}
        onContextMenu={(e) => {
          e.stopPropagation();
          onContext(e, node);
        }}
        title={statusMeta?.title || node.name}
      >
        {hasChildren ? (
          <button
            type="button"
            className="twisty"
            onClick={(e) => {
              e.stopPropagation();
              onOpenNode(node);
            }}
            aria-label={opened ? 'Collapse' : 'Expand'}
          >
            <img className="twisty-icon" src={opened ? arrowDownIcon : arrowRightIcon} alt="" aria-hidden="true" />
          </button>
        ) : (
          <span className="twisty-spacer" aria-hidden="true" />
        )}

        <span
          className="leaf"
          role="button"
          tabIndex={unavailable ? -1 : 0}
          aria-disabled={unavailable}
          onKeyDown={(e) => {
            if (unavailable) return;
            if (e.key !== 'Enter' && e.key !== ' ') return;
            e.preventDefault();
            e.stopPropagation();
            e.currentTarget.click();
          }}
          onClick={(e) => {
            e.stopPropagation();
            if (unavailable) return;

            if (e.ctrlKey || e.metaKey) {
              const next = new Set(selectedIds);
              next.has(node.id) ? next.delete(node.id) : next.add(node.id);
              setSelectedIds(next);
              return;
            }

            if (node.is_folder) {
              // Make the full name-area clickable for open/close.
              // Avoid re-opening on close (syncActivePath forces path folders open).
              setOpenMap((prev) => {
                const next = new Set(prev);
                if (next.has(node.id)) next.delete(node.id);
                else next.add(node.id);
                return next;
              });
              setSelectedIds(new Set());
              if (!opened) syncActivePath(node);
            } else {
              setSelectedIds(new Set([node.id]));
              syncActivePath(node);
              onOpenNode(node);
            }
          }}
        >
          <img
            className={`node-icon ${!node.is_folder && usesGenericTextFileIcon(node) ? 'node-icon--text' : ''}`}
            src={iconSrc}
            alt=""
            aria-hidden="true"
          />
          <span className="label">{node.name}</span>
          {statusMeta && <span className="node-status-badge">{statusMeta.label}</span>}
          {indexingMeta && (
            <span
              className={`node-indexing-badge ${indexingMeta.className}`}
              title={indexingMeta.tooltip}
              aria-label={indexingMeta.tooltip}
            >
              <img src={indexingMeta.icon} alt="" aria-hidden="true" />
            </span>
          )}
        </span>

        {opened && !!node.children?.length && <ul className="tree-children">{node.children.map(renderNode)}</ul>}
      </li>
    );
  };

  return (
    <div
      className="panel"
      style={{ height: '100%' }}
      onClick={(e) => {
        const onNode = e.target.closest?.('.t-node');
        if (!onNode) {
          setSelectedIds(new Set());
        }
      }}
      onContextMenu={(e) => {
        const onNode = e.target.closest?.('.t-node');
        if (!onNode) {
          e.preventDefault();
          if (canEditPersonalHierarchy) {
            const pos = getContextMenuPos(e);
            setCtx({ ...pos, node: null, root: true });
          } else {
            setCtx(null);
          }
        }
      }}
    >
      <div className="card scroll" style={{ overflow: 'auto' }}>
        {loading ? (
          <div className="loading">Loading...</div>
        ) : !nodes?.length ? (
          <div className="empty">Empty</div>
        ) : (
          <ul className="tree">{nodes.map(renderNode)}</ul>
        )}
      </div>

      {ctx && (
        <ContextMenuPortal x={ctx.x} y={ctx.y}>
          {canEditPersonalHierarchy && ctx.root && (
            <button
              className="ctx-item"
              disabled={hierarchyBusy}
              onClick={() => createPersonalFolder(selectedRootId)}
            >
              New folder
            </button>
          )}
          {canEditPersonalHierarchy && ctx.node?.is_folder && (
            <>
              <button className="ctx-item" disabled={hierarchyBusy} onClick={() => createPersonalFolder(ctx.node.id)}>
                New subfolder
              </button>
              <button className="ctx-item" disabled={hierarchyBusy} onClick={() => renamePersonalFolder(ctx.node)}>
                Rename folder
              </button>
              <button className="ctx-item" disabled={hierarchyBusy} onClick={() => movePersonalFolder(ctx.node)}>
                Move folder
              </button>
              <button className="ctx-item" disabled={hierarchyBusy} onClick={() => deletePersonalFolder(ctx.node)}>
                Delete folder
              </button>
            </>
          )}
          {ctx.node && !ctx.node.is_folder && (
            <>
              <button
                className="ctx-item"
                onClick={() => {
                  onOpenInNewTab(ctx.node);
                  setCtx(null);
                }}
              >
                Open in new tab
              </button>
              <button className="ctx-item" onClick={() => requestDownload(ctx.node)}>
                Download original
              </button>
              {canEditPersonalHierarchy && isPersonalKbNode(ctx.node) && (
                <>
                  <button
                    className="ctx-item"
                    disabled={hierarchyBusy}
                    onClick={() => renamePersonalDocument(ctx.node)}
                  >
                    Rename document
                  </button>
                  <button
                    className={'ctx-item'}
                    disabled={hierarchyBusy}
                    onClick={() => managePersonalDocumentVersions(ctx.node)}
                  >
                    Version management
                  </button>
                  <button
                    className="ctx-item"
                    disabled={hierarchyBusy}
                    onClick={() => movePersonalDocument(ctx.node)}
                  >
                    Move document
                  </button>
                  <button
                    className="ctx-item"
                    disabled={hierarchyBusy}
                    onClick={() => deletePersonalDocument(ctx.node)}
                  >
                    Delete document
                  </button>
                </>
              )}
            </>
          )}
          {ctx.node && (!isPersonalKbNode(ctx.node) || !ctx.node?.is_folder) && (
            <button
              className="ctx-item"
              onClick={() => {
                requestAddBookmark(ctx.node);
                setCtx(null);
              }}
            >
              Add to bookmarks
            </button>
          )}
        </ContextMenuPortal>
      )}

      <FolderPickerModal
        open={Boolean(moveFolderTarget)}
        onClose={() => {
          if (!hierarchyBusy) setMoveFolderTarget(null);
        }}
        onPick={confirmMovePersonalFolder}
        activeRootId={selectedRootId}
        title={moveFolderTarget ? `Move "${moveFolderTarget.name}"` : 'Move folder'}
        rootLabel={`${selectedRoot?.name || 'Personal KB'} (root)`}
        excludeIds={moveFolderExcludedIds}
        initialSelected={
          moveFolderTarget && !sameId(moveFolderTarget.parent, selectedRootId)
            ? toId(moveFolderTarget.parent)
            : null
        }
      />

      <FolderPickerModal
        open={Boolean(moveDocumentTarget)}
        onClose={() => {
          if (!hierarchyBusy) setMoveDocumentTarget(null);
        }}
        onPick={confirmMovePersonalDocument}
        activeRootId={selectedRootId}
        title={moveDocumentTarget ? `Move "${moveDocumentTarget.name}"` : 'Move document'}
        rootLabel={`${selectedRoot?.name || 'Personal KB'} (root)`}
        initialSelected={
          moveDocumentTarget && !sameId(moveDocumentTarget.parent, selectedRootId)
            ? toId(moveDocumentTarget.parent)
            : null
        }
      />
      <VersionManagerModal
        open={Boolean(versionDocumentTarget)}
        docId={versionDocumentTarget?.id}
        docTitle={versionDocumentTarget?.title}
        onClose={() => setVersionDocumentTarget(null)}
      />
      {bookmarkConfirm && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-label="Add bookmark confirmation">
            <div className="modal-head">
              <div className="modal-title">Add Bookmark</div>
            </div>
            <div className="modal-body" style={{ padding: '18px 20px', background: 'var(--bg-elev, #fff)' }}>
              <div style={{ fontSize: 14, lineHeight: 1.5, color: 'var(--text, #222)' }}>
                {bookmarkConfirm.type === 'folder' ? (
                  <>
                    Add <strong>"{bookmarkConfirm.title}"</strong> and all nested folders/files to bookmarks?
                    <br />
                    <span style={{ color: 'var(--muted, #666)' }}>
                      {bookmarkConfirm.fileCount} file(s) will be included.
                    </span>
                  </>
                ) : (
                  <>
                    Add <strong>"{bookmarkConfirm.title}"</strong> to bookmarks?
                  </>
                )}
              </div>
            </div>
            <div className="modal-foot">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setBookmarkConfirm(null)}
                disabled={bookmarkSubmitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn"
                onClick={confirmAddBookmark}
                disabled={bookmarkSubmitting}
              >
                {bookmarkSubmitting ? 'Adding...' : 'Yes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
