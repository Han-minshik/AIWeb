import React, { useCallback, useEffect, useState, useMemo, useRef } from 'react';
import LeftDock from './LeftDock';

import SearchDocsModal from '../modals/SearchDocsModals';

import { http } from '../../services/http/client';

import ProjectsTreePanel from '../panels/ProjectsTreePanel';
import BookmarksPanel from '../../components/bookmarks/BookmarksPanel';
import '../../styles/components/leftSidebar.css';

import folderIcon from '../../assets/icons/folder.svg';
import searchIcon from '../../assets/icons/search.svg';
import bookmarkIcon from '../../assets/icons/bookmark.svg';

import chevronDown from '../../assets/icons/chevron-down.svg';
import chevronUp from '../../assets/icons/chevron-up.svg';

export default function LeftSidebar({ leftOpen, onToggle }) {
  const [activeDock, setActiveDock] = useState('kb');
  const [activePanel, setActivePanel] = useState('kb');

  const [openSearch, setOpenSearch] = useState(false);
  const [searchMode, setSearchMode] = useState('kb');

  const [kbRoots, setKbRoots] = useState([]);
  const [kbRootId, setKbRootId] = useState(null);
  const [rootsLoading, setRootsLoading] = useState(false);

  const [vaultOpen, setVaultOpen] = useState(false);
  const vaultRef = useRef(null);

  const emitVaultSelect = useCallback((id, name) => {
    if (id == null) return;
    const kbId = isNaN(Number(id)) ? id : Number(id);
    const kbName = name || `KB ${kbId}`;
    try {
      window.dispatchEvent(new CustomEvent('kb:vault-select', { detail: { kbId, name: kbName } }));
    } catch {}
  }, []);

  const normalizeRoots = useCallback((res) => {
    const items = Array.isArray(res) ? res : res?.items || [];
    return items.map((k) => ({
      id: k.kb_id ?? k.id ?? k.tree_id,
      name: k.name ?? `KB ${k.kb_id ?? k.id ?? k.tree_id}`,
    }));
  }, []);

  const loadRoots = useCallback(async () => {
    setRootsLoading(true);
    try {
      const res = await http.get('/kb/trees');
      const arr = normalizeRoots(res);
      setKbRoots(arr);

      if (!kbRootId && arr[0]) {
        const nextId = arr[0].id;
        const nextName = arr[0].name;
        setKbRootId(nextId);
        emitVaultSelect(nextId, nextName);
      } else if (kbRootId) {
        const found = arr.find((r) => String(r.id) === String(kbRootId));
        emitVaultSelect(kbRootId, found?.name);
      }
    } catch {
      setKbRoots([]);
    } finally {
      setRootsLoading(false);
    }
  }, [kbRootId, emitVaultSelect, normalizeRoots]);

  const forceSelectVaultByName = useCallback(
    async (targetName) => {
      const nameKey = String(targetName || '').trim().toLowerCase();
      if (!nameKey) return;

      let roots = kbRoots;
      if (!roots.length) {
        try {
          const res = await http.get('/kb/trees');
          roots = normalizeRoots(res);
          setKbRoots(roots);
        } catch {
          return;
        }
      }

      const found = roots.find((r) => String(r?.name || '').trim().toLowerCase() === nameKey);
      if (!found) return;

      const nextId = isNaN(Number(found.id)) ? found.id : Number(found.id);
      setKbRootId(nextId);
      emitVaultSelect(nextId, found.name);
    },
    [kbRoots, emitVaultSelect, normalizeRoots]
  );

  useEffect(() => {
    if (leftOpen && activeDock === 'kb') loadRoots();
  }, [leftOpen, activeDock, loadRoots]);

  useEffect(() => {
    if (!leftOpen) setVaultOpen(false);
  }, [leftOpen]);

  useEffect(() => {
    const onForceSelect = (e) => {
      const targetName = e?.detail?.name ?? '';
      if (!targetName) return;
      void forceSelectVaultByName(targetName);
    };
    window.addEventListener('kb:vault-force-select', onForceSelect);
    return () => window.removeEventListener('kb:vault-force-select', onForceSelect);
  }, [forceSelectVaultByName]);

  const handleEdgeClick = (btn) => {
    if (btn.id === 'graph') {
      try {
        window.dispatchEvent(new CustomEvent('ui:open-graph-tab', { detail: {} }));
      } catch {}
      return;
    }

    if (btn.noToggle || btn.id !== 'kb') return;

    if (!leftOpen) {
      onToggle?.();
      setActiveDock('kb');
      return;
    }
    if (activeDock === 'kb') {
      onToggle?.();
      setActiveDock(null);
      return;
    }
    setActiveDock('kb');
  };

  const handleEdgeDoubleClick = (btn) => {
    if (btn.noToggle || btn.id !== 'kb') return;
    if (leftOpen) {
      onToggle?.();
      setActiveDock(null);
    }
  };

  const label = 'KB Select';

  const handleChangeRootId = (value) => {
    const v = value ? (isNaN(Number(value)) ? value : Number(value)) : null;
    setKbRootId(v);
    const found = (kbRoots || []).find((r) => String(r.id) === String(v));
    emitVaultSelect(v, found?.name);
  };

  const currentKB = useMemo(() => {
    if (!kbRootId) return null;
    return (kbRoots || []).find((r) => String(r.id) === String(kbRootId)) || null;
  }, [kbRoots, kbRootId]);

  const isVaultDisabled = rootsLoading || activePanel !== 'kb';

  const toggleVaultOpen = (e) => {
    e.stopPropagation();
    if (isVaultDisabled) return;
    setVaultOpen((v) => !v);
  };

  const closeVault = useCallback(() => setVaultOpen(false), []);

  useEffect(() => {
    if (!vaultOpen) return;

    const onDocMouseDown = (e) => {
      const el = vaultRef.current;
      if (!el) return;
      if (el.contains(e.target)) return;
      setVaultOpen(false);
    };

    const onDocKeyDown = (e) => {
      if (e.key === 'Escape') setVaultOpen(false);
    };

    document.addEventListener('mousedown', onDocMouseDown, true);
    document.addEventListener('keydown', onDocKeyDown, true);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown, true);
      document.removeEventListener('keydown', onDocKeyDown, true);
    };
  }, [vaultOpen]);

  const handleOptionClick = (id) => {
    if (isVaultDisabled) return;
    handleChangeRootId(id);
    setVaultOpen(false);
  };

  const vaultTitleText = rootsLoading
    ? 'Loading...'
    : (kbRoots?.length || 0) > 0
    ? currentKB?.name || `${label} Vault`
    : 'No result';

  return (
    <aside className={`left-rail ${leftOpen ? 'open' : 'closed'}`}>
      <div className="left-rail-dock">
        <LeftDock
          leftOpen={leftOpen}
          activeDock={activeDock}
          onEdgeClick={handleEdgeClick}
          onEdgeDoubleClick={handleEdgeDoubleClick}
        />

        {leftOpen && (
          <div className="left-rail-main">
            <div className="left-rail-toolbar">
              <button
                className={`dock-btn ${activePanel === 'kb' ? 'active' : ''}`}
                title="Folder"
                onClick={() => setActivePanel('kb')}
              >
                <img src={folderIcon} alt="folder" />
              </button>

              <button
                className="dock-btn"
                title="Search"
                onClick={() => {
                  setSearchMode(activePanel === 'bookmarks' ? 'bookmark' : 'kb');
                  setOpenSearch(true);
                }}
              >
                <img src={searchIcon} alt="search" />
              </button>

              <button
                className={`dock-btn ${activePanel === 'bookmarks' ? 'active' : ''}`}
                title="Bookmarks"
                onClick={() => setActivePanel('bookmarks')}
              >
                <img src={bookmarkIcon} alt="bookmark" />
              </button>
            </div>

            <div className="left-rail-content">
              {activePanel === 'kb' && <ProjectsTreePanel mode="kb" rootId={kbRootId} />}
              {activePanel === 'bookmarks' && <BookmarksPanel />}
              {!activePanel && (
                <div className="empty" style={{ padding: 8 }}>
                  Select a view from the left dock.
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {leftOpen && (
        <div className="left-bottombar">
          <div className="vault-header">
            <div
              ref={vaultRef}
              className={`vault-select-wrap ${vaultOpen ? 'is-open' : ''}`}
              style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}
            >
              <div
                className={`vault-select vault-select-custom kb-chip custom-select ${isVaultDisabled ? 'disabled' : ''}`}
                role="button"
                tabIndex={isVaultDisabled ? -1 : 0}
                aria-label={label}
                title={label}
                aria-expanded={vaultOpen}
                onClick={toggleVaultOpen}
                onKeyDown={(e) => {
                  if (isVaultDisabled) return;
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setVaultOpen((v) => !v);
                  }
                  if (e.key === 'Escape') closeVault();
                  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') setVaultOpen(true);
                }}
              >
                <span className="label">{vaultTitleText}</span>
                <img
                  src={vaultOpen ? chevronDown : chevronUp}
                  alt=""
                  aria-hidden="true"
                  style={{ marginLeft: 6, width: 18, height: 18 }}
                />
              </div>

              {vaultOpen && !isVaultDisabled && (
                <ul className="custom-options">
                  {(kbRoots || []).map((r) => (
                    <li key={`kb-${r.id}`} onClick={() => handleOptionClick(r.id)}>
                      {r.name}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      <SearchDocsModal open={openSearch} onClose={() => setOpenSearch(false)} mode={searchMode} />
    </aside>
  );
}
