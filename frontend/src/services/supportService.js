import api from './api';
import { captureContext } from '../utils/supportContext';

/**
 * The Customer Success Center: tickets, feature requests, What's New and
 * System Status -- the customer talking to the platform team, in the product.
 *
 * Every ticket carries its context automatically (page, URL, browser,
 * device, viewport, time); the server adds organization, plan and tenant.
 * The customer only writes the title and description.
 */
const withContext = (body, files = {}) => {
  const context = captureContext(body.from);
  const payload = {
    page: context.page, url: context.url, ...body, context,
  };
  delete payload.from;
  const hasFiles = Object.values(files).some(Boolean);
  if (!hasFiles) return payload;
  const form = new FormData();
  Object.entries(payload).forEach(([k, v]) => {
    if (v === undefined || v === null) return;
    form.append(k, typeof v === 'object' ? JSON.stringify(v) : v);
  });
  Object.entries(files).forEach(([k, f]) => { if (f) form.append(k, f); });
  return form;
};

const multipart = (body) => (body instanceof FormData
  ? { headers: { 'Content-Type': 'multipart/form-data' } } : undefined);

export const supportService = {
  mine: (params = {}) => api.get('/support/requests/', { params }).then((r) => r.data),
  /** Ratings and simple requests (kept for RateThis and older callers). */
  send: (body) => api.post('/support/requests/', withContext(body)).then((r) => r.data),
  /** A smart ticket: category, title, description, optional screenshot/file. */
  createTicket: (body, { screenshot, attachment } = {}) => {
    const payload = withContext(body, { screenshot, attachment });
    return api.post('/support/requests/', payload, multipart(payload)).then((r) => r.data);
  },
  ticket: (id) => api.get(`/support/requests/${id}/`).then((r) => r.data),
  reply: (id, body, attachment) => {
    const payload = attachment ? (() => {
      const f = new FormData(); f.append('body', body); f.append('attachment', attachment); return f;
    })() : { body };
    return api.post(`/support/requests/${id}/messages/`, payload, multipart(payload)).then((r) => r.data);
  },
  close: (id) => api.post(`/support/requests/${id}/`, { action: 'close' }).then((r) => r.data),
  rate: (id, rating, comment) => api.post(`/support/requests/${id}/`,
    { action: 'rate', rating, comment }).then((r) => r.data),
  fileUrl: (id, which, messageId) => `/support/requests/${id}/files/${which}/${messageId ? `${messageId}/` : ''}`,
  openFile: (id, which, messageId) => api.get(
    `/support/requests/${id}/files/${which}/${messageId ? `${messageId}/` : ''}`,
    { responseType: 'blob' },
  ).then((r) => window.open(URL.createObjectURL(r.data), '_blank', 'noopener')),
  featureRequests: () => api.get('/support/feature-requests/').then((r) => r.data),
  updates: () => api.get('/support/updates/').then((r) => r.data),
  markUpdatesSeen: () => api.post('/support/updates/').then((r) => r.data),
  status: () => api.get('/support/status/').then((r) => r.data),
  /** Before a ticket: similar tickets (with answers) and known issues. */
  assist: (query, category) => api.post('/support/assist/', { query, category }).then((r) => r.data),
  /** A suggestion answered it -- counted, so deflection is measurable. */
  deflected: (source) => api.post('/support/assist/', { deflected: true, source }).catch(() => {}),
  /** Suggestions were shown on this draft (once) -- the deflection denominator. */
  assistShown: () => api.post('/support/assist/', { shown: true }).catch(() => {}),
  badges: () => api.get('/support/badges/').then((r) => r.data),
};

export default supportService;
