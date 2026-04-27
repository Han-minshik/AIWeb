import { useEffect } from 'react';
import { ragQuery } from '../../services/rag/ragService';
import {
  createSession,
  postMessage,
  saveFeedback,
  listSessions,
} from '../../services/chat/chatService';
import { streamQaEvents } from '../../services/qa/qaStreamService';
import {
  API_BASE,
  STAGE_TO_TICKER,
  collectImagesFromSources,
  enrichSources,
  getAccessToken,
  isForbidden,
  parseTickerPayload,
  setLastAssistantMessageId,
  setSessionId,
  toAbsUrl,
  unwrapSessions,
} from './qaEventBridgeUtils';
import { buildQaSubmitContext } from './qaSubmitContext';

export default function QAEventBridge() {
  useEffect(() => {
    setSessionId(null);

    let currentRunId = null;
    let stopped = false;

    let streamAbortController = null;
    let streamState = null;

    const cleanupStream = () => {
      try {
        if (streamAbortController) streamAbortController.abort();
      } catch {}
      streamAbortController = null;
    };

    const sendAssistantToSidebar = (payload) => {
      window.dispatchEvent(new CustomEvent('qa:assistant-reply', { detail: payload }));
    };

    const isActiveRun = (runId) => !!runId && !stopped && runId === currentRunId;

    const ticker = {
      open(step = 'Question Analysis', meta = {}) {
        const detail = { visible: true, step, ...(meta || {}) };
        window.dispatchEvent(new CustomEvent('qa:ticker', { detail }));
      },
      step(step, meta = {}) {
        const detail = { step, ...(meta || {}) };
        window.dispatchEvent(new CustomEvent('qa:ticker-step', { detail }));
      },
      close() {
        window.dispatchEvent(
          new CustomEvent('qa:ticker', {
            detail: { visible: false, step: 'Done', stage: 'done', progress: 4 },
          }),
        );
      },
      error() {
        window.dispatchEvent(
          new CustomEvent('qa:ticker', {
            detail: { visible: false, step: 'Error', stage: 'error' },
          }),
        );
      },
      reset() {
        window.dispatchEvent(new CustomEvent('qa:reset', { detail: {} }));
      },
    };

    const validateGivenSession = async (sid) => {
      if (!sid) return null;
      try {
        const list = unwrapSessions(await listSessions());
        const ok = list.some((s) => Number(s?.id ?? s?.session_id) === Number(sid));
        return ok ? sid : null;
      } catch {
        return null;
      }
    };

    const ensureSession = async (titleHint, appType = 'LLM', preferredSid = null) => {
      const validPreferred = await validateGivenSession(preferredSid);
      if (validPreferred) {
        setSessionId(validPreferred);
        window.dispatchEvent(new CustomEvent('qa:set-session', { detail: { sessionId: validPreferred } }));
        return validPreferred;
      }
      const title = titleHint ? `Q&A: ${titleHint.slice(0, 40)}` : 'Q&A Session';
      const s = await createSession(title, appType);
      const newSid = s?.id ?? s?.session_id ?? null;
      if (newSid) {
        setSessionId(newSid);
        window.dispatchEvent(new CustomEvent('qa:set-session', { detail: { sessionId: newSid } }));
      }
      return newSid;
    };

    const onAppChange = () => {
      setSessionId(null);
      setLastAssistantMessageId(null);
      stopped = true;
      currentRunId = null;
      ticker.reset();
      window.dispatchEvent(new CustomEvent('qa:history:refresh', { detail: {} }));
    };

    const resolveAppType = (detail) => {
      const raw = typeof detail?.appType === 'string' ? detail.appType.trim() : '';
      if (!raw) return 'KB Question & Answer';
      if (raw.toUpperCase() === 'RAG') return 'KB Question & Answer';
      return raw;
    };

    const handleStreamQuery = async (detail, runId) => {
      const {
        text,
        cat,
        source,
        selectedDocIdsForBookmark,
        appType,
        kbName,
        kbId,
        docId,
      } = buildQaSubmitContext(detail, resolveAppType);
      if (!text) return;
      if (!isActiveRun(runId)) return;
      const historyRaw = Array.isArray(detail?.history) ? detail.history : [];
      const history = historyRaw
        .map((it) => ({
          question: String(it?.question || '').trim(),
          answer: String(it?.answer || '').trim(),
        }))
        .filter((it) => it.question && it.answer)
        .slice(-2);

      let sid = (await ensureSession(text, appType, detail?.sessionId ?? null)) || null;
      if (!sid) throw new Error('Failed to create session.');

      let userMsgId = null;
      try {
        const userMsg = await postMessage(sid, 'user', text, {
          engine: 'rag',
          kb_id: kbId || undefined,
          kb_name: kbName || undefined,
          selected_doc_ids: selectedDocIdsForBookmark.length
            ? selectedDocIdsForBookmark
            : undefined,
          doc_id: docId ?? undefined,
          source: source || undefined,
          cat: cat || undefined,
        });
        userMsgId = userMsg?.id ?? userMsg?.message_id ?? null;
      } catch (err) {
        if (isForbidden(err)) {
          setSessionId(null);
          sid = (await ensureSession(text, appType, null)) || null;
          if (!sid) throw new Error('Failed to recreate session.');
          const userMsg = await postMessage(sid, 'user', text, {
            engine: 'rag',
            kb_id: kbId || undefined,
            kb_name: kbName || undefined,
            selected_doc_ids: selectedDocIdsForBookmark.length
              ? selectedDocIdsForBookmark
              : undefined,
            doc_id: docId ?? undefined,
            source: source || undefined,
            cat: cat || undefined,
          });
          userMsgId = userMsg?.id ?? userMsg?.message_id ?? null;
        } else {
          throw err;
        }
      }

      ticker.open('Question Analysis', {
        stage: 'analyzing',
        progress: 1,
        hint: STAGE_TO_TICKER.analyzing.hint,
      });

      const token = getAccessToken();
      const reqBody = { key: text, session_id: sid };
      if (appType === 'General QA') {
        reqBody.mode = 'general';
      }
      if (history.length) reqBody.history = history;
      if (source === 'bookmark' && selectedDocIdsForBookmark.length) {
        reqBody.doc_id = selectedDocIdsForBookmark.map((v) => String(v)).join(',');
        reqBody.source = 'bookmark';
      } else if (source === 'activeDoc') {
        if (docId != null) reqBody.doc_id = docId;
      } else if (cat) {
        reqBody.cat = cat;
      }

      let collected = '';
      let meta = { images: [], sources: [], thumb_b64: null, thumb_mime: 'image/jpeg' };

      streamState = {
        runId,
        sid,
        userMsgId,
        collected: '',
        meta: { ...meta },
        appType,
        kbId,
        kbName,
        selectedDocIdsForBookmark,
        source,
        cat,
      };

      let doneHandled = false;

      const onMeta = (raw) => {
        if (!isActiveRun(runId)) return;
        try {
          const m = JSON.parse(raw || '{}');
          const next = { ...meta };
          if (Array.isArray(m.images)) next.images = m.images;
          if (Array.isArray(m.sources)) next.sources = m.sources;
          if (Array.isArray(m.docs)) next.sources = m.docs;
          if (m.thumb_b64 != null) next.thumb_b64 = m.thumb_b64;
          if (m.thumb_mime) next.thumb_mime = m.thumb_mime;
          meta = next;
          if (streamState && streamState.runId === runId) {
            streamState.meta = { ...next };
          }
        } catch {}
      };

      const onStep = (raw) => {
        if (!isActiveRun(runId)) return;
        try {
          const d = JSON.parse(raw || '{}');
          const hasStage = typeof d?.stage === 'string' && d.stage.trim();
          const hasMessage = typeof d?.message === 'string' && d.message.trim();
          if (hasStage || hasMessage) return;
          const meta = parseTickerPayload(d);
          if (meta) ticker.step(meta.step, meta);
        } catch {}
      };

      const onStatus = (raw) => {
        if (!isActiveRun(runId)) return;
        try {
          const d = JSON.parse(raw || '{}');
          const meta = parseTickerPayload(d);
          if (meta) ticker.step(meta.step, meta);
        } catch {}
      };

      const onChunk = (raw) => {
        if (!isActiveRun(runId)) return;
        try {
          const d = JSON.parse(raw || '{}');
          let t = typeof d.text === 'string' ? d.text : String(d.text ?? '');
          if (!t) return;
          if (/^(?:#{1,6}\s|[-*+]\s+|\d+\.\s+|\|.*\|$|!\[.*\]\(.*\))/.test(t)) {
            t = '\n\n' + t;
          }
          collected += t;
          if (streamState && streamState.runId === runId) {
            streamState.collected = collected;
          }
          sendAssistantToSidebar({ sessionId: sid, content: t, runId });
        } catch {}
      };

      const onDone = async (raw) => {
        if (doneHandled) return;
        doneHandled = true;
        if (!isActiveRun(runId)) return;
        ticker.close();

        try {
          const donePayload = JSON.parse(raw || '{}');

          const finSourcesRaw = donePayload.sources ?? donePayload.docs ?? meta.sources ?? [];
          const finSources = Array.isArray(finSourcesRaw) ? finSourcesRaw : [];
          const finImagesRaw = donePayload.images ?? donePayload.image_urls ?? meta.images ?? [];
          const thumb_b64 = donePayload.thumb_b64 || meta.thumb_b64 || null;
          const thumb_mime = donePayload.thumb_mime || meta.thumb_mime || 'image/jpeg';

          const pickUrl = (x) => (typeof x === 'string' ? x : (x?.url || x?.href || x?.path || ''));
          const abs = (u) =>
            u && !/^https?:\/\//i.test(u) && !/^data:/i.test(u)
              ? `${API_BASE}${u.startsWith('/') ? u : `/${u}`}`
              : u;
          let normalizedImageUrls = (Array.isArray(finImagesRaw) ? finImagesRaw : [])
            .map(pickUrl)
            .filter(Boolean)
            .map(abs);

          if (!normalizedImageUrls.length && finSources.length) {
            normalizedImageUrls = collectImagesFromSources(finSources);
          }

          const enrichedSources = enrichSources(finSources);

          if (!isActiveRun(runId)) return;
          sendAssistantToSidebar({
            sessionId: sid,
            content: collected || '',
            final: true,
            sources: enrichedSources,
            images: normalizedImageUrls,
            thumb_b64,
            thumb_mime,
            kb_name: kbName || undefined,
            selected_doc_ids: selectedDocIdsForBookmark.length
              ? selectedDocIdsForBookmark
              : undefined,
            runId,
          });

          try {
            if (!isActiveRun(runId)) return;
            const assistantMsg = await postMessage(sid, 'assistant', collected, {
              sources: finSources,
              images: normalizedImageUrls,
              engine: 'rag',
              stream: true,
              kb_id: kbId || undefined,
              kb_name: kbName || undefined,
              selected_doc_ids: selectedDocIdsForBookmark.length
                ? selectedDocIdsForBookmark
                : undefined,
              source: source || undefined,
              cat: cat || undefined,
            });
            const assistantMsgId = assistantMsg?.id ?? assistantMsg?.message_id ?? null;
            if (assistantMsgId != null) setLastAssistantMessageId(assistantMsgId);

            window.dispatchEvent(
              new CustomEvent('qa:history:refresh', {
                detail: { sessionId: sid, lastMessageId: assistantMsgId, userMessageId: userMsgId },
              }),
            );
          } catch {
            if (isActiveRun(runId)) {
              sendAssistantToSidebar({
                sessionId: sid,
                content: '[An error occurred while saving the history, but streaming was completed.]',
                runId,
              });
            }
          }
        } catch {}

        if (streamState && streamState.runId === runId) {
          streamState = null;
        }
      };

      const onSseEvent = async (name, data) => {
        if (name === 'meta') onMeta(data);
        else if (name === 'status') onStatus(data);
        else if (name === 'step') onStep(data);
        else if (name === 'chunk') onChunk(data);
        else if (name === 'done') await onDone(data);
      };

      try {
        cleanupStream();
        const controller = new AbortController();
        streamAbortController = controller;
        await streamQaEvents({
          path: `${API_BASE}/qa/stream`,
          token,
          payload: reqBody,
          signal: controller.signal,
          onEvent: onSseEvent,
        });

        if (!doneHandled && isActiveRun(runId)) {
          await onDone('{}');
        }
      } catch {
        if (!isActiveRun(runId)) return;
        ticker.error();
        sendAssistantToSidebar({ sessionId: sid, content: '[Straming Err.]', runId });
        if (streamState && streamState.runId === runId) {
          streamState = null;
        }
      } finally {
        if (streamAbortController) {
          streamAbortController = null;
        }
      }
    };

    const handleLegacyQuery = async (d, runId) => {
      const {
        text,
        cat,
        selectedDocIdsForBookmark,
        appType,
        kbName,
        kbId,
        docId,
      } = buildQaSubmitContext(d, resolveAppType);
      if (!text) return;
      if (!isActiveRun(runId)) return;

      const preferredSid = d.sessionId ?? null;
      let sid = (await ensureSession(text, appType, preferredSid)) || null;
      if (!sid) throw new Error('Failed to create session.');

      let userMsg;
      try {
        userMsg = await postMessage(sid, 'user', text, {
          engine: 'rag',
          kb_id: kbId || undefined,
          kb_name: kbName || undefined,
          selected_doc_ids: selectedDocIdsForBookmark.length
            ? selectedDocIdsForBookmark
            : undefined,
          doc_id: docId ?? undefined,
          cat: cat || undefined,
        });
      } catch (err) {
        if (isForbidden(err)) {
          setSessionId(null);
          sid = (await ensureSession(text, appType, null)) || null;
          if (!sid) throw new Error('Failed to recreate session.');
          userMsg = await postMessage(sid, 'user', text, {
            engine: 'rag',
            kb_id: kbId || undefined,
            kb_name: kbName || undefined,
            selected_doc_ids: selectedDocIdsForBookmark.length
              ? selectedDocIdsForBookmark
              : undefined,
            doc_id: docId ?? undefined,
            cat: cat || undefined,
          });
        } else {
          throw err;
        }
      }
      const userMsgId = userMsg?.id ?? userMsg?.message_id ?? null;

      let answer = '';
      let sources = [];
      let thumb_b64 = null;
      let thumb_mime = 'image/jpeg';
      let images = [];
      const res = await ragQuery(text, 5, {
        kb_id: kbId || undefined,
        doc_id: docId ?? undefined,
        session_id: sid,
      });
      answer = res?.answer || '';
      sources = Array.isArray(res?.docs) ? res.docs : [];
      thumb_b64 = res?.thumb_b64 || null;
      thumb_mime = res?.thumb_mime || thumb_mime;
      images = Array.isArray(res?.images)
        ? res.images
        : Array.isArray(res?.image_urls)
          ? res.image_urls
          : [];

      let imageUrls = (images || [])
        .map((x) => (typeof x === 'string' ? x : x?.url || x?.href || x?.path || ''))
        .filter(Boolean)
        .map(toAbsUrl);

      if (!imageUrls.length && sources.length) {
        imageUrls = collectImagesFromSources(sources);
      }

      const enrichedSources = enrichSources(sources);

      if (!isActiveRun(runId)) return;
      sendAssistantToSidebar({
        sessionId: sid,
        content: answer || '',
        final: true,
        sources: enrichedSources,
        images: imageUrls,
        thumb_b64,
        thumb_mime,
        kb_name: kbName || undefined,
        selected_doc_ids: selectedDocIdsForBookmark.length
          ? selectedDocIdsForBookmark
          : undefined,
        runId,
      });

      if (!isActiveRun(runId)) return;
      const assistantMsg = await postMessage(sid, 'assistant', answer, {
        sources,
        images: imageUrls,
        engine: 'rag',
        cat: cat || undefined,
        kb_id: kbId || undefined,
        kb_name: kbName || undefined,
        selected_doc_ids: selectedDocIdsForBookmark.length
          ? selectedDocIdsForBookmark
          : undefined,
      });
      const assistantMsgId = assistantMsg?.id ?? assistantMsg?.message_id ?? null;

      if (assistantMsgId != null) setLastAssistantMessageId(assistantMsgId);

      window.dispatchEvent(
        new CustomEvent('qa:history:refresh', {
          detail: { sessionId: sid, lastMessageId: assistantMsgId, userMessageId: userMsgId },
        }),
      );
    };

    const onSubmit = async (e) => {
      const d = e?.detail || {};
      const runId =
        d.runId || `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      try {
        const text = (d.text || '').trim();
        if (!text) return;

        stopped = false;
        currentRunId = runId;

        const useStream = d?.useStream === true || String(d?.engine || '') === 'stream';

        if (useStream) await handleStreamQuery(d, runId);
        else await handleLegacyQuery(d, runId);
      } catch (err) {
        const msg = err?.message || String(err);
        if (isActiveRun(runId)) {
          sendAssistantToSidebar({ content: `[Q&A Error] ${msg}`, runId });
        }
        cleanupStream();
        ticker.error();
      }
    };

    const onNew = async () => {
      setSessionId(null);
      setLastAssistantMessageId(null);
      stopped = true;
      currentRunId = null;
      sendAssistantToSidebar({ content: 'A new chat has started.' });
      ticker.reset();
      window.dispatchEvent(new CustomEvent('qa:history:refresh', { detail: {} }));
      cleanupStream();
    };

    const onFeedback = async (e) => {
      try {
        const d = e?.detail || {};
        if (!d.messageId || ![1, -1].includes(d.score)) return;

        const feedback_type = d.score > 0 ? 'like' : 'unlike';
        await saveFeedback({
          chat_message_id: d.messageId,
          feedback_type,
          comment: d.comment || '',
        });

        window.dispatchEvent(
          new CustomEvent('qa:feedback-status', {
            detail: { ok: true, messageId: d.messageId, score: d.score, comment: d.comment || '' },
          }),
        );

        window.dispatchEvent(
          new CustomEvent('qa:feedback-status-ui', {
            detail: { ok: true, step: 'Your feedback has been saved.' },
          }),
        );
      } catch {
        window.dispatchEvent(
          new CustomEvent('qa:feedback-status', {
            detail: {
              ok: false,
              messageId: e?.detail?.messageId,
              score: e?.detail?.score,
              comment: e?.detail?.comment || '',
            },
          }),
        );
        window.dispatchEvent(
          new CustomEvent('qa:feedback-status-ui', {
            detail: { ok: false, step: 'Failed to save feedback' },
          }),
        );
      }
    };

    const onStop = async () => {
      const state = streamState && streamState.runId === currentRunId ? { ...streamState } : null;
      stopped = true;
      currentRunId = null;
      cleanupStream();
      ticker.close();

      if (state && state.sid && state.collected && state.collected.trim()) {
        try {
          const sources = Array.isArray(state.meta?.sources) ? state.meta.sources : [];
          const imagesRaw = Array.isArray(state.meta?.images) ? state.meta.images : [];
          let imageUrls = imagesRaw
            .map((x) => (typeof x === 'string' ? x : x?.url || x?.href || x?.path || ''))
            .filter(Boolean)
            .map(toAbsUrl);

          if (!imageUrls.length && sources.length) {
            imageUrls = collectImagesFromSources(sources);
          }

          const assistantMsg = await postMessage(state.sid, 'assistant', state.collected, {
            sources,
            images: imageUrls,
            engine: 'rag',
            stream: true,
            partial: true,
            cancelled: true,
            kb_id: state.kbId || undefined,
            kb_name: state.kbName || undefined,
            selected_doc_ids: state.selectedDocIdsForBookmark?.length
              ? state.selectedDocIdsForBookmark
              : undefined,
            source: state.source || undefined,
            cat: state.cat || undefined,
          });
          const assistantMsgId = assistantMsg?.id ?? assistantMsg?.message_id ?? null;
          if (assistantMsgId != null) setLastAssistantMessageId(assistantMsgId);

          window.dispatchEvent(
            new CustomEvent('qa:history:refresh', {
              detail: { sessionId: state.sid, lastMessageId: assistantMsgId, userMessageId: state.userMsgId },
            }),
          );
        } catch {}
      }

      streamState = null;
    };

    window.addEventListener('qa:submit', onSubmit);
    window.addEventListener('qa:new', onNew);
    window.addEventListener('qa:feedback', onFeedback);
    window.addEventListener('ai:select', onAppChange);
    window.addEventListener('qa:stop', onStop);

    return () => {
      cleanupStream();
      window.removeEventListener('qa:submit', onSubmit);
      window.removeEventListener('qa:new', onNew);
      window.removeEventListener('qa:feedback', onFeedback);
      window.removeEventListener('ai:select', onAppChange);
      window.removeEventListener('qa:stop', onStop);
    };
  }, []);

  return null;
}
