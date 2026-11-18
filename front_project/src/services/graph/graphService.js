const GRAPH_API_BASE = import.meta.env.VITE_GRAPH_API_BASE || 'http://localhost:8005';

function buildGraphUrl(path, params) {
  const qs = params instanceof URLSearchParams ? params.toString() : '';
  return `${GRAPH_API_BASE}${path}${qs ? `?${qs}` : ''}`;
}

async function fetchGraphJson(path, params, fallbackMessage) {
  const res = await fetch(buildGraphUrl(path, params));
  if (!res.ok) {
    throw new Error(fallbackMessage);
  }
  return res.json();
}

export async function fetchGraphData(params) {
  return fetchGraphJson('/graph/data', params, 'Failed to fetch graph data');
}

export async function fetchSearchTermData(params) {
  return fetchGraphJson('/graph/search_term', params, 'Failed to fetch search term graph data');
}

export async function fetchHopAnalysisData(params) {
  return fetchGraphJson('/graph/hop_analysis', params, 'Failed to fetch hop analysis data');
}
