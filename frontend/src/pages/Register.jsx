import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { registrationService } from '../services/registrationService';

/**
 * Phase S7 Parts 1 and 2: create a workspace.
 *
 * NOT THE SAME THING AS USER SELF-REGISTRATION, which this product removed in
 * Phase 2.5 and which stays removed: nobody signs themselves up as an
 * EMPLOYEE of an existing organization — their administrator creates that
 * account. This form creates an ORGANIZATION and its first administrator, and
 * those are the only two things it creates.
 *
 * THE SUBDOMAIN FIELD IS THE WHOLE DESIGN PROBLEM. It is permanent, it is
 * public, it becomes their hostname, and most people filling this form have
 * never chosen one before. So it is checked as they type, the answer says
 * WHICH problem it has ("taken", "reserved", "too short") rather than a
 * generic "invalid", and the hostname they will actually use is shown under
 * the field as it will look.
 *
 * NOTHING IS CREATED BY THIS PAGE. The copy says so twice, before and after
 * submitting, because the next thing that happens is an email and a person
 * who believes they already have a workspace will not go looking for it.
 */
const BLANK = {
  organization_name: '',
  slug: '',
  industry: '',
  country: 'NP',
  organization_email: '',
  admin_name: '',
  admin_email: '',
  password: '',
  password_confirmation: '',
  website: '',          // honeypot; see below
};

