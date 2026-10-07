import React, { useState, useEffect } from 'react';
import { useNavigate, Navigate, Link } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { useTenantBranding } from '../hooks/useTenantBranding';
import { PLATFORM_NAME } from '../config/platform';
import AuthShell from '../components/auth/AuthShell';

/**
 * What to tell somebody whose sign-in did not work, in their language.
 *
 * The server's own sentence wins when it is specific -- a locked account,
 * a suspended workspace, the wrong address for this workspace -- because
 * it knows why. The generic cases are rewritten: "Invalid email or
 * password." is accurate and cold, and axios's "Network Error" is neither.
 */
const signInError = (err) => {
  const response = err?.response;
  if (!response) {
    return 'We can’t reach the server. Check your internet connection and try again.';
  }
  if (response.status === 429) {
    return 'Too many attempts. Please wait a minute, then try again.';
  }
  const data = response.data || {};
  const said = (typeof data.detail === 'string' && data.detail)
    || (Array.isArray(data.non_field_errors) && data.non_field_errors[0])
    || '';
  if (!said || /invalid email or password|no active account|credentials/i.test(said)) {
    if (response.status === 400 || response.status === 401) {
      return 'That email and password don’t match. Check them and try again.';
    }
  }
  if (said && said.length < 240 && !/<html/i.test(said)) return said;
  return 'We couldn’t sign you in. Please try again.';
};

const Login = () => {
  const navigate = useNavigate();
  const { login, isAuthenticated, isPlatformStaff } = useAuth();
  const [showPassword, setShowPassword] = useState(false);
  const [formValues, setFormValues] = useState({ email: '', password: '' });
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showLogoutSuccess, setShowLogoutSuccess] = useState(false);
  // Phase S6 Part 7. Whose login page is this? Answered before anybody signs
  // in, from the host the request arrived on. Null means "no custom branding
  // or no answer", and the page then renders exactly as it ships.
  const { branding, registrationOpen } = useTenantBranding({ withMeta: true });
  // Whether this deployment offers self-service signup at all. Read from the
  // same unauthenticated payload the branding comes from, so a link to
  // /register is only offered where those routes exist -- they answer 404
  // when the capability is off, and a link to a 404 is worse than no link.
  //
  // AND ONLY ON THE PLATFORM'S OWN PAGE. This read the flag from `branding`,
  // which exists only for a known tenant -- so the link appeared on every
  // customer's sign-in page, inviting their staff to "create a workspace for
  // your organization" on the page of the organization they already belong
  // to, and never on the platform's page, where it was meant to be.
  const canRegister = registrationOpen && !branding;

  useEffect(() => {
    document.title = `Sign in · ${branding?.name || PLATFORM_NAME}`;
    const justLoggedOut = localStorage.getItem('justLoggedOut');
    if (justLoggedOut) {
      localStorage.removeItem('justLoggedOut');
      setShowLogoutSuccess(true);
      setTimeout(() => setShowLogoutSuccess(false), 4000);
    }
  }, [branding?.name]);

  // Always hand off to the role landing ("/"), which sends each role to its own
  // dashboard. Never bounce back to a deep-linked page (that used to land users
  // on /memos after login).
  //
  // A PLATFORM OPERATOR GOES SOMEWHERE ELSE ENTIRELY (Phase S6). They belong
  // to no organization, so "/" has no workspace to show them: every widget on
  // it reads the tenant they are in, and they are in none. The server refuses
  // those requests anyway — this is what stops an operator seeing a screenful
  // of failures before they can get to the console.
  if (isAuthenticated) {
    return <Navigate to={isPlatformStaff ? '/platform' : '/'} replace />;
  }

  const handleChange = (e) => setFormValues({ ...formValues, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const user = await login(formValues.email, formValues.password);
      localStorage.setItem('justLoggedIn', 'true');
      // First-login password change takes precedence over the dashboard redirect.
      if (user.must_change_password) {
        navigate('/auth/first-login-change-password', { replace: true });
        return;
      }
      // A platform operator goes to the console; everybody else to the role
      // landing, which routes each role to its dashboard.
      navigate(user.is_platform_staff ? '/platform' : '/', { replace: true });
    } catch (err) {
      setError(signInError(err));
    } finally {
      setLoading(false);
    }
  };

  const name = branding?.name || PLATFORM_NAME;
  const welcome = branding?.login_tagline
    || (branding
      ? `Attendance, leave, tasks and documents for everyone at ${branding.name}.`
      : 'Sign in to your organization’s workspace.');

  return (
    <AuthShell branding={branding} welcome={welcome}>
          <h1 className="lg-title">Sign in to {name}</h1>
          <p className="lg-sub">Use your work email and password.</p>

          {showLogoutSuccess && (
            <p className="lg-banner is-ok" role="status">You’ve been signed out.</p>
          )}
          {error && (
            <p className="lg-banner is-err" role="alert" id="login-error">{error}</p>
          )}

          <form className="lg-form" onSubmit={handleSubmit} noValidate={false}>
            <label className="lg-field" htmlFor="login-email">
              <span>Email</span>
              <input
                id="login-email"
                name="email"
                type="email"
                value={formValues.email}
                onChange={handleChange}
                required
                placeholder="you@yourorganization.com"
                autoComplete="username"
                autoFocus
                aria-describedby={error ? 'login-error' : undefined}
              />
            </label>

            {/* The Show button sits OUTSIDE the label: inside it, the
                field's accessible name became "Password Show". */}
            <div className="lg-field">
              <label htmlFor="login-password">Password</label>
              <span className="lg-pass">
                <input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  name="password"
                  value={formValues.password}
                  onChange={handleChange}
                  required
                  placeholder="Your password"
                  autoComplete="current-password"
                  aria-describedby={error ? 'login-error' : undefined}
                />
                <button type="button" className="lg-pass-toggle"
                        onClick={() => setShowPassword(!showPassword)}
                        aria-label={showPassword ? 'Hide password' : 'Show password'}
                        aria-pressed={showPassword}>
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </span>
            </div>

            <button
              type="submit"
              className="lg-submit"
              disabled={loading || !formValues.email.trim() || !formValues.password}
            >
              {loading ? (
                <><span className="auth-spinner" aria-hidden="true" />Signing in…</>
              ) : 'Sign in'}
            </button>
          </form>

          <p className="lg-help">
            <Link to="/forgot-password">Forgot your password?</Link>
          </p>
          {canRegister && (
            <p className="lg-help">
              New to {PLATFORM_NAME}? <Link to="/register">Create a workspace</Link>
            </p>
          )}
    </AuthShell>
  );
};

export default Login;
