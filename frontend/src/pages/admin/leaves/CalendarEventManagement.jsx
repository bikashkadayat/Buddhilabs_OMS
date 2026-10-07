import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';

import { adminLeaveService } from '../../../services/adminLeaveService';
import YearSelector from '../../../components/leave-records/YearSelector';
import ConfirmModal from '../../../components/admin/ConfirmModal';
import Toast from '../../../components/admin/Toast';
import { Skeleton, EmptyState, ErrorState } from '../../../components/leave-records/States';

/**
 * Admin management for the Nepali-calendar events (festivals, jayantis,
 * observances, national days) that every employee sees on /calendar.
 *
 * Kept SEPARATE from Holiday management on purpose: a Holiday makes the day
 * non-working and there is one per day; a CalendarEvent is display-only and a
 * single day can carry several. `is_public_holiday` here only colours the day
 * red on the patro — it does NOT give the office the day off. To actually close
 * the office, add a Holiday too (Administration → Holidays). The form says so.
 */
const EVENT_TYPES = [
  { value: 'festival', label: 'Festival' },
  { value: 'national', label: 'National day' },
  { value: 'religious', label: 'Religious observance' },
  { value: 'jayanti', label: 'Jayanti' },
  { value: 'observance', label: 'Observance' },
];

const empty = {
  date: '', name: '', name_np: '', event_type: 'festival',
  tithi: '', detail: '', is_public_holiday: false, is_active: true,
};

const EventForm = ({ initial, busy, error, onClose, onSubmit }) => {
  const [form, setForm] = useState(initial || empty);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const setBool = (k) => (e) => setForm({ ...form, [k]: e.target.checked });
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Calendar event" onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head">
          <h3>{initial ? 'Edit' : 'New'} calendar event</h3>
          <button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </div>
        <label className="lr-field"><span>Date</span><input type="date" value={form.date} onChange={set('date')} aria-label="Date" /></label>
        <label className="lr-field"><span>Name (English)</span><input type="text" value={form.name} onChange={set('name')} aria-label="Name (English)" placeholder="Krishna Janmashtami" /></label>
        <label className="lr-field"><span>Name (Devanagari)</span><input type="text" value={form.name_np} onChange={set('name_np')} aria-label="Name (Devanagari)" placeholder="श्रीकृष्ण जन्माष्टमी" /></label>
        <label className="lr-field"><span>Type</span>
          <select value={form.event_type} onChange={set('event_type')} aria-label="Type">
            {EVENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <label className="lr-field"><span>Tithi</span><input type="text" value={form.tithi} onChange={set('tithi')} aria-label="Tithi" placeholder="अष्टमी" /></label>
        <label className="lr-field"><span>Detail</span><input type="text" value={form.detail} onChange={set('detail')} aria-label="Detail" /></label>
        <label className="lr-check"><input type="checkbox" checked={form.is_public_holiday} onChange={setBool('is_public_holiday')} /> <span>Public holiday (colours the day red on the calendar — does <strong>not</strong> close the office; add a Holiday for that)</span></label>
        <label className="lr-check"><input type="checkbox" checked={form.is_active} onChange={setBool('is_active')} /> <span>Active (visible on the calendar)</span></label>
        {error && <div className="memo-form-error" role="alert">{error}</div>}
        <div className="lr-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="lr-btn lr-btn-primary" disabled={!form.date || !form.name || busy} onClick={() => onSubmit(form)}>Save</button>
        </div>
      </div>
    </div>
  );
};

const CalendarEventManagement = () => {
  const qc = useQueryClient();
  const [year, setYear] = useState(new Date().getFullYear());
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [toast, setToast] = useState(null);
  const [formError, setFormError] = useState('');

  const {
    data: eventsData = [], isLoading, isError, error, refetch,
  } = useQuery({ queryKey: ['admin-calendar-events', year], queryFn: () => adminLeaveService.getCalendarEvents(year) });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['admin-calendar-events'] });
    // The employee-facing calendar reads a different query key; refresh it too
    // so an added festival shows up without a hard reload.
    qc.invalidateQueries({ queryKey: ['calendar-events'] });
  };

  const save = useMutation({
    mutationFn: (form) => (editing?.id
      ? adminLeaveService.updateCalendarEvent(editing.id, form)
      : adminLeaveService.createCalendarEvent(form)),
    onSuccess: () => { invalidate(); setToast({ message: 'Event saved.', tone: 'success' }); setEditing(null); setFormError(''); },
    onError: (e) => {
      const data = e?.response?.data || {};
      const first = data.name?.[0] || data.date?.[0] || data.non_field_errors?.[0] || data.detail;
      setFormError(first || 'Save failed.');
    },
  });
  const del = useMutation({
    mutationFn: (id) => adminLeaveService.deleteCalendarEvent(id),
    onSuccess: () => { invalidate(); setToast({ message: 'Event deleted.', tone: 'success' }); setDeleting(null); },
    onError: () => { setToast({ message: 'Delete failed.', tone: 'error' }); setDeleting(null); },
  });

  const openNew = () => { setFormError(''); setEditing({}); };
  const openEdit = (row) => { setFormError(''); setEditing(row); };

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>Calendar events</h2>
          <div className="lr-page-sub">Festivals, jayantis and observances shown on the Nepali calendar for all employees</div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <YearSelector currentYear={year} onChange={setYear} minYear={2023} />
          <button type="button" className="lr-btn lr-btn-primary" onClick={openNew}>New event</button>
        </div>
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && eventsData.length === 0 && (
        <EmptyState message={`No calendar events for ${year}. Add festivals and observances employees will see on the calendar.`} />
      )}

      {!isLoading && !isError && eventsData.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead>
              <tr>
                <th scope="col">Date</th><th scope="col">Name</th><th scope="col">Type</th>
                <th scope="col">Tithi</th><th scope="col">Holiday</th><th scope="col">Active</th><th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {eventsData.map((ev) => (
                <tr key={ev.id}>
                  <td>{ev.date}</td>
                  <td>{ev.name_np ? `${ev.name_np} · ${ev.name}` : ev.name}</td>
                  <td>{ev.event_type_display || ev.event_type}</td>
                  <td>{ev.tithi || '—'}</td>
                  <td>{ev.is_public_holiday ? 'Yes' : 'No'}</td>
                  <td>{ev.is_active ? 'Yes' : 'No'}</td>
                  <td style={{ display: 'flex', gap: 8 }}>
                    <button type="button" className="lr-btn lr-btn-ghost" onClick={() => openEdit(ev)}>Edit</button>
                    <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setDeleting(ev)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <EventForm
          initial={editing.id ? editing : null}
          busy={save.isPending}
          error={formError}
          onClose={() => { setEditing(null); setFormError(''); }}
          onSubmit={(f) => save.mutate(f)}
        />
      )}
      {deleting && (
        <ConfirmModal
          title="Delete calendar event" danger confirmWord="DELETE" confirmLabel="Delete"
          busy={del.isPending}
          message={`Delete "${deleting.name_np || deleting.name}" on ${deleting.date}?`}
          onClose={() => setDeleting(null)} onConfirm={() => del.mutate(deleting.id)}
        />
      )}
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default CalendarEventManagement;
