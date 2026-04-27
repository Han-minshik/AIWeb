function toStringOrEmpty(v) {
  return String(v ?? '').trim();
}

function toNumberList(v) {
  if (!Array.isArray(v)) return [];
  return v
    .map((x) => Number(x))
    .filter((x) => Number.isFinite(x));
}

export function resolveDocIdFromDetail(detail = {}) {
  const rawDocId = detail?.objectId ?? detail?.docId ?? null;
  if (rawDocId && typeof rawDocId === 'object') {
    return (
      rawDocId.object_id ??
      rawDocId.objectId ??
      rawDocId.storage_object_id ??
      rawDocId.storageObjectId ??
      rawDocId.doc_id ??
      rawDocId.docId ??
      rawDocId.doc_master_id ??
      rawDocId.docMasterId ??
      null
    );
  }
  return rawDocId;
}

export function buildQaSubmitContext(detail = {}, resolveAppType) {
  const text = toStringOrEmpty(detail?.text);
  const cat = toStringOrEmpty(detail?.cat);
  const source = toStringOrEmpty(detail?.source);
  const selectedDocIds = toNumberList(detail?.selectedDocIds);
  const selectedDocIdsForBookmark = source === 'bookmark' ? selectedDocIds : [];
  const appType = resolveAppType(detail);
  const kbName = toStringOrEmpty(detail?.kbName);
  const kbId = detail?.kbId || null;
  const docId = resolveDocIdFromDetail(detail);

  return {
    text,
    cat,
    source,
    selectedDocIds,
    selectedDocIdsForBookmark,
    appType,
    kbName,
    kbId,
    docId,
  };
}
