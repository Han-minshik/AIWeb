import React, { useMemo, useState } from 'react';

function normalizeRow(row) {
  const rating = typeof row.rating !== 'undefined' ? row.rating : row.score;
  const score = typeof row.score !== 'undefined' ? row.score : rating;

  const sessionId = row.session_id ?? row.sessionId ?? row.message_id ?? row.msg_id;

  return {
    key: String(row.feedback_id ?? row.id ?? `${row.msg_id ?? ''}-${row.user_id ?? ''}`),
    createdAt: row.created_at ?? row.time ?? row.ts,
    rating,
    score,
    comment: row.comment ?? '',
    sessionId,
    appType: row.app_type ?? row.app ?? '',
    sessionSummary: row.session_summary ?? row.summary ?? '',
    raw: row,
  };
}

function formatRatingLabel(rating, score) {
  if (typeof rating === 'number') {
    if (rating >= 5) return 'Like';
    if (rating <= 1) return 'Dislike';
  }
  if (typeof score === 'number') {
    if (score > 0) return 'Like';
    if (score < 0) return 'Dislike';
  }
  return 'Neutral';
}

export default function SessionFeedbackTable({
  rows = [],
  onSelectRow,
}) {
  const [query, setQuery] = useState('');
  const [onlyWithComment, setOnlyWithComment] = useState(false);

  const normalized = useMemo(
    () => (Array.isArray(rows) ? rows.map(normalizeRow) : []),
    [rows],
  );

  const filtered = useMemo(() => {
    return normalized.filter((r) => {
      if (onlyWithComment && !r.comment) return false;

      if (!query) return true;

      const q = query.toLowerCase();
      return (
        (r.comment && r.comment.toLowerCase().includes(q)) ||
        (r.sessionSummary && r.sessionSummary.toLowerCase().includes(q)) ||
        (r.appType && r.appType.toLowerCase().includes(q)) ||
        (r.sessionId && String(r.sessionId).includes(q))
      );
    });
  }, [normalized, query, onlyWithComment]);

  return (
    <div className="card feedback-table-card">
      <div className="feedback-table-toolbar">
        <input
          className="input"
          placeholder="Search in comment / session summary / app / session id"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="feedback-table-toggle">
          <input
            type="checkbox"
            checked={onlyWithComment}
            onChange={(e) => setOnlyWithComment(e.target.checked)}
          />
          <span className="muted">Comment only</span>
        </label>
      </div>

      <div className="table-wrap feedback-table-wrap">
        <table className="table feedback-table">
          <thead>
            <tr>
              <th style={{ width: 160 }}>Time</th>
              <th style={{ width: 96 }}>Session</th>
              <th style={{ width: 96 }}>App</th>
              <th style={{ width: 96 }}>Rating</th>
              <th>Comment / Summary</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 16 }}>
                  No feedback in this range.
                </td>
              </tr>
            )}

            {filtered.map((r) => {
              const label = formatRatingLabel(r.rating, r.score);

              let ratingClass = '';
              if (label === 'Like') ratingClass = 'rating-pill rating-like';
              else if (label === 'Dislike') ratingClass = 'rating-pill rating-dislike';
              else ratingClass = 'rating-pill rating-neutral';

              return (
                <tr
                  key={r.key}
                  className="feedback-row"
                  onClick={() => onSelectRow && onSelectRow(r.raw)}
                >
                  <td>{r.createdAt}</td>
                  <td>{r.sessionId ?? '-'}</td>
                  <td>{r.appType || '-'}</td>
                  <td>
                    <span className={ratingClass}>{label}</span>
                  </td>
                  <td>
                    {r.comment ? (
                      <span className="feedback-comment">
                        {r.comment.length > 80 ? `${r.comment.slice(0, 80)}…` : r.comment}
                      </span>
                    ) : (
                      <span className="muted">
                        {r.sessionSummary || '(no comment)'}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
