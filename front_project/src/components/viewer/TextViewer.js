import React, { useEffect, useMemo, useRef, useState } from 'react';

const LKEY = 'qa.last_assistant_message_id';

export default function TextViewer({ title = 'Result', content = '' }) {
  const scrollRef = useRef(null);
  const [lastMsgId, setLastMsgId] = useState(() => {
    const v = sessionStorage.getItem(LKEY);
    return v ? Number(v) : null;
  });

  const lines = useMemo(() => {
    const raw = String(content ?? '');
    if (!raw.includes('\n') && !/^#{1,6}\s+/.test(raw)) {
      return null; 
    }
    const arr = raw.split(/\r?\n/);
    return arr.map((line) => {
      const m = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
      if (m) {
        return {
          type: 'heading',
          level: m[1].length,
          text: m[2],
          raw: line,
        };
      }
      return { type: 'text', raw: line };
    });
  }, [content]);

  useEffect(() => {
    const handler = (e) => {
      const heading = (e?.detail?.heading || '').trim();
      if (!heading || !scrollRef.current) return;

      const nodes = scrollRef.current.querySelectorAll('[data-heading-text]');
      let target = null;
      for (const el of nodes) {
        if ((el.dataset.headingText || '').trim() === heading) {
          target = el;
          break;
        }
      }
      if (!target) return;

      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      const prevBg = target.style.backgroundColor;
      target.style.backgroundColor = 'rgba(255, 235, 59, 0.35)';
      setTimeout(() => {
        target.style.transition = 'background-color 400ms ease';
        target.style.backgroundColor = prevBg || 'transparent';
      }, 900);
    };

    window.addEventListener('doc:goto-heading', handler);
    return () => window.removeEventListener('doc:goto-heading', handler);
  }, []);

  useEffect(() => {
    const onLast = (e) => {
      const mid = e?.detail?.messageId;
      if (mid != null) setLastMsgId(Number(mid));
    };
    window.addEventListener('qa:last-message', onLast);
    return () => window.removeEventListener('qa:last-message', onLast);
  }, []);

  const sendFeedback = (score, comment) => {
    if (!lastMsgId) return; 
    window.dispatchEvent(new CustomEvent('qa:feedback', {
      detail: { messageId: lastMsgId, score, comment }
    }));
  };
  const onLike = () => sendFeedback(1);
  const onDislike = () => sendFeedback(-1);
  const onComment = () => {
    const c = window.prompt('Enter your comment (optional)');
    if (c && c.trim()) sendFeedback(0, c.trim()); 
  };
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(String(content ?? ''));
    } catch {
    }
  };

  return (
    <div className="viewer text-viewer">
      <div className="panel-head" style={{ padding: '6px 10px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div className="panel-title" style={{ flex: 1 }}>{title}</div>

        <div className="panel-actions" style={{ display: 'flex', gap: 6 }}>
          <button className="icon-btn" title="Like" onClick={onLike} disabled={!lastMsgId}><i className="bi bi-hand-thumbs-up" /></button>
          <button className="icon-btn" title="Dislike" onClick={onDislike} disabled={!lastMsgId}><i className="bi bi-hand-thumbs-down" /></button>
          <button className="icon-btn" title="Comment" onClick={onComment} disabled={!lastMsgId}><i className="bi bi-chat-left-text" /></button>
          <span className="muted" style={{ alignSelf: 'center' }}>|</span>
          <button className="icon-btn" title="Copy" onClick={onCopy}><i className="bi bi-clipboard" /></button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="scroll"
        style={{ padding: 12, whiteSpace: 'pre-wrap' }}
      >
        {lines ? (
          lines.map((ln, idx) => {
            if (ln.type === 'heading') {
              return (
                <div
                  key={idx}
                  data-heading-text={ln.text}
                  style={{ fontWeight: 600 }}
                >
                  {ln.raw}
                </div>
              );
            }
            return <div key={idx}>{ln.raw}</div>;
          })
        ) : (
          content
        )}
      </div>
    </div>
  );
}
