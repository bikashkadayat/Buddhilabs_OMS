import api from './api';

/**
 * The Platform Admin Console API (Phase S6).
 *
 * SEPARATE FROM EVERY OTHER SERVICE, deliberately. Nothing here is reachable
 * by a tenant user: each endpoint is guarded server-side by IsPlatformStaff
 * AND by console.require_platform, and once TENANCY_ENABLED is on, served
 * only on a platform host. This module is the client half of that boundary,
 * and keeping it in one file means the boundary is greppable.
 *
 * STATUS IS NEVER A FIELD. There is no `update({status})` here, because there
 * is no such endpoint: every lifecycle move is its own verb, and each carries
 * the input that move requires — a suspension needs a reason, an extension
 * needs months. That is what makes the audit trail readable afterwards.
 */
const ORG = (slug) => `/platform/organizations/${encodeURIComponent(slug)}`;

export const platformService = {
  // --- Part 1: the dashboard ---
  dashboard: () => api.get('/platform/dashboard/'),
  health: () => api.get('/platform/health/'),
  plans: () => api.get('/platform/plans/'),

  // --- Part 2: organizations ---
  organizations: (params = {}) =>
    api.get('/platform/organizations/', { params }),
  organization: (slug) => api.get(`${ORG(slug)}/`),
  // One request provisions a complete, usable tenant: organization, settings,
  // branding, subscription, bootstrap configuration and (optionally) the first
  // administrator. The response carries the provisioning receipt.
  /**
   * Provision a tenant. One request, branding included.
   *
   * MULTIPART ONLY WHEN THERE ARE FILES. The endpoint accepts both, and a
   * JSON body is easier to read in a network panel and in a test -- so the
   * form upgrades itself to multipart only when a logo or favicon is
   * actually attached. Every existing caller that sends JSON keeps working.
   */
  provision: (body) => {
    const files = ['logo', 'favicon'].filter((k) => body[k]);
    if (!files.length) {
      return api.post('/platform/organizations/', body);
    }
    const form = new FormData();
    Object.entries(body).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        form.append(key, value);
      }
    });
    return api.post('/platform/organizations/', form,
      { headers: { 'Content-Type': 'multipart/form-data' } });
  },
  update: (slug, body) => api.patch(`${ORG(slug)}/`, body),
  usage: (slug) => api.get(`${ORG(slug)}/usage/`),
  // Client handover: the customer's way in, and emailing it to them. The
  // password is never part of `access`; `sendAccess` takes it only when the
  // creation dialog still holds it, and otherwise issues a new one.
  access: (slug) => api.get(`${ORG(slug)}/access/`),
  sendAccess: (slug, password) =>
    api.post(`${ORG(slug)}/access/send/`, password ? { password } : {}),
  tenantHealth: (slug) => api.get(`${ORG(slug)}/health/`),
  repair: (slug) => api.post(`${ORG(slug)}/repair/`, {}),

  // --- Part 6: lifecycle ---
  suspend: (slug, reason) => api.post(`${ORG(slug)}/suspend/`, { reason }),
  activate: (slug, note = '') => api.post(`${ORG(slug)}/activate/`, { note }),
  cancel: (slug, reason) => api.post(`${ORG(slug)}/cancel/`, { reason }),
  setStatus: (slug, status, note = '') =>
    api.post(`${ORG(slug)}/status/`, { status, note }),

  // --- Part 5: subscriptions ---
  assignPlan: (slug, planCode, months, note = '') =>
    api.post(`${ORG(slug)}/assign-plan/`, { plan_code: planCode, months, note }),
  changePlan: (slug, planCode, note = '') =>
    api.post(`${ORG(slug)}/change-plan/`, { plan_code: planCode, note }),
  startTrial: (slug, days, note = '') =>
    api.post(`${ORG(slug)}/start-trial/`, { days, note }),
  extend: (slug, months, note = '') =>
    api.post(`${ORG(slug)}/extend/`, { months, note }),

  // --- Part 7: branding and settings ---
  branding: (slug) => api.get(`${ORG(slug)}/branding/`),
  setBranding: (slug, body) => api.patch(`${ORG(slug)}/branding/`, body),
  setBrandingAsset: (slug, field, file) => {
    const form = new FormData();
    form.append('field', field);
    form.append('file', file);
    return api.post(`${ORG(slug)}/branding/asset/`, form);
  },
  settings: (slug) => api.get(`${ORG(slug)}/settings/`),
  setSettings: (slug, body) => api.patch(`${ORG(slug)}/settings/`, body),

  // --- Phase S6.5: portability and the archive ---
  exports: (slug) => api.get(`${ORG(slug)}/exports/`),
  createExport: (slug, contents) =>
    api.post(`${ORG(slug)}/exports/`, contents ? { contents } : {}),
  exportManifest: (slug, id) => api.get(`${ORG(slug)}/exports/${id}/manifest/`),

  // THE DOWNLOAD GOES THROUGH AXIOS, NOT AN <a href>, and that is not a
  // style choice: this app authenticates with a Bearer token in a header, so
  // a plain link would arrive at the console endpoint with no credentials and
  // answer 401. The bundle therefore comes back as a blob and is handed to
  // the browser through an object URL.
  //
  // A tenant export can be large, and a blob is held in memory -- which is
  // the known cost of not having a cookie session to lean on. It is bounded
  // by the same thing that bounds the export itself: one tenant's data.
  downloadExport: async (slug, id, filename) => {
    const response = await api.get(`${ORG(slug)}/exports/${id}/download/`,
      { responseType: 'blob' });
    const url = window.URL.createObjectURL(
      new Blob([response.data], { type: 'application/zip' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || `${slug}-export.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => window.URL.revokeObjectURL(url), 60000);
    // The checksum the server put in the header, so the caller can show the
    // operator what to verify the handed-over file against.
    return response.headers['x-export-sha256'] || '';
  },

  archive: (slug, reason) => api.post(`${ORG(slug)}/archive/`, { reason }),
  restore: (slug, body = {}) => api.post(`${ORG(slug)}/restore/`, body),

  // --- Phase S6.75: launch readiness and the event dashboards ---
  launchReadiness: () => api.get('/platform/launch-readiness/'),

  /**
   * Phase S9: every custom domain on the platform, in one list.
   *
   * SEPARATE FROM THE PER-TENANT CALLS BELOW because the question an
   * operator has is cross-tenant: "which claims are stuck". A claim checked
   * fifteen times and still failing is a customer who needs a phone call,
   * and nobody finds that by opening tenants one at a time.
   */
  domains: () => api.get('/platform/domains/'),
  orgDomains: (slug) => api.get(`/platform/organizations/${slug}/domains/`),
  claimDomain: (slug, hostname, method) =>
    api.post(`/platform/organizations/${slug}/domains/`, { hostname, method }),
  verifyDomain: (slug, hostname) =>
    api.post(`/platform/organizations/${slug}/domains/${encodeURIComponent(hostname)}/`, {}),
  removeDomain: (slug, hostname) =>
    api.delete(`/platform/organizations/${slug}/domains/${encodeURIComponent(hostname)}/`),
  events: (days = 30) => api.get('/platform/events/', { params: { days } }),
  registrationFunnel: (days = 30) =>
    api.get('/platform/registration-funnel/', { params: { days } }),

  // --- Part 9: audit ---
  audit: (params = {}) => api.get('/platform/audit/', { params }),
  recent: () => api.get('/platform/recent/'),
  // Customer success: health and adoption (from usage counters only), and
  // the inbox of what customers wrote to the platform team.
  customerHealth: (days = 30) => api.get('/platform/customer-health/', { params: { days } }),
  supportInbox: (params = {}) => api.get('/platform/support/', { params }),
  updateSupport: (id, body) => api.patch(`/platform/support/${id}/`, body),
  payments: (view) => api.get('/platform/payments/', { params: view ? { view } : {} }),
  // The Payment Center (no engineer, no shell): decide a payment, read the
  // receipt the customer sent.
  approvePayment: (id, note = '') => api.post(`/platform/payments/${id}/approve/`, { note }),
  rejectPayment: (id, reason) => api.post(`/platform/payments/${id}/reject/`, { reason }),
  requestPaymentInfo: (id, message) => api.post(`/platform/payments/${id}/request-info/`, { message }),
  paymentProof: (id) => api.get(`/platform/payments/${id}/proof/`, { responseType: 'blob' }),
  // Payment methods customers are shown on their subscription page.
  paymentMethods: (archived = false) => api.get('/platform/payment-methods/', { params: archived ? { archived: 1 } : {} }),
  createPaymentMethod: (body) => api.post('/platform/payment-methods/', body),
  updatePaymentMethod: (id, body) => api.patch(`/platform/payment-methods/${id}/`, body),
  setPaymentMethodState: (id, state) => api.post(`/platform/payment-methods/${id}/`, { state }),

  // --- operations ---
  refreshCounters: () => api.post('/platform/counters/refresh/', {}),
  reconcileMirrors: () => api.post('/platform/mirrors/reconcile/', {}),
};

/** Minor units to a readable amount. NEVER used for arithmetic. */
export const money = (minor, currency = 'NPR') => {
  if (minor === null || minor === undefined) return '—';
  const major = Number(minor) / 100;
  // Whole amounts without decimals (NPR 9,990); anything with paisa gets
  // both digits (NPR 832.50) -- never the "832.5" a price list never shows.
  const whole = Number(minor) % 100 === 0;
  return `${currency} ${major.toLocaleString(undefined, {
    minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2,
  })}`;
};

/** Bytes to the largest unit that keeps the figure readable. */
export const bytes = (value) => {
  const n = Number(value || 0);
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  return `${(n / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
};

// The lifecycle, in order, so a status reads as a position rather than a word.
export const ORG_STATUSES = [
  { value: 'provisioning', label: 'Provisioning', tone: 'is-warn' },
  { value: 'trial', label: 'Trial', tone: 'is-info' },
  { value: 'active', label: 'Active', tone: 'is-good' },
  { value: 'grace', label: 'Grace period', tone: 'is-warn' },
  { value: 'suspended', label: 'Suspended', tone: 'is-bad' },
  { value: 'cancelled', label: 'Cancelled', tone: 'is-mute' },
];

export const statusTone = (status) =>
  ORG_STATUSES.find((s) => s.value === status)?.tone || 'is-mute';

export const statusLabel = (status) =>
  ORG_STATUSES.find((s) => s.value === status)?.label || status || '—';

export default platformService;
