import { extractErrorMessage, reportClientError } from '../errors/errorReporter';
const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8001';
const DEFAULT_TIMEOUT = 300000;
const AUTO_REDIRECT_401 = true;

function getToken() {
  try {
    return typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
  } catch {
    return null;
  }
}

function baseHeaders(isJson, extra) {
  const token = getToken();
  const h = {
    'X-User-Id': (typeof window !== 'undefined' && localStorage.getItem('x_user_id')) || '1',
    'X-Username': (typeof window !== 'undefined' && localStorage.getItem('x_username')) || 'demo',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(extra || {}),
  };
  if (isJson) h['Content-Type'] = 'application/json';
  return h;
}

function buildURL(path, params) {
  const url = new URL(path.startsWith('http') ? path : `${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v == null || v === '') return;
      if (Array.isArray(v)) url.searchParams.set(k, v.join(','));
      else url.searchParams.set(k, String(v));
    });
  }
  return url;
}

async function handleResponse(res) {
  if (res.status === 401) {
    try {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('access_token');
        if (AUTO_REDIRECT_401 && !window.location.pathname.startsWith('/login')) {
          setTimeout(() => window.location.replace('/login'), 0);
        }
      }
    } catch {}
  }

  const ct = (res.headers.get('content-type') || '').toLowerCase();
  const isJSON = ct.includes('application/json') || ct.includes('+json');

  if (!res.ok) {
    let body = null;
    let msg = '';
    let detail = '';
    const requestIdHeader = res.headers.get('x-request-id') || '';
    try {
      body = isJSON ? await res.json() : await res.text();
      if (isJSON && body && typeof body === 'object') {
        const serverErr = body.error || {};
        msg = serverErr.message || body.message || `HTTP ${res.status}`;
        detail = serverErr.detail ? JSON.stringify(serverErr.detail) : '';
      } else {
        msg = typeof body === 'string' ? body : `HTTP ${res.status}`;
      }
    } catch {
      msg = `HTTP ${res.status}`;
    }
    const message = [msg, detail].filter(Boolean).join(' | ');
    const err = new Error(message || `HTTP ${res.status}`);
    err.status = res.status;
    err.response = res;
    err.requestId = requestIdHeader || body?.error?.request_id || '';
    err.path = res.url;
    err.userMessage = extractErrorMessage(err, `Request failed (${res.status}).`);
    reportClientError(err, {
      source: 'http',
      status: res.status,
      path: res.url,
      requestId: err.requestId,
    });
    throw err;
  }
  if (res.status === 204) return true;
  return isJSON ? res.json() : res.text();
}

function request(method, path, body, opts = {}) {
  const { params, headers, timeout } = opts;

  const nextParams = { ...(params || {}) };
  if (typeof path === 'string' && path.startsWith('/kb')) {
    const uid = (typeof window !== 'undefined' && localStorage.getItem('x_user_id')) || '1';
    if (!('user_id' in nextParams) || nextParams.user_id == null || nextParams.user_id === '') {
      nextParams.user_id = uid;
    }
  }

  const url = buildURL(path, nextParams);
  const isForm = (typeof FormData !== 'undefined') && body instanceof FormData;
  const isJson = !!body && !isForm;

  const controller = new AbortController();
  const to = setTimeout(() => controller.abort(), Math.max(1, timeout || DEFAULT_TIMEOUT));

  const init = {
    method,
    headers: baseHeaders(isJson, headers),
    signal: controller.signal,
  };
  if (body != null) {
    init.body = isForm ? body : JSON.stringify(body);
  }

  return fetch(url, init)
    .then(handleResponse)
    .catch((err) => {
      if (err?.name === 'AbortError') {
        const timeoutErr = new Error('Request timed out');
        timeoutErr.status = 408;
        timeoutErr.path = url.toString();
        timeoutErr.userMessage = 'Request timed out. Please try again.';
        reportClientError(timeoutErr, {
          source: 'http-timeout',
          status: 408,
          path: timeoutErr.path,
        });
        throw timeoutErr;
      }
      if (err?.status == null) {
        reportClientError(err, {
          source: 'http-network',
          path: url.toString(),
        });
      }
      throw err;
    })
    .finally(() => clearTimeout(to));
}

function get(path, params, opts = {}) {
  return request('GET', path, undefined, { ...opts, params });
}
function post(path, body, opts = {}) {
  return request('POST', path, body, opts);
}
function patch(path, body, opts = {}) {
  return request('PATCH', path, body, opts);
}
function _delete(path, body, opts = {}) {
  return request('DELETE', path, body, opts);
}

export const http = { get, post, patch, delete: _delete };
export default http;
