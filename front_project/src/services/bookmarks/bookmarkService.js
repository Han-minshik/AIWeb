import { http } from '../http/client';

export async function getBookmarkTree() {
  return await http.get('/bookmarks/tree');
}

export async function patchNode(nodeId, data) {
  return await http.patch(`/bookmarks/nodes/${nodeId}`, data);
}

export async function createNode(data) {
  return await http.post('/bookmarks/nodes', data);
}

export async function deleteNodes(nodeIds) {
  return await http.delete(
    '/bookmarks/nodes:bulk',
    { node_ids: nodeIds }
  );
}

export const addBookmark = async ({
  storage_id = null,
  title = null,
  parent_id = null,
  // Backward compatibility for old callers.
  target_type = null,
  target_id = null,
  preferred_node_id = null,
} = {}) =>
  await http.post('/bookmarks', {
    storage_id,
    title,
    parent_id,
    target_type,
    target_id,
    preferred_node_id,
  });

export const removeBookmark = async ({
  storage_id = null,
  // Backward compatibility for old callers.
  target_type = null,
  target_id = null,
} = {}) =>
  await http.delete('/bookmarks', { storage_id, target_type, target_id });
