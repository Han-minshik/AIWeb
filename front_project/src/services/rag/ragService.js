import { http } from '../http/client';

export async function ragQuery(question, top_k = 5, extra = {}) {
  const payload = { question, top_k, ...extra };
  try {
    const res = await http.post('/rag/query', payload);
    return normalize(res);
  } catch {
    const res = await http.post('/qa/ask', payload);
    return normalize(res);
  }
}

function normalize(res) {
  if (!res) return { answer: '', docs: [] };
  const answer = res.answer ?? res.content ?? res.text ?? '';
  const docs = Array.isArray(res.docs) ? res.docs
             : Array.isArray(res.sources) ? res.sources
             : Array.isArray(res.references) ? res.references
             : [];
  return { answer, docs };
}
