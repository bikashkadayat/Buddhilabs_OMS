import api from './api';

/**
 * Executive analytics API (Phase 10).
 *
 * Read-only. Every endpoint is role-scoped server-side and returns the same
 * envelope — `{ window, scope, generated_at, cached, data }` — so the pages
 * share one loading, error and "as of" treatment.
 *
 * Exports do NOT live here: they go through the existing reports hub
 * (`reportService`), which already provides async generation, signed download
 * URLs, audit logging and retention. `requestExport` below is a thin alias so
 * an analytics page does not have to know that.
 */

const get = async (path, params = {}) => (await api.get(path, { params })).data;

export const ANALYTICS_EXPORTS = [
  { type: 'executive_summary', format: 'pdf', label: 'Executive summary (PDF)',
    orgOnly: true },
  { type: 'department_analytics', format: 'pdf', label: 'Department analytics (PDF)' },
  { type: 'attendance_analytics', format: 'excel', label: 'Attendance analytics (Excel)' },
  { type: 'overtime_analytics', format: 'excel', label: 'Overtime analytics (Excel)' },
  { type: 'workforce_analytics', format: 'csv', label: 'Workforce analytics (CSV)' },
];

export const analyticsService = {
  executive: (params) => get('/analytics/executive/', params),
  hr: (params) => get('/analytics/hr/', params),
  management: (params) => get('/analytics/management/', params),
  attendance: (params) => get('/analytics/attendance/', params),
  departments: (params) => get('/analytics/departments/', params),
  leave: (params) => get('/analytics/leave/', params),
  wfh: (params) => get('/analytics/wfh/', params),
  compOff: (params) => get('/analytics/comp-off/', params),
  devices: (params) => get('/analytics/devices/', params),
  meta: () => get('/analytics/meta/'),

  /** Queue an export. Scope is injected server-side, never sent from here. */
  requestExport: async (reportType, format, params = {}) =>
    (await api.post('/reports/request/', {
      report_type: reportType,
      params: { format, ...params },
    })).data,

  exportStatus: async (id) => (await api.get(`/reports/${id}/status/`)).data,
};

export default analyticsService;
