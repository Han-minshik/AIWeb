import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import '../../../styles/admin/kbDash.css';
import { getActivityStats, getOpenSearchStats, getRecentStats } from '../../../services/kb/kbDashboardService';

import cell4Icon from '../../../assets/icons/cell_4.svg';
import textFileIcon from '../../../assets/icons/files.svg';
import databaseIcon from '../../../assets/icons/database-fill-check.svg';
import arrowClockwiseIcon from '../../../assets/icons/arrow-clockwise.svg';

const toInt = (v) => {
  const n = Number.parseInt(String(v ?? '').trim(), 10);
  return Number.isFinite(n) ? n : null;
};

const formatBytes = (bytes) => {
  const b = Number(bytes || 0);
  if (!Number.isFinite(b) || b <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const fixed = v >= 100 ? 0 : v >= 10 ? 1 : 1;
  return `${v.toFixed(fixed)} ${units[i]}`;
};

const timeAgo = (iso) => {
  if (!iso) return '-';
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return '-';
  const diff = Date.now() - t;
  const m = Math.floor(diff / 1000 / 60);
  if (m < 1) return 'Just now';
  if (m < 60) return `${m} minutes ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hours ago`;
  const d = Math.floor(h / 24);
  return `${d} days ago`;
};

const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

const healthTone = (h) => {
  const v = String(h || '').toLowerCase();
  if (v.includes('opt') || v.includes('ok') || v.includes('good') || v.includes('healthy')) return 'ok';
  if (v.includes('warn') || v.includes('degrad') || v.includes('slow') || v.includes('unknown')) return 'warn';
  return 'bad';
};

const MB = 1024 ** 2;
const DEFAULT_ACTIVITY_PERIOD = 'week';
const ACTIVITY_PERIOD_OPTIONS = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
];
const ENV = import.meta.env || {};
const CAPACITY_LIMIT_MB = (() => {
  const n = Number(
    ENV.VITE_OPENSEARCH_CAPACITY_MB
    || ENV.REACT_APP_OPENSEARCH_CAPACITY_MB
    || 3000,
  );
  return Number.isFinite(n) && n > 0 ? n : 3000;
})();

const DEFAULT_SUMMARY = {
  total_chunks: 0,
  chunks_delta_pct: 0,
  total_bytes: 0,
  bytes_delta_pct: 0,
  last_content_update: null,
  health: 'Unknown',
  health_score_pct: 0,
};

const DEFAULT_SPARK_DOCS = [0, 0, 0, 0, 0];

const DEFAULT_INSIGHT = {
  title: `Activity Trend (${DEFAULT_ACTIVITY_PERIOD.toUpperCase()})`,
  headline: 'Activity',
  yoy_pct: 0,
  desc: 'KB QA 0 | General 0',
  months: ['-', '-', '-', '-', '-'],
  kbQaSeries: [0, 0, 0, 0, 0],
  generalQaSeries: [0, 0, 0, 0, 0],
};

const parseRecentTimestamp = (raw) => {
  if (!raw) return null;
  const normalized = String(raw).trim().replace(' ', 'T');
  const ms = Date.parse(normalized);
  if (!Number.isFinite(ms)) return null;
  return new Date(ms).toISOString();
};

const parseActivityDateMs = (value) => {
  if (!value) return null;
  const normalized = String(value).trim().replace(' ', 'T');
  const ms = Date.parse(normalized);
  return Number.isFinite(ms) ? ms : null;
};

const formatActivityLabel = (dateValue, period) => {
  const key = String(dateValue || '').trim();
  if (!key) return '-';
  if (period === 'month' && /^\d{4}-\d{2}$/.test(key)) return key.slice(2, 7);
  if (/^\d{4}-\d{2}-\d{2}$/.test(key)) return key.slice(5, 10);

  const ms = parseActivityDateMs(key);
  if (!Number.isFinite(ms)) return key;
  const d = new Date(ms);
  const y = `${d.getFullYear()}`.slice(2);
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return period === 'month' ? `${y}-${m}` : `${m}-${day}`;
};

