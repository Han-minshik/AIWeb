import React from 'react';
import { createPortal } from 'react-dom';

import themeRoutineIcon from '../../assets/icons/theme_routine.svg';
import themeDarkIcon from '../../assets/icons/theme_dark.svg';
import themeLightIcon from '../../assets/icons/theme_light.svg';

export default function SettingsDropdown({
  settingsOpen,
  themeOpen,
  settingsPos,
  settingsMenuRect,
  settingsMenuRef,
  themeMenuRef,
  onToggleThemeMenu,
  onApplyTheme,
}) {
  return (
    <>
      {settingsOpen &&
        createPortal(
          <div
            className="dropdown"
            ref={settingsMenuRef}
            role="menu"
            aria-label="Settings Menu"
            style={{
              position: 'fixed',
              top: settingsPos.top,
              left: settingsPos.left,
              zIndex: 2147483647,
              minWidth: 180,
            }}
          >
            <button
              className="dropdown_item"
              role="menuitem"
              onClick={onToggleThemeMenu}
              style={{ display: 'flex', alignItems: 'center', gap: 8 }}
            >
              <img src={themeRoutineIcon} alt="" aria-hidden="true" style={{ width: 16, height: 16 }} />
              Theme
            </button>
          </div>,
          document.body
        )}
      {settingsOpen &&
        themeOpen &&
        createPortal(
          <div
            className="dropdown"
            ref={themeMenuRef}
            role="menu"
            aria-label="Theme Menu"
            style={{
              position: 'fixed',
              top: settingsMenuRect ? Math.round(settingsMenuRect.top) : settingsPos.top,
              left: settingsMenuRect
                ? Math.max(8, Math.round(settingsMenuRect.left) - 180 - 8)
                : Math.max(8, settingsPos.left - 180 - 8),
              zIndex: 2147483647,
              minWidth: 180,
            }}
          >
            <button
              className="dropdown_item"
              role="menuitem"
              onClick={() => onApplyTheme('dark')}
              style={{ display: 'flex', alignItems: 'center', gap: 8 }}
            >
              <img src={themeDarkIcon} alt="" aria-hidden="true" style={{ width: 16, height: 16 }} />
              Dark Mode
            </button>
            <button
              className="dropdown_item"
              role="menuitem"
              onClick={() => onApplyTheme('light')}
              style={{ display: 'flex', alignItems: 'center', gap: 8 }}
            >
              <img src={themeLightIcon} alt="" aria-hidden="true" style={{ width: 16, height: 16 }} />
              Light Mode
            </button>
          </div>,
          document.body
        )}
    </>
  );
}

