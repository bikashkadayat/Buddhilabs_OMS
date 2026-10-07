import api from './api';

/**
 * Appraisal API client (Phase APM-03b).
 *
 * THREE RULES THIS FILE KEEPS
 *
 * 1. EVERY LIST IS A SCOPE. `getAppraisals({ scope })` is the only list call,
 *    because the SERVER owns what each menu means (appraisal.views._apply_scope)
 *    — mine, supervising, committee, needs_me, open, closed. A new screen is a
 *    route plus a scope string, never a filter written here. Two copies of
 *    "which appraisals are mine" is two things that can disagree, and the copy
 *    on this side of the wire is the one with no enforcement behind it.
 *
 * 2. EVERY TRANSITION IS ITS OWN ENDPOINT, named for the step it performs.
 *    There is deliberately no `setStatus(id, status)`: a generic status setter
 *    lets the client ask for a move the engine would refuse, and every guard
 *    lives on the server.
 *
 * 3. NO EVIDENCE IS COMPUTED HERE. `getEvidence` reads what the task module
 *    already said, through the Phase T6 contract. This client does no
 *    arithmetic on any figure it receives — not a percentage, not a total, not
 *    an average. A second place that computes evidence is a second set of
 *    numbers that disagrees with the first, in a conversation about somebody's
 *    year.
 */
const BASE = '/appraisals/';

