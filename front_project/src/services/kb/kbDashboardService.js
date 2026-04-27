import { http } from '../http/client';

const ENV = import.meta.env || {};
const STATS_API_BASE = String(
  ENV.VITE_STATS_API_BASE
  || ENV.REACT_APP_STATS_API_BASE
  || ENV.VITE_API_TARGET
  || ENV.REACT_APP_API_BASE
  || 'http://15.165.181.171:8000',
).replace(/\/+$/, '');

const statsUrl = (path) => {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${STATS_API_BASE}${cleanPath}`;
};

export async function getKbDashboardSummary(kbId) {
  return http.get(`/kb/trees/${kbId}/dashboard/summary`);
}

export async function getKbDashboardGrowth(kbId) {
  return http.get(`/kb/trees/${kbId}/dashboard/growth`);
}

export async function getOpenSearchStats() {
  return http.get(statsUrl('/stats/opensearch'));
}

export async function getActivityStats(period = 'week') {
  return http.get(statsUrl('/stats/activity'), { period });
}

export async function getRecentStats(limit = 10) {
  return http.get(statsUrl('/stats/recent'), { limit });
}
