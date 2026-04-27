import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { dispatchOpenDocTab, readOpenDocTabDetail } from '../utils/openDocTab';

const newId = () => `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

const makeEmptyTab = () => ({ id: newId(), title: 'New Tab', type: 'empty' });
const makeKbDashboardTab = (kbId = null) => ({
  id: newId(),
  title: 'KB Dashboard',
  type: 'kb_dashboard',
  kbId: Number.isFinite(Number(kbId)) ? Number(kbId) : null,
});

const makeInitialStack = () => {
  const t = makeKbDashboardTab(null);
  return { tabs: [t], activeId: t.id };
};

const ensureActive = (stack) => {
  if (!stack) return makeInitialStack();
  const tabs0 = Array.isArray(stack.tabs) ? stack.tabs : [];
  const has = tabs0.length > 0;
  const activeId0 = stack.activeId ?? (has ? tabs0[0].id : null);
  return {
    tabs: has ? [...tabs0] : makeInitialStack().tabs,
    activeId: has ? activeId0 : makeInitialStack().tabs[0].id,
  };
};

const MAX_TABS = 10;

export default function useTabsAndDrag() {
  const [layout, setLayout] = useState('1');

  const [panes, setPanes] = useState(() => ({
    a: ensureActive(null),
    b: null,
    c: null,
    d: null,
  }));

  const activePaneKeyRef = useRef('a');
  const pendingNotifyRef = useRef(null);

  const notifyActiveTabChange = useCallback((paneKey, tab) => {
    if (!tab) return;
    // Defer dispatch to avoid setState during render in subscribers.
    setTimeout(() => {
      dispatchOpenDocTab({
        __fromTabs: true,
        paneKey,
        type: tab.type ?? 'empty',
        docId: tab.type === 'doc' ? (tab.docId ?? tab.objectId ?? null) : null,
        objectId: tab.type === 'doc' ? (tab.objectId ?? tab.docId ?? null) : null,
        title: tab.title ?? '',
        page: tab.type === 'doc' ? (tab.page ?? null) : null,
        docTitle: tab.docTitle ?? null,
        docRelPath: tab.docRelPath ?? null,
        docSubPath: tab.docSubPath ?? null,
        docFileRight: tab.docFileRight ?? null,
        docStoredPath: tab.docStoredPath ?? null,
        docBranchType: tab.docBranchType ?? null,
      });
    }, 0);
  }, []);

  const addTab = useCallback((paneKey = 'a', tabConfig) => {
    setPanes((prev) => {
      const p = prev[paneKey] ? ensureActive(prev[paneKey]) : ensureActive(null);

      if (tabConfig?.type === 'kb_dashboard') {
        const found = p.tabs.find((t) => t.type === 'kb_dashboard');
        if (found) {
          const nextActiveTab = p.tabs.find((t) => t.id === found.id) || found;
          pendingNotifyRef.current = { paneKey, tab: nextActiveTab };
          return {
            ...prev,
            [paneKey]: {
              tabs: p.tabs.map((t) =>
                t.id === found.id
                  ? {
                      ...t,
                      kbId: Number.isFinite(Number(tabConfig.kbId))
                        ? Number(tabConfig.kbId)
                        : t.kbId,
                    }
                  : t
              ),
              activeId: found.id,
            },
          };
        }
      }

      if (p.tabs.length >= MAX_TABS) {
        window.dispatchEvent(
          new CustomEvent('ui:toast', {
            detail: { type: 'warning', message: `You can only open up to ${MAX_TABS} tabs.` },
          })
        );
        return prev;
      }

      const t = tabConfig ? { id: newId(), ...tabConfig } : makeEmptyTab();

      const next = { ...prev, [paneKey]: { tabs: [...p.tabs, t], activeId: t.id } };
      activePaneKeyRef.current = paneKey;
      pendingNotifyRef.current = { paneKey, tab: t };
      return next;
    });
  }, []);

  const closeTab = useCallback((paneKey = 'a', tabId) => {
    setPanes((prev) => {
      const p = ensureActive(prev[paneKey]);
      const left = p.tabs.filter((t) => t.id !== tabId);
      if (!left.length) {
        const t = makeEmptyTab();
        pendingNotifyRef.current = { paneKey, tab: t };
        return { ...prev, [paneKey]: { tabs: [t], activeId: t.id } };
      }
      const nextActive = p.activeId === tabId ? left[left.length - 1].id : p.activeId;
      const nextActiveTab = left.find((t) => t.id === nextActive) || left[0] || null;
      pendingNotifyRef.current = { paneKey, tab: nextActiveTab };
      return { ...prev, [paneKey]: { tabs: left, activeId: nextActive } };
    });
  }, []);

  const selectTab = useCallback(
    (paneKey = 'a', tabId) => {
      setPanes((prev) => {
        const p = ensureActive(prev[paneKey]);
        const nextActiveTab = p.tabs.find((t) => t.id === tabId) || p.tabs[0] || null;
        const next = { ...prev, [paneKey]: { ...p, activeId: tabId } };
        activePaneKeyRef.current = paneKey;
        pendingNotifyRef.current = { paneKey, tab: nextActiveTab };
        return next;
      });
    },
    []
  );

  useEffect(() => {
    const pending = pendingNotifyRef.current;
    if (!pending) return;
    pendingNotifyRef.current = null;
    notifyActiveTabChange(pending.paneKey, pending.tab);
  }, [panes, notifyActiveTabChange]);

  const [hint, setHint] = useState(null);
  const onDragTabStart = useCallback(() => {}, []);
  const onDragTabEnd = useCallback(() => setHint(null), []);
  const onPanesDragOver = useCallback(() => {}, []);
  const onPanesDrop = useCallback(() => {}, []);

  const setActiveTabPayload = useCallback((paneKey, updater) => {
    setPanes((prev) => {
      const p = ensureActive(prev[paneKey]);
      const idx = p.tabs.findIndex((t) => t.id === (p.activeId ?? p.tabs[0]?.id));
      if (idx < 0) return prev;
      const old = p.tabs[idx] || makeEmptyTab();
      const nextTab = updater(old);
      const nextTabs = [...p.tabs];
      nextTabs[idx] = nextTab;
      return { ...prev, [paneKey]: { tabs: nextTabs, activeId: nextTab.id } };
    });
  }, []);

  const openDocInActive = useCallback(
    (docId, objectId, title, page, docTitle, docRelPath, docSubPath, docFileRight, docStoredPath, docBranchType) => {
      const key = activePaneKeyRef.current || 'a';
      setPanes((prev) => prev);
      setActiveTabPayload(key, (tab) => ({
        ...tab,
        type: 'doc',
        docId: docId ?? null,
        objectId: objectId ?? null,
        docTitle: docTitle ?? tab.docTitle ?? (title || null),
        docRelPath: docRelPath ?? tab.docRelPath ?? null,
        docSubPath: docSubPath ?? tab.docSubPath ?? null,
        docFileRight: docFileRight ?? tab.docFileRight ?? null,
        docStoredPath: docStoredPath ?? tab.docStoredPath ?? null,
        docBranchType: docBranchType ?? tab.docBranchType ?? null,
        page: page ?? tab.page ?? null,
        title: title || docTitle || tab.title || ((docId || objectId) ? `Doc ${docId ?? objectId}` : 'Doc'),
      }));
    },
    [setActiveTabPayload]
  );

  const openDocInNewTab = useCallback(
    (docId, objectId, title, page, paneKey, docTitle, docRelPath, docSubPath, docFileRight, docStoredPath, docBranchType) => {
    const key = paneKey || activePaneKeyRef.current || 'a';
    setPanes((prev) => {
      const p = prev[key] ? ensureActive(prev[key]) : ensureActive(null);
      if (p.tabs.length >= MAX_TABS) return prev;
      const t = {
        id: newId(),
        type: 'doc',
        docId: docId ?? null,
        objectId: objectId ?? null,
        docTitle: docTitle ?? title ?? null,
        docRelPath: docRelPath ?? null,
        docSubPath: docSubPath ?? null,
        docFileRight: docFileRight ?? null,
        docStoredPath: docStoredPath ?? null,
        docBranchType: docBranchType ?? null,
        page: page ?? null,
        title: title || docTitle || ((docId || objectId) ? `Doc ${docId ?? objectId}` : 'Doc'),
      };
      activePaneKeyRef.current = key;
      return { ...prev, [key]: { tabs: [...p.tabs, t], activeId: t.id } };
    });
  }, []);

  const openTextInActive = useCallback(
    (title, content) => {
      const key = activePaneKeyRef.current || 'a';
      setActiveTabPayload(key, (tab) => ({
        ...tab,
        type: 'text',
        title: title || tab.title || 'Result',
        content: content ?? '',
      }));
    },
    [setActiveTabPayload]
  );

  useEffect(() => {
    const onOpenDoc = (e) => {
      const detail = readOpenDocTabDetail(e);
      if (detail?.__fromTabs) return;
      const {
        docId,
        objectId,
        title,
        page,
        forceNewTab,
        paneKey,
        docTitle,
        docRelPath,
        docSubPath,
        docFileRight,
        docStoredPath,
        docBranchType,
      } = detail || {};
      if (!docId && !objectId && !docTitle) return;
      if (forceNewTab) {
        openDocInNewTab(
          docId ?? null,
          objectId ?? null,
          title,
          page,
          paneKey,
          docTitle,
          docRelPath,
          docSubPath,
          docFileRight,
          docStoredPath,
          docBranchType
        );
      } else {
        openDocInActive(
          docId ?? null,
          objectId ?? null,
          title,
          page,
          docTitle,
          docRelPath,
          docSubPath,
          docFileRight,
          docStoredPath,
          docBranchType
        );
      }
    };
    const onOpenText = (e) => {
      const { title, content } = e.detail || {};
      openTextInActive(title, content);
    };
    window.addEventListener('open-doc-tab', onOpenDoc);
    window.addEventListener('open-text-tab', onOpenText);
    return () => {
      window.removeEventListener('open-doc-tab', onOpenDoc);
      window.removeEventListener('open-text-tab', onOpenText);
    };
  }, [openDocInActive, openDocInNewTab, openTextInActive]);

  const safePanes = useMemo(() => {
    const clone = (p) => (p ? { tabs: [...(p.tabs || [])], activeId: p.activeId } : null);
    return { a: clone(panes.a), b: clone(panes.b), c: clone(panes.c), d: clone(panes.d) };
  }, [panes]);

  return {
    layout,
    panes: safePanes,
    addTab,
    closeTab,
    selectTab,
    onPanesDragOver,
    onPanesDrop,
    onDragTabStart,
    onDragTabEnd,
    hint,
    setLayout,
    setPanes,
    openDocInActive,
    openDocInNewTab,
    openTextInActive,
  };
}
