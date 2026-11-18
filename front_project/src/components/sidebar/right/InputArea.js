import React, { useCallback, useEffect, useRef, useState } from 'react';
import { SendIcon } from 'lucide-react';
import KBChipSelect from '../../right/KBChipSelect';
import chatIcon from '../../../assets/icons/chat.svg';
import clockHistoryIcon from '../../../assets/icons/clock-history.svg';
import stopCircleIcon from '../../../assets/icons/stop-circle.svg';
import sendIcon from '../../../assets/icons/send.svg';

const InputArea = React.memo(function InputArea({
  showKBSelector,
  kbChipLabel,
  onChangeKBSelect,
  vaultKbId,
  activeDocDocId,
  bookmarkDocCount,
  isStreaming,
  onSubmitText,
  onStop,
  resetNonce,
}) {
  const [text, setText] = useState('');
  const taRef = useRef(null);

  const metricsRef = useRef({
    ready: false,
    lh: 20,
    pad: 0,
    border: 0,
    maxH: 240,
    lastH: 0,
    lastOv: '',
  });

  const rafRef = useRef(0);

  const computeMetrics = useCallback((ta) => {
    if (!ta) return;
    const cs = window.getComputedStyle(ta);
    const lh = parseFloat(cs.lineHeight) || 20;
    const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    const border =
      (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
    const maxH = lh * 10 + pad + border;
    metricsRef.current = {
      ready: true,
      lh,
      pad,
      border,
      maxH,
      lastH: metricsRef.current.lastH || 0,
      lastOv: metricsRef.current.lastOv || '',
    };
  }, []);

  const applyAutosize = useCallback((ta) => {
    if (!ta) return;
    if (!metricsRef.current.ready) computeMetrics(ta);

    ta.style.height = 'auto';

    const { maxH } = metricsRef.current;
    const sh = ta.scrollHeight || 0;
    const nextH = Math.min(sh, maxH);
    const nextOv = sh > maxH ? 'auto' : 'hidden';

    if (metricsRef.current.lastH !== nextH) {
      ta.style.height = `${nextH}px`;
      metricsRef.current.lastH = nextH;
    } else {
      ta.style.height = `${nextH}px`;
    }

    if (metricsRef.current.lastOv !== nextOv) {
      ta.style.overflowY = nextOv;
      metricsRef.current.lastOv = nextOv;
    }
  }, [computeMetrics]);

  const scheduleAutosize = useCallback(() => {
    const ta = taRef.current;
    if (!ta) return;
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      applyAutosize(ta);
    });
  }, [applyAutosize]);

  useEffect(() => {
    setText('');
    const ta = taRef.current;
    if (ta) {
      computeMetrics(ta);
      scheduleAutosize();
    }
  }, [resetNonce, computeMetrics, scheduleAutosize]);

  useEffect(() => {
    scheduleAutosize();
  }, [text, scheduleAutosize]);

  useEffect(() => {
    const onResize = () => {
      const ta = taRef.current;
      if (!ta) return;
      computeMetrics(ta);
      scheduleAutosize();
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [computeMetrics, scheduleAutosize]);

  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    };
  }, []);

  const submitLocal = useCallback(() => {
    const q = (text || '').replace(/\r\n/g, '\n').trimEnd();
    if (!q) return;
    onSubmitText(q);
    setText('');
  }, [text, onSubmitText]);

  return (
    <div className="assistant-input-area">
      <div className="assistant-toolbar">
        {showKBSelector && (
          <KBChipSelect
            kbChipLabel={kbChipLabel}
            onChangeKBSelect={onChangeKBSelect}
            vaultKbId={vaultKbId}
            activeDocDocId={activeDocDocId}
            bookmarkDocCount={bookmarkDocCount}
          />
        )}
        <div className="toolbar-spacer" />
        <button
          className="icon-btn"
          title="New Chat"
          onClick={() => window.dispatchEvent(new CustomEvent('qa:new'))}
        >
          <img src={chatIcon} alt="" aria-hidden="true" />
        </button>
        <button
          className="icon-btn"
          title="History"
          onClick={() => window.dispatchEvent(new CustomEvent('qa:history:open'))}
        >
          <img src={clockHistoryIcon} alt="" aria-hidden="true" />
        </button>
      </div>

      <div
        className="input-wrap"
        onClick={() => taRef.current?.focus()}
        style={{
          display: 'flex',
          gap: 8,
          alignItems: 'flex-start',
          cursor: 'text',
          height: 'auto',
        }}
      >
        <textarea
          ref={taRef}
          className="assistant-input"
          aria-label="Message input"
          value={text}
          placeholder=""
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || e.isComposing) return;
            if (e.shiftKey) return;
            e.preventDefault();
            if (!isStreaming) submitLocal();
          }}
          style={{ flex: 1 }}
        />

        {!!text.trim() && (
          <button
            type="button"
            className="icon-btn-send"
            title="send"
            onClick={(e) => {
              e.stopPropagation();
              submitLocal();
            }}
            disabled={isStreaming}
            style={{ alignSelf: 'center', height: 36, minWidth: 36 }}
          >
            <img src={sendIcon} alt="" aria-hidden="true" />
          </button>
        )}

        {isStreaming && (
          <button
            className="icon-btn-stop"
            title="stop"
            onClick={onStop}
            style={{ alignSelf: 'center', height: 36, minWidth: 36 }}
          >
            <img src={stopCircleIcon} alt="" aria-hidden="true" />
          </button>
        )}
      </div>

      <div className="assistant-disclaimer" aria-live="polite">
        AI can make mistakes. Double-check important information.
      </div>
    </div>
  );
});



export default InputArea;