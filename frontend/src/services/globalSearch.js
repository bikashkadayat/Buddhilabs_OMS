/**
 * Enterprise search (Phase 204 / blueprint §07).
 *
 * Fans out to the module endpoints that ALREADY accept `?search=`, plus two
 * local indexes — navigation and quick actions — that need no network at all.
 *
 * WHAT IS AND IS NOT SEARCHABLE, AND WHY
 *
 * DRF's SearchFilter is installed globally (config/settings.py), but a viewset
 * only honours `?search=` if it declares `search_fields` -- otherwise a request
 * returns 200 with the full unfiltered list, the param silently ignored. So
 * "it returns 200" is not evidence a source is searchable. Leave
 * (`LeaveViewSet.search_fields`), attendance (`AttendanceListView` reads
 * `search` itself) and the asset register (custom `search` handling) were
 * checked, and the first two given search, before being wired up here.
 *
 * Sources carry a `gate`: a source this role may not query is not asked.
 *
 * People search does NOT go through a user viewset. `/memos/employees/` is the
 * approval-matrix picker: IsAuthenticated rather than admin-only, gated at two
 * characters, capped, throttled, and it never returns an email address. That
 * makes it the right directory source for every user, and it needs no backend
 * change.
 *
 * Sources call the shared axios instance directly rather than the module
 * services: they are read-only GETs at the same URLs, and the services take no
 * request config, so routing through them would mean editing four files that
 * this phase has no other reason to touch.
 */
import api from './api';
import { can } from './roles';
import { searchHelp } from './helpContent';

/** Minimum query length. Matches the server's own gate on the directory. */
export const MIN_QUERY = 2;
/** Rows kept per group. The palette is a shortcut, not a report. */
export const PER_GROUP = 5;

export const GROUPS = {
  ACTION: 'Quick actions',
  NAV: 'Navigation',
  MEMO: 'Memos',
  MINUTE: 'Minutes',
  CIRCULAR: 'Circulars',
  TASK: 'Tasks',
  PEOPLE: 'People',
  DEPARTMENT: 'Departments',
  LEAVE: 'Leave',
  ATTENDANCE: 'Attendance',
  ASSET: 'Assets',
  HELP: 'Help',
};

/** Group render order. Local groups first — they resolve instantly. */
export const GROUP_ORDER = [
  GROUPS.ACTION, GROUPS.NAV, GROUPS.PEOPLE, GROUPS.DEPARTMENT, GROUPS.TASK,
  GROUPS.LEAVE, GROUPS.ATTENDANCE, GROUPS.ASSET, GROUPS.MEMO, GROUPS.MINUTE,
  GROUPS.CIRCULAR, GROUPS.HELP,
];

const rows = (payload) => {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
};

const pick = (row, keys, fallback = '') => {
  for (const key of keys) {
    const v = key.split('.').reduce((a, k) => (a == null ? a : a[k]), row);
    if (v !== undefined && v !== null && v !== '') return v;
  }
  return fallback;
};

// ---------------------------------------------------------------------------
// Local index 1 — quick actions
// ---------------------------------------------------------------------------

/**
 * `gate` is a capability from services/roles.js, checked with the SAME can()
 * the sidebar uses. Nothing here invents a permission rule; an action the user
 * could not perform simply is not offered.
 */
export const QUICK_ACTIONS = [
  { id: 'act:memo', label: 'Create memo', to: '/memos/create', gate: 'createMemo', keywords: 'new write draft' },
  { id: 'act:minute', label: 'Create minute', to: '/minutes/create', keywords: 'new meeting write' },
  { id: 'act:circular', label: 'Create circular', to: '/circulars/create', keywords: 'new notice broadcast' },
  { id: 'act:leave', label: 'Apply for leave', to: '/leave/apply', gate: 'applyLeave', keywords: 'holiday absence request' },
  { id: 'act:task', label: 'Create task', to: '/tasks/create', keywords: 'new assign work todo' },
  { id: 'act:asset', label: 'Request an asset', to: '/inventory/requests', keywords: 'equipment laptop hardware' },
  { id: 'act:queue', label: 'Open my work queue', to: '/queue', keywords: 'pending approvals todo' },
  { id: 'act:home', label: 'Open home', to: '/', keywords: 'dashboard start' },
];

