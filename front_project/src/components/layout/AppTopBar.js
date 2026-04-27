import React from 'react';

import menuHamburger from '../../assets/icons/menu_hamburger.svg';
import bellIcon from '../../assets/icons/bell.svg';
import gearIcon from '../../assets/icons/gear.svg';
import personCircleIcon from '../../assets/icons/person-circle.svg';

export default function AppTopBar({
  onOpenHamburger,
  onOpenNotifications,
  onOpenSettings,
  onOpenUser,
  settingsBtnRef,
}) {
  return (
    <div className="app-topbar">
      <div className="app-topbar-left">
        <button
          type="button"
          className="app-topbar-btn-menu"
          aria-label="Menu"
          title="Menu"
          onClick={onOpenHamburger}
        >
          <img src={menuHamburger} alt="Menu" />
        </button>
        <div className="app-topbar-brand">SPACE AI</div>
      </div>

      <div className="app-topbar-center" />

      <div className="app-topbar-right">
        <button
          type="button"
          className="app-topbar-btn"
          aria-label="Notifications"
          title="Notifications"
          onClick={onOpenNotifications}
        >
          <img src={bellIcon} alt="Notifications" />
        </button>
        <button
          type="button"
          className="app-topbar-btn"
          aria-label="Settings"
          title="Settings"
          onClick={(e) => onOpenSettings?.(e)}
          ref={settingsBtnRef}
        >
          <img src={gearIcon} alt="Settings" />
        </button>
        <button
          type="button"
          className="app-topbar-btn"
          aria-label="User"
          title="User"
          onClick={onOpenUser}
        >
          <img src={personCircleIcon} alt="User" />
        </button>
      </div>
    </div>
  );
}

