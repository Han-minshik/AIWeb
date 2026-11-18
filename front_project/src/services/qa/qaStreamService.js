const API_BASE = import.meta.env.VITE_API_BASE || '/api';

function buildQaStreamUrl(path = '/qa/stream') {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

export async function streamQaEvents({
  path = '/qa/stream',
  token = null,
  payload,
  signal,
  onEvent,
}) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('payload is required.');
  }
  if (typeof onEvent !== 'function') {
    throw new Error('onEvent callback is required.');
  }

  const headers = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(buildQaStreamUrl(path), {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify(payload),
    signal,
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  if (!res.body) {
    throw new Error('Empty stream response');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let eventName = 'message';
  let dataLines = [];

  const dispatchEvent = async () => {
    if (!dataLines.length) {
      eventName = 'message';
      return;
    }
    const payloadText = dataLines.join('\n');
    const name = eventName || 'message';
    eventName = 'message';
    dataLines = [];
    await onEvent(name, payloadText);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let lineEnd = buffer.indexOf('\n');
    while (lineEnd >= 0) {
      let line = buffer.slice(0, lineEnd);
      buffer = buffer.slice(lineEnd + 1);
      if (line.endsWith('\r')) line = line.slice(0, -1);

      if (!line) {
        await dispatchEvent();
      } else if (line.startsWith(':')) {
        // SSE keep-alive comment.
      } else if (line.startsWith('event:')) {
        eventName = line.slice(6).trim() || 'message';
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
      lineEnd = buffer.indexOf('\n');
    }
  }

  const tail = decoder.decode();
  if (tail) buffer += tail;
  if (buffer) {
    const line = buffer.endsWith('\r') ? buffer.slice(0, -1) : buffer;
    if (line.startsWith('event:')) eventName = line.slice(6).trim() || 'message';
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length) {
    await onEvent(eventName, dataLines.join('\n'));
  }
}
