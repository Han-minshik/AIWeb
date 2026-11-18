import { http } from '../http/client';

function normalizeList(data) {
  if (!data) return { items: [], total: 0, page: 1, page_size: 20 };
  if (Array.isArray(data)) return { items: data, total: data.length, page: 1, page_size: data.length };
  return {
    items: data.items ?? data.rows ?? data.results ?? [],
    total: data.total ?? data.count ?? (data.items?.length ?? 0),
    page: data.page ?? 1,
    page_size: data.page_size ?? data.limit ?? 20,
  };
}

export async function listUsers({
  q = '',
  page = 1,
  page_size = 20,
  group_id = null,
  group_empty = false,
  position_id = null,
  position_empty = false,
  is_admin = null,
} = {}) {
  const params = { q, page, page_size };
  if (group_id != null && group_id !== '') params.group_id = group_id;
  if (group_empty) params.group_empty = true;
  if (position_id != null && position_id !== '') params.position_id = position_id;
  if (position_empty) params.position_empty = true;
  if (is_admin != null && is_admin !== '') params.is_admin = is_admin;
  const res = await http.get('/admin/users', params);
  return normalizeList(res);
}

export async function createUser(payload) {
  return await http.post('/admin/users', payload);
}

export async function updateUser(userId, patch) {
  return await http.patch(`/admin/users/${userId}`, patch);
}

export async function deleteUser(userId) {
  return await http.delete(`/admin/users/${userId}`);
}

export async function permanentlyDeleteUser(userId) {
  return await http.delete(`/admin/users/${userId}/permanent`);
}

export async function toggleAdmin(userId, isAdmin) {
  return updateUser(userId, { is_admin: !!isAdmin });
}

export async function listDepartments() {
  const res = await http.get('/admin/departments', {});
  return Array.isArray(res) ? res : (res?.items || []);
}

export async function createDepartment(name) {
  return await http.post('/admin/departments', { name });
}

export async function getGroupUsage(groupId) {
  return await http.get(`/admin/groups/${groupId}/usage`, {});
}

export async function listPositions() {
  const res = await http.get('/admin/positions', {});
  return Array.isArray(res) ? res : (res?.items || []);
}

export async function createPosition(name) {
  return await http.post('/admin/positions', { name });
}

export async function deletePosition(positionId) {
  return await http.delete(`/admin/positions/${positionId}`);
}
