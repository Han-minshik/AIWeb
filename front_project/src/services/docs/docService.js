import { http } from '../http/client';
import { reportClientError } from '../errors/errorReporter';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export const DOCUMENT_UPLOAD_ACCEPT =
  '.pdf,.doc,.docx,.ppt,.pptx,.hwp,.hwpx,.xls,.xlsx,.csv,.txt,.md';
const ALLOWED_DOCUMENT_EXTENSIONS = new Set(
  DOCUMENT_UPLOAD_ACCEPT.split(',').map((value) => value.slice(1))
);

function assertAllowedDocumentFile(file) {
  const name = String(file?.name || '').trim();
  const dot = name.lastIndexOf('.');
  const extension = dot >= 0 ? name.slice(dot + 1).toLowerCase() : '';
  if (!ALLOWED_DOCUMENT_EXTENSIONS.has(extension)) {
    throw new Error('Unsupported document type. Allowed: ' + DOCUMENT_UPLOAD_ACCEPT);
  }
}

function newUploadRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16);
    return (token === 'x' ? value : ((value & 0x3) | 0x8)).toString(16);
  });
}

export async function uploadDocToMinio(file, {
  parent_node_id,
  branch_type = 'general',
  kiwixAssets = [],
  kiwixMetadata = null,
  kiwixStoredFileName = null,
  onProgress,
  onNameConflict,
} = {}) {
  if (!(file instanceof File)) throw new Error('A file is required.');
  assertAllowedDocumentFile(file);
  if (parent_node_id == null) throw new Error('parent_node_id is required.');
  const normalizedBranch = String(branch_type || '').trim().toLowerCase();
  const isKiwixMarkdown = normalizedBranch === 'kiwix' && /[.]md$/i.test(file.name);
  if (isKiwixMarkdown) {
    const form = new FormData();
    form.append('upload_request_id', newUploadRequestId());
    form.append('parent_node_id', String(Number(parent_node_id)));
    form.append('markdown', file, kiwixStoredFileName || file.name);
    form.append('metadata_json', JSON.stringify(kiwixMetadata || {}));
    const assetPaths = [];
    for (const asset of kiwixAssets || []) {
      if (!(asset?.file instanceof File) || !asset?.path) continue;
      form.append('assets', asset.file, asset.file.name);
      assetPaths.push(String(asset.path));
    }
    form.append('asset_paths_json', JSON.stringify(assetPaths));
    return await http.post('/docs/uploads/kiwix/preparsed', form, {
      timeout: 300000,
      gatewayRetries: 3,
      gatewayRetryDelay: 1000,
    });
  }
  let initialized;
  try {
    initialized = await http.post('/docs/uploads/init', {
      upload_request_id: newUploadRequestId(),
      parent_node_id: Number(parent_node_id),
      file_name: file.name,
      file_size: file.size,
      content_type: file.type || 'application/octet-stream',
      branch_type,
      kiwix_metadata: normalizedBranch === 'kiwix' ? kiwixMetadata : null,
    });
  } catch (error) {
    const detail = error?.detail;
    if (
      detail?.code === 'DOCUMENT_NAME_CONFLICT' &&
      detail?.retryable_as_version &&
      Number.isFinite(Number(detail?.object_id))
    ) {
      const approved = typeof onNameConflict === 'function'
        ? await onNameConflict({
            file,
            fileName: detail.file_name || file.name,
            objectId: Number(detail.object_id),
            nodeId: Number(detail.node_id),
          })
        : false;
      if (approved) {
        const version = await uploadVersion(Number(detail.object_id), file, { onProgress });
        return { ...version, uploaded_as_version: true };
      }
      const cancelled = new Error(`Upload cancelled because "${detail.file_name || file.name}" already exists.`);
      cancelled.code = 'UPLOAD_CANCELLED';
      throw cancelled;
    }
    throw error;
  }
  if (initialized?.storage_status === 'READY') return initialized;
  return await http.upload(initialized.upload_url, file, {
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    onProgress,
    timeout: 0,
  });
}

