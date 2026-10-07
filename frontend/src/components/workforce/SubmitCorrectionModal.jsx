import React, { useState } from 'react';
import { Paperclip, X } from 'lucide-react';
import useBodyScrollLock from '../../hooks/useBodyScrollLock';

const today = () => new Date().toISOString().slice(0, 10);

/**
 * Raise a correction.
 *
 * Times are entered as local `HH:MM` against the chosen date and sent as a full
 * ISO string WITH offset. The API rejects naive timestamps outright — a naive
 * time would be read as UTC and land 5h45m out for Asia/Kathmandu — so building
 * the offset in here is load-bearing, not cosmetic.
 */
const SubmitCorrectionModal = ({ onClose, onSubmit, isPending }) => {
  useBodyScrollLock(true);
  const [form, setForm] = useState({
    attendance_date: today(), check_in: '', check_out: '',
    requested_status: '', reason: '',
  });
  const [file, setFile] = useState(null);
  const [error, setError] = useState('');

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const toIso = (time) => {
    if (!time) return null;
    // `new Date('YYYY-MM-DDTHH:MM')` is parsed as LOCAL time, and toISOString
    // then carries the correct instant. Concatenating a 'Z' would not.
    const local = new Date(`${form.attendance_date}T${time}`);
    return Number.isNaN(local.getTime()) ? null : local.toISOString();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!form.reason.trim()) {
      setError('Please explain what needs correcting.');
      return;
    }
    if (!form.check_in && !form.check_out && !form.requested_status) {
      setError('Request at least one of: check-in time, check-out time, or status.');
      return;
    }
    if (form.attendance_date > today()) {
      setError('You cannot correct a day that has not happened yet.');
      return;
    }
    if (form.check_in && form.check_out && form.check_out <= form.check_in) {
      setError('Check-out must be after check-in.');
      return;
    }

    try {
      await onSubmit({
        attendance_date: form.attendance_date,
        requested_check_in: toIso(form.check_in),
        requested_check_out: toIso(form.check_out),
        requested_status: form.requested_status,
        reason: form.reason.trim(),
        attachment: file,
      });
    } catch (err) {
      const detail = err?.response?.data;
      setError(
        detail?.detail
        || (detail && typeof detail === 'object'
            ? Object.entries(detail).map(([k, v]) => `${k}: ${v}`).join(' · ')
            : 'Could not submit that request.'),
      );
    }
  };

  return (
    <div className="wf-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="wf-modal" role="dialog" aria-modal="true"
        aria-labelledby="wf-correction-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="wf-modal-head">
          <h2 id="wf-correction-title">Request an attendance correction</h2>
          <button type="button" className="wf-btn wf-btn-ghost wf-btn-icon"
                  onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="wf-form">
          <label className="wf-field">
            <span className="wf-field-label">Date</span>
            <input type="date" className="wf-input" max={today()}
                   value={form.attendance_date} onChange={set('attendance_date')} required />
          </label>

          <div className="wf-field-row">
            <label className="wf-field">
              <span className="wf-field-label">Check in</span>
              <input type="time" className="wf-input" value={form.check_in}
                     onChange={set('check_in')} />
            </label>
            <label className="wf-field">
              <span className="wf-field-label">Check out</span>
              <input type="time" className="wf-input" value={form.check_out}
                     onChange={set('check_out')} />
            </label>
          </div>

          <label className="wf-field">
            <span className="wf-field-label">Status (optional)</span>
            <select className="wf-input" value={form.requested_status}
                    onChange={set('requested_status')}>
              <option value="">Derive it from the times</option>
              <option value="present">Present</option>
              <option value="late">Late</option>
              <option value="half_day">Half Day</option>
              <option value="absent">Absent</option>
              <option value="on_leave">On Leave</option>
              <option value="holiday">Holiday</option>
            </select>
            {/* Work From Home is intentionally absent: it is derived from an
                approved WFH request plus a real check-in. */}
          </label>

          <label className="wf-field">
            <span className="wf-field-label">Reason</span>
            <textarea className="wf-input" rows={3} value={form.reason}
                      onChange={set('reason')}
                      placeholder="e.g. The device did not register my morning punch."
                      required />
          </label>

          <label className="wf-field wf-file">
            <span className="wf-field-label">Attachment (optional)</span>
            <span className="wf-btn wf-btn-ghost">
              <Paperclip size={16} /> {file ? file.name : 'Choose a file'}
            </span>
            <input type="file" className="wf-file-input"
                   onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </label>

          {error && <p className="wf-inline-error" role="alert">{error}</p>}

          <div className="wf-modal-actions">
            <button type="button" className="wf-btn wf-btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="wf-btn wf-btn-primary" disabled={isPending}>
              {isPending ? 'Submitting…' : 'Submit request'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default SubmitCorrectionModal;
