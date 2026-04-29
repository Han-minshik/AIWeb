import React, { useEffect, useMemo, useRef, useState } from 'react';
import { http } from '../../../services/http/client';
import '../../../styles/admin/syncManageModal.css';

const TABS = [
  { key: 'edit', label: 'Edit' },
  { key: 'delete', label: 'Delete' },
  { key: 'add', label: 'Add' },
  { key: 'duplicate', label: 'Duplicate Paths' },
];

const CHECK_COL_WIDTH = 52;
const DATA_COLS = [
  { key: 'branch', label: 'branch_type', min: 96, defaultWidth: 128 },
  { key: 'target', label: 'Target Path', min: 280, defaultWidth: 560 },
  { key: 'change', label: 'Action Type', min: 140, defaultWidth: 190 },
  { key: 'preview', label: 'Change Reason', min: 240, defaultWidth: 420 },
];
const APPLY_BATCH_SIZE = 50;

function splitByAction(items = []) {
  const out = { add: [], edit: [], delete: [], duplicate: [] };
  for (const it of items) {
    const a = String(it?.action || '').toLowerCase();
    if (a === 'duplicate_path' || a === 'same_content_multi_path') out.duplicate.push(it);
    else if (a === 'add' || a === 'insert') out.add.push(it);
    else if (a === 'edit' || a === 'update' || a === 'path_mismatch') out.edit.push(it);
    else if (a === 'delete' || a === 'remove') out.delete.push(it);
  }
  return out;
}

function toRow(tabKey, item, idx) {
  const keyPrefix = tabKey === 'duplicate' ? 'M' : tabKey === 'add' ? 'A' : tabKey === 'edit' ? 'E' : 'D';
  const kbId = item?.kb_id ?? item?.kbId ?? '-';
  const kbName = item?.kb_name ?? item?.kbName ?? '-';
  const rawId =
    item?.id ??
    item?.row_id ??
    item?.object_id ??
    item?.doc_id ??
    item?.node_id ??
    `${keyPrefix}-${idx + 1}`;

  const relatedDocIds = Array.isArray(item?.same_doc_ids) ? item.same_doc_ids : [];
  const relatedPaths = Array.isArray(item?.db_paths) ? item.db_paths : [];
  const detailPreview =
    relatedDocIds.length || relatedPaths.length
      ? `doc_master: ${relatedDocIds.join(', ') || '-'} | DB paths: ${relatedPaths.join(' ; ') || '-'}`
      : item?.reason || item?.preview || '-';

  return {
    rowId: `${kbId}-${keyPrefix}-${rawId}`,
    sourceId: rawId,
    objectId: item?.object_id ?? item?.objectId ?? null,
    docId: item?.doc_id ?? item?.docId ?? null,
    nodeId: item?.node_id ?? item?.nodeId ?? null,
    kbId,
    kbName,
    branchType: String(item?.branch_type || 'GENERAL').toUpperCase(),
    entryType: String(item?.entry_type || item?.entryType || 'file').toLowerCase(),
    applySupported: item?.apply_supported !== false && item?.applySupported !== false,
    targetPath: item?.target_path || item?.path || '-',
    rawAction: item?.action || null,
    rawChange: item?.change || null,
    rawReason: item?.reason || null,
    change: String(item?.change || item?.action || '-'),
    preview: detailPreview,
    updatedAt: item?.updated_at || item?.checked_at || '-',
  };
}