// ---------------------------------------------------------------------------
// Local index 2 — navigation
// ---------------------------------------------------------------------------

/**
 * Destinations, each carrying its OLD label as an alias.
 *
 * This is what makes a trimmed sidebar safe: somebody who learned "Draft for
 * Review Memo" still finds it by typing exactly that, even once the menu says
 * "Sent for review". Aliases are searched but never displayed.
 */
export const NAV_INDEX = [
  { id: 'nav:home', label: 'Home', to: '/' },
  { id: 'nav:queue', label: 'My Work Queue', to: '/queue', alias: 'pending approvals inbox todo' },
  { id: 'nav:drafts', label: 'Unfinished Work', to: '/drafts', alias: 'drafts autosave recover resume' },

  // Phase E launchers. Aliased against the OLD section names as well, so
  // somebody who learned "Inventory" or "Workforce" still lands correctly.
  { id: 'nav:l-docs', label: 'Documents', to: '/documents', alias: 'memo minute circular governance launcher' },
  { id: 'nav:l-people', label: 'People & Attendance', to: '/people', alias: 'workforce my records team attendance leave management' },
  { id: 'nav:l-assets', label: 'Assets', to: '/assets', alias: 'inventory equipment register' },
  { id: 'nav:l-reports', label: 'Reports overview', to: '/reports/overview', alias: 'analytics reports and analytics executive kpi', gate: 'reports' },

  { id: 'nav:memos', label: 'Memo overview', to: '/memos', alias: 'memo dashboard' },
  { id: 'nav:memo-pending', label: 'Memos waiting for me', to: '/memos/pending', alias: 'my pending actions' },
  { id: 'nav:memo-drafts', label: 'My memo drafts', to: '/memos/drafts', alias: 'draft memo' },
  { id: 'nav:memo-review', label: 'Memos sent for review', to: '/memos/draft-for-review', alias: 'draft for review memo' },
  { id: 'nav:memo-dept', label: 'Department memos', to: '/memos/department' },
  { id: 'nav:memo-archive', label: 'Memo archive', to: '/memos/archived', alias: 'archived memo' },

  { id: 'nav:minutes', label: 'Minute overview', to: '/minutes', alias: 'minute dashboard' },
  { id: 'nav:minute-me', label: 'Minutes waiting for me', to: '/minutes/needs-me', alias: 'needs my action' },
  { id: 'nav:minute-mine', label: 'My minutes', to: '/minutes/mine' },
  { id: 'nav:minute-all', label: 'All minutes', to: '/minutes/all' },
  { id: 'nav:minute-archive', label: 'Minute archive', to: '/minutes/archived', alias: 'archived minutes' },

  { id: 'nav:tasks', label: 'Task overview', to: '/tasks', alias: 'task dashboard assignment todo' },
  { id: 'nav:task-me', label: 'Tasks waiting for me', to: '/tasks/needs-me', alias: 'needs my action task' },
  { id: 'nav:task-mine', label: 'My tasks', to: '/tasks/mine', alias: 'assigned to me' },
  { id: 'nav:task-by-me', label: 'Tasks assigned by me', to: '/tasks/assigned-by-me', alias: 'i assigned delegated' },
  { id: 'nav:task-team', label: 'Team tasks', to: '/tasks/team', alias: 'department tasks', gate: 'tasksTeam' },
  { id: 'nav:task-due', label: 'Tasks due today', to: '/tasks/due-today', alias: 'due today' },
  { id: 'nav:task-overdue', label: 'Overdue tasks', to: '/tasks/overdue', alias: 'late overdue' },
  { id: 'nav:task-done', label: 'Completed tasks', to: '/tasks/completed', alias: 'finished closed task' },
  { id: 'nav:task-templates', label: 'Task templates', to: '/tasks/templates', alias: 'reusable checklist template onboarding' },
  { id: 'nav:task-board', label: 'Task board', to: '/tasks/board', alias: 'kanban board columns' },
  { id: 'nav:task-calendar', label: 'Task calendar', to: '/tasks/calendar', alias: 'due dates schedule month week day' },
  { id: 'nav:task-workload', label: 'Task workload', to: '/tasks/workload', alias: 'capacity balance who is busy', gate: 'tasksTeam' },
  { id: 'nav:task-overdue-screen', label: 'Chase overdue tasks', to: '/tasks/overdue-screen', alias: 'late chasing overdue follow up' },
  { id: 'nav:task-reports', label: 'Task reports', to: '/tasks/reports', alias: 'completion status department workload overdue report csv', gate: 'tasksTeam' },
  { id: 'nav:task-review-queue', label: 'Task review queue', to: '/tasks/review-queue', alias: 'awaiting review backlog reviewer bottleneck aging' },
  { id: 'nav:task-analytics', label: 'Task performance', to: '/tasks/analytics', alias: 'kpi analytics health trend department ranking executive' },
  { id: 'nav:task-evidence', label: 'My task performance', to: '/tasks/my-record', alias: 'evidence appraisal my record figures completion' },
  { id: 'nav:task-team-evidence', label: 'Team task evidence', to: '/tasks/team-evidence', alias: 'reports direct evidence activity', gate: 'tasksTeam' },
  { id: 'nav:appraisal-mine', label: 'My appraisal', to: '/appraisals', alias: 'performance review goals self assessment appraisal' },
  { id: 'nav:appraisal-goals', label: 'My goals', to: '/appraisals/goals', alias: 'objectives weight target achievement appraisal goals' },
  { id: 'nav:appraisal-evidence', label: 'My work record', to: '/appraisals/evidence', alias: 'my evidence appraisal tasks completion sources cited' },
  { id: 'nav:appraisal-team', label: 'Team reviews', to: '/appraisals/team', alias: 'appraisal direct reports supervisor review pending', gate: 'appraisalTeam' },
  { id: 'nav:appraisal-committee', label: 'Reviews I sit on', to: '/appraisals/committee', alias: 'calibration queue appraisal committee review cross team' },
  { id: 'nav:appraisal-hr', label: 'Appraisal cycle dashboard', to: '/appraisals/hr', alias: 'appraisal completion promotion readiness succession training needs', gate: 'appraisalHR' },
  { id: 'nav:appraisal-cycles', label: 'Appraisal cycles', to: '/appraisals/cycles', alias: 'appraisal cycle period deadline open close', gate: 'appraisalHR' },
  { id: 'nav:appraisal-reports', label: 'Appraisal reports', to: '/appraisals/reports', alias: 'appraisal summary goal completion training development promotion report csv', gate: 'appraisalTeam' },
  { id: 'nav:appraisal-new', label: 'Open an appraisal', to: '/appraisals/new', alias: 'raise appraisal new employee supervisor committee', gate: 'appraisalHR' },

  { id: 'nav:circulars', label: 'Circular overview', to: '/circulars', alias: 'circular dashboard' },
  { id: 'nav:circ-ack', label: 'Circulars to acknowledge', to: '/circulars/my-acknowledgements', alias: 'my acknowledgements' },
  { id: 'nav:circ-unread', label: 'Unread circulars', to: '/circulars/unread' },
  { id: 'nav:circ-assigned', label: 'Circulars assigned to me', to: '/circulars/assigned', alias: 'assigned circular' },
  { id: 'nav:circ-archive', label: 'Circular archive', to: '/circulars/archived', alias: 'archived circular' },

  { id: 'nav:leave', label: 'Leave dashboard', to: '/leave' },
  { id: 'nav:leave-apply', label: 'Apply for leave', to: '/leave/apply', gate: 'applyLeave' },
  { id: 'nav:leave-mine', label: 'My leave applications', to: '/leave/my-applications', gate: 'myApplications' },
  { id: 'nav:leave-pending', label: 'Leave to review', to: '/leave/pending', gate: 'reviewLeave', alias: 'pending requests approvals' },
  { id: 'nav:leave-cal', label: 'Team calendar', to: '/leave/calendar' },
  { id: 'nav:leave-history', label: 'My leave history', to: '/leaves/my-history' },
  { id: 'nav:leave-balance', label: 'Leave balance', to: '/leave/balance', alias: 'remaining days entitlement' },
  { id: 'nav:leave-policy', label: 'Leave policy', to: '/leave/policy', alias: 'rules entitlement' },
  { id: 'nav:calendar', label: 'Nepali calendar', to: '/calendar', alias: 'bs date patro holidays' },
  { id: 'nav:leave-weekly', label: 'Weekly leave report', to: '/leaves/weekly-report', gate: 'reviewLeave' },
  { id: 'nav:leave-monthly', label: 'Monthly leave report', to: '/leaves/monthly-report', gate: 'reviewLeave' },

  { id: 'nav:attendance', label: 'My attendance', to: '/my-attendance', alias: 'punch check in out biometric' },
  { id: 'nav:workforce', label: 'My workforce', to: '/workforce' },
  { id: 'nav:corrections', label: 'Attendance corrections', to: '/workforce/corrections', alias: 'punch correction' },
  { id: 'nav:wfh', label: 'Work from home', to: '/workforce/wfh', alias: 'wfh remote' },
  { id: 'nav:compoff', label: 'Comp off', to: '/workforce/comp-off', alias: 'compensatory' },
  { id: 'nav:team', label: 'Team dashboard', to: '/workforce/team', gate: 'workforceTeam' },

  { id: 'nav:assets-mine', label: 'My assigned assets', to: '/inventory/my-assets', alias: 'laptop equipment device' },
  { id: 'nav:assets-dash', label: 'Asset dashboard', to: '/inventory/dashboard', alias: 'inventory' },
  { id: 'nav:assets-req', label: 'Asset requests', to: '/inventory/requests', alias: 'inventory request' },
  { id: 'nav:assets-appr', label: 'Take-out approvals', to: '/inventory/approvals', alias: 'gate pass takeout', gate: 'assignTask' },
  { id: 'nav:assets-my-takeouts', label: 'My take-out requests', to: '/inventory/my-requests', alias: 'gate pass takeout mine' },
  { id: 'nav:assets-maint', label: 'Maintenance tickets', to: '/inventory/maintenance', alias: 'repair' },

  { id: 'nav:reports', label: 'Build a report', to: '/reports', alias: 'reports custom export', gate: 'reports' },
  { id: 'nav:analytics', label: 'Executive dashboard', to: '/analytics/executive', gate: 'analyticsOrg' },
  { id: 'nav:notifications', label: 'Notifications', to: '/notifications' },
  { id: 'nav:me', label: 'My profile', to: '/me', alias: 'me account summary' },
  { id: 'nav:profile', label: 'Profile details', to: '/profile', alias: 'edit profile photo phone address emergency contact' },
  { id: 'nav:security', label: 'Change password', to: '/profile#security', alias: 'security password' },
  { id: 'nav:sessions', label: 'Signed-in devices', to: '/profile#sessions', alias: 'sessions sign out everywhere devices' },
  { id: 'nav:prefs', label: 'Notification preferences', to: '/notifications?tab=prefs', alias: 'preferences email settings unsubscribe' },
  { id: 'nav:users', label: 'User management', to: '/admin/users', gate: 'userManagement' },
  { id: 'nav:health', label: 'System health', to: '/monitoring', gate: 'systemMonitoring' },
  { id: 'nav:help', label: 'Help center', to: '/help', alias: 'how to faq tutorial guide question' },
  { id: 'nav:support', label: 'Create a support ticket', to: '/help/contact', alias: 'contact support report problem bug issue help desk' },
  { id: 'nav:tickets', label: 'My support tickets', to: '/help/tickets', alias: 'ticket support request reply status' },
  { id: 'nav:features', label: 'Feature requests', to: '/help/features', alias: 'suggest idea integration improvement roadmap' },
  { id: 'nav:updates', label: 'What’s new', to: '/help/updates', alias: 'product updates release notes changelog new features' },
  { id: 'nav:status', label: 'System status', to: '/help/status', alias: 'outage down status incident is it working' },
  { id: 'nav:contact-us', label: 'Contact Buddhi Labs', to: '/help/contact-us', alias: 'contact support team email reach us' },
  { id: 'nav:getting-started', label: 'Getting started', to: '/getting-started', alias: 'setup onboarding checklist', gate: 'userManagement' },
  { id: 'nav:task-drafts', label: 'My task drafts', to: '/tasks/drafts', alias: 'unfinished draft task' },
  { id: 'nav:memo-all', label: 'All memos', to: '/memos/all', gate: 'allMemos' },
  { id: 'nav:memo-rejected', label: 'Returned memos', to: '/memos/rejected', alias: 'rejected memo' },
  { id: 'nav:circ-all', label: 'All circulars', to: '/circulars/all' },
  { id: 'nav:att-records', label: 'Attendance records', to: '/attendance/records', alias: 'team punches daily', gate: 'workforceTeam' },
  { id: 'nav:att-reports', label: 'Attendance reports', to: '/admin/attendance-reports', alias: 'pdf export monthly', gate: 'analyticsOrg' },

  // Administration and settings (admin only).
  { id: 'nav:settings', label: 'Organisation settings', to: '/settings', alias: 'account settings', gate: 'userManagement' },
  { id: 'nav:billing', label: 'Subscription & billing', to: '/settings/subscription', alias: 'plan payment invoice trial', gate: 'userManagement' },
  { id: 'nav:branding', label: 'Branding', to: '/settings/branding', alias: 'logo colours colors theme', gate: 'userManagement' },
  { id: 'nav:domains', label: 'Custom domain', to: '/settings/domains', alias: 'web address dns', gate: 'userManagement' },
  { id: 'nav:employees', label: 'Employees', to: '/admin/leaves/employees', alias: 'staff people directory', gate: 'userManagement' },
  { id: 'nav:departments', label: 'Departments', to: '/admin/leaves/departments', alias: 'teams department head', gate: 'userManagement' },
  { id: 'nav:policies', label: 'Leave policies', to: '/admin/leaves/policies', alias: 'entitlement rules', gate: 'userManagement' },
  { id: 'nav:att-rules', label: 'Attendance rules', to: '/admin/attendance/policies', alias: 'office hours late grace half day overtime shift policy', gate: 'workforceHR' },
  { id: 'nav:leave-types', label: 'Leave types', to: '/admin/leaves/leave-types', alias: 'annual sick casual', gate: 'userManagement' },
  { id: 'nav:holidays', label: 'Holidays & events', to: '/admin/leaves/holidays', alias: 'public holiday calendar events', gate: 'userManagement' },
  { id: 'nav:bulk', label: 'Bulk leave actions', to: '/admin/leaves/bulk-actions', alias: 'approve many reject many', gate: 'userManagement' },
  { id: 'nav:biometric', label: 'Biometric devices', to: '/settings/attendance', alias: 'device punches fingerprint zkteco attendance mode sync', gate: 'userManagement' },
];