export const appraisalService = {
  // --- lists and detail ---
  getAppraisals: (params = {}) => api.get(BASE, { params }).then((r) => r.data),
  getAppraisal: (id) => api.get(`${BASE}${id}/`).then((r) => r.data),
  createAppraisal: (payload) => api.post(BASE, payload).then((r) => r.data),
  /** The written record. Guarded field by field on the server. */
  updateAppraisal: (id, payload) =>
    api.patch(`${BASE}${id}/`, payload).then((r) => r.data),
  deleteAppraisal: (id) => api.delete(`${BASE}${id}/`).then((r) => r.data),
  getTimeline: (id) => api.get(`${BASE}${id}/timeline/`).then((r) => r.data),

  // --- cycles (HR) ---
  getCycles: (params = {}) =>
    api.get('/appraisal-cycles/', { params }).then((r) => r.data),
  createCycle: (payload) =>
    api.post('/appraisal-cycles/', payload).then((r) => r.data),
  updateCycle: (id, payload) =>
    api.patch(`/appraisal-cycles/${id}/`, payload).then((r) => r.data),
  activateCycle: (id) =>
    api.post(`/appraisal-cycles/${id}/activate/`).then((r) => r.data),
  closeCycle: (id) =>
    api.post(`/appraisal-cycles/${id}/close/`).then((r) => r.data),

  getCompetencies: () => api.get('/competencies/').then((r) => r.data),

  /**
   * The employee directory, for picking an employee, a supervisor, a committee
   * or a mentor.
   *
   * This is `/memos/employees/` — the shared approval-matrix picker, not a memo
   * feature. It is the right directory source for every module because it is
   * IsAuthenticated rather than admin-only, gated at two characters, capped,
   * throttled, and it never returns an email address. Enterprise search already
   * calls it directly for the same reason. Building a second directory endpoint
   * would mean a second set of PII rules to keep in step with this one.
   */
  searchPeople: (search) =>
    api.get('/memos/employees/', { params: search ? { search } : {} })
      .then((r) => r.data),

  // --- goals ---
  getGoals: (id) => api.get(`${BASE}${id}/goals/`).then((r) => r.data),
  addGoal: (id, payload) =>
    api.post(`${BASE}${id}/goals/`, payload).then((r) => r.data),
  updateGoal: (id, goalId, payload) =>
    api.patch(`${BASE}${id}/goals/${goalId}/`, payload).then((r) => r.data),
  deleteGoal: (id, goalId) =>
    api.delete(`${BASE}${id}/goals/${goalId}/`).then((r) => r.data),

  // --- competency ratings ---
  /**
   * `level` is a WORD — needs_development … outstanding — and `comment` is
   * mandatory on the server. Both are properties of the model, not of this
   * client, and neither is convertible to a number anywhere in the UI.
   */
  rate: (id, payload) =>
    api.post(`${BASE}${id}/rate/`, payload).then((r) => r.data),

  // --- development and training ---
  getDevelopmentPlan: (id) =>
    api.get(`${BASE}${id}/development-plan/`).then((r) => r.data),
  addDevelopmentAction: (id, payload) =>
    api.post(`${BASE}${id}/development-plan/`, payload).then((r) => r.data),
  getTrainingPlan: (id) =>
    api.get(`${BASE}${id}/training-plan/`).then((r) => r.data),
  addTrainingNeed: (id, payload) =>
    api.post(`${BASE}${id}/training-plan/`, payload).then((r) => r.data),
  decideTraining: (id, trainingId, payload) =>
    api.post(`${BASE}${id}/training-plan/${trainingId}/decide/`, payload)
      .then((r) => r.data),

  // --- evidence: read and cite, never recompute ---
  getEvidence: (id) => api.get(`${BASE}${id}/evidence/`).then((r) => r.data),
  /** Freeze the current figures onto the record as a dated citation. */
  attachEvidence: (id, payload = {}) =>
    api.post(`${BASE}${id}/attach-evidence/`, payload).then((r) => r.data),

  // --- workflow, one call per named stage ---
  /** Goal Setting -> Goal Approval. The employee hands their objectives over. */
  submitGoals: (id) =>
    api.post(`${BASE}${id}/submit-goals/`).then((r) => r.data),
  /** Goal Approval -> Mid-Year. The supervisor accepts them. */
  agreeGoals: (id) => api.post(`${BASE}${id}/agree-goals/`).then((r) => r.data),
  recordMidYear: (id) =>
    api.post(`${BASE}${id}/record-mid-year/`).then((r) => r.data),
  submitSelfAssessment: (id) =>
    api.post(`${BASE}${id}/submit-self-assessment/`).then((r) => r.data),
  recordSupervisorReview: (id) =>
    api.post(`${BASE}${id}/record-supervisor-review/`).then((r) => r.data),
  recordCommitteeReview: (id) =>
    api.post(`${BASE}${id}/record-committee-review/`).then((r) => r.data),
  recordFinalReview: (id) =>
    api.post(`${BASE}${id}/record-final-review/`).then((r) => r.data),
  agreeDevelopmentPlan: (id) =>
    api.post(`${BASE}${id}/agree-development-plan/`).then((r) => r.data),
  agreeTrainingPlan: (id) =>
    api.post(`${BASE}${id}/agree-training-plan/`).then((r) => r.data),
  /** Send it back a stage. The server requires a written reason. */
  returnStage: (id, payload) =>
    api.post(`${BASE}${id}/return/`, payload).then((r) => r.data),
  reopen: (id, payload) =>
    api.post(`${BASE}${id}/reopen/`, payload).then((r) => r.data),

  // --- dashboards and reports ---
  /**
   * One endpoint, three optional blocks. Which blocks come back is decided by
   * the server from the caller's role; this client renders what it was given
   * and never re-derives the role to decide what to ask for.
   */
  getDashboard: () => api.get(`${BASE}dashboard/`).then((r) => r.data),
  getReports: () => api.get(`${BASE}reports/`).then((r) => r.data),
  getReport: (slug, params = {}) =>
    api.get(`${BASE}reports/${slug}/`, { params }).then((r) => r.data),
  /**
   * Download a report as a FILE, through the authenticated client.
   *
   * NOT a plain `<a href>`. This app authenticates with a JWT held in
   * JavaScript, not a session cookie, so a link navigation carries no
   * Authorization header and the export endpoint answers 401 — the browser then
   * renders that JSON, or saves it as the "file". That is exactly what the
   * earlier `reportCsvUrl` / `reportPdfUrl` helpers did, and their comment
   * claimed "the browser downloads them with the session it already has". There
   * is no session.
   *
   * Returns the whole axios response, not just the body: `saveBlob` reads the
   * server's Content-Disposition filename off the headers, so the file is named
   * once, by the server, rather than guessed at each call site.
   *
   */
  downloadReport: (slug, format) =>
    api.get(`${BASE}reports/${slug}/`, {
      params: { export: format },
      responseType: 'blob',
    }),
};

export default appraisalService;
