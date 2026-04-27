import React, { useState } from 'react';

export default function SafeImg({ srcs = [], alt = '' }) {
  const [idx, setIdx] = useState(0);
  if (!srcs.length) return null;

  const tryNext = () => setIdx((i) => (i + 1 < srcs.length ? i + 1 : i));

  return (
    <img
      src={srcs[idx]}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={tryNext}
      style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
      referrerPolicy="no-referrer"
    />
  );
}