/** Case-insensitive substring match over label + aliases + keywords. */
const matchesLocal = (entry, q) => {
  const hay = `${entry.label} ${entry.alias || ''} ${entry.keywords || ''}`.toLowerCase();
  return hay.includes(q);
};

/** Local rows, filtered by query and by the user's own capabilities. */
export const searchLocal = (query, role) => {
  const q = query.trim().toLowerCase();
  const allowed = (e) => !e.gate || can(role, e.gate);
  const actions = QUICK_ACTIONS.filter(allowed).filter((e) => matchesLocal(e, q))
    .slice(0, PER_GROUP)
    .map((e) => ({
      id: e.id, group: GROUPS.ACTION, title: e.label, subtitle: null, to: e.to,
      actions: [{ label: 'Open', to: e.to }],
    }));
  const nav = NAV_INDEX.filter(allowed).filter((e) => matchesLocal(e, q))
    .slice(0, PER_GROUP)
    .map((e) => ({
      id: e.id, group: GROUPS.NAV, title: e.label, subtitle: e.to, to: e.to,
      actions: [{ label: 'Go', to: e.to }],
    }));
  // Help articles, so "how do I…" typed into search finds the answer.
  const help = q.length >= MIN_QUERY
    ? searchHelp(q, role).slice(0, 3).map((a) => ({
      id: `help:${a.slug}`, group: GROUPS.HELP, title: a.title, subtitle: a.summary,
      to: `/help/${a.slug}`, actions: [{ label: 'Read', to: `/help/${a.slug}` }],
    }))
    : [];
  return [...actions, ...nav, ...help];
};

