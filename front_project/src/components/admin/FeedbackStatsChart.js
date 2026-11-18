import React, { useMemo, useState } from 'react';

const SERIES = [
  { key: 'total', label: 'Total', className: 'feedback-chart-bar-total' },
  { key: 'up', label: 'Like', className: 'feedback-chart-bar-like' },
  { key: 'down', label: 'Dislike', className: 'feedback-chart-bar-dislike' },
];

export default function FeedbackStatsChart({ data = [], groupBy }) {
  const [visible, setVisible] = useState({ total: true, up: true, down: true });

  const chart = useMemo(() => {
    const rows = (Array.isArray(data) ? data : []).map((row) => ({
      label: formatLabel(row.label, groupBy),
      total: Number(row.total ?? 0),
      up: Number(row.up ?? row.likes ?? 0),
      down: Number(row.down ?? row.dislikes ?? 0),
    }));
    const maxValue = Math.max(1, ...rows.flatMap((row) => [row.total, row.up, row.down]));
    const tickMax = niceMax(maxValue);
    const tickValues = tickMax <= 5
      ? Array.from({ length: tickMax + 1 }, (_, index) => index)
      : Array.from({ length: 5 }, (_, index) => (tickMax * index) / 4);
    const width = Math.max(720, rows.length * 92 + 64);
    const height = 220;
    const plot = { left: 46, right: 18, top: 12, bottom: 42 };
    const plotWidth = width - plot.left - plot.right;
    const plotHeight = height - plot.top - plot.bottom;
    const baseline = plot.top + plotHeight;
    const slotWidth = plotWidth / Math.max(rows.length, 1);
    const barGap = 4;
    const barWidth = Math.min(24, Math.max(12, (slotWidth - 24 - barGap * 2) / 3));
    const groupWidth = barWidth * 3 + barGap * 2;

    return {
      rows,
      tickMax,
      tickValues,
      width,
      height,
      plot,
      plotWidth,
      plotHeight,
      baseline,
      slotWidth,
      barGap,
      barWidth,
      groupWidth,
    };
  }, [data, groupBy]);

  const toggleSeries = (key) => {
    setVisible((current) => ({ ...current, [key]: !current[key] }));
  };

  return (
    <div className="card feedback-chart-card">
      <div className="feedback-chart-header">
        <div className="feedback-chart-heading">
          <strong>Feedback statistics</strong>
          <span className="muted">Positive / Negative / Total</span>
        </div>
        <div className="feedback-chart-legend" aria-label="Chart series">
          {SERIES.map((series) => (
            <label key={series.key}>
              <input
                type="checkbox"
                checked={visible[series.key]}
                onChange={() => toggleSeries(series.key)}
              />
              <span className={`feedback-chart-swatch ${series.className}`} aria-hidden="true" />
              {series.label}
            </label>
          ))}
        </div>
      </div>

      {!chart.rows.length ? (
        <div className="feedback-chart-empty muted">No feedback data in this range.</div>
      ) : (
        <div className="feedback-chart-scroll">
          <svg
            className="feedback-chart-svg"
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            style={{ minWidth: chart.width }}
            role="img"
            aria-label="Feedback statistics chart"
          >
            {chart.tickValues.map((value) => {
              const ratio = value / chart.tickMax;
              const y = chart.baseline - ratio * chart.plotHeight;
              return (
                <g key={value}>
                  <line
                    className="feedback-chart-grid-line"
                    x1={chart.plot.left}
                    y1={y}
                    x2={chart.plot.left + chart.plotWidth}
                    y2={y}
                  />
                  <text className="feedback-chart-axis-label" x={chart.plot.left - 9} y={y + 4} textAnchor="end">
                    {Math.round(value)}
                  </text>
                </g>
              );
            })}

            {chart.rows.map((row, rowIndex) => {
              const startX = chart.plot.left
                + rowIndex * chart.slotWidth
                + (chart.slotWidth - chart.groupWidth) / 2;
              return (
                <g key={`${row.label}-${rowIndex}`}>
                  {SERIES.map((series, seriesIndex) => {
                    const value = row[series.key];
                    const barHeight = value > 0
                      ? Math.max(2, chart.plotHeight * (value / chart.tickMax))
                      : 0;
                    return visible[series.key] ? (
                      <rect
                        key={series.key}
                        className={series.className}
                        x={startX + seriesIndex * (chart.barWidth + chart.barGap)}
                        y={chart.baseline - barHeight}
                        width={chart.barWidth}
                        height={barHeight}
                        rx="3"
                      >
                        <title>{`${row.label} - ${series.label}: ${value}`}</title>
                      </rect>
                    ) : null;
                  })}
                  <text
                    className="feedback-chart-axis-label feedback-chart-x-label"
                    x={chart.plot.left + rowIndex * chart.slotWidth + chart.slotWidth / 2}
                    y={chart.baseline + 24}
                    textAnchor="middle"
                  >
                    {row.label}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}
    </div>
  );
}

function niceMax(value) {
  if (value <= 5) return value;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

function formatLabel(label, groupBy) {
  if (!label) return '';
  const value = String(label);

  if (groupBy === 'day') return value.slice(5);
  if (groupBy === 'week') return value.replace(/^(\d{4})-/, '');
  return value;
}
