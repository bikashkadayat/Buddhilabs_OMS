import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  CheckCircle2, XCircle, MessageCircleQuestion, FileText, Settings2,
} from 'lucide-react';

import PageHeader from '../../components/common/PageHeader';
import EmptyState from '../../components/common/EmptyState';
import StatusBadge from '../../components/common/StatusBadge';
import { platformService, money } from '../../services/platformService';
import { describeApiError } from '../../services/apiErrors';

/**
 * The Payment Center: decide customers' payments, here, in the console.
 *
 * WHAT IT REPLACES. The queue was read-only. Approving a payment took an
 * engineer, a Django shell and a support-runbook procedure, so every paid
 * conversion waited on server access. Now one operator reads the receipt and
 * presses one button; the subscription activates or extends, the customer is
 * emailed, and the decision lands on the audit trail -- the same service
 * functions the shell called, with the steps a shell never took.
 *
 * Three tabs, because the three states want different attention: payments
 * waiting on us, payments waiting on the customer, and what was decided.
 */
const TABS = [
  { key: 'review', label: 'To review' },
  { key: 'waiting', label: 'Waiting on customer' },
  { key: 'decided', label: 'Decided' },
];

const when = (iso) => (iso
  ? new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
  })
  : '—');

const Field = ({ label, children }) => (
  <div className="pc-field">
    <span>{label}</span>
    <b>{children || '—'}</b>
  </div>
);

const Receipt = ({ payment }) => {
  const [state, setState] = useState({ busy: false, url: null, type: '', error: '' });
  useEffect(() => () => { if (state.url) URL.revokeObjectURL(state.url); }, [state.url]);

  const open = async () => {
    setState({ busy: true, url: null, type: '', error: '' });
    try {
      const { data } = await platformService.paymentProof(payment.id);
      setState({ busy: false, url: URL.createObjectURL(data), type: data.type || '', error: '' });
    } catch (error) {
      setState({ busy: false, url: null, type: '', error: describeApiError(error, 'The receipt couldn’t be opened.') });
    }
  };

  if (!payment.has_proof) return <p className="pc-muted">No receipt attached.</p>;
  if (state.url && state.type.startsWith('image/')) {
    return <img className="pc-proof" src={state.url} alt={`Receipt for ${payment.payment_reference}`} />;
  }
  return (
    <div className="pc-proof-row">
      {state.url ? (
        <a className="btn btn-ghost" href={state.url} target="_blank" rel="noreferrer">
          <FileText size={15} aria-hidden="true" /> Open the receipt (PDF)
        </a>
      ) : (
        <button type="button" className="btn btn-ghost" onClick={open} disabled={state.busy}>
          <FileText size={15} aria-hidden="true" /> {state.busy ? 'Opening…' : 'View receipt'}
        </button>
      )}
      {state.error && <span className="pc-err" role="alert">{state.error}</span>}
    </div>
  );
};

const Decision = ({ payment, onDone }) => {
  const [mode, setMode] = useState(null);       // null | approve | reject | info
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const go = async () => {
    setBusy(true);
    setError('');
    try {
      if (mode === 'approve') {
        const { data } = await platformService.approvePayment(payment.id, text);
        onDone(`Approved. ${payment.organization_name} is active until ${data.subscription?.current_period_end}. They’ve been emailed.`);
      } else if (mode === 'reject') {
        await platformService.rejectPayment(payment.id, text);
        onDone(`Rejected. ${payment.organization_name} has been emailed the reason.`);
      } else {
        await platformService.requestPaymentInfo(payment.id, text);
        onDone(`Asked ${payment.organization_name} for more information.`);
      }
    } catch (e) {
      setError(describeApiError(e, 'That didn’t go through. Please try again.'));
      setBusy(false);
    }
  };

  if (!mode) {
    return (
      <div className="pc-actions">
        <button type="button" className="btn btn-primary" onClick={() => setMode('approve')}>
          <CheckCircle2 size={15} aria-hidden="true" /> Approve
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setMode('info')}>
          <MessageCircleQuestion size={15} aria-hidden="true" /> Ask for more
        </button>
        <button type="button" className="btn btn-ghost pc-danger" onClick={() => setMode('reject')}>
          <XCircle size={15} aria-hidden="true" /> Reject
        </button>
      </div>
    );
  }

  const copy = {
    approve: {
      title: `Approve ${money(payment.amount_minor, payment.currency)} for the ${payment.plan_name} plan?`,
      hint: 'The subscription activates or extends now, and the customer is emailed. Add a note for the record if you like.',
      label: 'Note (optional)', button: 'Approve payment', required: false,
    },
    reject: {
      title: 'Reject this payment',
      hint: 'The customer reads this, word for word, with steps to fix it and resubmit.',
      label: 'Reason', button: 'Reject payment', required: true,
    },
    info: {
      title: 'Ask the customer for more',
      hint: 'Nothing is rejected. They see your message and upload the receipt again.',
      label: 'What do you need?', button: 'Send request', required: true,
    },
  }[mode];

  return (
    <div className={`pc-confirm is-${mode}`} role="group" aria-label={copy.title}>
      <p className="pc-confirm-title">{copy.title}</p>
      <p className="pc-muted">{copy.hint}</p>
      <label className="pc-text">
        <span>{copy.label}</span>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      {error && <p className="pc-err" role="alert">{error}</p>}
      <div className="pc-actions">
        <button type="button" className={`btn ${mode === 'reject' ? 'btn-danger' : 'btn-primary'}`}
                onClick={go} disabled={busy || (copy.required && !text.trim())}>
          {busy ? 'Saving…' : copy.button}
        </button>
        <button type="button" className="btn btn-ghost" disabled={busy}
                onClick={() => { setMode(null); setText(''); setError(''); }}>
          Cancel
        </button>
      </div>
    </div>
  );
};

