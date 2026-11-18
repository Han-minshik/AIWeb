import React, { useCallback, useMemo } from 'react';
import AnswerActionBar from './AnswerActionBar';

const toInt = (v) => {
  const s = String(v ?? '').trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? Math.trunc(n) : null;
};

const pickFirst = (...vals) => {
  for (const v of vals) {
    if (v == null) continue;
    if (typeof v === 'string' && !v.trim()) continue;
    return v;
  }
  return null;
};

const normalizeExt = (v) => {
  const s = String(v || '')
    .trim()
    .replace(/^\./, '')
    .toLowerCase();
  return s || null;
};

const normalizeBranchType = (v) => {
  const s = String(v || '')
    .trim()
    .toUpperCase();
  return s || null;
};

const normalizePathPart = (v) =>
  String(v || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\/+|\/+$/g, '');

const joinPath = (rel, sub) => {
  const r = normalizePathPart(rel);
  const s = normalizePathPart(sub);
  if (r && s) return `${r}/${s}`;
  return r || s || '';
};

const readPath = (it = {}) => {
  const rel = pickFirst(it.docRelPath, it.doc_rel_path, it.relative_path, it.rel_path) || null;
  const sub = pickFirst(it.docSubPath, it.doc_sub_path, it.sub_path) || null;
  return { rel, sub };
};

const getDocTitle = (it = {}, fallback = 'Document') =>
  pickFirst(it.docTitle, it.doc_title, it.title, fallback) || fallback;

const getObjectId = (it = {}) =>
  toInt(pickFirst(it.objectId, it.object_id, it.storage_object_id, it.storageObjectId));

const getDocId = (it = {}) =>
  toInt(pickFirst(it.docId, it.doc_id, it.doc_master_id, it.docMasterId));

const getAiDocumentId = (it = {}) => {
  const raw = pickFirst(it.aiDocumentId, it.ai_document_id, it.aid, it.meta?.aiDocumentId, it.meta?.ai_document_id, it.meta?.aid);
  const s = String(raw || '').trim();
  return s || null;
};

const getFileRight = (it = {}) =>
  normalizeExt(pickFirst(it.docFileRight, it.file_right, it.fileRight));

const getBranchType = (it = {}) =>
  normalizeBranchType(
    pickFirst(
      it.docBranchType,
      it.doc_branch_type,
      it.branch_type,
      it.branchType
    )
  );

const getItemtypeName = (it = {}) => {
  const raw = pickFirst(it.itemtypeName, it.itemtype_name, it.itemTypeName);
  const s = String(raw || '').trim();
  return s || null;
};

const getStoredPath = (it = {}) =>
  normalizePathPart(
    pickFirst(
      it.docStoredPath,
      it.doc_stored_path,
      it.stored_path,
      it.storedPath
    )
  ) || null;

const buildGroupKey = (it = {}) => {
  const title = String(getDocTitle(it, 'Document')).trim().toLowerCase();
  const objectId = getObjectId(it);
  const docId = getDocId(it);
  const aiDocumentId = getAiDocumentId(it);
  const idKey = objectId != null ? `oid:${objectId}` : docId != null ? `did:${docId}` : aiDocumentId ? `aid:${aiDocumentId}` : 'id:none';
  const p = readPath(it);
  const pathKey = joinPath(p.rel, p.sub).toLowerCase() || 'path:none';
  const storedPath = String(getStoredPath(it) || '').toLowerCase() || 'stored:none';
  const branchType = String(getBranchType(it) || '').toLowerCase() || 'branch:none';
  const itemtypeName = String(getItemtypeName(it) || '').toLowerCase() || 'itemtype:none';
  return `${idKey}|${pathKey}|stored:${storedPath}|branch:${branchType}|itemtype:${itemtypeName}|title:${title}`;
};

const pickBestCandidate = (list = [], fallbackTitle = 'Document') => {
  const arr = Array.isArray(list) ? list : [];
  if (!arr.length) return null;

  const scored = arr.map((it) => {
    const objectId = getObjectId(it);
    const docId = getDocId(it);
    const aiDocumentId = getAiDocumentId(it);
    const docTitle = getDocTitle(it, fallbackTitle);
    const fileRight = getFileRight(it);
    const storedPath = getStoredPath(it);
    const branchType = getBranchType(it);
    const p = readPath(it);
    const pathText = joinPath(p.rel, p.sub);
    const page = toInt(it.page) || 1;

    let score = 0;
    if (objectId != null || docId != null || aiDocumentId) score += 100;
    if (fileRight === 'pdf') score += 30;
    else if (fileRight === 'md') score += 20;
    if (pathText) score += 10;
    if (storedPath) score += 10;
    if (docTitle) score += 5;
    if (page > 0) score += 1;

    return {
      it,
      score,
      objectId,
      docId,
      aiDocumentId,
      docTitle,
      page,
      docRelPath: p.rel,
      docSubPath: p.sub,
      docFileRight: fileRight,
      docBranchType: branchType,
      docStoredPath: storedPath,
    };
  });

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.page - b.page;
  });
  return scored[0] || null;
};