// ---------------------------------------------------------------------------
// Remote sources
// ---------------------------------------------------------------------------

/**
 * Each source declares the request and the row mapping. `signal` is threaded
 * to axios so a superseded query is aborted rather than left to land late and
 * overwrite fresher results.
 */
export const REMOTE_SOURCES = [
  {
    key: 'task',
    group: GROUPS.TASK,
    // Phase T3 Part 9. `search` covers the task number, the title, the
    // description and the department (tasks.views.TaskViewSet.search_fields),
    // and the endpoint is already scoped — so a hit can never be a task the
    // searcher could not open.
    fetch: (q, signal) => api
      .get('/tasks/', { params: { search: q, page_size: PER_GROUP }, signal })
      .then((r) => r.data),
    map: (row) => ({
      id: `task:${row.id}`,
      group: GROUPS.TASK,
      title: pick(row, ['title'], 'Untitled task'),
      // The four facts the specification asks a task hit to surface, beyond the
      // name: number, assignee, priority and status.
      subtitle: [
        pick(row, ['task_number']),
        (row.assignee_names || []).join(', ') || 'Unassigned',
        pick(row, ['priority_label']),
        pick(row, ['status_label', 'status']),
      ].filter(Boolean).join(' · '),
      to: `/tasks/${row.id}`,
      // Tasks are not in the Work Queue, so a hit gets Open and nothing else —
      // rather than an inline action this file would have to authorise itself.
      queueId: null,
    }),
  },
  {
    key: 'memo',
    group: GROUPS.MEMO,
    fetch: (q, signal) => api
      .get('/memos/', { params: { search: q, page_size: PER_GROUP }, signal })
      .then((r) => r.data),
    map: (row) => ({
      id: `memo:${row.id}`,
      group: GROUPS.MEMO,
      title: pick(row, ['subject'], 'Untitled memo'),
      subtitle: [pick(row, ['memo_number']), pick(row, ['status_label', 'status'])]
        .filter(Boolean).join(' · '),
      to: `/memos/${row.id}`,
      queueId: `memo:${row.id}`,
    }),
  },
  {
    key: 'minute',
    group: GROUPS.MINUTE,
    fetch: (q, signal) => api
      .get('/minutes/', { params: { search: q, page_size: PER_GROUP }, signal })
      .then((r) => r.data),
    map: (row) => ({
      id: `minute:${row.id}`,
      group: GROUPS.MINUTE,
      title: pick(row, ['subject', 'title'], 'Untitled minute'),
      subtitle: [pick(row, ['minute_number']), pick(row, ['status_label', 'status'])]
        .filter(Boolean).join(' · '),
      to: `/minutes/${row.id}`,
      queueId: `minute:${row.id}`,
    }),
  },
  {
    key: 'circular',
    group: GROUPS.CIRCULAR,
    fetch: (q, signal) => api
      .get('/circulars/', { params: { search: q, page_size: PER_GROUP }, signal })
      .then((r) => r.data),
    map: (row) => ({
      id: `circular:${row.id}`,
      group: GROUPS.CIRCULAR,
      title: pick(row, ['subject'], 'Untitled circular'),
      subtitle: [pick(row, ['circular_number']), pick(row, ['status_label', 'status'])]
        .filter(Boolean).join(' · '),
      to: `/circulars/${row.id}`,
      queueId: `circular:${row.id}`,
    }),
  },
  {
    key: 'people',
    group: GROUPS.PEOPLE,
    // The approval-matrix directory: IsAuthenticated, >=2 chars, capped,
    // throttled, and it never returns an email address.
    fetch: (q, signal) => api
      .get('/memos/employees/', { params: { search: q }, signal })
      .then((r) => r.data),
    map: (row, role) => ({
      id: `person:${row.id}`,
      group: GROUPS.PEOPLE,
      title: pick(row, ['full_name'], 'Unnamed'),
      subtitle: [pick(row, ['designation']), pick(row, ['department']), pick(row, ['role_display'])]
        .filter(Boolean).join(' · '),
      // Administrators have a person page (the employee record); for
      // everyone else a hit is informational -- there is no page they may
      // open, and a link to "Unauthorized" would be worse than none.
      to: role === 'admin' ? `/admin/leaves/employees/${row.id}` : null,
      queueId: null,
    }),
  },
  {
    // Every department, from the same tree the memo pickers use. Filtered
    // here: the endpoint has no search, and there are tens, not thousands.
    key: 'departments',
    group: GROUPS.DEPARTMENT,
    fetch: (q, signal) => api.get('/memos/departments/', { signal }).then((r) => {
      const flat = [];
      const walk = (list) => (Array.isArray(list) ? list : rows(list)).forEach((d) => {
        flat.push(d);
        if (d.children) walk(d.children);
      });
      walk(r.data);
      const needle = q.toLowerCase();
      return flat.filter((d) => `${d.name || ''} ${d.code || ''}`.toLowerCase().includes(needle));
    }),
    map: (row, role) => ({
      id: `dept:${row.id}`,
      group: GROUPS.DEPARTMENT,
      title: pick(row, ['name'], 'Department'),
      subtitle: [pick(row, ['code']), pick(row, ['head_name'])].filter(Boolean).join(' · '),
      to: role === 'admin' ? '/admin/leaves/departments' : null,
      queueId: null,
    }),
  },
  {
    // Leave requests the caller may already see (the server scopes them:
    // your own; a head's department; HR all) -- search only narrows.
    key: 'leave',
    group: GROUPS.LEAVE,
    fetch: (q, signal) => api
      .get('/leaves/', { params: { search: q, page_size: PER_GROUP }, signal })
      .then((r) => r.data),
    map: (row, role) => ({
      id: `leave:${row.id}`,
      group: GROUPS.LEAVE,
      title: `${pick(row, ['user_name'], 'Leave')} — ${pick(row, ['leave_type_display', 'leave_type'])}`,
      subtitle: [`${pick(row, ['start_date'])} to ${pick(row, ['end_date'])}`,
        pick(row, ['status_display', 'status'])].filter(Boolean).join(' · '),
      to: can(role, 'reviewLeave') ? '/leave/pending' : '/leave/my-applications',
      queueId: `leave:${row.id}`,
    }),
  },
  {
    // Recent attendance by person, for the people who see a team's
    // attendance. An employee's own is one click away already.
    key: 'attendance',
    group: GROUPS.ATTENDANCE,
    gate: 'workforceTeam',
    fetch: (q, signal) => {
      const from = new Date(Date.now() - 6 * 86_400_000).toISOString().slice(0, 10);
      return api.get('/attendance/', { params: { search: q, date_from: from }, signal })
        .then((r) => r.data);
    },
    map: (row) => ({
      id: `att:${row.id}`,
      group: GROUPS.ATTENDANCE,
      title: `${pick(row, ['employee_name'], 'Attendance')} — ${pick(row, ['date'])}`,
      subtitle: [pick(row, ['status_display', 'status']), pick(row, ['department_name'])]
        .filter(Boolean).join(' · '),
      to: '/attendance/records',
      queueId: null,
    }),
  },
  {
    // The asset register, for those who may browse it (the server answers
    // 403 to anyone else, so they are not asked).
    key: 'assets',
    group: GROUPS.ASSET,
    gate: 'assetRegisterRead',
    fetch: (q, signal) => api
      .get('/inventory/items/', { params: { search: q, page_size: PER_GROUP }, signal })
      .then((r) => r.data),
    map: (row) => ({
      id: `asset:${row.id}`,
      group: GROUPS.ASSET,
      title: pick(row, ['name'], 'Asset'),
      subtitle: [pick(row, ['asset_code']), pick(row, ['status_display']),
        typeof row.current_holder === 'string' ? row.current_holder : pick(row, ['current_holder.name'])]
        .filter(Boolean).join(' · '),
      to: `/inventory/items/${row.id}`,
      queueId: null,
    }),
  },
];

