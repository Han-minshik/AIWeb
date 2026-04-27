import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import checkAllIcon from '../../assets/icons/check-all.svg';
import copyIcon from '../../assets/icons/copy.svg';
import MarkdownBlock from '../../components/common/MarkdownBlock';
import AnswerActionBar from './AnswerActionBar';
import Gallery from './Gallery';
import RefAccordion from './RefAccordion';

export default function Bubble({
  role,
  content,
  messageMid,
  showActions,
  actionText,
  isStreaming,
  normalizeMdLite,
  messages,
  openDoc,
  openDocByTitle,
}) {
  const isUser = role === 'user';
  const [userCopied, setUserCopied] = useState(false);
  const userCopyTimeoutRef = useRef(null);
  const textContent = typeof content === 'string' ? content : '';

  const md = useMemo(() => {
    return isStreaming ? normalizeMdLite(textContent) : textContent;
  }, [isStreaming, normalizeMdLite, textContent]);

  useEffect(() => {
    setUserCopied(false);
    if (userCopyTimeoutRef.current) {
      clearTimeout(userCopyTimeoutRef.current);
      userCopyTimeoutRef.current = null;
    }
  }, [messageMid, textContent]);

  useEffect(() => {
    return () => {
      if (userCopyTimeoutRef.current) {
        clearTimeout(userCopyTimeoutRef.current);
        userCopyTimeoutRef.current = null;
      }
    };
  }, []);

  const onCopyUserQuestion = useCallback(async () => {
    if (!textContent) return;
    try {
      await navigator.clipboard.writeText(textContent);
      setUserCopied(true);
      if (userCopyTimeoutRef.current) clearTimeout(userCopyTimeoutRef.current);
      userCopyTimeoutRef.current = setTimeout(() => {
        setUserCopied(false);
        userCopyTimeoutRef.current = null;
      }, 3000);
    } catch {}
  }, [textContent]);

  const feedback = useMemo(() => {
    if (!messageMid || !Array.isArray(messages)) return { liked: null, comment: '' };
    const m = messages.find((x) => x?.message_id === messageMid);
    const score = m?.feedback?.score;
    return {
      liked: score === 1 ? true : score === -1 ? false : null,
      comment: m?.feedback?.comment || '',
    };
  }, [messages, messageMid]);

  if (typeof content === 'object' && content?.type === 'refs') {
    const items = Array.isArray(content.items) ? content.items : [];
    return (
      <RefAccordion
        items={items}
        isUser={isUser}
        showActions={showActions && !isStreaming}
        actionText={actionText}
        messageMid={messageMid}
        messages={messages}
        openDoc={openDoc}
        openDocByTitle={openDocByTitle}
      />
    );
  }

  if (typeof content === 'object' && content?.type === 'gallery') {
    return (
      <>
        <Gallery images={Array.isArray(content.images) ? content.images : []} />
        {!isUser && showActions && !isStreaming && (
          <AnswerActionBar
            isAssistant
            contentStr={actionText || ''}
            messageMid={messageMid}
            fallbackMessageId={null}
            initialLiked={feedback.liked}
            initialComment={feedback.comment}
          />
        )}
      </>
    );
  }

  if (typeof content === 'object' && content?.type === 'image') {
    return (
      <>
        <div className={`bubble-row ${isUser ? 'right' : 'left'}`}>
          <div className="md center">
            <img
              src={content.src}
              alt=""
              style={{ maxWidth: '100%', borderRadius: '8px', display: 'block', margin: '8px 0' }}
            />
          </div>
        </div>

        {!isUser && showActions && !isStreaming && (
          <AnswerActionBar
            isAssistant
            contentStr={actionText || ''}
            messageMid={messageMid}
            fallbackMessageId={null}
            initialLiked={feedback.liked}
            initialComment={feedback.comment}
          />
        )}
      </>
    );
  }

  return (
    <>
      {isUser ? (
        <div className="user-question-group">
          <div className="bubble-row right">
            <div className="bubble bubble-user">
              <div className="md section-card">
                <MarkdownBlock content={md} preserveLineBreaks />
              </div>
            </div>
          </div>
          <div className="user-question-actions">
            <button
              className={`btn-chip question-copy-btn ${userCopied ? 'sent' : ''}`}
              onClick={onCopyUserQuestion}
              aria-label="Copy question"
            >
              <img
                className="aa-icon"
                src={userCopied ? checkAllIcon : copyIcon}
                alt=""
                aria-hidden="true"
              />
            </button>
          </div>
        </div>
      ) : (
        <div className="bubble-row left">
          <div className="bubble bubble-assistant">
            <div className="md section-card">
              <MarkdownBlock content={md} />
            </div>
          </div>
        </div>
      )}

      {!isUser && showActions && !isStreaming && (
        <AnswerActionBar
          isAssistant
          contentStr={textContent}
          messageMid={messageMid}
          fallbackMessageId={null}
          initialLiked={feedback.liked}
          initialComment={feedback.comment}
        />
      )}
    </>
  );
}
