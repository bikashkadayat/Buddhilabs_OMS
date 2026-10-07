import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, AlertTriangle, Rocket, Tags, CreditCard } from 'lucide-react';

import { platformService } from '../../services/platformService';
import { PLATFORM_NAME } from '../../config/platform';

/**
 * Platform settings — the things that belong to the platform rather than to
 * any customer.
 *
 * DELIBERATELY NOT A FORM. Almost everything here is an environment
 * variable, read at boot and gated by `manage.py check --deploy`: a console
 * field that appeared to change `DJANGO_DEBUG` would be a lie, because the
 * process has already started. So this page REPORTS the configuration and
 * links to the pages that can actually change something — plans and payment
 * instructions, which are database rows.
 *
 * It exists because the brief asks the sidebar to have a Settings entry and
 * because an operator does need one place that answers "how is this
 * deployment configured" without a shell.
 */
const PlatformSettings = () => {
  const [launch, setLaunch] = useState(null);

  useEffect(() => {
    let alive = true;
    platformService.launchReadiness()
      .then(({ data }) => { if (alive) setLaunch(data); })
      .catch(() => { if (alive) setLaunch({ checks: [] }); });
    return () => { alive = false; };
  }, []);

  const checks = launch?.checks || [];
  const failing = checks.filter((c) => !c.ready);

  return (
    <div className="pf-page">
      <header className="pf-page-head">
        <div>
          <h1 className="pf-page-title">Platform settings</h1>
          <p className="pf-page-sub">
            How this deployment of {PLATFORM_NAME} is configured, and what is
            still outstanding.
          </p>
        </div>
      </header>

      <div className="pf-split">
        <section className="pf-card">
          <h2 className="pf-card-title">
            {failing.length === 0
              ? <CheckCircle2 size={16} aria-hidden="true" className="pf-ok-ic" />
              : <AlertTriangle size={16} aria-hidden="true" className="pf-warn-ic" />}
            Configuration
          </h2>
          {launch === null ? (
            <p className="pf-muted">Reading the launch gate…</p>
          ) : (
            <>
              <p className="pf-card-lede pf-muted">
                {failing.length === 0
                  ? 'Every check the launch gate makes is satisfied.'
                  : `${failing.length} of ${checks.length} checks are not satisfied. `
                    + 'Each is an environment setting, applied when the process starts.'}
              </p>
              <ul className="pf-kv">
                {checks.map((check) => (
                  <li key={check.key}>
                    <span>{check.label}</span>
                    <b className={check.ready ? 'pf-ok-text' : 'pf-warn-text'}>
                      {check.ready ? 'OK' : check.severity}
                    </b>
                  </li>
                ))}
              </ul>
              <p className="pf-prof-note">
                Changing any of these means changing the environment and
                restarting — a field here could not do it, so there is not
                one. <code>manage.py check --deploy</code> is the gate.
              </p>
            </>
          )}
        </section>

        <section className="pf-card">
          <h2 className="pf-card-title">What you can change here</h2>
          <p className="pf-card-lede pf-muted">
            These are database rows, not configuration, so they take effect
            immediately.
          </p>
          <div className="pf-quick-grid">
            <Link className="pf-action" to="/platform/plans">
              <span className="pf-action-ic" aria-hidden="true"><Tags size={16} /></span>
              <span>
                <strong>Plans and prices</strong>
                <small>Prices are immutable rows; a change adds one</small>
              </span>
            </Link>
            <Link className="pf-action" to="/platform/payment-methods">
              <span className="pf-action-ic" aria-hidden="true"><CreditCard size={16} /></span>
              <span>
                <strong>Payment methods</strong>
                <small>Bank accounts, wallets and QR codes customers pay into</small>
              </span>
            </Link>
            <Link className="pf-action" to="/platform/launch">
              <span className="pf-action-ic" aria-hidden="true"><Rocket size={16} /></span>
              <span>
                <strong>Launch readiness</strong>
                <small>The full gate, check by check</small>
              </span>
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
};

export default PlatformSettings;
