import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../../components/common/PageHeader';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';

/**
 * Phase S6.75 Parts 1, 4 and 5: is this platform fit to sell workspaces?
 *
 * A DIFFERENT QUESTION FROM PLATFORM HEALTH, which is why it is a different
 * page. Health asks whether the platform is working. This asks whether a
 * customer arriving in the next five minutes would get a working workspace —
 * and a platform with a healthy database, a warm cache and no purchasable
 * plan is perfectly healthy and completely unfit to sell.
 *
 * THE VERDICT IS NOT THE SCORE, and both are shown because they answer
 * different things. "87%" tells an operator how much work is left; "Not
 * Launch Ready" tells them whether to open the doors. A platform missing one
 * critical dependency scores well and must not launch, so the verdict leads
 * and the score follows it.
 *
 * SEVERITY IS SHOWN ON EVERY ROW, including the ones that pass, because
 * "advisory" and "critical" failures look identical in a flat list and the
 * difference is the whole point of grading them.
 */
const TONE = { critical: 'is-bad', important: 'is-warn', advisory: 'is-mute' };

const LaunchReadiness = () => {
  const [report, setReport] = useState(null);
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);
  const [checking, setChecking] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([
      platformService.launchReadiness(),
      platformService.events(30),
    ])
      .then(([readiness, series]) => {
        if (!alive) return;
        setReport(readiness.data);
        setEvents(series.data);
        setError(null);
      })
      .catch(() => { if (alive) setError('The readiness report could not be read.'); })
      .finally(() => { if (alive) setChecking(false); });
    return () => { alive = false; };
  }, [reload]);

  const recheck = useCallback(() => {
    setChecking(true);
    setReload((n) => n + 1);
  }, []);

  if (error) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Platform administration" title="Launch readiness" />
        <EmptyState variant="error" title="Unavailable" body={error}
                    onAction={recheck} actionLabel="Try again" />
      </div>
    );
  }
  if (!report) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Platform administration" title="Launch readiness" />
        <Skeleton rows={6} height={40} label="Running the readiness checks" />
      </div>
    );
  }

  const { counts, mode } = report;

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Launch readiness"
        description="Whether a customer arriving now would get a working workspace — not whether the platform is up."
        actions={(
          <button type="button" className="btn btn-secondary" onClick={recheck}
                  disabled={checking}>
            {checking ? 'Re-checking…' : 'Re-check'}
          </button>
        )}
      />

      <section className={`lr-verdict ${report.ready ? 'is-ok' : 'is-bad'}`}
               aria-label="Verdict">
        <div>
          <strong className="lr-verdict-text">{report.verdict}</strong>
          <p className="lr-verdict-sub">
            {counts.ready} of {counts.total} checks ready
            {counts.critical_failing > 0 && (
              <> · <b>{counts.critical_failing} critical failing</b></>
            )}
            {counts.important_failing > 0 && (
              <> · {counts.important_failing} important</>
            )}
          </p>
        </div>
        <div className="lr-score" aria-label="Readiness score">
          <span>{report.score}%</span>
        </div>
      </section>

      <p className="pf-note">
        Mode: tenancy {mode.tenancy ? 'on' : 'off'}, public registration{' '}
        {mode.public_registration ? 'on' : 'off'}. Checks are graded against
        that posture — a single-tenant deployment needs no purchasable plan.
      </p>

      <section className="pf-panel" aria-label="Readiness checks">
        <ul className="lr-list">
          {report.checks.map((check) => (
            <li key={check.key}
                className={check.ready ? 'is-ready' : TONE[check.severity]}>
              <span className="lr-mark" aria-hidden="true">
                {check.ready ? '✓' : '✕'}
              </span>
              <div className="lr-body">
                <p className="lr-label">
                  {check.label}
                  <span className={`lr-sev ${TONE[check.severity]}`}>
                    {check.severity}
                  </span>
                </p>
                <p className="lr-detail">{check.detail}</p>
                {!check.ready && check.hint && (
                  <p className="lr-hint">{check.hint}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {events && (
        <section className="pf-panel" aria-label="Platform events">
          <h2 className="pf-panel-title">
            Last {events.window_days} days
          </h2>
          <p className="pf-note">
            Six of these come from the platform audit trail. The two login
            series come from a maintained counter: a sign-in is recorded in
            the tenant&apos;s own audit log, and under row-level security the
            console counts zero of those.
          </p>
          <ul className="pf-kv">
            {events.panels.map((panel) => (
              <li key={panel.key}>
                <span>
                  {panel.label}
                  <span className="lr-source">{panel.source}</span>
                </span>
                <b>{panel.total}</b>
              </li>
            ))}
          </ul>
          <ul className="pf-kv">
            <li>
              <span>Provisioning success rate</span>
              <b>
                {events.rates.provisioning_success === null
                  ? 'no attempts'
                  : `${events.rates.provisioning_success}%`}
              </b>
            </li>
            <li>
              <span>Sign-in success rate</span>
              <b>
                {events.rates.login_success === null
                  ? 'no attempts'
                  : `${events.rates.login_success}%`}
              </b>
            </li>
          </ul>
        </section>
      )}
    </div>
  );
};

export default LaunchReadiness;
