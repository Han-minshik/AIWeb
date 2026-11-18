import { http } from './http/client';

const inFlightPurges = new Map();

export async function listGroups(params = {}) {
  const res = await http.get('/admin/groups', params);
  return Array.isArray(res) ? res : (res?.items || []);
}

export async function createGroup(payload) {
  return await http.post('/admin/groups', payload);
}

export async function updateGroup(groupId, patch) {
  return await http.patch(`/admin/groups/${groupId}`, patch);
}

export async function deactivateGroup(groupId) {
  return await http.delete(`/admin/groups/${groupId}`);
}

export async function listGroupMembers(groupId, params = {}) {
  const res = await http.get(`/admin/groups/${groupId}/members`, params);
  return Array.isArray(res) ? res : (res?.items || []);
}

export async function addGroupMember(groupId, payload) {
  return await http.post(`/admin/groups/${groupId}/members`, payload);
}

export async function updateGroupMember(groupId, userId, patch) {
  return await http.patch(`/admin/groups/${groupId}/members/${userId}`, patch);
}

export async function deactivateGroupMember(groupId, userId) {
  return await http.delete(`/admin/groups/${groupId}/members/${userId}`);
}

export async function listDeletedDocuments(status = 'pending') {
  const res = await http.get('/admin/deleted-documents', { status });
  return Array.isArray(res) ? res : (res?.items || []);
}

export async function restoreDeletedDocument(deletionId) {
  return await http.post(`/admin/deleted-documents/${deletionId}/restore`, {});
}

export function purgeDeletedDocument(deletionId) {
  const key = String(deletionId);
  const existing = inFlightPurges.get(key);
  if (existing) return existing;
  const request = http.post(`/admin/deleted-documents/${deletionId}/purge`, {})
    .finally(() => inFlightPurges.delete(key));
  inFlightPurges.set(key, request);
  return request;
}

export async function listAiIndexJobs(status = 'failed') {
  const res = await http.get('/pipeline/jobs', { status, scope: 'se', limit: 1000 });
  return Array.isArray(res) ? res : (res?.items || []);
}

export async function retryAiIndexJobs(jobIds) {
  return await http.post('/pipeline/jobs/retry', { job_ids: jobIds });
}


