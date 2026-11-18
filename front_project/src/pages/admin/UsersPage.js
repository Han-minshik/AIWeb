import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  listUsers,
  createUser,
  updateUser,
  deleteUser,
  permanentlyDeleteUser,
  toggleAdmin,
  listDepartments,
  createDepartment,
  getGroupUsage,
  listPositions,
  createPosition,
  deletePosition,
} from '../../services/users/userService';
import AdminGuard from '../../components/admin/AdminGuard';
import UserAccessLogModal from '../../components/admin/UserAccessLogModal';
import UserFormModal from '../../components/admin/UserFormModal';

export default function UsersPage() {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 20;
  const FILTER_ALL = 'all';
  const FILTER_NONE = '__none__';

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const [selectedUser, setSelectedUser] = useState(null);
  const [departments, setDepartments] = useState([]);
  const [positions, setPositions] = useState([]);

  const [formModal, setFormModal] = useState({
    open: false,
    mode: 'create',
    target: null,
  });
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [filters, setFilters] = useState({
    department: FILTER_ALL,
    position: FILTER_ALL,
    admin: FILTER_ALL,
  });
  const [openFilter, setOpenFilter] = useState(null);
  const [groupUsageModal, setGroupUsageModal] = useState({
    open: false,
    group: null,
    loading: false,
    error: '',
    data: null,
  });

  const totalPages = useMemo(() => Math.max(1, Math.ceil((total || 0) / PAGE_SIZE)), [total]);

  const requestFilters = useMemo(() => {
    const next = {};
    if (filters.department === FILTER_NONE) {
      next.group_empty = true;
    } else if (filters.department !== FILTER_ALL) {
      next.group_id = Number(filters.department);
    }

    if (filters.position === FILTER_NONE) {
      next.position_empty = true;
    } else if (filters.position !== FILTER_ALL) {
      next.position_id = Number(filters.position);
    }

    if (filters.admin === 'true') next.is_admin = true;
    if (filters.admin === 'false') next.is_admin = false;
    return next;
  }, [filters]);

  const hasActiveFilter = useMemo(
    () =>
      filters.department !== FILTER_ALL ||
      filters.position !== FILTER_ALL ||
      filters.admin !== FILTER_ALL,
    [filters]
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listUsers({ q, page, page_size: PAGE_SIZE, ...requestFilters });
      setRows(res.items || []);
      setTotal(res.total || 0);
      setErr('');
    } catch (e) {
      setRows([]);
      setTotal(0);
      setErr(e?.message || 'Failed to load user list.');
    } finally {
      setLoading(false);
    }
  }, [q, page, requestFilters]);

  const loadLookupData = useCallback(async () => {
    try {
      const [deptRows, positionRows] = await Promise.all([
        listDepartments(),
        listPositions(),
      ]);
      setDepartments(Array.isArray(deptRows) ? deptRows : []);
      setPositions(Array.isArray(positionRows) ? positionRows : []);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('Failed to load departments/positions', e);
      setDepartments([]);
      setPositions([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadLookupData();
  }, [loadLookupData]);

  useEffect(() => {
    const close = () => setOpenFilter(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, []);

  const openCreateModal = () => {
    setFormModal({ open: true, mode: 'create', target: null });
  };

  const openEditModal = (user) => {
    setFormModal({ open: true, mode: 'edit', target: user });
  };

  const closeFormModal = () => {
    if (formSubmitting) return;
    setFormModal({ open: false, mode: 'create', target: null });
  };

  const handleCreateDepartment = async (name) => {
    const created = await createDepartment(name);
    await loadLookupData();
    return created;
  };

  const handleCreatePosition = async (name) => {
    const created = await createPosition(name);
    await loadLookupData();
    return created;
  };

  const handleDeletePosition = async (positionId) => {
    await deletePosition(positionId);
    await loadLookupData();
    if (filters.position === String(positionId)) {
      setFilters((prev) => ({ ...prev, position: FILTER_ALL }));
      setPage(1);
    }
  };

  const handleSubmitForm = async (payload) => {
    setFormSubmitting(true);
    try {
      if (formModal.mode === 'create') {
        await createUser(payload);
      } else if (formModal.target?.id) {
        await updateUser(formModal.target.id, payload);
      }
      setFormModal({ open: false, mode: 'create', target: null });
      await load();
    } catch (e) {
      const prefix = formModal.mode === 'create' ? 'Create' : 'Update';
      window.alert(`${prefix} failed: ${e?.message || e}`);
    } finally {
      setFormSubmitting(false);
    }
  };

  const onToggleAdmin = async (u) => {
    try {
      await toggleAdmin(u.id, !u.is_admin);
      await load();
    } catch (e) {
      window.alert(`Permission change failed: ${e?.message || e}`);
    }
  };

  const onDelete = async (u) => {
    const label = u.username || u.name || u.email;
    const permanent = !u.is_active;
    const prompt = permanent
      ? `Permanently delete this user and personal KB data? This cannot be undone. (${label})`
      : `Deactivate this user? (${label})`;
    if (!window.confirm(prompt)) return;
    try {
      const result = permanent
        ? await permanentlyDeleteUser(u.id)
        : await deleteUser(u.id);
      if (permanent && result?.deleted === false) {
        window.alert(
          `Permanent deletion is waiting for document cleanup. Remaining documents: ${result.remaining_documents || 0}`
        );
      }
      if (rows.length === 1 && page > 1) setPage((p) => Math.max(1, p - 1));
      else await load();
    } catch (e) {
      window.alert(`Delete failed: ${e?.message || e}`);
    }
  };

  const renderRow = (u) => {
    const name = u.username || u.name || '(No name)';
    return (
      <tr key={u.id}>
        <td style={{ padding: 8, textAlign: 'left' }}>{u.id}</td>
        <td
          className="access-log"
          style={{ padding: 8, textAlign: 'left', cursor: 'pointer' }}
          title="View access log"
          onClick={() => setSelectedUser(u)}
        >
          {name}
        </td>
        <td style={{ padding: 8, textAlign: 'left' }}>{u.email}</td>
        <td style={{ padding: 8, textAlign: 'left' }}>{u.department || ''}</td>
        <td style={{ padding: 8, textAlign: 'left' }}>{u.position || ''}</td>
        <td style={{ padding: 8, textAlign: 'left' }}>
          <input
            type="checkbox"
            checked={!!u.is_admin}
            onChange={() => onToggleAdmin(u)}
            aria-label="Admin Permission"
            disabled={!u.is_active}
          />
        </td>
        <td style={{ padding: 8, textAlign: 'left' }}>{u.is_active ? 'Active' : 'Inactive'}</td>
        <td className="users-actions-cell">
          <div className="users-actions">
            <button className="btn ghost" onClick={() => openEditModal(u)}>
              Edit
            </button>
            <button className="btn ghost danger" onClick={() => onDelete(u)}>
              {u.is_active ? 'Deactivate' : 'Delete permanently'}
            </button>
          </div>
        </td>
      </tr>
    );
  };

  const applyFilter = (key, value) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
    setOpenFilter(null);
  };

  const clearAllFilters = () => {
    setFilters({
      department: FILTER_ALL,
      position: FILTER_ALL,
      admin: FILTER_ALL,
    });
    setOpenFilter(null);
    setPage(1);
  };

  const openGroupUsage = async (option) => {
    const groupId = Number(option.value);
    if (!Number.isFinite(groupId)) return;
    setOpenFilter(null);
    setGroupUsageModal({
      open: true,
      group: { group_id: groupId, name: option.label },
      loading: true,
      error: '',
      data: null,
    });
    try {
      const data = await getGroupUsage(groupId);
      setGroupUsageModal({
        open: true,
        group: data?.group || { group_id: groupId, name: option.label },
        loading: false,
        error: '',
        data,
      });
    } catch (e) {
      setGroupUsageModal((prev) => ({
        ...prev,
        loading: false,
        error: e?.message || 'Failed to load group usage.',
      }));
    }
  };

  const closeGroupUsage = () => {
    setGroupUsageModal({
      open: false,
      group: null,
      loading: false,
      error: '',
      data: null,
    });
  };

  const departmentOptions = useMemo(
    () => [
      { value: FILTER_ALL, label: 'All departments' },
      { value: FILTER_NONE, label: '(No department)' },
      ...departments.map((d) => ({
        value: String(d.group_id),
        label: d.name || String(d.group_id),
      })),
    ],
    [departments]
  );

  const positionOptions = useMemo(
    () => [
      { value: FILTER_ALL, label: 'All positions' },
      { value: FILTER_NONE, label: '(No position)' },
      ...positions.map((p) => ({
        value: String(p.position_id),
        label: p.name || String(p.position_id),
      })),
    ],
    [positions]
  );

  const adminOptions = useMemo(
    () => [
      { value: FILTER_ALL, label: 'All users' },
      { value: 'true', label: 'Admin only' },
      { value: 'false', label: 'Non-admin only' },
    ],
    []
  );

  const filterLabel = (key, options) => {
    const value = filters[key];
    return options.find((option) => option.value === value)?.label || 'All';
  };

  const docPath = (row) =>
    [row?.relative_path, row?.sub_path, row?.doc_name || row?.node_name]
      .filter(Boolean)
      .join('/');

  const renderFilterHeader = (key, label, options, style) => {
    const active = filters[key] !== FILTER_ALL;
    const isOpen = openFilter === key;
    return (
      <th
        className={`users-filter-th${active ? ' is-filtered' : ''}`}
        style={style}
        onClick={(e) => {
          e.stopPropagation();
          setOpenFilter((prev) => (prev === key ? null : key));
        }}
      >
        <button type="button" className="users-filter-trigger">
          <span>{label}</span>
          <span className="users-filter-caret">{isOpen ? '^' : 'v'}</span>
        </button>
        {active ? <div className="users-filter-current">{filterLabel(key, options)}</div> : null}
        {isOpen ? (
          <div
            className="users-filter-menu"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="users-filter-menu-title">{label} filter</div>
            <div className="users-filter-options">
              {options.map((option) => (
                <div key={option.value} className="users-filter-option-row">
                  <button
                    type="button"
                    className={`users-filter-option${filters[key] === option.value ? ' selected' : ''}`}
                    onClick={() => applyFilter(key, option.value)}
                  >
                    <span className="users-filter-check">
                      {filters[key] === option.value ? 'x' : ''}
                    </span>
                    <span>{option.label}</span>
                  </button>
                  {key === 'department' && option.value !== FILTER_ALL && option.value !== FILTER_NONE ? (
                    <button
                      type="button"
                      className="users-filter-view"
                      onClick={() => openGroupUsage(option)}
                      title="View group members and ACL documents"
                    >
                      View
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </th>
    );
  };

  return (
    <AdminGuard>
      <div className="panel" style={{ height: '100%' }}>
        <div className="panel-head">
          <div className="panel-title">Users</div>
          <div className="panel-actions" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              className="input"
              placeholder="Search by name, email, or display name"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') load();
              }}
              style={{ minWidth: 260 }}
            />
            <button className="btn search" onClick={load} disabled={loading}>
              Search
            </button>
            <button
              className="btn ghost"
              onClick={() => {
                setQ('');
                clearAllFilters();
                setPage(1);
              }}
            >
              Reset
            </button>
            {hasActiveFilter && (
              <button className="btn ghost" onClick={clearAllFilters}>
                Clear Filters
              </button>
            )}

            <span className="muted" style={{ margin: '0 8px' }}>
              |
            </span>

            <button className="btn add" onClick={openCreateModal} disabled={formSubmitting}>
              Add User
            </button>

            {loading && <div className="loading">Loading...</div>}
            {err && <div className="muted" style={{ color: 'var(--danger, #c00)' }}>{err}</div>}
          </div>
        </div>

        <div className="panel-body" style={{ display: 'grid', gridTemplateRows: '1fr auto', gap: 10, height: 'calc(100% - 40px)' }}>
          <div className="card scroll" style={{ overflow: 'auto' }}>
            <table className="table users-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: 8, width: 80 }}>ID</th>
                  <th style={{ textAlign: 'left', padding: 8, width: 200 }}>Name</th>
                  <th style={{ textAlign: 'left', padding: 8, width: 240 }}>E-mail</th>
                  {renderFilterHeader('department', 'Department', departmentOptions, { textAlign: 'left', padding: 8, width: 160 })}
                  {renderFilterHeader('position', 'Position', positionOptions, { textAlign: 'left', padding: 8, width: 120 })}
                  {renderFilterHeader('admin', 'Admin', adminOptions, { textAlign: 'left', padding: 8, width: 100 })}
                  <th style={{ textAlign: 'left', padding: 8, width: 100 }}>Status</th>
                  <th className="users-actions-head">Actions</th>
                </tr>
              </thead>
              <tbody>
                {!rows.length ? (
                  <tr>
                    <td colSpan={8} style={{ padding: 12, textAlign: 'left' }} className="empty">
                      No result.
                    </td>
                  </tr>
                ) : rows.map(renderRow)}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ padding: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn ghost"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </button>
            <span className="muted">Page</span>
            <strong>{page}</strong>
            <span className="muted">/ {totalPages}</span>
            <button
              className="btn ghost"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
            <div className="toolbar-spacer" />
            <span className="muted" style={{ fontSize: 12 }}>
              Per page {PAGE_SIZE} / Total {total.toLocaleString()}
            </span>
          </div>
        </div>
      </div>

      {selectedUser && (
        <UserAccessLogModal
          open={!!selectedUser}
          user={selectedUser}
          onClose={() => setSelectedUser(null)}
        />
      )}

      <UserFormModal
        open={formModal.open}
        mode={formModal.mode}
        initialData={formModal.target}
        departments={departments}
        positions={positions}
        submitting={formSubmitting}
        onClose={closeFormModal}
        onSubmit={handleSubmitForm}
        onCreateDepartment={handleCreateDepartment}
        onCreatePosition={handleCreatePosition}
        onDeletePosition={handleDeletePosition}
      />

      {groupUsageModal.open && (
        <div
          className="uform-overlay"
          role="dialog"
          aria-modal="true"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeGroupUsage();
          }}
        >
          <div className="uform-card group-usage-card">
            <div className="uform-head">
              <div>
                <div className="uform-title">
                  Department Usage: {groupUsageModal.group?.name || `#${groupUsageModal.group?.group_id || ''}`}
                </div>
                <div className="group-usage-summary">
                  Members and documents using this group ACL
                </div>
              </div>
              <button className="btn ghost" type="button" onClick={closeGroupUsage}>
                Close
              </button>
            </div>

            <div className="uform-body group-usage-body">
              {groupUsageModal.loading ? <div className="muted">Loading...</div> : null}
              {groupUsageModal.error ? <div className="uform-error">{groupUsageModal.error}</div> : null}
              {!groupUsageModal.loading && !groupUsageModal.error ? (
                <>
                  <div className="group-usage-stats">
                    <div className="group-usage-stat">
                      <span>Active members</span>
                      <strong>{groupUsageModal.data?.active_member_count || 0}</strong>
                    </div>
                    <div className="group-usage-stat">
                      <span>Total members</span>
                      <strong>{groupUsageModal.data?.member_count || 0}</strong>
                    </div>
                    <div className="group-usage-stat">
                      <span>ACL documents</span>
                      <strong>{groupUsageModal.data?.acl_document_count || 0}</strong>
                    </div>
                    <div className={`group-usage-stat ${groupUsageModal.data?.can_disable ? 'ok' : 'blocked'}`}>
                      <span>Disable</span>
                      <strong>{groupUsageModal.data?.can_disable ? 'Allowed' : 'Blocked'}</strong>
                    </div>
                  </div>

                  <section className="group-usage-section">
                    <h3>Members</h3>
                    <div className="group-usage-table-wrap">
                      <table className="table group-usage-table">
                        <thead>
                          <tr>
                            <th>User</th>
                            <th>Full name</th>
                            <th>Email</th>
                            <th>Role</th>
                            <th>State</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(groupUsageModal.data?.members || []).length ? (
                            groupUsageModal.data.members.map((m) => (
                              <tr key={m.group_member_id || `${m.group_id}-${m.user_id}`}>
                                <td>{m.username || m.user_id}</td>
                                <td>{m.full_name || '-'}</td>
                                <td>{m.email || '-'}</td>
                                <td>{m.role || '-'}</td>
                                <td>{m.is_active ? 'active' : 'inactive'}</td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={5}>No members.</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </section>

                  <section className="group-usage-section">
                    <h3>ACL Documents</h3>
                    <div className="group-usage-table-wrap">
                      <table className="table group-usage-table">
                        <thead>
                          <tr>
                            <th>Node</th>
                            <th>Policy</th>
                            <th>Document path</th>
                            <th>Status</th>
                            <th>Index</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(groupUsageModal.data?.acl_documents || []).length ? (
                            groupUsageModal.data.acl_documents.map((doc) => (
                              <tr key={`${doc.acl_id}-${doc.node_id}`}>
                                <td>{doc.node_id}</td>
                                <td>{doc.policy_code || '-'}</td>
                                <td title={docPath(doc)}>{docPath(doc) || '-'}</td>
                                <td>{doc.doc_status || '-'}</td>
                                <td>{doc.index_status || '-'}</td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={5}>No ACL documents.</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </section>
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </AdminGuard>
  );
}

