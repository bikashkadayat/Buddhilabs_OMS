import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../../components/common/PageHeader';
import StatTile from '../../components/common/StatTile';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';

/**
 * Part 1: Platform Health — is the PLATFORM working, as opposed to any one
 * tenant. (A tenant's own readiness lives on its organization page, because
 * that is where the remedy button is.)
 *
 * IT DOES NOT POLL. The check walks every organization comparing its mirror
 * columns against its subscription, which is work proportional to the number
 * of customers, and none of these signals changes on a timescale a timer helps
 * with. Re-checking is a button, so the figures on screen are always the
 * result of a check an operator asked for and can point at.
 *
 * DRIFT IS REPORTED HERE, NOT FIXED HERE. The health endpoint is a GET and
 * stays side-effect free; correcting the mirror is a POST an operator makes
 * deliberately from this page, and it is audited.
 *
 * Each of the four signals is here because it is invisible until it has been
 * broken for a while: nothing escalates the payment queue, a denormalised
 * column with no reconciler is a bug with a delay, and a tenant stuck in
 * PROVISIONING is a customer already waiting on a half-built workspace.
 */
const PlatformHealth = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [checking, setChecking] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    platformService.health()
      .then((response) => {
        if (!alive) return;
        setData(response.data);
        setError(null);
      })
      .catch(() => { if (alive) setError('Platform health could not be read.'); })
      .finally(() => { if (alive) setChecking(false); });
    return () => { alive = false; };
  }, [reload]);

  const recheck = () => {
    setChecking(true);
    setReload((n) => n + 1);
  };

  const repair = () => {
    setRepairing(true);
    platformService.reconcileMirrors()
      .then(() => { setChecking(true); setReload((n) => n + 1); })
      .catch(() => setError('The mirrors could not be corrected.'))
      .finally(() => setRepairing(false));
  };

  if (error) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Platform administration" title="Platform health" />
        <EmptyState variant="error" title="Health check unavailable" body={error}
                    onAction={recheck} actionLabel="Try again" />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Platform administration" title="Platform health" />
        <Skeleton rows={3} height={64} label="Running the health check" />
      </div>
    );
  }

  const stuck = data.organizations_stuck_provisioning || [];
  // A map of {slug: what disagrees}, not a list -- see services.reconcile_mirrors.
  const drift = Object.entries(data.subscription_mirror_drift || {});
  const problems = data.problems || [];

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Platform health"
        description="The platform itself: its database, its cache, and the queues nothing escalates on its own."
        actions={(
          <>
            {/* LAUNCH READINESS LIVES HERE NOW (console redesign).
                It left the sidebar because it is a deploy-time pre-flight
                check consulted twice a year, and a permanent rail entry for
                that is how a rail stops being scannable. This is where it
                went: somebody asking "is the platform all right" is already
                on this page. The route is unchanged, and the dashboard's
                System health card still links to it too. */}
            <Link className="btn btn-secondary" to="/platform/launch">
              Launch readiness
            </Link>
            <button type="button" className="btn btn-secondary" onClick={recheck}
                    disabled={checking}>
              {checking ? 'Re-checking…' : 'Re-check'}
            </button>
          </>
        )}
      />

      {problems.length > 0 ? (
        <div className="pf-alert" role="status">
          <strong>Needs attention.</strong>
          <ul className="pf-alert-list">
            {problems.map((problem) => <li key={problem}>{problem}</li>)}
          </ul>
        </div>
      ) : (
        <p className="pf-ok" role="status">All checks passed.</p>
      )}

      <section className="pf-tiles" aria-label="Platform signals">
        <StatTile
          value={data.database === 'up' ? 'Up' : 'Down'}
          label="Database"
          tone={data.database === 'up' ? 'good' : 'bad'}
        />
        <StatTile
          value={data.cache === 'up' ? 'Up' : (data.cache === 'down' ? 'Down' : 'Degraded')}
          label="Cache"
          hint="The tenant resolver and every throttle run on it"
          tone={data.cache === 'up' ? 'good' : 'bad'}
        />
        <StatTile
          value={data.payments_pending_verification}
          label="Payments to verify"
          tone={data.payments_pending_verification ? 'warn' : ''}
          to="/platform/payments"
          cta="Open queue"
        />
        <StatTile
          value={stuck.length}
          label="Stuck provisioning"
          hint="Half-built workspaces nobody can use"
          tone={stuck.length ? 'warn' : ''}
        />
        <StatTile
          value={drift.length}
          label="Mirrors drifted"
          hint="Organization columns that disagree with the subscription"
          tone={drift.length ? 'warn' : ''}
        />
      </section>

      {stuck.length > 0 && (
        <section className="pf-panel" aria-label="Organizations stuck provisioning">
          <h2 className="pf-panel-title">Stuck provisioning</h2>
          <p className="pf-note">
            Provisioning did not finish for these. Open each one and re-run it
            from its page — repairing fills gaps and overwrites nothing.
          </p>
          <ul className="pf-list">
            {stuck.map((slug) => (
              <li key={slug}>
                <Link to={`/platform/organizations/${slug}`} className="pf-link">
                  {slug}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {drift.length > 0 && (
        <section className="pf-panel" aria-label="Subscription mirror drift">
          <h2 className="pf-panel-title">Mirrors drifted</h2>
          <p className="pf-note">
            The organization row disagrees with its own subscription. The
            request path reads the mirror, so until this is corrected these
            tenants may be admitted or locked out on a stale answer.
          </p>
          <ul className="pf-list">
            {drift.map(([slug, problem]) => (
              <li key={slug}>
                <Link to={`/platform/organizations/${slug}`} className="pf-link">
                  {slug}
                </Link>
                {' — '}
                <span className="pf-muted">{problem}</span>
              </li>
            ))}
          </ul>
          <button type="button" className="btn btn-secondary" onClick={repair}
                  disabled={repairing}>
            {repairing ? 'Correcting…' : `Correct ${drift.length} mirror(s)`}
          </button>
        </section>
      )}
    </div>
  );
};

export default PlatformHealth;
