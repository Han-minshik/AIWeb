import React, { useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  deleteAllNotifications,
  listNotifications,
  markAllRead,
  markRead,
} from '../../services/notifications/notificationService';
import NotificationDetailModal from './NotificationDetailModal';
import '../../styles/components/notifications-modal.css';

const PAGE_SIZE = 10;

function formatKst(raw) {
  if (!raw) return '-';
  const text = String(raw).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)) return `${text} KST`;

  const date = new Date(text.replace(' ', 'T'));
  if (!Number.isFinite(date.getTime())) return text;
  const kst = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  const y = kst.getUTCFullYear();
  const m = String(kst.getUTCMonth() + 1).padStart(2, '0');
  const d = String(kst.getUTCDate()).padStart(2, '0');
  const hh = String(kst.getUTCHours()).padStart(2, '0');
  const mm = String(kst.getUTCMinutes()).padStart(2, '0');
  const ss = String(kst.getUTCSeconds()).padStart(2, '0');
  return `${y}-${m}-${d} ${hh}:${mm}:${ss} KST`;
}

export default function NotificationsModal({ open, onClose }) {
  const [notis, setNotis] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [retentionHours, setRetentionHours] = useState(24);
  const [detailNotiId, setDetailNotiId] = useState(null);

  const loadPage = useCallback(
    async ({ reset = false, pageOffset = 0 } = {}) => {
      const nextOffset = reset ? 0 : Math.max(0, Number(pageOffset) || 0);
      if (reset) setLoading(true);
      else setLoadingMore(true);
      try {
        const res = await listNotifications({
          unread_only: unreadOnly,
          limit: PAGE_SIZE,
          offset: nextOffset,
        });
        const items = Array.isArray(res?.items) ? res.items : [];
        setRetentionHours(Number(res?.retention_hours || 24));
        setHasMore(Boolean(res?.has_more));
        setOffset(nextOffset + items.length);
        if (reset) setNotis(items);
        else setNotis((prev) => [...prev, ...items]);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.error(e);
        if (reset) setNotis([]);
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [unreadOnly]
  );

  useEffect(() => {
    if (!open) return;
    setOffset(0);
    setNotis([]);
    setDetailNotiId(null);
    loadPage({ reset: true });
  }, [open, loadPage]);

  useEffect(() => {
    if (!open) return undefined;
    const refreshList = () => loadPage({ reset: true });
    window.addEventListener('notifications:changed', refreshList);
    return () => window.removeEventListener('notifications:changed', refreshList);
  }, [open, loadPage]);

  const handleMarkAllRead = async () => {
    try {
      await markAllRead();
      setNotis((prev) =>
        prev.map((n) => ({
          ...n,
          is_read: 1,
          read_at: n.read_at || new Date().toISOString(),
        }))
      );
      window.dispatchEvent(new CustomEvent('notifications:refresh'));
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error(e);
    }
  };

  const handleMarkOneRead = async (id) => {
    if (!id) return;
    try {
      await markRead([id]);
      setNotis((prev) =>
        prev.map((n) =>
          n.id === id
            ? {
                ...n,
                is_read: 1,
                read_at: n.read_at || new Date().toISOString(),
              }
            : n
        )
      );
      window.dispatchEvent(new CustomEvent('notifications:refresh'));
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error(e);
    }
  };

  const handleDeleteAll = async () => {
    const ok = window.confirm('Delete all notifications?');
    if (!ok) return;
    try {
      await deleteAllNotifications();
      setNotis([]);
      setOffset(0);
      setHasMore(false);
      window.dispatchEvent(new CustomEvent('notifications:refresh'));
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error(e);
    }
  };

  const detailNoti = notis.find((n) => n.id === detailNotiId) || null;

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
          <div className="modal-title-wrap">
            <div className="modal-title">Notifications</div>
            <div className="notif-retention-inline">
              Notifications are kept for {retentionHours} hours (KST).
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="close">
            &times;
          </button>
        </div>

        <div className="modal-body">
          {loading ? (
            <div className="loading">Loading...</div>
          ) : !notis.length ? (
            <div className="empty">Empty</div>
          ) : (
            <>
              <ul className="list">
                {notis.map((n) => {
                  const unread = Number(n.is_read || 0) === 0 && !n.read_at;
                  return (
                    <li
                      key={n.id}
                      className={`list-item${unread ? ' unread' : ''}`}
                      onClick={() => setDetailNotiId(n.id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setDetailNotiId(n.id);
                        }
                      }}
                    >
                      <div className="row">
                        <span className={`badge ${n.severity || 'info'}`}>
                          {n.kind || 'system'}
                        </span>
                        <strong className="title">{n.title || '(No title)'}</strong>
                      </div>
                      <div className="notif-meta">
                        <time className="muted">{formatKst(n.created_at)}</time>
                        {unread ? (
                          <button
                            type="button"
                            className="btn-ghost notif-read-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleMarkOneRead(n.id);
                            }}
                          >
                            Mark read
                          </button>
                        ) : (
                          <span className="muted">Read</span>
                        )}
                      </div>
                      {unread && <span className="dot" aria-hidden="true" />}
                    </li>
                  );
                })}
              </ul>
              {hasMore ? (
                <div className="notif-show-more-wrap">
                  <button
                    type="button"
                    className="btn-ghost notif-show-more"
                    disabled={loadingMore}
                    onClick={() => loadPage({ reset: false, pageOffset: offset })}
                  >
                    {loadingMore ? 'Loading...' : 'Show more'}
                  </button>
                </div>
              ) : null}
            </>
          )}
        </div>

        <div className="modal-footer">
          <div className="notif-footer-actions">
            <button
              type="button"
              className="btn-ghost notif-unread-toggle"
              onClick={() => setUnreadOnly((v) => !v)}
            >
              {unreadOnly ? 'Show all' : 'Unread only'}
            </button>
            <button className="btn-ghost" onClick={handleMarkAllRead}>
              Read all
            </button>
            <button className="btn-ghost" onClick={() => loadPage({ reset: true })}>
              Refresh
            </button>
            <button className="btn-ghost btn-danger-soft" onClick={handleDeleteAll}>
              Delete all
            </button>
          </div>
        </div>
        <NotificationDetailModal
          open={Boolean(detailNoti)}
          notification={detailNoti}
          onClose={() => setDetailNotiId(null)}
          onMarkRead={handleMarkOneRead}
          formatTime={formatKst}
        />
      </div>
    </div>,
    document.body
  );
}
