import React, { useEffect, useMemo, useState } from 'react';

function idOf(node) {
  const id = Number(node?.id ?? node?.node_id);
  return Number.isFinite(id) ? id : null;
}

function findNode(nodes, targetId) {
  for (const node of nodes || []) {
    if (idOf(node) === Number(targetId)) return node;
    const found = findNode(node.children, targetId);
    if (found) return found;
  }
  return null;
}

function flattenChildren(nodes, depth = 0, output = []) {
  for (const node of nodes || []) {
    output.push({ ...node, id: idOf(node), depth });
    flattenChildren(node.children, depth + 1, output);
  }
  return output;
}

function statusOf(node) {
  return String(node?.doc_status || node?.meta?.doc_status || '').toLowerCase();
}

function isPending(node) {
  return !node?.is_folder && ['deleted_pending', 'inactive_path_missing'].includes(statusOf(node));
}

function selectedRoots(rows, selectedIds) {
  const byId = new Map(rows.map((row) => [row.id, row]));
  return rows.filter((row) => {
    if (!selectedIds.has(row.id)) return false;
    let parent = Number(row.parent ?? row.parent_id);
    const guard = new Set();
    while (Number.isFinite(parent) && !guard.has(parent)) {
      if (selectedIds.has(parent)) return false;
      guard.add(parent);
      const parentNode = byId.get(parent);
      if (!parentNode) break;
      parent = Number(parentNode.parent ?? parentNode.parent_id);
    }
    return true;
  });
}

export default function FolderCleanupModal({
  open,
  folderId,
  nodes,
  onClose,
  onDeleteItems,
  onDeleteFolder,
  onRefresh,
}) {
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const folder = useMemo(() => findNode(nodes, folderId), [nodes, folderId]);
  const rows = useMemo(() => flattenChildren(folder?.children), [folder]);
  const selectableRows = useMemo(() => rows.filter((row) => !isPending(row)), [rows]);
  const pendingCount = rows.filter(isPending).length;
  const isEmpty = rows.length === 0;
  const allSelected = selectableRows.length > 0 && selectableRows.every((row) => selectedIds.has(row.id));

  useEffect(() => {
    if (!open) return;
    setSelectedIds(new Set());
    setMessage('');
    setError('');
    setWorking(false);
  }, [open, folderId]);

  useEffect(() => {
    if (!open) return;
    const validIds = new Set(selectableRows.map((row) => row.id));
    setSelectedIds((previous) => new Set(Array.from(previous).filter((id) => validIds.has(id))));
  }, [open, selectableRows]);

  if (!open) return null;

  const toggle = (id) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setMessage('');
    setError('');
  };

  const runCleanup = async (candidates, all = false) => {
    const roots = selectedRoots(rows, new Set(candidates.map((row) => row.id)));
    if (!roots.length || working) return;
    setWorking(true);
    setMessage('');
    setError('');
    try {
      await onDeleteItems?.(roots);
      setSelectedIds(new Set());
      setMessage(
        all
          ? 'Cleanup was requested for every item. Documents remain visible until permanent purge finishes.'
          : 'Cleanup was requested for the selected items.'
      );
    } catch (cleanupError) {
      setError(cleanupError?.userMessage || cleanupError?.message || 'Cleanup failed.');
    } finally {
      setWorking(false);
    }
  };

  const deleteFolder = async () => {
    if (!isEmpty || working) return;
    setWorking(true);
    setMessage('');
    setError('');
    try {
      await onDeleteFolder?.(folderId);
      onClose?.();
    } catch (deleteError) {
      setError(deleteError?.userMessage || deleteError?.message || 'Folder deletion failed.');
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !working) onClose?.();
    }}>
      <div className="modal folder-cleanup-modal" role="dialog" aria-modal="true" aria-label="Delete folder">
        <div className="modal-head">
          <div>
            <div className="modal-title">Delete folder</div>
            <div className="admin-bulk-move-subtitle">{folder?.name || 'Folder'}</div>
          </div>
          <button type="button" className="modal-close" onClick={onClose} disabled={working} aria-label="Close">X</button>
        </div>

        <div className="modal-body folder-cleanup-body">
          <div className={`folder-cleanup-notice${isEmpty ? ' ready' : ''}`}>
            {isEmpty
              ? 'This folder is empty and can now be deleted.'
              : 'Delete or purge every child file and folder before deleting this folder.'}
            {pendingCount > 0 && (
              <span>{pendingCount} document(s) are waiting for permanent purge.</span>
            )}
          </div>

          <div className="folder-cleanup-toolbar">
            <label>
              <input
                type="checkbox"
                checked={allSelected}
                disabled={!selectableRows.length || working}
                onChange={(event) => setSelectedIds(
                  event.target.checked ? new Set(selectableRows.map((row) => row.id)) : new Set()
                )}
              />
              Select all
            </label>
            <span>{selectedIds.size} selected / {rows.length} items</span>
          </div>

          <div className="folder-cleanup-list" role="list">
            {isEmpty && <div className="empty">No child files or folders.</div>}
            {rows.map((row) => {
              const pending = isPending(row);
              return (
                <label
                  key={row.id}
                  className={`folder-cleanup-row${selectedIds.has(row.id) ? ' selected' : ''}${pending ? ' pending' : ''}`}
                  style={{ paddingLeft: 12 + row.depth * 18 }}
                  title={row.name}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(row.id)}
                    disabled={pending || working}
                    onChange={() => toggle(row.id)}
                  />
                  <span className="folder-cleanup-kind" aria-hidden="true">
                    {row.is_folder ? 'DIR' : 'FILE'}
                  </span>
                  <span className="folder-cleanup-name">{row.name}</span>
                  <span className={`folder-cleanup-type${pending ? ' pending' : ''}`}>
                    {pending ? 'Pending purge' : row.is_folder ? 'Folder' : 'File'}
                  </span>
                </label>
              );
            })}
          </div>
        </div>

        {message && <div className="folder-cleanup-message" role="status">{message}</div>}
        {error && <div className="admin-bulk-move-error" role="alert">{error}</div>}

        <div className="modal-foot folder-cleanup-foot">
          <button
            type="button"
            className="btn ghost"
            disabled={working}
            onClick={async () => {
              setWorking(true);
              setError('');
              try {
                await onRefresh?.();
              } catch (refreshError) {
                setError(refreshError?.userMessage || refreshError?.message || 'Refresh failed.');
              } finally {
                setWorking(false);
              }
            }}
          >
            Refresh
          </button>
          <span className="admin-bulk-move-summary">
            {isEmpty ? 'Ready to delete.' : 'The folder will remain until it is empty.'}
          </span>
          <button
            type="button"
            className="btn danger"
            onClick={() => runCleanup(selectableRows.filter((row) => selectedIds.has(row.id)))}
            disabled={!selectedIds.size || working}
          >
            Delete selected
          </button>
          <button
            type="button"
            className="btn danger"
            onClick={() => runCleanup(selectableRows, true)}
            disabled={!selectableRows.length || working}
          >
            Delete all contents
          </button>
          <button type="button" className="btn danger" onClick={deleteFolder} disabled={!isEmpty || working}>
            {working ? 'Working...' : 'Delete folder'}
          </button>
        </div>
      </div>
    </div>
  );
}