function selectedFilePath(file) {
  return String(file?.webkitRelativePath || file?.name || '')
    .replace(/\\/g, '/')
    .replace(/^\/+/, '');
}

function normalizedPathKey(value) {
  return String(value || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '').toLocaleLowerCase();
}

function cleanManifestText(value) {
  return String(value ?? '').trim();
}

function manifestField(raw, ...names) {
  const normalized = new Map(
    Object.entries(raw || {}).map(([key, value]) => [
      String(key).replace(/[^a-z0-9]/gi, '').toLocaleLowerCase(),
      value,
    ])
  );
  for (const name of names) {
    const key = String(name).replace(/[^a-z0-9]/gi, '').toLocaleLowerCase();
    if (normalized.has(key)) return normalized.get(key);
  }
  return undefined;
}

function normalizeManifestDate(value) {
  const raw = cleanManifestText(value);
  if (!raw) return '';
  const compact = raw.match(/^(\d{4})(\d{2})(\d{2})(?:[ T]?(\d{2})(\d{2})(\d{2})?)?$/);
  const separated = raw.match(
    /^(\d{4})\D+(\d{1,2})\D+(\d{1,2})(?:\D+(\d{1,2})\D+(\d{1,2})(?:\D+(\d{1,2}))?)?/
  );
  const match = compact || separated;
  if (!match) return raw;
  const [, year, month, day, hour = '', minute = '', second = ''] = match;
  const parts = [year, month.padStart(2, '0'), day.padStart(2, '0')];
  if (!hour) return parts.join('-');
  return `${parts.join('-')} ${hour.padStart(2, '0')}:${minute.padStart(2, '0')}:${(second || '0').padStart(2, '0')}`;
}

function optionalRawManifestString(raw, fieldName) {
  const value = manifestField(raw, fieldName);
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string') {
    throw new Error(`${fieldName} must be a string or null.`);
  }
  return value;
}

function normalizeKiwixManifestRow(raw, index, manifestName, defaultProjectCode = '') {
  if (!raw || typeof raw !== 'object') {
    throw new Error(`${manifestName} row ${index + 1} is invalid.`);
  }
  const rawProjectCode = cleanManifestText(manifestField(raw, 'folderName', 'prjCode'));
  if (
    defaultProjectCode &&
    rawProjectCode &&
    rawProjectCode.toLocaleLowerCase() !== defaultProjectCode.toLocaleLowerCase()
  ) {
    throw new Error(
      `${manifestName} row ${index + 1} has folderName "${rawProjectCode}"; expected "${defaultProjectCode}".`
    );
  }
  const fileExt = cleanManifestText(manifestField(raw, 'fileExt') || 'md')
    .replace(/^\./, '')
    .toLowerCase();
  let fileName = cleanManifestText(manifestField(raw, 'fileName'));
  const suffix = `.${fileExt || 'md'}`;
  if (fileName.toLocaleLowerCase().endsWith(suffix.toLocaleLowerCase())) {
    fileName = fileName.slice(0, -suffix.length);
  }
  const metadata = {
    section: cleanManifestText(manifestField(raw, 'section')),
    platform: cleanManifestText(manifestField(raw, 'platform') || 'KIWIX').toUpperCase(),
    prjCode: rawProjectCode || defaultProjectCode,
    prjName: cleanManifestText(manifestField(raw, 'prjName')),
    itemtypeName: cleanManifestText(manifestField(raw, 'itemtypeName')),
    docCode: cleanManifestText(manifestField(raw, 'docCode')),
    fileName,
    fileExt: fileExt || 'md',
    fileCreate: normalizeManifestDate(manifestField(raw, 'fileCreate')),
    schema: optionalRawManifestString(raw, 'schema'),
    stage: optionalRawManifestString(raw, 'stage'),
  };
  if (metadata.platform && metadata.platform !== 'KIWIX') return null;
  if (!metadata.prjCode || !metadata.fileName || !metadata.fileCreate) {
    throw new Error(`${manifestName} row ${index + 1} requires folderName, fileName, and fileCreate.`);
  }
  return metadata;
}

