import React, { useEffect, useState } from 'react';
import { platformService } from '../../services/platformService';

/**
 * Parts 5 and 6: every lifecycle and subscription action, as ACTIONS.
 *
 * There is no status dropdown here, and that is the design. Each action
 * carries the input that action requires — a suspension needs a reason, an
 * extension needs a number of months, assigning a plan needs a plan — and a
 * single "set status" control could not ask for any of them. It is also what
 * makes the audit trail afterwards read as a sequence of decisions rather than
 * a list of field edits.
 *
 * ASSIGN PLAN AND CHANGE PLAN ARE DIFFERENT BUTTONS, deliberately, because
 * they are different commercial acts:
 *
 *   Assign  — a sale. The plan changes AND a term is added, so the
 *             subscription comes out active with a real end date.
 *   Change  — a correction. The plan changes and the period is untouched,
 *             because re-dating it mid-term would either hand the customer
 *             free time or take paid time away.
 */
const PROMPTS = {
  suspend: {
    label: 'Suspend', tone: 'is-danger',
    title: 'Suspend this workspace',
    body: 'Nobody will be able to sign in. Nothing is deleted. This survives '
        + 'their next payment — an operator suspension is a decision about the '
        + 'customer, not their balance.',
    field: 'reason', fieldLabel: 'Reason (required)', required: true,
  },
  cancel: {
    label: 'Cancel', tone: 'is-danger',
    title: 'Cancel this customer',
    body: 'Ends the relationship. Still deletes nothing, and is not final: a '
        + 'customer who returns is reactivated and keeps their history.',
    field: 'reason', fieldLabel: 'Reason (required)', required: true,
  },
  activate: {
    label: 'Activate', tone: 'is-primary',
    title: 'Let this customer back in',
    body: 'Adds a term without taking a payment, and clears any operator '
        + 'suspension so billing drives the workspace again.',
    field: 'note', fieldLabel: 'Note (optional)',
  },
  extend: {
    label: 'Extend', tone: '',
    title: 'Extend the paid term',
    body: 'Adds months to the end of the current period — a credit, a goodwill '
        + 'gesture or a fix. Paying early never costs a customer days they hold.',
    field: 'months', fieldLabel: 'Months', number: true, required: true,
    defaultValue: 1,
  },
  trial: {
    label: 'Start trial', tone: '',
    title: 'Start or restart a trial',
    body: 'For a customer being re-evaluated, or an onboarding that needs '
        + 'another run. A trial is not counted as revenue.',
    field: 'days', fieldLabel: 'Trial days', number: true, defaultValue: 14,
  },
};

