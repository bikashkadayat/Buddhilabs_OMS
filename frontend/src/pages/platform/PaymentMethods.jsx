import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Pencil, Power, Archive, RotateCcw, QrCode } from 'lucide-react';

import PageHeader from '../../components/common/PageHeader';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';
import { describeApiError } from '../../services/apiErrors';

/**
 * Where customers send money. Edited here, shown on every customer's
 * subscription page, hardcoded nowhere.
 *
 * These rows existed; only Django admin could edit them, which no operator
 * should need. The form asks for exactly what each type needs -- an account
 * number for a bank, an ID for a wallet, a QR image for Fonepay -- and the
 * server refuses a method a customer could not actually pay with.
 */
const TYPES = [
  { value: 'bank_transfer', label: 'Bank account', fields: ['bank_name', 'branch', 'account_name', 'account_number'] },
  { value: 'esewa', label: 'eSewa', fields: ['esewa_id', 'account_name'] },
  { value: 'khalti', label: 'Khalti', fields: ['khalti_id', 'account_name'] },
  { value: 'fonepay', label: 'Fonepay', fields: ['account_name'], qr: true },
  { value: 'qr', label: 'QR code', fields: ['account_name'], qr: true },
  { value: 'other', label: 'Other', fields: ['account_name', 'account_number'] },
];
const LABELS = {
  bank_name: 'Bank name', branch: 'Branch', account_name: 'Account name',
  account_number: 'Account number', esewa_id: 'eSewa ID', khalti_id: 'Khalti ID',
};
const typeOf = (value) => TYPES.find((t) => t.value === value) || TYPES[TYPES.length - 1];
const STATE_LABEL = { active: 'Shown to customers', disabled: 'Hidden', archived: 'Archived' };

const blank = { method: 'bank_transfer', label: '', bank_name: '', branch: '', account_name: '',
  account_number: '', esewa_id: '', khalti_id: '', instructions: '', sort_order: 100 };

