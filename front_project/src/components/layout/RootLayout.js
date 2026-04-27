import React from 'react';

export default function RootLayout({ className = '', style, children }) {
  return (
    <div className={`main-root ${className}`} style={style}>
      {children}
    </div>
  );
}
