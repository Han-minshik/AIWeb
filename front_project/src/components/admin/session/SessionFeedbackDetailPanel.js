import React from 'react';

function normalizeFeedback(feedback) {
  if (!feedback) return null;

  const rating = typeof feedback.rating !== 'undefined' ? feedback.rating : feedback.score;
  const score = typeof feedback.score !== 'undefined' ? feedback.score : rating;
  const sessionId =
    feedback.session_id ??
    feedback.sessionId ??
    feedback.message_id ??
    feedback.msg_id;

  const appType = feedback.app_type ?? feedback.app ?? '';
  const createdAt = feedback.created_at ?? feedback.time ?? feedback.ts;
  const comment = feedback.comment ?? '';
  const sessionSummary = feedback.session_summary ?? feedback.summary ?? '';

  return {
    rating,
    score,
    sessionId,
    appType,
    createdAt,
    comment,
    sessionSummary,
  };
}

function describeRating(rating, score) {
  if (typeof rating === 'number') {
    if (rating >= 5) return { label: 'Like', tone: 'positive' };
    if (rating <= 1) return { label: 'Dislike', tone: 'negative' };
  }
  if (typeof score === 'number') {
    if (score > 0) return { label: 'Like', tone: 'positive' };
    if (score < 0) return { label: 'Dislike', tone: 'negative' };
  }
  return { label: 'Neutral', tone: 'neutral' };
}

export default function SessionFeedbackDetailPanel({ feedback }) {
  const normalized = normalizeFeedback(feedback);

  if (!normalized) {
    return (
      <div className="card feedback-detail">
        <div className="feedback-detail-header">
          <strong>Feedback detail</strong>
        </div>
        <div className="feedback-detail-body muted">
          Select a row in the table to see detail.
        </div>
      </div>
    );
  }

  const { label, tone } = describeRating(normalized.rating, normalized.score);

  return (
    <div className="card feedback-detail">
      <div className="feedback-detail-header">
        <strong>Feedback detail</strong>
      </div>

      <div className="feedback-detail-body">
        <div className="feedback-detail-meta">
          <div>
            <div className="meta-label">Session</div>
            <div className="meta-value">{normalized.sessionId ?? '-'}</div>
          </div>
          <div>
            <div className="meta-label">App</div>
            <div className="meta-value">{normalized.appType || '-'}</div>
          </div>
          <div>
            <div className="meta-label">Time</div>
            <div className="meta-value">{normalized.createdAt || '-'}</div>
          </div>
          <div>
            <div className="meta-label">Rating</div>
            <div className={`meta-value rating-pill rating-${tone}`}>
              {label}
            </div>
          </div>
        </div>

        <div className="feedback-detail-section">
          <div className="section-title">Comment</div>
          <div className={normalized.comment ? 'section-body' : 'section-body muted'}>
            {normalized.comment || '(no comment)'}
          </div>
        </div>

        <div className="feedback-detail-section">
          <div className="section-title">Session summary</div>
          <div className={normalized.sessionSummary ? 'section-body' : 'section-body muted'}>
            {normalized.sessionSummary || '(no summary)'}
          </div>
        </div>
      </div>
    </div>
  );
}
