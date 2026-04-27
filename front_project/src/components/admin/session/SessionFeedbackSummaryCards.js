import React, { useMemo } from 'react';

export default function SessionFeedbackSummaryCards({ stats = [] }) {
  const summary = useMemo(() => {
    const rows = Array.isArray(stats) ? stats : [];

    let totalUp = 0;
    let totalDown = 0;
    let totalAll = 0;

    for (const r of rows) {
      const up = Number(
        r.likes ??
        r.up ??
        0,
      );
      const down = Number(
        r.dislikes ??
        r.down ??
        0,
      );
      const total = Number(
        r.total ??
        (up + down) ??
        0,
      );

      totalUp += up;
      totalDown += down;
      totalAll += total;
    }

    const positiveRate = totalAll > 0 ? Math.round((totalUp / totalAll) * 100) : 0;

    return {
      totalUp,
      totalDown,
      totalAll,
      positiveRate,
    };
  }, [stats]);

  return (
    <div className="feedback-summary card-row">
      <div className="card kpi-card">
        <div className="kpi-label">Total</div>
        <div className="kpi-value">{summary.totalAll.toLocaleString()}</div>
        <div className="kpi-sub muted">All feedback count</div>
      </div>

      <div className="card kpi-card">
        <div className="kpi-label">Like</div>
        <div className="kpi-value kpi-positive">{summary.totalUp.toLocaleString()}</div>
        <div className="kpi-sub muted">Positive feedback</div>
      </div>

      <div className="card kpi-card">
        <div className="kpi-label">Dislike</div>
        <div className="kpi-value kpi-negative">{summary.totalDown.toLocaleString()}</div>
        <div className="kpi-sub muted">Negative feedback</div>
      </div>

      <div className="card kpi-card">
        <div className="kpi-label">Positive rate</div>
        <div className="kpi-value">
          {summary.positiveRate}
          <span className="kpi-unit">%</span>
        </div>
        <div className="kpi-sub muted">
          (Like / Total)
        </div>
      </div>
    </div>
  );
}
