import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { userMgmtService } from '../../../services/userMgmtService';
import ConfirmModal from '../../../components/admin/ConfirmModal';
import Modal from '../../../components/common/Modal';
import Toast from '../../../components/admin/Toast';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';
import { ROLES, ROLE_LABELS, roleLabel, EMPLOYEE_TYPES, employeeTypeLabel,
  EMPLOYMENT_TYPES, GENDERS } from '../../../services/roles';

const emptyCreate = {
  full_name: '', email: '', role: 'maker', employee_type: 'employee',
  employment_type: 'permanent', gender: 'undisclosed', department_ref: '',
  designation: '', phone: '', date_of_joining: '', is_active: 'true', password: '',
  biometric_id: '',
};

// Client-side preview of the leave category (mirrors leaves.category_engine) so
// the form shows the resolved tier before saving. The backend is authoritative.
const monthsOfService = (isoDate) => {
  if (!isoDate) return null;
  const d = new Date(isoDate), t = new Date();
  let m = (t.getFullYear() - d.getFullYear()) * 12 + (t.getMonth() - d.getMonth());
  if (t.getDate() < d.getDate()) m -= 1;
  return Math.max(0, m);
};
const previewCategory = (etype, doj) => {
  const m = monthsOfService(doj);
  if (etype === 'intern' || etype === 'volunteer') return 'Category D — Intern / Volunteer';
  if (m === null) return 'Probation floor — no joining date (HR to confirm)';
  if (etype === 'permanent') return m > 36 ? 'Category A — Permanent (>3 yrs)' : m > 12 ? 'Category B — Permanent (1–3 yrs)' : 'Category C — Permanent (<1 yr)';
  if (etype === 'post_probation') return m > 12 ? 'Category B — auto-treated as Permanent (flag)' : m >= 3 ? 'Category C — Post-Probation' : 'Probation (<3 mo) — flag';
  if (etype === 'probation') return m >= 3 ? 'Category C — probation completed (flag)' : 'Probation (<3 mo)';
  return 'Probation floor — HR review';
};

