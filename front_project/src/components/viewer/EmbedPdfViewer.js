import React, { useEffect, useMemo, useState } from 'react';
import { getSynapJob } from '../../services/docs/docService';
import useDownload from '../../hooks/useDownload';
import '../../styles/components/embed-pdf-viewer.css';

function stripKnownExtension(name, fileRight) {
  const full = String(name || '').trim();
  const ext = String(fileRight || '').trim().toLowerCase();
  if (!full || !ext) return full;
  const suffix = `.${ext}`;
  return full.toLowerCase().endsWith(suffix) ? full.slice(0, -suffix.length) : full;
}

function normalizeStoredPath(raw) {
  const p = String(raw || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\/+|\/+$/g, '');
  if (!p) return null;
  const lower = p.toLowerCase();
  const prefixes = ['uploads-general/', 'uploads-kiwix/', 'uploads/'];
  for (const prefix of prefixes) {
    if (lower.startsWith(prefix)) return p.slice(prefix.length) || null;
  }
  return p;
}

function normalizeTheme(raw) {
  const s = String(raw || '').trim().toLowerCase();
  return s === 'dark' ? 'dark' : 'light';
}

function readUiTheme() {
  try {
    const attr = document?.documentElement?.getAttribute('data-theme');
    if (attr) return normalizeTheme(attr);
  } catch {}
  try {
    const saved = localStorage.getItem('ui_theme');
    if (saved) return normalizeTheme(saved);
  } catch {}
  return 'light';
}

export default function EmbedPdfViewer({
  docId,
  objectId,
  aiDocumentId,
  docTitle,
  docRelPath,
  docSubPath,
  docFileRight,
  docStoredPath,
  docBranchType,
  initialPage,
}) {
  const [viewerUrl, setViewerUrl] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const [uiTheme, setUiTheme] = useState(() => readUiTheme());
  const { downloadLatest } = useDownload();
  const downloadId = docId ?? objectId ?? null;

  const handleDownload = async () => {
    if (downloadId == null || downloading) return;
    setDownloading(true);
    setDownloadError('');
    try {
      await downloadLatest(downloadId);
    } catch (downloadFailure) {
      setDownloadError(downloadFailure?.message || 'Download failed.');
    } finally {
      setDownloading(false);
    }
  };

  const normalizedPage = useMemo(() => {
    const n = Number(initialPage);
    if (!Number.isFinite(n)) return null;
    return Math.max(1, Math.trunc(n));
  }, [initialPage]);

  useEffect(() => {
    const onThemeChange = (e) => {
      const next = normalizeTheme(e?.detail?.theme || readUiTheme());
      setUiTheme((prev) => (prev === next ? prev : next));
    };
    window.addEventListener('ui:theme-change', onThemeChange);
    return () => window.removeEventListener('ui:theme-change', onThemeChange);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setViewerUrl(null);
    setError(null);

    const run = async () => {
      setLoading(true);
      try {
        const title0 = String(docTitle || '').trim();
        const nameForLookup = title0
          ? title0
          : objectId != null
          ? `object-${objectId}`
          : docId != null
          ? `doc-${docId}`
          : '';
        const baseName = stripKnownExtension(nameForLookup, docFileRight);
        const lookupName = nameForLookup || baseName || null;
        const stored = normalizeStoredPath(docStoredPath);
        const result = await getSynapJob({
          doc_id: docId ?? null,
          object_id: objectId ?? null,
          ai_document_id: aiDocumentId ?? null,
          name: lookupName,
          stored_path: stored,
          relative_path: docRelPath ?? null,
          sub_path: docSubPath ?? null,
          file_right: docFileRight ?? null,
          branch_type: docBranchType ?? null,
          page: normalizedPage ?? null,
          theme: uiTheme,
        });
        if (cancelled) return;
        if (!result?.job_url) {
          setError('Failed to build Synap viewer URL.');
          return;
        }
        setViewerUrl(result.job_url);
      } catch {
        if (!cancelled) setError('Failed to load document metadata.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    if (docId != null || objectId != null || aiDocumentId || docTitle || docStoredPath) run();

    return () => {
      cancelled = true;
    };
  }, [
    docId,
    objectId,
    aiDocumentId,
    docTitle,
    docRelPath,
    docSubPath,
    docFileRight,
    docStoredPath,
    docBranchType,
    normalizedPage,
    uiTheme,
  ]);

  if (docId == null && objectId == null && !aiDocumentId && !docTitle && !docStoredPath) {
    return (
      <div className="viewer embedpdf-viewer">
        <div className="fallback">No file selected.</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="viewer embedpdf-viewer synap-viewer-shell">
        <div className="document-viewer-toolbar">
          <span className="document-viewer-title" title={String(docTitle || '')}>{docTitle || 'Document'}</span>
          <button className="btn document-download-btn" type="button" onClick={handleDownload} disabled={downloadId == null || downloading}>
            {downloading ? 'Downloading...' : 'Download original'}
          </button>
        </div>
        {downloadError && <div className="document-download-error">{downloadError}</div>}
        <div className="synap-viewer-content">
          <div className="fallback">{error}</div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="viewer embedpdf-viewer synap-viewer-shell"
    >
      <div className="document-viewer-toolbar">
        <span className="document-viewer-title" title={String(docTitle || '')}>{docTitle || 'Document'}</span>
        <button className="btn document-download-btn" type="button" onClick={handleDownload} disabled={downloadId == null || downloading}>
          {downloading ? 'Downloading...' : 'Download original'}
        </button>
      </div>
      {downloadError && <div className="document-download-error">{downloadError}</div>}
      <div className="synap-viewer-content">
        {(loading || !viewerUrl) && <div className="loading">Document Loading...</div>}
        {viewerUrl ? (
          <iframe
            title={String(docTitle || 'Synap Document Viewer')}
            src={viewerUrl}
            allowFullScreen
          />
        ) : null}
      </div>
    </div>
  );
}
