import React from 'react';
import { ExternalLink, Globe } from 'lucide-react';

function getDomain(url) {
  try {
    return new URL(url).hostname.replace(/^www\./i, '');
  } catch {
    return '';
  }
}

export default function SourceCard({ source }) {
  const domain = source?.domain || getDomain(source?.url || '');

  return (
    <a
      href={source?.url}
      target="_blank"
      rel="noopener noreferrer"
      className="dr-source-card"
    >
      <div className="dr-source-head">
        <p className="dr-source-title">{source?.title || 'Untitled'}</p>
        <ExternalLink className="dr-source-ext" />
      </div>
      <p className="dr-source-domain">
        <Globe className="dr-source-globe" />
        {domain}
      </p>
      {source?.topic_aspect ? <p className="dr-source-topic">{source.topic_aspect}</p> : null}
      {typeof source?.score === 'number' && source.score > 0 ? (
        <div className="dr-source-score-row">
          <div className="dr-source-score-track">
            <div
              className="dr-source-score-bar"
              style={{ width: `${Math.min(source.score * 100, 100)}%` }}
            />
          </div>
          <span className="dr-source-score-label">{(source.score * 100).toFixed(0)}</span>
        </div>
      ) : null}
    </a>
  );
}