function findManifestFile(metadata, candidates, used = new Set()) {
  const fileLeaf = `${metadata.fileName}.${metadata.fileExt || 'md'}`;
  const folderLeaf = metadata.docCode ? `${metadata.docCode}_${metadata.fileName}` : metadata.fileName;
  const expectedSuffix = normalizedPathKey(`${metadata.prjCode}/${folderLeaf}/${fileLeaf}`);
  const exact = candidates.filter((file) => {
    if (used.has(file)) return false;
    const path = normalizedPathKey(selectedFilePath(file));
    return path === expectedSuffix || path.endsWith(`/${expectedSuffix}`);
  });
  if (exact.length === 1) return exact[0];

  const projectSegment = normalizedPathKey(metadata.prjCode);
  const folderPrefix = normalizedPathKey(metadata.docCode ? `${metadata.docCode}_` : '');
  const leafKey = normalizedPathKey(fileLeaf);
  const fallback = candidates.filter((file) => {
    if (used.has(file)) return false;
    const parts = normalizedPathKey(selectedFilePath(file)).split('/');
    if (parts.at(-1) !== leafKey || !parts.includes(projectSegment)) return false;
    return !folderPrefix || parts.some((part) => part.startsWith(folderPrefix));
  });
  return fallback
    .sort((left, right) => selectedFilePath(left).localeCompare(selectedFilePath(right)))
    .at(0) || null;
}

function parentFolderName(file) {
  const directory = directoryOf(selectedFilePath(file));
  return directory.split('/').filter(Boolean).at(-1) || '';
}

export async function planKiwixManifestSelection(files) {
  const selected = Array.from(files || []);
  const manifests = selected.filter((file) => {
    if (!/[.]json$/i.test(String(file.name || ''))) return false;
    const relativePath = String(file?.webkitRelativePath || '').replace(/\\/g, '/');
    if (!relativePath) return true;
    return relativePath.split('/').filter(Boolean).length === 2;
  });
  const manifestSet = new Set(manifests);
  const candidates = selected.filter((file) => !manifestSet.has(file));
  const entries = [];
  const missing = [];
  const issues = [];
  const used = new Set();

  for (const manifest of manifests) {
    const manifestName = String(manifest.name || 'manifest.json');
    const isLegacyManifest = manifestName.toLocaleLowerCase() === 'documents.json';
    const defaultProjectCode = isLegacyManifest ? '' : manifestName.replace(/[.]json$/i, '').trim();
    let parsed;
    try {
      parsed = JSON.parse((await manifest.text()).replace(/^\uFEFF/, ''));
    } catch {
      issues.push({
        stage: 'manifest', code: 'INVALID_MANIFEST_JSON', fileName: manifestName,
        reason: `${manifestName} is not valid UTF-8 JSON.`,
      });
      continue;
    }
    const rows = Array.isArray(parsed) ? parsed : parsed?.documents;
    if (!Array.isArray(rows)) {
      issues.push({
        stage: 'manifest', code: 'INVALID_MANIFEST_DOCUMENTS', fileName: manifestName,
        reason: `${manifestName} must contain an array of documents.`,
      });
      continue;
    }

    rows.forEach((raw, index) => {
      let metadata;
      try {
        metadata = normalizeKiwixManifestRow(raw, index, manifestName, defaultProjectCode);
      } catch (error) {
        issues.push({
          stage: 'manifest', code: 'INVALID_MANIFEST_ROW', fileName: manifestName,
          row: index + 1, reason: error?.message || String(error),
        });
        return;
      }
      if (!metadata) return;
      const file = findManifestFile(metadata, candidates, used);
      if (!file) {
        const folderLeaf = metadata.docCode
          ? `${metadata.docCode}_${metadata.fileName}`
          : metadata.fileName;
        missing.push(`${metadata.prjCode}/${folderLeaf}/${metadata.fileName}.${metadata.fileExt}`);
        issues.push({
          stage: 'matching', code: 'KIWIX_MARKDOWN_NOT_FOUND',
          fileName: `${metadata.fileName}.${metadata.fileExt}`,
          reason: `No selected Markdown matched ${metadata.prjCode}/${folderLeaf}/${metadata.fileName}.${metadata.fileExt}.`,
        });
        return;
      }
      used.add(file);
      entries.push({ file, metadata, manifestFile: manifest });
    });
  }

  const duplicateNames = new Map();
  for (const entry of entries) {
    const key = `${entry.metadata.prjCode}/${entry.file.name}`.toLocaleLowerCase();
    duplicateNames.set(key, (duplicateNames.get(key) || 0) + 1);
  }
  for (const entry of entries) {
    const key = `${entry.metadata.prjCode}/${entry.file.name}`.toLocaleLowerCase();
    if ((duplicateNames.get(key) || 0) <= 1) continue;
    const folderName = parentFolderName(entry.file);
    if (folderName) entry.storedFileName = `${folderName}.${entry.metadata.fileExt || 'md'}`;
  }
  return { entries, missing, issues, assetPool: candidates, manifestFiles: manifests };
}

