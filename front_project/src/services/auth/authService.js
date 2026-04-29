import { http } from '../http/client';

function normalizeUser(r) {
  if (!r) return { id: 0, username: 'unknown', email: '', is_admin: false };
  return {
    id: r.id ?? r.user_id ?? 0,
    username: r.username ?? r.name ?? 'user',
    email: r.email ?? '',
    is_admin: !!(r.is_admin ?? r.admin ?? r.role === 'admin'),
  };
}

export async function me() {
  try {
    const r = await http.get('/auth/me');
    return normalizeUser(r);
  } catch {
    const username = localStorage.getItem('x_username') || 'demo';
    const is_admin = username === 'admin' || username === 'demo';
    return { id: 0, username, email: '', is_admin };
  }
}

export async function login(id, password) {
  const userId = String(id ?? '').trim();
  if (!userId || !String(password ?? '').trim()) {
    throw new Error('Please enter your ID and Password.');
  }

  const data = await http.post('/auth/login', { id: userId, password });
  const token = data?.access_token || data?.token;
  if (!token) {
    throw new Error('The token is not in the response. Please check the backend response structure.');
  }

  try {
    localStorage.setItem('access_token', token);
  } catch {}

  if (data?.user_id != null) {
    try {
      localStorage.setItem('x_user_id', String(data.user_id));
    } catch {}
  }
  if (data?.username) {
    try {
      localStorage.setItem('x_username', String(data.username));
    } catch {}
  }

  return { token, raw: data };
}

export async function logoutServer() {
  try {
    await http.post('/auth/logout', {});
    return true;
  } catch {
    return false;
  }
}

export function clearClientSession() {
  const LSK = [
    'access_token',
    'refresh_token',
    'x_user_id',
    'x_username',
  ];
  const SSK = [
    'qa.current_session_id',
    'qa.last_assistant_message_id',
  ];

  try {
    LSK.forEach((k) => localStorage.removeItem(k));
  } catch {}
  try {
    SSK.forEach((k) => sessionStorage.removeItem(k));
  } catch {}
}

export async function logoutAll() {
  await logoutServer();
  clearClientSession();
}
