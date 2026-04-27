import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  useEffect,
} from 'react';
import { listSessions, listMessages, deleteSession } from '../services/chat/chatService';
import { searchChatMessages } from '../services/search/searchService';

const HistoryContext = createContext(null);
export function useHistory() {
  return useContext(HistoryContext);
}

function toNumberOrNull(v) {
  if (v === undefined || v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function mapAppInfo(raw) {
  const rawType = raw.app_type || raw.appType || '';
  const appId = toNumberOrNull(raw.app_id ?? raw.appId);
  const normType = typeof rawType === 'string' ? rawType.trim() : '';
  const upper = typeof rawType === 'string' ? rawType.trim().toUpperCase() : '';
  const lower = typeof normType === 'string' ? normType.toLowerCase() : '';
  const agentId = raw.agent_id ?? raw.agentId ?? null;
  const kbName = (raw.kb_name ?? raw.kbName ?? '').toString().trim();

  let display = 'KB Question & Answer';
  if (normType && upper !== 'RAG') {
    display = normType;
  } else if (upper === 'RAG' || !upper) {
    display = 'KB Question & Answer';
  }
  // Canonicalize known agent labels in case backend stores different casing.
  if (lower === 'kb question & answer') display = 'KB Question & Answer';
  if (lower === 'general qa') display = 'General QA';
  if (lower === 'deep research') display = 'Deep Research';
  // General QA sessions are stored with app_id NULL (thus app_type "RAG" from backend list endpoint).
  // We store a marker in selected_kb_name to recover the correct label in history.
  if ((upper === 'RAG' || !upper) && kbName === 'General QA') {
    display = 'General QA';
  }

  return {
    app_type: display,
    raw_app_type: normType || null,
    app_id: appId,
    agent_id: agentId,
  };
}

function normalizeFromSessions(rows) {
  return (rows || []).map((r, idx) => {
    const appInfo = mapAppInfo(r);
    return {
      id: r.session_id ?? r.id ?? idx,
      title: r.title || r.first_question_summary || '(No title)',
      last_message:
        r.last_message ||
        (Array.isArray(r.messages)
          ? r.messages[r.messages.length - 1]?.text
          : ''),
      app_type: appInfo.app_type,
      raw_app_type: appInfo.raw_app_type,
      app_id: appInfo.app_id,
      agent_id: appInfo.agent_id,
      kb_name: r.kb_name || r.kbName || '-',
      selected_doc_ids: r.selected_doc_ids ?? r.selectedDocIds ?? null,
      updated_at: r.last_activity_at || r.updated_at || r.lastAt || '',
      created_at: r.created_at || '',
    };
  });
}

function normalizeFromMessages(items) {
  const bySession = new Map();
  (items || []).forEach((m) => {
    const sid = m.session_id;
    if (!bySession.has(sid)) {
      bySession.set(sid, []);
    }
    bySession.get(sid).push(m);
  });

  const rows = [];
  bySession.forEach((msgs, sid) => {
    msgs.sort((a, b) => {
      const at = new Date(a.created_at).getTime();
      const bt = new Date(b.created_at).getTime();
      if (Number.isNaN(at) || Number.isNaN(bt)) return 0;
      return bt - at;
    });
    const latest = msgs[0];
    const appInfo = mapAppInfo(latest || {});
    rows.push({
      id: sid,
      title: latest?.session_summary || '(No title)',
      last_message: latest?.content || '',
      app_type: appInfo.app_type,
      raw_app_type: appInfo.raw_app_type,
      app_id: appInfo.app_id,
      agent_id: appInfo.agent_id,
      kb_name: '-',
      updated_at: latest?.created_at || '',
      created_at: latest?.created_at || '',
    });
  });

  rows.sort((a, b) => {
    const at = new Date(a.updated_at).getTime();
    const bt = new Date(b.updated_at).getTime();
    if (Number.isNaN(at) || Number.isNaN(bt)) return 0;
    return bt - at;
  });

  return rows;
}

export function HistoryProvider({ children }) {
  const [isOpen, setIsOpen] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listSessions();
      setSessions(normalizeFromSessions(rows));
    } catch (e) {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const open = useCallback(async () => {
    setIsOpen(true);
    await loadSessions();
  }, [loadSessions]);

  const close = useCallback(() => {
    setIsOpen(false);
    setSessions([]);
  }, []);

  const value = useMemo(
    () => ({ isOpen, open, close, sessions, loading }),
    [isOpen, open, close, sessions, loading]
  );

  useEffect(() => {
    const onOpen = () => open();
    window.addEventListener('qa:history:open', onOpen);
    return () => window.removeEventListener('qa:history:open', onOpen);
  }, [open]);

  useEffect(() => {
    const onRefresh = async () => {
      if (!isOpen) return;
      await loadSessions();
    };
    window.addEventListener('qa:history:refresh', onRefresh);
    return () => window.removeEventListener('qa:history:refresh', onRefresh);
  }, [isOpen, loadSessions]);

  useEffect(() => {
    const onSelect = async (e) => {
      const sessionId = e?.detail?.sessionId;
      if (!sessionId) return;

      const row = sessions.find(
        (s) => String(s.id) === String(sessionId)
      );

      const appType =
        row?.raw_app_type ??
        row?.app_type ??
        e?.detail?.appType ??
        null;
      const appId =
        row?.app_id ??
        toNumberOrNull(e?.detail?.appId) ??
        null;
      const agentId =
        row?.agent_id ??
        e?.detail?.agentId ??
        null;
      const selectedDocIds =
        row?.selected_doc_ids ??
        e?.detail?.selectedDocIds ??
        null;
      const kbName =
        row?.kb_name ??
        e?.detail?.kbName ??
        null;

      try {
        const resp = await listMessages(sessionId);
        const msgs = Array.isArray(resp?.items)
          ? resp.items
          : Array.isArray(resp)
          ? resp
          : [];

        window.dispatchEvent(
          new CustomEvent('qa:load-session', {
            detail: {
              sessionId,
              appType,
              appId,
              agentId,
              selectedDocIds,
              kbName,
              messages: msgs,
            },
          })
        );

        window.dispatchEvent(
          new CustomEvent('qa:set-session', { detail: { sessionId } })
        );
      } catch (err) {}
    };

    window.addEventListener('qa:history:select', onSelect);
    return () => window.removeEventListener('qa:history:select', onSelect);
  }, [sessions]);

  useEffect(() => {
    const onDelete = async (e) => {
      const sessionId = e?.detail?.sessionId;
      if (!sessionId) return;

      try {
        await deleteSession(sessionId);
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
        window.dispatchEvent(
          new CustomEvent('qa:set-session', { detail: { sessionId: null } })
        );
      } catch (err) {
        window.dispatchEvent(
          new CustomEvent('qa:assistant-reply', {
            detail: {
              content: 'An error occurred while deleting the session. Please try again later.',
              final: true,
            },
          })
        );
      }
    };

    window.addEventListener('qa:history:delete', onDelete);
    return () => window.removeEventListener('qa:history:delete', onDelete);
  }, []);

  useEffect(() => {
    const onSearch = async (e) => {
      const q = e?.detail?.q ?? '';
      setLoading(true);
      try {
        const data = await searchChatMessages({
          q,
          order: 'desc',
          limit: 50,
          offset: 0,
        });
        const items = Array.isArray(data?.items) ? data.items : (Array.isArray(data) ? data : []);
        setSessions(normalizeFromMessages(items));
      } catch (err) {
        window.dispatchEvent(
          new CustomEvent('qa:assistant-reply', {
            detail: {
              content: 'An error occurred while searching.Please try again later.',
              final: true,
            },
          })
        );
      } finally {
        setLoading(false);
      }
    };

    const onSearchReset = async () => {
      await loadSessions();
    };

    window.addEventListener('qa:history:search', onSearch);
    window.addEventListener('qa:history:search-reset', onSearchReset);
    return () => {
      window.removeEventListener('qa:history:search', onSearch);
      window.removeEventListener('qa:history:search-reset', onSearchReset);
    };
  }, [loadSessions]);

  return (
    <HistoryContext.Provider value={value}>
      {children}
    </HistoryContext.Provider>
  );
}
