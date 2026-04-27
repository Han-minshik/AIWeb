function toNullable(value) {
  return value == null ? null : value;
}

function toNullableString(value) {
  if (value == null) return null;
  const text = String(value).trim();
  return text || null;
}

export function toSafeDocTitle(title, docId = null, objectId = null) {
  const text = toNullableString(title);
  if (text) return text;
  if (docId != null) return `Doc ${docId}`;
  if (objectId != null) return `Doc ${objectId}`;
  return 'Doc';
}

export function normalizeOpenDocTabDetail(detail = {}) {
  const normalized = {
    ...detail,
    docId: toNullable(detail.docId),
    objectId: toNullable(detail.objectId),
    page: toNullable(detail.page),
    title: toNullableString(detail.title),
    docTitle: toNullableString(detail.docTitle),
    docRelPath: toNullableString(detail.docRelPath),
    docSubPath: toNullableString(detail.docSubPath),
    docFileRight: toNullableString(detail.docFileRight),
    docStoredPath: toNullableString(detail.docStoredPath),
    docBranchType: toNullableString(detail.docBranchType),
    forceNewTab: detail.forceNewTab === true,
  };

  if (!normalized.title) {
    normalized.title = toSafeDocTitle(
      normalized.docTitle,
      normalized.docId,
      normalized.objectId
    );
  }
  if (!normalized.docTitle) {
    normalized.docTitle = normalized.title;
  }

  return normalized;
}

export function hasOpenDocIdentity(detail = {}) {
  const d = normalizeOpenDocTabDetail(detail);
  return Boolean(d.docId != null || d.objectId != null || d.docTitle);
}

export function readOpenDocTabDetail(event) {
  return normalizeOpenDocTabDetail(event?.detail || {});
}

export function dispatchOpenDocTab(detail = {}) {
  const d = normalizeOpenDocTabDetail(detail);
  if (!hasOpenDocIdentity(d)) return false;
  window.dispatchEvent(
    new CustomEvent('open-doc-tab', { detail: d })
  );
  return true;
}
