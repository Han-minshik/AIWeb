import { http } from '../http/client';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export async function listNotifications(params = {}) {
  return await http.get('/notifications', params);
}

export async function markRead(ids = []) {
  return await http.post('/notifications/read', ids);
}

export async function markAllRead() {
  return await http.post('/notifications/read-all', {});
}

export async function deleteAllNotifications() {
  return await http.delete('/notifications/all');
}

export async function getUnreadCount() {
  const res = await http.get('/notifications/unread-count');
  return Number(res?.unread || 0);
}

export function subscribeNotifications({ onEvent, onError } = {}) {
  const source = new EventSource(`${API_BASE}/notifications/stream`, {
    withCredentials: true,
  });
  source.onmessage = (event) => {
    try {
      onEvent?.(JSON.parse(event.data || '{}'));
    } catch (error) {
      onError?.(error);
    }
  };
  source.onerror = (error) => onError?.(error);
  return () => source.close();
}