import { http } from '../http/client';
import { reportClientError } from '../errors/errorReporter';

const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8001';

export const uploadDoc = async (
  file,
  {
    doc_type,
    status = 'Draft',
    tree_id = null,
    parent_node_id = null,
    branch_type = null,
  } = {}
) => {
  const form = new FormData();
  form.append('file', file);
  form.append('doc_type', doc_type);
  form.append('status', status);
  if (tree_id) form.append('tree_id', tree_id);
  if (parent_node_id) form.append('parent_node_id', parent_node_id);
  if (branch_type) form.append('branch_type', String(branch_type).toUpperCase());
  return await http.post('/docs/upload', form);
};

export const listDocs = async (params = {}) => {
  const res = await http.get('/docs', params);
  if (Array.isArray(res)) return res;
  if (res?.items) return res.items;
  return [];
};

export const getDoc = async (docId) => await http.get(`/docs/${docId}`);
export const patchDoc = async (docId, body) => await http.patch(`/docs/${docId}`, body);

export const uploadVersion = async (docId, file) => {
  const form = new FormData();
  form.append('file', file);
  return await http.post(`/docs/${docId}/upload-version`, form);
};

export const listDocRevisions = async (docId) =>
  await http.get(`/docs/${docId}/revisions`);

export const reorderDocRevisions = async (docId, revision_ids) =>
  await http.post(`/docs/${docId}/revisions/reorder`, { revision_ids });

export const deleteDoc = async (docId, mode = 'all') =>
  await http.post(`/docs/${docId}/delete`, { mode });

export const listTags = async (docId) => await http.get(`/docs/${docId}/tags`);

export const setTags = async (docId, tags) =>
  await http.patch(`/docs/${docId}/tags`, { tags });

export const getDocsTree = async (group_by = 'none', kb_id) => {
  const params = { group_by };
  if (kb_id) params.kb_id = kb_id;
  return await http.get('/docs-tree/tree-data', params);
};

export const batchRename = async (objectIds, new_name_prefix) =>
  await http.patch('/docs/batch-rename', { object_ids: objectIds, new_name_prefix });

export const batchDelete = async (objectIds) =>
  await http.delete('/docs/batch', { object_ids: objectIds });

export const copyDoc = async (docId, { new_name, parent_node_id = null, tree_id = null } = {}) =>
  await http.post(`/docs/${docId}/copy`, { new_name, parent_node_id, tree_id });

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
  const match = /filename="(.+?)"/.exec(raw);
  return match ? decodeURIComponent(match[1]) : fallbackName;
}

export async function downloadLatestDocBlob(docId) {
  if (docId == null) throw new Error('docId is required.');
  const url = `${API_BASE}/docs/${docId}/download`;
  const fallbackName = `file_${docId}`;

  const res = await fetch(url, { headers: buildAuthHeaders() });
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
    const res = await fetch(abs, { headers: buildAuthHeaders({ Accept: 'text/markdown, text/plain;q=0.9' }) });
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
