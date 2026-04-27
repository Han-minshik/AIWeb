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
  try {
    const res = await http.get('/admin/users', { q, page, page_size });
    return normalizeList(res);
  } catch {
    const res = await http.get('/users', { q, page, page_size });
    return normalizeList(res);
  }
}

export async function createUser(payload) {
  try {
    return await http.post('/admin/users', payload);
  } catch {
    return await http.post('/users', payload);
  }
}

export async function updateUser(userId, patch) {
  try {
    return await http.patch(`/admin/users/${userId}`, patch);
  } catch {
    return await http.patch(`/users/${userId}`, patch);
  }
}

export async function deleteUser(userId) {
  try {
    return await http.delete(`/admin/users/${userId}`);
  } catch {
    return await http.delete(`/users/${userId}`);
  }
}

export async function toggleAdmin(userId, isAdmin) {
  return updateUser(userId, { is_admin: !!isAdmin });
}

export async function resetPassword(userId, newPassword) {
  try {
    return await http.post(`/admin/users/${userId}/reset_password`, { password: newPassword });
  } catch {
    return await updateUser(userId, { password: newPassword });
  }
}
