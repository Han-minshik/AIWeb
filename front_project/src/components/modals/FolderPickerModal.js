import React, { useEffect, useState } from 'react';
import { http } from '../../services/http/client';
import '../../styles/components/modal.css';

export default function FolderPickerModal({
  open,
  onClose,
  onPick,
  activeRootId = null,
  title = 'Select folder to move',
}) {
  const [nodes, setNodes] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;

    (async () => {
      if (activeRootId == null) {
        setNodes([]);
        return;
      }

      setLoading(true);
      try {
        const res = await http.get(`/kb/trees/${Number(activeRootId)}/tree`);
        const items = res?.nodes || res?.items || [];

        const flat = [];
        const walk = (arr, depth = 0) => {
          for (const n of arr) {
            const isFolder = (n?.is_folder === true) || !!(n?.children && n.children.length);
            flat.push({ id: n?.id ?? n?.node_id, name: n?.name ?? n?.title, depth, isFolder });
            if (isFolder) walk(n.children || [], depth + 1);
          }
        };

        walk(items, 0);
        setNodes(flat.filter((x) => x.isFolder));
      } catch {
        setNodes([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [open, activeRootId]);

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
          ) : !nodes.length ? (
            <div className="empty">No folder.</div>
          ) : (
            <ul className="list">
              <li
                className={`list-item ${selected == null ? 'selected' : ''}`}
                onClick={() => setSelected(null)}
                title="Root"
              >
                <i className="bi bi-folder" /> <strong>Root</strong>
              </li>

              {nodes.map((n) => (
                <li
                  key={n.id}
                  className={`list-item ${selected === n.id ? 'selected' : ''}`}
                  onClick={() => setSelected(n.id)}
                  title={n.name}
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
          <button className="btn" onClick={() => onPick?.(selected ?? null)}>Move</button>
        </div>
      </div>
    </div>
  );
}
