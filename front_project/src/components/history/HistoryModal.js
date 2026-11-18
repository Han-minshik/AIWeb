import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { useHistory } from '../../contexts/HistoryContext';
import '../../styles/components/history-modal.css';
import closeIcon from '/src/assets/icons/x.svg';

const KST_TIME_ZONE = 'Asia/Seoul';

function parseUtcDate(value) {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;

  const raw = String(value).trim();
  if (!raw) return null;

  const normalized = raw.replace(' ', 'T');
  // Do not force UTC for timezone-less strings.
  // Backend can return DB DATETIME values without timezone (already local time),
  // and appending "Z" would shift them again (e.g. KST -> +9h).
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return null;
  return date;
}

function formatDate(s) {
  if (!s) return '-';
  const d = parseUtcDate(s);
  if (!d) return s;

  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: KST_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    hourCycle: 'h23',
  }).formatToParts(d);

  const map = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return `${map.year}-${map.month}-${map.day} ${map.hour}:${map.minute}`;
}

function agentLabel(appType, agentId, appId) {
  const normType = typeof appType === 'string' ? appType.trim() : '';
  const upper = normType ? normType.toUpperCase() : null;
  const lower = normType ? normType.toLowerCase() : '';
  if (normType === 'KB Question & Answer') return 'KB Question & Answer';
  if (lower === 'kb question & answer') return 'KB Question & Answer';
  if (lower === 'general qa') return 'General QA';
  if (upper === 'RAG') return 'KB Question & Answer';
  if (normType) return normType;
  return 'KB Question & Answer';
}

const TrashIcon = (props) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="16"
    height="16"
    fill="currentColor"
    viewBox="0 0 16 16"
    aria-hidden="true"
    {...props}
  >
    <path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0z" />
    <path d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4zM2.5 3h11V2h-11z" />
  </svg>
);

