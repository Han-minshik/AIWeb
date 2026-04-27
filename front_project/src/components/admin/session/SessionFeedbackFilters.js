import React from 'react';

export default function SessionFeedbackFilters({
  startYm,
  endYm,
  groupBy,
  appType,
  loading = false,
  onChangeStartYm,
  onChangeEndYm,
  onChangeGroupBy,
  onChangeAppType,
  onSubmitStats,
  onShowRaw,
}) {
  const handleSubmit = (e) => {
    e.preventDefault();
    if (onSubmitStats) onSubmitStats();
  };

  return (
    <div className="panel-actions feedback-filters">
      <form
        className="feedback-filters-form"
        onSubmit={handleSubmit}
      >
        <label className="muted feedback-filters-label">Period</label>
        <input
          type="month"
          className="input"
          value={startYm}
          onChange={(e) => onChangeStartYm && onChangeStartYm(e.target.value)}
        />
        <span className="muted">~</span>
        <input
          type="month"
          className="input"
          value={endYm}
          onChange={(e) => onChangeEndYm && onChangeEndYm(e.target.value)}
        />

        <span className="muted feedback-filters-label">Group</span>
        <select
          className="input"
          value={groupBy}
          onChange={(e) => onChangeGroupBy && onChangeGroupBy(e.target.value)}
        >
          <option value="month">Month</option>
          <option value="week">Week</option>
          <option value="day">Day</option>
        </select>

        <span className="muted feedback-filters-label">App</span>
        <select
          className="input"
          value={appType || ''}
          onChange={(e) => onChangeAppType && onChangeAppType(e.target.value || null)}
        >
          <option value="">All</option>
          <option value="rag">RAG</option>
          <option value="req1">req1</option>
          <option value="req2">req2</option>
          <option value="req3">req3</option>
          <option value="req4">req4</option>
          <option value="req5">req5</option>
          <option value="req8">req8</option>
        </select>

        <button
          type="submit"
          className="btn check"
          disabled={loading}
        >
          {loading ? 'Loading...' : 'Check'}
        </button>

        <button
          type="button"
          className="btn ghost"
          onClick={onShowRaw}
        >
          Raw Data
        </button>
      </form>
    </div>
  );
}