const PaymentCard = ({ payment, decided, onDone }) => (
  <article className="pc-card" aria-label={`Payment ${payment.payment_reference}`}>
    <header className="pc-head">
      <div>
        <Link className="pc-org" to={`/platform/organizations/${payment.organization_slug}`}>
          {payment.organization_name}
        </Link>
        <p className="pc-muted">{payment.plan_name} plan · {payment.payment_reference}</p>
      </div>
      <div className="pc-amount">
        <b>{money(payment.amount_minor, payment.currency)}</b>
        <StatusBadge status={payment.status} label={payment.status_display} />
      </div>
    </header>
    <div className="pc-grid">
      <Field label="Method">{payment.method_display}</Field>
      <Field label="Their transaction ID">{payment.transaction_id}</Field>
      <Field label="Paid on">{payment.paid_at}</Field>
      <Field label="Submitted">{when(payment.submitted_at)}</Field>
      <Field label="Requested by">{payment.submitted_by}</Field>
      {payment.reviewer_email && <Field label="Being reviewed by">{payment.reviewer_email}</Field>}
    </div>
    {payment.payer_note && <p className="pc-note">“{payment.payer_note}”</p>}
    {payment.status === 'needs_info' && (
      <p className="pc-note is-asked">You asked: {payment.review_message}</p>
    )}
    {payment.status === 'rejected' && (
      <p className="pc-note is-rejected">Rejected: {payment.rejection_reason}</p>
    )}
    <Receipt payment={payment} />
    {!decided && payment.status !== 'needs_info' && <Decision payment={payment} onDone={onDone} />}
  </article>
);

const PlatformPayments = () => {
  const [tab, setTab] = useState('review');
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    platformService.payments(tab === 'decided' ? 'decided' : undefined)
      .then(({ data }) => { if (alive) { setRows(data); setError(null); } })
      .catch(() => { if (alive) setError('The payments couldn’t be loaded.'); });
    return () => { alive = false; };
  }, [tab, reload]);

  const shown = (rows || []).filter((p) => (tab === 'decided' ? true
    : tab === 'waiting' ? p.status === 'needs_info'
      : p.status === 'submitted' || p.status === 'under_review'));

  const done = (message) => {
    setNotice(message);
    setReload((n) => n + 1);
  };

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Payments"
        description="Read the receipt, decide, and the customer’s subscription follows."
        actions={(
          <Link className="btn btn-ghost" to="/platform/payment-methods">
            <Settings2 size={15} aria-hidden="true" /> Payment methods
          </Link>
        )}
      />
      <div className="pf-tabs" role="tablist" aria-label="Payments">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
                  className={`pf-tab${tab === t.key ? ' is-active' : ''}`}
                  onClick={() => { setTab(t.key); setRows(null); }}>
            {t.label}
          </button>
        ))}
      </div>
      {notice && <p className="pf-allclear" role="status"><CheckCircle2 size={16} aria-hidden="true" /> {notice}</p>}
      {error && <EmptyState variant="error" title="Couldn’t load payments" body={error}
                            onAction={() => setReload((n) => n + 1)} actionLabel="Try again" />}
      {!error && rows === null && <p className="pc-muted">Loading…</p>}
      {!error && rows !== null && shown.length === 0 && (
        <EmptyState variant="cleared"
                    title={tab === 'review' ? 'Nothing to review' : tab === 'waiting' ? 'Nobody to chase' : 'No decisions yet'}
                    body={tab === 'review'
                      ? 'When a customer sends a receipt, it appears here.'
                      : tab === 'waiting' ? 'No payment is waiting on a customer’s answer.'
                        : 'Approved and rejected payments appear here.'} />
      )}
      <div className="pc-list">
        {shown.map((p) => (
          <PaymentCard key={p.id} payment={p} decided={tab === 'decided'} onDone={done} />
        ))}
      </div>
    </div>
  );
};

export default PlatformPayments;
