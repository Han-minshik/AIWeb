import { http } from '../http/client';

export async function getKbDashboardSummary(kbId) {
  return http.get(`/kb/trees/${kbId}/dashboard/summary`);
}

export async function getKbDashboardGrowth(kbId) {
  return http.get(`/kb/trees/${kbId}/dashboard/growth`);
}

export async function getOpenSearchStats() {
  return http.get('/kb/dashboard/stats/opensearch');
}

export async function getActivityStats(period = 'week') {
  return http.get('/kb/dashboard/stats/activity', { period });
}

export async function getRecentStats(limit = 10) {
  return http.get('/kb/dashboard/stats/recent', { limit });
}