function directoryOf(path) {
  const normalized = String(path || '').replace(/\\/g, '/');
  const index = normalized.lastIndexOf('/');
  return index >= 0 ? normalized.slice(0, index) : '';
}

function normalizeKiwixAssetReference(value) {
  const raw = String(value || '')
    .trim()
    .replace(/^<|>$/g, '')
    .split(' "', 1)[0]
    .replace(/\\/g, '/')
    .replace(/^\.\//, '');
  if (!raw || /^(?:https?:|data:)/i.test(raw)) return null;
  if (raw.startsWith('/') || /^[a-z]:\//i.test(raw)) return { invalid: true, path: raw };
  const parts = raw.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..')) return { invalid: true, path: raw };
  return { invalid: false, path: parts.join('/') };
}

async function resolveKiwixMarkdownAssets(markdownFile, assetPool) {
  const markdownText = await markdownFile.text();
  const references = [];
  const referenceTargets = [];
  const markdownImage = /!\[[^\]]*\]\(([^)]+)\)/g;
  const htmlImage = /<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>/gi;
  let match;
  while ((match = markdownImage.exec(markdownText)) !== null) referenceTargets.push(match[1]);
  while ((match = htmlImage.exec(markdownText)) !== null) referenceTargets.push(match[1]);
  for (const target of referenceTargets) {
    const reference = normalizeKiwixAssetReference(target);
    if (!reference) continue;
    if (reference.invalid) {
      const error = new Error(`Unsupported KIWIX image path: ${reference.path}`);
      error.detail = {
        code: 'KIWIX_ASSET_PATH_UNSUPPORTED',
        file_name: markdownFile.name,
        markdown_path: selectedFilePath(markdownFile),
        reference_path: reference.path,
        stage: 'asset-preflight',
        reason: `Use a relative image path inside the selected folder: ${reference.path}`,
        retryable: true,
      };
      throw error;
    }
    if (!references.includes(reference.path)) references.push(reference.path);
  }

  const candidates = Array.from(assetPool || []).filter((file) => file !== markdownFile);
  const byPath = new Map(candidates.map((file) => [selectedFilePath(file).toLocaleLowerCase(), file]));
  const markdownDirectory = directoryOf(selectedFilePath(markdownFile));
  const assets = [];

  for (const referencePath of references) {
    const lookupReference = (() => {
      try {
        return decodeURIComponent(referencePath);
      } catch {
        return referencePath;
      }
    })();
    const expectedPath = [markdownDirectory, lookupReference].filter(Boolean).join('/');
    let assetFile = byPath.get(expectedPath.toLocaleLowerCase());

    if (!assetFile) {
      const baseName = lookupReference.split('/').pop().toLocaleLowerCase();
      const sameName = candidates.filter((file) => String(file.name || '').toLocaleLowerCase() === baseName);
      if (sameName.length === 1) assetFile = sameName[0];
    }

    if (!assetFile) {
      const error = new Error(`KIWIX image was not selected: ${referencePath}`);
      error.detail = {
        code: 'KIWIX_ASSET_NOT_SELECTED',
        file_name: markdownFile.name,
        markdown_path: selectedFilePath(markdownFile),
        reference_path: referencePath,
        stage: 'asset-preflight',
        reason: `Select the folder containing the Markdown and image: ${referencePath}`,
        retryable: true,
      };
      error.matchedAssets = assets.slice();
      throw error;
    }
    assets.push({ file: assetFile, path: referencePath });
  }
  return assets;
}

