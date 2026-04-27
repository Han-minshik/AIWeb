import React, { useEffect, useState } from 'react';
import useNotifications from '../../hooks/useNotifications';
import NotificationsModal from '../modals/NotificationsModal';

import DatabaseIcon from '/src/assets/icons/database.svg';
import ChatbotIcon from '/src/assets/icons/Chatbot.svg';
import HomeIcon from '/src/assets/icons/cell_4_home.svg';

const TOP_BUTTONS = [
  { id: 'kb', icon: DatabaseIcon, title: 'KB' },
  { id: 'ai', icon: ChatbotIcon, title: 'AI', noToggle: true },
  { id: 'dashboard', icon: HomeIcon, title: 'KB Dashboard', noToggle: true },
];

export default function LeftDock({
  leftOpen,
  activeDock,
  onEdgeClick,
  onEdgeDoubleClick,
}) {
  const { unread } = useNotifications();
  const [bump, setBump] = useState(false);
  const [showNotis, setShowNotis] = useState(false);

  useEffect(() => {
    if (unread > 0) {
      setBump(true);
      const t = setTimeout(() => setBump(false), 300);
      return () => clearTimeout(t);
    }
  }, [unread]);

  const renderBtn = (btn) => {
    const isKbBtn = btn.id === 'kb';
    const isActive =
      leftOpen && !btn.noToggle && (isKbBtn ? true : activeDock === btn.id);

    const className = ['dock-btn', isActive ? 'active' : '', btn.noToggle ? 'no-toggle' : '']
      .filter(Boolean)
      .join(' ');

    const handleClick = (e) => {
      onEdgeClick?.(btn, e.currentTarget);
      if (btn.id === 'ai') {
        window.dispatchEvent(new CustomEvent('ui:open-agent-picker'));
      }
      if (btn.id === 'dashboard') {
        window.dispatchEvent(new CustomEvent('ui:open-kb-dashboard-tab'));
      }
    };

    return (
      <button
        key={btn.id}
        className={className}
        title={btn.title}
        onClick={handleClick}
        onDoubleClick={() => onEdgeDoubleClick?.(btn)}
        style={{ position: 'relative' }}
        aria-label={btn.title}
      >
        <img src={btn.icon} alt={btn.title} />
      </button>
    );
  };

  return (
    <>
      <div className="dock-stack">
        {TOP_BUTTONS.map(renderBtn)}
      </div>
      <NotificationsModal open={showNotis} onClose={() => setShowNotis(false)} />
    </>
  );
}
