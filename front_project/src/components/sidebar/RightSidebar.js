import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useKB } from '../../contexts/KBContext';
import { resolveAgent } from '../ai/AICatalogPanel';
import '../../styles/components/rightSidebar.css';

import ChatHero from '../right/ChatHero';
import ChatBody from './right/ChatBody';
import InputArea from './right/InputArea';

import {
  AGENT_EXAMPLE_QUESTIONS,
  KB_QA_FORCED_KB_BY_EXAMPLE_INDEX,
  extractExtFromPathLike,
  inferBranchTypeFromPathLike,
  isLikelyUrl,
  mapKBToCat,
  normalizeBranchType,
  normalizeExt,
  normalizeMdFinal,
  normalizeMdLite,
  splitPathHint,
} from './rightSidebarUtils';
import { dispatchOpenDocTab, readOpenDocTabDetail } from '../../utils/openDocTab';

const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8001';

export default function RightSidebar({ rightOpen, onToggle }) {
  const { kbId, kbName, selectedDocIds } = useKB();
  const [mode, setMode] = useState('rag');
  const [selectedAgent, setSelectedAgent] = useState('KB Question & Answer');
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [source, setSource] = useState(null);
  const [vault, setVault] = useState({ kbId: null, name: '' });
  const [activeDoc, setActiveDoc] = useState(null);
  const [bookmarkDocIds, setBookmarkDocIds] = useState([]);

  const streamingMsgIdRef = useRef(null);
  const lastChunkRef = useRef('');
  const refsSectionAddedRef = useRef(false);
  const finalHandledRef = useRef(false);
  const galleryMsgIdRef = useRef(null);
  const galleryAddedRef = useRef(false);

  const stoppedRef = useRef(false);
  const activeRunIdRef = useRef(null);

  const scrollRef = useRef(null);
  const bottomRef = useRef(null);

  const lastAssistantIdRef = useRef(null);

  const [tickerVisible, setTickerVisible] = useState(false);
  const [tickerStep, setTickerStep] = useState('Question Analysis');
  const [tickerHint, setTickerHint] = useState('Analyzing your question and search intent.');
  const [tickerProgress, setTickerProgress] = useState(1);

  const [inputResetNonce, setInputResetNonce] = useState(0);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (bottomRef.current) {
      try {
        bottomRef.current.scrollIntoView({ block: 'end' });
      } catch {}
    }
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    const onLast = (e) => {
      const d = e?.detail || {};
      if (!d?.messageId) return;

      const serverId = d.messageId;
      lastAssistantIdRef.current = serverId;

      setMessages((prev) => {
        const list = [...prev];
        let lastIdx = -1;
        for (let i = list.length - 1; i >= 0; i--) {
          const m = list[i];
          if (m?.role === 'assistant' && typeof m?.content !== 'object') {
            lastIdx = i;
            break;
          }
        }
        if (lastIdx === -1) return prev;

        if (list[lastIdx]?.message_id === serverId) return prev;

        for (let i = lastIdx; i < list.length; i++) {
          const m = list[i];
          if (m?.role !== 'assistant') break;
          if (!m.message_id) {
            list[i] = { ...m, message_id: serverId };
          }
        }
        return list;
      });
    };

    window.addEventListener('qa:last-message', onLast);
    return () => window.removeEventListener('qa:last-message', onLast);
  }, []);

  useEffect(() => {
    const clearLastAssistantId = () => {
      try {
        sessionStorage.removeItem('qa.last_assistant_message_id');
      } catch {}
      lastAssistantIdRef.current = null;
    };
    ['qa:load-session', 'ai:select', 'qa:new', 'qa:reset'].forEach((evt) => {
      window.addEventListener(evt, clearLastAssistantId);
    });
    return () => {
      ['qa:load-session', 'ai:select', 'qa:new', 'qa:reset'].forEach((evt) => {
        window.removeEventListener(evt, clearLastAssistantId);
      });
    };
  }, []);

  useEffect(() => {
    if (!kbId) return;
    setVault((prev) => {
      if (prev.kbId === kbId && (prev.name || '') === (kbName || '')) return prev;
      return { kbId, name: kbName || `KB ${kbId}` };
    });
    setSource((prev) => prev || 'vault');
  }, [kbId, kbName]);

  useEffect(() => {
    const onBookmarkSelect = (e) => {
      const ids = Array.isArray(e?.detail?.docIds) ? e.detail.docIds : [];
      const next = ids.map((v) => Number(v)).filter((v) => Number.isFinite(v));
      setBookmarkDocIds(next);
      if (!next.length && source === 'bookmark') {
        setSource(null);
      }
    };
    window.addEventListener('bookmarks:selected-doc-ids', onBookmarkSelect);
    return () => window.removeEventListener('bookmarks:selected-doc-ids', onBookmarkSelect);
  }, [source]);

  const handleSelectAgent = useCallback((item) => {
    setSelectedAgent(item.title);
    setMode('rag');
  }, []);

  const hasActiveDoc = useMemo(
    () => Boolean(activeDoc?.docId != null || activeDoc?.objectId != null),
    [activeDoc]
  );

  const effectiveSource = useMemo(() => {
    if (source === 'activeDoc' && !hasActiveDoc) {
      return vault?.kbId ? 'vault' : null;
    }
    return source;
  }, [source, hasActiveDoc, vault]);

  const kbChipLabel = useMemo(() => {
    if (effectiveSource === 'activeDoc') return 'Selected QA';
    if (effectiveSource === 'bookmark') return 'Bookmark QA';
    return 'KB QA';
  }, [effectiveSource]);

  const onChangeKBSelect = useCallback(
    (e) => {
      const val = e.target.value;
      if (val === 'use-vault') {
        if (vault?.kbId) setSource('vault');
        else window.alert('Select KB (Vault) on the left first.');
      } else if (val === 'use-active-doc') {
        if (hasActiveDoc) setSource('activeDoc');
        else window.alert('There are currently no documents.');
      } else if (val === 'use-bookmark') {
        if (bookmarkDocIds?.length) setSource('bookmark');
        else window.alert('Select a bookmark document first.');
      }
      e.target.value = '';
    },
    [vault, hasActiveDoc, bookmarkDocIds]
  );

  useEffect(() => {
    const onVaultSelect = (e) => {
      const d = e?.detail || {};
      const kid = d.kbId ?? null;
      const name = d.name ?? '';
      if (kid == null) return;

      setVault({ kbId: kid, name: name || `KB ${kid}` });
      setSource((prev) => prev || 'vault');
    };
    window.addEventListener('kb:vault-select', onVaultSelect);
    return () => window.removeEventListener('kb:vault-select', onVaultSelect);
  }, []);

  useEffect(() => {
    const onOpenDoc = (e) => {
      const d = readOpenDocTabDetail(e);
      const docId = d.docId ?? d.doc_id ?? null;
      const objectId =
        d.objectId ??
        d.object_id ??
        d.storage_object_id ??
        d.storageObjectId ??
        null;
      const title = d.title ?? '';
      const tabType = d.type ?? d.tabType ?? d.kind ?? null;

      if ((!docId && !objectId) || (tabType && tabType !== 'doc')) {
        setActiveDoc(null);
        setSource((prev) => (prev === 'activeDoc' ? null : prev));
        return;
      }
      setActiveDoc({ docId: docId ?? objectId, objectId: objectId ?? docId, title });
    };
    window.addEventListener('open-doc-tab', onOpenDoc);
    return () => window.removeEventListener('open-doc-tab', onOpenDoc);
  }, []);

  useEffect(() => {
    // Keep Selected QA valid only while an active document actually exists.
    if (source !== 'activeDoc') return;
    if (activeDoc?.docId || activeDoc?.objectId) return;
    if (vault?.kbId) {
      setSource('vault');
    } else {
      setSource(null);
    }
  }, [source, activeDoc, vault]);

  useEffect(() => {
    const onSelect = (e) => {
      const d = e?.detail || {};
      if (!d) return;
      handleSelectAgent(d);
    };
    window.addEventListener('ai:select', onSelect);
    return () => window.removeEventListener('ai:select', onSelect);
  }, [handleSelectAgent]);

  const toAbsUrl = useCallback((u) => {
    if (!u) return '';
    if (/^https?:\/\//i.test(u) || /^data:/i.test(u)) return u;
    return `${API_BASE}${u.startsWith('/') ? u : `/${u}`}`;
  }, []);

  const normalizeRefs = useCallback(
    (srcs = []) => {
      const arr = Array.isArray(srcs) ? srcs : [];
      const toInt = (v) => {
        const s = String(v ?? '').trim();
        if (!s) return null;
        const n = Number(s);
        return Number.isFinite(n) ? Math.trunc(n) : null;
      };
      const pickFirst = (...vals) => {
        for (const v of vals) {
          if (v == null) continue;
          if (typeof v === 'string' && v.trim() === '') continue;
          return v;
        }
        return null;
      };
      const pickPathList = (...vals) => {
        for (const v of vals) {
          if (Array.isArray(v) && v.length) return v;
        }
        return [];
      };
      const parsePageValues = (raw) => {
        const out = [];
        const push = (v) => {
          const n = toInt(v);
          if (n != null && n > 0) out.push(n);
        };
        if (Array.isArray(raw)) {
          raw.forEach(push);
          return out;
        }
        if (typeof raw === 'string') {
          const s = raw.trim();
          if (!s) return out;
          if (s.startsWith('[') && s.endsWith(']')) {
            try {
              const parsed = JSON.parse(s);
              if (Array.isArray(parsed)) {
                parsed.forEach(push);
                return out;
              }
            } catch {}
          }
          if (/[,\s]/.test(s)) {
            s
              .split(/[,\s]+/)
              .filter(Boolean)
              .forEach(push);
            return out;
          }
          push(s);
          return out;
        }
        push(raw);
        return out;
      };
      const uniqSortedPages = (vals = []) => {
        const seen = new Set();
        const out = [];
        vals.forEach((v) => {
          const n = toInt(v);
          if (n == null || n <= 0 || seen.has(n)) return;
          seen.add(n);
          out.push(n);
        });
        out.sort((a, b) => a - b);
        return out;
      };
      return arr.flatMap((s, i) => {
        const obj = typeof s === 'string' ? { id: s } : s || {};
        const meta = obj.meta && typeof obj.meta === 'object' ? obj.meta : {};
        const title =
          obj.title ||
          obj.name ||
          obj.label ||
          obj.filename ||
          obj.file_name ||
          obj.doc_title ||
          meta.doc_title ||
          meta.docTitle ||
          `doc_${i + 1}`;
        const itemtypeRaw = pickFirst(
          obj.itemtypeName,
          obj.itemtype_name,
          obj.itemTypeName,
          meta.itemtypeName,
          meta.itemtype_name,
          meta.itemTypeName
        );
        const itemtypeName =
          itemtypeRaw == null ? null : String(itemtypeRaw).trim() || null;
        const rawObjectId = pickFirst(
          obj.object_id,
          obj.objectid,
          obj.objectId,
          obj.storage_object_id,
          obj.storageObjectId,
          meta.object_id,
          meta.objectid,
          meta.objectId,
          meta.storage_object_id,
          meta.storageObjectId
        );
        const rawDocId = pickFirst(
          obj.doc_id,
          obj.docId,
          obj.doc_master_id,
          obj.docMasterId,
          meta.doc_id,
          meta.docId,
          meta.doc_master_id,
          meta.docMasterId
        );
        const fallbackId = pickFirst(obj.id, meta.id);
        const objectId = toInt(rawObjectId) || toInt(rawDocId) || toInt(fallbackId);
        const docId = toInt(rawDocId) || toInt(rawObjectId) || objectId;
        const rawHref =
          pickFirst(
            obj.url,
            obj.href,
            obj.s3_url,
            obj.path,
            obj.relpath && (obj.relpath.url || obj.relpath.path),
            meta.url,
            meta.file_url,
            meta.href
          ) || '';
        let href = isLikelyUrl(rawHref) ? toAbsUrl(rawHref) : '';
        const scoreRaw = pickFirst(obj.score, meta.score);
        const scoreNum = Number(scoreRaw);
        const score = Number.isFinite(scoreNum) ? scoreNum : undefined;
        const pickPagesFrom = (target, targetMeta = {}) => {
          const directPages = uniqSortedPages(
            [
              target?.page,
              target?.page_no,
              target?.pageNo,
              target?.pageNumber,
              target?.page_number,
              target?.pages,
              target?.page_numbers,
              target?.pageNumbers,
              targetMeta?.page,
              targetMeta?.page_no,
              targetMeta?.pageNo,
              targetMeta?.pageNumber,
              targetMeta?.page_number,
              targetMeta?.pages,
              targetMeta?.page_numbers,
              targetMeta?.pageNumbers,
            ].flatMap((v) => parsePageValues(v))
          );
          if (directPages.length) return directPages;

          const imageRefs = [
            ...(Array.isArray(target?.image_refs) ? target.image_refs : []),
            ...(Array.isArray(targetMeta?.image_refs) ? targetMeta.image_refs : []),
          ];
          for (const ref of imageRefs) {
            if (!ref || typeof ref !== 'object') continue;
            const refMeta = ref.meta && typeof ref.meta === 'object' ? ref.meta : {};
            const refPages = pickPagesFrom(ref, refMeta);
            if (refPages.length) return refPages;
          }

          return [];
        };
        const sourcePages = pickPagesFrom(obj, meta);
        const page = sourcePages[0] ?? null;
        const fileRight =
          pickFirst(
            obj.file_right,
            obj.fileRight,
            obj.ext,
            obj.file_ext,
            obj.storage_object_file_right,
            meta.file_right,
            meta.fileRight,
            meta.ext,
            meta.file_ext,
            meta.storage_object_file_right
          ) || null;
        const branchTypeRaw =
          pickFirst(
            obj.branch_type,
            obj.branchType,
            obj.doc_branch_type,
            obj.docBranchType,
            obj.storage_object_branch_type,
            meta.branch_type,
            meta.branchType,
            meta.doc_branch_type,
            meta.docBranchType,
            meta.storage_object_branch_type
          ) || null;
        const relPathRaw =
          pickFirst(
            obj.relative_path,
            obj.relativePath,
            obj.rel_path,
            obj.storage_object_relative_path,
            meta.relative_path,
            meta.relativePath,
            meta.rel_path,
            meta.storage_object_relative_path
          ) || null;
        const subPathRaw =
          pickFirst(
            obj.sub_path,
            obj.subPath,
            obj.storage_object_sub_path,
            meta.sub_path,
            meta.subPath,
            meta.storage_object_sub_path
          ) || null;
        const fileUrlRaw =
          pickFirst(
            obj.file_url,
            obj.fileUrl,
            obj.file_uri,
            obj.filepath,
            obj.doc_path,
            obj.path,
            obj.relpath && (obj.relpath.path || obj.relpath.url),
            meta.file_url,
            meta.fileUrl,
            meta.file_uri,
            meta.filepath,
            meta.doc_path,
            meta.path
          ) || null;
        const docPathRaw =
          pickFirst(obj.doc_path, obj.docPath, meta.doc_path, meta.docPath) || null;
        const hintRaw = isLikelyUrl(fileUrlRaw) ? docPathRaw : fileUrlRaw;
        const hinted = splitPathHint(hintRaw);
        const storedPathRaw =
          pickFirst(
            obj.stored_path,
            obj.storedPath,
            obj.doc_stored_path,
            obj.docStoredPath,
            meta.stored_path,
            meta.storedPath,
            meta.doc_stored_path,
            meta.docStoredPath
          ) || null;
        const hintedStored = splitPathHint(storedPathRaw);
        const docTitle =
          pickFirst(obj.doc_title, obj.docTitle, meta.doc_title, meta.docTitle, hinted.fileName, title) || title;
        const inferredExt =
          extractExtFromPathLike(fileUrlRaw) ||
          extractExtFromPathLike(rawHref) ||
          extractExtFromPathLike(docTitle);

        const docRelPath = relPathRaw ?? hinted.relPath ?? null;
        const docSubPath = subPathRaw ?? hinted.subPath ?? null;
        const docBranchType = normalizeBranchType(
          branchTypeRaw ?? inferBranchTypeFromPathLike(hintRaw) ?? inferBranchTypeFromPathLike(docPathRaw)
        );
        const docFileRight = normalizeExt(
          fileRight ??
            hinted.fileRight ??
            inferredExt ??
            (docBranchType === 'KIWIX' ? 'md' : null)
        );
        const docStoredPath = hintedStored.storedPath ?? hinted.storedPath ?? null;

        if (!href && isLikelyUrl(fileUrlRaw)) href = toAbsUrl(fileUrlRaw);

        const baseItem = {
          title,
          itemtypeName,
          docTitle,
          objectId,
          docId,
          href,
          score,
          page,
          docRelPath,
          docSubPath,
          docFileRight,
          docBranchType,
          docStoredPath,
        };

        const linkedPathsRaw = pickPathList(
          obj.linked_paths,
          obj.paths,
          meta.linked_paths,
          meta.paths
        );
        const linkedPaths = linkedPathsRaw
          .map((p) => String(p || '').trim())
          .filter(Boolean);
        const pathVariants = [];
        if (!linkedPaths.length) {
          pathVariants.push(baseItem);
        } else {
          const pathSeen = new Set();
          for (const p of linkedPaths) {
            const hintedPath = splitPathHint(p);
            const item = {
              ...baseItem,
              docTitle: hintedPath.fileName ?? baseItem.docTitle,
              docRelPath: baseItem.docRelPath ?? hintedPath.relPath ?? null,
              docSubPath: baseItem.docSubPath ?? hintedPath.subPath ?? null,
              docFileRight: normalizeExt(
                baseItem.docFileRight ?? hintedPath.fileRight ?? null
              ),
              docBranchType: normalizeBranchType(
                baseItem.docBranchType ?? inferBranchTypeFromPathLike(p)
              ),
              docStoredPath: baseItem.docStoredPath ?? hintedPath.storedPath ?? null,
            };
            if (!item.docFileRight && item.docBranchType === 'KIWIX') {
              item.docFileRight = 'md';
            }
            const pathKey = [
              item.objectId ?? '',
              item.docId ?? '',
              item.itemtypeName ?? '',
              item.docTitle ?? '',
              item.docRelPath ?? '',
              item.docSubPath ?? '',
              item.docFileRight ?? '',
              item.docBranchType ?? '',
              item.docStoredPath ?? '',
            ].join('|');
            if (pathSeen.has(pathKey)) continue;
            pathSeen.add(pathKey);
            pathVariants.push(item);
          }
        }

        const chunkRows = [
          ...(Array.isArray(obj.chunks) ? obj.chunks : []),
          ...(Array.isArray(meta.chunks) ? meta.chunks : []),
        ].filter((chunk) => chunk && typeof chunk === 'object');
        const chunkVariants = chunkRows.length
          ? chunkRows.flatMap((chunk) => {
              const chunkMeta = chunk.meta && typeof chunk.meta === 'object' ? chunk.meta : {};
              const chunkScoreRaw = pickFirst(chunk.score, chunkMeta.score, score);
              const chunkScoreNum = Number(chunkScoreRaw);
              const chunkScore = Number.isFinite(chunkScoreNum) ? chunkScoreNum : score;
              const chunkPages = pickPagesFrom(chunk, chunkMeta);
              const effectivePages = chunkPages.length ? chunkPages : sourcePages.length ? sourcePages : [null];
              return effectivePages.map((p) => ({
                page: p,
                score: chunkScore,
              }));
            })
          : (sourcePages.length ? sourcePages : [null]).map((p) => ({ page: p, score }));

        const out = [];
        const seen = new Set();
        for (const pathItem of pathVariants) {
          for (const chunkItem of chunkVariants) {
            const item = {
              ...pathItem,
              page: chunkItem.page ?? pathItem.page ?? null,
              score: chunkItem.score ?? pathItem.score,
            };
            const key = [
              item.objectId ?? '',
              item.docId ?? '',
              item.itemtypeName ?? '',
              item.docTitle ?? '',
              item.docRelPath ?? '',
              item.docSubPath ?? '',
              item.docFileRight ?? '',
              item.docBranchType ?? '',
              item.docStoredPath ?? '',
              item.page ?? '',
            ].join('|');
            if (seen.has(key)) continue;
            seen.add(key);
            out.push(item);
          }
        }

        return out.length ? out : [baseItem];
      });
    },
    [toAbsUrl]
  );

  const normalizeImageList = useCallback(
    (arr = []) => {
      const pick = (x) =>
        typeof x === 'string' ? x : x?.url || x?.href || x?.path || x?.src || x?.dataUrl || '';
      const out = [];
      const seen = new Set();
      (Array.isArray(arr) ? arr : []).forEach((it) => {
        const raw = pick(it);
        if (!raw) return;
        const src = toAbsUrl(raw);
        if (!seen.has(src)) {
          seen.add(src);
          out.push(src);
        }
      });
      return out;
    },
    [toAbsUrl]
  );

  const extractThumbDataUrls = useCallback((sources = []) => {
    const buildDataUrl = (thumbB64, mime) => {
      const b64 = (thumbB64 || '').trim();
      if (!b64) return null;
      const m = (mime && typeof mime === 'string' ? mime : '').trim() || 'image/jpeg';
      return `data:${m};base64,${b64}`;
    };
    const results = [];
    for (const s of Array.isArray(sources) ? sources : []) {
      const chunks = Array.isArray(s?.meta?.chunks)
        ? s.meta.chunks
        : Array.isArray(s?.chunks)
        ? s.chunks
        : [];
      for (const ch of chunks) {
        const refs = Array.isArray(ch?.image_refs) ? ch.image_refs : [];
        for (const r of refs) {
          const dataUrl = buildDataUrl(r?.thumb_b64, r?.thumb_mime);
          if (dataUrl) results.push({ dataUrl });
        }
      }
    }
    return results;
  }, []);

  const gatherImages = useCallback(
    (payload) => {
      const imagesA = payload?.images || [];
      const imagesB = payload?.image_urls || [];
      const imagesC = payload?.imageUrls || [];
      const sources = payload?.sources || [];
      const thumb_b64 = payload?.thumb_b64;
      const thumb_mime = payload?.thumb_mime || 'image/jpeg';

      const urlList = normalizeImageList([].concat(imagesA, imagesB, imagesC));
      const thumbList = [];

      if (thumb_b64) thumbList.push(`data:${thumb_mime};base64,${String(thumb_b64).trim()}`);
      if (Array.isArray(sources) && sources.length) {
        thumbList.push(...extractThumbDataUrls(sources).map((t) => t.dataUrl).filter(Boolean));
      }

      const maxLen = Math.max(thumbList.length, urlList.length);
      const grouped = [];
      for (let i = 0; i < maxLen; i++) {
        const candidates = [];
        if (thumbList[i]) candidates.push(thumbList[i]);
        if (urlList[i]) candidates.push(urlList[i]);
        if (candidates.length) grouped.push(candidates);
      }

      if (grouped.length === 0) {
        if (thumbList.length) grouped.push(...thumbList.map((t) => [t]));
        else if (urlList.length) grouped.push(...urlList.map((u) => [u]));
      }
      return grouped;
    },
    [normalizeImageList, extractThumbDataUrls]
  );

  const findLastAssistantIndex = useCallback((list) => {
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i]?.role === 'assistant' && typeof list[i]?.content !== 'object') return i;
    }
    return -1;
  }, []);

  useEffect(() => {
    const onAssistantReply = (e) => {
      if (stoppedRef.current) return;

      const d = e?.detail || {};
      if (d?.runId && d.runId !== activeRunIdRef.current) return;
      const textChunk = (d?.content ?? '').toString();
      if (finalHandledRef.current && d?.final !== true) return;

      const ensureRefsSection = (srcs) => {
        if (refsSectionAddedRef.current) return;
        const items = normalizeRefs(srcs);
        if (!items.length) return;
        setMessages((prev) => [
          ...prev,
          { id: `a-src-${Date.now()}`, role: 'assistant', content: { type: 'refs', items } },
        ]);
        refsSectionAddedRef.current = true;
      };

      const streamImgs = gatherImages(d);
      if (streamImgs.length) {
        if (!galleryAddedRef.current) {
          const gid = `a-gal-${Date.now()}-stream`;
          galleryMsgIdRef.current = gid;
          galleryAddedRef.current = true;
          setMessages((prev) => [
            ...prev,
            { id: gid, role: 'assistant', content: { type: 'gallery', images: streamImgs } },
          ]);
        } else if (galleryMsgIdRef.current) {
          setMessages((prev) => {
            const list = [...prev];
            const idx = list.findIndex((m) => m.id === galleryMsgIdRef.current);
            if (idx >= 0) list[idx] = { ...list[idx], content: { type: 'gallery', images: streamImgs } };
            return list;
          });
        }
      }

      if (d?.final === true) {
        if (finalHandledRef.current) return;
        finalHandledRef.current = true;

        setMessages((prev) => {
          const id = streamingMsgIdRef.current;
          const list = [...prev];

          if (id) {
            const idx = list.findIndex((m) => m.id === id);
            if (idx >= 0) {
              const nextText = normalizeMdFinal(textChunk || list[idx].content || '');
              list[idx] = { ...list[idx], content: nextText };
              return list;
            }
          }

          const lastIdx = findLastAssistantIndex(list);
          if (lastIdx >= 0) {
            const nextText = normalizeMdFinal(textChunk || list[lastIdx].content || '');
            list[lastIdx] = { ...list[lastIdx], content: nextText };
            return list;
          }

          const finalText = normalizeMdFinal(textChunk || '');
          if (finalText) list.push({ id: `a-final-${Date.now()}`, role: 'assistant', content: finalText });
          return list;
        });

        const galleryImages = gatherImages(d);
        if (galleryImages.length) {
          if (!galleryAddedRef.current) {
            const gid = `a-gal-${Date.now()}`;
            galleryMsgIdRef.current = gid;
            galleryAddedRef.current = true;
            setMessages((prev) => [
              ...prev,
              { id: gid, role: 'assistant', content: { type: 'gallery', images: galleryImages } },
            ]);
          } else if (galleryMsgIdRef.current) {
            setMessages((prev) => {
              const list = [...prev];
              const idx = list.findIndex((m) => m.id === galleryMsgIdRef.current);
              if (idx >= 0) list[idx] = { ...list[idx], content: { type: 'gallery', images: galleryImages } };
              return list;
            });
          }
        }

        if (Array.isArray(d?.sources) && d.sources.length > 0) {
          refsSectionAddedRef.current = false;
          ensureRefsSection(d.sources);
        }

        streamingMsgIdRef.current = null;
        lastChunkRef.current = '';
        setTimeout(scrollToBottom, 0);
        setTickerVisible(false);
        return;
      }

      if (textChunk !== '') {
        if (textChunk === lastChunkRef.current) return;
        lastChunkRef.current = textChunk;
        setMessages((prev) => {
          const list = [...prev];
          const idx = list.findIndex((m) => m.id === streamingMsgIdRef.current);
          if (idx >= 0) {
            list[idx] = { ...list[idx], content: `${list[idx].content}${textChunk}` };
          } else {
            const id = `a-${Date.now()}`;
            streamingMsgIdRef.current = id;
            list.push({ id, role: 'assistant', content: textChunk });
          }
          return list;
        });
      }

      setTimeout(scrollToBottom, 0);
    };

    const onReset = () => {
      stoppedRef.current = false;
      activeRunIdRef.current = null;
      setMessages([]);
      streamingMsgIdRef.current = null;
      lastChunkRef.current = '';
      refsSectionAddedRef.current = false;
      finalHandledRef.current = false;
      galleryAddedRef.current = false;
      galleryMsgIdRef.current = null;
      setTickerVisible(false);
      setTickerStep('Question Analysis');
      setTickerHint('Analyzing your question and search intent.');
      setTickerProgress(1);
      setInputResetNonce((n) => n + 1);
    };

    window.addEventListener('qa:assistant-reply', onAssistantReply);
    window.addEventListener('qa:reset', onReset);
    return () => {
      window.removeEventListener('qa:assistant-reply', onAssistantReply);
      window.removeEventListener('qa:reset', onReset);
    };
  }, [normalizeRefs, gatherImages, findLastAssistantIndex, scrollToBottom]);

  const normalizeMessage = useCallback((m, idx) => {
    const rawSender = m.sender ?? (m.role ? String(m.role).toLowerCase() : '');
    const sender = typeof rawSender === 'string' ? rawSender.toLowerCase() : '';
    const textVal = m.text ?? m.content ?? '';
    const mid = m.message_id ?? m.id ?? idx;
    return {
      id: String(mid),
      message_id: mid,
      role: sender === 'user' ? 'user' : 'assistant',
      content: textVal,
      meta: m.meta || {},
      images: m.images || m.image_urls || m.imageUrls || [],
      sources: Array.isArray(m?.meta?.sources) ? m.meta.sources : m.sources || [],
      thumb_b64: m?.meta?.thumb_b64,
      thumb_mime: m?.meta?.thumb_mime,
      feedback: m.feedback || null,
    };
  }, []);

  const buildRecentHistory = useCallback(() => {
    const linear = messages.filter(
      (m) =>
        (m?.role === 'user' || m?.role === 'assistant') &&
        typeof m?.content === 'string' &&
        m.content.trim()
    );

    const pairs = [];
    let pendingQuestion = null;
    for (const m of linear) {
      if (m.role === 'user') {
        pendingQuestion = String(m.content || '').replace(/<br\s*\/?>/gi, '\n').trim();
        continue;
      }
      if (m.role === 'assistant' && pendingQuestion) {
        const answer = String(m.content || '').replace(/<br\s*\/?>/gi, '\n').trim();
        if (answer) pairs.push({ question: pendingQuestion, answer });
        pendingQuestion = null;
      }
    }
    return pairs.slice(-2);
  }, [messages]);

  useEffect(() => {
    const onLoadSession = (e) => {
      const d = e?.detail || {};
      const msgs = Array.isArray(d.messages) ? d.messages : [];
      const sid = d.sessionId ?? null;
      const appType = d.appType ?? null;
      const appId = d.appId ?? null;
      const agentId = d.agentId ?? null;
      const sessionKbName = d.kbName ?? '';
      const sessionSelectedDocIds = Array.isArray(d.selectedDocIds) ? d.selectedDocIds : [];

      const meta = resolveAgent(appType, agentId, appId);
      setMode(meta.mode);
      setSelectedAgent(
        meta.mode === 'rag'
          ? meta.title && meta.title !== 'RAG'
            ? meta.title
            : 'KB Question & Answer'
          : 'KB Question & Answer'
      );

      setSessionId(sid);
      if (sessionKbName === 'Bookmarks' && sessionSelectedDocIds.length) {
        setSource('bookmark');
        setBookmarkDocIds(sessionSelectedDocIds);
      }
      stoppedRef.current = false;
      activeRunIdRef.current = null;
      streamingMsgIdRef.current = null;
      lastChunkRef.current = '';
      refsSectionAddedRef.current = false;
      finalHandledRef.current = false;
      galleryAddedRef.current = false;
      galleryMsgIdRef.current = null;

      const built = [];
      msgs.forEach((m, idx) => {
        const nm = normalizeMessage(m, idx);
        const baseText = normalizeMdFinal(String(nm.content ?? '').trim());
        if (baseText) {
          built.push({
            id: nm.id,
            message_id: nm.message_id,
            role: nm.role,
            content: baseText,
            feedback: nm.feedback || null,
          });
        }
        if (nm.role === 'assistant') {
          const galleryImages = gatherImages({
            images: nm.images,
            sources: nm.sources,
            thumb_b64: nm.thumb_b64,
            thumb_mime: nm.thumb_mime,
          });
          if (galleryImages.length) {
            built.push({
              id: `a-gal-${Date.now()}-${idx}`,
              message_id: nm.message_id,
              role: 'assistant',
              content: { type: 'gallery', images: galleryImages },
              feedback: nm.feedback || null,
            });
          }
          const items = normalizeRefs(nm.sources);
          if (items.length) {
            built.push({
              id: `a-src-${Date.now()}-${idx}`,
              message_id: nm.message_id,
              role: 'assistant',
              content: { type: 'refs', items },
              feedback: nm.feedback || null,
            });
          }
        }
      });

      setMessages(built);
      setTimeout(scrollToBottom, 0);
      setInputResetNonce((n) => n + 1);
    };

    const onSetSession = (e) => {
      const sid = e?.detail?.sessionId ?? null;
      if (sid) setSessionId(sid);
    };

    window.addEventListener('qa:load-session', onLoadSession);
    window.addEventListener('qa:set-session', onSetSession);
    return () => {
      window.removeEventListener('qa:load-session', onLoadSession);
      window.removeEventListener('qa:set-session', onSetSession);
    };
  }, [normalizeMessage, normalizeRefs, gatherImages, scrollToBottom]);

  useEffect(() => {
    const onTicker = (e) => {
      const d = e?.detail || {};
      if (d?.visible === true) setTickerVisible(true);
      if (d?.visible === false) setTickerVisible(false);
      if (typeof d?.step === 'string' && d.step.trim()) setTickerStep(d.step.trim());
      const hint =
        typeof d?.hint === 'string'
          ? d.hint.trim()
          : typeof d?.message === 'string'
            ? d.message.trim()
            : '';
      if (hint) setTickerHint(hint);
      if (Number.isFinite(Number(d?.progress))) {
        const p = Math.max(1, Math.min(4, Math.round(Number(d.progress))));
        setTickerProgress(p);
      }
    };
    window.addEventListener('qa:ticker', onTicker);
    window.addEventListener('qa:ticker-step', onTicker);
    return () => {
      window.removeEventListener('qa:ticker', onTicker);
      window.removeEventListener('qa:ticker-step', onTicker);
    };
  }, [mode]);

  useEffect(() => {
    const onFbStatus = (e) => {
      const d = e?.detail || {};
      if (!d || !d.messageId) return;
      setMessages((prev) => {
        const list = [...prev];
        const idx = list.findIndex((m) => m.message_id === d.messageId || m.id === d.messageId);
        if (idx >= 0 && d.ok) {
          const next = { ...(list[idx] || {}) };
          next.feedback = { score: d.score > 0 ? 1 : -1, comment: d.comment || null };
          list[idx] = next;
        }
        return list;
      });
    };
    window.addEventListener('qa:feedback-status', onFbStatus);
    return () => window.removeEventListener('qa:feedback-status', onFbStatus);
  }, []);

  const submitText = useCallback(
    (rawText, opts = {}) => {
      const q = (String(rawText ?? '') || '').replace(/\r\n/g, '\n').trimEnd();
      if (!q) return;

      const forcedVault =
        opts?.forceVault && typeof opts.forceVault === 'object' ? opts.forceVault : null;

      if (selectedAgent === 'Deep Research') {
        window.alert('Deep Research is not implemented yet.');
        return;
      }

      stoppedRef.current = false;
      const runId = `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      activeRunIdRef.current = runId;

      const userId = `u-${Date.now()}`;
      const preId = `a-pre-${Date.now()}`;
      const useStream = true;

      const nextMsgs = [
        { id: userId, role: 'user', content: q.replace(/\n/g, '<br>') },
        { id: preId, role: 'assistant', content: '' },
      ];

      setMessages((prev) => [...prev, ...nextMsgs]);

      streamingMsgIdRef.current = preId;
      lastChunkRef.current = '';
      refsSectionAddedRef.current = false;
      finalHandledRef.current = false;
      galleryAddedRef.current = false;
      galleryMsgIdRef.current = null;

      setTickerVisible(true);
      setTickerStep('Question Analysis');
      setTickerHint('Analyzing your question and search intent.');
      setTickerProgress(1);
      setTimeout(scrollToBottom, 0);

      const isGeneralQA = selectedAgent === 'General QA';
      const history = buildRecentHistory();
      const sourceForSubmit = forcedVault ? 'vault' : effectiveSource;
      const vaultForSubmit = forcedVault
        ? { kbId: forcedVault.kbId ?? null, name: forcedVault.name || '' }
        : vault;

      if (!isGeneralQA && mode === 'rag' && !sourceForSubmit) {
        window.alert('Please select KB first from the right KB selection chip.');
        return;
      }
      if (!isGeneralQA && mode === 'rag' && sourceForSubmit === 'bookmark' && !bookmarkDocIds?.length) {
        window.alert('Select a bookmark document first.');
        return;
      }

      const ragKbId = isGeneralQA
        ? null
        : mode === 'rag' && sourceForSubmit === 'vault'
          ? vaultForSubmit?.kbId ?? null
          : null;

      const docIdSingle = isGeneralQA
        ? null
        : mode === 'rag'
          ? sourceForSubmit === 'bookmark'
            ? bookmarkDocIds?.length
              ? bookmarkDocIds[0]
              : null
            : sourceForSubmit === 'activeDoc'
            ? activeDoc?.objectId ?? activeDoc?.docId ?? null
            : (selectedDocIds?.length ?? 0) >= 1
              ? selectedDocIds[0]
              : null
          : null;

      const selectedDocIdsPayload = isGeneralQA
        ? []
        : mode === 'rag' && sourceForSubmit === 'bookmark'
          ? Array.isArray(bookmarkDocIds)
            ? bookmarkDocIds
            : []
          : [];

      let kbNameOut = '';
      if (!isGeneralQA && mode === 'rag') {
        if (sourceForSubmit === 'vault') kbNameOut = vaultForSubmit?.name || '';
        else if (sourceForSubmit === 'activeDoc') kbNameOut = activeDoc?.title || '';
        else if (sourceForSubmit === 'bookmark') kbNameOut = 'Bookmarks';
        else if (Array.isArray(selectedDocIds) && selectedDocIds.length > 0) {
          kbNameOut = typeof kbName === 'string' ? kbName : '';
        }
      } else if (!isGeneralQA) {
        kbNameOut = typeof kbName === 'string' ? kbName : '';
      }

      const cat = isGeneralQA ? '' : mapKBToCat(kbNameOut || '');

      window.dispatchEvent(
        new CustomEvent('qa:submit', {
          detail: {
            text: q,
            useStream,
            appType: selectedAgent || 'KB Question & Answer',
            kbId: isGeneralQA ? undefined : ragKbId ?? undefined,
            docId: isGeneralQA ? undefined : docIdSingle ?? undefined,
            kbName: isGeneralQA ? undefined : kbNameOut || undefined,
            selectedDocIds:
              isGeneralQA ? undefined : selectedDocIdsPayload.length ? selectedDocIdsPayload : undefined,
            source: isGeneralQA ? undefined : sourceForSubmit || undefined,
            engine: 'rag',
            sessionId: sessionId || null,
            cat: isGeneralQA ? undefined : cat || undefined,
            history: history.length ? history : undefined,
            runId,
          },
        })
      );
    },
    [
      mode,
      effectiveSource,
      vault,
      activeDoc,
      selectedDocIds,
      bookmarkDocIds,
      kbName,
      selectedAgent,
      sessionId,
      scrollToBottom,
      buildRecentHistory,
    ]
  );

  const openDoc = useCallback(
    (
      docId,
      page,
      title,
      objectId = null,
      docTitle = null,
      docRelPath = null,
      docSubPath = null,
      docFileRight = null,
      docStoredPath = null,
      docBranchType = null
    ) => {
      if (!docId && !objectId) return;
            dispatchOpenDocTab({
        docId,
        objectId: objectId ?? null,
        page,
        title,
        docTitle: docTitle || title,
        docRelPath: docRelPath ?? null,
        docSubPath: docSubPath ?? null,
        docFileRight: docFileRight ?? null,
        docStoredPath: docStoredPath ?? null,
        docBranchType: docBranchType ?? null,
        forceNewTab: true,
      });
    },
    []
  );

  const openDocByTitle = useCallback((docTitle, page, docRelPath = null, docSubPath = null, docFileRight = null, docStoredPath = null, docBranchType = null) => {
    if (!docTitle) return;
        dispatchOpenDocTab({
      docTitle,
      page,
      docRelPath: docRelPath ?? null,
      docSubPath: docSubPath ?? null,
      docFileRight: docFileRight ?? null,
      docStoredPath: docStoredPath ?? null,
      docBranchType: docBranchType ?? null,
      forceNewTab: true,
    });
  }, []);

  const isStreaming =
    !!streamingMsgIdRef.current && finalHandledRef.current !== true && stoppedRef.current !== true;

  const stopStreaming = useCallback(() => {
    const sid = streamingMsgIdRef.current;

    window.dispatchEvent(new CustomEvent('qa:stop'));
    stoppedRef.current = true;
    activeRunIdRef.current = null;

    streamingMsgIdRef.current = null;
    lastChunkRef.current = '';
    refsSectionAddedRef.current = false;
    finalHandledRef.current = false;
    galleryAddedRef.current = false;
    galleryMsgIdRef.current = null;

    setTickerVisible(false);

    if (sid) {
      setMessages((prev) => {
        const list = [...prev];
        const idx = list.findIndex((m) => m.id === sid);
        if (idx >= 0) {
          const cur = list[idx];
          if (cur?.role === 'assistant' && typeof cur?.content === 'string' && !cur.content.trim()) {
            list.splice(idx, 1);
          }
        }
        return list;
      });
    }
  }, []);

  useEffect(() => {
    const hardResetStreamState = () => {
      stoppedRef.current = true;
      activeRunIdRef.current = null;
      streamingMsgIdRef.current = null;
      lastChunkRef.current = '';
      refsSectionAddedRef.current = false;
      finalHandledRef.current = false;
      galleryAddedRef.current = false;
      galleryMsgIdRef.current = null;
      setTickerVisible(false);
      setTickerStep('Question Analysis');
      setTickerHint('Analyzing your question and search intent.');
      setTickerProgress(1);
    };

    const onNew = () => {
      window.dispatchEvent(new CustomEvent('qa:stop'));

      hardResetStreamState();

      setMessages([]);
      setSessionId(null);

      setMode('rag');
      setSelectedAgent('KB Question & Answer');
      setSource('vault');
      setActiveDoc(null);

      setInputResetNonce((n) => n + 1);
      setTimeout(scrollToBottom, 0);
    };

    window.addEventListener('qa:new', onNew);
    return () => window.removeEventListener('qa:new', onNew);
  }, [scrollToBottom]);

  const renderChatBody = useCallback(() => {
    return (
      <>
        <div className={`assistant-canvas ${messages.length ? 'fill' : 'center'}`}>
          {!messages.length ? (
            <ChatHero
              agentName={selectedAgent}
              exampleQuestions={AGENT_EXAMPLE_QUESTIONS[selectedAgent] || []}
              onPickQuestion={(q, _item, idx) => {
                if (isStreaming) return;
                const forcedKbName =
                  selectedAgent === 'KB Question & Answer'
                    ? KB_QA_FORCED_KB_BY_EXAMPLE_INDEX[idx]
                    : null;
                if (forcedKbName) {
                  setSource('vault');
                  try {
                    window.dispatchEvent(
                      new CustomEvent('kb:vault-force-select', {
                        detail: { name: forcedKbName },
                      })
                    );
                  } catch {}
                  submitText(q, { forceVault: { name: forcedKbName } });
                  return;
                }
                submitText(q);
              }}
            />
          ) : (
            <ChatBody
              messages={messages}
              tickerVisible={tickerVisible}
              tickerStep={tickerStep}
              tickerHint={tickerHint}
              tickerProgress={tickerProgress}
              streamingMsgId={streamingMsgIdRef.current}
              finalHandled={finalHandledRef.current}
              lastAssistantId={lastAssistantIdRef.current}
              scrollRef={scrollRef}
              bottomRef={bottomRef}
              openDoc={openDoc}
              openDocByTitle={openDocByTitle}
              normalizeMdLite={normalizeMdLite}
            />
          )}
        </div>

        <InputArea
          showKBSelector={selectedAgent === 'KB Question & Answer'}
          kbChipLabel={kbChipLabel}
          onChangeKBSelect={onChangeKBSelect}
          vaultKbId={vault?.kbId}
          activeDocDocId={hasActiveDoc ? (activeDoc?.docId ?? activeDoc?.objectId) : null}
          bookmarkDocCount={bookmarkDocIds.length}
          isStreaming={isStreaming}
          onSubmitText={submitText}
          onStop={stopStreaming}
          resetNonce={inputResetNonce}
        />
      </>
    );
  }, [
    messages,
    tickerVisible,
    tickerStep,
    tickerHint,
    tickerProgress,
    isStreaming,
    submitText,
    selectedAgent,
    kbChipLabel,
    onChangeKBSelect,
    vault,
    activeDoc,
    hasActiveDoc,
    bookmarkDocIds,
    openDoc,
    openDocByTitle,
    stopStreaming,
    inputResetNonce,
  ]);

  return (
    <aside className={`right-rail ${rightOpen ? 'open' : 'closed'}`}>
      <div className="right-rail-topbar">
        <div className="right-rail-app-title">{selectedAgent || 'KB Question & Answer'}</div>
      </div>
      <div className="right-rail-body">{renderChatBody()}</div>
    </aside>
  );
}
