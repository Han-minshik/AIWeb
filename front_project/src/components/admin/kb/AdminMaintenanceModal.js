import React, { useEffect, useMemo, useState } from 'react';
import {
  addGroupMember,
  createGroup,
  deactivateGroup,
  deactivateGroupMember,
  listAiIndexJobs,
  listDeletedDocuments,
  listGroupMembers,
  listGroups,
  purgeDeletedDocument,
  restoreDeletedDocument,
  retryAiIndexJobs,
  updateGroupMember,
} from '../../../services/adminMaintenanceService';
import { subscribePipelineJobs } from '../../../services/pipeline/pipelineJobService';
import { pipelineJobLabel, pipelineStatusClass, retryableJobs } from '../../../utils/pipelineJobs';
import { listUsers } from '../../../services/users/userService';
import '../../../styles/admin/syncManageModal.css';

const TABS = [
  { key: 'groups', label: 'Groups' },
  { key: 'deleted', label: 'Deleted Docs' },
  { key: 'ai', label: 'AI Jobs' },
];

const USER_SELECT_PAGE_SIZE = 200;

function fmt(v) {
  if (!v) return '-';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString('ko-KR');
}

function pathOf(row) {
  return [row?.relative_path, row?.sub_path, row?.doc_name].filter(Boolean).join('/');
}

function filterAiJobs(rows, query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return rows || [];
  return (rows || []).filter((row) =>
    [
      row.job_id,
      row.ai_document_id,
      row.action,
      row.status,
      row.index_status,
      row.last_error,
      pathOf(row),
    ]
      .filter((v) => v != null)
      .some((v) => String(v).toLowerCase().includes(q))
  );
}

