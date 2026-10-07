import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  Shield, KeyRound, Clock, CheckCircle2, AlertTriangle, Camera,
  MonitorSmartphone,
} from 'lucide-react';

import api from '../../services/api';
import { platformService } from '../../services/platformService';
import { useAuth } from '../../hooks/useAuth';
import UserAvatar from '../../components/common/UserAvatar';

/**
 * The operator's own account.
 *
 * WHAT WAS MISSING. Every tenant user has a profile, a password change and a
 * photo. Platform staff — the accounts that can suspend a customer, read an
 * audit trail and provision a workspace — had none of it. Their identity
 * appeared once, as grey text at the foot of the rail. An operator could not
 * change their own password without a shell.
 *
 * RECENT ACTIONS ARE THEIR OWN, FILTERED FROM THE PLATFORM TRAIL. Not a
 * second feed: the same append-only log the Audit page shows, narrowed to
 * this operator. "What have I done today" and "what has anybody done" should
 * never be able to disagree.
 */
const Field = ({ label, children }) => (
  <div className="pf-prof-field">
    <span className="pf-prof-label">{label}</span>
    <span className="pf-prof-value">{children}</span>
  </div>
);

/** The `jti` of a JWT, read without verifying it — only to label a row. */
const jtiOf = (token) => {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(part)).jti || null;
  } catch {
    return null;
  }
};

const stamp = (value) => (value ? new Date(value).toLocaleString() : '—');

