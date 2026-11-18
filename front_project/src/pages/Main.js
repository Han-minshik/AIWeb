import React, { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import LeftSidebar from '../components/sidebar/LeftSidebar';
import RightSidebar from '../components/sidebar/RightSidebar';
import RootLayout from '../components/layout/RootLayout';
import AppTopBar from '../components/layout/AppTopBar';
import SettingsDropdown from '../components/layout/SettingsDropdown';
import EdgeToggle from '../components/common/EdgeToggle';
import Pane from '../components/panes/Pane';
import AICatalogPanel from '../components/ai/AICatalogPanel';

import NotificationsModal from '../components/modals/NotificationsModal';
import PersonalKbModal from '../components/modals/PersonalKbModal';
import UserProfileMenu from '../components/UserProfileMenu';
import HamburgerMenu from '../components/menus/HamburgerMenu';

import useSidebarResize from '../hooks/useSidebarResize';
import usePaneSplit from '../hooks/usePaneSplit';
import useTabsAndDrag from '../hooks/useTabsAndDrag';

import { logoutAll } from '../services/auth/authService';
import { http } from '../services/http/client';

import '../styles/components/leftSidebar.css';
import '../styles/components/rightSidebar.css';
import '../styles/components/tabBar.css';
import '../styles/components/editor.css';
import '../styles/components/edgeToggle.css';
import '../styles/layout/layout.css';

export default function Main() {
  const {
    leftOpen,
    rightOpen,
    gridStyle,
    startDrag,
    toggleLeft,
    toggleRight,
    isMet,
    isResizing,
  } = useSidebarResize();

  const {
    layout,
    panes,
    addTab,
    closeTab,
    selectTab,
    onPanesDragOver,
    onPanesDrop,
    onDragTabStart,
    onDragTabEnd,
    hint,
  } = useTabsAndDrag();

  const { panesStyle, startPaneResize, showColResizer, showRowResizer, splitSideClass } =
    usePaneSplit(layout);

  const [openHam, setOpenHam] = useState(false);
  const [openNotif, setOpenNotif] = useState(false);
  const [openPersonalKb, setOpenPersonalKb] = useState(false);
  const [openProfile, setOpenProfile] = useState(false);
  const [profileAnchor, setProfileAnchor] = useState(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);
  const [settingsAnchor, setSettingsAnchor] = useState(null);
  const [settingsPos, setSettingsPos] = useState({ top: 56, left: 8 });
  const [settingsMenuRect, setSettingsMenuRect] = useState(null);
  const [imageViewerOpen, setImageViewerOpen] = useState(false);

  const settingsBtnRef = useRef(null);
  const settingsMenuRef = useRef(null);
  const themeMenuRef = useRef(null);

  const [kbCtx, setKbCtx] = useState({ kbId: null, name: 'Obsidian Vault' });

  const deriveIsAdmin = useCallback((u) => {
    if (!u) return false;
    const roleStr = String(u.role ?? u.user_role ?? '').toLowerCase();
    const rolesArr = (u.roles ?? u.scopes ?? []).map((r) => String(r).toLowerCase());
    return Boolean(
      u.is_admin === true ||
        u.admin === true ||
        roleStr === 'admin' ||
        rolesArr.includes('admin')
    );
  }, []);

  useEffect(() => {
    const u = window?.AppUser;
    if (u) {
      setIsAdmin(deriveIsAdmin(u));
      return;
    }
    (async () => {
      try {
        const me = await http.get('/auth/me');
        setIsAdmin(deriveIsAdmin(me));
      } catch {
        setIsAdmin(false);
      }
    })();
  }, [deriveIsAdmin]);

  useEffect(() => {
    if (!openProfile) return;
    const u = window?.AppUser;
    if (u) setIsAdmin(deriveIsAdmin(u));
  }, [openProfile, deriveIsAdmin]);

  useEffect(() => {
    const onImageViewerState = (e) => {
      setImageViewerOpen(Boolean(e?.detail?.open));
    };
    window.addEventListener('ui:image-viewer-state', onImageViewerState);
    return () => window.removeEventListener('ui:image-viewer-state', onImageViewerState);
  }, []);

  useEffect(() => {
    const onVaultSelect = (e) => {
      const kbId = e?.detail?.kbId ?? null;
      const name = e?.detail?.name ?? 'Obsidian Vault';
      setKbCtx({ kbId, name });
    };
    window.addEventListener('kb:vault-select', onVaultSelect);
    return () => window.removeEventListener('kb:vault-select', onVaultSelect);
  }, []);

  useEffect(() => {
    const handler = () => {
      const paneKeys = ['a', 'b', 'c', 'd'];
      let existingTab = null;
      let existingPaneKey = null;

      for (const key of paneKeys) {
        const stack = panes[key];
        if (!stack || !Array.isArray(stack.tabs)) continue;
        const found = stack.tabs.find((t) => t && t.type === 'graph');
        if (found) {
          existingTab = found;
          existingPaneKey = key;
          break;
        }
      }

      if (existingTab && existingPaneKey) {
        selectTab(existingPaneKey, existingTab.id);
        return;
      }

      addTab('a', { type: 'graph', title: 'Knowledge Graph' });
    };

    window.addEventListener('ui:open-graph-tab', handler);
    return () => {
      window.removeEventListener('ui:open-graph-tab', handler);
    };
  }, [panes, addTab, selectTab]);

  useEffect(() => {
    const handler = () => {
      const kbIdRaw = kbCtx?.kbId ?? null;
      const curKb = Number.isFinite(Number(kbIdRaw)) ? Number(kbIdRaw) : null;

      const paneKeys = ['a', 'b', 'c', 'd'];
      let existingTab = null;
      let existingPaneKey = null;

      for (const key of paneKeys) {
        const stack = panes[key];
        if (!stack || !Array.isArray(stack.tabs)) continue;
        const found = stack.tabs.find((t) => {
          if (!t || t.type !== 'kb_dashboard') return false;
          const tKb = Number.isFinite(Number(t.kbId)) ? Number(t.kbId) : null;
          return tKb === curKb;
        });
        if (found) {
          existingTab = found;
          existingPaneKey = key;
          break;
        }
      }

      if (existingTab && existingPaneKey) {
        selectTab(existingPaneKey, existingTab.id);
        return;
      }

      addTab('a', { type: 'kb_dashboard', title: 'KB Dashboard', kbId: curKb });
    };

    window.addEventListener('ui:open-kb-dashboard-tab', handler);
    return () => {
      window.removeEventListener('ui:open-kb-dashboard-tab', handler);
    };
  }, [kbCtx, panes, addTab, selectTab]);

  const sampleNotifications = useMemo(
    () => [
      { title: 'Build Succeeded', time: 'now', body: 'Frontend buid succeeded.', severity: 'info' },
      {
        title: 'Data synchronization delay',
        time: '5 minutes ago',
        body: 'Server response is slow.',
        severity: 'warn',
      },
    ],
    []
  );

  const closeNotif = () => {
    setOpenNotif(false);
    try {
      window.dispatchEvent(
        new CustomEvent('notifications:refresh', { detail: { reason: 'modal-close' } })
      );
    } catch {}
  };

  const handleLogout = async () => {
    const ok = window.confirm('Do you want to log out?');
    if (!ok) return;
    try {
      await logoutAll();
    } catch {}
    window.dispatchEvent(new Event('app:logout'));
    try {
      window.location.href = '/login';
    } catch {
      window.location.href = '/';
    }
  };

  const onOpenHamburger = () => setOpenHam(true);
  const onOpenNotifications = () => setOpenNotif(true);
  const onOpenSettings = () => {
    try {
      window.dispatchEvent(new CustomEvent('ui:open-settings', { detail: {} }));
    } catch {}
  };
  const onOpenSettingsMenu = (e) => {
    onOpenSettings();
    const rect = e?.currentTarget?.getBoundingClientRect?.() || null;
    setSettingsAnchor(rect);
    setSettingsOpen((v) => !v);
    setThemeOpen(false);
  };
  const onOpenUser = (e) => {
    try {
      setProfileAnchor(e?.currentTarget?.getBoundingClientRect?.() ?? null);
    } catch {
      setProfileAnchor(null);
    }
    setOpenProfile(true);
  };

  const computeSettingsPos = useCallback(() => {
    const rect = settingsAnchor;
    const gap = 8;
    const vw = typeof window !== 'undefined' ? window.innerWidth : 0;
    const vh = typeof window !== 'undefined' ? window.innerHeight : 0;
    const approxW = 180;
    const approxH = 140;

    let top = rect ? Math.round(rect.bottom + gap) : 56;
    let left = rect ? Math.round(rect.left) : 8;

    if (left + approxW > vw - 8) left = Math.max(8, vw - approxW - 8);
    if (top + approxH > vh - 8) top = Math.max(8, vh - approxH - 8);
    return { top, left };
  }, [settingsAnchor]);

  useEffect(() => {
    if (!settingsOpen) return;
    const update = () => setSettingsPos(computeSettingsPos());
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [settingsOpen, computeSettingsPos]);

  useEffect(() => {
    if (!settingsOpen) {
      setThemeOpen(false);
      return;
    }
    const id = requestAnimationFrame(() => {
      const rect = settingsMenuRef.current?.getBoundingClientRect?.();
      if (rect) setSettingsMenuRect(rect);
    });
    return () => cancelAnimationFrame(id);
  }, [settingsOpen, settingsPos]);

  useEffect(() => {
    if (!settingsOpen && !themeOpen) return;
    const onEsc = (e) => {
      if (e.key === 'Escape') {
        setThemeOpen(false);
        setSettingsOpen(false);
      }
    };
    const onDocClick = (e) => {
      const t = e.target;
      if (settingsBtnRef.current?.contains(t)) return;
      if (settingsMenuRef.current?.contains(t)) return;
      if (themeMenuRef.current?.contains(t)) return;
      setThemeOpen(false);
      setSettingsOpen(false);
    };
    document.addEventListener('keydown', onEsc, true);
    document.addEventListener('mousedown', onDocClick, true);
    return () => {
      document.removeEventListener('keydown', onEsc, true);
      document.removeEventListener('mousedown', onDocClick, true);
    };
  }, [settingsOpen, themeOpen]);

  const applyTheme = (next) => {
    try {
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('ui_theme', next);
      window.dispatchEvent(new CustomEvent('ui:theme-change', { detail: { theme: next } }));
    } catch {}
    setThemeOpen(false);
    setSettingsOpen(false);
  };

  return (
    <>
      <AppTopBar
        onOpenHamburger={onOpenHamburger}
        onOpenNotifications={onOpenNotifications}
        onOpenSettings={onOpenSettingsMenu}
        onOpenUser={onOpenUser}
        settingsBtnRef={settingsBtnRef}
      />

      <RootLayout style={gridStyle}>
        <LeftSidebar leftOpen={leftOpen} onToggle={toggleLeft} />

        <div className="resize-handle left" onMouseDown={(e) => startDrag('left', e)} />
        <div className="resize-handle right" onMouseDown={(e) => startDrag('right', e)} />

        {isMet && <div className="resize-handle meet" onMouseDown={(e) => startDrag('meet', e)} />}

        <section className={`center ${isMet ? 'center-collapsed' : ''}`}>
          <div
            className={`panes layout-${layout} ${splitSideClass}`}
            style={panesStyle}
            onDragOver={onPanesDragOver}
            onDrop={onPanesDrop}
          >
            <Pane
              keyName="a"
              stack={panes.a}
              onSelect={(id) => selectTab('a', id)}
              onClose={(id) => closeTab('a', id)}
              onNew={() => addTab('a')}
              onDragTabStart={(id) => onDragTabStart('a', id)}
              onDragTabEnd={onDragTabEnd}
              isResizing={isResizing}
            />

            {layout !== '1' && panes.b && (
              <Pane
                keyName="b"
                stack={panes.b}
                onSelect={(id) => selectTab('b', id)}
                onClose={(id) => closeTab('b', id)}
                onNew={() => addTab('b')}
                onDragTabStart={(id) => onDragTabStart('b', id)}
                onDragTabEnd={onDragTabEnd}
                isResizing={isResizing}
              />
            )}

            {(layout === '3L' || layout === '3R' || layout === '4') && panes.c && (
              <Pane
                keyName="c"
                stack={panes.c}
                onSelect={(id) => selectTab('c', id)}
                onClose={(id) => closeTab('c', id)}
                onNew={() => addTab('c')}
                onDragTabStart={(id) => onDragTabStart('c', id)}
                onDragTabEnd={onDragTabEnd}
                isResizing={isResizing}
              />
            )}

            {layout === '4' && panes.d && (
              <Pane
                keyName="d"
                stack={panes.d}
                onSelect={(id) => selectTab('d', id)}
                onClose={(id) => closeTab('d', id)}
                onNew={() => addTab('d')}
                onDragTabStart={(id) => onDragTabStart('d', id)}
                onDragTabEnd={onDragTabEnd}
                isResizing={isResizing}
              />
            )}

            {hint && (
              <div
                className="drop-hint"
                style={{ left: hint.x, top: hint.y, width: hint.w, height: hint.h }}
              >
                <span className="drop-label">
                  <i className="bi bi-file-earmark" /> New Tab
                </span>
              </div>
            )}

            {showColResizer && (
              <div className="pane-resizer vertical" onMouseDown={(e) => startPaneResize('col', e)}>
                <span className="line" />
              </div>
            )}

            {showRowResizer && (
              <div
                className="pane-resizer horizontal"
                onMouseDown={(e) => startPaneResize('row', e)}
              >
                <span className="line" />
              </div>
            )}
          </div>
        </section>

        {!imageViewerOpen && <EdgeToggle side="right" open={rightOpen} onToggle={toggleRight} />}
        <RightSidebar rightOpen={rightOpen} />

        <AICatalogPanel />
      </RootLayout>

      <HamburgerMenu open={openHam} onClose={() => setOpenHam(false)} />

      <NotificationsModal open={openNotif} onClose={closeNotif} items={sampleNotifications} />
      <PersonalKbModal open={openPersonalKb} onClose={() => setOpenPersonalKb(false)} />

      <UserProfileMenu
        open={openProfile}
        onClose={() => setOpenProfile(false)}
        anchorRect={profileAnchor}
        onAction={(key) => {
          if (key === 'logout') handleLogout();
          if (key === 'admin') window.location.href = '/admin';
          if (key === 'my-kb') setOpenPersonalKb(true);
          if (key === 'settings') {
            onOpenSettings();
            setSettingsAnchor(profileAnchor);
            setSettingsOpen(true);
            setThemeOpen(false);
          }
        }}
        isAdmin={isAdmin}
      />
      <SettingsDropdown
        settingsOpen={settingsOpen}
        themeOpen={themeOpen}
        settingsPos={settingsPos}
        settingsMenuRect={settingsMenuRect}
        settingsMenuRef={settingsMenuRef}
        themeMenuRef={themeMenuRef}
        onToggleThemeMenu={() => setThemeOpen((v) => !v)}
        onApplyTheme={applyTheme}
      />
    </>
  );
}
