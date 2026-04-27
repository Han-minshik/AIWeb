import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

export default function CustomSelect({ value, onChange, options, disabled }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onOutside);
    return () => document.removeEventListener('mousedown', onOutside);
  }, [open]);

  return (
    <div ref={ref} className="dr-select">
      <button
        type="button"
        className="dr-select-trigger"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{value}</span>
        <ChevronDown className={`dr-select-icon ${open ? 'open' : ''}`} />
      </button>
      {open ? (
        <div className="dr-select-menu">
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              className={`dr-select-item ${opt === value ? 'selected' : ''}`}
              onClick={() => {
                onChange(opt);
                setOpen(false);
              }}
            >
              {opt}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

