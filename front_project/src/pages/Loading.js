import React from 'react';
import '../styles/pages/Loading.css';

export default function Loading() {
  return (
    <div className="loading-root">
      <div className="loading-title">Loading plugins...</div>
      <div className="loading-bar-wrap" aria-label="loading-progress">
        <div className="loading-bar-fill" />
        <div className="loading-bar-track" />
      </div>
    </div>
  );
}
