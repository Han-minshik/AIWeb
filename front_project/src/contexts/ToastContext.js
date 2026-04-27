import React, { useEffect, useRef, useState } from 'react';
import '../styles/toast.css';

export default function ToastProvider() {
  const [toasts, setToasts] = useState([]);
  const timersRef = useRef(new Map());
  const lastShownRef = useRef({ key: '', at: 0 });

  useEffect(() => {
    const onToast = (e) => {
      const { message = '', type = 'info', duration = 3000, key } = e.detail || {};
      const now = Date.now();
      const dedupeKey = key || `${type}:${message}`;
      if (dedupeKey === lastShownRef.current.key && now - lastShownRef.current.at < 1200) return;
      lastShownRef.current = { key: dedupeKey, at: now };

      const id = `${now}_${Math.random().toString(36).slice(2, 8)}`;
      setToasts((prev) => {
        const next = [...prev, { id, message, type }];
        return next.slice(-4);
      });

      const t = setTimeout(() => {
        setToasts((prev) => prev.filter((x) => x.id !== id));
        timersRef.current.delete(id);
      }, Math.max(1200, duration));
      timersRef.current.set(id, t);
    };

    window.addEventListener('ui:toast', onToast);

    const timers = timersRef.current;
    return () => {
      window.removeEventListener('ui:toast', onToast);
      timers.forEach(clearTimeout);
      timers.clear();
    };
  }, []);

  const close = (id) => {
    const t = timersRef.current.get(id);
    if (t) clearTimeout(t);
    timersRef.current.delete(id);
    setToasts((prev) => prev.filter((x) => x.id !== id));
  };

  return (
    <div className="toast-container">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.type}`}>
          <span className="toast-msg">{t.message}</span>
          <button className="toast-x" onClick={() => close(t.id)}>×</button>
        </div>
      ))}
    </div>
  );
}