export async function uploadDocsToMinio(files, options = {}) {
  const originalList = Array.from(files || []);
  const branch = String(options.branch_type || '').trim().toLowerCase();
  const assetPool = Array.from(options.kiwixAssetPool || originalList);
  const markdownPlans = new Map();
  const planningErrors = new Map();
  const referencedAssets = new Set();

  if (branch === 'kiwix') {
    for (const file of originalList.filter((item) => /[.]md$/i.test(item.name))) {
      try {
        const assets = await resolveKiwixMarkdownAssets(file, assetPool);
        markdownPlans.set(file, assets);
        assets.forEach((asset) => referencedAssets.add(asset.file));
      } catch (error) {
        planningErrors.set(file, error);
        for (const asset of error?.matchedAssets || []) referencedAssets.add(asset.file);
      }
    }
  }

  const list = branch === 'kiwix'
    ? originalList.filter((file) => /[.]md$/i.test(file.name) || !referencedAssets.has(file))
    : originalList;
  const concurrency = branch === 'kiwix'
    ? 1
    : Math.max(1, Math.min(2, Number(options.concurrency) || 2));
  const results = new Array(list.length);
  let cursor = 0;
  const worker = async () => {
    while (cursor < list.length) {
      const index = cursor;
      cursor += 1;
      const file = list[index];
      options.onItemChange?.(index, { file, status: 'uploading', percent: 0 });
      try {
        if (planningErrors.has(file)) throw planningErrors.get(file);
        const result = await uploadDocToMinio(file, {
          ...options,
          parent_node_id: options.parentNodeIdByFile?.get(file) ?? options.parent_node_id,
          kiwixAssets: markdownPlans.get(file) || [],
          kiwixMetadata: options.kiwixMetadataByFile?.get(file) || null,
          kiwixStoredFileName: options.kiwixStoredFileNameByFile?.get(file) || null,
          onProgress: (progress) => options.onItemChange?.(index, {
            file,
            status: 'uploading',
            percent: progress.percent,
            loaded: progress.loaded,
            total: progress.total,
          }),
        });
        results[index] = { success: true, file, result };
        options.onItemChange?.(index, { file, status: 'ready', percent: 100, result });
      } catch (error) {
        results[index] = { success: false, file, error };
        options.onItemChange?.(index, { file, status: 'failed', error });
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, list.length) }, worker));
  return results;
}
export const listDocs = async (params = {}) => {
  const res = await http.get('/docs', params);
  if (Array.isArray(res)) return res;
  if (res?.items) return res.items;
  return [];
};

export const getDoc = async (docId) => await http.get(`/docs/${docId}`);
export const patchDoc = async (docId, body) => await http.patch(`/docs/${docId}`, body);

export const resolveAiDocumentIds = async (objectIds = []) =>
  await http.post('/docs/ai-document-ids', { object_ids: objectIds });

export async function uploadVersion(docId, file, { onProgress } = {}) {
  if (!(file instanceof File)) throw new Error('A file is required.');
  assertAllowedDocumentFile(file);
  if (docId == null) throw new Error('docId is required.');
  const initialized = await http.post(`/docs/uploads/${Number(docId)}/versions/init`, {
    upload_request_id: newUploadRequestId(),
    file_name: file.name,
    file_size: file.size,
    content_type: file.type || 'application/octet-stream',
  });
  if (initialized?.storage_status === 'READY') return initialized;
  return await http.upload(initialized.upload_url, file, {
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    onProgress,
    timeout: 0,
  });
}

