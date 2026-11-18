import React, { useEffect, useMemo, useRef, useState } from 'react';

function nodeId(node) {
  const value = Number(node?.node_id ?? node?.id);
  return Number.isFinite(value) ? value : null;
}

function policyNeedsTargetGroup(policy) {
  if (!policy) return false;
  const code = String(policy.policy_code || '').trim().toUpperCase();
  if (['GROUP', 'SG'].includes(code)) return true;
  if (['PUBLIC', 'PRIVATE', 'S1', 'S2', 'S3'].includes(code)) return false;
  const group = policy.group || {};
  const other = policy.other || {};
  return Boolean(group.r || group.w || group.x) && !Boolean(other.r || other.w || other.x);
}

function buildIndex(kbId, kbName, nodes) {
  const folders = [{ id: Number(kbId), label: kbName || `KB #${kbId}` }];
  const documents = [];
  const walk = (items, folderPath, ancestorFolderIds, parentId) => {
    for (const node of items || []) {
      const id = nodeId(node);
      if (id == null) continue;
      const name = String(node.name || node.alias || `#${id}`);
      if (node.is_folder) {
        const path = [...folderPath, name];
        folders.push({ id, label: path.join(' / ') });
        walk(node.children, path, [...ancestorFolderIds, id], id);
      } else {
        documents.push({
          id,
          name,
          path: [...folderPath, name].join(' / '),
          parentId: Number(parentId),
          ancestorFolderIds,
          policyCode: String(node.policy_code || ''),
        });
      }
    }
  };
  walk(nodes, [], [Number(kbId)], Number(kbId));
  return { folders, documents };
}

