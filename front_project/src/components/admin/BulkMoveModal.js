import React, { useEffect, useMemo, useState } from 'react';

function nodeId(node) {
  const value = Number(node?.id ?? node?.node_id);
  return Number.isFinite(value) ? value : null;
}

function parentId(node) {
  const value = Number(node?.parent ?? node?.parent_id);
  return Number.isFinite(value) ? value : null;
}

function unavailable(node) {
  const status = String(node?.doc_status || node?.meta?.doc_status || '').toLowerCase();
  return Boolean(node?.disabled) || ['deleted_pending', 'inactive_path_missing'].includes(status);
}

function flattenTree(nodes, depth = 0, rows = []) {
  for (const node of nodes || []) {
    rows.push({ ...node, id: nodeId(node), parent_id: parentId(node), depth });
    flattenTree(node.children, depth + 1, rows);
  }
  return rows;
}

function visibleTreeRows(nodes, collapsedIds, query, depth = 0, rows = []) {
  const search = String(query || '').trim().toLocaleLowerCase();
  for (const node of nodes || []) {
    const normalized = { ...node, id: nodeId(node), parent_id: parentId(node), depth };
    if (!node.is_folder) {
      if (!search || String(node.name || '').toLocaleLowerCase().includes(search)) rows.push(normalized);
      continue;
    }

    if (search) {
      const matchingChildren = [];
      visibleTreeRows(node.children, collapsedIds, search, depth + 1, matchingChildren);
      if (matchingChildren.length) {
        rows.push(normalized, ...matchingChildren);
      }
      continue;
    }

    rows.push(normalized);
    if (!collapsedIds.has(normalized.id)) {
      visibleTreeRows(node.children, collapsedIds, '', depth + 1, rows);
    }
  }
  return rows;
}

function folderOptions(nodes, rootId, rootName) {
  const options = [{ id: rootId, name: rootName || 'KB root', depth: 0 }];
  const walk = (items, depth) => {
    for (const node of items || []) {
      if (!node?.is_folder) continue;
      options.push({ id: nodeId(node), name: node.name || 'Folder', depth });
      walk(node.children, depth + 1);
    }
  };
  walk(nodes, 1);
  return options.filter((item) => Number.isFinite(item.id));
}

