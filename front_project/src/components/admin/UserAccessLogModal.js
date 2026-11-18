import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { http } from '../../services/http/client';

const modalRoot =
  document.getElementById('modal-root') ||
  (() => {
    const el = document.createElement('div');
    el.id = 'modal-root';
    document.body.appendChild(el);
    return el;
  })();

export default function UserAccessLogModal({ open, onClose, user }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !user) return;
    (async () => {
      setLoading(true);
      try {
        const data = await http.get('/admin/access-logs', { user_id: user.id, limit: 200 });
        setLogs(Array.isArray(data) ? data : (data?.items || []));
      } catch (e) {
        console.error('Failed to laod Connection log', e);
        setLogs([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [open, user]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onClose?.();
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  const node = (
    <div
      className="ualm-overlay"
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div className="ualm-card">
        <div className="ualm-head">
          <div className="ualm-title">User Access Log</div>
          <button className="btn ghost" onClick={onClose} aria-label="Cancel">Close</button>
        </div>
        <div className="ualm-body">
          {loading ? (
            <div className="loading">Loading...</div>
          ) : !logs.length ? (
            <div className="empty">No log result.</div>
          ) : (
            <table className="table w100">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>IP</th>
                  <th>Action</th>
                  <th>Root</th>
                  <th>Environment</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l) => (
                  <tr key={l.access_id || `${l.user_id}-${l.created_at}-${l.ip_addr}`}>
                    <td>{String(l.created_at || '').replace('T',' ').slice(0,19)}</td>
                    <td>{l.ip_addr}</td>
                    <td>{l.action}</td>
                    <td>{l.path || l.detail || ''}</td>
                    <td className="nowrap">{l.user_agent}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(node, modalRoot);
}