const Register = () => {
  const [values, setValues] = useState(BLANK);
  const [slugState, setSlugState] = useState(null);
  const [checking, setChecking] = useState(false);
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(null);
  const [closed, setClosed] = useState(false);
  const timer = useRef(null);

  useEffect(() => { document.title = 'Create a workspace'; }, []);

  // The trial length, from the server. Phase S11 Part 7.
  const [trialDays, setTrialDays] = useState(null);
  useEffect(() => {
    let alive = true;
    registrationService.availability()
      .then(({ data }) => { if (alive) setTrialDays(data?.trial_days ?? null); })
      .catch(() => { /* the page still works; it just promises less */ });
    return () => { alive = false; };
  }, []);

  const set = (field) => (event) => {
    const { value } = event.target;
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  };

  // DEBOUNCED, because this fires per keystroke and the endpoint is rate
  // limited to 30/min. 400ms is long enough that typing "abcschool" is one
  // request rather than nine.
  const onSlugChange = (event) => {
    const value = event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '');
    setValues((current) => ({ ...current, slug: value }));
    setErrors((current) => ({ ...current, slug: undefined }));
    setSlugState(null);
    if (timer.current) clearTimeout(timer.current);
    if (!value) return;
    setChecking(true);
    timer.current = setTimeout(() => {
      registrationService.checkSlug(value)
        .then(({ data }) => setSlugState(data))
        .catch((error) => {
          if (error.response?.status === 404) setClosed(true);
          else setSlugState(null);
        })
        .finally(() => setChecking(false));
    }, 400);
  };

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const submit = useCallback(async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setErrors({});
    try {
      const { data } = await registrationService.register(values);
      setSent(data);
    } catch (error) {
      if (error.response?.status === 404) setClosed(true);
      else if (error.response?.status === 429) {
        setErrors({ detail: 'Too many attempts from here. Please try again later.' });
      } else {
        const body = error.response?.data || {};
        setErrors(typeof body === 'object' ? body
          : { detail: 'The registration could not be submitted.' });
      }
    } finally {
      setSubmitting(false);
    }
  }, [values]);

  if (closed) {
    return (
      <main className="auth-page" role="main">
        <div className="auth-card">
          <h1 className="auth-title">Not available</h1>
          <p className="auth-sub">
            This installation does not accept new workspaces. If you have an
            account, <Link to="/login">sign in</Link>.
          </p>
        </div>
      </main>
    );
  }

  if (sent) {
    return (
      <main className="auth-page" role="main">
        <div className="auth-card">
          <h1 className="auth-title">Check your email</h1>
          <p className="auth-sub">
            We have sent a confirmation link to{' '}
            <strong>{values.admin_email}</strong>. It is good for{' '}
            {sent.expires_in_hours} hours.
          </p>
          <p className="auth-note">
            <strong>Nothing has been created yet.</strong> Your workspace is
            built when you confirm the address — that is also how we know the
            mailbox is yours.
          </p>
          <p className="auth-note">
            You will sign in with the password you just chose. We never saw it
            and cannot send it to you.
          </p>
          <p className="auth-foot"><Link to="/login">Back to sign in</Link></p>
        </div>
      </main>
    );
  }

  const slugTone = slugState?.available ? 'is-ok' : 'is-bad';

  return (
    <main className="auth-page" role="main">
      <form className="auth-card auth-card-wide" onSubmit={submit} noValidate>
        <h1 className="auth-title">Create a workspace</h1>
        <p className="auth-sub">
          {/* Phase S11 Part 7. This page promised nothing and disclaimed a
              card: the trial was stated in the verification email and on the
              first-login page, and nowhere on the screen where somebody
              decides whether to sign up. The length comes from the server,
              so changing the offer cannot leave this copy stale. */}
          {trialDays
            ? `Free for ${trialDays} days. `
            : ''}
          For your organization. Your own staff accounts are created by you
          afterwards, from inside it.
        </p>

        {errors.detail && (
          <p className="auth-error" role="alert">{String(errors.detail)}</p>
        )}

        <fieldset className="auth-group">
          <legend>Your organization</legend>

          <div className="auth-field">
            <label htmlFor="reg-org-name">Organization name</label>
            <input id="reg-org-name" value={values.organization_name}
                   onChange={set('organization_name')}
                   autoComplete="organization" required />
            {errors.organization_name && (
              <em className="auth-field-error">{errors.organization_name}</em>
            )}
          </div>

          <div className="auth-field">
            <label htmlFor="reg-slug">Workspace address</label>
            <div className="auth-slug">
              <input id="reg-slug" value={values.slug} onChange={onSlugChange}
                     placeholder="abcschool" autoCapitalize="none"
                     autoCorrect="off" spellCheck="false" required
                     aria-describedby="reg-slug-hint" />
              <span className="auth-slug-suffix">.platform.com</span>
            </div>
            {checking && <em className="auth-field-hint">Checking…</em>}
            {!checking && slugState && (
              <em className={`auth-field-hint ${slugTone}`}>
                {slugState.detail}
              </em>
            )}
            {errors.slug && <em className="auth-field-error">{errors.slug}</em>}
            <em className="auth-field-hint" id="reg-slug-hint">
              This becomes your sign-in address and cannot be changed later.
            </em>
          </div>

          <div className="auth-row">
            <div className="auth-field">
              <label htmlFor="reg-industry">Industry</label>
              <input id="reg-industry" value={values.industry}
                     onChange={set('industry')} placeholder="Education" />
            </div>
            <div className="auth-field">
              <label htmlFor="reg-country">Country</label>
              <input id="reg-country" value={values.country}
                     onChange={set('country')} maxLength={2}
                     placeholder="NP" />
              {errors.country && (
                <em className="auth-field-error">{errors.country}</em>
              )}
            </div>
          </div>

          <div className="auth-field">
            <label htmlFor="reg-org-email">Organization email</label>
            <input id="reg-org-email" type="email"
                   value={values.organization_email}
                   onChange={set('organization_email')} required
                   aria-describedby="reg-org-email-hint" />
            {errors.organization_email && (
              <em className="auth-field-error">{errors.organization_email}</em>
            )}
            <em className="auth-field-hint" id="reg-org-email-hint">
              Used for billing and service notices, not for signing in.
            </em>
          </div>
        </fieldset>

        <fieldset className="auth-group">
          <legend>You, as its administrator</legend>

          <div className="auth-field">
            <label htmlFor="reg-admin-name">Your name</label>
            <input id="reg-admin-name" value={values.admin_name}
                   onChange={set('admin_name')} autoComplete="name" />
          </div>

          <div className="auth-field">
            <label htmlFor="reg-admin-email">Your email</label>
            <input id="reg-admin-email" type="email" value={values.admin_email}
                   onChange={set('admin_email')} autoComplete="username"
                   required aria-describedby="reg-admin-email-hint" />
            {errors.admin_email && (
              <em className="auth-field-error">{errors.admin_email}</em>
            )}
            <em className="auth-field-hint" id="reg-admin-email-hint">
              We send the confirmation link here, and this is how you sign in.
            </em>
          </div>

          <div className="auth-field">
            <label htmlFor="reg-password">Password</label>
            <input id="reg-password" type="password" value={values.password}
                   onChange={set('password')} autoComplete="new-password"
                   required />
            {errors.password && (
              <em className="auth-field-error">
                {Array.isArray(errors.password)
                  ? errors.password.join(' ') : errors.password}
              </em>
            )}
          </div>

          <div className="auth-field">
            <label htmlFor="reg-password-2">Confirm password</label>
            <input id="reg-password-2" type="password"
                   value={values.password_confirmation}
                   onChange={set('password_confirmation')}
                   autoComplete="new-password" required />
            {errors.password_confirmation && (
              <em className="auth-field-error">
                {errors.password_confirmation}
              </em>
            )}
          </div>
        </fieldset>

        {/*
          THE HONEYPOT. Off-screen rather than display:none, because some bots
          skip hidden inputs but fill everything they can reach. A person
          never sees it; a script that posts every field it finds fills it, and
          the server then answers with the ordinary success body so the script
          learns nothing.
        */}
        <div className="auth-offscreen" aria-hidden="true">
          <label htmlFor="reg-website">Website</label>
          <input id="reg-website" value={values.website}
                 onChange={set('website')} tabIndex={-1}
                 autoComplete="off" />
        </div>

        <button type="submit" className="btn btn-primary auth-submit"
                disabled={submitting || (slugState && !slugState.available)}>
          {submitting ? 'Sending…' : 'Create workspace'}
        </button>

        <p className="auth-note">
          Nothing is created until you confirm your email.
          {trialDays
            ? ` No payment details are needed, and nothing is charged during
                the ${trialDays}-day trial.`
            : ' No payment details are needed.'}
        </p>
        <p className="auth-foot">
          Already have a workspace? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </main>
  );
};

export default Register;