const LifecycleActions = ({ slug, organization, subscription, onChanged }) => {
  const [open, setOpen] = useState(null);
  const [value, setValue] = useState('');
  const [plans, setPlans] = useState([]);
  const [planCode, setPlanCode] = useState('');
  const [planMode, setPlanMode] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    platformService.plans()
      .then((response) => {
        const active = response.data.filter((plan) => plan.is_active);
        setPlans(active);
        setPlanCode(subscription?.plan_code || active[0]?.code || '');
      })
      .catch(() => setPlans([]));
  }, [subscription?.plan_code]);

  const start = (key) => {
    const prompt = PROMPTS[key];
    setValue(prompt.defaultValue !== undefined ? String(prompt.defaultValue) : '');
    setError(null);
    setOpen(key);
  };

  const run = async (call) => {
    setBusy(true);
    setError(null);
    try {
      await call();
      setOpen(null);
      setPlanMode(null);
      onChanged();
    } catch (err) {
      const data = err.response?.data;
      setError(data?.detail || data?.reason?.[0] || data?.months?.[0]
               || 'That action was refused.');
    } finally {
      setBusy(false);
    }
  };

  const confirm = () => {
    const calls = {
      suspend: () => platformService.suspend(slug, value),
      cancel: () => platformService.cancel(slug, value),
      activate: () => platformService.activate(slug, value),
      extend: () => platformService.extend(slug, Number(value)),
      trial: () => platformService.startTrial(slug, Number(value) || undefined),
    };
    return run(calls[open]);
  };

  const applyPlan = () => run(() => (planMode === 'assign'
    ? platformService.assignPlan(slug, planCode)
    : platformService.changePlan(slug, planCode)));

  const prompt = open ? PROMPTS[open] : null;
  const locked = organization.status === 'cancelled';

  return (
    <section className="pf-panel" aria-label="Lifecycle and subscription">
      <h2 className="pf-panel-title">Actions</h2>

      <div className="pf-actions">
        {organization.is_admitted ? (
          <button type="button" className="btn pf-btn-danger"
                  onClick={() => start('suspend')}>
            Suspend
          </button>
        ) : (
          <button type="button" className="btn btn-primary"
                  onClick={() => start('activate')}>
            Activate
          </button>
        )}
        <button type="button" className="btn btn-ghost" onClick={() => start('extend')}>
          Extend term
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => start('trial')}>
          Start trial
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setPlanMode('assign')}>
          Assign plan &amp; sell a term
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setPlanMode('change')}>
          Change plan only
        </button>
        {!locked && (
          <button type="button" className="btn pf-btn-danger"
                  onClick={() => start('cancel')}>
            Cancel customer
          </button>
        )}
      </div>

      {organization.status_override && (
        <p className="pf-note">
          This workspace status was set by an operator, so billing will not move
          it. Activating the customer clears that.
        </p>
      )}

      {prompt && (
        <div className="pf-modal" role="dialog" aria-modal="true"
             aria-label={prompt.title}>
          <div className="pf-modal-card">
            <h3 className="pf-modal-title">{prompt.title}</h3>
            <p className="pf-modal-lead">{prompt.body}</p>
            {error && <p className="pf-err" role="alert">{error}</p>}
            <label className="pf-field">
              <span>{prompt.fieldLabel}</span>
              {prompt.number ? (
                <input type="number" min="1" value={value}
                       onChange={(e) => setValue(e.target.value)} />
              ) : (
                <textarea rows={3} value={value}
                          onChange={(e) => setValue(e.target.value)} />
              )}
            </label>
            <div className="pf-modal-actions">
              <button type="button" className={`btn ${prompt.tone === "is-danger" ? "pf-btn-danger" : "btn-primary"}`}
                      disabled={busy || (prompt.required && !value.trim())}
                      onClick={confirm}>
                {busy ? 'Working…' : prompt.label}
              </button>
              <button type="button" className="btn btn-ghost" disabled={busy}
                      onClick={() => setOpen(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {planMode && (
        <div className="pf-modal" role="dialog" aria-modal="true"
             aria-label="Plan">
          <div className="pf-modal-card">
            <h3 className="pf-modal-title">
              {planMode === 'assign' ? 'Assign a plan and sell a term'
                                     : 'Change the plan only'}
            </h3>
            <p className="pf-modal-lead">
              {planMode === 'assign'
                ? 'The plan changes and a term of its length is added, so the '
                  + 'subscription becomes active with a real end date.'
                : 'The plan changes and the current period is left exactly as '
                  + 'it is. Use this to correct a mistake, not to sell.'}
            </p>
            {error && <p className="pf-err" role="alert">{error}</p>}
            <label className="pf-field">
              <span>Plan</span>
              <select value={planCode} onChange={(e) => setPlanCode(e.target.value)}>
                {plans.map((plan) => (
                  <option key={plan.code} value={plan.code}>
                    {plan.name} — {plan.interval_months} month(s)
                  </option>
                ))}
              </select>
            </label>
            <div className="pf-modal-actions">
              <button type="button" className="btn btn-primary" disabled={busy}
                      onClick={applyPlan}>
                {busy ? 'Working…' : 'Apply'}
              </button>
              <button type="button" className="btn btn-ghost" disabled={busy}
                      onClick={() => setPlanMode(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
};

export default LifecycleActions;
