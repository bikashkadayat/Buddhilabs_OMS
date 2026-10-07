import React, { useState } from 'react';
import { Link } from 'react-router-dom';

import api from '../services/api';
import { describeApiError } from '../services/apiErrors';
import { useTenantBranding } from '../hooks/useTenantBranding';
import AuthShell from '../components/auth/AuthShell';

/**
 * Forgot password: ask for a link.
 *
 * The answer is the same whether or not the address has an account -- the
 * server guarantees that, and this page does not try to be cleverer than it:
 * it shows what the server said, and how to try again.
 */
const ForgotPassword = () => {
  const { branding } = useTenantBranding({ withMeta: true });
  const [email, setEmail] = useState('');
  const [state, setState] = useState({ busy: false, sent: '', error: '' });

  const submit = async (event) => {
    event.preventDefault();
    setState({ busy: true, sent: '', error: '' });
    try {
      const { data } = await api.post('/auth/password-reset/', { email });
      setState({ busy: false, sent: data.detail, error: '' });
    } catch (error) {
      setState({
        busy: false, sent: '',
        error: error?.response?.data?.email
          || describeApiError(error, 'We couldn’t send the link. Please try again.'),
      });
    }
  };

  return (
    <AuthShell branding={branding} welcome="It happens. We’ll send you a link to choose a new password.">
      <h1 className="lg-title">Forgot your password?</h1>
      {state.sent ? (
        <>
          <p className="lg-banner is-ok" role="status">{state.sent}</p>
          <p className="lg-help">
            Didn’t get it?{' '}
            <button type="button" className="lg-link-btn"
                    onClick={() => setState({ busy: false, sent: '', error: '' })}>
              Try again
            </button>
          </p>
        </>
      ) : (
        <>
          <p className="lg-sub">Enter the email you sign in with and we’ll email you a link.</p>
          {state.error && <p className="lg-banner is-err" role="alert">{state.error}</p>}
          <form className="lg-form" onSubmit={submit}>
            <label className="lg-field" htmlFor="forgot-email">
              <span>Email</span>
              <input id="forgot-email" type="email" required autoFocus
                     autoComplete="username" value={email}
                     placeholder="you@yourorganization.com"
                     onChange={(e) => setEmail(e.target.value)} />
            </label>
            <button type="submit" className="lg-submit" disabled={state.busy || !email.trim()}>
              {state.busy ? 'Sending…' : 'Send reset link'}
            </button>
          </form>
        </>
      )}
      <p className="lg-help"><Link to="/login">Back to sign in</Link></p>
    </AuthShell>
  );
};

export default ForgotPassword;
