import api from './api';

/**
 * Circular API client (Phase 50).
 *
 * Deliberately shaped like memoService and minuteService: the three modules answer
 * overlapping kinds of question, and a developer who knows one should not have to
 * learn a third set of conventions. Every list call goes through
 * `getCirculars({ scope })` because the server owns what each sidebar menu means
 * (circulars.views._apply_scope), so a new menu is a route plus a scope string
 * rather than a new query here.
 *
 * The block that has no counterpart in the other two is the broadcast group. A memo
 * is distributed by its approval chain; a circular is broadcast to an audience, and
 * that audience has to be previewed before it is committed to - which is why
 * `previewAudience` exists alongside `broadcast` and takes the identical payload.
 */
export const circularService = {
  // --- lists and detail ---
  getCirculars: (params = {}) =>
    api.get('/circulars/', { params }).then((r) => r.data),
  getCircular: (id) => api.get(`/circulars/${id}/`).then((r) => r.data),
  createCircular: (payload) =>
    api.post('/circulars/', payload).then((r) => r.data),
  updateCircular: (id, payload) =>
    api.patch(`/circulars/${id}/`, payload).then((r) => r.data),
  deleteCircular: (id) => api.delete(`/circulars/${id}/`).then((r) => r.data),

  // --- vocabulary, from the server's own choice lists ---
  getTaxonomy: () => api.get('/circulars/taxonomy/').then((r) => r.data),
  // Departments and groups a broadcaster may choose from.
  getAudiences: () => api.get('/circulars/audiences/').then((r) => r.data),
  getDashboard: () => api.get('/circulars/dashboard/').then((r) => r.data),

  // --- the review / issue chain ---
  setChain: (id, workflow) =>
    api.post(`/circulars/${id}/chain/`, { workflow }).then((r) => r.data),
  sendForReview: (id, workflow) =>
    api.post(`/circulars/${id}/send-for-review/`,
      workflow ? { workflow } : {}).then((r) => r.data),
  act: (id, payload) =>
    api.post(`/circulars/${id}/act/`, payload).then((r) => r.data),
  cancel: (id, remarks) =>
    api.post(`/circulars/${id}/cancel/`, { remarks }).then((r) => r.data),
  archive: (id) => api.post(`/circulars/${id}/archive/`).then((r) => r.data),

  // --- content import; editable afterwards, not a live link ---
  importFrom: (id, payload) =>
    api.post(`/circulars/${id}/import/`, payload).then((r) => r.data),

  // --- broadcast ---
  // previewAudience takes the SAME payload as broadcast on purpose: a preview that
  // could differ from the send it previews would be worse than none, and
  // broadcasting is irreversible.
  previewAudience: (id, payload) =>
    api.post(`/circulars/${id}/audience-preview/`, payload).then((r) => r.data),
  broadcast: (id, payload) =>
    api.post(`/circulars/${id}/broadcast/`, payload).then((r) => r.data),

  // --- registers (narrower than reading the circular; the server enforces it) ---
  getRecipients: (id) =>
    api.get(`/circulars/${id}/recipients/`).then((r) => r.data),
  getAcknowledgements: (id) =>
    api.get(`/circulars/${id}/acknowledgements/`).then((r) => r.data),
  acknowledge: (id, payload) =>
    api.post(`/circulars/${id}/acknowledge/`, payload).then((r) => r.data),
  remind: (id) => api.post(`/circulars/${id}/remind/`).then((r) => r.data),

  // --- attachments ---
  getAttachments: (id) =>
    api.get(`/circulars/${id}/attachments/`).then((r) => r.data),
  uploadAttachments: (id, files) => {
    const form = new FormData();
    [...files].forEach((file) => form.append('files', file));
    return api.post(`/circulars/${id}/attachments/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data);
  },
  deleteAttachment: (id, attachmentId) =>
    api.delete(`/circulars/${id}/attachments/${attachmentId}/`).then((r) => r.data),

  // --- audit and export ---
  getAuditTrail: (id) =>
    api.get(`/circulars/${id}/audit-trail/`).then((r) => r.data),
  pdf: (id) =>
    api.get(`/circulars/${id}/pdf/`, { responseType: 'blob' }).then((r) => r.data),
};

export default circularService;
