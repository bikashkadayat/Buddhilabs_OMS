import api from './api';

const list = (res) => (Array.isArray(res.data) ? res.data : (res.data?.results ?? []));

/** Report catalog used by the hub cards and the builder forms. */
export const REPORT_TYPES = [
  { key: 'employee_register', name: 'Employee Leave Register', desc: 'Full register: summary, per-day detail and adjustments.', formats: ['excel'], fields: ['year', 'department'] },
  { key: 'monthly_attendance', name: 'Monthly Attendance', desc: 'Working days, leave days and attendance %, grouped by department.', formats: ['excel', 'pdf'], fields: ['year', 'month', 'department'] },
  { key: 'leave_utilization', name: 'Leave Utilization', desc: 'Usage by type, department and month with trend analysis.', formats: ['pdf'], fields: ['year', 'department'] },
  { key: 'compliance', name: 'Compliance Report', desc: 'High usage, unusual patterns and document-required leaves.', formats: ['pdf'], fields: ['year'] },
  { key: 'audit_trail', name: 'Audit Trail', desc: 'Filterable AuditLog export for HR / regulatory audits.', formats: ['excel'], fields: ['date_from', 'date_to', 'action'] },
  // Phase DEPARTMENT-GOVERNANCE-HARDENING. No date fields: it is a register of
  // who is accountable RIGHT NOW, and a window would imply a history it does
  // not keep.
  { key: 'department_ownership', name: 'Department Ownership', desc: 'Every department, who answers for it, and which have nobody.', formats: ['excel', 'pdf', 'csv'], fields: [] },
];

/**
 * Workforce reports (Phase 9). All three formats, all windowed by from/to.
 * Kept in its own list so the existing hub keeps showing the five leave reports
 * unchanged, while /workforce/reports renders these.
 */
export const WORKFORCE_REPORTS = [
  { key: 'attendance_vs_leave', name: 'Attendance vs Leave', desc: 'Days worked against days on leave, and the overlap that charges a leave day for a day worked.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to', 'department'] },
  { key: 'attendance_vs_wfh', name: 'Attendance vs WFH', desc: 'Approved work-from-home against work-from-home actually recorded.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to', 'department'] },
  { key: 'overtime_summary', name: 'Overtime Summary', desc: 'Hours beyond the policy threshold, per employee. Payroll-ready.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to', 'department'] },
  { key: 'late_arrival_summary', name: 'Late Arrival Summary', desc: 'Late days and minutes. Late means a check-in after 11:45.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to', 'department'] },
  { key: 'shift_utilization', name: 'Shift Utilization', desc: 'Days, hours and lateness grouped by the shift that applied.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to'] },
  { key: 'department_attendance', name: 'Department Attendance', desc: 'Per-department status counts and an attendance percentage.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to'] },
  { key: 'comp_off_report', name: 'Comp Off Summary', desc: 'Earned, confirmed, pending and used compensatory days.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to', 'department'] },
  { key: 'monthly_workforce_summary', name: 'Monthly Workforce Summary', desc: 'The whole month on one page: hours, overtime, leave, WFH and open queues.', formats: ['excel', 'pdf', 'csv'], fields: ['from', 'to'] },
];

/**
 * Analytics exports (Phase 10). One format each, on purpose: every one is laid
 * out for the medium it targets — a narrative one-pager, a multi-sheet
 * workbook, a flat table — so offering all three would ship two broken variants
 * of each. `orgOnly` marks the executive summary, which is organisation-wide by
 * definition and therefore not offered to a department head.
 */
export const ANALYTICS_REPORTS = [
  { key: 'executive_summary', name: 'Executive Summary', desc: 'The one-page picture: headline KPIs, the compliance trend and departments ranked.', formats: ['pdf'], fields: ['from', 'to'], orgOnly: true },
  { key: 'department_analytics', name: 'Department Analytics', desc: 'Every department compared on attendance, leave, WFH, overtime and health.', formats: ['pdf'], fields: ['from', 'to', 'department'] },
  { key: 'attendance_analytics', name: 'Attendance Analytics', desc: 'Four sheets: KPI summary, the trend, department rows and period comparisons.', formats: ['excel'], fields: ['from', 'to', 'department'] },
  { key: 'overtime_analytics', name: 'Overtime Analytics', desc: 'Overtime totals, spread and trend — by department, never per employee.', formats: ['excel'], fields: ['from', 'to', 'department'] },
  { key: 'workforce_analytics', name: 'Workforce Analytics', desc: 'One flat department-grained table of every workforce metric, for modelling.', formats: ['csv'], fields: ['from', 'to', 'department'] },
];

export const reportTypeByKey = (key) =>
  REPORT_TYPES.find((r) => r.key === key)
  || WORKFORCE_REPORTS.find((r) => r.key === key)
  || ANALYTICS_REPORTS.find((r) => r.key === key);

export const reportService = {
  listReports: async (mine = true) => list(await api.get('/reports/', { params: mine ? { mine: 1 } : {} })),
  requestReport: async (reportType, params) => (await api.post('/reports/request/', { report_type: reportType, params })).data,
  getStatus: async (id) => (await api.get(`/reports/${id}/status/`)).data,
  download: async (id) => api.get(`/reports/${id}/download/`, { responseType: 'blob' }),
  getAnalytics: async (year) => (await api.get('/reports/analytics/', { params: { year } })).data,
};

/** Trigger a browser download from a blob axios response. */
export const saveBlob = (response, fallbackName = 'report') => {
  const disposition = response.headers?.['content-disposition'] || '';
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const name = match ? match[1] : fallbackName;
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
};

export default reportService;
