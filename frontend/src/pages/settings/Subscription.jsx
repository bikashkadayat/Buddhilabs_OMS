import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../../components/common/PageHeader';
import DataTable from '../../components/common/DataTable';
import StatusBadge from '../../components/common/StatusBadge';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import { subscriptionService, money } from '../../services/subscriptionService';

/**
 * Phase S8: Settings → Subscription. The customer's own view of what they
 * are paying for, and the only page in this product that talks about money.
 *
 * THE EXPLANATION COMES FROM THE SERVER, not from a lookup table in here.
 * The same six states are described in the platform's support runbook and
 * shown on this page, and a front end that writes its own wording is how a
 * customer is told something different from what support reads back to them.
 *
 * NO PRICE IS WRITTEN IN THIS FILE. Every figure is rendered from the
 * amount the API returned, so a price change in the console is a price
 * change here with no deploy.
 *
 * NOTHING ON THIS PAGE ACTIVATES ANYTHING, and the copy says so twice. A
 * customer who believes "Request upgrade" upgraded them will not go on to
 * send the money, and will be surprised when their trial ends — so the
 * button is labelled as a request, and the receipt that follows says in
 * plain words that nothing is active yet.
 */
const METHOD_LABEL = {
  bank_transfer: 'Bank transfer',
  esewa: 'eSewa',
  khalti: 'Khalti',
  fonepay: 'Fonepay',
  qr: 'QR code',
  cheque: 'Cheque',
  cash: 'Cash',
  other: 'Other',
};