export default function BulkMoveModal({
  open,
  nodes,
  kbId,
  kbName,
  initialSelectedIds = [],
  initialTargetId,
  onClose,
  onMove,
}) {
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [collapsedIds, setCollapsedIds] = useState(() => new Set());
  const [query, setQuery] = useState('');
  const [targetId, setTargetId] = useState(null);
  const [moving, setMoving] = useState(false);
  const [error, setError] = useState('');

  const rows = useMemo(() => flattenTree(nodes), [nodes]);
  const files = useMemo(
    () => rows.filter((node) => !node.is_folder && node.id != null && !unavailable(node)),
    [rows]
  );
  const visibleRows = useMemo(
    () => visibleTreeRows(nodes, collapsedIds, query),
    [nodes, collapsedIds, query]
  );
  const visibleFiles = useMemo(
    () => visibleRows.filter((node) => !node.is_folder && node.id != null && !unavailable(node)),
    [visibleRows]
  );
  const folders = useMemo(
    () => folderOptions(nodes, Number(kbId), kbName),
    [nodes, kbId, kbName]
  );

  useEffect(() => {
    if (!open) return;
    const validFileIds = new Set(files.map((file) => file.id));
    setSelectedIds(new Set(initialSelectedIds.map(Number).filter((id) => validFileIds.has(id))));
    setCollapsedIds(new Set());
    setQuery('');
    const requestedTarget = Number(initialTargetId);
    setTargetId(
      folders.some((folder) => folder.id === requestedTarget)
        ? requestedTarget
        : Number(kbId)
    );
    setError('');
    setMoving(false);
  }, [open, initialSelectedIds, initialTargetId, files, folders, kbId]);

  if (!open) return null;

  const targetItems = rows.filter((node) => node.parent_id === targetId);
  const selectedFiles = files.filter((file) => selectedIds.has(file.id));
  const movableFiles = selectedFiles.filter((file) => file.parent_id !== targetId);
  const allVisibleSelected =
    visibleFiles.length > 0 && visibleFiles.every((file) => selectedIds.has(file.id));

  const toggleFile = (id) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setError('');
  };

  const toggleFolder = (id) => {
    if (query) return;
    setCollapsedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllVisible = (checked) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      for (const file of visibleFiles) {
        if (checked) next.add(file.id);
        else next.delete(file.id);
      }
      return next;
    });
    setError('');
  };

  const submit = async () => {
    if (!movableFiles.length || moving) return;
    setMoving(true);
    setError('');
    try {
      await onMove?.(movableFiles, targetId);
      onClose?.();
    } catch (moveError) {
      setError(moveError?.userMessage || moveError?.message || 'Files could not be moved.');
    } finally {
      setMoving(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !moving) onClose?.();
    }}>
      <div className="modal admin-bulk-move-modal" role="dialog" aria-modal="true" aria-label="Move files">
        <div className="modal-head">
          <div>
            <div className="modal-title">Move files</div>
            <div className="admin-bulk-move-subtitle">Select multiple files and preview the destination folder.</div>
          </div>
          <button type="button" className="modal-close" onClick={onClose} disabled={moving} aria-label="Close">X</button>
        </div>

        <div className="modal-body admin-bulk-move-body">
          <section className="admin-bulk-move-pane">
            <div className="admin-bulk-move-pane-head">
              <div>
                <strong>Current file structure</strong>
                <span>{selectedIds.size} selected</span>
              </div>
              <label className="admin-bulk-select-all">
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  disabled={!visibleFiles.length || moving}
                  onChange={(event) => toggleAllVisible(event.target.checked)}
                />
                Select visible
              </label>
            </div>
            <div className="admin-bulk-search">
              <input
                className="input"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search documents"
                disabled={moving}
              />
              {query && (
                <button type="button" className="btn ghost" onClick={() => setQuery('')} disabled={moving}>
                  Clear
                </button>
              )}
            </div>
            <div className="admin-bulk-move-list" role="list">
              {visibleRows.length === 0 && (
                <div className="empty">{query ? 'No matching documents.' : 'No files.'}</div>
              )}
              {visibleRows.map((node) => {
                const isFolder = Boolean(node.is_folder);
                const checked = selectedIds.has(node.id);
                const isUnavailable = !isFolder && unavailable(node);
                const collapsed = collapsedIds.has(node.id);
                return (
                  <div
                    key={node.id}
                    className={`admin-bulk-move-row${isFolder ? ' folder' : ''}${checked ? ' selected' : ''}`}
                    style={{ paddingLeft: 12 + node.depth * 18 }}
                    title={node.name}
                  >
                    {isFolder ? (
                      <button
                        type="button"
                        className="admin-bulk-folder-toggle"
                        onClick={() => toggleFolder(node.id)}
                        disabled={Boolean(query) || moving}
                        aria-label={collapsed ? 'Expand folder' : 'Collapse folder'}
                      >
                        {query || !collapsed ? 'v' : '>'}
                      </button>
                    ) : (
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={isUnavailable || moving}
                        onChange={() => toggleFile(node.id)}
                      />
                    )}
                    <span className="admin-bulk-node-kind" aria-hidden="true">{isFolder ? 'DIR' : 'FILE'}</span>
                    <span className="admin-bulk-node-name">{node.name}</span>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="admin-bulk-move-pane">
            <div className="admin-bulk-target-field">
              <label htmlFor="admin-bulk-move-target">Destination folder</label>
              <select
                id="admin-bulk-move-target"
                className="input"
                value={targetId ?? ''}
                onChange={(event) => {
                  setTargetId(Number(event.target.value));
                  setError('');
                }}
                disabled={moving}
              >
                {folders.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {`${'-- '.repeat(folder.depth)}${folder.name}`}
                  </option>
                ))}
              </select>
            </div>
            <div className="admin-bulk-move-pane-head destination">
              <div>
                <strong>Files after move</strong>
                <span>{targetItems.length + movableFiles.length} items</span>
              </div>
            </div>
            <div className="admin-bulk-move-list preview" role="list">
              {targetItems.map((node) => (
                <div key={`current-${node.id}`} className="admin-bulk-move-row">
                  <span className="admin-bulk-node-kind" aria-hidden="true">{node.is_folder ? 'DIR' : 'FILE'}</span>
                  <span className="admin-bulk-node-name">{node.name}</span>
                  <span className="admin-bulk-state current">Current</span>
                </div>
              ))}
              {movableFiles.map((node) => (
                <div key={`moving-${node.id}`} className="admin-bulk-move-row moving">
                  <span className="admin-bulk-node-kind" aria-hidden="true">FILE</span>
                  <span className="admin-bulk-node-name">{node.name}</span>
                  <span className="admin-bulk-state incoming">Will move</span>
                </div>
              ))}
              {targetItems.length === 0 && movableFiles.length === 0 && (
                <div className="empty">The destination folder is empty.</div>
              )}
            </div>
          </section>
        </div>

        {error && <div className="admin-bulk-move-error" role="alert">{error}</div>}
        <div className="modal-foot">
          <span className="admin-bulk-move-summary">
            {movableFiles.length ? `${movableFiles.length} file(s) will be moved.` : 'Select files outside the destination folder.'}
          </span>
          <button type="button" className="btn ghost" onClick={onClose} disabled={moving}>Cancel</button>
          <button type="button" className="btn primary" onClick={submit} disabled={!movableFiles.length || moving}>
            {moving ? 'Moving...' : `Move ${movableFiles.length || ''}`.trim()}
          </button>
        </div>
      </div>
    </div>
  );
}
