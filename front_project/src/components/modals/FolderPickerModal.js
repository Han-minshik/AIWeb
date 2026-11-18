import React, { useEffect, useState } from 'react';
import { http } from '../../services/http/client';
import '../../styles/components/modal.css';

const EMPTY_EXCLUDE_IDS = Object.freeze([]);

export default function FolderPickerModal({
  open,
  onClose,
  onPick,
  activeRootId = null,
  title = 'Select folder to move',
  rootLabel = 'Root',
  confirmLabel = 'Move',
  excludeIds = EMPTY_EXCLUDE_IDS,
  initialSelected = null,
}) {
  const [nodes, setNodes] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSelected(initialSelected);

    (async () => {
      if (activeRootId == null) {
        setNodes([]);
        return;
      }

      setLoading(true);
      try {
        const res = await http.get(`/kb/trees/${Number(activeRootId)}/tree`, {
          include_empty_folders: true,
        });
        const items = res?.nodes || res?.items || [];

        const flat = [];
        const walk = (arr, depth = 0, parentPath = '') => {
          for (const n of arr) {
            const name = n?.name ?? n?.title ?? 'Folder';
            const path = parentPath ? `${parentPath} / ${name}` : String(name);
            const isFolder =
              n?.is_folder === true ||
              String(n?.node_type || '').toLowerCase().includes('folder') ||
              !!(n?.children && n.children.length);
            flat.push({ id: n?.id ?? n?.node_id, name, path, depth, isFolder });
            if (isFolder) walk(n.children || [], depth + 1, path);
          }
        };

        walk(items, 0);
        const excluded = new Set((excludeIds || []).map((id) => String(id)));
        setNodes(flat.filter((x) => x.isFolder && !excluded.has(String(x.id))));
      } catch {
        setNodes([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [open, activeRootId, excludeIds, initialSelected]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="modal-head">
          <div className="modal-title">{title}</div>
        </div>

        <div className="modal-body" style={{ maxHeight: 360, overflow: 'auto' }}>
          {loading ? (
            <div className="loading">Loading...</div>
          ) : (
            <ul className="list">
              <li
                className={`list-item ${selected == null ? 'selected' : ''}`}
                onClick={() => setSelected(null)}
                title={rootLabel}
              >
                <i className="bi bi-folder" /> <strong>{rootLabel}</strong>
              </li>

              {nodes.map((n) => (
                <li
                  key={n.id}
                  className={`list-item ${String(selected) === String(n.id) ? 'selected' : ''}`}
                  onClick={() => setSelected(n.id)}
                  title={n.path}
                >
                  <span style={{ paddingLeft: n.depth * 16 }} />
                  <i className="bi bi-folder2" /> {n.name}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="modal-foot" style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn" onClick={() => onPick?.(selected ?? null)}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
