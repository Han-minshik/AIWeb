import React, { useEffect, useMemo, useRef, useState } from 'react';
import { listDocRevisions, reorderDocRevisions, uploadVersion } from '../../../services/docs/docService';
import '../../../styles/admin/kbVersionModal.css';

function toId(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function formatDate(v) {
  if (!v) return '-';
  const d = new Date(v);
  return d.toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function VersionManagerModal({ open, docId, docTitle, onClose }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [dragId, setDragId] = useState(null);
  const fileRef = useRef(null);

  useEffect(() => {
    if (!open || !docId) return;
    setLoading(true);
    listDocRevisions(docId)
      .then((res) => {
        const rows = Array.isArray(res?.revisions) ? res.revisions : [];
        setItems(rows);
      })
      .finally(() => setLoading(false));
  }, [open, docId]);

  const ordered = useMemo(
    () => [...items].sort((a, b) => Number(b.rev_no || 0) - Number(a.rev_no || 0)),
    [items]
  );

  const onDragStart = (id) => (e) => {
    setDragId(id);
    e.dataTransfer.effectAllowed = 'move';
  };

  const onDragOver = (id) => (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const onDrop = (id) => async (e) => {
    e.preventDefault();
    if (!dragId || dragId === id) return;
    const next = [...ordered];
    const fromIdx = next.findIndex((r) => r.revision_id === dragId);
    const toIdx = next.findIndex((r) => r.revision_id === id);
    if (fromIdx < 0 || toIdx < 0) return;
    const [moved] = next.splice(fromIdx, 1);
    next.splice(toIdx, 0, moved);
    const local = next.map((r, idx) => ({ ...r, rev_no: next.length - idx }));
    setItems(local);
    setDragId(null);
    try {
      const ids = next.map((r) => r.revision_id).filter((v) => v != null);
      await reorderDocRevisions(docId, ids);
      const fresh = await listDocRevisions(docId);
      setItems(Array.isArray(fresh?.revisions) ? fresh.revisions : []);
    } catch {
      // keep local order if server fails
    }
  };

  const onUploadClick = () => {
    if (fileRef.current) fileRef.current.click();
  };

  const onUpload = async (e) => {
    const f = e.target.files?.[0];
    if (!f || !docId) return;
    setLoading(true);
    try {
      await uploadVersion(docId, f);
      const fresh = await listDocRevisions(docId);
      setItems(Array.isArray(fresh?.revisions) ? fresh.revisions : []);
    } finally {
      setLoading(false);
      e.target.value = '';
    }
  };

  if (!open) return null;

  return (
    <div className="kb-ver-modal-backdrop" onMouseDown={onClose}>
      <div
        className="kb-ver-modal"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="kb-ver-header">
          <div className="kb-ver-title">Version Management</div>
          <div className="kb-ver-sub">{docTitle || `Doc ${docId}`}</div>
          <button className="kb-ver-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="kb-ver-actions">
          <input ref={fileRef} type="file" style={{ display: 'none' }} onChange={onUpload} />
          <button type="button" className="kb-ver-btn" onClick={onUploadClick}>
            ??버전 ?�로??          </button>
          {loading && <span className="kb-ver-loading">Loading...</span>}
        </div>

        <div className="kb-ver-list">
          {ordered.length === 0 ? (
            <div className="kb-ver-empty">버전???�습?�다.</div>
          ) : (
            ordered.map((r) => (
              <div
                key={r.revision_id}
                className={`kb-ver-row ${dragId === r.revision_id ? 'dragging' : ''}`}
                draggable
                onDragStart={onDragStart(r.revision_id)}
                onDragOver={onDragOver(r.revision_id)}
                onDrop={onDrop(r.revision_id)}
              >
                <div className="kb-ver-rev">v{r.rev_no}</div>
                <div className="kb-ver-name">{r.file_name || `rev ${r.rev_no}`}</div>
                <div className="kb-ver-date">{formatDate(r.created_at)}</div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
