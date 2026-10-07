import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { authService } from '../services/authService';

const strength = (pw) => {
  let score = 0;
  if (pw.length >= 8) score += 1;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) score += 1;
  if (/\d/.test(pw)) score += 1;
  if (/[^A-Za-z0-9]/.test(pw)) score += 1;
  return score; // 0..4
};

const LABELS = ['Too weak', 'Weak', 'Fair', 'Good', 'Strong'];
const COLORS = ['#ef4444', '#f59e0b', '#f59e0b', '#10b981', '#10b981'];

const FirstLoginPasswordChange = () => {
  const navigate = useNavigate();
  const { completePasswordChange } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const s = strength(next);
  const valid = current && next.length >= 8 && next === confirm && s >= 2;

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await authService.changePassword(current, next);
      completePasswordChange();
      navigate('/', { replace: true });
    } catch (err) {
      const d = err?.response?.data;
      // Each field can carry a LIST of sentences ("Too short.", "Too
      // common."), which React would print run together with no spaces.
      const said = [d?.current_password, d?.new_password, d?.detail]
        .map((v) => (Array.isArray(v) ? v.join(' ') : v)).find(Boolean);
      setError(said || 'We couldn’t change your password. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page" style={{ display: 'flex', justifyContent: 'center', paddingTop: 60 }}>
      <div className="side-card" style={{ padding: 32, maxWidth: 440, width: '100%' }}>
        <h2 style={{ margin: '0 0 6px' }}>Set a new password</h2>
        <p className="lr-page-sub" style={{ marginBottom: 20 }}>
          For security, please change the temporary password before continuing.
        </p>
        <form onSubmit={submit}>
          <label className="lr-field"><span>Current password</span>
            <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" aria-label="Current password" />
          </label>
          <label className="lr-field"><span>New password</span>
            <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" aria-label="New password" />
          </label>
          {next && (
            <div aria-hidden="true" style={{ margin: '-4px 0 12px' }}>
              <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-main)', overflow: 'hidden' }}>
                <div style={{ width: `${(s / 4) * 100}%`, height: '100%', background: COLORS[s], transition: 'width .2s' }} />
              </div>
              <div style={{ fontSize: 'var(--fs-meta)', color: COLORS[s], marginTop: 4 }}>{LABELS[s]}</div>
            </div>
          )}
          <label className="lr-field"><span>Confirm new password</span>
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" aria-label="Confirm new password" />
          </label>
          {confirm && confirm !== next && <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)', marginBottom: 8 }}>Passwords do not match.</div>}
          {error && <div role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--danger)', marginBottom: 8 }}>{error}</div>}
          <button type="submit" className="lr-btn lr-btn-primary" disabled={!valid || busy} style={{ width: '100%' }}>
            {busy ? 'Saving…' : 'Change password & continue'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default FirstLoginPasswordChange;
