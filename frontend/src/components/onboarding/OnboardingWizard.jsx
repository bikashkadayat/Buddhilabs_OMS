import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../../services/api';

/**
 * Phase S7 Parts 6, 7 and 8: what a new administrator sees on their first day.
 *
 * TWO LISTS, AND THEY ARE NOT THE SAME LIST. "Ready to use" is what
 * provisioning already did — the reassurance that this is not an empty
 * database, which is the entire point of the bootstrap built in S6. "Next
 * steps" is what the customer still has to do. Merging them would produce one
 * list of thirteen items in which the eight finished ones read as work.
 *
 * IT DISAPPEARS ON ITS OWN. Once every step is done the server stops asking
 * for it to be shown, so nobody has to dismiss a list of ticks — and it is
 * dismissible before then, because somebody who wants to get on with their
 * job should not have to finish a checklist first.
 *
 * NOTHING HERE IS A PROGRESS BAR OVER NOTHING. Each measurable step is
 * computed from the tenant's own rows on every read, so uploading a logo ticks
 * that step and deleting it unticks it. A checklist stored as five booleans
 * starts lying the first time somebody undoes something, to the person least
 * able to tell.
 */
const OnboardingWizard = () => {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get('/tenant/onboarding/')
      .then(({ data }) => setState(data))
      .catch(() => setState({ applicable: false }));
  }, []);

  useEffect(load, [load]);

  const act = async (body) => {
    setBusy(true);
    try {
      const { data } = await api.post('/tenant/onboarding/', body);
      setState(data);
    } catch {
      /* leave the panel as it is; nothing here is worth an error banner */
    } finally {
      setBusy(false);
    }
  };

  if (!state?.applicable || !state.show_wizard) return null;

  const { organization, subscription, progress } = state;

  return (
    <section className="ob-card" aria-label="Getting started" data-tour="setup">
      <header className="ob-head">
        <div>
          <h2 className="ob-title">Welcome to your {organization.name} workspace</h2>
          <p className="ob-sub">
            {/* The full address, which they can bookmark and send to their
                team. The bare slug told them nothing they could use. */}
            Everything is set up and ready.{' '}
            {organization.login_url ? (
              <>Your team signs in at <strong>{organization.login_url.replace(/\/$/, '')}</strong></>
            ) : (
              <>Your workspace address is <code>{organization.slug}</code></>
            )}
            {subscription.is_trial && subscription.days_remaining != null && (
              <> · <strong>{subscription.days_remaining} days</strong> left of
                your trial</>
            )}
            {!subscription.is_trial && (
              <> · {organization.status_display}</>
            )}
          </p>
        </div>
        <span className="ob-head-acts">
          <Link to="/getting-started" className="ob-all">See every step</Link>
          <button type="button" className="ob-dismiss" disabled={busy}
                  onClick={() => act({ dismissed: true })}>
            Dismiss
          </button>
        </span>
      </header>

      {/* Part 8: the verdict, in the console's own words. */}
      {/* The console's verdict, in the customer's words. "Tenant Ready" is
          what an operator reads; a school's principal is not a tenant. */}
      {state.health.verdict === 'Tenant Ready' ? (
        <p className="ob-verdict is-ok">Everything is configured</p>
      ) : (
        <p className="ob-verdict is-bad" role="alert">
          Some setup is still missing:{' '}
          {Object.keys(state.health.configuration_gaps)
            .map((key) => key.replace(/_/g, ' ')).join(', ')}.
          {' '}You can carry on in the meantime; let support know if it does not clear.
        </p>
      )}

      <div className="ob-cols">
        {/* Part 6 */}
        <div>
          <h3 className="ob-h3">Ready to use</h3>
          <ul className="ob-ready">
            {state.ready.map((item) => (
              <li key={item.key} className={item.ready ? 'is-ok' : 'is-bad'}>
                <span aria-hidden="true">{item.ready ? '✅' : '⚠️'}</span>
                {item.label}
              </li>
            ))}
          </ul>
        </div>

        {/* Part 7 */}
        <div>
          <h3 className="ob-h3">
            Next steps
            <span className="ob-progress">
              {progress.completed} of {progress.total}
            </span>
          </h3>
          <div className="ob-bar" role="progressbar"
               aria-valuenow={progress.percent} aria-valuemin={0}
               aria-valuemax={100}
               aria-label="Onboarding progress">
            <span style={{ width: `${progress.percent}%` }} />
          </div>
          <ol className="ob-steps">
            {state.checklist.map((step) => (
              <li key={step.key} className={step.done ? 'is-done' : ''}>
                <div className="ob-step-main">
                  <span className="ob-step-mark" aria-hidden="true">
                    {step.done ? '✓' : ''}
                  </span>
                  <div>
                    <Link to={step.action} className="ob-step-title">
                      {step.title}
                    </Link>
                    <p className="ob-step-detail">{step.detail}</p>
                  </div>
                </div>
                {/* Every step here is measured, so the tick is an
                    override for a customer who does not need that one --
                    they run a single shift and will never look at
                    attendance rules. It can only add a tick, never remove
                    one, so it cannot contradict the workspace. */}
                {!step.done && (
                  <button type="button" className="ob-tick" disabled={busy}
                          onClick={() => act({ step_done: step.key })}>
                    Mark done
                  </button>
                )}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
};

export default OnboardingWizard;
