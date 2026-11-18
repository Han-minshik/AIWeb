import { normalizeStreamText } from '../../utils/textSanitizers';

const badgeReplace = (s = '') => {
  let t = String(s ?? '');
  if (!t) return t;

  const already = /<sup[^>]*class=["']?ref-badge["']?[^>]*>/i;
  if (already.test(t)) return t;

  t = t
    .replace(
      /\s*\(?\s*\[\s*([1-9]\d{0,2})(?:\s*[:\-][^\]]*)?\]\s*\)?/g,
      (_m, n) => `<sup class="ref-badge">${n}</sup>`
    )
    .replace(/\[\s*([1-9]\d{0,2})\s*\]/g, (_m, n) => `<sup class="ref-badge">${n}</sup>`);

  return t;
};

export const mapKBToCat = (name = '') => String(name || '').trim();

export const AGENT_EXAMPLE_QUESTIONS = {
  'KB Question & Answer': [
    {
      title: 'SE Standards & Guide explorer',
      question: 'Please explain the difference in the purpose of performing FCA and PCA',
    },
    {
      title: 'MOSAR Project Insight',
      question: 'What are the HOTDOCK risk scenarios?',
    },
  ],
  'General QA': [
    {
      title: 'Write Better Requirements',
      question: 'What makes a good requirement statement? Give 3 concrete examples.',
    },
    {
      title: 'Project Risk Checklist',
      question: 'How should I categorize project risks (tech, schedule, people, quality)? Provide a practical checklist.',
    },
    {
      title: 'Bug Report Template',
      question: 'Create a bug report template with the essential fields.',
    },
  ],
};

export const KB_QA_FORCED_KB_BY_EXAMPLE_INDEX = {
  0: '1. Standard',
  1: '5. Example',
};

export const isLikelyUrl = (raw) => {
  const s = String(raw || '').trim();
  if (!s) return false;
  if (/^https?:\/\//i.test(s) || /^data:/i.test(s)) return true;
  if (s.startsWith('/api/docs-embed/') || s.startsWith('/docs-embed/')) return true;
  if (s.startsWith('/api/uploads/') || s.startsWith('/uploads/')) return true;
  return false;
};

export const normalizePathHint = (raw) => {
  const s = String(raw || '').trim();
  if (!s) return null;
  const cleaned = s.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
  return cleaned || null;
};

export const splitPathHint = (raw) => {
  const rawText = String(raw || '').trim();
  const cleaned = normalizePathHint(rawText);
  if (!cleaned) return { relPath: null, subPath: null, fileRight: null, fileName: null, storedPath: null };

  const parts = cleaned.split('/').filter(Boolean);
  if (!parts.length) return { relPath: null, subPath: null, fileRight: null, fileName: null, storedPath: null };

  const isAbsWin = /^[A-Za-z]:\//.test(cleaned);
  const isAbsUnix = rawText.startsWith('/');
  const isAbsolute = isAbsWin || isAbsUnix;

  const last = parts[parts.length - 1] || '';
  const m = last.match(/\.([A-Za-z0-9]+)$/);
  const fileRight = m ? m[1] : null;
  const fileName = m ? last : null;

  const toRelStoredPath = () => {
    if (!fileName) return null;
    if (!isAbsolute) return cleaned;
    const lowerParts = parts.map((p) => String(p || '').toLowerCase());
    const markerIdx = lowerParts.findIndex(
      (p) => p === 'uploads' || p === 'uploads-general' || p === 'uploads-kiwix'
    );
    if (markerIdx >= 0 && markerIdx < parts.length - 1) {
      return parts.slice(markerIdx + 1).join('/');
    }
    return null;
  };

  const storedPath = toRelStoredPath();
  const baseParts = storedPath ? storedPath.split('/').filter(Boolean) : !isAbsolute ? parts : [];
  const folderParts = fileName ? baseParts.slice(0, -1) : baseParts;
  const relPath = folderParts[0] || null;
  const subPath = folderParts.length > 1 ? folderParts.slice(1).join('/') : null;

  return { relPath, subPath, fileRight, fileName, storedPath };
};

export const normalizeExt = (raw) => {
  const s = String(raw || '')
    .trim()
    .replace(/^\./, '')
    .toLowerCase();
  return s || null;
};

export const normalizeBranchType = (raw) => {
  const s = String(raw || '')
    .trim()
    .toUpperCase();
  return s || null;
};

export const inferBranchTypeFromPathLike = (raw) => {
  const s = String(raw || '')
    .trim()
    .replace(/\\/g, '/')
    .toLowerCase();
  if (!s) return null;
  return s.includes('kiwix') ? 'KIWIX' : null;
};

export const extractExtFromPathLike = (raw) => {
  const s = String(raw || '').trim();
  if (!s) return null;

  const pickExt = (value) => {
    const clean = String(value || '')
      .split('#')[0]
      .split('?')[0]
      .replace(/\\/g, '/')
      .replace(/\/+$/g, '');
    if (!clean) return null;
    const last = clean.split('/').filter(Boolean).pop() || '';
    const m = last.match(/\.([A-Za-z0-9]+)$/);
    return m ? normalizeExt(m[1]) : null;
  };

  try {
    if (/^https?:\/\//i.test(s)) {
      const fromUrl = pickExt(new URL(s).pathname);
      if (fromUrl) return fromUrl;
    }
  } catch {}

  return pickExt(s);
};

export const normalizeMdLite = (s = '') => {
  let t = String(s ?? '');
  t = t.replace(/<br\s*\/?>/gi, '\n');
  t = badgeReplace(t);
  return t;
};

export const normalizeMdFinal = (s = '') => {
  let t = normalizeStreamText(String(s ?? ''), { final: true });
  t = t.replace(/<br\s*\/?>/gi, '\n');
  t = badgeReplace(t);
  return t.trim();
};
