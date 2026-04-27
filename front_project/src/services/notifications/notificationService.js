import { http } from '../http/client';

export async function listNotifications(params = {}) {
  return await http.get('/notifications', params);
}

export async function markRead(ids = []) {
  return await http.post('/notifications/read', ids);
}

export async function getUnreadCount(user_id) {
  const rows = await listNotifications({ user_id, unread_only: true });
  return Array.isArray(rows) ? rows.length : 0;
}
