import api from './api';

/**
 * Task API client (Phase T1).
 *
 * Every list call goes through `getTasks({ scope })` because the server owns what
 * each menu means (tasks.views._apply_scope), so a new menu is a route plus a
 * scope string rather than a new query here. The scopes are the module's menus:
 * mine, assigned_by_me, team, due_today, overdue, completed, needs_me, drafts,
 * pending_review.
 *
 * Every transition is its own endpoint, named for the step of the workflow it
 * performs. There is no `updateStatus(id, status)` on purpose: a generic status
 * setter would let the client choose a transition the engine would otherwise
 * refuse, and the guards live on the server.
 */
export const taskService = {
  // --- lists and detail ---
  getTasks: (params = {}) => api.get('/tasks/', { params }).then((r) => r.data),
  getTask: (id) => api.get(`/tasks/${id}/`).then((r) => r.data),
  createTask: (payload) => api.post('/tasks/', payload).then((r) => r.data),
  updateTask: (id, payload) =>
    api.patch(`/tasks/${id}/`, payload).then((r) => r.data),
  deleteTask: (id) => api.delete(`/tasks/${id}/`).then((r) => r.data),

  // --- dashboard ---
  getDashboard: () => api.get('/tasks/dashboard/').then((r) => r.data),

  // --- workspace views (Phase T3) ---
  /**
   * The Kanban board. Columns come back WITH their status mapping, so this
   * client never needs a second copy of it — two copies is two things that can
   * disagree, and a task claimed by neither vanishes.
   */
  getBoard: (params = {}) =>
    api.get('/tasks/board/', { params }).then((r) => r.data),
  /**
   * Dated events for a Day / Week / Month window. The window is computed
   * server-side from `date` + `view`, so an unbounded range cannot be asked for
   * and "which day does the week start on" has one answer.
   */
  getCalendar: (params = {}) =>
    api.get('/tasks/calendar/', { params }).then((r) => r.data),
  getWorkload: (params = {}) =>
    api.get('/tasks/workload/', { params }).then((r) => r.data),
  getOverdue: (params = {}) =>
    api.get('/tasks/overdue/', { params }).then((r) => r.data),
  /** What is waiting for a review decision, oldest first, with its age. */
  getReviewQueue: (params = {}) =>
    api.get('/tasks/review-queue/', { params }).then((r) => r.data),

  // --- operations (Phase T4) ---
  /**
   * One action, many tasks. The response names what was applied and what was
   * refused, with a reason each — partial success is reported, not hidden.
   */
  bulk: (action, taskIds, payload = {}) =>
    api.post('/tasks/bulk/', { action, task_ids: taskIds, ...payload })
      .then((r) => r.data),
  getTaskGroups: () => api.get('/tasks/groups/').then((r) => r.data),
  /** Raise a batch from a template: one task per checklist section. */
  createTaskGroup: (payload) =>
    api.post('/tasks/groups/', payload).then((r) => r.data),
  /** Values the advanced filter offers, from the caller's own visible set. */
  getFilterOptions: () =>
    api.get('/tasks/filter-options/').then((r) => r.data),

  // --- reports (Phase T3, Part 8) ---
  getReports: () => api.get('/tasks/reports/').then((r) => r.data),
  getReport: (slug, params = {}) =>
    api.get(`/tasks/reports/${slug}/`, { params }).then((r) => r.data),
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
   * The current filters are carried into the export, so a report exported from
   * a filtered screen contains what was on the screen rather than everything.
   */
  downloadReport: (slug, format, params = {}) =>
    api.get(`/tasks/reports/${slug}/`, {
      params: { ...params, export: format },
      responseType: 'blob',
    }),

  /**
   * One task attachment, fetched with the token.
   *
   * Task files are NOT served from a signed media URL the way memo, minute and
   * circular attachments are — the serializer points at
   * `/api/v1/tasks/{task}/attachments/{id}/download/`, an authenticated
   * task-scoped view, and that choice is what makes the per-download audit log
   * possible. The cost is that the client has to FETCH the file: an
   * `<a href={file.download_url}>` navigates without the Authorization header
   * and downloads a 401 body, which is the bug this exists to fix.
   *
   * Returns the whole response so `saveBlob` can read the server's
   * Content-Disposition filename rather than each call site guessing one.
   */
  downloadAttachment: (taskId, attachmentId) =>
    api.get(`/tasks/${taskId}/attachments/${attachmentId}/download/`, {
      responseType: 'blob',
    }),

  // --- people ---
  /** The employee search behind Assigned To. Not a department picker. */
  searchEmployees: (search) =>
    api.get('/tasks/employees/', { params: { search } }).then((r) => r.data),
  setAssignees: (id, assigneeIds) =>
    api.post(`/tasks/${id}/assignees/`, { assignee_ids: assigneeIds })
      .then((r) => r.data),

  // --- transitions, one per named step of the workflow ---
  assign: (id) => api.post(`/tasks/${id}/assign/`, {}).then((r) => r.data),
  accept: (id) => api.post(`/tasks/${id}/accept/`, {}).then((r) => r.data),
  requestClarification: (id, reason) =>
    api.post(`/tasks/${id}/request-clarification/`, { reason }).then((r) => r.data),
  start: (id) => api.post(`/tasks/${id}/start/`, {}).then((r) => r.data),
  updateProgress: (id, progressPercent, note = '') =>
    api.post(`/tasks/${id}/progress/`, { progress_percent: progressPercent, note })
      .then((r) => r.data),
  submitForReview: (id, note = '') =>
    api.post(`/tasks/${id}/submit-for-review/`, { note }).then((r) => r.data),
  // Dependencies (Phase TASK-GOVERNANCE-HARDENING).
  getDependencies: (id) =>
    api.get(`/tasks/${id}/dependencies/`).then((r) => r.data),
  addDependency: (id, dependsOn, kind = 'blocked_by', note = '') =>
    api.post(`/tasks/${id}/dependencies/`, { depends_on: dependsOn, kind, note })
      .then((r) => r.data),
  removeDependency: (id, dependencyId) =>
    api.delete(`/tasks/${id}/dependencies/${dependencyId}/`).then((r) => r.data),
  approve: (id, remarks = '') =>
    api.post(`/tasks/${id}/approve/`, { remarks }).then((r) => r.data),
  requestRework: (id, reason) =>
    api.post(`/tasks/${id}/request-rework/`, { reason }).then((r) => r.data),
  close: (id, remarks = '') =>
    api.post(`/tasks/${id}/close/`, { remarks }).then((r) => r.data),
  block: (id, reason) =>
    api.post(`/tasks/${id}/block/`, { reason }).then((r) => r.data),
  unblock: (id) => api.post(`/tasks/${id}/unblock/`, {}).then((r) => r.data),
  cancel: (id, reason) =>
    api.post(`/tasks/${id}/cancel/`, { reason }).then((r) => r.data),

  // --- checklist (Phase T2.1) ---
  /** Flat: every item in order, each carrying the `group` it belongs to. */
  getChecklist: (id) => api.get(`/tasks/${id}/checklist/`).then((r) => r.data),
  /** Nested: loose items, then sections. Two routes, one shape each. */
  getChecklistGrouped: (id) =>
    api.get(`/tasks/${id}/checklist/grouped/`).then((r) => r.data),
  /**
   * Replace the checklist. `items` are top-level lines and `groups` are
   * sections; send either or both. An empty `items` array clears it — omitting
   * both is refused, because that is far more likely a bug than an instruction.
   */
  setChecklist: (id, { items, groups } = {}) =>
    api.post(`/tasks/${id}/checklist/`, {
      ...(items === undefined ? {} : { items }),
      ...(groups === undefined ? {} : { groups }),
    }).then((r) => r.data),
  /** Returns the item AND the recalculated progress, so one call updates both. */
  tickChecklistItem: (id, itemId, isDone) =>
    api.post(`/tasks/${id}/checklist/${itemId}/tick/`, { is_done: isDone })
      .then((r) => r.data),

  // --- subtasks (Phase TASK-SUBTASKS) ---
  /** The rows also travel inside the task detail payload; this is the refresh. */
  getSubtasks: (id) => api.get(`/tasks/${id}/subtasks/`).then((r) => r.data),
  createSubtask: (id, payload) =>
    api.post(`/tasks/${id}/subtasks/`, payload).then((r) => r.data),
  updateSubtask: (id, subtaskId, payload) =>
    api.patch(`/tasks/${id}/subtasks/${subtaskId}/`, payload).then((r) => r.data),
  deleteSubtask: (id, subtaskId) =>
    api.delete(`/tasks/${id}/subtasks/${subtaskId}/`).then((r) => r.data),
  /**
   * Returns the row AND the task's recalculated progress — while subtasks exist
   * the percentage is derived from them, so one call updates both.
   */
  completeSubtask: (id, subtaskId, isDone) =>
    api.post(`/tasks/${id}/subtasks/${subtaskId}/complete/`, { is_done: isDone })
      .then((r) => r.data),
  /** The whole order, every time: `ids` is the full list in its new sequence. */
  reorderSubtasks: (id, ids) =>
    api.post(`/tasks/${id}/subtasks/reorder/`, { ids }).then((r) => r.data),

  // --- comments (Phase T2.3) ---
  getComments: (id) => api.get(`/tasks/${id}/comments/`).then((r) => r.data),
  /**
   * `mentionIds` are the ids resolved from the picker, not names scraped from
   * the body: the body is plain text, so two colleagues with the same first
   * name are indistinguishable in it.
   */
  addComment: (id, body, { parent = null, mentionIds = [], subtask = null } = {}) =>
    api.post(`/tasks/${id}/comments/`, {
      body,
      ...(parent ? { parent } : {}),
      ...(mentionIds.length ? { mention_ids: mentionIds } : {}),
      // A comment opened from a subtask row stays on that subtask.
      ...(subtask ? { subtask } : {}),
    }).then((r) => r.data),
  editComment: (id, commentId, body) =>
    api.patch(`/tasks/${id}/comments/${commentId}/`, { body }).then((r) => r.data),

  getTimeline: (id) => api.get(`/tasks/${id}/timeline/`).then((r) => r.data),

  // --- attachments and evidence (Phase T2.4 / T2.5) ---
  getAttachments: (id) => api.get(`/tasks/${id}/attachments/`).then((r) => r.data),
  /**
   * Upload one or more files. Multipart, and `files` is repeated rather than
   * sent as an array field, because that is what DRF's request.FILES.getlist()
   * reads.
   */
  uploadAttachments: (id, files, { isEvidence = true, caption = '', subtask = null } = {}) => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    form.append('is_evidence', isEvidence ? 'true' : 'false');
    if (caption) form.append('caption', caption);
    if (subtask) form.append('subtask', subtask);
    return api.post(`/tasks/${id}/attachments/`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data);
  },
  /** Evidence that lives somewhere else — a dashboard, a published page. */
  addLink: (id, linkUrl, { caption = '', isEvidence = true } = {}) =>
    api.post(`/tasks/${id}/attachments/links/`, {
      link_url: linkUrl, caption, is_evidence: isEvidence,
    }).then((r) => r.data),
  /** Soft: the row and its history survive, it leaves the lists. */
  removeAttachment: (id, attachmentId) =>
    api.delete(`/tasks/${id}/attachments/${attachmentId}/`).then((r) => r.data),
  /** Move a row between Evidence and General Attachment. */
  flagAttachment: (id, attachmentId, isEvidence) =>
    api.post(`/tasks/${id}/attachments/${attachmentId}/flag/`,
      { is_evidence: isEvidence }).then((r) => r.data),
  /** Who has opened this file. Owner-only, server-side. */
  getDownloadLog: (id, attachmentId) =>
    api.get(`/tasks/${id}/attachments/${attachmentId}/downloads/`)
      .then((r) => r.data),

  // --- analytics (Phase T5) ---
  /**
   * Every analytics call is scoped SERVER-side to the caller's visible tasks,
   * so none of these needs a role check here. The two per-person views refuse
   * an employee outright rather than narrowing — a per-person table of one
   * person is not a per-person table.
   */
  getExecutive: (params = {}) =>
    api.get('/task-analytics/executive/', { params }).then((r) => r.data),
  getHealth: (params = {}) =>
    api.get('/task-analytics/health/', { params }).then((r) => r.data),
  getDepartmentAnalytics: (params = {}) =>
    api.get('/task-analytics/departments/', { params }).then((r) => r.data),
  getEmployeeAnalytics: (params = {}) =>
    api.get('/task-analytics/employees/', { params }).then((r) => r.data),
  getReviewerAnalytics: (params = {}) =>
    api.get('/task-analytics/reviewers/', { params }).then((r) => r.data),
  getTrend: (params = {}) =>
    api.get('/task-analytics/trend/', { params }).then((r) => r.data),
  /** Values AND the catalogue, so no client holds its own copy of a definition. */
  getKpis: (params = {}) =>
    api.get('/task-analytics/kpis/', { params }).then((r) => r.data),

  // --- appraisal evidence (Phase T5.7 / T5.8) ---
  /** Read-only. Counts and durations; nothing scored, weighted or compared. */
  getEvidence: (params = {}) =>
    api.get('/task-analytics/evidence/', { params }).then((r) => r.data),
  getEvidenceSnapshots: (params = {}) =>
    api.get('/task-analytics/evidence/snapshots/', { params }).then((r) => r.data),
  /** The schema itself — keys, units, definitions, contract version. */
  getEvidenceContract: () =>
    api.get('/task-analytics/evidence/contract/').then((r) => r.data),
  /** The same numbers in the shared cross-module shape. */
  getEvidenceStandard: (params = {}) =>
    api.get('/task-analytics/evidence/standard/', { params }).then((r) => r.data),
  /** A manager's direct reports. Ordered by name, server-side. */
  getTeamEvidence: (params = {}) =>
    api.get('/task-analytics/evidence/team/', { params }).then((r) => r.data),

  // --- templates (Phase T2.9) ---
  getTemplates: (params = {}) =>
    api.get('/task-templates/', { params }).then((r) => r.data),
  getTemplate: (templateId) =>
    api.get(`/task-templates/${templateId}/`).then((r) => r.data),
  createTemplate: (payload) =>
    api.post('/task-templates/', payload).then((r) => r.data),
  updateTemplate: (templateId, payload) =>
    api.patch(`/task-templates/${templateId}/`, payload).then((r) => r.data),
  /** Retires rather than deletes: tasks raised from it keep a name. */
  retireTemplate: (templateId) =>
    api.delete(`/task-templates/${templateId}/`).then((r) => r.data),
  saveAsTemplate: (id, payload) =>
    api.post(`/tasks/${id}/save-as-template/`, payload).then((r) => r.data),
  applyTemplate: (id, templateId) =>
    api.post(`/tasks/${id}/apply-template/`, { template: templateId })
      .then((r) => r.data),
};

export default taskService;
