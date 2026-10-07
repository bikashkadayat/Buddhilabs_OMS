import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminLeaveService } from '../../../services/adminLeaveService';
import ConfirmModal from '../../../components/admin/ConfirmModal';
import Toast from '../../../components/admin/Toast';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';
import { X } from 'lucide-react';

const emptyForm = { leave_type: '', department: '', role: '', days_per_year: '', effective_from: '', effective_until: '' };

const PolicyForm = ({ leaveTypes, departments, initial, busy, onClose, onSubmit }) => {
  const [form, setForm] = useState(initial || emptyForm);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const valid = form.leave_type && form.days_per_year && form.effective_from;
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Leave policy" onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head"><h3>{initial ? 'Edit' : 'New'} policy</h3><button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button></div>
        <label className="lr-field"><span>Leave type</span>
          <select value={form.leave_type} onChange={set('leave_type')} aria-label="Leave type">
            <option value="">Select…</option>
            {leaveTypes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <label className="lr-field"><span>Department (blank = org-wide)</span>
          <select value={form.department} onChange={set('department')} aria-label="Department">
            <option value="">Org-wide</option>
            {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <label className="lr-field"><span>Role (blank = all)</span>
          <select value={form.role} onChange={set('role')} aria-label="Role">
            <option value="">All roles</option>
            {['maker', 'checker', 'approver', 'admin'].map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="lr-field"><span>Days per year</span><input type="number" step="0.5" value={form.days_per_year} onChange={set('days_per_year')} aria-label="Days per year" /></label>
        <label className="lr-field"><span>Effective from</span><input type="date" value={form.effective_from} onChange={set('effective_from')} aria-label="Effective from" /></label>
        <label className="lr-field"><span>Effective until (optional)</span><input type="date" value={form.effective_until || ''} onChange={set('effective_until')} aria-label="Effective until" /></label>
        <div className="lr-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="lr-btn lr-btn-primary" disabled={!valid || busy} onClick={() => onSubmit(form)}>Save</button>
        </div>
      </div>
    </div>
  );
};

const PolicyManagement = () => {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(null);
  const [deprecating, setDeprecating] = useState(null);
  const [toast, setToast] = useState(null);

  const { data: policies = [], isLoading, isError, error, refetch } = useQuery({ queryKey: ['admin-policies'], queryFn: adminLeaveService.getPolicies });
  const { data: leaveTypes = [] } = useQuery({ queryKey: ['admin-leave-types'], queryFn: adminLeaveService.getLeaveTypes });
  const { data: departments = [] } = useQuery({ queryKey: ['admin-departments'], queryFn: adminLeaveService.getDepartments });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['admin-policies'] });
  const clean = (form) => ({ ...form, department: form.department || null, role: form.role || null, effective_until: form.effective_until || null });

  const save = useMutation({
    mutationFn: (form) => (editing?.id ? adminLeaveService.updatePolicy(editing.id, clean(form)) : adminLeaveService.createPolicy(clean(form))),
    onSuccess: (res) => {
      invalidate();
      setToast({ message: res.warning ? `Saved. ${res.warning}` : 'Policy saved.', tone: res.warning ? 'error' : 'success' });
      setEditing(null);
    },
    onError: () => setToast({ message: 'Save failed.', tone: 'error' }),
  });

  const deprecate = useMutation({
    mutationFn: (id) => adminLeaveService.deprecatePolicy(id),
    onSuccess: () => { invalidate(); setToast({ message: 'Policy deprecated (ended today).', tone: 'success' }); setDeprecating(null); },
  });

  const byDept = policies.reduce((acc, p) => {
    const key = p.department_code || 'Org-wide';
    (acc[key] = acc[key] || []).push(p);
    return acc;
  }, {});

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Leave Policies</h2><div className="lr-page-sub">Entitlement overrides by department, role and date range</div></div>
        <button type="button" className="lr-btn lr-btn-primary" onClick={() => setEditing({})}>New policy</button>
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && policies.length === 0 && <EmptyState message="No policies yet." ctaLabel="Create one" ctaTo={undefined} />}

      {!isLoading && !isError && Object.entries(byDept).map(([dept, list]) => (
        <section key={dept} style={{ marginBottom: 20 }}>
          <h3 className="lr-chart-title">{dept}</h3>
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead><tr><th scope="col">Type</th><th scope="col">Role</th><th scope="col">Days/yr</th><th scope="col">From</th><th scope="col">Until</th><th scope="col">Effective now</th><th scope="col">Actions</th></tr></thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.id}>
                    <td>{p.leave_type_code}</td><td>{p.role || 'All'}</td><td>{p.days_per_year}</td>
                    <td>{p.effective_from}</td><td>{p.effective_until || '—'}</td>
                    <td>{p.is_effective_now ? <span className="lr-att-good lr-attendance"><span className="lr-att-dot" />Active</span> : <span style={{ color: 'var(--text-muted)' }}>Inactive</span>}</td>
                    <td style={{ display: 'flex', gap: 8 }}>
                      <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setEditing({ ...p, leave_type: p.leave_type, department: p.department || '', role: p.role || '' })}>Edit</button>
                      <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setDeprecating(p)}>Deprecate</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}

      {editing && (
        <PolicyForm
          leaveTypes={leaveTypes} departments={departments}
          initial={editing.id ? editing : null} busy={save.isPending}
          onClose={() => setEditing(null)} onSubmit={(form) => save.mutate(form)}
        />
      )}

      {deprecating && (
        <ConfirmModal
          title="Deprecate policy" danger
          message={`End "${deprecating.leave_type_code}" for ${deprecating.department_code || 'Org-wide'} today? Policies are never deleted, only deprecated.`}
          confirmWord="DEPRECATE" confirmLabel="Deprecate" busy={deprecate.isPending}
          onClose={() => setDeprecating(null)} onConfirm={() => deprecate.mutate(deprecating.id)}
        />
      )}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default PolicyManagement;