const buildActivitySeries = (rows, period, maxPoints = 5) => {
  const list = Array.isArray(rows) ? rows : [];
  const points = list
    .map((row) => {
      const date = String(row?.date || '').trim();
      return {
        date,
        ms: parseActivityDateMs(date),
        rag: Number(row?.rag_count || 0),
        general: Number(row?.general_count || 0),
      };
    })
    .filter((p) => p.date)
    .sort((a, b) => {
      const am = Number.isFinite(a.ms) ? a.ms : null;
      const bm = Number.isFinite(b.ms) ? b.ms : null;
      if (am != null && bm != null) return am - bm;
      if (am != null) return -1;
      if (bm != null) return 1;
      return a.date.localeCompare(b.date);
    });

  const picked = points.slice(-Math.max(1, maxPoints));

  const currentBucket = picked.length ? picked[picked.length - 1] : null;
  const prevBucket = picked.length > 1 ? picked[picked.length - 2] : null;
  const totalRag = picked.reduce((sum, p) => sum + (Number.isFinite(p.rag) ? p.rag : 0), 0);
  const totalGeneral = picked.reduce((sum, p) => sum + (Number.isFinite(p.general) ? p.general : 0), 0);

  return {
    labels: picked.map((p) => formatActivityLabel(p.date, period)),
    kbQaSeries: picked.map((p) => (Number.isFinite(p.rag) ? p.rag : 0)),
    generalQaSeries: picked.map((p) => (Number.isFinite(p.general) ? p.general : 0)),
    currentBucketTotal: currentBucket ? currentBucket.rag + currentBucket.general : 0,
    prevBucketTotal: prevBucket ? prevBucket.rag + prevBucket.general : null,
    totalRag,
    totalGeneral,
  };
};

const deriveHealth = (items) => {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return { label: 'Unknown', scorePct: 0 };

  const successCount = rows.filter((it) => String(it?.status || '').toLowerCase() === 'success').length;
  const scorePct = (successCount / rows.length) * 100;

  if (scorePct >= 90) return { label: 'Optimal', scorePct };
  if (scorePct >= 60) return { label: 'Warning', scorePct };
  return { label: 'Critical', scorePct };
};

const calcDeltaPct = (currentValue, prevValue) => {
  const cur = Number(currentValue);
  const prev = Number(prevValue);
  if (!Number.isFinite(cur) || !Number.isFinite(prev) || prev <= 0) return 0;
  return ((cur - prev) / prev) * 100;
};

