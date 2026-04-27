import React from 'react';
import ReactDOM from 'react-dom';

export default function ContextMenuPortal({ x, y, children }) {
  if (typeof document === 'undefined') return null;
  const style = { position: 'fixed', top: y, left: x, zIndex: 100000 };
  return ReactDOM.createPortal(
    <div className="ctx-menu" style={style}>
      {children}
    </div>,
    document.body
  );
}