const Subscription = () => {
  const [state, setState] = useState(null);
  const [plans, setPlans] = useState([]);
  const [instructions, setInstructions] = useState([]);
  const [history, setHistory] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [proofFor, setProofFor] = useState(null);
  const [proof, setProof] = useState({
    method: 'bank_transfer', transactionId: '', paidAt: '', note: '',
    file: null,
  });
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([
      subscriptionService.current(),
      subscriptionService.plans(),
      subscriptionService.instructions(),
      subscriptionService.payments(),
    ])
      .then(([current, planList, methods, payments]) => {
        if (!alive) return;
        setState(current.data);
        setPlans(planList.data);
        setInstructions(methods.data);
        setHistory(payments.data);
        setError(null);
      })
      .catch((err) => {
        if (!alive) return;
        setError(err.response?.status === 403
          ? 'Only an administrator of this organization can manage its subscription.'
          : 'Your subscription could not be loaded.');
      });
    return () => { alive = false; };
  }, [reload]);

  const refresh = useCallback(() => setReload((n) => n + 1), []);

  const request = async (planCode) => {
    setBusy(true);
    setNotice(null);
    try {
      const { data } = await subscriptionService.requestPlan(planCode);
      setNotice(`${data.detail} Reference ${data.reference}, `
        + `${money(data.amount_minor, data.currency)}.`);
      setProofFor(data.reference);
      refresh();
    } catch (err) {
      setNotice(err.response?.data?.detail
        || 'That plan could not be requested.');
    } finally {
      setBusy(false);
    }
  };

  const submitProof = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await subscriptionService.submitProof(proofFor, proof);
      setNotice('Thank you — we have your receipt and will review it shortly.');
      setProofFor(null);
      setProof({ method: 'bank_transfer', transactionId: '', paidAt: '',
                 note: '', file: null });
      refresh();
    } catch (err) {
      const body = err.response?.data || {};
      setNotice(body.detail || body.paid_at?.[0] || body.method?.[0]
        || 'The receipt could not be submitted.');
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Settings" title="Subscription" />
        <EmptyState variant="denied" title="Not available" body={error} />
      </div>
    );
  }
  if (!state) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Settings" title="Subscription" />
        <Skeleton rows={4} height={56} label="Loading your subscription" />
      </div>
    );
  }
  if (!state.available) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Settings" title="Subscription" />
        <EmptyState variant="error" title="Unavailable" body={state.detail} />
      </div>
    );
  }

  const open = history.find((row) => row.can_submit_proof);

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Settings"
        title="Subscription"
        description={`${state.organization.name} — plan, renewal and payments.`}
      />

      {notice && <p className="sb-notice" role="status">{notice}</p>}

      {/* Part 1 */}
      <section className="pf-panel" aria-label="Current plan">
        <h2 className="pf-panel-title">
          Current plan
          <StatusBadge status={state.status} />
        </h2>
        <p className="sb-explain">{state.explanation}</p>
        <ul className="pf-kv">
          <li><span>Plan</span><b>{state.plan.name || '—'}</b></li>
          <li>
            <span>Price</span>
            <b>{money(state.plan.amount_minor, state.plan.currency)}
              {state.plan.interval_months
                ? ` / ${state.plan.interval_months} month${state.plan.interval_months === 1 ? '' : 's'}`
                : ''}
            </b>
          </li>
          <li><span>Started</span><b>{state.starts_on || '—'}</b></li>
          <li><span>Expires</span><b>{state.expires_on || '—'}</b></li>
          <li>
            <span>Days remaining</span>
            <b>{state.days_remaining ?? '—'}</b>
          </li>
          {state.trial.is_trial && (
            <li><span>Trial ends</span><b>{state.trial.ends_on || '—'}</b></li>
          )}
          {state.grace.in_grace && (
            <li>
              <span>Grace period until</span>
              <b>{state.grace.until || '—'}</b>
            </li>
          )}
          <li>
            <span>People using it</span>
            <b>
              {state.organization.seats_used}
              {state.plan.included_seats != null
                && ` of ${state.plan.included_seats}`}
            </b>
          </li>
        </ul>
      </section>

      {/* Part 2 */}
      <section className="pf-panel" aria-label="Plans">
        <h2 className="pf-panel-title">Plans</h2>
        <p className="pf-note">
          Choosing a plan creates a payment request. <strong>Nothing changes
          until we confirm your payment</strong> — you will not lose access
          while that happens.
        </p>
        <div className="sb-plans">
          {plans.map((plan) => (
            <article key={plan.code}
                     className={`sb-plan${plan.is_current ? ' is-current' : ''}`}>
              <h3>{plan.name}</h3>
              <p className="sb-price">{money(plan.amount_minor, plan.currency)}</p>
              <p className="sb-per">
                every {plan.interval_months} month
                {plan.interval_months === 1 ? '' : 's'}
              </p>
              <p className="sb-equiv">
                {money(plan.monthly_equivalent_minor, plan.currency)} a month
              </p>
              <ul className="sb-features">
                <li>
                  {plan.included_seats == null
                    ? 'Unlimited people' : `${plan.included_seats} people`}
                </li>
                {plan.description && <li>{plan.description}</li>}
              </ul>
              {plan.is_current ? (
                <p className="sb-current-tag">Your current plan</p>
              ) : (
                <button type="button" className="btn btn-primary"
                        disabled={busy} onClick={() => request(plan.code)}>
                  {plan.is_upgrade ? 'Request upgrade' : 'Request this plan'}
                </button>
              )}
            </article>
          ))}
        </div>
        {state.renewal_due && !open && (
          <p className="sb-notice" role="status">
            Your renewal is due. Request your current plan again to renew it.
          </p>
        )}
      </section>

      {/* Part 3 */}
      <section className="pf-panel" aria-label="How to pay">
        <h2 className="pf-panel-title">How to pay</h2>
        {instructions.length === 0 ? (
          <p className="pf-err">
            No payment methods are published yet. Please contact support and
            we will arrange it — do not send money until then.
          </p>
        ) : (
          <div className="sb-methods">
            {instructions.map((row) => (
              <article key={row.method} className="sb-method">
                <h3>{row.label || METHOD_LABEL[row.method] || row.method}</h3>
                <ul className="pf-kv">
                  {row.account_name && (
                    <li><span>Account name</span><b>{row.account_name}</b></li>
                  )}
                  {row.bank_name && (
                    <li><span>Bank</span><b>{row.bank_name}</b></li>
                  )}
                  {row.branch && (
                    <li><span>Branch</span><b>{row.branch}</b></li>
                  )}
                  {row.account_number && (
                    <li>
                      <span>Account number</span>
                      <b className="pf-code">{row.account_number}</b>
                    </li>
                  )}
                  {row.esewa_id && (
                    <li>
                      <span>eSewa id</span>
                      <b className="pf-code">{row.esewa_id}</b>
                    </li>
                  )}
                  {row.khalti_id && (
                    <li>
                      <span>Khalti ID</span>
                      <b className="pf-code">{row.khalti_id}</b>
                    </li>
                  )}
                </ul>
                {row.qr_image && (
                  <img src={row.qr_image} alt={`${row.label} QR code`}
                       className="sb-qr" width="160" height="160" />
                )}
                {row.instructions_html && (
                  /* Platform-authored, from the console — not tenant input,
                     so there is no tenant-supplied markup here to sanitise. */
                  <div className="sb-method-note"
                       dangerouslySetInnerHTML={{
                         __html: row.instructions_html,
                       }} />
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      {/* Part 4 */}
      {(proofFor || open) && (
        <section className="pf-panel" aria-label="Send your receipt">
          <h2 className="pf-panel-title">Send your receipt</h2>
          {/* Part 13: when a payment came back -- rejected, or with a
              question -- the reason and the next step come first, in the
              reviewer's own words, above the form that fixes it. */}
          {open && (open.status === 'rejected' || open.status === 'needs_info') && (
            <div className={`sb-returned is-${open.status}`} role="alert">
              <strong>
                {open.status === 'rejected'
                  ? `We couldn’t confirm payment ${open.reference}.`
                  : `We need a little more about payment ${open.reference}.`}
              </strong>
              <p>{open.explanation}</p>
              {open.next_step && <p className="sb-next">Next: {open.next_step}</p>}
            </div>
          )}
          <p className="pf-note">
            Quote reference <b className="pf-code">{proofFor || open.reference}</b>
            {' '}when you pay, then tell us about it here.
          </p>
          <form className="sb-proof" onSubmit={submitProof}>
            <div className="auth-field">
              <label htmlFor="proof-method">How did you pay?</label>
              <select id="proof-method" value={proof.method}
                      onChange={(e) => setProof({ ...proof, method: e.target.value })}>
                {Object.entries(METHOD_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <div className="auth-field">
              <label htmlFor="proof-txn">Transaction id or reference</label>
              <input id="proof-txn" value={proof.transactionId}
                     onChange={(e) => setProof({ ...proof, transactionId: e.target.value })} />
            </div>
            <div className="auth-field">
              <label htmlFor="proof-date">Date you paid</label>
              <input id="proof-date" type="date" value={proof.paidAt}
                     onChange={(e) => setProof({ ...proof, paidAt: e.target.value })} />
            </div>
            <div className="auth-field">
              <label htmlFor="proof-file">Screenshot or receipt</label>
              <input id="proof-file" type="file" accept="image/*,.pdf"
                     onChange={(e) => setProof({ ...proof, file: e.target.files[0] })} />
            </div>
            <div className="auth-field sb-wide">
              <label htmlFor="proof-note">Anything we should know</label>
              <textarea id="proof-note" rows={2} value={proof.note}
                        onChange={(e) => setProof({ ...proof, note: e.target.value })} />
            </div>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? 'Sending…' : 'Send receipt'}
            </button>
          </form>
        </section>
      )}

      {/* Part 7 */}
      <section className="pf-panel" aria-label="Payment history">
        <h2 className="pf-panel-title">Your payments</h2>
        <DataTable
          columns={[
            { key: 'reference', header: 'Reference',
              render: (row) => <span className="pf-code">{row.reference}</span> },
            { key: 'plan', header: 'Plan' },
            { key: 'amount_minor', header: 'Amount', align: 'right',
              render: (row) => money(row.amount_minor, row.currency) },
            { key: 'status', header: 'Status',
              render: (row) => <StatusBadge status={row.status} /> },
            { key: 'explanation', header: 'What this means' },
            { key: 'paid_at', header: 'Paid on',
              render: (row) => row.paid_at || '—' },
          ]}
          rows={history}
          rowKey={(row) => row.reference}
          caption="Your payment history"
          empty={{ variant: 'first', title: 'No payments yet',
                   body: 'Your trial needed none. Choose a plan above when '
                         + 'you are ready.' }}
        />
      </section>
    </div>
  );
};

export default Subscription;