const CreateUserModal = ({ busy, onClose, onSubmit }) => {
  const [form, setForm] = useState(emptyCreate);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const { data: departments = [] } = useQuery({
    queryKey: ['admin-departments'],
    queryFn: () => userMgmtService.departments(),
  });
  const valid = form.full_name.trim() && /.+@.+\..+/.test(form.email) && form.department_ref;
  const submit = () => {
    const [first, ...rest] = form.full_name.trim().split(' ');
    onSubmit({
      email: form.email, first_name: first, last_name: rest.join(' '),
      role: form.role, employee_type: form.employee_type,
      employment_type: form.employment_type, gender: form.gender,
      department_ref: form.department_ref || null, designation: form.designation || null,
      phone: form.phone || null, date_of_joining: form.date_of_joining || null,
      biometric_id: form.biometric_id.trim() || null,
      is_active: form.is_active === 'true',
      ...(form.password ? { password: form.password } : {}),
    });
  };
  return (
    <Modal
      title="Create employee"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="lr-btn lr-btn-primary" disabled={!valid || busy} onClick={submit}>Create</button>
        </>
      )}
    >
        <label className="lr-field"><span>Employee ID</span><input value="Auto-generated on save" disabled readOnly aria-label="Employee ID" /></label>
        <label className="lr-field"><span>Full name *</span><input value={form.full_name} onChange={set('full_name')} aria-label="Full name" /></label>
        <label className="lr-field"><span>Email *</span><input type="email" value={form.email} onChange={set('email')} aria-label="Email" /></label>
        <label className="lr-field"><span>Phone</span><input value={form.phone} onChange={set('phone')} aria-label="Phone" /></label>
        <label className="lr-field"><span>Department *</span>
          <select value={form.department_ref} onChange={set('department_ref')} aria-label="Department">
            <option value="">Select department…</option>
            {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <label className="lr-field"><span>Designation</span><input value={form.designation} onChange={set('designation')} aria-label="Designation" /></label>
        <label className="lr-field"><span>Employee Type *</span>
          <select value={form.employee_type} onChange={set('employee_type')} aria-label="Employee type">
            {EMPLOYEE_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <label className="lr-field"><span>Role *</span>
          <select value={form.role} onChange={set('role')} aria-label="Role">{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}</select>
        </label>
        <label className="lr-field"><span>Employment Type *</span>
          <select value={form.employment_type} onChange={set('employment_type')} aria-label="Employment type">
            {EMPLOYMENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <label className="lr-field"><span>Date of joining</span><input type="date" value={form.date_of_joining} onChange={set('date_of_joining')} aria-label="Date of joining" /></label>
        <label className="lr-field"><span>Gender</span>
          <select value={form.gender} onChange={set('gender')} aria-label="Gender">
            {GENDERS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
          </select>
        </label>
        <div className="lr-field"><span>Leave Category</span>
          <div style={{ padding: '8px 10px', background: 'var(--bg-main, #f5f6f8)', borderRadius: 6, fontSize: 'var(--fs-sm)', fontWeight: 600 }}
               aria-label="Computed leave category">
            {previewCategory(form.employment_type, form.date_of_joining)}
          </div>
          <small style={{ color: 'var(--text-muted)' }}>Auto-computed from employment type + service. Balances assign on save.</small>
        </div>
        <label className="lr-field"><span>Status</span>
          <select value={form.is_active} onChange={set('is_active')} aria-label="Status">
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </label>
        <label className="lr-field"><span>Biometric ID</span><input value={form.biometric_id} onChange={set('biometric_id')} aria-label="Biometric ID" placeholder="Device employee ID (optional)" /></label>
        <label className="lr-field"><span>Password (blank = auto-generate)</span><input value={form.password} onChange={set('password')} aria-label="Password" /></label>
    </Modal>
  );
};

const CredentialsModal = ({ email, password, onClose }) => (
  <Modal
    title="Account created"
    ariaLabel="Account credentials"
    onClose={onClose}
    footer={<button type="button" className="lr-btn lr-btn-primary" onClick={onClose}>Done</button>}
  >
    <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)' }}>Share these credentials with the employee. They must change the password on first login.</p>
    <dl className="lr-modal-grid">
      <div><dt>Email</dt><dd>{email}</dd></div>
      <div><dt>Temporary password</dt><dd><code>{password}</code></dd></div>
    </dl>
  </Modal>
);

const UserManagement = () => {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [creating, setCreating] = useState(false);
  const [credentials, setCredentials] = useState(null);
  const [confirm, setConfirm] = useState(null); // {type,user}
  const [toast, setToast] = useState(null);

  const params = {};
  if (search) params.search = search;
  if (roleFilter) params.role = roleFilter;

  const { data: users = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['admin-users-mgmt', search, roleFilter],
    queryFn: () => userMgmtService.list(params),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ['admin-users-mgmt'] });

  const create = useMutation({
    mutationFn: (payload) => userMgmtService.create(payload),
    onSuccess: (u) => { invalidate(); setCreating(false); setCredentials({ email: u.email, password: u.generated_password }); },
    onError: (e) => setToast({ message: e?.response?.data?.email?.[0] || 'Create failed.', tone: 'error' }),
  });

  const doAction = useMutation({
    mutationFn: async ({ type, user, role }) => {
      if (type === 'reset') return userMgmtService.resetPassword(user.id);
      if (type === 'deactivate') return userMgmtService.deactivate(user.id);
      if (type === 'activate') return userMgmtService.activate(user.id);
      if (type === 'role') return userMgmtService.changeRole(user.id, role);
      if (type === 'delete') return userMgmtService.remove(user.id);
      return null;
    },
    onSuccess: (res, vars) => {
      invalidate();
      setConfirm(null);
      if (vars.type === 'reset') setCredentials({ email: vars.user.email, password: res.generated_password });
      else if (vars.type === 'delete') setToast({ message: `${vars.user.full_name || vars.user.email} deleted.`, tone: 'success' });
      else setToast({ message: 'Done.', tone: 'success' });
    },
    // 409 guard messages (active user / last admin / sole approver) surface here verbatim.
    onError: (e) => setToast({ message: e?.response?.data?.detail || 'That didn’t go through. Please try again.', tone: 'error' }),
  });

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>User Management</h2><div className="lr-page-sub">Create and manage employee accounts</div></div>
        <button type="button" className="lr-btn lr-btn-primary" onClick={() => setCreating(true)}>Create employee</button>
      </div>

      <div className="lr-filter-bar">
        <input type="search" placeholder="Search name / email / employee id" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search users" />
        <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} aria-label="Filter by role">
          <option value="">All roles</option>{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
        </select>
      </div>

      {isLoading && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && users.length === 0 && <EmptyState message="No users match." ctaLabel="Create employee" ctaTo={undefined} />}

      {!isLoading && !isError && users.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead><tr><th scope="col">Employee</th><th scope="col">Department</th><th scope="col">Designation</th><th scope="col">Type</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{u.full_name}</div>
                    <div style={{ fontSize: 'var(--fs-label)', color: 'var(--text-muted)' }}>{u.employee_id || '—'}</div>
                    <div style={{ fontSize: 'var(--fs-label)', color: 'var(--text-muted)' }}>{u.email}</div>
                  </td>
                  <td>{u.department_name || '—'}</td>
                  <td>{u.designation || '—'}</td>
                  <td>{employeeTypeLabel(u.employee_type)}</td>
                  <td>
                    <select value={u.role} aria-label={`Role for ${u.email}`} onChange={(e) => setConfirm({ type: 'role', user: u, role: e.target.value })}
                      style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '4px 8px', fontSize: 'var(--fs-meta)' }}>
                      {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                    </select>
                  </td>
                  <td><span className={`ar-tl-status ${u.is_active ? 'ar-tl-approved' : ''}`}>{u.is_active ? 'Active' : 'Inactive'}</span></td>
                  <td style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setConfirm({ type: 'reset', user: u })}>Reset PW</button>
                    {u.is_active
                      ? <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setConfirm({ type: 'deactivate', user: u })}>Deactivate</button>
                      : <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setConfirm({ type: 'activate', user: u })}>Activate</button>}
                    <button
                      type="button"
                      className="lr-btn lr-btn-danger"
                      disabled={u.is_active}
                      title={u.is_active ? 'Deactivate first' : 'Permanently delete this user'}
                      onClick={() => setConfirm({ type: 'delete', user: u })}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {creating && <CreateUserModal busy={create.isPending} onClose={() => setCreating(false)} onSubmit={(p) => create.mutate(p)} />}
      {credentials && <CredentialsModal {...credentials} onClose={() => setCredentials(null)} />}

      {confirm && (
        <ConfirmModal
          title={{
            reset: 'Reset password', deactivate: 'Deactivate user', activate: 'Activate user', role: 'Change role', delete: 'Delete user',
          }[confirm.type]}
          message={{
            reset: `Generate a new temporary password for ${confirm.user.email}? They must change it on next login.`,
            deactivate: `Deactivate ${confirm.user.email}? They will be blocked at login.`,
            activate: `Reactivate ${confirm.user.email}?`,
            role: `Change ${confirm.user.email}'s role to "${roleLabel(confirm.role)}"?`,
            delete: `Permanently delete ${confirm.user.full_name || confirm.user.email}? This cannot be undone.`,
          }[confirm.type]}
          danger={confirm.type === 'deactivate' || confirm.type === 'delete'}
          confirmLabel="Confirm"
          busy={doAction.isPending}
          onClose={() => setConfirm(null)}
          onConfirm={() => doAction.mutate(confirm)}
        />
      )}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default UserManagement;