/** The remote sources this role may query. */
export const sourcesFor = (role) => REMOTE_SOURCES.filter((s) => !s.gate || can(role, s.gate));

/** Map one source's payload into result rows, dropping anything malformed. */
export const normaliseResults = (source, payload, role) => {
  const out = [];
  for (const row of rows(payload).slice(0, PER_GROUP)) {
    try {
      const item = source.map(row, role);
      if (item?.id) out.push(item);
    } catch { /* one bad row must not cost the group */ }
  }
  return out;
};

/**
 * Decorate results with the actions the WORK QUEUE already knows about.
 *
 * Deliberately not inferred from the search payload. The queue is the
 * server's own answer to "what may this person act on", so borrowing it means
 * search can never offer an Approve the API would refuse — and no permission
 * logic is duplicated here. A record that is not in the queue gets Open only.
 */
export const withQueueActions = (results, queueItems = []) => {
  const byId = new Map(queueItems.filter((i) => !i.resolved).map((i) => [i.id, i]));
  return results.map((r) => {
    const queued = r.queueId ? byId.get(r.queueId) : null;
    const open = r.to ? [{ label: 'Open', to: r.to }] : [];
    if (!queued?.actions?.length) return { ...r, actions: r.actions || open };
    // Actions needing a written remark are NOT offered here. A palette row has
    // nowhere to type a rejection reason, and firing one with an empty remark
    // would put an unexplained decision into the audit trail. Those open the
    // record instead - the same escalation rule the Work Queue follows.
    const inline = queued.actions
      .filter((a) => !a.needsRemark)
      .map((a) => ({ label: a.label, queueAction: a, item: queued }));
    return { ...r, actions: [...open, ...inline] };
  });
};

/** Order and flatten grouped results for rendering and arrow-key traversal. */
export const groupResults = (results) => {
  const grouped = new Map();
  for (const r of results) {
    if (!grouped.has(r.group)) grouped.set(r.group, []);
    grouped.get(r.group).push(r);
  }
  return GROUP_ORDER
    .filter((g) => grouped.get(g)?.length)
    .map((g) => ({ group: g, items: grouped.get(g) }));
};
