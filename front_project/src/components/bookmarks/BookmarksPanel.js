import React, { useCallback, useEffect, useState, useRef } from 'react';
import ReactDOM from 'react-dom';
import '../../styles/components/bookmarks.css';
import {
  getBookmarkTree,
  createNode,
  patchNode,
  deleteNodes,
} from '../../services/bookmarks/bookmarkService';
import { dispatchOpenDocTab } from '../../utils/openDocTab';

import ArrowRightIcon from '../../assets/icons/arrow-right.svg';
import ArrowDownIcon from '../../assets/icons/arrow-down.svg';

import FolderClosedIcon from '/src/assets/icons/colorFolder.svg';
import FolderOpenIcon from '/src/assets/icons/open-folder.svg';
import PdfIcon from '/src/assets/icons/colorSquarePDF.svg';
import HwpIcon from '/src/assets/icons/hwp.svg';
import DocxIcon from '/src/assets/icons/docx.svg';
import XlsIcon from '/src/assets/icons/xls.svg';
import MdIcon from '/src/assets/icons/html.svg';
import TextFileIcon from '/src/assets/icons/textFile.svg';

const DRAG_MIME = 'application/x-obs-bmk-node';
const CONTEXT_MENU_W = 200;
const CONTEXT_MENU_H = 200;
const CONTEXT_MENU_MARGIN = 8;

function getContextMenuPos(e) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let x = e.clientX;
  let y = e.clientY;

  if (x + CONTEXT_MENU_W + CONTEXT_MENU_MARGIN > vw) {
    x = vw - CONTEXT_MENU_W - CONTEXT_MENU_MARGIN;
  }
  if (y + CONTEXT_MENU_H + CONTEXT_MENU_MARGIN > vh) {
    y = vh - CONTEXT_MENU_H - CONTEXT_MENU_MARGIN;
  }
  if (x < CONTEXT_MENU_MARGIN) x = CONTEXT_MENU_MARGIN;
  if (y < CONTEXT_MENU_MARGIN) y = CONTEXT_MENU_MARGIN;

  return { x, y };
}

function ContextMenuPortal({ x, y, children }) {
  if (typeof document === 'undefined') return null;
  const style = { position: 'fixed', top: y, left: x, zIndex: 100000 };
  return ReactDOM.createPortal(
    <div className="ctx-menu" style={style}>
      {children}
    </div>,
    document.body
  );
}

function normalizeNode(n) {
  const children = (n.children || []).map(normalizeNode);
  return { ...n, children };
}

function collectDocIdsFromTree(nodes = []) {
  const out = [];
  const stack = Array.isArray(nodes) ? [...nodes] : [];
  while (stack.length) {
    const cur = stack.pop();
    if (!cur) continue;
    if ((cur?.node_type || '').toUpperCase() === 'ITEM') {
      const raw =
        cur?.meta?.storage_id ||
        cur?.meta?.object_id ||
        cur?.meta?.objectId ||
        cur?.meta?.storage_object_id ||
        cur?.meta?.storageObjectId ||
        cur?.meta?.target_id;
      const n = Number(raw);
      if (Number.isFinite(n)) out.push(n);
    }
    if (Array.isArray(cur.children) && cur.children.length) {
      stack.push(...cur.children);
    }
  }
  return Array.from(new Set(out));
}

const getFileExt = (node) => {
  const extFromMeta = String(
    node?.meta?.storage_object_file_right || node?.meta?.file_right || node?.meta?.fileRight || ''
  )
    .trim()
    .replace(/^\./, '')
    .toLowerCase();
  if (extFromMeta) return extFromMeta;

  const name = String(node?.name || '').trim();
  const m = name.match(/\.([A-Za-z0-9]+)$/);
  if (m?.[1]) return String(m[1]).toLowerCase();

  const mime = String(node?.meta?.mime_type || node?.meta?.mimeType || '').toLowerCase();
  if (!mime) return '';
  if (mime.includes('pdf')) return 'pdf';
  if (mime.includes('hwp')) return 'hwp';
  if (mime.includes('msword') || mime.includes('wordprocessingml')) return 'docx';
  if (mime.includes('excel') || mime.includes('spreadsheetml')) return 'xls';
  if (mime.includes('markdown')) return 'md';
  return '';
};

const getFileIcon = (node) => {
  const ext = getFileExt(node);
  if (ext === 'pdf') return PdfIcon;
  if (ext === 'hwp' || ext === 'hwpx') return HwpIcon;
  if (ext === 'doc' || ext === 'docx') return DocxIcon;
  if (ext === 'xls' || ext === 'xlsx') return XlsIcon;
  if (ext === 'md') return MdIcon;
  return TextFileIcon;
};

