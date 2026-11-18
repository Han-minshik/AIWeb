import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

const EMAIL_RE =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;

const modalRoot =
  document.getElementById('modal-root') ||
  (() => {
    const el = document.createElement('div');
    el.id = 'modal-root';
    document.body.appendChild(el);
    return el;
  })();

function toIntOrNull(v) {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export default function UserFormModal({
  open,
  mode = 'create',
  initialData = null,
  departments = [],
  positions = [],
  submitting = false,
  onClose,
  onSubmit,
  onCreateDepartment,
  onCreatePosition,
  onDeletePosition,
}) {
  const isEdit = mode === 'edit';
  const title = isEdit ? 'Edit User' : 'Create User';
  const [form, setForm] = useState({
    username: '',
    password: '',
    full_name: '',
    email: '',
    department_id: '',
    position_id: '',
    is_admin: false,
    is_active: true,
  });
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!open) return;
    setErr('');
    setForm({
      username: initialData?.username || initialData?.name || '',
      password: '',
      full_name: initialData?.full_name || initialData?.username || initialData?.name || '',
      email: initialData?.email || '',
      department_id:
        initialData?.group_id != null && initialData?.group_id !== ''
          ? String(initialData.group_id)
          : '',
      position_id:
        initialData?.position_id != null && initialData?.position_id !== ''
          ? String(initialData.position_id)
          : '',
      is_admin: !!initialData?.is_admin,
      is_active:
        initialData?.is_active == null ? true : !!initialData?.is_active,
    });
  }, [open, initialData]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  const deptOptions = useMemo(() => (Array.isArray(departments) ? departments : []), [departments]);
  const positionOptions = useMemo(() => (Array.isArray(positions) ? positions : []), [positions]);

  if (!open) return null;

  const update = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const handleAddDepartment = async () => {
    const name = window.prompt('Enter new department name.');
    if (!name || !name.trim()) return;
    try {
      const created = await onCreateDepartment?.(name.trim());
      const gid = created?.group_id;
      if (gid != null) update('department_id', String(gid));
    } catch (e) {
      window.alert(`Department creation failed: ${e?.message || e}`);
    }
  };

  const handleAddPosition = async () => {
    const name = window.prompt('Enter new position name.');
    if (!name || !name.trim()) return;
    try {
      const created = await onCreatePosition?.(name.trim());
      const pid = created?.position_id;
      if (pid != null) update('position_id', String(pid));
    } catch (e) {
      window.alert(`Position creation failed: ${e?.message || e}`);
    }
  };

  const handleDeletePosition = async () => {
    const pid = toIntOrNull(form.position_id);
    if (pid == null) {
      window.alert('Select the position to delete first.');
      return;
    }
    const target = positionOptions.find((p) => Number(p.position_id) === pid);
    const label = target?.name || String(pid);
    if (!window.confirm(`Delete position '${label}'?`)) return;
    try {
      await onDeletePosition?.(pid);
      update('position_id', '');
    } catch (e) {
      window.alert(`Position deletion failed: ${e?.message || e}`);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErr('');

    const username = String(form.username || '').trim();
    const fullName = String(form.full_name || '').trim();
    const password = String(form.password || '').trim();
    const email = String(form.email || '').trim();

    if (!username) {
      setErr('username is required');
      return;
    }
    if (!fullName) {
      setErr('full_name is required');
      return;
    }
    if (!isEdit && !password) {
      setErr('password is required');
      return;
    }
    if (email && !EMAIL_RE.test(email)) {
      setErr('invalid email format');
      return;
    }

    const payload = {
      username,
      full_name: fullName,
      email,
      department_id: toIntOrNull(form.department_id),
      position_id: toIntOrNull(form.position_id),
      is_admin: !!form.is_admin,
      is_active: !!form.is_active,
    };

    if (isEdit) {
      if (password) payload.password = password;
    } else {
      payload.password = password;
    }

    await onSubmit?.(payload);
  };

  return createPortal(
    <div
      className="uform-overlay"
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div className="uform-card">
        <div className="uform-head">
          <div className="uform-title">{title}</div>
          <button className="btn ghost" type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <form className="uform-body" onSubmit={handleSubmit}>
          <div className="uform-grid">
            <label className="uform-field">
              <span>username</span>
              <input
                className="input"
                value={form.username}
                onChange={(e) => update('username', e.target.value)}
                disabled={submitting}
                required
              />
            </label>

            <label className="uform-field">
              <span>password{isEdit ? ' (leave blank to keep)' : ''}</span>
              <input
                className="input"
                type="password"
                value={form.password}
                onChange={(e) => update('password', e.target.value)}
                disabled={submitting}
                required={!isEdit}
              />
            </label>

            <label className="uform-field">
              <span>full_name</span>
              <input
                className="input"
                value={form.full_name}
                onChange={(e) => update('full_name', e.target.value)}
                disabled={submitting}
                required
              />
            </label>

            <label className="uform-field">
              <span>email</span>
              <input
                className="input"
                type="email"
                value={form.email}
                onChange={(e) => update('email', e.target.value)}
                disabled={submitting}
              />
            </label>

            <label className="uform-field">
              <span>department</span>
              <div className="uform-inline">
                <select
                  className="input"
                  value={form.department_id}
                  onChange={(e) => update('department_id', e.target.value)}
                  disabled={submitting}
                >
                  <option value="">None</option>
                  {deptOptions.map((d) => (
                    <option key={d.group_id} value={String(d.group_id)}>
                      {d.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={handleAddDepartment}
                  disabled={submitting}
                >
                  New
                </button>
              </div>
            </label>

            <label className="uform-field">
              <span>position</span>
              <div className="uform-inline">
                <select
                  className="input"
                  value={form.position_id}
                  onChange={(e) => update('position_id', e.target.value)}
                  disabled={submitting}
                >
                  <option value="">None</option>
                  {positionOptions.map((p) => (
                    <option key={p.position_id} value={String(p.position_id)}>
                      {p.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={handleAddPosition}
                  disabled={submitting}
                >
                  New
                </button>
                <button
                  type="button"
                  className="btn ghost danger"
                  onClick={handleDeletePosition}
                  disabled={submitting}
                >
                  Delete
                </button>
              </div>
            </label>

            <label className="uform-check">
              <input
                type="checkbox"
                checked={!!form.is_admin}
                onChange={(e) => update('is_admin', e.target.checked)}
                disabled={submitting}
              />
              <span>is_admin</span>
            </label>

            <label className="uform-check">
              <input
                type="checkbox"
                checked={!!form.is_active}
                onChange={(e) => update('is_active', e.target.checked)}
                disabled={submitting}
              />
              <span>is_active</span>
            </label>
          </div>

          {err ? <div className="uform-error">{err}</div> : null}

          <div className="uform-foot">
            <button type="button" className="btn ghost" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={submitting}>
              {submitting ? 'Saving...' : isEdit ? 'Save' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    modalRoot
  );
}
