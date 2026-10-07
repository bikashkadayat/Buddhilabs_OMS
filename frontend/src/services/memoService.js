import api from './api';
import { unwrapPaginated } from './utils';

/** POST a memo payload, letting the browser set the multipart boundary for FormData. */
const postMemo = (url, data) => {
  const isForm = typeof FormData !== 'undefined' && data instanceof FormData;
  return isForm
    ? api.post(url, data, { headers: { 'Content-Type': 'multipart/form-data' } })
    : api.post(url, data);
};

/**
 * Memo module service.
 *
 * Talks to /api/v1/memos/*, which implements the approval-matrix workflow
 * (Reviewer -> Recommender -> Supporter -> Approver, any number of steps). Uses
 * the shared axios instance, so token refresh is handled for us.
 *
 * The legacy submit/review/approve/reject/return calls and the two role-filtered
 * assignee pickers were removed with the endpoints behind them; `setMatrix` +
 * `sendForReview` + `actOnMemo` replace all five, and `searchEmployees` replaces
 * both pickers.
 */
export const memoService = {
  /**
   * @param {Object} filters query params (status, memo_type, priority, search…)
   * @param {number} page
   * @returns {Promise<{items:Object[], count:number, next:?string, previous:?string}>}
   */
  listMemos: async (filters = {}, page = 1) => {
    const res = await api.get('/memos/', { params: { ...filters, page } });
    return {
      items: unwrapPaginated(res),
      count: res.data?.count ?? (Array.isArray(res.data) ? res.data.length : 0),
      next: res.data?.next ?? null,
      previous: res.data?.previous ?? null,
    };
  },

  /** @returns {Promise<Object>} full memo detail incl. approval_steps + can_* flags */
  getMemo: async (id) => (await api.get(`/memos/${id}/`)).data,

  /**
   * Create a draft memo. Accepts either a plain object (JSON) or a FormData
   * (when an attachment is included) - the single code path picks the right
   * content type (M5). @returns {Promise<Object>} created memo (id + memo_number)
   */
  createMemo: async (data) => (await postMemo('/memos/', data)).data,

  /**
   * Atomically create + submit a memo (M2). Same JSON/FormData handling as
   * createMemo. If the submit fails server-side the draft is rolled back.
   */
  createAndSubmit: async (data) => (await postMemo('/memos/create-and-submit/', data)).data,

  updateMemo: async (id, data) => (await api.patch(`/memos/${id}/`, data)).data,

  /** The author withdraws their own in-flight memo (status Cancelled). */
  withdrawMemo: async (id, { remarks = '' } = {}) =>
    (await api.post(`/memos/${id}/cancel/`, { remarks })).data,

  deleteMemo: async (id) => (await api.delete(`/memos/${id}/`)).data,

  // -- enterprise matrix workflow ------------------------------------------
  /**
   * Replace a memo's approval matrix. Sequence is list order, so reordering is
   * just a re-POST of the reordered array — nothing renumbers client-side.
   * @param {Array<{assignee_id:string, role_type:string}>} workflow
   */
  setMatrix: async (id, workflow) =>
    (await api.post(`/memos/${id}/matrix/`, { workflow })).data,

  getMatrix: async (id) => (await api.get(`/memos/${id}/matrix/`)).data,

  /** Draft → Draft For Review. Optionally sets the matrix in the same call. */
  sendForReview: async (id, { workflow, remarks = '' } = {}) =>
    (await api.post(`/memos/${id}/send-for-review/`, {
      ...(workflow ? { workflow } : {}), remarks,
    })).data,

  /**
   * Complete or reject the caller's own step. One endpoint for all four role
   * types — the server reads the active step's role and records the right verb,
   * so a client can never approve on a supporter's step.
   * @param {'proceed'|'reject'} decision
   */
  actOnMemo: async (id, { decision, remarks = '' }) =>
    (await api.post(`/memos/${id}/act/`, { decision, remarks })).data,

  archiveMemo: async (id) => (await api.post(`/memos/${id}/archive/`, {})).data,

  getTimeline: async (id) => (await api.get(`/memos/${id}/timeline/`)).data,

  /** Counts behind the dashboard tiles and the sidebar badges. */
  getDashboard: async () => (await api.get('/memos/dashboard/')).data,

  /**
   * The three dashboard chart datasets. Separate from getDashboard because the
   * tiles are polled every minute for the sidebar badge and these GROUP BY
   * queries have no business running that often.
   */
  getDashboardCharts: async (params = {}) =>
    (await api.get('/memos/dashboard/charts/', { params })).data,

  /** Full audit trail incl. client IP. HR/Admin only; 403 for anyone else. */
  getAuditTrail: async (id) => (await api.get(`/memos/${id}/audit-trail/`)).data,

  /**
   * Employee directory for the approval-matrix picker. Unlike the legacy
   * checker/approver pickers this is NOT role-filtered: any employee may hold
   * any role type in the matrix. Server requires >= 2 characters.
   */
  searchEmployees: async (search) =>
    (await api.get('/memos/employees/', { params: search ? { search } : {} })).data,

  /**
   * Excel export of the caller's current scope/filters. Returns the whole axios
   * response, not just the body: saveBlob() reads the server's
   * Content-Disposition filename off the headers.
   * @returns {Promise<import('axios').AxiosResponse>}
   */
  exportMemos: async (params = {}) =>
    api.get('/memos/export/', { params, responseType: 'blob' }),

  /**
   * The memo's ordered content blocks - Background, Recommendation, and anything
   * "+ Add more" produced (E-memo-manual p.3). The whole list is sent at once
   * because its ORDER is the document's order.
   */
  /* --- the manual's special cases (E-memo-manual pp. 8-17) ---------------- */

  /** "Mark as Unavailable and Forward memo" (p.8-9). */
  markUnavailable: async (id, { reason, reason_note = '', step_id = null }) =>
    (await api.post(`/memos/${id}/unavailable/`,
      { reason, reason_note, step_id })).data,

  /** "ADD NEW APPROVER" → "Submit to the new Approver" (p.14). */
  addReplacement: async (id, { user_id, reason, kind = null, step_id = null }) =>
    (await api.post(`/memos/${id}/replacement/`,
      { user_id, reason, kind, step_id })).data,

  /** "Add noted member" (p.10-11). Anyone in the chain may do this. */
  askToNote: async (id, { user_ids, remarks = '' }) =>
    (await api.post(`/memos/${id}/notes/`, { user_ids, remarks })).data,
  /** Record the caller's own note. */
  noteMemo: async (id, { remarks = '' } = {}) =>
    (await api.post(`/memos/${id}/note/`, { remarks })).data,

  /** "Re-Assign" — back to the initial recommender with changes sought (p.10). */
  reassignMemo: async (id, { user_id = null, reason, kind, step_id = null }) =>
    (await api.post(`/memos/${id}/reassign/`,
      { user_id, reason, kind, step_id })).data,

  /** "Add view access" on an archived memo (p.17). */
  grantArchiveAccess: async (id, payload) =>
    (await api.post(`/memos/${id}/archive-access/`, payload)).data,
  listArchiveAccess: async (id) =>
    (await api.get(`/memos/${id}/archive-access/`)).data,

  listSections: async (id) => (await api.get(`/memos/${id}/sections/`)).data,
  setSections: async (id, sections) =>
    (await api.post(`/memos/${id}/sections/`, { sections })).data,

  /** Departments, for the From unit/sub-unit pickers and the CC list. */
  listDepartments: async () => (await api.get('/memos/departments/')).data,

  listAttachments: async (id) => (await api.get(`/memos/${id}/attachments/`)).data,

  /**
   * Upload files. `name` is the manual's "File Name" box (p.6) - what the reader
   * is shown, in place of whatever the file was called on someone's desktop.
   *
   * Fifteen types are accepted — documents, spreadsheets, slides, images and
   * zip — up to 10 MB, the project default. The external E-memo manual (p.6)
   * still states 2 MB and needs correcting. The server enforces both the type
   * and the size, and names the offending file when it refuses one.
   */
  uploadAttachments: async (id, files, { name = '' } = {}) => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    if (name) form.append('name', name);
    // The multipart Content-Type MUST be set explicitly here.
    //
    // The previous comment said it was "deliberately unset so the browser adds
    // the multipart boundary itself" — true of a bare axios call, wrong for
    // this one: the shared instance in services/api.js declares a DEFAULT of
    // application/json, and that default was applied to the FormData body.
    // Django's parser then found no files, request.FILES was empty, and every
    // upload came back 400 "No file was supplied" in about 10ms without a byte
    // of the file being read.
    //
    // circularService and taskService always passed this header; memo and
    // minute did not. That is the whole difference between the modules whose
    // uploads worked and the ones whose did not.
    return (await api.post(`/memos/${id}/attachments/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })).data;
  },

  /** @returns {Promise<Object[]>} active memo templates */
  listTemplates: async () => unwrapPaginated(await api.get('/memo-templates/')),
};

export default memoService;
