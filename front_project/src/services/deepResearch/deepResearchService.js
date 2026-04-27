const API_BASE = '/api';

function fetchSSE(url, body, handlers, onError) {
  const controller = new AbortController();

  (async () => {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        onError?.(`HTTP ${response.status}`);
        return;
      }

      const reader = response.body.getReader();
      const contentType = response.headers.get('content-type') || '';
      const charsetMatch = contentType.match(/charset=([^;]+)/i);
      const charset = charsetMatch?.[1]?.trim().toLowerCase();
      const normalizedCharset =
        charset === 'cp949' || charset === 'ms949' ? 'euc-kr' : charset;
      let decoder;
      try {
        decoder = new TextDecoder(normalizedCharset || 'utf-8');
      } catch {
        decoder = new TextDecoder('utf-8');
      }

      let buffer = '';
      let currentEvent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            const raw = line.slice(6);
            const handler = handlers[currentEvent];
            if (handler) {
              try {
                handler(JSON.parse(raw));
              } catch {
                // Ignore malformed payload chunks.
              }
            }
            currentEvent = '';
          }
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      onError?.('Connection error');
    }
  })();

  return () => controller.abort();
}

export function streamDeepResearch(question, depth, maxSources, model, callbacks = {}) {
  return fetchSSE(
    `${API_BASE}/deep-research/run`,
    { question, depth, max_sources: maxSources, model: model || 'qwen3' },
    {
      status: (d) => callbacks.onStatus?.(d?.phase, d?.message),
      brief_token: (d) => callbacks.onBriefToken?.(d?.text || ''),
      brief: (d) => callbacks.onBrief?.(d?.text || ''),
      topics: (d) => callbacks.onTopics?.(d?.topics || []),
      search_result: (d) => callbacks.onSearchResult?.(d),
      compression: (d) => callbacks.onCompression?.(d?.text || ''),
      token: (d) => callbacks.onToken?.(d?.text || ''),
      sources: (d) => callbacks.onSources?.(d?.sources || []),
      done: (d) => callbacks.onDone?.(d?.status || 'completed'),
    },
    (error) => callbacks.onError?.(error)
  );
}
