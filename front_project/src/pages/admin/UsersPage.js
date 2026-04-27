import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  listUsers, createUser, updateUser, deleteUser, toggleAdmin, resetPassword,
} from '../../services/users/userService';
import AdminGuard from '../../components/admin/AdminGuard';
import UserAccessLogModal from '../../components/admin/UserAccessLogModal';

export default function UsersPage() {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 20;

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const [creating, setCreating] = useState(false);

  const [selectedUser, setSelectedUser] = useState(null);

  const totalPages = useMemo(() => Math.max(1, Math.ceil((total || 0) / PAGE_SIZE)), [total]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listUsers({ q, page, page_size: PAGE_SIZE });
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
  }, [q, page]);

  useEffect(() => { load(); }, [load]);

  const onCreate = async () => {
    setCreating(true);
    try {
      const username = window.prompt('Please enter the username (display name).');
      if (!username) return;
      const email = window.prompt('Please enter the email.') || '';
      const password = window.prompt('Enter the initial password (required).');
      if (!password) return;
      const is_admin = !!window.confirm('Grant administrator privileges? Confirm=Yes / Cancel=No');

      await createUser({ username, email, password, is_admin });
      await load();
    } catch (e) {
      window.alert(`Create Failed: ${e?.message || e}`);
    } finally {
      setCreating(false);
    }
  };

  const onEdit = async (u) => {
    const username = window.prompt('Name (Cancel to keep current)', u.username || u.name || '');
    if (username == null) return;
    const email = window.prompt('Email (Cancel to keep current)', u.email || '');
    if (email == null) return;
    const full_name = window.prompt('Display name (Cancel to keep current)', u.full_name || u.username || '');
    if (full_name == null) return;

    const patch = {};
    if (username !== (u.username || u.name)) patch.username = username;
    if (email !== u.email) patch.email = email;
    if (full_name !== (u.full_name || u.username || '')) patch.full_name = full_name;

    if (Object.keys(patch).length === 0) return;
    try {
      await updateUser(u.id, patch);
      await load();
    } catch (e) {
      window.alert(`Failed: ${e?.message || e}`);
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
    if (!window.confirm(`Are you sure you want to delete it? (${u.username || u.name || u.email})`)) return;
    try {
      await deleteUser(u.id);
      if (rows.length === 1 && page > 1) setPage(p => Math.max(1, p - 1));
      else await load();
    } catch (e) {
      window.alert(`Delete Failed: ${e?.message || e}`);
    }
  };

  const onResetPw = async (u) => {
    const pw = window.prompt('Enter the new password.');
    if (!pw) return;
    try {
      await resetPassword(u.id, pw);
      window.alert('Success.');
    } catch (e) {
      window.alert(`Failed: ${e?.message || e}`);
    }
  };

  const renderRow = (u) => {
    const name = u.username || u.name || '(No name)';
    return (
      <tr key={u.id}>
        <td style={{ padding: 8, textAlign: 'left' }}>{u.id}</td>
        <td
        className='access-log'
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
          />
        </td>
        <td style={{ padding: 8, textAlign: 'left' }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'center' }}>
            <button className="btn ghost" onClick={() => onEdit(u)}>Edit</button>
            <button className="btn ghost" onClick={() => onResetPw(u)}>Change PW</button>
            <button className="btn ghost danger" onClick={() => onDelete(u)}>Delete</button>
          </div>
        </td>
      </tr>
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
              onChange={(e) => { setQ(e.target.value); setPage(1); }}
              onKeyDown={(e) => { if (e.key === 'Enter') load(); }}
              style={{ minWidth: 260 }}
            />
            <button className="btn search" onClick={load} disabled={loading}>Search</button>
            <button className="btn ghost" onClick={() => { setQ(''); setPage(1); }}>Reset</button>

            <span className="muted" style={{ margin: '0 8px' }}>|</span>

            <button className="btn add" onClick={onCreate} disabled={creating}>Add User</button>

            {loading && <div className="loading">Loading...</div>}
            {err && <div className="muted" style={{ color: 'var(--danger, #c00)' }}>{err}</div>}
          </div>
        </div>

        <div className="panel-body" style={{ display: 'grid', gridTemplateRows: '1fr auto', gap: 10, height: 'calc(100% - 40px)' }}>
          <div className="card scroll" style={{ overflow: 'auto' }}>
            <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: 8, width: 80 }}>ID</th>
                  <th style={{ textAlign: 'left', padding: 8, width: 200 }}>Name</th>
                  <th style={{ textAlign: 'left', padding: 8, width: 240 }}>E-mail</th>
                  <th style={{ textAlign: 'left', padding: 8, width: 160 }}>Department</th>
                  <th style={{ textAlign: 'left', padding: 8, width: 120 }}>Rank</th>
                  <th style={{ textAlign: 'left', padding: 8, width: 100 }}>Admin</th>
                </tr>
              </thead>
              <tbody>
                {!rows.length ? (
                  <tr><td colSpan={7} style={{ padding: 12, textAlign: 'left' }} className="empty">No result.</td></tr>
                ) : rows.map(renderRow)}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ padding: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn ghost"
              disabled={page <= 1}
              onClick={() => setPage(p => Math.max(1, p - 1))}
            >
              Previous
            </button>
            <span className="muted">Page</span>
            <strong>{page}</strong>
            <span className="muted">/ {totalPages}</span>
            <button
              className="btn ghost"
              disabled={page >= totalPages}
              onClick={() => setPage(p => p + 1)}
            >
              Next
            </button>
            <div className="toolbar-spacer" />
            <span className="muted" style={{ fontSize: 12 }}>Mark {PAGE_SIZE} · Total {total.toLocaleString()}</span>
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
    </AdminGuard>
  );
}