const usesGenericTextFileIcon = (node) => getFileIcon(node) === TextFileIcon;

const toSafeTitle = (title, docId) => {
  const t = String(title || '').trim();
  if (t) return t;
  if (docId != null) return `Doc ${docId}`;
  return 'Doc';
};

const buildLookupFilename = (baseName, ext) => {
  const base = String(baseName || '').trim();
  if (!base) return '';
  const right = String(ext || '').trim().replace(/^\./, '');
  if (!right) return base;
  const lowerBase = base.toLowerCase();
  const lowerExt = right.toLowerCase();
  if (lowerBase.endsWith(`.${lowerExt}`)) return base;
  return `${base}.${right}`;
};

const dispatchOpenDoc = ({
  docId,
  title,
  objectId = null,
  docTitle = null,
  docRelPath = null,
  docSubPath = null,
  docFileRight = null,
  docStoredPath = null,
  docBranchType = null,
}) => {
  if (!docId) return;
  const safeTitle = toSafeTitle(title, docId);
  dispatchOpenDocTab({
    docId,
    objectId: objectId ?? null,
    title: safeTitle,
    docTitle: docTitle || safeTitle,
    docRelPath: docRelPath ?? null,
    docSubPath: docSubPath ?? null,
    docFileRight: docFileRight ?? null,
    docStoredPath: docStoredPath ?? null,
    docBranchType: docBranchType ?? null,
  });
};

function safeParseIdsFromAny(dt) {
  const tryParse = (raw) => {
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.map((v) => Number(v)).filter((v) => Number.isFinite(v));
    } catch {
      return [];
    }
  };

  const a = tryParse(dt?.getData?.(DRAG_MIME));
  if (a.length) return a;

  const b = tryParse(dt?.getData?.('text/plain'));
  if (b.length) return b;

  return [];
}

function setDragBundle(dt, ids) {
  try {
    const payload = JSON.stringify(ids);
    dt.setData(DRAG_MIME, payload);
    dt.setData('text/plain', payload);
    dt.effectAllowed = 'move';
  } catch {}
}

