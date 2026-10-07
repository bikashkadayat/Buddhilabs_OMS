import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminLeaveService } from '../../../services/adminLeaveService';
import Toast from '../../../components/admin/Toast';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';
import { X } from 'lucide-react';

const empty = {
  code: '', name: '', default_days_per_year: '0', is_paid: true, allow_half_day: true,
  allow_carry_forward: false, max_carry_forward_days: '', requires_document: false,
  min_notice_days: 0, is_active: true, display_color: '#6B7280',
};

const TypeForm = ({ initial, busy, onClose, onSubmit }) => {
  const [form, setForm] = useState(initial || empty);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const toggle = (k) => (e) => setForm({ ...form, [k]: e.target.checked });
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Leave type" onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head"><h3>{initial ? 'Edit' : 'New'} leave type</h3><button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button></div>
        <label className="lr-field"><span>Code</span><input type="text" value={form.code} disabled={!!initial} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} aria-label="Code" /></label>
        <label className="lr-field"><span>Name</span><input type="text" value={form.name} onChange={set('name')} aria-label="Name" /></label>
        <label className="lr-field"><span>Default days / year</span><input type="number" step="0.5" value={form.default_days_per_year} onChange={set('default_days_per_year')} aria-label="Default days per year" /></label>
        <label className="lr-field"><span>Max carry-forward (blank = none)</span><input type="number" step="0.5" value={form.max_carry_forward_days || ''} onChange={set('max_carry_forward_days')} aria-label="Max carry forward" /></label>
        <label className="lr-field"><span>Min notice days</span><input type="number" value={form.min_notice_days} onChange={set('min_notice_days')} aria-label="Min notice days" /></label>
        <label className="lr-field"><span>Colour</span><input type="color" value={form.display_color} onChange={set('display_color')} aria-label="Display colour" /></label>
        <div className="lr-checks">
          <label><input type="checkbox" checked={form.is_paid} onChange={toggle('is_paid')} /> Paid</label>
          <label><input type="checkbox" checked={form.allow_half_day} onChange={toggle('allow_half_day')} /> Half-day</label>
          <label><input type="checkbox" checked={form.allow_carry_forward} onChange={toggle('allow_carry_forward')} /> Carry forward</label>
          <label><input type="checkbox" checked={form.requires_document} onChange={toggle('requires_document')} /> Requires doc</label>
          <label><input type="checkbox" checked={form.is_active} onChange={toggle('is_active')} /> Active</label>
        </div>
        <div className="lr-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="lr-btn lr-btn-primary" disabled={!form.code || !form.name || busy} onClick={() => onSubmit({ ...form, max_carry_forward_days: form.max_carry_forward_days || null })}>Save</button>
        </div>
      </div>
    </div>
  );
};

const LeaveTypeManagement = () => {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(null);
  const [toast, setToast] = useState(null);

  const { data: types = [], isLoading, isError, error, refetch } = useQuery({ queryKey: ['admin-leave-types'], queryFn: adminLeaveService.getLeaveTypes });
  const invalidate = () => qc.invalidateQueries({ queryKey: ['admin-leave-types'] });

  const save = useMutation({
    mutationFn: (form) => (editing?.id ? adminLeaveService.updateLeaveType(editing.id, form) : adminLeaveService.createLeaveType(form)),
    onSuccess: () => { invalidate(); setToast({ message: 'Leave type saved.', tone: 'success' }); setEditing(null); },
    onError: () => setToast({ message: 'Save failed (duplicate code?).', tone: 'error' }),
  });

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Leave Types</h2><div className="lr-page-sub">CMS-driven leave types and their rules</div></div>
        <button type="button" className="lr-btn lr-btn-primary" onClick={() => setEditing({})}>New leave type</button>
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && types.length === 0 && <EmptyState message="No leave types." ctaLabel="Add one" ctaTo={undefined} />}

      {!isLoading && !isError && types.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead><tr><th scope="col">Code</th><th scope="col">Name</th><th scope="col">Days/yr</th><th scope="col">Paid</th><th scope="col">Carry</th><th scope="col">Active</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {types.map((t) => (
                <tr key={t.id}>
                  <td><span className="lr-type-swatch" style={{ '--chip-color': t.display_color, display: 'inline-block', marginRight: 6 }} />{t.code}</td>
                  <td>{t.name}</td><td>{t.default_days_per_year}</td>
                  <td>{t.is_paid ? 'Yes' : 'No'}</td>
                  <td>{t.allow_carry_forward ? `≤ ${t.max_carry_forward_days ?? '∞'}` : 'No'}</td>
                  <td>{t.is_active ? 'Yes' : 'No'}</td>
                  <td><button type="button" className="lr-btn lr-btn-ghost" onClick={() => setEditing(t)}>Edit</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && <TypeForm initial={editing.id ? editing : null} busy={save.isPending} onClose={() => setEditing(null)} onSubmit={(f) => save.mutate(f)} />}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default LeaveTypeManagement;