export default function BulkPermissionModal({
  open,
  kbId,
  kbName,
  nodes,
  policies,
  groups,
  initialFolderId,
  initialSelectedIds,
  onClose,
  onApply,
}) {
  const [folderId, setFolderId] = useState(null);
  const [includeDescendants, setIncludeDescendants] = useState(true);
  const [query, setQuery] = useState('');
  const [policyCode, setPolicyCode] = useState('');
  const [targetGroupId, setTargetGroupId] = useState('');
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const selectAllRef = useRef(null);
  const index = useMemo(() => buildIndex(kbId, kbName, nodes), [kbId, kbName, nodes]);

  useEffect(() => {
    if (!open) return;
    const requestedFolderId = Number(initialFolderId);
    setFolderId(index.folders.some((folder) => folder.id === requestedFolderId) ? requestedFolderId : Number(kbId));
    setIncludeDescendants(true);
    setQuery('');
    setPolicyCode('');
    setTargetGroupId('');
    setSelectedIds(new Set((initialSelectedIds || []).map(Number).filter(Number.isFinite)));
    setSaving(false);
    setError('');
  }, [open, initialFolderId, initialSelectedIds, index.folders, kbId, policies]);

  const candidates = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return index.documents.filter((document) => {
      const inScope = includeDescendants
        ? document.ancestorFolderIds.includes(Number(folderId))
        : document.parentId === Number(folderId);
      if (!inScope) return false;
      if (!needle) return true;
      return (
        document.name.toLocaleLowerCase().includes(needle) ||
        document.path.toLocaleLowerCase().includes(needle) ||
        document.policyCode.toLocaleLowerCase().includes(needle)
      );
    });
  }, [folderId, includeDescendants, index.documents, query]);

  const selectedPolicy = useMemo(
    () => policies.find((policy) => policy.policy_code === policyCode) || null,
    [policies, policyCode]
  );
  const requiresGroup = policyNeedsTargetGroup(selectedPolicy);
  const candidateIds = useMemo(() => candidates.map((document) => document.id), [candidates]);
  const selectedCandidateCount = candidateIds.filter((id) => selectedIds.has(id)).length;
  const allSelected = candidateIds.length > 0 && selectedCandidateCount === candidateIds.length;

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selectedCandidateCount > 0 && !allSelected;
  }, [allSelected, selectedCandidateCount]);

  if (!open) return null;

  const toggleAll = (checked) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      for (const id of candidateIds) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  };

  const apply = async () => {
    const nodeIds = Array.from(selectedIds);
    if (!nodeIds.length || !policyCode || (requiresGroup && !targetGroupId)) return;
    if (!window.confirm(`Apply ${policyCode} permission to ${nodeIds.length} selected document(s)?`)) return;
    setSaving(true);
    setError('');
    try {
      await onApply({
        node_ids: nodeIds,
        policy_code: policyCode,
        target_group_id: requiresGroup ? Number(targetGroupId) : null,
      });
      onClose();
    } catch (caught) {
      setError(caught?.userMessage || caught?.detail || caught?.message || 'Permission update failed.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal admin-bulk-permission-modal" role="dialog" aria-modal="true" aria-label="Bulk permission change">
        <div className="modal-head">
          <div>
            <div className="modal-title">Bulk Permission Change</div>
            <div className="admin-bulk-move-subtitle">The folder defines scope; ACLs are stored on documents only.</div>
          </div>
          <button type="button" className="modal-close" onClick={onClose} disabled={saving} aria-label="Close">X</button>
        </div>
        <div className="modal-body admin-bulk-permission-body">
          <section className="admin-permission-controls">
            <label className="modal-field">
              <span className="modal-label">Folder scope</span>
              <select className="input" value={folderId ?? ''} onChange={(event) => setFolderId(Number(event.target.value))}>
                {index.folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.label}</option>)}
              </select>
            </label>
            <label className="admin-bulk-select-all">
              <input type="checkbox" checked={includeDescendants} onChange={(event) => setIncludeDescendants(event.target.checked)} />
              Include all descendant folders
            </label>
            <label className="modal-field">
              <span className="modal-label">Find documents</span>
              <input className="input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, path, or policy" autoFocus />
            </label>
          </section>
          <section className="admin-permission-document-pane">
            <div className="admin-bulk-move-pane-head">
              <div><strong>Documents</strong><span>{candidates.length} result(s), {selectedIds.size} selected</span></div>
              <label className="admin-bulk-select-all">
                <input ref={selectAllRef} type="checkbox" checked={allSelected} disabled={!candidateIds.length} onChange={(event) => toggleAll(event.target.checked)} />
                Select search results
              </label>
            </div>
            <div className="admin-permission-document-list">
              {!candidates.length ? <div className="empty">No documents in this scope.</div> : candidates.map((document) => (
                <label key={document.id} className={`admin-permission-document-row${selectedIds.has(document.id) ? ' selected' : ''}`}>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(document.id)}
                    onChange={(event) => setSelectedIds((previous) => {
                      const next = new Set(previous);
                      if (event.target.checked) next.add(document.id);
                      else next.delete(document.id);
                      return next;
                    })}
                  />
                  <span className="admin-permission-document-name"><strong>{document.name}</strong><small>{document.path}</small></span>
                  <span className="admin-bulk-state current">{document.policyCode || 'No policy'}</span>
                </label>
              ))}
            </div>
          </section>
          <section className="admin-permission-policy-grid">
            <label className="modal-field">
              <span className="modal-label">New policy</span>
              <select className="input" value={policyCode} onChange={(event) => {
                const nextCode = event.target.value;
                setPolicyCode(nextCode);
                if (!policyNeedsTargetGroup(policies.find((policy) => policy.policy_code === nextCode))) setTargetGroupId('');
              }}>
                <option value="" disabled>Select policy</option>
                {policies.map((policy) => (
                  <option key={policy.policy_code} value={policy.policy_code}>
                    {policy.policy_code}{Array.isArray(policy.matrix) ? ` (${policy.matrix.join(' / ')})` : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="modal-field">
              <span className="modal-label">Target group</span>
              <select className="input" value={targetGroupId} onChange={(event) => setTargetGroupId(event.target.value)} disabled={!requiresGroup}>
                <option value="">{requiresGroup ? 'Select group' : 'Not required'}</option>
                {groups.map((group) => <option key={group.group_id} value={group.group_id}>{group.name} #{group.group_id}</option>)}
              </select>
            </label>
          </section>
        </div>
        {error && <div className="admin-bulk-move-error" role="alert">{error}</div>}
        <div className="modal-foot">
          <span className="admin-bulk-move-summary">
            {selectedIds.size ? `${selectedIds.size} document(s) will change to ${policyCode || 'the selected policy'}.` : 'Select at least one document.'}
          </span>
          <button type="button" className="btn ghost" onClick={onClose} disabled={saving}>Cancel</button>
          <button type="button" className="btn primary" onClick={apply} disabled={saving || !selectedIds.size || !policyCode || (requiresGroup && !targetGroupId)}>
            {saving ? 'Applying...' : 'Apply'}
          </button>
        </div>
      </div>
    </div>
  );
}
