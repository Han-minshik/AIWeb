import React, { useEffect, useMemo, useRef } from 'react';
import TabBar from '../tabs/TabBar';
import EmbedPdfViewer from '../viewer/EmbedPdfViewer';
import MarkdownDocViewer from '../viewer/MarkdownDocViewer';
import GraphTab from '../graph/GraphTab';
import { KbDashboardTab } from '../admin/kb/KBDashboardPage';

import emptyFileIcon from '../../assets/icons/textFile.svg';

const EMPTY_TABS = Object.freeze([]);

function EmptyState() {
  return (
    <div className="pane-empty">
      <img className="pane-empty-icon" src={emptyFileIcon} alt="" />
      <div className="pane-empty-text">Select a document to view</div>
    </div>
  );
}

export default function Pane({
  keyName,
  stack,
  onSelect,
  onClose,
  onNew,
  onDragTabStart,
  onDragTabEnd,
  isResizing = false,
}) {
  const { tabs, activeId, firstId } = useMemo(() => {
    const tabs0 = Array.isArray(stack?.tabs) ? stack.tabs : EMPTY_TABS;
    const activeId0 = stack?.activeId ?? null;
    const firstId0 = tabs0[0]?.id ?? null;
    return { tabs: tabs0, activeId: activeId0, firstId: firstId0 };
  }, [stack]);

  const tabsLen = tabs.length;

  const autoSelectedRef = useRef(null);
  useEffect(() => {
    if (tabsLen > 0 && !activeId && firstId) {
      if (autoSelectedRef.current !== firstId) {
        onSelect?.(firstId);
        autoSelectedRef.current = firstId;
      }
    }
  }, [tabsLen, activeId, firstId, onSelect]);

  const activeTab = useMemo(() => {
    if (! tabsLen || !activeId) return null;
    return tabs.find((t) => t.id === activeId) || null;
  }, [tabs, tabsLen, activeId]);

  const isDoc = activeTab && activeTab.type === 'doc';
  const hasDocKey = isDoc && (activeTab.docId != null || activeTab.objectId != null || !!activeTab.docTitle);
  const docExt = useMemo(() => {
    if (!activeTab) return '';
    const ext = String(activeTab.docFileRight || '').trim().toLowerCase();
    if (ext) return ext;
    const title = String(activeTab.docTitle || activeTab.title || '').toLowerCase();
    const idx = title.lastIndexOf('.');
    return idx >= 0 ? title.slice(idx + 1) : '';
  }, [activeTab]);
  const shouldUseMarkdownViewer = docExt === 'md';

  const isGraph = activeTab && activeTab.type === 'graph';
  const isDashboard = activeTab && activeTab.type === 'kb_dashboard';

  return (
    <div className="pane" data-key={keyName}>
      <div className="pane-tabs pane-tabs-modern">
        <TabBar
          tabs={tabs}
          activeId={activeId}
          onSelect={onSelect}
          onClose={onClose}
          onNew={onNew}
          onDragTabStart={onDragTabStart}
          onDragTabEnd={onDragTabEnd}
        />
      </div>

      <div className={`pane-body${isResizing ? ' is-resizing' : ''}`}>
        {!activeId ? (
          <EmptyState />
        ) : isGraph ? (
          <GraphTab />
        ) : isDashboard ? (
          <KbDashboardTab kbId={activeTab?.kbId ?? null} onBack={() => onClose?.(activeId)} />
        ) : hasDocKey ? (
          shouldUseMarkdownViewer ? (
            <MarkdownDocViewer
              docId={activeTab.docId ?? null}
              objectId={activeTab.objectId ?? null}
              docTitle={activeTab.docTitle ?? activeTab.title ?? ''}
              docRelPath={activeTab.docRelPath ?? null}
              docSubPath={activeTab.docSubPath ?? null}
              docFileRight={activeTab.docFileRight ?? null}
              docStoredPath={activeTab.docStoredPath ?? null}
              docBranchType={activeTab.docBranchType ?? null}
            />
          ) : (
            <EmbedPdfViewer
              docId={activeTab.docId ?? null}
              objectId={activeTab.objectId ?? null}
              docTitle={activeTab.docTitle ?? activeTab.title ?? ''}
              docRelPath={activeTab.docRelPath ?? null}
              docSubPath={activeTab.docSubPath ?? null}
              docFileRight={activeTab.docFileRight ?? null}
              docStoredPath={activeTab.docStoredPath ?? null}
              docBranchType={activeTab.docBranchType ?? null}
              initialPage={activeTab.page ?? 1}
            />
          )
        ) : (
          <EmptyState />
        )}
        {isResizing ? <div className="pane-resize-shield" aria-hidden="true" /> : null}
      </div>
    </div>
  );
}