const MethodForm = ({ initial, onSaved, onCancel }) => {
  const [form, setForm] = useState({ ...blank, ...initial });
  const [qr, setQr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const type = typeOf(form.method);

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    const fields = ['method', 'label', 'instructions', 'sort_order', ...type.fields];
    const body = new FormData();
    fields.forEach((f) => body.append(f, form[f] ?? ''));
    if (qr) body.append('qr_image', qr);
    try {
      const { data } = initial?.id
        ? await platformService.updatePaymentMethod(initial.id, body)
        : await platformService.createPaymentMethod(body);
      onSaved(data);
    } catch (e) {
      setError(describeApiError(e, 'The payment method couldn’t be saved.'));
      setBusy(false);
    }
  };

  const input = (key) => (
    <label className="pc-text" key={key}>
      <span>{LABELS[key]}</span>
      <input value={form[key] || ''} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
    </label>
  );

  return (
    <form className="pc-card pm-form" onSubmit={save} aria-label={initial?.id ? 'Edit payment method' : 'New payment method'}>
      <h2 className="pc-confirm-title">{initial?.id ? `Edit ${initial.label}` : 'Add a payment method'}</h2>
      <div className="pm-grid">
        <label className="pc-text">
          <span>Type</span>
          <select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
            {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <label className="pc-text">
          <span>Name customers see</span>
          <input required value={form.label} placeholder="e.g. NIC Asia Bank"
                 onChange={(e) => setForm({ ...form, label: e.target.value })} />
        </label>
        {type.fields.map(input)}
        {type.qr && (
          <label className="pc-text">
            <span>QR code image</span>
            <input type="file" accept="image/*" onChange={(e) => setQr(e.target.files?.[0] || null)} />
            {initial?.qr_image && !qr && <small className="pc-muted">A QR code is already uploaded.</small>}
          </label>
        )}
        <label className="pc-text">
          <span>Order on the page</span>
          <input type="number" min="0" max="1000" value={form.sort_order}
                 onChange={(e) => setForm({ ...form, sort_order: e.target.value })} />
        </label>
      </div>
      <label className="pc-text">
        <span>Instructions for customers</span>
        <textarea rows={3} value={form.instructions}
                  placeholder="e.g. Put your workspace address in the remarks so we can match your payment."
                  onChange={(e) => setForm({ ...form, instructions: e.target.value })} />
      </label>
      {error && <p className="pc-err" role="alert">{error}</p>}
      <div className="pc-actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
};

const PaymentMethods = () => {
  const [rows, setRows] = useState(null);
  const [archived, setArchived] = useState(false);
  const [editing, setEditing] = useState(null);       // null | {} (new) | row
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    platformService.paymentMethods(archived)
      .then(({ data }) => { if (alive) { setRows(data); setError(''); } })
      .catch((e) => { if (alive) setError(describeApiError(e, 'Payment methods couldn’t be loaded.')); });
    return () => { alive = false; };
  }, [archived, reload]);

  const setState = async (row, state) => {
    try {
      await platformService.setPaymentMethodState(row.id, state);
      setReload((n) => n + 1);
    } catch (e) {
      setError(describeApiError(e, 'That didn’t go through. Please try again.'));
    }
  };

  const active = (rows || []).filter((r) => r.state === 'active').length;

  return (
    <div className="page">
      <PageHeader
        breadcrumb={<Link to="/platform/payments">Payments</Link>}
        title="Payment methods"
        description="Where customers send money. What you set here is what every customer’s subscription page shows."
        actions={!editing && (
          <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>
            <Plus size={15} aria-hidden="true" /> Add method
          </button>
        )}
      />
      {rows && active === 0 && !archived && (
        <p className="pc-err" role="alert">
          No method is shown to customers. Until one is, the subscription page tells them what
          they owe and gives them no way to pay it.
        </p>
      )}
      {error && <p className="pc-err" role="alert">{error}</p>}
      {editing && (
        <MethodForm initial={editing.id ? editing : null}
                    onCancel={() => setEditing(null)}
                    onSaved={() => { setEditing(null); setReload((n) => n + 1); }} />
      )}
      <label className="pm-toggle">
        <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
        Show archived methods
      </label>
      {rows && rows.length === 0 && (
        <EmptyState variant="first" title="No payment methods yet"
                    body="Add the bank account or wallet customers should pay into."
                    onAction={() => setEditing({})} actionLabel="Add method" />
      )}
      <div className="pc-list">
        {(rows || []).map((row) => (
          <article key={row.id} className={`pc-card pm-row is-${row.state}`} aria-label={row.label}>
            <header className="pc-head">
              <div>
                <p className="pc-org">{row.label}</p>
                <p className="pc-muted">{typeOf(row.method).label}</p>
              </div>
              <span className={`pf-chip ${row.state === 'active' ? 'pf-chip-ok' : 'pf-chip-wait'}`}>
                {STATE_LABEL[row.state]}
              </span>
            </header>
            <div className="pc-grid">
              {typeOf(row.method).fields.map((f) => row[f] && (
                <div className="pc-field" key={f}><span>{LABELS[f]}</span><b>{row[f]}</b></div>
              ))}
              {row.qr_image && (
                <div className="pc-field"><span>QR code</span><b><QrCode size={14} aria-hidden="true" /> Uploaded</b></div>
              )}
            </div>
            {row.instructions && <p className="pc-note">{row.instructions}</p>}
            <div className="pc-actions">
              {row.state !== 'archived' && (
                <button type="button" className="btn btn-ghost" onClick={() => setEditing(row)}>
                  <Pencil size={14} aria-hidden="true" /> Edit
                </button>
              )}
              {row.state === 'active' && (
                <button type="button" className="btn btn-ghost" onClick={() => setState(row, 'disabled')}>
                  <Power size={14} aria-hidden="true" /> Hide from customers
                </button>
              )}
              {row.state === 'disabled' && (
                <button type="button" className="btn btn-ghost" onClick={() => setState(row, 'active')}>
                  <Power size={14} aria-hidden="true" /> Show to customers
                </button>
              )}
              {row.state !== 'archived' ? (
                <button type="button" className="btn btn-ghost" onClick={() => setState(row, 'archived')}>
                  <Archive size={14} aria-hidden="true" /> Archive
                </button>
              ) : (
                <button type="button" className="btn btn-ghost" onClick={() => setState(row, 'disabled')}>
                  <RotateCcw size={14} aria-hidden="true" /> Restore
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
};

export default PaymentMethods;
