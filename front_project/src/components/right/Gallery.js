import React, { useEffect, useRef, useState } from 'react';
import SafeImg from './SafeImg';

export default function Gallery({ images = [] }) {
  const PAGE = 3;
  const [start, setStart] = useState(0);
  const [modalImg, setModalImg] = useState(null);
  const [cols, setCols] = useState(3);
  const containerRef = useRef(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width || el.clientWidth || 0;
      const nextCols = w >= 720 ? 3 : w >= 480 ? 2 : 1;
      setCols(nextCols);
    });

    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const canNav = images.length > PAGE;

  const advance = (dir) => {
    if (!canNav) return;
    const len = images.length;
    const step = PAGE;
    setStart((s) => (s + (dir > 0 ? step : -step) + len) % len);
  };

  const visible = (() => {
    if (images.length <= PAGE) return images;
    const doubled = images.concat(images);
    return doubled.slice(start, start + PAGE);
  })();

  return (
    <>
      <div className="bubble-row left">
        <div className="bubble bubble-assistant" style={{ width: '100%' }}>
          {images.length > 0 && (
            <div className="md" style={{ marginBottom: 6 }}>
              <strong>Relevant image</strong>
            </div>
          )}

          <div
            ref={containerRef}
            className="ai-gallery"
            style={{
              position: 'relative',
              width: '100%',
              maxWidth: '100%',
              display: 'grid',
              gridTemplateColumns: `repeat(${cols}, 1fr)`,
              gap: 8,
              alignItems: 'center',
            }}
          >
            {visible.map((src, i) => (
              <div
                key={`${start}-${i}`}
                className="ai-gallery-cell"
                onClick={() => setModalImg(src)}
                style={{
                  width: '100%',
                  aspectRatio: '16 / 9',
                  background: 'var(--panel-2)',
                  borderRadius: 8,
                  overflow: 'hidden',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                }}
              >
                <SafeImg srcs={Array.isArray(src) ? src : [src]} alt="" />
              </div>
            ))}

            {canNav && (
              <>
                <button
                  type="button"
                  aria-label="이전"
                  onClick={() => advance(-1)}
                  className="ai-gallery-nav"
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    border: '1px solid var(--line)',
                    background: 'var(--panel)',
                    borderRadius: 8,
                    width: 28,
                    height: 28,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                  }}
                >
                  ‹
                </button>

                <button
                  type="button"
                  aria-label="다음"
                  onClick={() => advance(1)}
                  className="ai-gallery-nav"
                  style={{
                    position: 'absolute',
                    right: 0,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    border: '1px solid var(--line)',
                    background: 'var(--panel)',
                    borderRadius: 8,
                    width: 28,
                    height: 28,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                  }}
                >
                  ›
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {modalImg && (
        <div
          className="modal-backdrop"
          onClick={() => setModalImg(null)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            className="modal-content"
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'relative',
              maxWidth: '90vw',
              maxHeight: '90vh',
            }}
          >
            <button
              onClick={() => setModalImg(null)}
              style={{
                position: 'absolute',
                top: 10,
                right: 10,
                background: 'rgba(0,0,0,0.5)',
                color: '#fff',
                border: 'none',
                borderRadius: 4,
                padding: '4px 8px',
                cursor: 'pointer',
              }}
            >
              ✕
            </button>

            <div
              style={{
                maxWidth: '100%',
                maxHeight: '90vh',
                borderRadius: 8,
                overflow: 'hidden',
              }}
            >
              <SafeImg srcs={Array.isArray(modalImg) ? modalImg : [modalImg]} alt="" />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