export default function HistoryModal() {
  const { isOpen, close, sessions, loading } = useHistory();
  const [query, setQuery] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [target, setTarget] = useState(null);

  const tableCols = useMemo(
    () => [
      { key: 'title', label: 'Title', width: 260, align: 'left' },
      { key: 'date', label: 'Date', width: 160, align: 'left' },
      { key: 'kb', label: 'KB', width: 160, align: 'left' },
      { key: 'agent', label: 'Agent', width: 180, align: 'left' },
      { key: 'actions', label: '', width: 70, align: 'right' },
    ],
    [],
  );

  const emitSearch = useCallback((q) => {
    const text = (q || '').trim();
    if (text.length === 0) {
      window.dispatchEvent(new CustomEvent('qa:history:search-reset'));
      return;
    }
    window.dispatchEvent(
      new CustomEvent('qa:history:search', { detail: { q: text } }),
    );
  }, []);

  const onKeyDown = useCallback(
    (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        emitSearch(query);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        setQuery('');
        window.dispatchEvent(new CustomEvent('qa:history:search-reset'));
      }
    },
    [emitSearch, query],
  );

  const handleRowClick = useCallback(
    (s) => {
      const rawAppId = s.app_id ?? s.appId;
      const n = rawAppId == null ? null : Number(rawAppId);
      const appId = Number.isFinite(n) ? n : null;
      const appType =
        typeof s.app_type === 'string'
          ? s.app_type
          : typeof s.appType === 'string'
          ? s.appType
          : null;
      const agentId = s.agent_id || s.agentId || null;

      window.dispatchEvent(
        new CustomEvent('qa:history:select', {
          detail: { sessionId: s.id ?? s.session_id, appType, appId, agentId },
        }),
      );
      close();
    },
    [close],
  );

  const openConfirm = useCallback((s) => {
    setTarget(s);
    setConfirmOpen(true);
  }, []);

  const cancelConfirm = useCallback(() => {
    setConfirmOpen(false);
    setTarget(null);
  }, []);

  const confirmDelete = useCallback(() => {
    if (!target) return;
    window.dispatchEvent(
      new CustomEvent('qa:history:delete', {
        detail: { sessionId: target.id ?? target.session_id },
      }),
    );
    setConfirmOpen(false);
    setTarget(null);
  }, [target]);

  useEffect(() => {
    if (!isOpen) return;
    const body = document.body;
    const count = Number(body.dataset.modalOpenCount || '0') + 1;
    body.dataset.modalOpenCount = String(count);
    body.classList.add('modal-dim');
    return () => {
      const next = Number(body.dataset.modalOpenCount || '1') - 1;
      if (next <= 0) {
        delete body.dataset.modalOpenCount;
        body.classList.remove('modal-dim');
      } else {
        body.dataset.modalOpenCount = String(next);
      }
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <>
      <div className="hm-backdrop" onClick={close}>
        <div className="hm-container" onClick={(e) => e.stopPropagation()}>
          <div className="hm-header">
            <div className="hm-header-left">
              <h2>Chat History</h2>
            </div>

            <div className="hm-header-center">
              <input
                className="hm-search"
                type="text"
                placeholder="Session/Message Search… (Enter)"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onKeyDown}
                onClick={(e) => e.stopPropagation()}
              />
            </div>

            <div className="hm-header-right">
              <button className="hm-close" onClick={close} aria-label="Close">
                <img src={closeIcon} alt="" aria-hidden="true" />
              </button>
            </div>
          </div>

          <div className="hm-content">
            <div className="hm-table-wrap">
              <table className="hm-table">
                <colgroup>
                  {tableCols.map((c) => (
                    <col key={c.key} style={{ width: `${c.width}px` }} />
                  ))}
                </colgroup>

                <thead>
                  <tr>
                    {tableCols.map((c) => (
                      <th
                        key={c.key}
                        className={c.key === 'actions' ? 'hm-th-actions' : ''}
                        style={{ textAlign: c.align }}
                      >
                        {c.label}
                      </th>
                    ))}
                  </tr>
                </thead>

                <tbody>
                  {loading && (
                    <tr>
                      <td colSpan={tableCols.length} className="hm-empty">
                        Loading…
                      </td>
                    </tr>
                  )}

                  {!loading && (!sessions || sessions.length === 0) && (
                    <tr>
                      <td colSpan={tableCols.length} className="hm-empty">
                        No result.
                      </td>
                    </tr>
                  )}

                  {!loading &&
                    (sessions || []).map((s, i) => {
                      const rowKey =
                        s.id ??
                        s.session_id ??
                        s.updated_at ??
                        s.created_at ??
                        i;

                      const title = s.title || '(No title)';
                      const kbName = (s.kb_name ?? s.kbName ?? '-').toString() || '-';

                      return (
                        <tr
                          key={rowKey}
                          onClick={() => handleRowClick(s)}
                          className="clickable hm-row"
                        >
                          <td className="hm-ellipsis" title={title}>
                            {title}
                          </td>

                          <td className="hm-date">
                            {formatDate(
                              s.last_activity_at || s.updated_at || s.created_at,
                            )}
                          </td>

                          <td
                            className="hm-ellipsis"
                            title={kbName}
                          >
                            {kbName}
                          </td>

                          <td className="hm-agent">
                            {agentLabel(
                              s.app_type ?? s.appType ?? null,
                              s.agent_id ?? s.agentId ?? null,
                              s.app_id ?? s.appId ?? null,
                            )}
                          </td>

                          <td className="hm-actions">
                            <button
                              className="hm-icon-btn hm-trash-btn"
                              onClick={(e) => {
                                e.stopPropagation();
                                openConfirm(s);
                              }}
                              aria-label="Delete session"
                              title="Delete"
                            >
                              <TrashIcon className="hm-trash-icon" />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="hm-footer">
            <button className="hm-btn" onClick={close}>
              Close
            </button>
          </div>
        </div>
      </div>

      {confirmOpen && target && (
        <div className="hm-confirm-backdrop" onClick={cancelConfirm}>
          <div className="hm-confirm" onClick={(e) => e.stopPropagation()}>
            <div className="hm-confirm-head">Do you want to delete this session?</div>

            <div className="hm-confirm-body">
              <div className="hm-confirm-row">
                <b>Title:</b> {target.title || '(No title)'}
              </div>
              <div className="hm-confirm-row">
                <b>Date:</b>{' '}
                {formatDate(
                  target.last_activity_at || target.updated_at || target.created_at,
                )}
              </div>
            </div>

            <div className="hm-confirm-foot">
              <button className="hm-btn" onClick={cancelConfirm}>
                Cancel
              </button>
              <button className="hm-btn-danger" onClick={confirmDelete}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
