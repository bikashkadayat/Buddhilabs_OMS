import React, { useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminLeaveService } from '../../../services/adminLeaveService';
import YearSelector from '../../../components/leave-records/YearSelector';
import ConfirmModal from '../../../components/admin/ConfirmModal';
import Toast from '../../../components/admin/Toast';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';
import { X } from 'lucide-react';

const empty = { date: '', name: '', holiday_type: 'public', description: '' };

const HolidayForm = ({ initial, busy, onClose, onSubmit }) => {
  const [form, setForm] = useState(initial || empty);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Holiday" onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head"><h3>{initial ? 'Edit' : 'New'} holiday</h3><button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button></div>
        <label className="lr-field"><span>Date</span><input type="date" value={form.date} onChange={set('date')} aria-label="Date" /></label>
        <label className="lr-field"><span>Name</span><input type="text" value={form.name} onChange={set('name')} aria-label="Name" /></label>
        <label className="lr-field"><span>Type</span>
          <select value={form.holiday_type} onChange={set('holiday_type')} aria-label="Type">
            <option value="public">Public</option><option value="optional">Optional</option><option value="religious">Religious</option>
          </select>
        </label>
        <label className="lr-field"><span>Description</span><input type="text" value={form.description} onChange={set('description')} aria-label="Description" /></label>
        <div className="lr-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="lr-btn lr-btn-primary" disabled={!form.date || !form.name || busy} onClick={() => onSubmit(form)}>Save</button>
        </div>
      </div>
    </div>
  );
};

const HolidayManagement = () => {
  const qc = useQueryClient();
  const fileRef = useRef(null);
  const [year, setYear] = useState(new Date().getFullYear());
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [toast, setToast] = useState(null);

  const { data: holidays = [], isLoading, isError, error, refetch } = useQuery({ queryKey: ['admin-holidays', year], queryFn: () => adminLeaveService.getHolidays(year) });
  const invalidate = () => qc.invalidateQueries({ queryKey: ['admin-holidays'] });

  const save = useMutation({
    mutationFn: (form) => (editing?.id ? adminLeaveService.updateHoliday(editing.id, form) : adminLeaveService.createHoliday(form)),
    onSuccess: () => { invalidate(); setToast({ message: 'Holiday saved.', tone: 'success' }); setEditing(null); },
    onError: () => setToast({ message: 'Save failed (duplicate date?).', tone: 'error' }),
  });
  const del = useMutation({ mutationFn: (id) => adminLeaveService.deleteHoliday(id), onSuccess: () => { invalidate(); setToast({ message: 'Holiday deleted.', tone: 'success' }); setDeleting(null); } });
  const importCsv = useMutation({
    mutationFn: (file) => adminLeaveService.bulkImportHolidays(file),
    onSuccess: (res) => {
      invalidate();
      const errs = res.errors?.length ? ` ${res.errors.length} row(s) failed.` : '';
      setToast({ message: `Imported: ${res.created} created, ${res.updated} updated.${errs}`, tone: res.errors?.length ? 'error' : 'success' });
    },
    onError: () => setToast({ message: 'Import failed.', tone: 'error' }),
  });

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Holidays</h2><div className="lr-page-sub">Public holidays excluded from working-day calculations</div></div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <YearSelector currentYear={year} onChange={setYear} minYear={2023} />
          <a className="lr-btn lr-btn-ghost" href="/templates/holidays_template.csv" download>CSV template</a>
          <button type="button" className="lr-btn lr-btn-ghost" onClick={() => fileRef.current?.click()}>Bulk import</button>
          <input ref={fileRef} type="file" accept=".csv" hidden aria-hidden="true"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importCsv.mutate(f); e.target.value = ''; }} />
          <button type="button" className="lr-btn lr-btn-primary" onClick={() => setEditing({})}>New holiday</button>
        </div>
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && holidays.length === 0 && <EmptyState message={`No holidays for ${year}.`} ctaLabel="Add one" ctaTo={undefined} />}

      {!isLoading && !isError && holidays.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead><tr><th scope="col">Date</th><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Active</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {holidays.map((h) => (
                <tr key={h.id}>
                  <td>{h.date}</td><td>{h.name}</td><td style={{ textTransform: 'capitalize' }}>{h.holiday_type}</td>
                  <td>{h.is_active ? 'Yes' : 'No'}</td>
                  <td style={{ display: 'flex', gap: 8 }}>
                    <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setEditing(h)}>Edit</button>
                    <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setDeleting(h)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && <HolidayForm initial={editing.id ? editing : null} busy={save.isPending} onClose={() => setEditing(null)} onSubmit={(f) => save.mutate(f)} />}
      {deleting && (
        <ConfirmModal title="Delete holiday" danger confirmWord="DELETE" confirmLabel="Delete" busy={del.isPending}
          message={`Delete "${deleting.name}" on ${deleting.date}?`} onClose={() => setDeleting(null)} onConfirm={() => del.mutate(deleting.id)} />
      )}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default HolidayManagement;