export const listDocRevisions = async (docId) =>
  await http.get(`/docs/${docId}/revisions`);

export const deleteDocRevision = async (docId, revisionId) =>
  await http.delete(`/docs/${docId}/revisions/${revisionId}`);

export const reorderDocRevisions = async (docId, revision_ids) =>
  await http.post(`/docs/${docId}/revisions/reorder`, { revision_ids });

export const deleteDoc = async (docId, mode = 'all') =>
  await http.post(`/docs/${docId}/delete`, { mode });

export const getDocsTree = async (group_by = 'none', kb_id) => {
  const params = { group_by };
  if (kb_id) params.kb_id = kb_id;
  return await http.get('/docs-tree/tree-data', params);
};

export const copyDoc = async (docId, { new_name, parent_node_id = null, tree_id = null } = {}) =>
  await http.post(`/docs/${docId}/copy`, { new_name, parent_node_id, tree_id });

export const copyDocToPersonal = async (docId, { new_name, parent_node_id }) =>
  await http.post(`/docs/${docId}/copy-to-personal`, { new_name, parent_node_id });

export const moveDoc = async (docId, opts = {}) => {
  const {
    new_parent,
    new_parent_node,
    new_tree,
    new_tree_id,
  } = opts || {};

  const body = {};
  if (new_parent_node != null) body.new_parent_node = new_parent_node;
  else if (new_parent != null) body.new_parent_node = new_parent;

  if (new_tree != null) body.new_tree = new_tree;
  else if (new_tree_id != null) body.new_tree = new_tree_id;

  return await http.post(`/docs/${docId}/move`, body);
};

export const createNote = async ({
  title,
  content = '',
  parent_node_id = null,
  tree_id = null,
}) =>
  await http.post('/docs/create-note', { title, content, parent_node_id, tree_id });

export const reorderDocs = async (treeId, parentNodeId, orders) =>
  await http.post('/docs/reorder', { tree_id: treeId, parent: parentNodeId, orders });

export const listFilesByTree = async (kb_id, parent_id = null, q = '') => {
  const params = { kb_id: Number(kb_id) };
  if (parent_id != null) params.parent_id = Number(parent_id);
  const qq = String(q || '').trim();
  if (qq) params.q = qq;

  const res = await http.get('/docs-tree/files', params);
  const items = Array.isArray(res) ? res : (res?.items || []);
  return items;
};

export const getDocMeta = async (docId) => {
  if (docId == null) throw new Error('docId is required.');
  const idStr = String(docId).trim();
  if (!/^\d+$/.test(idStr)) {
    throw new Error('docId must be a numeric string.');
  }
  return await http.get(`/docs-embed/by-id/${idStr}`);
};

export const getDocMetaByObjectId = async (objectId) => {
  if (objectId == null) throw new Error('objectId is required.');
  const idStr = String(objectId).trim();
  if (!/^\d+$/.test(idStr)) {
    throw new Error('objectId must be a numeric string.');
  }
  return await http.get(`/docs-embed/by-object/${idStr}`);
};

export const getDocMetaByAiDocumentId = async (aiDocumentId) => {
  const aid = String(aiDocumentId ?? '').trim();
  if (!aid) throw new Error('aiDocumentId is required.');
  return await http.get(`/docs-embed/by-ai-document/${encodeURIComponent(aid)}`);
};

export const getDocMetaByName = async (name, opts = {}) => {
  const title = String(name ?? '').trim();
  if (!title) throw new Error('name is required.');
  const params = { name: title };
  if (opts?.relative_path) params.relative_path = opts.relative_path;
  if (opts?.sub_path) params.sub_path = opts.sub_path;
  if (opts?.file_right) params.file_right = opts.file_right;
  return await http.get('/docs-embed/by-name', params);
};