const getPagesFromList = (list = []) => {
  const out = [];
  const seen = new Set();
  for (const it of Array.isArray(list) ? list : []) {
    const p = toInt(it?.page);
    if (p == null || p <= 0) continue;
    if (seen.has(p)) continue;
    seen.add(p);
    out.push(p);
  }
  out.sort((a, b) => a - b);
  return out;
};

export default function RefAccordion({
  items,
  isUser,
  showActions,
  actionText,
  messageMid,
  messages,
  openDoc,
  openDocByTitle,
}) {
  const grouped = useMemo(() => {
    return (items || []).reduce((acc, it) => {
      const key = buildGroupKey(it);
        if (!acc[key]) {
          const p = readPath(it);
          acc[key] = {
            title: getDocTitle(it, 'Document'),
            itemtypeName: getItemtypeName(it),
            docRelPath: p.rel,
            docSubPath: p.sub,
            docBranchType: getBranchType(it),
            list: [],
          };
        }
        if (!acc[key].itemtypeName) {
          acc[key].itemtypeName = getItemtypeName(it);
        }
        acc[key].list.push(it);
        return acc;
      }, {});
  }, [items]);

  const onOpenParent = useCallback(
    (title, list, preferredPage = null) => {
      const base = Array.isArray(list) ? list : [];
      const narrowed =
        preferredPage != null ? base.filter((it) => toInt(it?.page) === toInt(preferredPage)) : base;
      const picked = pickBestCandidate(narrowed.length ? narrowed : base, title);
      if (!picked) return;

      if (picked.docId != null || picked.objectId != null || picked.aiDocumentId) {
        openDoc?.(
          picked.docId,
          picked.page,
          picked.docTitle || title,
          picked.objectId,
          picked.aiDocumentId,
          picked.docTitle,
          picked.docRelPath ?? null,
          picked.docSubPath ?? null,
          picked.docFileRight ?? null,
          picked.docStoredPath ?? null,
          picked.docBranchType ?? null
        );
        return;
      }

      if (picked.docTitle) {
        openDocByTitle?.(
          picked.docTitle,
          picked.page,
          picked.docRelPath ?? null,
          picked.docSubPath ?? null,
          picked.docFileRight ?? null,
          picked.docStoredPath ?? null,
          picked.docBranchType ?? null
        );
        return;
      }

      const firstWithHref = (Array.isArray(list) ? list : []).find((it) => it?.href);
      if (firstWithHref?.href) {
        window.open(firstWithHref.href, '_blank', 'noopener,noreferrer');
      }
    },
    [openDoc, openDocByTitle]
  );

  return (
    <>
      <div className="bubble-row left">
        <div className="bubble bubble-assistant">
          <div className="md">
            <div className="ref-title" style={{ margin: '0 0 8px' }}>
              <strong>Reference Doc</strong>
            </div>
            <div className="ref-acc">
              {Object.entries(grouped).map(([key, group]) => {
                const pathText = joinPath(group.docRelPath, group.docSubPath);
                const pages = getPagesFromList(group.list);
                return (
                  <div key={key}>
                    <div className="hdr" onClick={() => onOpenParent(group.title, group.list)}>
                      <span>
                        - {group.title}
                        {group.itemtypeName ? ` [${group.itemtypeName}]` : ''}
                        {pathText ? ` (${pathText})` : ''}
                      </span>
                      <span className="meta">
                        {pages.length ? `${pages.length} page(s)` : `${group.list.length} item(s)`}
                      </span>
                    </div>
                    {!!pages.length && (
                      <div className="ref-pages">
                        {pages.map((p) => (
                          <button
                            key={`${key}-p-${p}`}
                            type="button"
                            className="ref-page-chip"
                            onClick={() => onOpenParent(group.title, group.list, p)}
                          >
                            p.{p}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {!isUser && showActions && (
        <AnswerActionBar
          isAssistant
          contentStr={actionText || ''}
          messageMid={messageMid}
          fallbackMessageId={null}
          initialLiked={
            messages?.find((m) => m.message_id === messageMid)?.feedback?.score === 1
              ? true
              : messages?.find((m) => m.message_id === messageMid)?.feedback?.score === -1
              ? false
              : null
          }
          initialComment={messages?.find((m) => m.message_id === messageMid)?.feedback?.comment || ''}
        />
      )}
    </>
  );
}
