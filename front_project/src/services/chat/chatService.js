import { http } from '../http/client';

function normSession(r) {
  if (!r) return null;
  let selected_doc_ids = r.selected_doc_ids ?? r.selectedDocIds ?? null;
  if (typeof selected_doc_ids === 'string') {
    try {
      selected_doc_ids = JSON.parse(selected_doc_ids);
    } catch {
      selected_doc_ids = null;
    }
  }
  if (!Array.isArray(selected_doc_ids)) selected_doc_ids = null;

  return {
    id: r.id ?? r.session_id ?? r.sid,
    title: r.title ?? r.first_question_summary ?? r.name ?? 'Q&A Session',
    app_type: r.app_type ?? r.type ?? 'LLM',
    app_id: r.app_id ?? r.appId ?? null,
    created_at: r.created_at ?? r.ts ?? r.createdAt ?? null,
    updated_at: r.updated_at ?? r.ut ?? r.last_activity_at ?? null,
    kb_id: r.kb_id ?? r.kbId ?? null,
    kb_name: r.kb_name ?? r.kbName ?? null,
    selected_doc_ids,
  };
}

function normMessage(r) {
  if (!r) return null;
  return {
    id: r.id ?? r.message_id ?? r.mid,
    role: r.role?.toLowerCase?.() ?? (r.sender === 'user' ? 'user' : 'assistant'),
    content: r.content ?? r.text ?? '',
    created_at: r.created_at ?? r.ts ?? null,
    meta: r.meta ?? r.metadata ?? { sources: r.sources, images: r.images },
    feedback: r.feedback ?? null,
  };
}

export async function listSessionsRaw(params = {}) {
  return await http.get('/chat/sessions', params);
}

export async function listSessions(pageOrParams = 1, page_size = 20, q = '', app_type) {
  let params;
  if (typeof pageOrParams === 'object' && pageOrParams !== null) {
    params = pageOrParams;
  } else {
    params = { page: pageOrParams, page_size, q };
    if (app_type) params.app_type = app_type;
  }

  const data = await listSessionsRaw(params);
  const rawItems = Array.isArray(data)
    ? data
    : Array.isArray(data?.items)
    ? data.items
    : [];

  return rawItems.map(normSession);
}

export async function getSession(sessionId) {
  const row = await http.get(`/chat/sessions/${sessionId}`);
  return row ? normSession(row) : null;
}

export async function createSession(arg1 = 'Q&A Session', appType = 'LLM') {
  const summary = typeof arg1 === 'string' ? arg1 : (arg1 && arg1.summary) || 'Q&A Session';
  const org_id = (typeof arg1 === 'object' && arg1.org_id) || 1;
  const body = { org_id, summary, app_type: appType };
  const res = await http.post('/chat/sessions', body);
  return normSession({ ...res, title: summary, app_type: appType });
}

export async function addMessage(sessionId, body) {
  return await http.post(`/chat/sessions/${sessionId}/messages`, body);
}

export async function postMessage(sessionId, role, content, metadata = {}) {
  const payload = { role, content, meta: metadata };
  const res = await addMessage(sessionId, payload);
  return normMessage(res);
}

export async function listMessagesRaw(sessionId, params = {}) {
  return await http.get(`/chat/sessions/${sessionId}/messages`, params);
}

export async function listMessages(sessionId, page = 1, page_size = 200) {
  const data = await listMessagesRaw(sessionId, { page, page_size });
  const items = data?.items ?? data ?? [];
  return {
    items: Array.isArray(items) ? items.map(normMessage) : [],
    total: data?.total ?? items?.length ?? 0,
    page: data?.page ?? page,
    page_size: data?.page_size ?? page_size,
  };
}

export async function deleteSession(sessionId) {
  if (sessionId == null) throw new Error('sessionId is required');
  return await http.delete(`/chat/sessions/${sessionId}`);
}

export async function saveFeedback({ chat_message_id, feedback_type, comment }) {
  return http.post('/chat/feedback', {
    chat_message_id,
    feedback_type,
    comment: comment ?? '',
  });
}
