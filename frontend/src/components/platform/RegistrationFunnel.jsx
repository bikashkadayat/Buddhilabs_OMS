import React, { useEffect, useState } from 'react';
import { platformService } from '../../services/platformService';

/**
 * Phase S7 Part 11: the registration funnel.
 *
 * THE RATES ARE THE POINT, NOT THE TOTALS. "41 registrations" is a number
 * nobody can act on. "Of 41, 12 never opened the email" names a
 * deliverability problem; "of 29 verified, 3 have no workspace" names a
 * platform fault — and those go to different people, which is why drop-off
 * and failure are shown separately rather than added together into one
 * "incomplete" figure.
 *
 * `verified_without_workspace` is the only number on this panel that should
 * ever wake somebody up: every one of those is a customer who was told their
 * email was confirmed and has nothing to sign in to. So it is styled as an
 * alert when non-zero and plain when it is not, rather than sitting in a
 * list of neutral statistics.
 */
// `embedded`: drawn inside a card that already carries the title (the
// dashboard), so it renders its content alone. Standalone it brings its own
// panel and heading. Without this the dashboard showed "Registration funnel"
// twice, a panel inside a card.
const Frame = ({ embedded, days, children }) => (embedded ? (
  <div aria-label="Registration funnel">{children}</div>
) : (
  <section className="pf-panel" aria-label="Registration funnel">
    <h2 className="pf-panel-title">
      Registration funnel
      <span className="lr-source">last {days} days</span>
    </h2>
    {children}
  </section>
));

const RegistrationFunnel = ({ days = 30, embedded = false }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    platformService.registrationFunnel(days)
      .then((response) => { if (alive) setData(response.data); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [days]);

  // A payload without stages -- an older server, a proxy's error page parsed
  // as JSON -- renders nothing rather than taking the dashboard down with it.
  if (error || !data || !Array.isArray(data.stages) || !data.stages.length) return null;
  if (data.stages[0].count === 0) {
    return (
      <Frame embedded={embedded} days={data.window_days}>
        <p className="pf-note">
          No self-service registrations in the last {data.window_days} days.
        </p>
      </Frame>
    );
  }

  const widest = data.stages[0].count || 1;
  const stalled = data.failures.verified_without_workspace;

  return (
    <Frame embedded={embedded} days={data.window_days}>

      <ol className="rf-stages">
        {data.stages.map((stage) => (
          <li key={stage.key}>
            <div className="rf-head">
              <span>{stage.label}</span>
              <b>
                {stage.count}
                {stage.of_previous !== null && (
                  <span className="rf-rate">{stage.of_previous}%</span>
                )}
              </b>
            </div>
            <div className="rf-bar">
              <span style={{ width: `${Math.round(100 * stage.count / widest)}%` }} />
            </div>
          </li>
        ))}
      </ol>

      <ul className="pf-kv">
        <li>
          <span>Awaiting verification</span>
          <b>{data.drop_off.awaiting_verification}</b>
        </li>
        <li>
          <span>Expired unverified</span>
          <b>{data.drop_off.expired_unverified}</b>
        </li>
        <li>
          <span>Verification emails re-sent</span>
          <b>{data.drop_off.verification_resends}</b>
        </li>
        <li>
          <span>Trial activations</span>
          <b>{data.trial_activations}</b>
        </li>
      </ul>

      {stalled > 0 ? (
        <p className="pf-err" role="alert">
          <strong>{stalled}</strong> registration(s) verified without a
          workspace. Each one is a customer who was told their email was
          confirmed and has nothing to sign in to — check the launch
          readiness report and the application log.
        </p>
      ) : (
        <p className="pf-ok">
          Every verified registration became a workspace.
        </p>
      )}
    </Frame>
  );
};

export default RegistrationFunnel;
