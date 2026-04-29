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

export async function listUsers({ q = '', page = 1, page_size = 20 } = {}) {
  const res = await http.get('/admin/users', { q, page, page_size });
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

export async function toggleAdmin(userId, isAdmin) {
  return updateUser(userId, { is_admin: !!isAdmin });
}

export async function resetPassword(userId, newPassword) {
  return await http.post(`/admin/users/${userId}/reset_password`, { password: newPassword });
}