const PlatformProfile = () => {
  const { user, refreshUser } = useAuth();
  const location = useLocation();
  const photoInput = useRef(null);
  const [photoState, setPhotoState] = useState({ busy: false, error: null });
  const [sessions, setSessions] = useState(null);
  const [sessionNote, setSessionNote] = useState(null);
  const [profile, setProfile] = useState(null);
  const [actions, setActions] = useState([]);
  const [pw, setPw] = useState({ current_password: '', new_password: '', confirm: '' });
  const [pwState, setPwState] = useState({ busy: false, error: null, done: false });

  useEffect(() => {
    let alive = true;
    api.get('/profile/me/')
      .then(({ data }) => { if (alive) setProfile(data); })
      .catch(() => { if (alive) setProfile({}); });
    platformService.audit({ limit: 200 })
      .then(({ data }) => {
        if (!alive) return;
        const rows = Array.isArray(data) ? data : (data.results || []);
        setActions(rows.filter((r) => r.actor_email === user?.email).slice(0, 8));
      })
      .catch(() => setActions([]));
    return () => { alive = false; };
  }, [user?.email]);

  const loadSessions = useCallback(() => {
    api.get('/auth/sessions/')
      .then(({ data }) => setSessions(Array.isArray(data) ? data : []))
      .catch(() => setSessions([]));
  }, []);
  useEffect(loadSessions, [loadSessions]);

  // The menu links to #security and #sessions. React Router changes the URL
  // and does nothing else, so without this the link lands at the top of the
  // page and the operator has to find the section themselves.
  useEffect(() => {
    if (!location.hash || !profile) return;
    document.getElementById(location.hash.slice(1))
      ?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }, [location.hash, profile]);

  const currentJti = jtiOf(localStorage.getItem('refreshToken') || '');

  const endOthers = async () => {
    setSessionNote(null);
    try {
      const { data } = await api.post('/auth/sessions/end-others/', {
        refresh: localStorage.getItem('refreshToken'),
      });
      setSessionNote(data.ended
        ? `Signed out of ${data.ended} other session${data.ended === 1 ? '' : 's'}.`
        : 'There were no other sessions to end.');
      loadSessions();
    } catch {
      setSessionNote('The other sessions could not be ended. Try again.');
    }
  };

  const uploadPhoto = async (file) => {
    if (!file) return;
    setPhotoState({ busy: true, error: null });
    try {
      const body = new FormData();
      body.append('photo', file);
      const { data } = await api.post('/profile/me/photo/', body);
      setProfile((p) => ({ ...p, profile_photo: data.profile_photo }));
      refreshUser?.();
      setPhotoState({ busy: false, error: null });
    } catch (error) {
      setPhotoState({
        busy: false,
        error: error?.response?.data?.detail || 'The photo could not be uploaded.',
      });
    }
  };

  const removePhoto = async () => {
    setPhotoState({ busy: true, error: null });
    try {
      await api.delete('/profile/me/photo/');
      setProfile((p) => ({ ...p, profile_photo: null }));
      refreshUser?.();
      setPhotoState({ busy: false, error: null });
    } catch {
      setPhotoState({ busy: false, error: 'The photo could not be removed.' });
    }
  };

  const changePassword = async (event) => {
    event.preventDefault();
    if (pw.new_password !== pw.confirm) {
      setPwState({ busy: false, error: 'The two new passwords do not match.', done: false });
      return;
    }
    setPwState({ busy: true, error: null, done: false });
    try {
      await api.post('/auth/change-password/', {
        current_password: pw.current_password,
        new_password: pw.new_password,
      });
      setPw({ current_password: '', new_password: '', confirm: '' });
      setPwState({ busy: false, error: null, done: true });
    } catch (error) {
      setPwState({
        busy: false,
        done: false,
        error: error?.response?.data?.detail
          || Object.values(error?.response?.data || {})[0]
          || 'The password could not be changed.',
      });
    }
  };


  return (
    <div className="pf-page">
      <header className="pf-page-head">
        <div>
          <h1 className="pf-page-title">My profile</h1>
          <p className="pf-page-sub">Your platform account, and what you have done with it.</p>
        </div>
      </header>

      <div className="pf-prof-grid">
        <section className="pf-card">
          <div className="pf-prof-id">
            <div className="pf-prof-photo">
              {/* The same component the top bar uses, refreshed from the
                  session after an upload, so both change together. */}
              <UserAvatar size={56} className="pf-avatar pf-avatar-xl"
                          color="var(--brand-blue)" />
              <button type="button" className="pf-prof-photo-btn"
                      onClick={() => photoInput.current?.click()}
                      disabled={photoState.busy}
                      aria-label={profile?.profile_photo ? 'Change photo' : 'Add a photo'}>
                <Camera size={13} aria-hidden="true" />
              </button>
              <input ref={photoInput} type="file" hidden
                     accept="image/jpeg,image/png,image/webp"
                     onChange={(e) => uploadPhoto(e.target.files?.[0])} />
            </div>
            <div>
              <h2 className="pf-card-title">{profile?.first_name
                ? `${profile.first_name} ${profile.last_name || ''}`.trim()
                : (user?.name || 'Platform operator')}</h2>
              <p className="pf-muted">{user?.email}</p>
              <span className="pf-badge-role">
                <Shield size={11} aria-hidden="true" /> Platform staff
              </span>
            </div>
          </div>
          {photoState.error && <p className="pf-err" role="alert">{photoState.error}</p>}
          {profile?.profile_photo && (
            <button type="button" className="pf-link pf-link-btn" onClick={removePhoto}
                    disabled={photoState.busy}>
              Remove photo
            </button>
          )}
          <Field label="Email">{user?.email || '—'}</Field>
          <Field label="Username">{profile?.username || '—'}</Field>
          <Field label="Role">
            Platform operator — not a member of any workspace
          </Field>
          <Field label="Last sign-in">
            {profile?.last_login
              ? new Date(profile.last_login).toLocaleString()
              : 'This session'}
          </Field>
          <Field label="Two-step sign-in">
            <span className="pf-chip pf-chip-wait">Not available yet</span>
          </Field>
          <p className="pf-prof-note">
            A platform account belongs to no organization — that pairing is a
            database constraint, which is what stops an operator&apos;s token
            working inside a customer&apos;s workspace.
          </p>
        </section>

        <section className="pf-card" id="security">
          <h2 className="pf-card-title">
            <KeyRound size={16} aria-hidden="true" /> Password
          </h2>
          <p className="pf-muted pf-card-lede">
            Yours is the account that can reach every customer&apos;s records.
            Change it here; you do not need an administrator.
          </p>
          <form onSubmit={changePassword} className="pf-form">
            <label className="pf-field">
              <span>Current password</span>
              <input type="password" autoComplete="current-password" required
                     value={pw.current_password}
                     onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
            </label>
            <label className="pf-field">
              <span>New password</span>
              <input type="password" autoComplete="new-password" required
                     value={pw.new_password}
                     onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
            </label>
            <label className="pf-field">
              <span>Confirm new password</span>
              <input type="password" autoComplete="new-password" required
                     value={pw.confirm}
                     onChange={(e) => setPw({ ...pw, confirm: e.target.value })} />
            </label>
            {pwState.error && <p className="pf-err" role="alert">{pwState.error}</p>}
            {pwState.done && (
              <p className="pf-ok" role="status">
                <CheckCircle2 size={14} aria-hidden="true" /> Password changed.
              </p>
            )}
            <button type="submit" className="btn btn-primary" disabled={pwState.busy}>
              {pwState.busy ? 'Changing…' : 'Change password'}
            </button>
          </form>

          <div className="pf-prof-sec">
            <h3 className="pf-card-sub">Account protection</h3>
            <ul className="pf-prof-sec-list">
              <li>
                <CheckCircle2 size={14} aria-hidden="true" className="pf-ok-ic" />
                Lockout after repeated failures
              </li>
              <li>
                <CheckCircle2 size={14} aria-hidden="true" className="pf-ok-ic" />
                Sessions expire and refresh tokens rotate
              </li>
              <li>
                <CheckCircle2 size={14} aria-hidden="true" className="pf-ok-ic" />
                The console is served only on the platform hostname
              </li>
              <li>
                {/* Stated rather than implied. A security panel that lists
                    only what IS in place reads as a complete list. */}
                <AlertTriangle size={14} aria-hidden="true" className="pf-warn-ic" />
                Two-factor authentication is not available yet
              </li>
            </ul>
          </div>
        </section>

        <section className="pf-card pf-prof-wide" id="sessions">
          <h2 className="pf-card-title">
            <MonitorSmartphone size={16} aria-hidden="true" /> Where you are signed in
          </h2>
          <p className="pf-muted pf-card-lede">
            Each row is a browser or device still signed in to this account.
            If you see more than you expect, sign the others out.
          </p>
          {sessions === null ? (
            <p className="pf-muted">Loading…</p>
          ) : sessions.length === 0 ? (
            <p className="pf-muted">No active sessions could be listed.</p>
          ) : (
            <ul className="pf-feed">
              {sessions.map((row) => (
                <li key={row.jti}>
                  <span className="pf-feed-what">
                    {row.jti === currentJti ? 'This browser' : 'Another session'}
                  </span>
                  <span className="pf-feed-who">Last active {stamp(row.last_active)}</span>
                  <span className="pf-feed-when">Ends {stamp(row.expires_at)}</span>
                </li>
              ))}
            </ul>
          )}
          {sessions && sessions.length > 1 && (
            <button type="button" className="btn btn-ghost" onClick={endOthers}>
              Sign out everywhere else
            </button>
          )}
          {sessionNote && <p className="pf-ok" role="status">{sessionNote}</p>}
        </section>

        <section className="pf-card pf-prof-wide">
          <h2 className="pf-card-title">
            <Clock size={16} aria-hidden="true" /> Your recent actions
          </h2>
          {actions.length === 0 ? (
            <p className="pf-muted">
              Nothing recorded against your account yet. Every console action
              you take appears here and on the platform audit trail.
            </p>
          ) : (
            <ul className="pf-feed">
              {actions.map((row) => (
                <li key={row.id}>
                  <span className="pf-feed-what">{row.action_display || row.action}</span>
                  <span className="pf-feed-who">{row.organization_name || '—'}</span>
                  <span className="pf-feed-when">
                    {row.created_at ? new Date(row.created_at).toLocaleString() : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link className="pf-link" to="/platform/audit">
            The whole platform trail →
          </Link>
        </section>
      </div>
    </div>
  );
};

export default PlatformProfile;
