import api from './api';

/**
 * Workforce management API (Phase 9).
 *
 * Every endpoint here is role-scoped server-side; the UI guards are defence in
 * depth, never the security boundary. Paginated list endpoints return the DRF
 * envelope, which `page()` normalises so callers never have to care whether a
 * response was paginated.
 */

const page = (data) =>
  Array.isArray(data)
    ? { results: data, count: data.length, next: null, previous: null }
    : { results: data?.results ?? [], count: data?.count ?? 0,
        next: data?.next ?? null, previous: data?.previous ?? null };

export const workforceService = {
  // --- dashboards (one composed payload each) ----------------------------
  me: async () => (await api.get('/workforce/me/')).data,
  team: async (params = {}) => (await api.get('/workforce/team/', { params })).data,
  hr: async (params = {}) => (await api.get('/workforce/hr/', { params })).data,

  // --- corrections -------------------------------------------------------
  corrections: async (params = {}) =>
    page((await api.get('/workforce/corrections/', { params })).data),
  correctionCounts: async () =>
    (await api.get('/workforce/corrections/queue-counts/')).data,

  /**
   * Submit a correction. Sends multipart only when a file is attached, so the
   * common case stays a plain JSON POST.
   */
  submitCorrection: async (payload) => {
    const { attachment, ...rest } = payload;
    if (!attachment) {
      return (await api.post('/workforce/corrections/', rest)).data;
    }
    const form = new FormData();
    Object.entries(rest).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') form.append(key, value);
    });
    form.append('attachment', attachment);
    return (await api.post('/workforce/corrections/', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })).data;
  },

  approveCorrection: async (id, remarks = '') =>
    (await api.post(`/workforce/corrections/${id}/approve/`, { remarks })).data,
  rejectCorrection: async (id, reason = '') =>
    (await api.post(`/workforce/corrections/${id}/reject/`, { reason })).data,
  cancelCorrection: async (id) =>
    (await api.post(`/workforce/corrections/${id}/cancel/`, {})).data,
  revertCorrection: async (id, reason = '') =>
    (await api.post(`/workforce/corrections/${id}/revert/`, { reason })).data,
  correctionAttachment: (id) =>
    api.get(`/workforce/corrections/${id}/attachment/`, { responseType: 'blob' }),

  // --- conflicts, WFH, comp-off -----------------------------------------
  conflicts: async (params = {}) =>
    (await api.get('/workforce/conflicts/', { params })).data,
  leavePreview: async (params = {}) =>
    (await api.get('/workforce/leave-preview/', { params })).data,
  wfhSummary: async (params = {}) =>
    (await api.get('/workforce/wfh/summary/', { params })).data,
  compOffSummary: async () =>
    (await api.get('/workforce/comp-off/summary/')).data,

  // --- WFH requests (Phase 8 endpoints) ----------------------------------
  wfhRequests: async (params = {}) =>
    page((await api.get('/attendance/wfh/', { params })).data),
  submitWfh: async (payload) => (await api.post('/attendance/wfh/', payload)).data,
  approveWfh: async (id, note = '') =>
    (await api.post(`/attendance/wfh/${id}/approve/`, { note })).data,
  rejectWfh: async (id, note = '') =>
    (await api.post(`/attendance/wfh/${id}/reject/`, { note })).data,
  cancelWfh: async (id) => (await api.post(`/attendance/wfh/${id}/cancel/`, {})).data,

  // --- comp-off review (Phase 8 endpoints) -------------------------------
  confirmCompOff: async (id, note = '') =>
    (await api.post(`/leaves/compensatory/${id}/confirm/`, { note })).data,
  rejectCompOff: async (id, reason = '') =>
    (await api.post(`/leaves/compensatory/${id}/reject/`, { reason })).data,
};

export default workforceService;
