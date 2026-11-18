const API_BASE = import.meta.env.VITE_API_BASE || '/api';

function authHeaders() {
  return {
    'X-User-Id': localStorage.getItem('x_user_id') || '1',
    'X-Username': localStorage.getItem('x_username') || 'demo',
  };
}

export async function fetchDocAsText(docId) {
  const url = `${API_BASE}/docs/${docId}/download`;
  const res = await fetch(url, { headers: authHeaders(), credentials: 'include' });
  if (!res.ok) return null;

  const ct = (res.headers.get('content-type') || '').toLowerCase();
  try {
    if (ct.includes('text') || ct.includes('markdown') || ct.includes('json')) {
      return await res.text();
    }

    const maybeText = await res.text();

    if (maybeText.includes('\0')) {
      return null;
    }

    return maybeText;
  } catch {
    return null;
  }
}
