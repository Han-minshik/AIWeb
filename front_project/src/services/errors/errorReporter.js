const API_BASE = import.meta.env.VITE_API_BASE || '/api';

function safeString(value, fallback = '') {
  try {
    if (value == null) return fallback;
    const text = String(value);
    return text || fallback;
  } catch {
    return fallback;
  }
}

export function extractErrorMessage(error, fallback = 'Unexpected error occurred.') {
  if (!error) return fallback;
  if (typeof error === 'string') return error;
  if (typeof error?.message === 'string' && error.message.trim()) return error.message.trim();
  if (typeof error?.error?.message === 'string' && error.error.message.trim()) return error.error.message.trim();
  return fallback;
}

export function emitUserError(message, meta = {}) {
  try {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(
      new CustomEvent('app:error', {
        detail: {
          message: safeString(message, 'Unexpected error occurred.'),
          requestId: safeString(meta?.requestId, ''),
          source: safeString(meta?.source, 'client'),
          timestamp: Date.now(),
        },
      })
    );
  } catch {}
}

export function reportClientError(error, context = {}) {
  const message = extractErrorMessage(error);
  const stack = safeString(error?.stack, '');
  const requestId = safeString(context?.requestId, '');
  const payload = {
    source: safeString(context?.source, 'frontend'),
    message,
    stack,
    meta: {
      ...context,
      href: typeof window !== 'undefined' ? window.location.href : '',
      userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      at: new Date().toISOString(),
    },
  };

  try {
    // Browser console visibility
    // eslint-disable-next-line no-console
    console.error('[APP_ERROR]', payload, error);
  } catch {}

  emitUserError(message, { requestId, source: payload.source });

  try {
    fetch(`${API_BASE}/errors/client`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
  } catch {}
}

