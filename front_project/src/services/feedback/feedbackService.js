import { http } from '../http/client';

export async function getFeedbackStats(start, end, groupBy = 'month', appType) {
  try {
    const params = { start_date: start, end_date: end, group_by: groupBy };
    if (appType) params.app_type = appType;
    const data = await http.get('/chat/feedback/stats', params);
    const rows = Array.isArray(data) ? data : (data?.items || []);
    return rows.map(r => ({
      label: r.label ?? r.period ?? '',
      up: Number(r.up ?? r.like ?? 0),
      down: Number(r.down ?? r.dislike ?? 0),
      total: Number(r.total ?? (Number(r.up ?? 0) + Number(r.down ?? 0))),
    }));
  } catch {
    const list = await listFeedback(start, end, appType);
    return aggregate(list, groupBy);
  }
}

export async function listFeedback(start, end, appType) {
  const params = { start_date: start, end_date: end };
  if (appType) params.app_type = appType;
  const data = await http.get('/chat/feedback', params);
  const rows = Array.isArray(data) ? data : (data?.items || []);
  return rows.map(r => {
    const rating = typeof r.rating !== 'undefined' ? Number(r.rating) : undefined;
    let score = typeof r.score !== 'undefined' ? Number(r.score) : undefined;

    if (typeof score === 'undefined' && typeof rating === 'number') {
      if (rating >= 5) score = 1;
      else if (rating <= 1) score = -1;
      else score = 0;
    }

    return {
      id: r.feedback_id ?? r.id,
      message_id: r.message_id ?? r.mid,
      session_id: r.session_id,
      user_id: r.user_id,
      rating: typeof rating === 'number' ? rating : (typeof score === 'number' ? score : 3),
      score: typeof score === 'number' ? score : 0,
      comment: r.comment ?? '',
      created_at: r.created_at ?? r.time ?? r.ts,
      app_type: r.app_type ?? r.app,
      session_summary: r.session_summary ?? r.summary,
    };
  });
}

function aggregate(rows, groupBy) {
  const byKey = new Map();
  for (const r of rows) {
    const key = labelFor(r.created_at, groupBy);
    if (!byKey.has(key)) byKey.set(key, { label: key, up: 0, down: 0, total: 0 });
    const g = byKey.get(key);
    if (r.score > 0) g.up += 1;
    else if (r.score < 0) g.down += 1;
    g.total += 1;
  }
  return Array.from(byKey.values()).sort((a, b) => (a.label < b.label ? -1 : 1));
}

function labelFor(ts, groupBy) {
  const d = ts ? new Date(ts) : new Date();
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  if (groupBy === 'day') return `${y}-${m}-${day}`;
  if (groupBy === 'week') {
    const w = isoWeek(d);
    return `${y}-W${String(w).padStart(2, '0')}`;
  }
  return `${y}-${m}`;
}

function isoWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
  return weekNo;
}