export default function SyncManageModal({ open, onClose, kbId }) {
  const [tab, setTab] = useState('edit');
  const [rowsByTab, setRowsByTab] = useState({ add: [], edit: [], delete: [], duplicate: [] });
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [reloadKey, setReloadKey] = useState(0);
  const [colWidths, setColWidths] = useState(() =>
    DATA_COLS.reduce((acc, c) => ({ ...acc, [c.key]: c.defaultWidth }), {})
  );
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [syncDirection, setSyncDirection] = useState('LOCAL_TO_WEB');
  const [localApplyGeneral, setLocalApplyGeneral] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const headerCheckRef = useRef(null);
  const tableRef = useRef(null);
  const listScrollRef = useRef(null);
  const rafRef = useRef(null);
  const resizeRef = useRef({
    key: null,
    pointerX: 0,
    maxWidth: Number.POSITIVE_INFINITY,
    edge: null,
    edgeStep: 12,
  });

  const rows = rowsByTab[tab] || [];
  const activeTab = TABS.find((t) => t.key === tab)?.label || '';
  const selectableRows = useMemo(() => rows.filter((r) => r.applySupported), [rows]);
  const allSelected = selectableRows.length > 0 && selectableRows.every((r) => selectedIds.has(r.rowId));
  const someSelected = selectableRows.some((r) => selectedIds.has(r.rowId));
  const tableWidth = useMemo(
    () => CHECK_COL_WIDTH + DATA_COLS.reduce((sum, c) => sum + (colWidths[c.key] || c.defaultWidth), 0),
    [colWidths]
  );
  const selectedRows = useMemo(
    () => rows.filter((r) => r.applySupported && selectedIds.has(r.rowId)),
    [rows, selectedIds]
  );
  const selectedSummary = useMemo(() => {
    const byBranch = { GENERAL: 0, KIWIX: 0, OTHER: 0 };
    const byAction = { insert: 0, remove: 0, path_mismatch: 0, same_content_multi_path: 0, other: 0 };
    selectedRows.forEach((r) => {
      const bt = String(r.branchType || '').toUpperCase();
      if (bt === 'GENERAL') byBranch.GENERAL += 1;
      else if (bt === 'KIWIX') byBranch.KIWIX += 1;
      else byBranch.OTHER += 1;

      const ch = String(r.change || '').toLowerCase();
      if (Object.prototype.hasOwnProperty.call(byAction, ch)) byAction[ch] += 1;
      else byAction.other += 1;
    });
    return { byBranch, byAction };
  }, [selectedRows]);

  useEffect(() => {
    setSelectedIds(new Set());
    setConfirmOpen(false);
  }, [tab, open]);

  useEffect(() => {
    if (syncDirection !== 'WEB_TO_LOCAL' || selectedSummary.byBranch.GENERAL <= 0) {
      setLocalApplyGeneral(false);
    }
  }, [syncDirection, selectedSummary.byBranch.GENERAL]);

  useEffect(() => {
    if (!headerCheckRef.current) return;
    headerCheckRef.current.indeterminate = !allSelected && someSelected;
  }, [allSelected, someSelected]);

  useEffect(() => {
    const tickResize = () => {
      rafRef.current = null;
      const s = resizeRef.current;
      if (!s.key) return;
      const col = DATA_COLS.find((c) => c.key === s.key);
      if (!col) return;

      const colIdx = DATA_COLS.findIndex((c) => c.key === s.key);
      if (colIdx < 0) return;
      const nth = colIdx + 2; // +1 for checkbox col, +1 for nth-child(1-based)
      const handleCell = tableRef.current?.querySelector?.(`thead th:nth-child(${nth})`);
      if (!handleCell) return;

      const rect = listScrollRef.current?.getBoundingClientRect?.();
      let px = s.pointerX;
      if (rect) px = Math.max(rect.left, Math.min(rect.right, px));
      const rawHandleX = handleCell.getBoundingClientRect().right;
      const handleX = rect
        ? Math.max(rect.left, Math.min(rect.right, rawHandleX))
        : rawHandleX;
      const diff = px - handleX;

      setColWidths((prev) => {
        const cur = prev[s.key] || col.defaultWidth;
        let delta = Math.abs(diff) > 0.5 ? diff : 0;
        if (delta === 0 && s.edge) {
          const edgeBoost = Math.max(2, s.edgeStep || 12);
          delta = s.edge === 'right' ? edgeBoost : -edgeBoost;
        }

        const next = Math.min(s.maxWidth, Math.max(col.min, cur + delta));
        if (next === cur) return prev;

        if (s.edge === 'right' && listScrollRef.current) {
          listScrollRef.current.scrollLeft += next - cur;
        }
        return { ...prev, [s.key]: next };
      });

      if (resizeRef.current.key) {
        rafRef.current = window.requestAnimationFrame(tickResize);
      }
    };

    const scheduleResize = () => {
      if (rafRef.current == null) {
        rafRef.current = window.requestAnimationFrame(tickResize);
      }
    };

    const onMouseMove = (e) => {
      const s = resizeRef.current;
      if (!s.key) return;
      s.pointerX = e.clientX;
      const rect = listScrollRef.current?.getBoundingClientRect?.();
      if (rect) {
        if (e.clientX >= rect.right - 2) {
          s.edge = 'right';
          const overshoot = Math.max(0, e.clientX - rect.right);
          s.edgeStep = Math.min(80, 12 + overshoot * 0.4);
        } else if (e.clientX <= rect.left + 2) {
          s.edge = 'left';
          const overshoot = Math.max(0, rect.left - e.clientX);
          s.edgeStep = Math.min(80, 12 + overshoot * 0.4);
        } else {
          s.edge = null;
          s.edgeStep = 12;
        }
      }
      scheduleResize();
    };

    const onMouseUp = () => {
      resizeRef.current = {
        key: null,
        pointerX: 0,
        maxWidth: Number.POSITIVE_INFINITY,
        edge: null,
        edgeStep: 12,
      };
      if (rafRef.current != null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      if (rafRef.current != null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    let alive = true;

    (async () => {
      setLoading(true);
      setError('');
      setInfo('');

      try {
        const kbRes = await http.get('/kb/trees', { kb_type: 'admin' });
        const kbItems = Array.isArray(kbRes) ? kbRes : kbRes?.items || [];
        const kbRows = kbItems
          .map((kb) => ({
            kbId: Number.parseInt(String(kb?.kb_id ?? kb?.id), 10),
            kbName: String(kb?.name || ''),
          }))
          .filter((kb) => Number.isFinite(kb.kbId));

        const targetKbId = Number.parseInt(String(kbId), 10);
        const scopedKbRows = Number.isFinite(targetKbId)
          ? kbRows.filter((kb) => kb.kbId === targetKbId)
          : kbRows;

        const settled = await Promise.allSettled(
          scopedKbRows.map((kb) =>
            http.get('/kb/sync/preview', { kb_id: kb.kbId }).then((res) => ({ kb, res }))
          )
        );

        let hasAuthError = false;
        let hasApiMissing = false;
        let hasKbMissing = false;
        const items = [];

        for (const s of settled) {
          if (s.status === 'fulfilled') {
            const { kb, res } = s.value;
            const arr = Array.isArray(res) ? res : res?.items || [];
            for (const it of arr) {
              items.push({
                ...it,
                kb_id: kb.kbId,
                kb_name: kb.kbName,
              });
            }
            continue;
          }

          const status = s?.reason?.status;
          const msg = String(s?.reason?.message || '').toLowerCase();
          if (status === 401 || status === 403) hasAuthError = true;
          if (status === 404 && msg.includes('kb not found')) hasKbMissing = true;
          else if (status === 404 || status === 405) hasApiMissing = true;
        }

        const grouped = splitByAction(items);
        if (!alive) return;

        setRowsByTab({
          add: grouped.add.map((it, i) => toRow('add', it, i)),
          edit: grouped.edit.map((it, i) => toRow('edit', it, i)),
          delete: grouped.delete.map((it, i) => toRow('delete', it, i)),
          duplicate: grouped.duplicate.map((it, i) => toRow('duplicate', it, i)),
        });

        if (hasAuthError) {
          setError('Unable to load sync items due to authentication/authorization error.');
        } else if (hasKbMissing && settled.length > 0 && items.length === 0) {
          setInfo('The selected KB does not exist in the sync comparison DB. (0 items)');
        } else if (hasApiMissing && settled.length > 0 && items.length === 0) {
          setInfo('Validation API is unavailable, so change items cannot be returned. (0 items)');
        } else if (!items.length) {
          setInfo('No verified change items found.');
        }
      } catch (e) {
        if (!alive) return;
        setRowsByTab({ add: [], edit: [], delete: [], duplicate: [] });
        const status = e?.status;
        if (status === 404 || status === 405) {
          setInfo('Validation API is unavailable, so change items cannot be returned. (0 items)');
        } else {
          setError(e?.message || 'Failed to load sync items.');
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [open, kbId, reloadKey]);

  const toggleAll = (checked) => {
    if (!checked) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(new Set(selectableRows.map((r) => r.rowId)));
  };

  const toggleOne = (id, checked) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const buildSelectedItems = () =>
    selectedRows.map((r) => {
      const num = Number(r.sourceId);
      const action = String(r.rawAction || r.change || '-');
      const change = String(r.rawChange || r.change || '-');
      return {
        id: Number.isFinite(num) ? num : r.sourceId,
        object_id: r.objectId == null ? null : Number(r.objectId),
        doc_id: r.docId == null ? null : Number(r.docId),
        node_id: r.nodeId == null ? null : Number(r.nodeId),
        branch_type: String(r.branchType || '').toUpperCase(),
        target_path: r.targetPath || '-',
        action,
        change,
        reason: r.rawReason || null,
      };
    });

  const effectText = (change) => {
    const c = String(change || '').toLowerCase();
    if (c === 'insert' || c === 'add') return 'DB insert/link candidate | tables: tree_nodes, pf_node_props, doc_master, doc_revisions, storage_objects';
    if (c === 'remove' || c === 'delete') return 'DB delete/unlink candidate | tables: tree_nodes, pf_node_props, doc_revisions, doc_master, storage_objects';
    if (c === 'path_mismatch' || c === 'edit' || c === 'update') return 'DB path/meta update candidate | tables: storage_objects, doc_revisions, doc_master, pf_node_props, tree_nodes';
    if (c === 'folder_path_mismatch') return 'Folder rename/move detected from child file paths (preview-only item)';
    if (c === 'same_content_multi_path' || c === 'duplicate_path') return 'DB duplicate-path/link candidate | tables: tree_nodes, pf_node_props, doc_master, doc_revisions';
    return 'DB change candidate';
  };

  const handleRefresh = () => {
    setReloadKey((v) => v + 1);
  };

  const handleOpenConfirm = () => {
    if (!selectedRows.length || !Number.isFinite(Number(kbId))) return;
    setConfirmOpen(true);
  };

  const handleConfirmSave = async () => {
    if (!selectedRows.length || !Number.isFinite(Number(kbId))) return;
    const selectedItems = buildSelectedItems();
    const batches = [];
    for (let i = 0; i < selectedItems.length; i += APPLY_BATCH_SIZE) {
      batches.push(selectedItems.slice(i, i + APPLY_BATCH_SIZE));
    }
    const doLocalApply = syncDirection === 'WEB_TO_LOCAL' && localApplyGeneral;
    try {
      setSaving(true);
      let appliedTotal = 0;
      let failedTotal = 0;
      for (const batch of batches) {
        const res = await http.post('/kb/sync/apply', {
          kb_id: Number(kbId),
          selected_items: batch,
          local_apply: doLocalApply,
          sync_direction: syncDirection,
        }, { timeout: 1800000 });
        appliedTotal += Number(res?.applied_count || 0);
        failedTotal += Number(res?.failed_count || 0);
      }
      setConfirmOpen(false);
      window.alert(`Applied: ${appliedTotal}, Failed: ${failedTotal}`);
    } catch (e) {
      const msg = String(e?.message || '');
      if (e?.name === 'AbortError' || msg.toLowerCase().includes('aborted')) {
        window.alert('Sync apply timed out on client side. The server may still be processing. Please refresh and re-check results.');
      } else {
        window.alert(msg || 'Failed to apply sync job.');
      }
    } finally {
      setSaving(false);
    }
  };

  const measureColumnMaxWidth = (key) => {
    const tableEl = tableRef.current;
    const colIdx = DATA_COLS.findIndex((c) => c.key === key);
    if (!tableEl || colIdx < 0) return Number.POSITIVE_INFINITY;
    const nth = colIdx + 2;
    const selector = `thead th:nth-child(${nth}), tbody td:nth-child(${nth})`;
    const cells = tableEl.querySelectorAll(selector);
    let max = DATA_COLS[colIdx].min;
    cells.forEach((el) => {
      const w = Math.ceil(el.scrollWidth) + 24;
      if (w > max) max = w;
    });
    return max;
  };

  const startResize = (key, e) => {
    e.preventDefault();
    const rect = listScrollRef.current?.getBoundingClientRect?.();
    const startX = rect
      ? Math.max(rect.left, Math.min(rect.right, e.clientX))
      : e.clientX;
    resizeRef.current = {
      key,
      pointerX: startX,
      maxWidth: Math.max(colWidths[key] || 0, measureColumnMaxWidth(key)),
      edge: null,
      edgeStep: 12,
    };
    if (rafRef.current != null) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  const fitColumnToData = (key) => {
    const tableEl = tableRef.current;
    const colIdx = DATA_COLS.findIndex((c) => c.key === key);
    if (!tableEl || colIdx < 0) return;
    const nth = colIdx + 2;
    const selector = `thead th:nth-child(${nth}), tbody td:nth-child(${nth})`;
    const cells = tableEl.querySelectorAll(selector);
    let max = DATA_COLS[colIdx].min;
    cells.forEach((el) => {
      const w = Math.ceil(el.scrollWidth) + 24;
      if (w > max) max = w;
    });
    setColWidths((prev) => ({ ...prev, [key]: max }));
  };

  if (!open) return null;

  const routeText = (row) => {
    const bt = String(row?.branchType || '').toUpperCase();
    if (syncDirection === 'WEB_TO_LOCAL') {
      if (bt === 'KIWIX') return 'Web/DB -> Local (blocked for KIWIX)';
      return localApplyGeneral ? 'Web/DB -> Local' : 'Web/DB -> Local (preview only)';
    }
    return 'Local -> Web/DB';
  };

  return (
    <div className="sync-modal-overlay" role="dialog" aria-modal="true" aria-label="Sync Management">
      <div className="sync-modal-card">
        <div className="sync-modal-head">
          <div className="sync-modal-title">Sync Management</div>
          <button type="button" className="sync-modal-close" onClick={onClose} aria-label="Close">
            X
          </button>
        </div>

        <div className="sync-modal-tabs" role="tablist" aria-label="Sync Management Tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className={`sync-modal-tab${tab === t.key ? ' active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="sync-modal-meta">
          <span>{activeTab} List</span>
          <span>
            Total {rows.length} / Selected {selectedIds.size}
          </span>
        </div>
        <div className="sync-modal-note">
          {tab === 'duplicate'
            ? 'A single logical document with identical content (checksum) is linked to multiple paths.'
            : 'Comparison is based on local files/folders. Folder-only rows are preview-only.'}
        </div>

        <div className="sync-modal-list-scroll" ref={listScrollRef}>
          {loading ? (
            <div className="sync-modal-empty">Loading data...</div>
          ) : error ? (
            <div className="sync-modal-error">{error}</div>
          ) : info ? (
            <div className="sync-modal-empty">{info}</div>
          ) : rows.length === 0 ? (
            <div className="sync-modal-empty">No verified change items found.</div>
          ) : (
            <table ref={tableRef} className="sync-modal-table" style={{ width: tableWidth, minWidth: tableWidth }}>
              <colgroup>
                <col className="col-check" />
                <col style={{ width: colWidths.branch }} />
                <col style={{ width: colWidths.target }} />
                <col style={{ width: colWidths.change }} />
                <col style={{ width: colWidths.preview }} />
              </colgroup>
              <thead>
                <tr>
                  <th>
                    <input
                      ref={headerCheckRef}
                      type="checkbox"
                      checked={allSelected}
                      onChange={(e) => toggleAll(e.target.checked)}
                      aria-label="Select all"
                    />
                  </th>
                  {DATA_COLS.map((col) => (
                    <th key={col.key} className="sync-th-resizable">
                      <span>{col.label}</span>
                      <button
                        type="button"
                        className="sync-col-resizer"
                        aria-label={`Resize ${col.label}`}
                        title="Drag to resize / Double-click to fit data"
                        onMouseDown={(e) => startResize(col.key, e)}
                        onDoubleClick={() => fitColumnToData(col.key)}
                      />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.rowId}>
                    <td>
                      <input
                        type="checkbox"
                        checked={r.applySupported && selectedIds.has(r.rowId)}
                        disabled={!r.applySupported}
                        onChange={(e) => toggleOne(r.rowId, e.target.checked)}
                        aria-label={r.applySupported ? `Select ${r.rowId}` : `Preview-only ${r.rowId}`}
                        title={r.applySupported ? 'Selectable' : 'Preview-only row'}
                      />
                    </td>
                    <td>{r.branchType}</td>
                    <td title={r.targetPath}>{r.targetPath}</td>
                    <td>{r.change}</td>
                    <td title={r.preview}>{r.preview}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="sync-modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
          <button type="button" className="btn" onClick={handleRefresh} disabled={loading || saving}>
            Refresh
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={saving || selectedIds.size === 0 || !rows.length}
            onClick={handleOpenConfirm}
          >
            {saving ? 'Saving...' : 'Confirm Selected Items'}
          </button>
        </div>
      </div>
      {confirmOpen ? (
        <div className="sync-confirm-overlay" role="dialog" aria-modal="true" aria-label="Sync Confirm">
          <div className="sync-confirm-card">
            <div className="sync-confirm-head">
              <div className="sync-confirm-title">Confirm Sync Changes</div>
              <button
                type="button"
                className="sync-modal-close"
                onClick={() => setConfirmOpen(false)}
                aria-label="Close confirm"
              >
                ��
              </button>
            </div>
            <div className="sync-confirm-warning">
              Local files/folders will NOT be changed. This request is DB-side sync processing only.
            </div>
            <div className="sync-confirm-info">
              PDF viewer linkage is validated via storage_objects(relative_path, sub_path, name, file_right) and latest doc_revisions object mapping.
            </div>
            <div className="sync-confirm-options">
              <div className="sync-confirm-option-row">
                <span className="sync-confirm-option-title">Direction</span>
                <label>
                  <input
                    type="radio"
                    name="sync-direction"
                    checked={syncDirection === 'LOCAL_TO_WEB'}
                    onChange={() => setSyncDirection('LOCAL_TO_WEB')}
                  />
                  Local -&gt; Web/DB
                </label>
                <label>
                  <input
                    type="radio"
                    name="sync-direction"
                    checked={syncDirection === 'WEB_TO_LOCAL'}
                    onChange={() => setSyncDirection('WEB_TO_LOCAL')}
                  />
                  Web/DB -&gt; Local
                </label>
              </div>
              {syncDirection === 'WEB_TO_LOCAL' && selectedSummary.byBranch.GENERAL > 0 ? (
                <div className="sync-confirm-option-row">
                  <span className="sync-confirm-option-title">Local Apply</span>
                  <label>
                    <input
                      type="checkbox"
                      checked={localApplyGeneral}
                      onChange={(e) => setLocalApplyGeneral(e.target.checked)}
                    />
                    Apply local file/folder changes for GENERAL only (KIWIX excluded)
                  </label>
                </div>
              ) : null}
            </div>
            <div className="sync-confirm-meta">
              <span className="sync-confirm-chip">
                Selected {selectedRows.length}
              </span>
              <span className="sync-confirm-chip">
                GENERAL {selectedSummary.byBranch.GENERAL} / KIWIX {selectedSummary.byBranch.KIWIX}
              </span>
              <span className="sync-confirm-chip">
                insert {selectedSummary.byAction.insert}
              </span>
              <span className="sync-confirm-chip">
                remove {selectedSummary.byAction.remove}
              </span>
              <span className="sync-confirm-chip">
                edit {selectedSummary.byAction.path_mismatch + selectedSummary.byAction.other}
              </span>
            </div>
            <div className="sync-confirm-scroll">
              <table className="sync-confirm-table">
                <thead>
                  <tr>
                    <th>branch_type</th>
                    <th>Target Path</th>
                    <th>Direction</th>
                    <th>Requested Action</th>
                    <th>DB Expected Effect</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedRows.map((r) => (
                    <tr key={`confirm-${r.rowId}`}>
                      <td>{r.branchType}</td>
                      <td title={r.targetPath}>{r.targetPath}</td>
                      <td>{routeText(r)}</td>
                      <td>{r.change}</td>
                      <td>{effectText(r.change)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="sync-confirm-foot">
              <button type="button" className="btn" onClick={() => setConfirmOpen(false)} disabled={saving}>
                Cancel
              </button>
              <button type="button" className="btn primary" onClick={handleConfirmSave} disabled={saving || !selectedRows.length}>
                {saving ? 'Saving...' : (syncDirection === 'WEB_TO_LOCAL' && localApplyGeneral ? 'Apply (DB + Local GENERAL)' : 'Apply (DB Only)')}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
