import api from './api';

/**
 * Phase S8: the customer's own subscription, from the tenant side.
 *
 * SEPARATE FROM `platformService`, and the separation is the point. That
 * module is the console: an operator acting on a customer. This one is a
 * customer acting on themselves, and every call here is scoped server-side
 * to the organization the caller belongs to — there is no id to pass, which
 * makes asking about somebody else's subscription unexpressible rather than
 * merely refused.
 *
 * NO PRICES ARE DEFINED IN THIS FILE, or anywhere else in the front end.
 * `PlanPrice` rows are immutable and superseded by insertion, so "the price"
 * is a question about a date; a figure hard-coded here is one that goes stale
 * at the first change and is shown to the one audience that must never see a
 * stale price.
 */
export const subscriptionService = {
  /** Current plan, dates, days remaining, trial/grace, and an explanation. */
  current: () => api.get('/tenant/subscription/'),

  /** What they could move to, priced from the database. */
  plans: () => api.get('/tenant/plans/'),

  /** How to pay. Managed from the platform console, never hardcoded. */
  instructions: () => api.get('/tenant/payment-instructions/'),

  /** Their own payments, newest first. */
  payments: () => api.get('/tenant/payments/'),

  /**
   * Ask for a plan. ACTIVATES NOTHING: it opens a payment with a reference
   * and a server-resolved amount. No amount is sent, because the endpoint
   * does not accept one.
   */
  requestPlan: (planCode) =>
    api.post('/tenant/subscription/request/', { plan_code: planCode }),

  /** Attach the receipt. Multipart, because it carries a screenshot. */
  submitProof: (reference, { method, transactionId, paidAt, note, file }) => {
    const form = new FormData();
    form.append('method', method);
    if (transactionId) form.append('transaction_id', transactionId);
    if (paidAt) form.append('paid_at', paidAt);
    if (note) form.append('payer_note', note);
    if (file) form.append('proof', file);
    return api.post(`/tenant/payments/${reference}/proof/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};

/** Minor units to a readable amount. Never float arithmetic on money. */
export const money = (minor, currency = 'NPR') => {
  if (minor === null || minor === undefined) return '—';
  const whole = Math.trunc(Math.abs(minor) / 100);
  const part = String(Math.abs(minor) % 100).padStart(2, '0');
  const sign = minor < 0 ? '-' : '';
  const grouped = whole.toLocaleString('en-IN');
  return part === '00'
    ? `${currency} ${sign}${grouped}`
    : `${currency} ${sign}${grouped}.${part}`;
};

export default subscriptionService;
