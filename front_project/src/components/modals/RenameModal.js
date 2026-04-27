import React, { useEffect, useRef, useState } from 'react';

export default function RenameModal({ open, defaultName = '', onClose, onSubmit }) {
  const [name, setName] = useState(defaultName || '');
  const ref = useRef(null);

  useEffect(() => {
    if (open) {
      setName(defaultName || '');
      setTimeout(() => ref.current?.focus(), 10);
    }
  }, [open, defaultName]);

  if (!open) return null;

  const ok = () => {
    const v = (name || '').trim();
    if (!v) return;
    onSubmit?.(v);
    onClose?.();
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="modal-head">
          <div className="modal-title">Change Name</div>
        </div>
        <div className="modal-body">
          <input
            ref={ref}
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') ok(); }}
            placeholder="New name"
          />
        </div>
        <div className="modal-foot" style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn" onClick={ok}>Confirm</button>
        </div>
      </div>
    </div>
  );
}
