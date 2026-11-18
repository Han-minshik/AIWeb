const SKEY = 'qa.current_session_id';
const LKEY = 'qa.last_assistant_message_id';
const TOKEN_KEY = 'access_token';

export const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export function getAccessToken() {
  try {
    return typeof window !== 'undefined' ? localStorage.getItem(TOKEN_KEY) : null;
  } catch {
    return null;
  }
}

export function getSessionId() {
  const v = sessionStorage.getItem(SKEY);
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function setSessionId(id) {
  if (id == null) sessionStorage.removeItem(SKEY);
  else sessionStorage.setItem(SKEY, String(id));
}

export function setLastAssistantMessageId(id) {
  if (id == null) sessionStorage.removeItem(LKEY);
  else sessionStorage.setItem(LKEY, String(id));
  window.dispatchEvent(
    new CustomEvent('qa:last-message', {
      detail: { messageId: id, sessionId: getSessionId() },
    }),
  );
}

export function isForbidden(err) {
  const s = err?.status ?? err?.response?.status;
  if (s === 403) return true;
  const msg = (err?.detail || err?.message || '').toString();
  return /403|forbidden|permission\s*denied|\uAD8C\uD55C\s*\uC5C6|\uC811\uADFC\s*\uAD8C\uD55C/i.test(msg);
}

export function unwrapSessions(res) {
  if (Array.isArray(res)) return res;
  if (res && Array.isArray(res.items)) return res.items;
  return [];
}

export function toAbsUrl(u) {
  if (!u) return '';
  const value = String(u).trim();
  if (/^(?:https?:\/\/|data:|blob:)/i.test(value)) return value;

  const base = String(API_BASE || '').trim().replace(/\/+$/, '');
  const path = value.startsWith('/') ? value : `/${value}`;
  if (!base) return path;

  if (/^https?:\/\//i.test(base)) {
    const parsedBase = new URL(base);
    const basePath = parsedBase.pathname.replace(/\/+$/, '');
    if (basePath && (path === basePath || path.startsWith(`${basePath}/`))) {
      return `${parsedBase.origin}${path}`;
    }
    return `${base}${path}`;
  }

  const basePath = base.startsWith('/') ? base : `/${base}`;
  if (path === basePath || path.startsWith(`${basePath}/`)) return path;
  return `${base}${path}`;
}

export function toDataUri(raw, mime = 'image/jpeg') {
  if (!raw) return null;
  let v = String(raw).trim();
  if (v.startsWith('data:')) return v.replace(/\s+/g, '');
  v = v.replace(/[\r\n\s]+/g, '').replace(/-/g, '+').replace(/_/g, '/');
  const pad = v.length % 4;
  if (pad === 2) v += '==';
  else if (pad === 3) v += '=';
  return `data:${mime};base64,${v}`;
}

function pickUrlLike(obj) {
  if (!obj) return '';
  if (typeof obj === 'string') return obj.trim();
  if (typeof obj === 'object') {
    const cands = [
      obj.url,
      obj.href,
      obj.path,
      obj.relpath && (obj.relpath.url || obj.relpath.path),
    ];
    for (const v of cands) {
      if (typeof v === 'string' && v.trim()) return v.trim();
    }
  }
  return '';
}

export function collectImagesFromSources(sources) {
  const out = [];
  const seen = new Set();
  const pushIfNew = (val) => {
    const key = String(val || '');
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(val);
  };

  for (const s of sources || []) {
    if (Array.isArray(s?.images)) {
      for (const it of s.images) {
        const u = pickUrlLike(it);
        if (u) pushIfNew(toAbsUrl(u));
      }
    }
    if (Array.isArray(s?.image_refs)) {
      for (const r of s.image_refs) {
        const u = pickUrlLike(r) || pickUrlLike(r?.relpath);
        if (u) pushIfNew(toAbsUrl(u));
        else if (r?.thumb_b64) {
          const uri = toDataUri(r.thumb_b64, r.thumb_mime || 'image/jpeg');
          if (uri) pushIfNew(uri);
        }
      }
    }
    const chunks = Array.isArray(s?.chunks)
      ? s.chunks
      : typeof s?.chunks === 'object'
        ? [s.chunks]
        : [];
    for (const ch of chunks) {
      if (!ch || typeof ch !== 'object') continue;
      const refs = Array.isArray(ch.image_refs) ? ch.image_refs : [];
      for (const r of refs) {
        const u = pickUrlLike(r) || pickUrlLike(r?.relpath);
        if (u) pushIfNew(toAbsUrl(u));
        else if (r?.thumb_b64) {
          const uri = toDataUri(r.thumb_b64, r.thumb_mime || 'image/jpeg');
          if (uri) pushIfNew(uri);
        }
      }
    }
  }
  return out;
}

export function enrichSources(srcs) {
  const arr = Array.isArray(srcs) ? srcs : [];
  return arr.map((s, i) => {
    const obj = typeof s === 'string' ? { id: s } : (s || {});
    const title =
      obj.title ||
      obj.name ||
      obj.label ||
      obj.filename ||
      obj.file_name ||
      obj.doc_title ||
      `doc_${i + 1}`;
    const object_id =
      obj.object_id || obj.objectId || obj.objectID || obj.id || obj.doc_id || null;
    let href =
      obj.url ||
      obj.href ||
      obj.path ||
      (obj.relpath && (obj.relpath.url || obj.relpath.path)) ||
      '';
    href = href ? toAbsUrl(href) : '';
    const score = typeof obj.score === 'number' ? obj.score : undefined;
    return { ...obj, title, object_id, href, score };
  });
}

export const TICKER_STEPS = ['Question Analysis', 'Searching', 'Thinking', 'Answering'];

export const STAGE_TO_TICKER = {
  analyzing: {
    step: 'Question Analysis',
    progress: 1,
    hint: 'Analyzing your question and search intent.',
  },
  searching: {
    step: 'Searching',
    progress: 2,
    hint: 'Searching related knowledge and documents.',
  },
  summarizing: {
    step: 'Thinking',
    progress: 3,
    hint: 'Summarizing findings and preparing the answer.',
  },
  answering: {
    step: 'Answering',
    progress: 4,
    hint: 'Generating the final response.',
  },
  no_results: {
    step: 'Searching',
    progress: 2,
    hint: 'No matching documents found. Continuing with best effort.',
  },
  done: {
    step: 'Answering',
    progress: 4,
    hint: 'Finalizing the response.',
  },
};

export function normalizeStatusStage(rawStage = '') {
  const stage = String(rawStage || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
  if (!stage) return 'analyzing';
  if (stage.includes('no_result') || stage.includes('noresult') || stage.includes('empty')) {
    return 'no_results';
  }
  if (stage.includes('done') || stage.includes('complete') || stage.includes('finish')) {
    return 'done';
  }
  if (stage.includes('answer') || stage.includes('respond') || stage.includes('final')) {
    return 'answering';
  }
  if (stage.includes('summar') || stage.includes('reason') || stage.includes('think')) {
    return 'summarizing';
  }
  if (stage.includes('search') || stage.includes('retriev') || stage.includes('index')) {
    return 'searching';
  }
  if (stage.includes('analy') || stage.includes('question') || stage.includes('plan')) {
    return 'analyzing';
  }
  return 'analyzing';
}

function toTickerFromStage(rawStage = '', rawMessage = '') {
  const stage = normalizeStatusStage(rawStage);
  const meta = STAGE_TO_TICKER[stage] || STAGE_TO_TICKER.analyzing;
  const hint = String(rawMessage || '').trim() || meta.hint;
  return {
    step: meta.step,
    stage,
    progress: meta.progress,
    hint,
  };
}

function toTickerFromProgress(rawProgress) {
  const n = Number(rawProgress);
  if (!Number.isFinite(n)) return null;
  const progress = Math.max(1, Math.min(TICKER_STEPS.length, Math.round(n)));
  const step = TICKER_STEPS[progress - 1];
  const stage = normalizeStatusStage(step);
  const fallbackHint = (STAGE_TO_TICKER[stage] || STAGE_TO_TICKER.analyzing).hint;
  return { step, stage, progress, hint: fallbackHint };
}

export function parseTickerPayload(rawObj) {
  const src = rawObj && typeof rawObj === 'object' ? rawObj : {};
  const message = String(src.message || src.hint || '').trim();

  if (typeof src.stage === 'string' && src.stage.trim()) {
    return toTickerFromStage(src.stage, message);
  }
  const fromProgress = toTickerFromProgress(src.progress);
  if (fromProgress) {
    if (message) return { ...fromProgress, hint: message };
    return fromProgress;
  }
  return null;
}
