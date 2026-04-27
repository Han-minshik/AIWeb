import { http } from '../http/client';

export async function listGroups() {
  return await http.get('/bookmarks/groups');
}

export async function createGroup(name) {
  return await http.post('/bookmarks/groups', { name });
}

export async function renameGroup(groupId, name) {
  return await http.patch(`/bookmarks/groups/${groupId}`, { name });
}

export async function deleteGroup(groupId) {
  return await http.delete(`/bookmarks/groups/${groupId}`);
}

export async function getBookmarkTree(groupId) {
  return await http.get(`/bookmarks/groups/${groupId}/tree`);
}

export async function createNode(groupId, data) {
  return await http.post(`/bookmarks/groups/${groupId}/nodes`, data);
}

export async function patchNode(nodeId, data) {
  return await http.patch(`/bookmarks/nodes/${nodeId}`, data);
}

export async function deleteNodes(groupId, nodeIds) {
  return await http.delete(
    `/bookmarks/groups/${groupId}/nodes:bulk`,
    { node_ids: nodeIds }
  );
}

export const addBookmark = async ({ target_type, target_id, title = null, preferred_node_id = null }) =>
  await http.post('/bookmarks', { target_type, target_id, title, preferred_node_id });

export const removeBookmark = async ({ target_type, target_id }) =>
  await http.delete('/bookmarks', { target_type, target_id });