export default function BookmarksPanel() {
  const [tree, setTree] = useState([]);
  const [loadingT, setLoadingT] = useState(false);

  const [openMap, setOpenMap] = useState(() => new Set());
  const [selectedIds, setSelectedIds] = useState(new Set());

  const dragBundleRef = useRef([]);
  const rootDroppableOver = useRef(false);
  const [rootDropOver, setRootDropOver] = useState(false);

  const [ctx, setCtx] = useState(null);
  const [blankCtx, setBlankCtx] = useState(null);

  const emitBookmarkSelection = useCallback((docIds) => {
    const ids = Array.isArray(docIds)
      ? docIds.map((v) => Number(v)).filter((v) => Number.isFinite(v))
      : [];
    window.dispatchEvent(
      new CustomEvent('bookmarks:selected-doc-ids', {
        detail: { docIds: ids },
      })
    );
  }, []);

  const loadTree = useCallback(async () => {
    setLoadingT(true);
    try {
      const res = await getBookmarkTree();
      const nodes = res?.nodes || [];
      const normalized = nodes.map(normalizeNode);
      setTree(normalized);
      setOpenMap(new Set());
      setSelectedIds(new Set());
      emitBookmarkSelection(collectDocIdsFromTree(normalized));
    } catch {
      setTree([]);
      emitBookmarkSelection([]);
    } finally {
      setLoadingT(false);
    }
  }, [emitBookmarkSelection]);

  useEffect(() => {
    loadTree();
  }, [loadTree]);

  useEffect(() => {
    const onRefresh = () => {
      loadTree();
    };
    window.addEventListener('bookmarks:refresh', onRefresh);
    return () => window.removeEventListener('bookmarks:refresh', onRefresh);
  }, [loadTree]);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const handler = (e) => {
      const menus = document.querySelectorAll('.ctx-menu');
      let inside = false;
      menus.forEach((m) => {
        if (m.contains(e.target)) inside = true;
      });
      if (!inside) {
        setCtx(null);
        setBlankCtx(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggleFolder = (id) => {
    setOpenMap((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const openBookmark = (node) => {
    if (!node) return;
    const meta = node?.meta || {};
    const objectId =
      meta?.storage_id ||
      meta?.object_id ||
      meta?.objectId ||
      meta?.storage_object_id ||
      meta?.storageObjectId ||
      null;
    const docId =
      meta?.doc_id ||
      meta?.doc_master_id ||
      objectId;
    if (!docId && !objectId) {
      window.alert('Document information not found.');
      return;
    }
    const fileRightRaw =
      meta?.storage_object_file_right ||
      meta?.file_right ||
      meta?.fileRight ||
      null;
    const fileRight = String(fileRightRaw || '').trim().replace(/^\./, '') || null;
    const baseName = meta?.storage_object_name || meta?.doc_title || node?.name || '';
    const lookupTitle = buildLookupFilename(baseName, fileRight);
    const docRelPath =
      meta?.storage_object_relative_path ??
      meta?.relative_path ??
      meta?.doc_rel_path ??
      null;
    const docSubPath =
      meta?.storage_object_sub_path ??
      meta?.sub_path ??
      meta?.doc_sub_path ??
      null;
    const docBranchType =
      meta?.branch_type ??
      meta?.doc_branch_type ??
      null;
    const docStoredPath =
      meta?.stored_path ??
      meta?.doc_stored_path ??
      null;

    dispatchOpenDoc({
      docId,
      title: toSafeTitle(node?.name, docId),
      objectId,
      docTitle: lookupTitle || toSafeTitle(node?.name, docId),
      docRelPath,
      docSubPath,
      docFileRight: fileRight,
      docStoredPath,
      docBranchType,
    });
  };

  const renameNode = async (node) => {
    if (!node) return;
    const name = window.prompt('New name', node.name);
    if (!name || name === node.name) return;
    await patchNode(node.id, { name });
    await loadTree();
  };

  const deleteNode = async (node) => {
    if (!node) return;
    if (!window.confirm('Delete?')) return;
    await deleteNodes([node.id]);
    await loadTree();
  };

  const createFolder = async (parentId = null) => {
    const name = window.prompt('Folder name');
    if (!name) return;
    await createNode({
      name,
      type: 'FOLDER',
      parent_id: parentId,
    });
    await loadTree();
  };

  const moveNodes = async (movingIds, newParentId) => {
    if (!movingIds || !movingIds.length) return;
    try {
      for (const id of movingIds) {
        if (newParentId === id) continue;
        await patchNode(id, { parent_id: newParentId });
      }
      dragBundleRef.current = [];
      await loadTree();
    } catch (e) {
      window.alert(`Move failed: ${e?.message || e}`);
      dragBundleRef.current = [];
      await loadTree();
    }
  };

  const onDropToNode = async (movingIds, dropTargetNode) => {
    const isFolderNode =
      dropTargetNode.isFolder ||
      dropTargetNode.is_folder ||
      dropTargetNode.node_type === 'FOLDER';
    const newParent = isFolderNode ? dropTargetNode.id : dropTargetNode.parent ?? null;
    await moveNodes(movingIds, newParent);
  };

  const onDropToRoot = async (movingIds) => {
    await moveNodes(movingIds, null);
  };

  const buildDragBundle = useCallback(
    (nodeId) => {
      const id = Number(nodeId);
      if (!Number.isFinite(id)) return [];
      const selected = Array.from(selectedIds)
        .map((v) => Number(v))
        .filter((v) => Number.isFinite(v));
      if (selected.length && selected.includes(id)) return selected;
      return [id];
    },
    [selectedIds]
  );

  const getMovingIds = (dt) => {
    const ids = safeParseIdsFromAny(dt);
    if (ids.length) return ids;
    const refIds = Array.isArray(dragBundleRef.current) ? dragBundleRef.current : [];
    return refIds.map((v) => Number(v)).filter((v) => Number.isFinite(v));
  };

  const onRootDragOver = (e) => {
    const ids = getMovingIds(e.dataTransfer);
    if (!ids.length) return;

    const onNode = e.target.closest?.('.t-node');
    if (onNode) return;

    e.preventDefault();
    if (!rootDroppableOver.current) {
      rootDroppableOver.current = true;
      setRootDropOver(true);
    }
    e.dataTransfer.dropEffect = 'move';
  };

  const onRootDragLeave = (e) => {
    const related = e.relatedTarget;
    if (related && e.currentTarget.contains(related)) return;

    if (rootDroppableOver.current) {
      rootDroppableOver.current = false;
      setRootDropOver(false);
    }
  };

  const onRootDrop = async (e) => {
    const ids = getMovingIds(e.dataTransfer);
    rootDroppableOver.current = false;
    setRootDropOver(false);
    if (!ids.length) return;

    const onNode = e.target.closest?.('.t-node');
    if (onNode) return;

    e.preventDefault();
    await onDropToRoot(ids);
  };

  const renderNode = (n) => {
    const isFolderNode = n.node_type === 'FOLDER';
    const opened = isFolderNode && openMap.has(n.id);
    const hasChildren = isFolderNode && n.children?.length;

    const selected = selectedIds.has(n.id);

    return (
      <li
        key={n.id}
        className={`t-node ${isFolderNode ? 'folder' : 'file'} ${selected ? 'file-selected' : ''}`}
        draggable
        onDragStart={(e) => {
          e.stopPropagation();
          const bundle = buildDragBundle(n.id);
          dragBundleRef.current = bundle;
          setDragBundle(e.dataTransfer, bundle);
        }}
        onDragEnd={() => {
          dragBundleRef.current = [];
        }}
        onDragOver={(e) => {
          const moving = getMovingIds(e.dataTransfer);
          if (!moving.length) return;

          if (!isFolderNode) return;
          if (moving.includes(Number(n.id))) return;

          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
        }}
        onDrop={async (e) => {
          const moving = getMovingIds(e.dataTransfer);
          if (!moving.length) return;

          e.preventDefault();
          e.stopPropagation();
          await onDropToNode(moving, n);
        }}
        onClick={(e) => {
          e.stopPropagation();
          setSelectedIds(new Set([n.id]));
          if (isFolderNode) {
            toggleFolder(n.id);
          } else {
            openBookmark(n);
          }
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          const pos = getContextMenuPos(e);
          setBlankCtx(null);
          setCtx({ ...pos, node: n });
        }}
        title={n.name}
      >
        {hasChildren ? (
          <button
            type="button"
            className="twisty"
            onClick={(e) => {
              e.stopPropagation();
              toggleFolder(n.id);
            }}
          >
            <img src={opened ? ArrowDownIcon : ArrowRightIcon} alt="" />
          </button>
        ) : (
          <span className="twisty-spacer" />
        )}

        <span
          className="leaf"
          onDoubleClick={(e) => {
            e.stopPropagation();
            if (isFolderNode) toggleFolder(n.id);
            else openBookmark(n);
          }}
        >
          <img
            className={`node-icon ${!isFolderNode && usesGenericTextFileIcon(n) ? 'node-icon--text' : ''}`}
            src={
              isFolderNode
                ? opened
                  ? FolderOpenIcon
                  : FolderClosedIcon
                : getFileIcon(n)
            }
            alt=""
          />
          <span className="label">{n.name}</span>
        </span>

        {opened && hasChildren && (
          <ul className="tree-children">{n.children.map(renderNode)}</ul>
        )}
      </li>
    );
  };

  return (
    <div
      className="panel bookmarks-panel"
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
      <div
        className={`card scroll ${rootDropOver ? 'root-drop-over' : ''}`}
        style={{ overflow: 'auto', height: '100%' }}
        onDragOver={onRootDragOver}
        onDragLeave={onRootDragLeave}
        onDrop={onRootDrop}
      >
        {loadingT ? (
          <div className="loading">Loading...</div>
        ) : (
          <ul className="tree">{tree.map(renderNode)}</ul>
        )}
      </div>

      {ctx && (
        <ContextMenuPortal x={ctx.x} y={ctx.y}>
          <button
            className="ctx-item"
            onClick={() => {
              if (ctx.node?.node_type === 'FOLDER') {
                toggleFolder(ctx.node.id);
              } else {
                openBookmark(ctx.node);
              }
              setCtx(null);
            }}
          >
            Open
          </button>

          {ctx.node?.node_type === 'FOLDER' && (
            <button
              className="ctx-item"
              onClick={async () => {
                try {
                  await createFolder(ctx.node.id);
                  setOpenMap((prev) => {
                    const next = new Set(prev);
                    next.add(ctx.node.id);
                    return next;
                  });
                } catch (e) {
                  window.alert(`Folder creation failed: ${e?.message || e}`);
                } finally {
                  setCtx(null);
                }
              }}
            >
              New folder
            </button>
          )}

          <button
            className="ctx-item"
            onClick={async () => {
              try {
                await renameNode(ctx.node);
              } catch (e) {
                window.alert(`Rename failed: ${e?.message || e}`);
              } finally {
                setCtx(null);
              }
            }}
          >
            Change name
          </button>

          <button
            className="ctx-item danger"
            onClick={async () => {
              try {
                await deleteNode(ctx.node);
              } catch (e) {
                window.alert(`Delete failed: ${e?.message || e}`);
              } finally {
                setCtx(null);
              }
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
                await createFolder(null);
              } catch (e) {
                window.alert(`Folder creation failed: ${e?.message || e}`);
              } finally {
                setBlankCtx(null);
              }
            }}
          >
            New folder
          </button>
        </ContextMenuPortal>
      )}
    </div>
  );
}
