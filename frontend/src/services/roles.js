// Business-facing role labels. Internal values stay maker/checker/approver/admin
// (the workflow engine + backend are unchanged); the UI shows clear names.
//
// Ordered by seniority (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE):
//   Employee < Department Head < HR < Board of Directors < Admin
// `bod` mirrors users.User.Roles.BOD. The server (users/roles.py) is what
// enforces every rule below; these only avoid offering what would be refused.
export const ROLES = ['maker', 'checker', 'approver', 'bod', 'admin'];

export const ROLE_LABELS = {
  maker: 'Employee',
  checker: 'Department Head',
  approver: 'HR',
  bod: 'Board of Directors',
  admin: 'Admin',
};

/** Seniority, lowest first. */
export const ROLE_RANK = Object.fromEntries(ROLES.map((r, i) => [r, i]));

export const roleLabel = (r) => ROLE_LABELS[r] || r || '—';

// Org-hierarchy label, independent of the permission role.
export const EMPLOYEE_TYPES = [
  { value: 'employee', label: 'Employee' },
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'manager', label: 'Manager' },
  { value: 'department_head', label: 'Department Head' },
  { value: 'hr_officer', label: 'HR Officer' },
  { value: 'system_admin', label: 'System Admin' },
];

export const employeeTypeLabel = (t) =>
  EMPLOYEE_TYPES.find((x) => x.value === t)?.label || t || '—';

// Contract basis that drives the leave-category engine (distinct from role and
// from employee_type / org rank). Matches users.User.EmploymentType.
export const EMPLOYMENT_TYPES = [
  { value: 'permanent', label: 'Permanent' },
  { value: 'post_probation', label: 'Post-Probation' },
  { value: 'probation', label: 'Probation' },
  { value: 'intern', label: 'Intern' },
  { value: 'volunteer', label: 'Volunteer' },
];

export const employmentTypeLabel = (t) =>
  EMPLOYMENT_TYPES.find((x) => x.value === t)?.label || t || '—';

export const GENDERS = [
  { value: 'undisclosed', label: 'Prefer not to say' },
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
];

// ---------------------------------------------------------------------------
// Central role→capability config (drives sidebar + dashboard). Admin is an
// oversight/management role and has NO personal self-service actions.
// ---------------------------------------------------------------------------
const NON_ADMIN = ['maker', 'checker', 'approver']; // Employee, Dept Head, HR
// The Board applies for leave and authors documents like any member of staff
// (Phase BOD, "documents only") - it is a person with a seat, not a system role.
const PERSONAL = [...NON_ADMIN, 'bod'];

export const PERMISSIONS = {
  applyLeave: PERSONAL,        // Employee, Dept Head, HR, Board (NOT Admin)
  createMemo: PERSONAL,        // + the Board: memos are documents, not operations
  myApplications: PERSONAL,    // personal leave history (NOT Admin)
  myMemos: PERSONAL,           // personal memos (NOT Admin)
  // The Board reviews Department Heads' leave (leaves.approvals).
  reviewLeave: ['checker', 'approver', 'bod', 'admin'],
  allMemos: ['maker', 'checker', 'approver', 'bod', 'admin'],
  userManagement: ['admin'],
  reports: ['checker', 'approver', 'bod', 'admin'],
  // Workforce layer (Phase 9.1). Self-service is everyone; the team view is
  // Dept Head and up; the command centre and conflicts are HR/Admin.
  workforceSelf: PERSONAL,
  // The Board reads organisation-wide attendance summaries, not the HR command
  // centre, which is a desk of confirmations and approvals (workforceHR).
  workforceTeam: ['checker', 'approver', 'bod', 'admin'],
  workforceHR: ['approver', 'admin'],
  workforceConflicts: ['checker', 'approver', 'bod', 'admin'],
  workforceReports: ['checker', 'approver', 'bod', 'admin'],
  // Analytics (Phase 10). Employees have none — this layer is for management,
  // HR leadership and directors, and reports on departments, never on people.
  // `analyticsOrg` is the executive / HR / device tier: the system has no
  // Director role, so "executive" maps to HR + Admin, the same gate the HR
  // command centre uses.
  analyticsView: ['checker', 'approver', 'bod', 'admin'],
  // The Board IS the executive audience this tier was waiting for.
  analyticsOrg: ['approver', 'bod', 'admin'],
  analyticsExport: ['checker', 'approver', 'bod', 'admin'],
  // Device fleet health: infrastructure. Not the Board (analytics.permissions).
  analyticsDevices: ['approver', 'admin'],
  // Phase 11. Infrastructure health, backup state and login-failure counts.
  // HR/Admin only: a department head has no action to take on any of it, and
  // the Board configures no system (users/roles.py can_configure_system).
  systemMonitoring: ['approver', 'admin'],
  // EVERYBODY raises tasks, and since Phase TASK-SIMPLIFICATION there is one
  // kind of task: the creator picks who does it and who reviews it.
  createTask: ['maker', 'checker', 'approver', 'bod', 'admin'],
  // What is left of the old management verb: managing task TEMPLATES and task
  // groups (tasks.permissions.can_manage_templates). Assigning work to another
  // person stopped being gated in Phase TASK-SIMPLIFICATION — anybody may.
  assignTask: ['checker', 'approver', 'admin'],
  // Everybody has tasks, including Admin, so viewing is ungated.
  tasksTeam: ['checker', 'approver', 'bod', 'admin'],
  // Approving completed work. The Board is absent deliberately: it reads every
  // task and decides none of them.
  taskReview: ['checker', 'approver', 'admin'],
  // Every task in the organisation, read-only for the Board
  // (tasks.permissions.has_org_wide_read).
  tasksAll: ['approver', 'bod', 'admin'],
  // Phase APM-03b. Everybody has an appraisal of their own, so "My Appraisal"
  // is ungated — including for Admin, who is a person with a manager like
  // anybody else.
  //
  // `appraisalTeam` is the supervisor's queue and `appraisalHR` the
  // organisation-wide one. Both mirror the SERVER's rule
  // (appraisal.permissions.has_org_scope maps HR to the approver role) and
  // exist only to avoid offering a rail item that leads to an empty page —
  // the enforcement is entirely server-side, and an appraisal is scoped by
  // explicit membership rather than by role in any case.
  appraisalTeam: ['checker', 'approver', 'admin'],
  appraisalHR: ['approver', 'admin'],
  // Phase BOD-ROLE-EXECUTIVE-GOVERNANCE.
  // The organisation-wide appraisal summary, read-only (appraisal.permissions).
  appraisalOverview: ['approver', 'bod', 'admin'],
  // The Board's read-only Executive Dashboard. HR and Admin see it too: it is a
  // view of the organisation, and there is nothing on it only the Board may read.
  executiveDashboard: ['approver', 'bod', 'admin'],
  // Reading every department's assets (inventory.roles.can_browse_register).
  assetRegisterRead: ['checker', 'approver', 'bod', 'admin'],
};

/** Can `role` perform `action`? Central gate for role-based UI. */
export const can = (role, action) => (PERMISSIONS[action] || []).includes(role);
