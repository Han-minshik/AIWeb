import React, { useMemo } from 'react';
import { createPortal } from 'react-dom';
import '../../styles/components/notification-detail-modal.css';

const FIELD_LABELS = {
  by: 'Updated by',
  title: 'Document title',
  file: 'File',
  doc_id: 'Document',
  rev_no: 'Revision',
  is_admin: 'Admin permission',
  is_active: 'Active status',
  reason: 'Reason',
  source: 'Source',
  path: 'Path',
  message: 'Message',
  request_id: 'Request ID',
  applied: 'Applied items',
  failed: 'Failed items',
  detail: 'Failure details',
  changes: 'Changed fields',
};

const FLAG_FIELDS = new Set(['is_admin', 'is_active', 'is_read']);

function toLabel(key) {
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  const text = String(key || '').replace(/_/g, ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : 'Detail';
}

function normalizeFlagValue(field, value) {
  const raw = String(value ?? '').trim();
  if (!FLAG_FIELDS.has(field)) return raw;
  const lower = raw.toLowerCase();
  if (lower === '1' || lower === 'true' || lower === 'yes') return 'Enabled';
  if (lower === '0' || lower === 'false' || lower === 'no') return 'Disabled';
  return raw;
}

function parsePairs(text) {
  const source = String(text || '').trim();
  if (!source) return [];
  const re = /(?:^|,\s*)([a-zA-Z_][a-zA-Z0-9_]*)=/g;
  const marks = [];
  let m;
  while ((m = re.exec(source)) !== null) {
    marks.push({
      key: m[1],
      matchIndex: m.index,
      valueStart: re.lastIndex,
    });
  }
  if (!marks.length) return [];
  const out = [];
  for (let i = 0; i < marks.length; i += 1) {
    const curr = marks[i];
    const next = marks[i + 1];
    const end = next ? next.matchIndex : source.length;
    const value = source.slice(curr.valueStart, end).replace(/,\s*$/, '').trim();
    out.push({ key: curr.key, value });
  }
  return out;
}

function parseChanges(value) {
  const src = String(value || '').trim();
  if (!src) return [];
  return src
    .split(/\s*,\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const m = part.match(/^([a-zA-Z_][a-zA-Z0-9_]*):(.*?)\s*->\s*(.*)$/);
      if (!m) return null;
      const field = m[1];
      const fromRaw = m[2].trim();
      const toRaw = m[3].trim();
      return `${toLabel(field)}: ${normalizeFlagValue(field, fromRaw)} -> ${normalizeFlagValue(field, toRaw)}`;
    })
    .filter(Boolean);
}

function toFriendlyRows(body) {
  const rows = [];
  const pairs = parsePairs(body);
  if (!pairs.length) return rows;

  for (let i = 0; i < pairs.length; i += 1) {
    const { key, value } = pairs[i];
    if (key === 'changes') {
      const lines = parseChanges(value);
      if (lines.length) {
        rows.push({ label: toLabel(key), lines });
      } else if (value) {
        rows.push({ label: toLabel(key), value });
      }
      continue;
    }
    if (key === 'by') {
      rows.push({ label: toLabel(key), value: value ? `User #${value}` : '-' });
      continue;
    }
    if (key === 'doc_id') {
      rows.push({ label: toLabel(key), value: value ? `#${value}` : '-' });
      continue;
    }
    if (key === 'rev_no') {
      rows.push({ label: toLabel(key), value: value ? `#${value}` : '-' });
      continue;
    }
    if (key === 'request_id' && (!value || value === '-')) {
      rows.push({ label: toLabel(key), value: 'N/A' });
      continue;
    }
    rows.push({ label: toLabel(key), value: normalizeFlagValue(key, value || '-') });
  }
  return rows;
}

export default function NotificationDetailModal({
  open,
  notification,
  onClose,
  onMarkRead,
  formatTime,
}) {
  const details = useMemo(() => toFriendlyRows(notification?.body), [notification?.body]);
  if (!open || !notification) return null;

  const unread = Number(notification.is_read || 0) === 0 && !notification.read_at;

  return createPortal(
    <div className="notif-detail-backdrop" onClick={onClose}>
      <div
        className="notif-detail-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Notification details"
      >
        <div className="notif-detail-head">
          <h3 className="notif-detail-title">{notification.title || 'Notification'}</h3>
          <button type="button" className="notif-detail-close" onClick={onClose} aria-label="close">
            &times;
          </button>
        </div>

        <div className="notif-detail-meta">
          <span className={`badge ${notification.severity || 'info'}`}>{notification.kind || 'system'}</span>
          <span className="muted">{formatTime ? formatTime(notification.created_at) : notification.created_at}</span>
        </div>

        <div className="notif-detail-body">
          {details.length ? (
            <div className="notif-detail-grid">
              {details.map((row, idx) => (
                <div className="notif-detail-row" key={`${row.label}-${idx}`}>
                  <div className="notif-detail-label">{row.label}</div>
                  {Array.isArray(row.lines) ? (
                    <ul className="notif-detail-list">
                      {row.lines.map((line, lineIdx) => (
                        <li key={`${line}-${lineIdx}`}>{line}</li>
                      ))}
                    </ul>
                  ) : (
                    <div className="notif-detail-value">{row.value || '-'}</div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="notif-detail-value">
              {String(notification.body || 'No additional details.').trim() || 'No additional details.'}
            </div>
          )}
        </div>

        <div className="notif-detail-footer">
          {unread ? (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => onMarkRead && onMarkRead(notification.id)}
            >
              Mark read
            </button>
          ) : (
            <span className="muted">Read</span>
          )}
          <button type="button" className="btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
