function toStringOrEmpty(v) {
  return String(v ?? '').trim();
}

function toIdList(v) {
  if (!Array.isArray(v)) return [];
  return v
    .map((x) => String(x ?? '').trim())
    .filter(Boolean);
}

function toNumberList(v) {
  const values = Array.isArray(v) ? v : v == null || v === '' ? [] : [v];
  return [...new Set(values.map(Number).filter(Number.isFinite))];
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
  const selectedDocIds = toIdList(detail?.selectedDocIds);
  const selectedDocIdsForBookmark = source === 'bookmark' ? selectedDocIds : [];
  const appType = resolveAppType(detail);
  const kbName = toStringOrEmpty(detail?.kbName);
  const kbId = detail?.kbId || null;
  const userId = detail?.userId ?? detail?.user_id ?? null;
  const rawItemTypeCode = detail?.itemTypeCode ?? detail?.item_type_code;
  const parsedItemTypeCode = Number(rawItemTypeCode);
  const itemTypeCode =
    rawItemTypeCode != null && rawItemTypeCode !== '' && Number.isFinite(parsedItemTypeCode)
      ? parsedItemTypeCode
      : null;
  const itemTypeCodes = toNumberList(
    detail?.itemTypeCodes ??
      detail?.item_type_codes ??
      detail?.itemTypeCode ??
      detail?.item_type_code
  );
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
    userId,
    itemTypeCode,
    itemTypeCodes,
    docId,
  };
}
