import React, { useCallback, useEffect, useState } from 'react';
import { KeyRound, MonitorSmartphone, CheckCircle2 } from 'lucide-react';

import api from '../../services/api';
import { authService } from '../../services/authService';
import { describeApiError } from '../../services/apiErrors';

/**
 * Change your password, and see where you are signed in.
 *
 * Every tenant user could change their password exactly once -- at the forced
 * first sign-in -- and never again without asking an administrator. And
 * nobody but platform staff could see their sessions. Both are now on the
 * profile, reachable from the account menu's Security and Sessions.
 */
const jtiOf = (token) => {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(part)).jti || null;
  } catch {
    return null;
  }
};

const when = (iso) => (iso
  ? new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
  })
  : '—');

export const ChangePassword = () => {
  const [form, setForm] = useState({ current: '', next: '', confirm: '' });
  const [state, setState] = useState({ busy: false, error: '', done: false });

  const submit = async (event) => {
    event.preventDefault();
    if (form.next !== form.confirm) {
      setState({ busy: false, error: 'The two new passwords don’t match.', done: false });
      return;
    }
    setState({ busy: true, error: '', done: false });
    try {
      await authService.changePassword(form.current, form.next);
      setForm({ current: '', next: '', confirm: '' });
      setState({ busy: false, error: '', done: true });
    } catch (error) {
      const d = error?.response?.data || {};
      const said = [d.current_password, d.new_password]
        .map((v) => (Array.isArray(v) ? v.join(' ') : v)).find(Boolean);
      setState({
        busy: false, done: false,
        error: said || describeApiError(error, 'We couldn’t change your password. Please try again.'),
      });
    }
  };

  const field = (key, label, autoComplete) => (
    <label className="acc-field">
      <span>{label}</span>
      <input type="password" autoComplete={autoComplete} required value={form[key]}
             onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
    </label>
  );

  return (
    <section className="acc-card" id="security" aria-labelledby="acc-sec-h">
      <h2 id="acc-sec-h" className="acc-title"><KeyRound size={18} aria-hidden="true" /> Password</h2>
      <p className="acc-lede">Choose something you don’t use anywhere else.</p>
      <form className="acc-form" onSubmit={submit}>
        {field('current', 'Current password', 'current-password')}
        {field('next', 'New password', 'new-password')}
        {field('confirm', 'Confirm new password', 'new-password')}
        {state.error && <p className="acc-err" role="alert">{state.error}</p>}
        {state.done && (
          <p className="acc-ok" role="status">
            <CheckCircle2 size={15} aria-hidden="true" /> Your password has been changed.
          </p>
        )}
        <button type="submit" className="btn btn-primary" disabled={state.busy}>
          {state.busy ? 'Saving…' : 'Change password'}
        </button>
      </form>
    </section>
  );
};

export const Sessions = () => {
  const [rows, setRows] = useState(null);
  const [note, setNote] = useState('');

  const load = useCallback(() => {
    api.get('/auth/sessions/')
      .then(({ data }) => setRows(Array.isArray(data) ? data : []))
      .catch(() => setRows([]));
  }, []);
  useEffect(load, [load]);

  const here = jtiOf(localStorage.getItem('refreshToken') || '');
  const endOthers = async () => {
    setNote('');
    try {
      const { data } = await api.post('/auth/sessions/end-others/', {
        refresh: localStorage.getItem('refreshToken'),
      });
      setNote(data.ended
        ? `Signed out of ${data.ended} other ${data.ended === 1 ? 'device' : 'devices'}.`
        : 'You weren’t signed in anywhere else.');
      load();
    } catch (error) {
      setNote(describeApiError(error, 'We couldn’t sign out your other devices. Please try again.'));
    }
  };

  return (
    <section className="acc-card" id="sessions" aria-labelledby="acc-ses-h">
      <h2 id="acc-ses-h" className="acc-title">
        <MonitorSmartphone size={18} aria-hidden="true" /> Where you’re signed in
      </h2>
      <p className="acc-lede">If you see a device you don’t recognise, sign out everywhere else.</p>
      {rows === null ? <p className="acc-lede">Loading…</p> : (
        <ul className="acc-list">
          {rows.map((row) => (
            <li key={row.jti}>
              <strong>{row.jti === here ? 'This device' : 'Another device'}</strong>
              <span>Last active {when(row.last_active)}</span>
            </li>
          ))}
          {rows.length === 0 && <li><span>No active sessions could be listed.</span></li>}
        </ul>
      )}
      {rows && rows.length > 1 && (
        <button type="button" className="btn btn-ghost" onClick={endOthers}>
          Sign out everywhere else
        </button>
      )}
      {note && <p className="acc-ok" role="status">{note}</p>}
    </section>
  );
};
