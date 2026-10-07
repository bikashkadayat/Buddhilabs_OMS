import api from './api';

/**
 * Minute API client.
 *
 * Every list call goes through `getMinutes({ scope })` because the server owns what
 * each menu means (minutes.views._apply_scope), so a new menu is a route plus a scope
 * string rather than a new query here.
 *
 * The surface follows the E-minute manual: a draft is saved, optionally sent to the
 * FRO for draft review, then submitted for acknowledgement, and archives itself once
 * every member present has acknowledged. There is no approval chain and no OTP.
 */
export const minuteService = {
  // --- lists and detail ---
  getMinutes: (params = {}) =>
    api.get('/minutes/', { params }).then((r) => r.data),
  getMinute: (id) => api.get(`/minutes/${id}/`).then((r) => r.data),
  createMinute: (payload) => api.post('/minutes/', payload).then((r) => r.data),
  updateMinute: (id, payload) =>
    api.patch(`/minutes/${id}/`, payload).then((r) => r.data),
  deleteMinute: (id) => api.delete(`/minutes/${id}/`).then((r) => r.data),

  // The Minute Type dropdown and the attendance/status option lists. Fetched once and
  // cached; adding a type in the admin shows up in the form with no frontend change.
  getTaxonomy: () => api.get('/minutes/taxonomy/').then((r) => r.data),

  // --- the two submit buttons (manual p.7) ---
  sendForReview: (id) =>
    api.post(`/minutes/${id}/send-for-review/`, {}).then((r) => r.data),
  returnReview: (id, remarks) =>
    api.post(`/minutes/${id}/return-review/`, { remarks }).then((r) => r.data),
  sendForAcknowledgement: (id) =>
    api.post(`/minutes/${id}/send-for-acknowledgement/`, {}).then((r) => r.data),

  // --- people ---
  setParticipants: (id, participants) =>
    api.post(`/minutes/${id}/participants/`, { participants }).then((r) => r.data),
  setInvolvements: (id, involvements) =>
    api.post(`/minutes/${id}/involvements/`, { involvements }).then((r) => r.data),

  // --- acknowledgement (manual pp. 8-9; no OTP) ---
  acknowledge: (id, payload) =>
    api.post(`/minutes/${id}/acknowledge/`, payload).then((r) => r.data),
  remindAcknowledgements: (id) =>
    api.post(`/minutes/${id}/remind-acknowledgements/`).then((r) => r.data),

  /**
   * The Reference No. search (manual p.5): look up a previous minute and return its
   * agenda so the editor can be seeded with it.
   */
  lookupReference: (reference) =>
    api.get('/minutes/reference-lookup/', { params: { reference } })
      .then((r) => r.data),

  // --- dashboard and audit ---
  getDashboard: () => api.get('/minutes/dashboard/').then((r) => r.data),
  getCharts: () => api.get('/minutes/dashboard/charts/').then((r) => r.data),
  getAuditTrail: (id) =>
    api.get(`/minutes/${id}/audit-trail/`).then((r) => r.data),

  // --- attachments ---
  getAttachments: (id) =>
    api.get(`/minutes/${id}/attachments/`).then((r) => r.data),
  /**
   * Upload one or more files. Multipart, and `files` is repeated rather than sent as
   * an array field, because that is what DRF's request.FILES.getlist() reads.
   */
  uploadAttachments: (id, files, { replaces } = {}) => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    if (replaces) form.append('replaces', replaces);
    // The multipart Content-Type IS set here, and the comment this replaced was
    // wrong about why it should not be.
    //
    // It claimed "setting it by hand produces a request the server cannot
    // parse". It does not: when the body is FormData, axios replaces the value
    // with one carrying the correct boundary. What actually breaks the request
    // is leaving it off, because the shared instance in services/api.js
    // declares a DEFAULT of application/json which is then applied to the
    // multipart body — the server sees no files and returns 400.
    //
    // circularService and taskService have always passed this header and their
    // uploads have always worked, which is the evidence the old comment was
    // written against.
    return api.post(`/minutes/${id}/attachments/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data);
  },
  deleteAttachment: (id, attachmentId) =>
    api.delete(`/minutes/${id}/attachments/${attachmentId}/`).then((r) => r.data),

  // --- exports. All three are allowed on an archived minute; each download is audited.
  pdf: (id) => api.get(`/minutes/${id}/pdf/`, { responseType: 'blob' }),
  excel: (id) => api.get(`/minutes/${id}/excel/`, { responseType: 'blob' }),
  acknowledgementSheet: (id) =>
    api.get(`/minutes/${id}/acknowledgement-sheet/`, { responseType: 'blob' }),
};

export default minuteService;
