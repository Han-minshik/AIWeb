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

function persistUserMetadata(user) {
  if (!user) return;
  try {
    if (user.id != null && Number(user.id) > 0) {
      localStorage.setItem('x_user_id', String(user.id));
    }
    if (user.username) {
      localStorage.setItem('x_username', String(user.username));
    }
  } catch {}
}

export async function me() {
  const r = await http.get('/auth/me');
  const user = normalizeUser(r);
  persistUserMetadata(user);
  return user;
}

export async function login(id, password) {
  const userId = String(id ?? '').trim();
  if (!userId || !String(password ?? '').trim()) {
    throw new Error('Please enter your ID and Password.');
  }

  const data = await http.post('/auth/login', { id: userId, password });
  const user = normalizeUser(data?.user || data);

  // New browser sessions use the HttpOnly cookie set by the backend. Remove a
  // stale browser-stored token after a successful login, while the HTTP client
  // still supports pre-deployment legacy sessions until they sign in again.
  try {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  } catch {}

  persistUserMetadata(user);

  return { user, raw: data };
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

export async function createEntryToken(target) {
  const data = await http.post('/auth/entry-token/' + encodeURIComponent(target), {});
  if (!data?.token) throw new Error('Entry token was not returned.');
  return data.token;
}

export async function openEntryApp(url, target) {
  const popup = window.open('about:blank', '_blank');
  if (popup) popup.opener = null;

  try {
    const token = await createEntryToken(target);
    const launchUrl = String(url).split('#')[0] + '#t=' + encodeURIComponent(token);
    if (popup) popup.location.replace(launchUrl);
    else window.open(launchUrl, '_blank', 'noopener,noreferrer');
  } catch (error) {
    if (popup && !popup.closed) popup.close();
    throw error;
  }
}
