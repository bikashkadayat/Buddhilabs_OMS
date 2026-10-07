import React, { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { registrationService } from '../services/registrationService';

/**
 * Phase S7 Parts 3, 4, 5 and 8: the moment the workspace comes into existence.
 *
 * THE REQUEST IS FIRED ONCE, FROM A REF GUARD, and that is not paranoia about
 * React's strict mode — it is about what this endpoint does. Verifying
 * provisions a tenant: an organization, a subscription, 76 configuration rows
 * and an administrator. The server is idempotent (a second click on the same
 * link returns the same workspace rather than building another), but a page
 * that fires its side effect twice on every mount is relying on that, and
 * relying on it is how the one case it does not cover gets found in
 * production.
 *
 * IT REPORTS THE HEALTH VERDICT, because Part 8 asks for it and because the
 * alternative is worse than useless: a welcome screen over a half-built
 * workspace sends somebody off to add employees to a system that cannot
 * approve their leave. The verdict comes from the same `verify_organization()`
 * the platform console reads, so the customer and the operator see one answer.
 */
const VerifyEmail = () => {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [state, setState] = useState(token ? 'working' : 'missing');
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const fired = useRef(false);

  useEffect(() => {
    document.title = 'Confirming your workspace';
    if (!token || fired.current) return;
    fired.current = true;
    registrationService.verify(token)
      .then(({ data }) => { setResult(data); setState('done'); })
      .catch((err) => {
        const body = err.response?.data || {};
        setError(body.token || body.detail
          || 'That link could not be confirmed.');
        setState('failed');
      });
  }, [token]);

  if (state === 'missing') {
    return (
      <main className="auth-card" role="main">
        <h1 className="auth-title">Incomplete link</h1>
        <p className="auth-help">
          The confirmation link is missing its token. Open the link in the
          email exactly as it was sent, or{' '}
          <Link to="/register">register again</Link>.
        </p>
      </main>
    );
  }

  if (state === 'working') {
    return (
      <main className="auth-card" role="main">
        <h1 className="auth-title">Building your workspace</h1>
        <p className="auth-help">
          <span className="auth-spinner" aria-hidden="true" />
          Confirming your email and setting everything up. This takes a few
          seconds.
        </p>
      </main>
    );
  }

  if (state === 'failed') {
    return (
      <main className="auth-card" role="main">
        <h1 className="auth-title">Could not confirm</h1>
        <p className="auth-error" role="alert">{String(error)}</p>
        <p className="auth-help">
          Links expire, and registering again replaces the previous link. You
          can <Link to="/register">start again</Link> — nothing was created.
        </p>
      </main>
    );
  }

  const ready = result?.health?.verdict === 'Tenant Ready';
  const gaps = Object.entries(result?.health?.configuration_gaps || {});

  return (
    <main className="auth-card auth-card-wide" role="main">
      <h1 className="auth-title">
        {result.status === 'already_provisioned'
          ? 'Already confirmed' : 'Your workspace is ready'}
      </h1>
      <p className="auth-sub">
        <strong>{result.organization.name}</strong> is set up at{' '}
        <strong>{result.organization.slug}</strong>.
      </p>

      {ready ? (
        <p className="auth-ok" role="status">
          Tenant Ready — departments, leave, attendance, tasks, minutes and
          inventory are all configured and waiting for you.
        </p>
      ) : (
        <div className="auth-error" role="alert">
          <strong>Missing configuration.</strong>
          <ul>
            {gaps.map(([area, missing]) => (
              <li key={area}>
                {area.replace(/_/g, ' ')}: {missing.join(', ')}
              </li>
            ))}
          </ul>
          <p>
            Your workspace exists and you can sign in, but tell us about this
            — some of it will not work until it is fixed.
          </p>
        </div>
      )}

      {result.trial?.days && (
        <p className="auth-help">
          You are on a {result.trial.days}-day trial
          {result.trial.ends_on ? ` until ${result.trial.ends_on}` : ''}. No
          payment details were needed.
        </p>
      )}

      <p className="auth-help">
        Sign in as <strong>{result.admin_email}</strong> with the password you
        chose when you registered.
      </p>

      <Link to="/login" className="btn btn-primary auth-submit">
        Sign in to {result.organization.slug}
      </Link>
    </main>
  );
};

export default VerifyEmail;