export default function AdminMaintenanceModal({ open, initialTab = 'groups', onClose }) {
  const [tab, setTab] = useState(initialTab);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [groups, setGroups] = useState([]);
  const [users, setUsers] = useState([]);
  const [selectedGroupId, setSelectedGroupId] = useState(null);
  const [members, setMembers] = useState([]);
  const [groupName, setGroupName] = useState('');
  const [managerUserId, setManagerUserId] = useState('');
  const [memberUserId, setMemberUserId] = useState('');
  const [memberRole, setMemberRole] = useState('member');

  const [deletedRows, setDeletedRows] = useState([]);
  const [selectedDeletedIds, setSelectedDeletedIds] = useState(() => new Set());
  const [aiRows, setAiRows] = useState([]);
  const [aiStatus, setAiStatus] = useState('failed');
  const [aiSearch, setAiSearch] = useState('');
  const [selectedJobIds, setSelectedJobIds] = useState(() => new Set());

  useEffect(() => {
    if (open) setTab(initialTab || 'groups');
  }, [open, initialTab]);

  const selectedGroup = useMemo(
    () => groups.find((g) => Number(g.group_id) === Number(selectedGroupId)) || null,
    [groups, selectedGroupId]
  );

  const userOptions = useMemo(
    () =>
      (users || []).map((u) => ({
        id: Number(u.user_id ?? u.id),
        label: `${u.username || u.name || u.full_name || `User ${u.user_id ?? u.id}`} (#${u.user_id ?? u.id})${u.email ? ` - ${u.email}` : ''}`,
      })).filter((u) => Number.isFinite(u.id)),
    [users]
  );

  const loadGroups = async () => {
    const [groupRows, userRes] = await Promise.all([
      listGroups({ include_inactive: true }),
      listUsers({ page: 1, page_size: USER_SELECT_PAGE_SIZE }),
    ]);
    const rows = groupRows || [];
    setGroups(rows);
    setUsers(userRes?.items || []);
    const firstId = rows[0]?.group_id ?? null;
    setSelectedGroupId((prev) => (rows.some((g) => Number(g.group_id) === Number(prev)) ? prev : firstId));
    return rows;
  };

  const loadMembers = async (groupId) => {
    if (!groupId) {
      setMembers([]);
      return;
    }
    setMembers(await listGroupMembers(groupId, { include_inactive: false }));
  };

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      if (tab === 'groups') {
        await loadGroups();
      } else if (tab === 'deleted') {
        setDeletedRows(await listDeletedDocuments('pending'));
        setSelectedDeletedIds(new Set());
      } else if (tab === 'ai') {
        setAiRows(await listAiIndexJobs(aiStatus));
        setSelectedJobIds(new Set());
      }
    } catch (e) {
      setError(e?.message || 'Failed to load maintenance data.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab, aiStatus]);

  useEffect(() => {
    if (!open || tab !== 'ai') return undefined;
    return subscribePipelineJobs({
      scope: 'se',
      onSnapshot: (payload) => {
        const allRows = Array.isArray(payload?.items) ? payload.items : [];
        const visibleRows = aiStatus === 'all'
          ? allRows
          : allRows.filter((row) => row.status === aiStatus);
        setAiRows(visibleRows);
        setSelectedJobIds((previous) => new Set(
          Array.from(previous).filter((id) =>
            allRows.some((row) => row.job_id === id && row.retry_allowed === true)
          )
        ));
      },
    });
  }, [open, tab, aiStatus]);

  useEffect(() => {
    if (!open || tab !== 'groups') return;
    loadMembers(selectedGroupId).catch((e) => setError(e?.message || 'Failed to load group members.'));
  }, [open, tab, selectedGroupId]);

  if (!open) return null;

  const actionError = (e, fallback) => {
    setNotice('');
    const message = e?.userMessage || e?.message || fallback;
    setError(message);
    return message;
  };

  const refreshSelectedGroup = async (groupId = selectedGroupId) => {
    await loadGroups();
    if (groupId) await loadMembers(groupId);
  };

  const createNewGroup = async () => {
    const name = groupName.trim();
    if (!name || !managerUserId) return;
    setError('');
    setNotice('');
    try {
      const created = await createGroup({
        name,
        manager_user_id: Number(managerUserId),
      });
      setGroupName('');
      setManagerUserId('');
      await loadGroups();
      if (created?.group_id) setSelectedGroupId(created.group_id);
      setNotice(`Group "${name}" created.`);
    } catch (e) {
      actionError(e, 'Failed to create group.');
    }
  };

  const addMember = async () => {
    if (!selectedGroupId || !memberUserId) return;
    setError('');
    setNotice('');
    try {
      await addGroupMember(selectedGroupId, { user_id: Number(memberUserId), role: memberRole });
      setMemberUserId('');
      setMemberRole('member');
      await refreshSelectedGroup(selectedGroupId);
      setNotice('Group member saved. Users tab department is synced.');
    } catch (e) {
      actionError(e, 'Failed to add or update group member.');
    }
  };

  const deleteSelectedGroup = async (group) => {
    if (!group?.group_id) return;
    if (!window.confirm(`Delete group "${group.name || group.group_id}"?`)) return;
    setError('');
    setNotice('');
    try {
      await deactivateGroup(group.group_id);
      const rows = await loadGroups();
      const nextGroupId = rows[0]?.group_id ?? null;
      setSelectedGroupId(nextGroupId);
      if (nextGroupId) await loadMembers(nextGroupId);
      else setMembers([]);
      setNotice('Group deleted. User departments and group members were cleared.');
    } catch (e) {
      actionError(e, 'Failed to delete group.');
    }
  };

  const changeMemberRole = async (member, role) => {
    if (!selectedGroupId || !member?.user_id) return;
    setError('');
    setNotice('');
    try {
      await updateGroupMember(selectedGroupId, member.user_id, { role });
      await refreshSelectedGroup(selectedGroupId);
      setNotice('Member role updated.');
    } catch (e) {
      actionError(e, 'Failed to update member role.');
    }
  };

  const disableMember = async (member) => {
    if (!selectedGroupId || !member?.user_id) return;
    setError('');
    setNotice('');
    try {
      await deactivateGroupMember(selectedGroupId, member.user_id);
      await refreshSelectedGroup(selectedGroupId);
      setNotice('Member disabled. Users tab department is synced.');
    } catch (e) {
      actionError(e, 'Failed to disable group member.');
    }
  };

  const retrySelected = async () => {
    const ids = Array.from(selectedJobIds);
    if (!ids.length) return;
    const result = await retryAiIndexJobs(ids);
    setSelectedJobIds(new Set());
    setNotice(`Retry requested: ${result?.succeeded || 0} succeeded, ${result?.failed || 0} failed.`);
    await load();
  };

  const toggleDeleted = (deletionId, checked) => {
    setSelectedDeletedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(deletionId);
      else next.delete(deletionId);
      return next;
    });
  };

  const toggleAllDeleted = (checked) => {
    setSelectedDeletedIds(checked ? new Set(deletedRows.map((r) => r.deletion_id)) : new Set());
  };

  const purgeSelectedDeleted = async () => {
    const ids = Array.from(selectedDeletedIds);
    if (!ids.length) return;
    if (!window.confirm(`Permanently purge ${ids.length} deleted document(s)?`)) return;
    try {
      setLoading(true);
      for (const id of ids) {
        await purgeDeletedDocument(id);
      }
      setSelectedDeletedIds(new Set());
      await load();
    } finally {
      setLoading(false);
    }
  };


  const toggleJob = (jobId, checked) => {
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(jobId);
      else next.delete(jobId);
      return next;
    });
  };

  const visibleAiRows = filterAiJobs(aiRows, aiSearch);
  const visibleRetryableAiRows = retryableJobs(visibleAiRows);

  return (
    <div className="sync-modal-overlay" role="dialog" aria-modal="true" aria-label="Admin maintenance">
      <div className="sync-modal-card" style={{ width: 'min(1120px, 94vw)', height: 'min(760px, 88vh)' }}>
        <div className="sync-modal-head">
          <div>
            <div className="sync-modal-title">Maintenance</div>
            <div className="muted" style={{ fontSize: 12 }}>
              Groups, deletion recovery, and AI indexing retry
            </div>
          </div>
          <button type="button" className="sync-modal-close" onClick={onClose} aria-label="Close">
            X
          </button>
        </div>

        <div className="sync-modal-tabs" role="tablist" aria-label="Maintenance tabs">
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

        <div className="sync-modal-foot" style={{ justifyContent: 'space-between' }}>
          <div className="muted">{loading ? 'Loading...' : error || notice || 'Ready'}</div>
          <button type="button" className="btn" onClick={load} disabled={loading}>
            Refresh
          </button>
        </div>
        <div className={`sync-modal-list-scroll maintenance-modal-body${tab === 'ai' ? ' ai-jobs-body' : ''}`} style={{ padding: 12 }}>
          {tab === 'groups' ? (
            <div className="maintenance-grid">
              <div className="card maintenance-scroll-card">
                <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                  <input className="input" placeholder="Group name" value={groupName} onChange={(e) => setGroupName(e.target.value)} />
                  <select
                    className="input"
                    value={managerUserId}
                    onChange={(e) => setManagerUserId(e.target.value)}
                    style={{ minWidth: 260 }}
                  >
                    <option value="">Select manager</option>
                    {userOptions.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.label}
                      </option>
                    ))}
                  </select>
                  <button type="button" className="btn primary" onClick={createNewGroup} disabled={!groupName.trim() || !managerUserId}>
                    Create
                  </button>
                </div>
                <table className="table" style={{ width: '100%' }}>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Name</th>
                      <th>State</th>
                      <th>Managers</th>
                      <th>Members</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groups.map((g) => (
                      <tr key={g.group_id} className={Number(g.group_id) === Number(selectedGroupId) ? 'selected' : ''}>
                        <td>{g.group_id}</td>
                        <td>
                          <button type="button" className="btn ghost" onClick={() => setSelectedGroupId(g.group_id)}>
                            {g.name}
                          </button>
                        </td>
                        <td>{g.is_active ? 'active' : 'inactive'}</td>
                        <td>{g.manager_count || 0}</td>
                        <td>{g.member_count || 0}</td>
                        <td>
                          <button type="button" className="btn ghost danger" onClick={() => deleteSelectedGroup(g)}>
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="card maintenance-scroll-card">
                <div style={{ fontWeight: 700, marginBottom: 8 }}>
                  Members {selectedGroup ? `- ${selectedGroup.name}` : ''}
                </div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                  <select
                    className="input"
                    value={memberUserId}
                    onChange={(e) => setMemberUserId(e.target.value)}
                    style={{ minWidth: 260 }}
                  >
                    <option value="">Select user</option>
                    {userOptions.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.label}
                      </option>
                    ))}
                  </select>
                  <select className="input" value={memberRole} onChange={(e) => setMemberRole(e.target.value)}>
                    <option value="member">member</option>
                    <option value="manager">manager</option>
                  </select>
                  <button type="button" className="btn primary" onClick={addMember} disabled={!selectedGroupId || !memberUserId}>
                    Add/Update
                  </button>
                </div>
                <table className="table" style={{ width: '100%' }}>
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Email</th>
                      <th>Role</th>
                      <th>State</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((m) => (
                      <tr key={m.group_member_id}>
                        <td>{m.username || m.user_id}</td>
                        <td>{m.email || '-'}</td>
                        <td>
                          <select
                            className="input"
                            value={m.role}
                            onChange={(e) => changeMemberRole(m, e.target.value)}
                          >
                            <option value="member">member</option>
                            <option value="manager">manager</option>
                          </select>
                        </td>
                        <td>{m.is_active ? 'active' : 'inactive'}</td>
                        <td>
                          <button type="button" className="btn ghost danger" onClick={() => disableMember(m)}>
                            Disable
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {tab === 'deleted' ? (
            <div className="deleted-docs-section">
              <div className="deleted-docs-action-row">
                <label className="deleted-docs-select-all">
                  <input
                    type="checkbox"
                    checked={deletedRows.length > 0 && selectedDeletedIds.size === deletedRows.length}
                    onChange={(e) => toggleAllDeleted(e.target.checked)}
                  />
                  <span>Select All</span>
                </label>
                <button
                  type="button"
                  className="btn ghost danger"
                  onClick={purgeSelectedDeleted}
                  disabled={!selectedDeletedIds.size || loading}
                >
                  Purge Selected ({selectedDeletedIds.size})
                </button>
              </div>
              <div className="deleted-docs-table-wrap">
                <table className="table deleted-docs-table">
                  <thead>
                    <tr>
                      <th aria-label="Select" />
                      <th>ID</th>
                      <th>Document</th>
                      <th>Status</th>
                      <th>Detected</th>
                      <th>Remain Until</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deletedRows.map((r) => (
                      <tr key={r.deletion_id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedDeletedIds.has(r.deletion_id)}
                            onChange={(e) => toggleDeleted(r.deletion_id, e.target.checked)}
                            aria-label={`Select deleted document ${r.deletion_id}`}
                          />
                        </td>
                        <td>{r.deletion_id}</td>
                        <td title={pathOf(r)}>{pathOf(r)}</td>
                        <td>{r.doc_status || r.status}</td>
                        <td>{fmt(r.detected_at)}</td>
                        <td>{fmt(r.remain_until)}</td>
                        <td>
                          <div className="deleted-docs-row-actions">
                            <button type="button" className="btn ghost" onClick={() => restoreDeletedDocument(r.deletion_id).then(load)}>
                              Restore
                            </button>
                            <button type="button" className="btn ghost danger" onClick={() => purgeDeletedDocument(r.deletion_id).then(load)}>
                              Purge
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {tab === 'ai' ? (
            <div className="ai-jobs-section">
              <div className="ai-jobs-action-row">
                <select
                  className="input"
                  value={aiStatus}
                  onChange={(e) => setAiStatus(e.target.value)}
                >
                  <option value="failed">failed</option>
                  <option value="pending">pending</option>
                  <option value="running">running</option>
                  <option value="sent">sent</option>
                  <option value="completed">completed</option>
                  <option value="cancelled">cancelled</option>
                  <option value="superseded">superseded</option>
                  <option value="all">all</option>
                </select>

                <button type="button" className="btn primary" onClick={retrySelected} disabled={!selectedJobIds.size}>
                  Retry Selected ({selectedJobIds.size})
                </button>
                <input
                  className="input ai-jobs-search-input"
                  value={aiSearch}
                  onChange={(e) => setAiSearch(e.target.value)}
                  placeholder="Search job, document, status, error"
                />
              </div>
              <div className="ai-jobs-table-wrap">
                <table className="table ai-jobs-table">
                  <thead>
                    <tr>
                      <th>
                        <input
                          type="checkbox"
                          aria-label="Select all retryable jobs"
                          checked={visibleRetryableAiRows.length > 0 && visibleRetryableAiRows.every((row) => selectedJobIds.has(row.job_id))}
                          disabled={!visibleRetryableAiRows.length}
                          onChange={(e) => setSelectedJobIds(e.target.checked ? new Set(visibleRetryableAiRows.map((row) => row.job_id)) : new Set())}
                        />
                      </th>
                      <th>Job</th>
                      <th>AI Document</th>
                      <th>Action</th>
                      <th>Status</th>
                      <th>Document</th>
                      <th>Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleAiRows.map((r) => (
                      <tr key={r.job_id}>
                        <td>
                          <input type="checkbox" disabled={!r.retry_allowed} checked={selectedJobIds.has(r.job_id)} onChange={(e) => toggleJob(r.job_id, e.target.checked)} />
                        </td>
                        <td>{r.job_id}</td>
                        <td>{r.ai_document_id}</td>
                        <td>{r.action}</td>
                        <td><span className={pipelineStatusClass(r.status)}>{pipelineJobLabel(r)}</span></td>
                        <td title={pathOf(r)}>{pathOf(r)}</td>
                        <td title={r.last_error || r.retry_block_reason || ''}>{r.last_error || r.retry_block_reason || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
