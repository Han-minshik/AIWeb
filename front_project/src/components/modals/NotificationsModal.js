import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { listNotifications, markRead } from '../../services/notifications/notificationService';
import '../../styles/components/notifications-modal.css';

export default function NotificationsModal({ open, onClose }) {
  const [notis, setNotis] = useState([]);
  const [loading, setLoading] = useState(false);
  const userId = useMemo(
    () => parseInt(localStorage.getItem('x_user_id') || '1', 10),
    []
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listNotifications({ user_id: userId });
      setNotis(Array.isArray(rows) ? rows : []);
    } catch (e) {
      console.error(e);
      setNotis([]);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const handleMarkAllRead = async () => {
    try {
      const ids = (notis || [])
        .filter((n) => !n.read_at && n.id != null)
        .map((n) => n.id);
      if (ids.length > 0) {
        await markRead(ids);
        const now = new Date().toISOString();
        setNotis((prev) =>
          prev.map((n) => (ids.includes(n.id) ? { ...n, read_at: now } : n))
        );
      }
    } catch (e) {
      console.error(e);
    }
  };

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-head">
          <div className="modal-title">Notifications</div>
          <button className="modal-close" onClick={onClose} aria-label="close">
            ×
          </button>
        </div>

        <div className="modal-body">
          {loading ? (
            <div className="loading">Loading...</div>
          ) : !notis.length ? (
            <div className="empty">Empty</div>
          ) : (
            <ul className="list">
              {notis.map((n) => {
                const unread = !n.read_at;
                return (
                  <li
                    key={n.id}
                    className={`list-item${unread ? ' unread' : ''}`}
                  >
                    <div className="row">
                      <span className={`badge ${n.severity || 'info'}`}>
                        {n.kind || 'Notification'}
                      </span>
                      <strong className="title">
                        {n.title || '(No title)'}
                      </strong>
                      <time className="muted">
                        {n.created_at?.replace('T', ' ').slice(0, 19)}
                      </time>
                    </div>
                    {unread && <span className="dot" aria-hidden="true" />}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-ghost" onClick={handleMarkAllRead}>
            Read all
          </button>
          <button className="btn-ghost" onClick={load}>
            Refresh
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
