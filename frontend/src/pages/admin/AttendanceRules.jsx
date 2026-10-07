import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Clock3 } from 'lucide-react';

import api from '../../services/api';
import { describeApiError } from '../../services/apiErrors';
import PageHeader from '../../components/common/PageHeader';

/**
 * Attendance rules: office hours, when somebody counts as late, a full day.
 *
 * THERE WAS NO SCREEN FOR THIS. The policy engine, its API and its audit
 * trail all existed, and provisioning seeds a default policy -- but nothing
 * in the workspace could show or change it, while the setup checklist told
 * every new administrator to "Review your attendance rules" and linked to a
 * page that did not exist.
 *
 * Deliberately the everyday fields only, in plain words. Shifts, per-
 * department assignments and the comp-off thresholds stay in the API for
 * now; most organizations have one policy and change it rarely.
 */
const hm = (value) => (value ? String(value).slice(0, 5) : '');

const FIELDS = [
  { key: 'office_start_time', kind: 'time', label: 'Office starts at',
    help: 'The time staff are expected in.' },
  { key: 'late_after_time', kind: 'time', label: 'Late after',
    help: 'Checking in after this counts as late. Leave empty to allow the grace minutes below instead.' },
  { key: 'grace_minutes', kind: 'number', label: 'Grace minutes',
    help: 'Used only when “Late after” is empty.', min: 0, max: 240 },
  { key: 'half_day_after_time', kind: 'time', label: 'Half day after',
    help: 'Checking in at or after this counts as a half day.' },
  { key: 'absent_cutoff_time', kind: 'time', label: 'Absent if not in by',
    help: 'After this, a day with no check-in is marked absent.' },
  { key: 'full_day_hours', kind: 'number', label: 'Hours in a full day', step: '0.25', min: 1, max: 24 },
  { key: 'half_day_hours', kind: 'number', label: 'Hours in a half day', step: '0.25', min: 0.5, max: 24 },
  { key: 'overtime_threshold_hours', kind: 'number', label: 'Overtime after (hours)',
    help: 'Leave empty if you don’t track overtime.', step: '0.25', min: 1, max: 24, optional: true },
];

const toForm = (policy) => Object.fromEntries(FIELDS.map(({ key, kind }) => [
  key, kind === 'time' ? hm(policy[key]) : (policy[key] ?? ''),
]));

const AttendanceRules = () => {
  const [policies, setPolicies] = useState(null);
  const [active, setActive] = useState(null);
  const [form, setForm] = useState({});
  const [state, setState] = useState({ busy: false, error: '', saved: false });
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    api.get('/attendance/policies/')
      .then(({ data }) => {
        const rows = Array.isArray(data) ? data : (data.results || []);
        setPolicies(rows);
        const first = rows.find((p) => p.is_active) || rows[0] || null;
        setActive(first);
        if (first) setForm(toForm(first));
      })
      .catch((error) => setLoadError(describeApiError(error,
        'We couldn’t load your attendance rules. Please try again.')));
  }, []);

  const choose = (id) => {
    const policy = policies.find((p) => p.id === id);
    setActive(policy);
    setForm(toForm(policy));
    setState({ busy: false, error: '', saved: false });
  };

  const save = async (event) => {
    event.preventDefault();
    setState({ busy: true, error: '', saved: false });
    const body = Object.fromEntries(FIELDS.map(({ key, kind, optional }) => {
      const value = form[key];
      if (value === '' || value == null) return [key, optional || kind === 'time' ? null : value];
      return [key, value];
    }));
    try {
      const { data } = await api.patch(`/attendance/policies/${active.id}/`, body);
      setPolicies((rows) => rows.map((p) => (p.id === data.id ? data : p)));
      setActive(data);
      setForm(toForm(data));
      setState({ busy: false, error: '', saved: true });
    } catch (error) {
      setState({
        busy: false, saved: false,
        error: describeApiError(error, 'We couldn’t save the rules. Please check them and try again.'),
      });
    }
  };

  return (
    <div className="page">
      <PageHeader
        title="Attendance rules"
        description="Office hours, and when a day counts as late, half or full."
      />
      {loadError && <p className="acc-err" role="alert">{loadError}</p>}
      {policies && policies.length === 0 && (
        <p className="ar-empty">
          No attendance policy is set up yet.
        </p>
      )}
      {active && (
        <form className="acc-card ar-card" onSubmit={save}>
          <div className="ar-head">
            <h2 className="acc-title"><Clock3 size={18} aria-hidden="true" /> {active.name}</h2>
            {policies.length > 1 && (
              <label className="ar-pick">
                <span>Policy</span>
                <select value={active.id} onChange={(e) => choose(e.target.value)}>
                  {policies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            )}
          </div>
          {/* Seed-data notes ("Seeded at provisioning…") are for engineers. */}
          {active.description && !/^seeded at provisioning/i.test(active.description) && (
            <p className="acc-lede">{active.description}</p>
          )}
          <p className="acc-lede">
            Applies to {active.assignment_count
              ? `${active.assignment_count} assigned group${active.assignment_count === 1 ? '' : 's'}`
              : 'everyone without a more specific policy'}.
          </p>

          <div className="ar-grid">
            {FIELDS.map(({ key, kind, label, help, step, min, max }) => (
              <label className="acc-field" key={key}>
                <span>{label}</span>
                <input type={kind} value={form[key] ?? ''} step={step} min={min} max={max}
                       onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
                {help && <small className="ar-help">{help}</small>}
              </label>
            ))}
          </div>

          {state.error && <p className="acc-err" role="alert">{state.error}</p>}
          {state.saved && (
            <p className="acc-ok" role="status">
              <CheckCircle2 size={15} aria-hidden="true" /> Your changes have been saved.
            </p>
          )}
          <div className="ar-actions">
            <button type="submit" className="btn btn-primary" disabled={state.busy}>
              {state.busy ? 'Saving…' : 'Save changes'}
            </button>
            <Link to="/settings" className="btn btn-ghost">Back to settings</Link>
          </div>
        </form>
      )}
    </div>
  );
};

export default AttendanceRules;
