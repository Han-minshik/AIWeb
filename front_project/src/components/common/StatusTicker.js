import React from 'react';
import '../../styles/components/status-ticker.css';
import starsIcon from '../../assets/icons/stars.svg';

export default function StatusTicker({
  visible = false,
  step = '',
  steps = ['Question Analyzing', 'Knowledge Base Retrieval', 'Answer Generating'],
  hint = '',
  orientation = 'horizontal',
  compact = true,
  variant = 'typing-bubble',
  percent,
  avatarUrl = '',
  avatarAlt = 'assistant'
}) {
  if (!visible) return null;

  const isVert = orientation === 'vertical';
  const klass = `stx ${isVert ? 'stx-vert' : 'stx-horz'} ${compact ? 'stx-compact' : ''}`;

  if (variant === 'typing-bubble') {
    return (
      <div className={`${klass} stx-bubble-row`} aria-live="polite">
        <div className="stx-bubble stx-bubble-onlydots" role="status">
          <span className="stx-typing">
            <span className="stx-typing-dot" />
            <span className="stx-typing-dot" />
            <span className="stx-typing-dot" />
          </span>
        </div>
      </div>
    );
  }

  const isDone = String(step).toLowerCase() === 'done';
  const found = steps.findIndex(s => s === step);
  const idx = isDone ? steps.length : (found >= 0 ? found : 0);
  const pctFromSteps = isDone ? 100 : Math.round(((idx + 1) / Math.max(steps.length, 1)) * 100);
  const pct = typeof percent === 'number' ? Math.max(0, Math.min(100, percent)) : pctFromSteps;

  if (variant === 'bar') {
    return (
      <div className={`${klass} stx-onlybar`}>
        <div className="stx-progress" aria-label="Seledding">
          <div
            className="stx-bar"
            style={{
              width: !isVert ? `${pct}%` : undefined,
              height: isVert ? `${pct}%` : undefined,
            }}
          />
        </div>
        <div className="stx-hint">
          <img className="stx-spin stx-stars-icon" src={starsIcon} alt="" aria-hidden="true" />
          <span>{step || hint}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={klass}>
      <div className="stx-track" aria-label="Sledding">
        {steps.map((s, i) => {
          const state = i < idx ? 'done' : (!isDone && i === idx ? 'active' : 'todo');
          return (
            <div key={s} className={`stx-node ${state}`} title={s}>
              <span className="stx-dot" />
              <span className="stx-label">{s}</span>
              {i < steps.length - 1 && <span className="stx-connector" />}
            </div>
          );
        })}
      </div>

      {!compact && (
        <div className="stx-progress">
          <div
            className="stx-bar"
            style={{
              width: !isVert ? `${pct}%` : undefined,
              height: isVert ? `${pct}%` : undefined,
            }}
          />
        </div>
      )}

      <div className="stx-hint">
        <img className="stx-spin stx-stars-icon" src={starsIcon} alt="" aria-hidden="true" />
        <span>{step || hint}</span>
      </div>
    </div>
  );
}