const formatDeltaPct = (value, digits = 0) => {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return digits > 0 ? `0.${'0'.repeat(digits)}%` : '0%';
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}%`;
};

const deltaToneClass = (value) => (Number(value || 0) >= 0 ? 'up' : 'down');

function MiniBars({ values = [], className = '' }) {
  const vals = (Array.isArray(values) ? values : []).slice(0, 5);
  const maxV = Math.max(...vals, 1);

  const w = 140;
  const h = 60;
  const gap = 10;
  const barW = Math.floor((w - gap * (vals.length - 1)) / vals.length);

  const bars = vals.map((v, i) => {
    const pct = maxV > 0 ? v / maxV : 0;
    const bh = clamp(Math.round(pct * (h - 12)) + 12, 12, h);
    const x = i * (barW + gap);
    const y = h - bh;
    return { x, y, w: barW, h: bh, on: i === vals.length - 1 };
  });

  return (
    <svg className={className} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      {bars.map((b, i) => (
        <rect
          key={i}
          x={b.x}
          y={b.y}
          width={b.w}
          height={b.h}
          rx="3"
          className={b.on ? 'kb-v2-bar on' : 'kb-v2-bar'}
        />
      ))}
    </svg>
  );
}

function CanvasLineChart({
  values = [],
  className = '',
  themeKey = 0,
  lineColorVar = '--kb-chart-line',
  fillStartVar = '--kb-chart-fill-start',
  fillEndVar = '--kb-chart-fill-end',
}) {
  const ref = useRef(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    let frameId = 0;
    const draw = () => {
      const dpr = Math.max(1, window.devicePixelRatio || 1);
      const cssW = Math.max(1, canvas.clientWidth || 560);
      const cssH = Math.max(1, canvas.clientHeight || 220);

      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);

    const arr = (Array.isArray(values) ? values : []).map((v) => Number(v) || 0);
    if (arr.length < 2) return;

    const padX = 16;
    const padY = 16;
    const w = cssW - padX * 2;
    const h = cssH - padY * 2;

    const minV = Math.min(...arr);
    const maxV = Math.max(...arr);
    const span = Math.max(1, maxV - minV);

    const pts = arr.map((v, i) => ({
      x: padX + (i / (arr.length - 1)) * w,
      y: padY + (1 - (v - minV) / span) * h,
    }));

    const p = new Path2D();
    p.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i += 1) {
      const prev = pts[i - 1];
      const cur = pts[i];
      const cx = (prev.x + cur.x) / 2;
      p.bezierCurveTo(cx, prev.y, cx, cur.y, cur.x, cur.y);
    }

    const area = new Path2D(p);
    area.lineTo(pts[pts.length - 1].x, padY + h);
    area.lineTo(pts[0].x, padY + h);
    area.closePath();

    const styles = window.getComputedStyle(document.documentElement);
    const lineColor = styles.getPropertyValue(lineColorVar).trim() || 'rgba(37, 99, 235, 0.95)';
    const fillStart = styles.getPropertyValue(fillStartVar).trim() || 'rgba(37, 99, 235, 0.2)';
    const fillEnd = styles.getPropertyValue(fillEndVar).trim() || 'rgba(37, 99, 235, 0)';

    const grad = ctx.createLinearGradient(0, padY, 0, padY + h);
    grad.addColorStop(0, fillStart);
    grad.addColorStop(1, fillEnd);

    ctx.fillStyle = grad;
    ctx.fill(area);

    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 3.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
      ctx.stroke(p);
    };

    const scheduleDraw = () => {
      window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(draw);
    };

    scheduleDraw();
    const resizeObserver = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(scheduleDraw)
      : null;
    resizeObserver?.observe(canvas);
    window.addEventListener('resize', scheduleDraw);

    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener('resize', scheduleDraw);
      window.cancelAnimationFrame(frameId);
    };
  }, [values, themeKey, lineColorVar, fillStartVar, fillEndVar]);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
}

function KbDashboardView({ kbId }) {
  const kbIdNum = useMemo(() => (Number.isFinite(Number(kbId)) ? Number(kbId) : null), [kbId]);

  const [summary, setSummary] = useState(DEFAULT_SUMMARY);
  const [sparkDocs, setSparkDocs] = useState(DEFAULT_SPARK_DOCS);
  const [insight, setInsight] = useState(DEFAULT_INSIGHT);
  const [activityPeriod, setActivityPeriod] = useState(DEFAULT_ACTIVITY_PERIOD);
  const [themeKey, setThemeKey] = useState(0);
  const prevSnapshotRef = useRef({ chunks: null, sizeMb: null });

  const s = summary;

  const progressPct = clamp((s.total_bytes / Math.max(CAPACITY_LIMIT_MB * MB, 1)) * 100, 0, 100);
  const ringPct = clamp(Number(s.health_score_pct || 0), 0, 100);

  const chunks = Number(s.total_chunks || 0);
  const chunkValueText = chunks.toLocaleString();

  useEffect(() => {
    let cancelled = false;

    const loadDashboardStats = async () => {
      const [openSearchRes, activityRes, recentRes] = await Promise.allSettled([
        getOpenSearchStats(),
        getActivityStats(activityPeriod),
        getRecentStats(10),
      ]);

      if (cancelled) return;

      const openSearch = openSearchRes.status === 'fulfilled' ? (openSearchRes.value || {}) : {};
      const activity = activityRes.status === 'fulfilled' ? (activityRes.value || {}) : {};
      const recent = recentRes.status === 'fulfilled' && Array.isArray(recentRes.value) ? recentRes.value : [];

      const totalChunks = Number(openSearch?.total_chunks ?? openSearch?.total_documents ?? 0);
      const totalSizeMb = Number(openSearch?.total_size_mb || 0);
      const indices = Array.isArray(openSearch?.indices) ? openSearch.indices : [];

      const spark = indices
        .map((item) => Number(item?.chunk_count ?? item?.doc_count ?? 0))
        .filter((n) => Number.isFinite(n))
        .slice(0, 5);

      const activityRows = Array.isArray(activity?.data) ? activity.data : [];
      const periodSeries = buildActivitySeries(activityRows, activityPeriod, 5);
      const chartLabels = periodSeries.labels;
      const kbQaSeriesRaw = periodSeries.kbQaSeries;
      const generalQaSeriesRaw = periodSeries.generalQaSeries;

      const totals = activity?.totals || {};
      const apiRagTotal = Number(totals?.rag_count);
      const apiGeneralTotal = Number(totals?.general_count);
      const ragTotal = Number.isFinite(apiRagTotal) ? apiRagTotal : periodSeries.totalRag;
      const generalTotal = Number.isFinite(apiGeneralTotal) ? apiGeneralTotal : periodSeries.totalGeneral;

      const prev = prevSnapshotRef.current || {};
      const chunksDeltaPct = calcDeltaPct(totalChunks, prev.chunks);
      const bytesDeltaPct = calcDeltaPct(totalSizeMb, prev.sizeMb);
      const activityDeltaPct = calcDeltaPct(periodSeries.currentBucketTotal, periodSeries.prevBucketTotal);
      const health = deriveHealth(recent);

      const labels = chartLabels.length >= 2
        ? chartLabels
        : (chartLabels.length === 1 ? [chartLabels[0], chartLabels[0]] : DEFAULT_INSIGHT.months);
      const kbQaSeries = kbQaSeriesRaw.length >= 2
        ? kbQaSeriesRaw
        : (kbQaSeriesRaw.length === 1 ? [kbQaSeriesRaw[0], kbQaSeriesRaw[0]] : DEFAULT_INSIGHT.kbQaSeries);
      const generalQaSeries = generalQaSeriesRaw.length >= 2
        ? generalQaSeriesRaw
        : (generalQaSeriesRaw.length === 1 ? [generalQaSeriesRaw[0], generalQaSeriesRaw[0]] : DEFAULT_INSIGHT.generalQaSeries);

      setSummary({
        total_chunks: totalChunks,
        chunks_delta_pct: chunksDeltaPct,
        total_bytes: totalSizeMb > 0 ? totalSizeMb * MB : 0,
        bytes_delta_pct: bytesDeltaPct,
        last_content_update: parseRecentTimestamp(recent[0]?.timestamp),
        health: health.label,
        health_score_pct: health.scorePct,
      });

      setSparkDocs(spark.length ? spark : DEFAULT_SPARK_DOCS);
      setInsight({
        title: `Activity Trend (${activityPeriod.toUpperCase()})`,
        headline: 'Activity',
        yoy_pct: activityDeltaPct,
        desc: `KB QA ${ragTotal} | General ${generalTotal}`,
        months: labels,
        kbQaSeries,
        generalQaSeries,
      });

      prevSnapshotRef.current = {
        chunks: totalChunks,
        sizeMb: totalSizeMb,
      };
    };

    loadDashboardStats();
    const timer = setInterval(loadDashboardStats, 60000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [activityPeriod, kbIdNum]);

  useEffect(() => {
    const onTheme = () => setThemeKey((v) => v + 1);
    window.addEventListener('ui:theme-change', onTheme);
    return () => window.removeEventListener('ui:theme-change', onTheme);
  }, []);

  return (
    <div className="kb-dashboard kb-v2" data-kb-id={kbIdNum ?? ''}>
      <div className="kb-dashboard-header kb-v2-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 30, height: 30, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <img src={cell4Icon} alt="" style={{ width: 18, height: 18, display: 'block' }} />
          </div>
          <div className="kb-dashboard-title">Knowledge Base Overview</div>
        </div>

        <div className="kb-dashboard-actions">
          <div className="kb-v2-pill">
            <span className="kb-v2-dot" />
            Real-time updating
          </div>
        </div>
      </div>

      <div className="kb-dashboard-body kb-v2-body">
        <div className="kb-summary-grid kb-v2-metrics">
          <div className="kb-card kb-v2-card kb-v2-metric">
            <div className="kb-v2-metricTop">
              <div className="kb-card-title">Total Chunks</div>
              <div className="kb-v2-iconBox" aria-hidden="true">
                <img src={textFileIcon} alt="" />
              </div>
            </div>

            <div className="kb-card-value">{chunkValueText}</div>

            <div className="kb-v2-metricBottom">
              <div className={`kb-v2-delta ${deltaToneClass(s.chunks_delta_pct)}`}>
                {formatDeltaPct(s.chunks_delta_pct, 0)}
              </div>
              <div className="kb-card-sub">This month</div>
            </div>

            <div className="kb-v2-vizCenter">
              <MiniBars values={sparkDocs} className="kb-v2-miniBarSvg" />
            </div>
          </div>

          <div className="kb-card kb-v2-card kb-v2-metric">
            <div className="kb-v2-metricTop">
              <div className="kb-card-title">Data Capacity</div>
              <div className="kb-v2-iconBox" aria-hidden="true">
                <img src={databaseIcon} alt="" />
              </div>
            </div>

            <div className="kb-card-value">{formatBytes(s.total_bytes)}</div>

            <div className="kb-v2-metricBottom">
              <div className={`kb-v2-delta ${deltaToneClass(s.bytes_delta_pct)}`}>
                {formatDeltaPct(s.bytes_delta_pct, 0)}
              </div>
              <div className="kb-card-sub">{Math.round(progressPct)}% of {CAPACITY_LIMIT_MB.toLocaleString()} MB</div>
            </div>

            <div className="kb-v2-vizCenter">
              <div className="kb-v2-progress kb-v2-progressCenter">
                <div className="kb-v2-progressTrack">
                  <div className="kb-v2-progressFill" style={{ width: `${progressPct}%` }} />
                </div>
              </div>
            </div>
          </div>

          <div className="kb-card kb-v2-card kb-v2-metric">
            <div className="kb-v2-metricTop">
              <div className="kb-card-title">Last Update</div>
              <div className="kb-v2-iconBox" aria-hidden="true">
                <img src={arrowClockwiseIcon} alt="" />
              </div>
            </div>

            <div className="kb-card-value">{timeAgo(s.last_content_update)}</div>

            <div className={`kb-health ${healthTone(s.health)}`}>
              <span>System Health:</span>&nbsp;<span>{String(s.health || 'UNKNOWN')}</span>
            </div>

            <div className="kb-v2-vizCenter">
              <div className="kb-v2-ringWrap" aria-hidden="true">
                <svg className="kb-v2-ring" viewBox="0 0 40 40">
                  <circle className="kb-v2-ringBg" cx="20" cy="20" r="14" pathLength="100" />
                  <circle
                    className="kb-v2-ringFg"
                    cx="20"
                    cy="20"
                    r="14"
                    pathLength="100"
                    strokeDasharray={`${ringPct} ${100 - ringPct}`}
                  />
                  <text className="kb-v2-ringText" x="20" y="20">{Math.round(ringPct)}%</text>
                </svg>
              </div>
            </div>
          </div>
        </div>

        <div className="kb-card kb-v2-card kb-v2-insight">
          <div className="kb-v2-insightBadge">INSIGHT</div>

          <div className="kb-v2-insightGrid">
            <div className="kb-v2-insightText">
              <div className="kb-v2-periodSwitch" role="group" aria-label="Activity period">
                {ACTIVITY_PERIOD_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    className={activityPeriod === opt.value ? 'kb-v2-periodBtn on' : 'kb-v2-periodBtn'}
                    onClick={() => setActivityPeriod(opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              <div className="kb-v2-insightTitle">{String(insight.title || '')}</div>
              <div className="kb-v2-insightHeadline">
                {String(insight.headline || '')} <span className="kb-v2-yoy">{formatDeltaPct(insight.yoy_pct, 1)}</span>
              </div>

              <div className="kb-card-sub kb-v2-insightDesc">{String(insight.desc || '')}</div>
            </div>

            <div className="kb-v2-insightChart">
              <div className="kb-v2-chartGrid">
                <div className="kb-v2-chartPanel">
                  <div className="kb-v2-chartTitle">KB QA</div>
                  <div className="kb-v2-chartFrame">
                    <CanvasLineChart
                      values={insight.kbQaSeries}
                      className="kb-v2-canvasChart"
                      themeKey={themeKey}
                      lineColorVar="--kb-chart-line"
                      fillStartVar="--kb-chart-fill-start"
                      fillEndVar="--kb-chart-fill-end"
                    />
                    <div className="kb-v2-xLabels" aria-hidden="true">
                      {(Array.isArray(insight.months) ? insight.months : []).map((m, idx) => (
                        <span key={`kb-${m}-${idx}`}>{m}</span>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="kb-v2-chartPanel">
                  <div className="kb-v2-chartTitle">General QA</div>
                  <div className="kb-v2-chartFrame">
                    <CanvasLineChart
                      values={insight.generalQaSeries}
                      className="kb-v2-canvasChart"
                      themeKey={themeKey}
                      lineColorVar="--kb-chart-general-line"
                      fillStartVar="--kb-chart-general-fill-start"
                      fillEndVar="--kb-chart-general-fill-end"
                    />
                    <div className="kb-v2-xLabels" aria-hidden="true">
                      {(Array.isArray(insight.months) ? insight.months : []).map((m, idx) => (
                        <span key={`general-${m}-${idx}`}>{m}</span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function KbDashboardTab({ kbId }) {
  return <KbDashboardView kbId={kbId} />;
}

export default function KbDashboardPage() {
  const { kbId: kbIdParam } = useParams();
  const kbId = useMemo(() => toInt(kbIdParam), [kbIdParam]);
  return <KbDashboardView kbId={kbId} />;
}
