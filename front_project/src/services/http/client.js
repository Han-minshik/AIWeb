import { extractErrorMessage, reportClientError } from '../errors/errorReporter';
const API_BASE = import.meta.env.VITE_API_BASE || '/api';
const DEFAULT_TIMEOUT = 300000;
const AUTO_REDIRECT_401 = true;
const GATEWAY_RETRY_STATUSES = new Set([502, 503, 504]);

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getToken() {
  try {
    return typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
  } catch {
    return null;
  }
}

function decodeJwtPayload(token) {
  if (!token || typeof token !== 'string') return null;
  try {
    const part = token.split('.')[1];
    if (!part) return null;
    const normalized = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), '=');
    return JSON.parse(atob(padded));
  } catch {
    return null;
  }
}

function getCurrentUserId() {
  const tokenUserId = decodeJwtPayload(getToken())?.sub;
  if (tokenUserId != null && tokenUserId !== '') return String(tokenUserId);
  try {
    return (typeof window !== 'undefined' && localStorage.getItem('x_user_id')) || '1';
  } catch {
    return '1';
  }
}

function baseHeaders(isJson, extra) {
  const token = getToken();
  const h = {
    'X-User-Id': getCurrentUserId(),
    'X-Username': (typeof window !== 'undefined' && localStorage.getItem('x_username')) || 'demo',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(extra || {}),
  };
  if (isJson) h['Content-Type'] = 'application/json';
  return h;
}

function buildURL(path, params) {
  const rawUrl = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const url = new URL(rawUrl, window.location.origin);
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
        const bodyDetail = body.detail;
        const detailMessage =
          typeof bodyDetail === 'string'
            ? bodyDetail
            : bodyDetail && typeof bodyDetail === 'object'
            ? bodyDetail.message || JSON.stringify(bodyDetail)
            : '';
        msg = serverErr.message || body.message || detailMessage || `HTTP ${res.status}`;
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
    err.body = body;
    err.detail = body?.detail ?? body?.error?.detail ?? null;
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
  const {
    params,
    headers,
    timeout,
    gatewayRetries = 0,
    gatewayRetryDelay = 1000,
  } = opts;

  const nextParams = { ...(params || {}) };
  if (typeof path === 'string' && path.startsWith('/kb')) {
    const uid = getCurrentUserId();
    if (!('user_id' in nextParams) || nextParams.user_id == null || nextParams.user_id === '') {
      nextParams.user_id = uid;
    }
  }

  const url = buildURL(path, nextParams);
  const isForm = (typeof FormData !== 'undefined') && body instanceof FormData;
  const isJson = !!body && !isForm;

  const maxGatewayRetries = Math.max(0, Math.min(3, Number(gatewayRetries) || 0));

  const execute = async (attempt = 0) => {
    const controller = new AbortController();
    const to = setTimeout(() => controller.abort(), Math.max(1, timeout || DEFAULT_TIMEOUT));
    const init = {
      method,
      headers: baseHeaders(isJson, headers),
      credentials: 'include',
      signal: controller.signal,
    };
    if (body != null) {
      init.body = isForm ? body : JSON.stringify(body);
    }

    try {
      return await fetch(url, init).then(handleResponse);
    } catch (err) {
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
      if (GATEWAY_RETRY_STATUSES.has(Number(err?.status)) && attempt < maxGatewayRetries) {
        await wait(Math.max(0, Number(gatewayRetryDelay) || 0) * (2 ** attempt));
        return execute(attempt + 1);
      }
      if (err?.status == null) {
        reportClientError(err, {
          source: 'http-network',
          path: url.toString(),
        });
      }
      throw err;
    } finally {
      clearTimeout(to);
    }
  };

  return execute();
}

function get(path, params, opts = {}) {
  return request('GET', path, undefined, {
    gatewayRetries: 2,
    gatewayRetryDelay: 500,
    ...opts,
    params,
  });
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

function upload(path, body, opts = {}) {
  const { headers, onProgress, timeout = 0 } = opts;
  const url = buildURL(path);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url.toString(), true);
    xhr.withCredentials = true;
    xhr.timeout = Math.max(0, Number(timeout) || 0);
    Object.entries(baseHeaders(false, headers)).forEach(([key, value]) => {
      if (value != null) xhr.setRequestHeader(key, String(value));
    });
    if (typeof onProgress === 'function') {
      xhr.upload.onprogress = (event) => {
        onProgress({
          loaded: event.loaded,
          total: event.lengthComputable ? event.total : body?.size || 0,
          percent: event.lengthComputable && event.total > 0
            ? Math.round((event.loaded / event.total) * 100)
            : null,
        });
      };
    }
    xhr.onload = () => {
      let payload = xhr.responseText;
      try { payload = payload ? JSON.parse(payload) : true; } catch {}
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }
      const message = payload?.detail || payload?.error?.message || `HTTP ${xhr.status}`;
      const error = new Error(String(message));
      error.status = xhr.status;
      error.path = url.toString();
      error.userMessage = extractErrorMessage(error, `Upload failed (${xhr.status}).`);
      reportClientError(error, { source: 'http-upload', status: xhr.status, path: error.path });
      reject(error);
    };
    xhr.onerror = () => {
      const error = new Error('Upload network error');
      error.path = url.toString();
      error.userMessage = 'Upload failed because the server could not be reached.';
      reportClientError(error, { source: 'http-upload-network', path: error.path });
      reject(error);
    };
    xhr.ontimeout = () => {
      const error = new Error('Upload timed out');
      error.status = 408;
      error.path = url.toString();
      error.userMessage = 'Upload timed out. Please try again.';
      reject(error);
    };
    xhr.onabort = () => {
      const error = new Error('Upload cancelled');
      error.status = 499;
      error.path = url.toString();
      reject(error);
    };
    xhr.send(body);
  });
}

export const http = { get, post, patch, delete: _delete, upload };
export default http;
