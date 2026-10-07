import React, { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import api from '../services/api';
import { describeApiError } from '../services/apiErrors';
import { useTenantBranding } from '../hooks/useTenantBranding';
import AuthShell from '../components/auth/AuthShell';

/**
 * Reset password: follow the emailed link, choose a new password.
 *
 * The link is checked before the form is shown, so somebody with an expired
 * link is told so straight away rather than after typing a password twice.
 */
const ResetPassword = () => {
  const { branding } = useTenantBranding({ withMeta: true });
  const [params] = useSearchParams();
  const uid = params.get('uid') || '';
  const token = params.get('token') || '';
  const [link, setLink] = useState({ checking: true, valid: false, detail: '', email: '' });
  const [form, setForm] = useState({ next: '', confirm: '' });
  const [show, setShow] = useState(false);
  const [state, setState] = useState({ busy: false, error: '', done: '' });

  useEffect(() => {
    api.get('/auth/password-reset/confirm/', { params: { uid, token } })
      .then(({ data }) => setLink({ checking: false, ...data }))
      .catch((error) => setLink({
        checking: false, valid: false,
        detail: describeApiError(error, 'We couldn’t check this link. Please try again.'),
      }));
  }, [uid, token]);

  const submit = async (event) => {
    event.preventDefault();
    if (form.next !== form.confirm) {
      setState({ busy: false, error: 'The two passwords don’t match.', done: '' });
      return;
    }
    setState({ busy: true, error: '', done: '' });
    try {
      const { data } = await api.post('/auth/password-reset/confirm/', {
        uid, token, new_password: form.next,
      });
      setState({ busy: false, error: '', done: data.detail });
    } catch (error) {
      const d = error?.response?.data || {};
      const said = Array.isArray(d.new_password) ? d.new_password.join(' ') : d.new_password;
      setState({
        busy: false, done: '',
        error: said || describeApiError(error, 'We couldn’t change your password. Please try again.'),
      });
    }
  };

  let body;
  if (link.checking) {
    body = <p className="lg-sub">Checking your link…</p>;
  } else if (state.done) {
    body = (
      <>
        <p className="lg-banner is-ok" role="status">{state.done}</p>
        <Link to="/login" className="lg-submit lg-submit-link">Sign in</Link>
      </>
    );
  } else if (!link.valid) {
    body = (
      <>
        <p className="lg-banner is-err" role="alert">{link.detail}</p>
        <Link to="/forgot-password" className="lg-submit lg-submit-link">Send a new link</Link>
      </>
    );
  } else {
    body = (
      <>
        <p className="lg-sub">For {link.email}. You’ll be signed out on every other device.</p>
        {state.error && <p className="lg-banner is-err" role="alert">{state.error}</p>}
        <form className="lg-form" onSubmit={submit}>
          <div className="lg-field">
            <label htmlFor="reset-new">New password</label>
            <span className="lg-pass">
              <input id="reset-new" type={show ? 'text' : 'password'} required autoFocus
                     autoComplete="new-password" value={form.next}
                     onChange={(e) => setForm({ ...form, next: e.target.value })} />
              <button type="button" className="lg-pass-toggle" aria-pressed={show}
                      aria-label={show ? 'Hide password' : 'Show password'}
                      onClick={() => setShow(!show)}>
                {show ? 'Hide' : 'Show'}
              </button>
            </span>
          </div>
          <label className="lg-field" htmlFor="reset-confirm">
            <span>Confirm new password</span>
            <input id="reset-confirm" type={show ? 'text' : 'password'} required
                   autoComplete="new-password" value={form.confirm}
                   onChange={(e) => setForm({ ...form, confirm: e.target.value })} />
          </label>
          <button type="submit" className="lg-submit"
                  disabled={state.busy || !form.next || !form.confirm}>
            {state.busy ? 'Saving…' : 'Set new password'}
          </button>
        </form>
      </>
    );
  }

  return (
    <AuthShell branding={branding} welcome="Choose a new password and you’re back in.">
      <h1 className="lg-title">Choose a new password</h1>
      {body}
      <p className="lg-help"><Link to="/login">Back to sign in</Link></p>
    </AuthShell>
  );
};

export default ResetPassword;
