import React from 'react';
import rightPanelOpen from '/src/assets/icons/right_panel_open.svg';
import rightPanelClose from '/src/assets/icons/right_panel_close.svg';

export default function EdgeToggle({ side = 'right', open, onToggle }) {
  const iconSrc = open ? rightPanelClose : rightPanelOpen;

  return (
    <button
      className={`edge-toggle ${side} ${open ? 'open' : 'closed'}`}
      onClick={onToggle}
      aria-label={`${side}-sidebar-toggle`}
      title={open ? 'Right Sidebar Close' : 'Right Sidebar Open'}
    >
      <img src={iconSrc} alt="" />
    </button>
  );
}
