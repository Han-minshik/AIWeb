import React, { useMemo, useState } from 'react';

export default function FeedbackStatsChart({ data = [], groupBy }) {
  const [showTotal, setShowTotal] = useState(true);
  const [showUp, setShowUp] = useState(true);
  const [showDown, setShowDown] = useState(true);

  const chart = useMemo(() => {
    const rows = Array.isArray(data) ? data : [];
    const max = Math.max(
      1,
      ...rows.map(r => {
        const t = r.total ?? 0;
        const u = r.up ?? r.likes ?? 0;
        const d = r.down ?? r.dislikes ?? 0;
        return Math.max(t, u, d);
      })
    );

    const barW = 44;
    const gap = 12;
    const h = 160;
    const pad = 28;
    const width = rows.length * (barW + gap) + 20;
    const height = h + pad + 10;

    const bars = [];
    rows.forEach((r, i) => {
      const x = 10 + i * (barW + gap);

      const total = r.total ?? 0;
      const up = r.up ?? r.likes ?? 0;
      const down = r.down ?? r.dislikes ?? 0;

      const totalH = Math.round(h * (total / max));
      const upH = Math.round(h * (up / max));
      const downH = Math.round(h * (down / max));

      if (showTotal) {
        const baseY = h - totalH + 10;
        bars.push(
          <rect
            key={`t-${i}`}
            x={x}
            y={baseY}
            width={barW}
            height={totalH}
            rx="4"
            fill="#e6e6e6"
          />
        );
      }

      if (showUp) {
        bars.push(
          <rect
            key={`u-${i}`}
            x={x}
            y={h - upH + 10}
            width={barW / 2 - 2}
            height={upH}
            rx="3"
            fill="#36cfc9"
          />
        );
      }

      if (showDown) {
        bars.push(
          <rect
            key={`d-${i}`}
            x={x + barW / 2 + 2}
            y={h - downH + 10}
            width={barW / 2 - 2}
            height={downH}
            rx="3"
            fill="#ff7875"
          />
        );
      }

      bars.push(
        <text
          key={`l-${i}`}
          x={x + barW / 2}
          y={h + 22}
          textAnchor="middle"
          fontSize="10"
          fill="#666"
        >
          {formatLabel(r.label, groupBy)}
        </text>
      );
    });

    return { width, height, bars };
  }, [data, showTotal, showUp, showDown, groupBy]);

  return (
    <div className="card" style={{ padding: 10 }}>
      <div
        className="row"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 8,
          flexWrap: 'wrap'
        }}
      >
        <strong>Feedback statistics</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          Positive / Negative / Total
        </span>

        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
          <input
            type="checkbox"
            checked={showTotal}
            onChange={(e) => setShowTotal(e.target.checked)}
          />
          Total
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
          <input
            type="checkbox"
            checked={showUp}
            onChange={(e) => setShowUp(e.target.checked)}
          />
          Like
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
          <input
            type="checkbox"
            checked={showDown}
            onChange={(e) => setShowDown(e.target.checked)}
          />
          Dislike
        </label>
      </div>

      <svg width={chart.width} height={chart.height} role="img" aria-label="Feedback chart">
        <line x1="10" y1="10" x2={chart.width - 10} y2="10" stroke="#f0f0f0" />
        <line x1="10" y1="170" x2={chart.width - 10} y2="170" stroke="#f0f0f0" />
        {chart.bars}
      </svg>
    </div>
  );
}

function formatLabel(label, groupBy) {
  if (!label) return '';

  if (groupBy === 'day') {
    return label.slice(5);
  }

  if (groupBy === 'week') {
    return label.replace(/^(\d{4})-/, '');
  }

  return label;
}
