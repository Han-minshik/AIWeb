import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import checkAllIcon from '../../assets/icons/check-all.svg';
import copyIcon from '../../assets/icons/copy.svg';
import pencilIcon from '../../assets/icons/pencil.svg';
import likeIcon from '../../assets/icons/like.svg';
import dislikeIcon from '../../assets/icons/dislike.svg';

const ICONS = {
  checkAll: checkAllIcon,
  copy: copyIcon,
  pencil: pencilIcon,
  like: likeIcon,
  dislike: dislikeIcon,
};

const Icon = memo(function Icon({ src, className }) {
  return <img className={className} src={src} alt="" aria-hidden="true" />;
});

function AnswerActionBar({
  isAssistant,
  contentStr,
  messageMid,
  fallbackMessageId = null,
  initialLiked = null,
  initialComment = '',
}) {
  const [fbPanelOpen, setFbPanelOpen] = useState(false);
  const [liked, setLiked] = useState(initialLiked);
  const [comment, setComment] = useState(initialComment || '');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(Boolean(initialLiked !== null));
  const [copied, setCopied] = useState(false);

  const copyTimeoutRef = useRef(null);

  const prefKey = useMemo(
    () => `qa.feedback.${String(messageMid || fallbackMessageId || '')}`,
    [messageMid, fallbackMessageId]
  );

  useEffect(() => {
    setCopied(false);
    if (copyTimeoutRef.current) {
      clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = null;
    }
  }, [messageMid]);

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
        copyTimeoutRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const mid = messageMid || fallbackMessageId;
    if (!mid) {
      setLiked(initialLiked);
      setComment(initialComment || '');
      setSent(Boolean(initialLiked !== null));
      return;
    }

    let stored = null;
    try {
      const raw = localStorage.getItem(prefKey);
      stored = raw ? JSON.parse(raw) : null;
    } catch {
      stored = null;
    }

    const storedLiked =
      stored && typeof stored.score === 'number'
        ? stored.score > 0
          ? true
          : stored.score < 0
          ? false
          : null
        : null;

    setLiked(storedLiked !== null ? storedLiked : initialLiked);
    setComment(
      (stored && typeof stored.comment === 'string' ? stored.comment : initialComment) || ''
    );
    setSent(Boolean(storedLiked !== null ? storedLiked !== null : initialLiked !== null));
  }, [messageMid, fallbackMessageId, initialLiked, initialComment, prefKey]);

  useEffect(() => {
    const onStatus = (e) => {
      const d = e?.detail || {};
      const currentMid = messageMid || fallbackMessageId;
      if (!d || !d.messageId || !currentMid || d.messageId !== currentMid) return;

      setSending(false);
      if (d.ok) {
        setSent(true);
        try {
          localStorage.setItem(
            prefKey,
            JSON.stringify({
              score: d.score,
              comment: d.comment || '',
              at: Date.now(),
            })
          );
        } catch {}
      }
    };
    window.addEventListener('qa:feedback-status', onStatus);
    return () => window.removeEventListener('qa:feedback-status', onStatus);
  }, [messageMid, fallbackMessageId, prefKey]);

  if (!isAssistant || !contentStr) return null;

  const persistLocal = useCallback(
    (score, cmt = '') => {
      try {
        localStorage.setItem(
          prefKey,
          JSON.stringify({
            score,
            comment: cmt || '',
            at: Date.now(),
          })
        );
      } catch {}
    },
    [prefKey]
  );

  const sendFeedback = useCallback(
    (score, cmt = '') => {
      const mid = messageMid || fallbackMessageId;
      if (!mid) {
        window.alert('Message ID not found. Please try again later.');
        return;
      }
      setSending(true);
      window.dispatchEvent(
        new CustomEvent('qa:feedback', {
          detail: { messageId: mid, score, comment: cmt },
        })
      );
    },
    [messageMid, fallbackMessageId]
  );

  const onLike = useCallback(() => {
    setLiked(true);
    setFbPanelOpen(false);
    setSent(true);
    persistLocal(1, comment);
    if (!sending) sendFeedback(1);
  }, [persistLocal, comment, sending, sendFeedback]);

  const onDislike = useCallback(() => {
    setLiked(false);
    setFbPanelOpen(false);
    setSent(true);
    persistLocal(-1, comment);
    if (!sending) sendFeedback(-1);
  }, [persistLocal, comment, sending, sendFeedback]);

  const onSubmitFb = useCallback(() => {
    const score = liked === false ? -1 : 1;
    const cmt = comment.trim();
    setSent(true);
    persistLocal(score, cmt);
    sendFeedback(score, cmt);
    setComment('');
    setFbPanelOpen(false);
  }, [liked, comment, persistLocal, sendFeedback]);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(contentStr);
      setCopied(true);
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = setTimeout(() => {
        setCopied(false);
        copyTimeoutRef.current = null;
      }, 3000);
    } catch {}
  }, [contentStr]);

  return (
    <div
      className="answer-actions"
      style={{ display: 'flex', gap: 8, margin: '6px 12px 0', alignItems: 'center' }}
    >
      <button
        className={`btn-chip ${sending ? 'sending' : ''} ${sent ? 'sent' : ''}`}
        onClick={onCopy}
        aria-label="복사"
      >
        {copied ? (
          <Icon src={ICONS.checkAll} className="aa-icon aa-icon-check-all" />
        ) : (
          <Icon src={ICONS.copy} className="aa-icon aa-icon-copy" />
        )}
      </button>

      <button
        className={`btn-chip ${liked === true ? 'active' : ''} ${sending ? 'sending' : ''} ${
          sent ? 'sent' : ''
        }`}
        onClick={onLike}
        aria-label="좋아요"
      >
        <Icon src={ICONS.like} className="aa-icon aa-icon-like" />
      </button>

      <button
        className={`btn-chip ${liked === false ? 'active' : ''} ${sending ? 'sending' : ''} ${
          sent ? 'sent' : ''
        }`}
        onClick={onDislike}
        aria-label="싫어요"
      >
        <Icon src={ICONS.dislike} className="aa-icon aa-icon-dislike" />
      </button>

      <button
        className={`btn-chip ${sending ? 'sending' : ''} ${sent ? 'sent' : ''}`}
        onClick={() => setFbPanelOpen((v) => !v)}
        aria-label="피드백"
      >
        <Icon src={ICONS.pencil} className="aa-icon aa-icon-pencil" />
      </button>

      {fbPanelOpen && (
        <div className="fb-panel" style={{ width: '100%', marginTop: 8 }}>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={3}
            placeholder="Please write down the areas that need improvement."
            style={{ width: '100%', border: '1px solid var(--line)', borderRadius: 8, padding: 8 }}
          />
          <div
            className="fb-actions"
            style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 6 }}
          >
            <button className="btn" onClick={() => setFbPanelOpen(false)}>
              Cancel
            </button>
            <button className="btn send" onClick={onSubmitFb} disabled={sending}>
              Send
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(AnswerActionBar);
