import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { http } from '../../services/http/client';
import { copyDoc, moveDoc, createNote } from '../../services/docs/docService';
import { addBookmark } from '../../services/bookmarks/bookmarkService';
import { dispatchOpenDocTab } from '../../utils/openDocTab';
import FolderPickerModal from '../modals/FolderPickerModal';
import folderIcon from '../../assets/icons/colorFolder.svg';
import openFolderIcon from '../../assets/icons/open-folder.svg';
import arrowRightIcon from '../../assets/icons/arrow-right.svg';
import arrowDownIcon from '../../assets/icons/arrow-down.svg';
import ContextMenuPortal from './projectsTree/ContextMenuPortal';
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
} from './projectsTree/treeUtils';

const MODE = {
  KB: 'kb',
};

export default function ProjectsTreePanel({ mode = MODE.KB, rootId = null }) {
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
  const [blankCtx, setBlankCtx] = useState(null);

  const [moveModalOpen, setMoveModalOpen] = useState(false);
  const [moveTarget, setMoveTarget] = useState(null);

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
      }));
      setRoots(arr);

      if (rootId == null) {
        const preferred = activeRootId ?? null;
        const exists = arr.some((r) => sameId(r.id, preferred));
        const nextId = exists ? preferred : arr[0]?.id ?? null;
        setActiveRootId(nextId);
      }
    } catch {
      setRoots([]);
      if (rootId == null) {
        setActiveRootId(null);
      }
    }
  }, [rootId, activeRootId]);

  const fetchTree = useCallback(async () => {
    const rid = rootId ?? activeRootId;
    if (!rid) return;

    setLoading(true);
    try {
      const res = await http.get(`/kb/trees/${toId(rid)}/tree`);
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
      if (activeRootId == null || !roots.some((r) => sameId(r.id, activeRootId))) {
        setActiveRootId(roots[0]?.id ?? null);
      }
    }

    pendingDefaultOpenRef.current = true;
    lastAppliedRootRef.current = null;

    setOpenMap(new Set());
    setSelectedIds(new Set());
    setActivePathIds([]);
  }, [roots, activeRootId, rootId]);

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
        setBlankCtx(null);
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

    // Default open: keep Guidebook aligned with other top-level folders.
    const excluded = new Set(['textbook']);

    const toExcludedKey = (raw) => {
      const s = String(raw || '').trim().toLowerCase();
      return s.replace(/^\s*\d+\s*[\.\)\-:]*\s*/, '');
    };

    const nextOpen = new Set();
    for (const n of nodes) {
      const key = toExcludedKey(n?.name);
      if (n?.is_folder && !excluded.has(key)) {
        nextOpen.add(n.id);
      }
    }

    setOpenMap(nextOpen);
    setSelectedIds(new Set());
    setActivePathIds([]);
  }, [nodes, rootId, activeRootId]);

  const onContext = (e, node) => {
    e.preventDefault();
    const pos = getContextMenuPos(e);
    setBlankCtx(null);
    setCtx({ ...pos, node });
  };

  const isWriteEnabled = () => true;

  const onCreateNode = async (parent = null) => {
    if (!isWriteEnabled()) {
      window.alert('Node creation is not supported in the current mode.');
      return;
    }
    const name = window.prompt('Enter the node name');
    if (!name) return;

    const rid = rootId ?? activeRootId;
    if (!rid) return;

    try {
      await http.post(`/kb/trees/${toId(rid)}/nodes`, {
        name,
        parent_id: parent ?? null,
        node_type: 'KB_FOLDER',
      });
      await fetchTree();
    } catch (err) {
      if (err?.status === 409) {
        window.alert('The same name already exists.');
      } else if (err?.status === 403) {
        window.alert('You do not have write permission. (Adding root subdirectories is only allowed for administrators)');
      } else {
        window.alert(`Node creation failed: ${err?.message || err}`);
      }
    }
  };

  const onRenameNode = async (node) => {
    if (!isWriteEnabled()) {
      window.alert('Renaming is not supported in the current mode.');
      return;
    }
    const name = window.prompt('Please enter a new name', node?.name || '');
    if (!name || name === node?.name) return;

    try {
      await http.patch(`/kb/trees/nodes/${node.id}`, { name });
      await fetchTree();
    } catch (err) {
      if (err?.status === 409) {
        window.alert('The same name already exists.');
      } else if (err?.status === 403) {
        window.alert('You do not have permission to change name.');
      } else {
        window.alert(`Rename failed: ${err?.message || err}`);
      }
    }
  };

  const onDeleteSelected = async () => {
    if (!isWriteEnabled()) {
      window.alert('Deletion is no supported in current mode.');
      return;
    }
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    if (!window.confirm(`Delete selected ${ids.length} nodes?`)) return;

    const rid = rootId ?? activeRootId;
    if (!rid) return;

    try {
      await http.delete(`/kb/trees/${toId(rid)}/nodes:bulk`, { node_ids: ids });
      setSelectedIds(new Set());
      setActivePathIds([]);
      await fetchTree();
    } catch (err) {
      if (err?.state === 403) {
        window.alert('You do not have permission to delete.');
      } else {
        window.alert(`Delete failed: ${err?.message || err}`);
      }
    }
  };

  const toSafeTitle = (title, docId) => {
    const t = String(title || '').trim();
    if (t) return t;
    if (docId != null) return `Doc ${docId}`;
    return 'Doc';
  };

  const dispatchOpenDoc = (
    docId,
    title,
    objectId = null,
    relPath = null,
    subPath = null,
    fileRight = null,
    fileNameForLookup = null,
    branchType = null
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
    });
  };

  const onOpenNode = (node) => {
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

      dispatchOpenDoc(docId, title, objectId, relPath, subPath, fileRight, fileNameForLookup, branchType);
    }
  };

  const onOpenInNewTab = (node) => {
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

    dispatchOpenDoc(docId, title, objectId, relPath, subPath, fileRight, fileNameForLookup, branchType);
  };

  const moveNodes = async (movingIds, newParent) => {
    if (!isWriteEnabled()) return;
    if (!Array.isArray(movingIds) || movingIds.length === 0) return;

    try {
      for (const id of movingIds) {
        await http.patch(`/kb/trees/nodes/${id}`, { parent_id: newParent ?? null });
      }
      await fetchTree();
    } catch (err) {
      const msg = err && err.message ? String(err.message) : '';
      if (err?.status === 409) {
        window.alert('Cannot be moved because the same name already exists.');
      } else if (err?.status === 400) {
        if (msg.includes('Parent is folder') || msg.toLowerCase().includes('parent')) {
          window.alert('Move failed: Parent must be a folder.');
        } else if (msg.includes('Descendants') || msg.toLowerCase().includes('descendant')) {
          window.alert('Move failed: Cannot set parent to child.');
        } else if (msg.includes('Yourself') || msg.toLowerCase().includes('itself')) {
          window.alert('Move failed: Cannot set parent to self.');
        } else {
          window.alert(`Move failed: ${msg || 'Request denied.'}`);
        }
      } else {
        window.alert(`Move failed: ${msg || err}`);
      }
      await fetchTree();
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

    const folderPathActive = node.is_folder && activePathSet.has(node.id);
    const indexingMeta = getFolderIndexingMeta(node);

    const iconSrc = node.is_folder ? (opened ? openFolderIcon : folderIcon) : getFileIcon(node);

    return (
      <li
        key={node.id}
        className={`t-node ${fileSelected ? 'file-selected' : ''} ${
          folderPathActive ? 'path-active' : ''
        } ${node.is_folder ? 'folder' : 'file'} ${hasChildren ? 'has-children' : 'no-children'}`}
        data-node-id={node.id}
        onContextMenu={(e) => {
          e.stopPropagation();
          onContext(e, node);
        }}
        title={node.name}
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
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            e.preventDefault();
            e.stopPropagation();
            e.currentTarget.click();
          }}
          onClick={(e) => {
            e.stopPropagation();

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
          <img className="node-icon" src={iconSrc} alt="" aria-hidden="true" />
          <span className="label">{node.name}</span>
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
          setCtx(null);
          const pos = getContextMenuPos(e);
          setBlankCtx(pos);
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
          <button
            className="ctx-item"
            onClick={() => {
              onOpenInNewTab(ctx.node);
              setCtx(null);
            }}
          >
            Open in new tab
          </button>
          <button
            className="ctx-item"
            onClick={async () => {
              const docId = ctx.node?.meta?.doc_id || ctx.node?.meta?.docId || ctx.node?.doc_id || ctx.node?.id;
              if (!docId) {
                window.alert('Select a file.');
                setCtx(null);
                return;
              }
              try {
                await copyDoc(docId, { new_name: `${ctx.node.name}_copy` });
                await fetchTree();
              } catch (e) {
                window.alert(`Copy failed: ${e?.message || e}`);
              } finally {
                setCtx(null);
              }
            }}
          >
            Create a copy
          </button>
          <button
            className="ctx-item"
            onClick={() => {
              setMoveTarget(ctx.node);
              setMoveModalOpen(true);
              setCtx(null);
            }}
          >
            Move to...
          </button>
          <button
            className="ctx-item"
            onClick={async () => {
              const docId = ctx.node?.meta?.doc_id || ctx.node?.meta?.docId || ctx.node?.doc_id || ctx.node?.id;
              if (!docId) {
                window.alert('Select file.');
                setCtx(null);
                return;
              }
              try {
                await addBookmark({
                  target_type: 'DOC_MASTER',
                  target_id: docId,
                  title: ctx.node.name,
                  preferred_node_id: ctx.node.id,
                });

                window.dispatchEvent(new Event('bookmarks:refresh'));
              } catch (e) {
                window.alert(`Bookmark failed: ${e?.message || e}`);
              } finally {
                setCtx(null);
              }
            }}
          >
            Bookmarks??
          </button>
          <button
            className="ctx-item"
            onClick={() => {
              onRenameNode(ctx.node);
              setCtx(null);
            }}
          >
            Change name
          </button>
          <button
            className="ctx-item danger"
            onClick={() => {
              setSelectedIds(new Set([ctx.node.id]));
              onDeleteSelected();
              setCtx(null);
            }}
          >
            Delete
          </button>
        </ContextMenuPortal>
      )}

      {blankCtx && (
        <ContextMenuPortal x={blankCtx.x} y={blankCtx.y}>
          <button
            className="ctx-item"
            onClick={async () => {
              try {
                const title = window.prompt('Enter a note title');
                if (!title) return;
                await createNote({ title });
                await fetchTree();
              } catch (e) {
                window.alert(`Note creation failed: ${e?.message || e}`);
              } finally {
                setBlankCtx(null);
              }
            }}
          >
            New note
          </button>
          <button
            className="ctx-item"
            onClick={() => {
              onCreateNode(null);
              setBlankCtx(null);
            }}
          >
            New folder
          </button>
        </ContextMenuPortal>
      )}

      <FolderPickerModal
        open={moveModalOpen}
        onClose={() => {
          setMoveModalOpen(false);
          setMoveTarget(null);
        }}
        activeRootId={rootId ?? activeRootId}
        title="Select folder to move"
        onPick={async (parentId) => {
          try {
            const n = moveTarget;
            if (n?.is_folder) {
              await moveNodes([n.id], parentId ?? null);
            } else {
              const docId = n?.meta?.doc_id || n?.meta?.docId || n?.doc_id || n?.id;
              if (docId) {
                await moveDoc(docId, { new_parent: parentId ?? null });
              } else {
                await moveNodes([n.id], parentId ?? null);
              }
            }
            await fetchTree();
          } catch (e) {
            window.alert(`Move failed: ${e?.message || e}`);
          } finally {
            setMoveModalOpen(false);
            setMoveTarget(null);
          }
        }}
      />
    </div>
  );
}