export const getDocMetaByStoredPath = async (storedPath) => {
  const normalized = String(storedPath ?? '').replace(/\\/g, '/').replace(/^\/+/, '');
  if (!normalized) throw new Error('storedPath is required.');
  const encoded = encodeURIComponent(normalized);
  return await http.get(`/docs-embed/${encoded}`);
};

export const getSynapJob = async ({
  doc_id = null,
  object_id = null,
  ai_document_id = null,
  name = null,
  stored_path = null,
  relative_path = null,
  sub_path = null,
  file_right = null,
  branch_type = null,
  page = null,
  theme = null,
} = {}) => {
  const params = {};
  if (doc_id != null) params.doc_id = doc_id;
  if (object_id != null) params.object_id = object_id;
  if (ai_document_id) params.ai_document_id = ai_document_id;
  if (name) params.name = name;
  if (stored_path) params.stored_path = stored_path;
  if (relative_path) params.relative_path = relative_path;
  if (sub_path) params.sub_path = sub_path;
  if (file_right) params.file_right = file_right;
  if (branch_type) params.branch_type = branch_type;
  if (page != null) params.page = page;
  if (theme) params.theme = theme;
  return await http.get('/docs-embed/synap/job', params);
};

export const lookupUploadByTitle = async (title) => {
  const res = await http.get(`/uploads/lookup?title=${encodeURIComponent(title)}`);
  return res;
};

export async function listBookmarkedDocs(q) {
  return await http.get('/docs/bookmarks/search', { q });
}

function buildAuthHeaders(extra = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
  const userId = (typeof window !== 'undefined' && localStorage.getItem('x_user_id')) || '1';
  const username = (typeof window !== 'undefined' && localStorage.getItem('x_username')) || 'demo';
  return {
    'X-User-Id': userId,
    'X-Username': username,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

function parseDownloadFilename(contentDisposition, fallbackName) {
  const raw = String(contentDisposition || '');
  const encodedMatch = /filename\*=UTF-8''([^;]+)/i.exec(raw);
  if (encodedMatch?.[1]) {
    try {
      return decodeURIComponent(encodedMatch[1]);
    } catch {}
  }
  const match = /filename="(.+?)"/.exec(raw);
  return match ? match[1] : fallbackName;
}

export async function downloadLatestDocBlob(docId) {
  if (docId == null) throw new Error('docId is required.');
  const url = `${API_BASE}/docs/${docId}/download?as_attachment=true`;
  const fallbackName = `file_${docId}`;

  const res = await fetch(url, { headers: buildAuthHeaders(), credentials: 'include' });
  if (!res.ok) {
    const err = new Error(`Download failed (HTTP ${res.status})`);
    err.status = res.status;
    err.path = url;
    reportClientError(err, { source: 'docs-download', path: url, status: res.status });
    throw err;
  }

  const blob = await res.blob();
  const filename = parseDownloadFilename(res.headers.get('Content-Disposition'), fallbackName);
  return { blob, filename };
}

export async function loadMarkdownByRoutes(candidateBaseRoutes, encodedPath, displayPathForError = '') {
  const routes = Array.isArray(candidateBaseRoutes) ? candidateBaseRoutes : [];
  const encoded = String(encodedPath || '').replace(/^\/+/, '');
  const attemptedPaths = [];

  for (const route of routes) {
    const path = `${String(route || '').replace(/\/+$/, '')}/${encoded}`;
    const abs = `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
    attemptedPaths.push(`${route}/${displayPathForError || encoded}`);
    const res = await fetch(abs, {
      headers: buildAuthHeaders({ Accept: 'text/markdown, text/plain;q=0.9' }),
      credentials: 'include',
    });
    if (!res.ok) continue;

    const text = await res.text();
    return { text: text || '', route: String(route || '') };
  }

  const err = new Error('Failed to load markdown file.');
  err.attemptedPaths = attemptedPaths;
  err.path = displayPathForError || encoded;
  reportClientError(err, { source: 'docs-markdown', path: err.path });
  throw err;
}
