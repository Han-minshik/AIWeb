import React, { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { getDocMetaByObjectId, loadMarkdownByRoutes } from '../../services/docs/docService';
import '../../styles/components/markdown-doc-viewer.css';

const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8001';

function toAbsApiUrl(path) {
  const origin = window.location.origin;
  const base = new URL(API_BASE, origin);
  const p = String(path || '').startsWith('/') ? String(path || '') : `/${String(path || '')}`;
  const url = new URL(p, base);
  const sameHostAsApp = url.host === new URL(origin).host;
  const isStaticUploadPath =
    url.pathname.startsWith('/uploads/') ||
    url.pathname.startsWith('/uploads-general/') ||
    url.pathname.startsWith('/uploads-kiwix/');

  if (sameHostAsApp && !url.pathname.startsWith('/api/') && !isStaticUploadPath) {
    url.pathname = `/api${url.pathname}`;
  }
  return url.toString();
}

function normalizePath(path) {
  return String(path || '')
    .replace(/\\/g, '/')
    .replace(/\/{2,}/g, '/')
    .replace(/^\/+|\/+$/g, '');
}

function normalizeExt(v) {
  return String(v || '')
    .trim()
    .replace(/^\./, '')
    .toLowerCase();
}

function extractTrailingExt(name) {
  const raw = String(name || '').trim();
  const m = raw.match(/\.([A-Za-z0-9]+)$/);
  return m ? normalizeExt(m[1]) : '';
}

function isLikelyFileExt(ext) {
  const s = normalizeExt(ext);
  if (!s) return false;
  if (!/^[a-z0-9]{1,10}$/.test(s)) return false;
  // Numeric-only tails like ".2" are usually version tokens, not file extensions.
  return /[a-z]/.test(s);
}

function fileNameFromStoredPath(raw) {
  const path = String(raw || '')
    .trim()
    .replace(/\\/g, '/');
  if (!path) return '';
  const segs = path.split('/').filter(Boolean);
  return segs.length ? segs[segs.length - 1] : '';
}

function resolveRelativePath(baseDir, rel) {
  const stack = baseDir ? baseDir.split('/').filter(Boolean) : [];
  const parts = String(rel || '').replace(/\\/g, '/').split('/');
  for (const p of parts) {
    if (!p || p === '.') continue;
    if (p === '..') stack.pop();
    else stack.push(p);
  }
  return stack.join('/');
}

function encodePath(path) {
  return normalizePath(path)
    .split('/')
    .map((s) => {
      const seg = String(s || '');
      if (!seg) return '';
      try {
        return encodeURIComponent(decodeURIComponent(seg));
      } catch {
        return encodeURIComponent(seg);
      }
    })
    .join('/');
}

function normalizeImgSrc(src) {
  const raw = String(src || '').trim();
  if (!raw) return '';
  if (raw.startsWith('<') && raw.endsWith('>')) {
    return raw.slice(1, -1).trim();
  }
  return raw;
}

function parseLooseMarkdownImage(text) {
  const t = String(text || '').trim();
  if (!t.startsWith('![') || !t.endsWith(')')) return null;
  const altEnd = t.indexOf(']');
  if (altEnd < 2) return null;
  if (t[altEnd + 1] !== '(') return null;

  const alt = t.slice(2, altEnd).trim();
  const inner = normalizeImgSrc(t.slice(altEnd + 2, -1));
  if (!inner) return null;
  return { alt, src: inner };
}

export default function MarkdownDocViewer({
  docId,
  objectId,
  docTitle,
  docRelPath,
  docSubPath,
  docFileRight,
  docBranchType,
}) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [baseUploadDir, setBaseUploadDir] = useState('');
  const [resolvedBaseRoute, setResolvedBaseRoute] = useState('/uploads-general');

  const ext = useMemo(() => {
    const byRight = String(docFileRight || '').trim().toLowerCase();
    if (byRight) return byRight;
    const t = String(docTitle || '').trim().toLowerCase();
    const idx = t.lastIndexOf('.');
    return idx >= 0 ? t.slice(idx + 1) : '';
  }, [docTitle, docFileRight]);

  const branchTypeNorm = useMemo(
    () => String(docBranchType || '').trim().toUpperCase(),
    [docBranchType]
  );
  const preferredBaseRoute = useMemo(() => {
    if (branchTypeNorm === 'KIWIX') return '/uploads-kiwix';
    if (branchTypeNorm === 'GENERAL') return '/uploads-general';
    return '/uploads-kiwix';
  }, [branchTypeNorm]);
  const candidateBaseRoutes = useMemo(() => {
    const out = [preferredBaseRoute];
    if (!out.includes('/uploads-kiwix')) out.push('/uploads-kiwix');
    if (!out.includes('/uploads-general')) out.push('/uploads-general');
    return out;
  }, [preferredBaseRoute]);

  useEffect(() => {
    let cancelled = false;
    setContent('');
    setError(null);
    setBaseUploadDir('');
    setResolvedBaseRoute(preferredBaseRoute);

    if (ext !== 'md') {
      setError(`This file format (${ext || 'unknown'}) is not supported by this viewer.`);
      return;
    }

    (async () => {
      setLoading(true);
      try {
        const fileNameRaw = String(docTitle || '').trim() || 'document';
        const trailingExt = extractTrailingExt(fileNameRaw);
        const hasUsableTrailingExt = isLikelyFileExt(trailingExt);

        let resolvedRight = normalizeExt(docFileRight);
        if (!resolvedRight && objectId != null) {
          try {
            const meta = await getDocMetaByObjectId(objectId);
            const fileNameFromMeta = fileNameFromStoredPath(meta?.name);
            const extFromMetaName = extractTrailingExt(fileNameFromMeta);
            if (isLikelyFileExt(extFromMetaName)) {
              resolvedRight = extFromMetaName;
            }
          } catch {}
        }

        const fallbackExt = branchTypeNorm === 'KIWIX' ? 'md' : '';
        const expectedExt = resolvedRight || fallbackExt;
        const hasExpectedExtSuffix =
          !!expectedExt && fileNameRaw.toLowerCase().endsWith(`.${expectedExt}`);
        const shouldAppend = !hasUsableTrailingExt && !!expectedExt && !hasExpectedExtSuffix;
        const fileName = shouldAppend ? `${fileNameRaw}.${expectedExt}` : fileNameRaw;
        const rel = normalizePath(docRelPath);
        const sub = normalizePath(docSubPath);
        const dir =  normalizePath([rel, sub].filter(Boolean).join('/'));
        if (!dir || !fileName) {
          if (!cancelled) setError('Failed to resolve markdown file path from DB values.');
          return;
        }

        const encoded = encodePath(`${dir}/${fileName}`);
        const { text, route } = await loadMarkdownByRoutes(
          candidateBaseRoutes,
          encoded,
          `${dir}/${fileName}`
        );
        if (!cancelled) {
          setBaseUploadDir(dir);
          setResolvedBaseRoute(route);
          setContent(text || '');
        }
      } catch (err) {
        if (!cancelled) {
          const attempted = Array.isArray(err?.attemptedPaths) ? err.attemptedPaths : [];
          if (attempted.length) {
            setError(`Failed to load markdown file. Tried: ${attempted.join(' | ')}`);
          } else {
            setError('Failed to load markdown file.');
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [docId, objectId, docTitle, docRelPath, docSubPath, docFileRight, branchTypeNorm, preferredBaseRoute, candidateBaseRoutes, ext]);

  const components = useMemo(
    () => ({
      p: ({ children }) => {
        const textOnly = (Array.isArray(children) ? children : [children])
          .map((c) => (typeof c === 'string' ? c : ''))
          .join('')
          .trim();
        const hit = parseLooseMarkdownImage(textOnly);
        if (!hit) return <p>{children}</p>;

        const raw = normalizeImgSrc(hit.src);
        if (!raw) return <p>{children}</p>;
        const isAbs = /^https?:\/\//i.test(raw) || raw.startsWith('data:');
        const resolved = resolveRelativePath(baseUploadDir, raw);
        const finalSrc = isAbs ? raw : toAbsApiUrl(`${resolvedBaseRoute}/${encodePath(resolved)}`);
        return (
          <p>
            <img src={finalSrc} alt={hit.alt || ''} loading="lazy" />
          </p>
        );
      },
      img: ({ src, alt }) => {
        const raw = normalizeImgSrc(src);
        if (!raw) return null;
        const isAbs = /^https?:\/\//i.test(raw) || raw.startsWith('data:');
        const resolved = resolveRelativePath(baseUploadDir, raw);
        const finalSrc = isAbs ? raw : toAbsApiUrl(`${resolvedBaseRoute}/${encodePath(resolved)}`);
        return <img src={finalSrc} alt={alt || ''} loading="lazy" />;
      },
    }),
    [baseUploadDir, resolvedBaseRoute]
  );

  if (!docId && !objectId && !docTitle) {
    return (
      <div className="viewer embedpdf-viewer">
        <div className="fallback">No file selected.</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="viewer embedpdf-viewer">
        <div className="fallback">{error}</div>
      </div>
    );
  }

  return (
    <div className="viewer embedpdf-viewer markdown-doc-viewer">
      {loading ? (
        <div className="loading">MD Loading...</div>
      ) : (
        <div className="markdown-doc-body">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
            {content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}
