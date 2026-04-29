import React, { useCallback, useMemo } from 'react';
import StatusTicker from '../../common/StatusTicker';
import Bubble from '../../right/Bubble';

const ChatBody = React.memo(function ChatBody({
  messages,
  tickerVisible,
  tickerStep,
  tickerHint,
  tickerProgress,
  streamingMsgId,
  finalHandled,
  lastAssistantId,
  scrollRef,
  bottomRef,
  openDoc,
  openDocByTitle,
  normalizeMdLite = (s = '') => String(s ?? ''),
}) {
  const isAssistantAddon = useCallback((m) => {
    return (
      m?.role === 'assistant' &&
      typeof m?.content === 'object' &&
      (m.content?.type === 'refs' || m.content?.type === 'gallery' || m.content?.type === 'image')
    );
  }, []);

  const actionMeta = useMemo(() => {
    const n = messages.length;
    const showActions = new Array(n).fill(false);
    const actionText = new Array(n).fill('');

    const findActionTextFor = (idx) => {
      for (let i = idx; i >= 0; i--) {
        const m = messages[i];
        if (m?.role !== 'assistant') break;
        if (typeof m?.content === 'string' && m.content.trim()) return m.content;
        if (!isAssistantAddon(m)) break;
      }
      return '';
    };

    for (let idx = 0; idx < n; idx++) {
      const cur = messages[idx];
      if (cur?.role !== 'assistant') continue;

      const isStreamingNow = cur?.id === streamingMsgId && finalHandled !== true;
      if (isStreamingNow) continue;

      const next = messages[idx + 1];
      const nextIsAddon = next && isAssistantAddon(next);

      showActions[idx] = !(next && nextIsAddon);
      if (showActions[idx]) actionText[idx] = findActionTextFor(idx);
    }

    return { showActions, actionText };
  }, [messages, streamingMsgId, finalHandled, isAssistantAddon]);

  const tickerPercent = Number.isFinite(Number(tickerProgress))
    ? Math.round((Math.max(1, Math.min(4, Number(tickerProgress))) / 4) * 100)
    : undefined;

  return (
    <div ref={scrollRef} className="chat-scroll card scroll">
      {messages.map((m, idx) => {
        if (
          tickerVisible &&
          m.id === streamingMsgId &&
          m.role === 'assistant' &&
          typeof m.content === 'string' &&
          !m.content.trim()
        ) {
          return (
            <div key={m.id}>
              <div className="bubble-row left" style={{ marginBottom: 4 }}>
                <div
                  className="bubble bubble-assistant"
                  style={{ padding: 0, background: 'transparent', boxShadow: 'none' }}
                >
                  <StatusTicker
                    visible
                    step={tickerStep}
                    hint={tickerHint}
                    steps={['Question Analysis', 'Searching', 'Thinking', 'Answering']}
                    orientation="horizontal"
                    compact={false}
                    variant="pipeline"
                    percent={tickerPercent}
                  />
                </div>
              </div>
            </div>
          );
        }

        if (typeof m.content === 'string' && !m.content.trim()) return null;

        const isStreamingBubble = m.id === streamingMsgId && !finalHandled;
        const messageMid = m.message_id || lastAssistantId || m.id;

        return (
          <div key={m.id}>
            <Bubble
              role={m.role}
              content={m.content}
              messageMid={messageMid}
              showActions={actionMeta.showActions[idx]}
              actionText={actionMeta.actionText[idx]}
              isStreaming={isStreamingBubble}
              normalizeMdLite={normalizeMdLite}
              messages={messages}
              openDoc={openDoc}
              openDocByTitle={openDocByTitle}
            />
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
});



export default ChatBody;
