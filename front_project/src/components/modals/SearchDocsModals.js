import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { listFilesByTree, listBookmarkedDocs } from '../../services/docs/docService';
import { dispatchOpenDocTab } from '../../utils/openDocTab';
import searchIcon from '/src/assets/icons/search.svg';
import xIcon from '/src/assets/icons/x.svg';
import xCircleIcon from '/src/assets/icons/x-circle.svg';
import '../../styles/components/search-modal.css';

export default function SearchDocsModal({
  open = false,
  onClose = () => {},
  title = 'KB Search',
  onPick = null,
  kbId: kbIdProp = null,
  mode = 'kb',
}) {
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(-1);
  const [kbIdFromEvent, setKbIdFromEvent] = useState(null);

  useEffect(() => {
    const handler = (e) => {
      const id = e?.detail?.kbId ?? e?.detail?.id ?? null;
      if (id != null) setKbIdFromEvent(id);
    };
    window.addEventListener('kb:vault-select', handler);
    return () => window.removeEventListener('kb:vault-select', handler);
  }, []);

  const effectiveKbId = useMemo(() => {
    if (mode !== 'kb') return null;
    if (kbIdProp != null && kbIdProp !== '') return kbIdProp;
    if (kbIdFromEvent != null && kbIdFromEvent !== '') return kbIdFromEvent;
    return null;
  }, [kbIdProp, kbIdFromEvent, mode]);

  useEffect(() => {
    if (!open) return;
    const body = document.body;
    const count = Number(body.dataset.modalOpenCount || '0') + 1;
    body.dataset.modalOpenCount = String(count);
    body.classList.add('modal-dim');
    setQ('');
    setItems([]);
    setSelected(-1);
    setError('');
    setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      const next = Number(body.dataset.modalOpenCount || '1') - 1;
      if (next <= 0) {
        delete body.dataset.modalOpenCount;
        body.classList.remove('modal-dim');
      } else {
        body.dataset.modalOpenCount = String(next);
      }
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handle = setTimeout(async () => {
      if (!q?.trim()) {
        setItems([]);
        setError('');
        return;
      }

      if (mode === 'kb') {
        if (effectiveKbId == null) {
          setItems([]);
          setError('kB is not selected.');
          return;
        }
        setLoading(true);
        setError('');
        try {
          const rows = await listFilesByTree(effectiveKbId, null, q);
          const norm = rows.map((r) => ({
            id: r.doc_id ?? r.id ?? r.object_id,
            doc_id: r.doc_id ?? r.id ?? null,
            object_id: r.object_id ?? r.storage_object_id ?? null,
            node_id: r.node_id ?? r.nodeId ?? null,
            name: r.name ?? r.title ?? `Doc ${r.id ?? ''}`,
            doc_title: r.name ?? r.title ?? `Doc ${r.id ?? ''}`,
            doc_rel_path: r.storage_object_relative_path ?? r.relative_path ?? null,
            doc_sub_path: r.storage_object_sub_path ?? r.sub_path ?? null,
            doc_file_right: r.storage_object_file_right ?? r.file_right ?? null,
            doc_branch_type: r.branch_type ?? null,
            storage_scope: r.storage_scope ?? null,
            upload_mode: r.upload_mode ?? null,
            kb_type: r.kb_type ?? null,
            created_at: r.created_at ?? r.createdAt ?? r.created ?? null,
            updated_at: r.updated_at ?? r.updatedAt ?? r.updated ?? null,
          }));
          setItems(norm);
          setSelected(norm.length ? 0 : -1);
        } catch (e) {
          setError(e?.message || 'Error');
          setItems([]);
          setSelected(-1);
        } finally {
          setLoading(false);
        }
        return;
      }

      if (mode === 'bookmark') {
        setLoading(true);
        setError('');
        try {
          const rows = await listBookmarkedDocs(q);
          const norm = rows.map((r) => ({
            id: r.doc_id ?? r.id ?? r.object_id,
            doc_id: r.doc_id ?? r.id ?? null,
            object_id: r.object_id ?? r.storage_object_id ?? null,
            node_id: r.node_id ?? r.nodeId ?? null,
            name: r.name ?? r.title ?? `Doc ${r.id ?? ''}`,
            doc_title: r.name ?? r.title ?? `Doc ${r.id ?? ''}`,
            doc_rel_path: r.storage_object_relative_path ?? r.relative_path ?? null,
            doc_sub_path: r.storage_object_sub_path ?? r.sub_path ?? null,
            doc_file_right: r.storage_object_file_right ?? r.file_right ?? null,
            doc_branch_type: r.branch_type ?? null,
            storage_scope: r.storage_scope ?? null,
            upload_mode: r.upload_mode ?? null,
            kb_type: r.kb_type ?? null,
            created_at: r.created_at ?? r.createdAt ?? r.created ?? null,
            updated_at: r.updated_at ?? r.updatedAt ?? r.updated ?? null,
          }));
          setItems(norm);
          setSelected(norm.length ? 0 : -1);
        } catch (e) {
          setError(e?.message || 'Error');
          setItems([]);
          setSelected(-1);
        } finally {
          setLoading(false);
        }
      }
    }, 220);
    return () => clearTimeout(handle);
  }, [q, open, effectiveKbId, mode]);

  const formatTs = (v) => {
    if (!v) return '';
    try {
      const d = new Date(v);
      if (Number.isNaN(d.getTime())) return String(v);
      const pad = (n) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return String(v);
    }
  };

  const openRow = (row) => {
    if (!row) return;
    const docId = row.doc_id ?? row.id ?? null;
    const objectId = row.object_id ?? null;
    if (docId != null || objectId != null) {
      const safeTitle = row.name || `Doc ${docId ?? objectId}`;
      try {
        dispatchOpenDocTab({
          docId,
          objectId,
          title: safeTitle,
          docTitle: row.doc_title ?? safeTitle,
          docRelPath: row.doc_rel_path ?? null,
          docSubPath: row.doc_sub_path ?? null,
          docFileRight: row.doc_file_right ?? null,
          docBranchType: row.doc_branch_type ?? null,
        });
      } catch {}
    }
    onPick?.(row);
    onClose?.();
  };

  const scrollIntoView = (idx) => {
    const listEl = listRef.current;
    if (!listEl) return;
    const rowEl = listEl.querySelector(`[data-row-index="${idx}"]`);
    if (!rowEl) return;
    rowEl.scrollIntoView({ block: 'nearest' });
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose?.();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (selected >= 0 && selected < items.length) openRow(items[selected]);
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelected((i) => {
        const next = Math.min((i < 0 ? 0 : i + 1), items.length - 1);
        scrollIntoView(next);
        return next;
      });
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelected((i) => {
        const next = Math.max((i < 0 ? 0 : i - 1), 0);
        scrollIntoView(next);
        return next;
      });
      return;
    }
  };

  if (!open) return null;

  const modal = (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className="modal-card search-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="search-modal-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="search-modal-title">{title}</h3>
          <button className="icon-btn close-btn" aria-label="Close" onClick={onClose}>
            <img src={xIcon} alt="" />
          </button>
        </div>

        <div className="modal-body">
          <div className="search-row">
            <img src={searchIcon} alt="" />
            <input
              ref={inputRef}
              type="text"
              className="search-input"
              placeholder={
                mode === 'kb'
                  ? effectiveKbId == null
                    ? 'Select KB and search'
                    : 'Search by document name'
                  : 'Search in bookmarks'
              }
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={mode === 'kb' && effectiveKbId == null}
            />
            {q && (
              <button
                className="icon-btn clear-btn"
                title="Clear"
                aria-label="Clear search term"
                onClick={() => {
                  setQ('');
                  inputRef.current?.focus();
                }}
              >
                <img src={xCircleIcon} alt="" />
              </button>
            )}
          </div>

          <div className="results-wrap">
            {loading && <div className="loading">Loading...</div>}
            {!!error && !loading && <div className="error">{error}</div>}

            {!loading && !error && (
              <div className="results-table" ref={listRef} tabIndex={-1}>
                <div className="table-head">
                  <div className="th col-name">Name</div>
                  <div className="th col-scope">Scope</div>
                  <div className="th col-created">Created at</div>
                  <div className="th col-updated">Updated at</div>
                </div>
                {items.length === 0 ? (
                  <div className="empty">No result</div>
                ) : (
                  <div className="table-body">
                    {items.map((row, idx) => (
                      <button
                        key={row.node_id ?? row.id ?? `${row.name}-${idx}`}
                        type="button"
                        className={`tr ${idx === selected ? 'selected' : ''}`}
                        data-row-index={idx}
                        onClick={() => setSelected(idx)}
                        onDoubleClick={() => openRow(row)}
                        onMouseEnter={() => setSelected(idx)}
                        title={row.name}
                      >
                        <div className="td col-name">
                          <span className="name-ellipsis">{row.name}</span>
                        </div>
                        <div className="td col-scope">{row.storage_scope || '-'}</div>
                        <div className="td col-created">{formatTs(row.created_at)}</div>
                        <div className="td col-updated">{formatTs(row.updated_at)}</div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="modal-footer">
          <div className="hint">
            <kbd>Enter:</kbd> Open · <kbd>Up</kbd>/<kbd>Down</kbd> Move · <kbd>Esc:</kbd> Close
          </div>
          <div className="spacer" />
          <button className="openbtn" disabled={selected < 0 || selected >= items.length} onClick={() => openRow(items[selected])}>
            Open
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
