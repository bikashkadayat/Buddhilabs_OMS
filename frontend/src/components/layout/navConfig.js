/**
 * The navigation tree, declaratively (Phase E / blueprint §06).
 *
 * One place that answers "what does this person see, and where". Replaces a
 * 42 KB component that rendered 13 sections and 76 links on every screen.
 *
 * TWO RULES THIS FILE EXISTS TO KEEP
 *
 * 1. EVERY GATE IS MOVED, NOT REWRITTEN. Each `gate` below is the same
 *    expression LeaveSidebar.jsx used, copied verbatim - including the ones
 *    that look inconsistent. `reports` is gated on isAdmin rather than
 *    can(role,'reports') because that is what shipped; widening it would be a
 *    visibility change wearing the costume of a refactor. Noted in the phase
 *    report as something to decide separately.
 *
 * 2. NOTHING IS REMOVED. Items dropped from a rail stay routed and stay in the
 *    search index. "Fewer links on screen" must never become "fewer places you
 *    can get to" - the routes are pinned by test/routes.parity.test.js.
 */
import { can } from '../../services/roles';

// --- gates, copied verbatim from LeaveSidebar.jsx --------------------------
const gApply = (r) => can(r, 'applyLeave');
const gOwnApplications = (r) => can(r, 'myApplications');
const gCreateMemo = (r) => can(r, 'createMemo');
const gOwnMemos = (r) => can(r, 'myMemos');
const gReview = (r) => can(r, 'reviewLeave');
const gWorkforceTeam = (r) => can(r, 'workforceTeam');
const gWorkforceHR = (r) => can(r, 'workforceHR');
const gAnalytics = (r) => can(r, 'analyticsView');
const gAnalyticsOrg = (r) => can(r, 'analyticsOrg');
const gMonitoring = (r) => can(r, 'systemMonitoring');
const gTaskTeam = (r) => can(r, 'tasksTeam');
const gTaskAll = (r) => can(r, 'tasksAll');
const gTaskReview = (r) => can(r, 'taskReview');
const gAppraisalTeam = (r) => can(r, 'appraisalTeam');
const gAppraisalHR = (r) => can(r, 'appraisalHR');
const gAdmin = (r) => r === 'admin';
// Phase BOD-ROLE-EXECUTIVE-GOVERNANCE. The workspace Reports entry was Admin-only;
// the Board reads every report, so it gets the entry too. HR is unchanged here -
// HR's reports stay reachable from its own module rails, as before.
const gReportsRail = (r) => r === 'admin' || r === 'bod';
const gExecutive = (r) => r === 'bod';
const gAssetReports = (r) => can(r, 'assetRegisterRead');

/** Badge selectors. Read the cached dashboard payloads; never fetch. */
const b = {
  memoPending: (c) => c.memo?.pending_actions || null,
  memoDrafts: (c) => c.memo?.drafts || null,
  memoReview: (c) => c.memo?.draft_for_review || null,
  minuteAction: (c) => c.minute?.needs_my_action || null,
  taskAction: (c) => c.task?.needs_my_action || null,
  taskOverdue: (c) => c.task?.overdue || null,
  taskReview: (c) => c.task?.pending_review || null,
  minuteDrafts: (c) => c.minute?.my_drafts || null,
  circAck: (c) => c.circular?.pending_acknowledgement || null,
  circUnread: (c) => c.circular?.unread || null,
  circAssigned: (c) => c.circular?.assigned || null,
  circDrafts: (c) => c.circular?.drafts || null,
  corrections: (c) => {
    const t = (c.corrections?.manager_stage || 0) + (c.corrections?.hr_stage || 0)
      + (c.corrections?.mine_open || 0);
    return t || null;
  },
  appraisalMine: (c) => (c.appraisal?.awaiting_me ? 1 : null),
  appraisalTeam: (c) => c.appraisal?.pending_reviews || null,
  queue: (c) => c.queue || null,
  drafts: (c) => c.drafts || null,
};

/**
 * The workspace rail: eight entries, the whole product at a glance.
 * Two verbs, four launchers, one system entry.
 */
const WORKSPACE = [
  { key: 'home', label: 'Home', to: '/', end: true, icon: 'home' },
  // The Board's front door (Phase BOD): one read-only picture of the organisation.
  { key: 'executive', label: 'Executive Dashboard', to: '/executive', icon: 'insights', gate: gExecutive },
  { key: 'queue', label: 'My Work Queue', to: '/queue', icon: 'queue', badge: b.queue },
  { key: 'drafts', label: 'Unfinished Work', to: '/drafts', icon: 'drafts', badge: b.drafts, quiet: true },
  { group: 'GO TO' },
  { key: 'tasks', label: 'Tasks', to: '/tasks', icon: 'tasks', badge: b.taskAction },
  { key: 'documents', label: 'Documents', to: '/documents', icon: 'documents' },
  { key: 'people', label: 'People & Attendance', to: '/people', icon: 'people' },
  { key: 'assets', label: 'Assets', to: '/assets', icon: 'assets' },
  /* The Bikram Sambat calendar, on its own page at /calendar.
     It is NOT the leave calendar wearing a different label: that one
     (/leaves/my-calendar) answers "when am I off", this one answers "what day
     is it and what falls on it". Somebody opening a patro to check Dashain
     should not be shown their own annual leave beside it, so this page fetches
     no leave data at all. */
  { key: 'calendar', label: 'Nepali Calendar', to: '/calendar', icon: 'calendar' },
  { key: 'reports', label: 'Reports', to: '/reports/overview', icon: 'reports', gate: gReportsRail },
  { group: 'SYSTEM' },
  { key: 'admin', label: 'Administration', to: '/admin/users', icon: 'admin', gate: gAdmin },
];

/**
 * Module rails. Each is the module's own menu plus a way back.
 *
 * Where an entry was dropped relative to the old sidebar it is noted, with
 * where it now lives. It is always still routed.
 */
export const CONTEXTS = {
  workspace: { key: 'workspace', items: WORKSPACE },

  memo: {
    key: 'memo',
    title: 'Memo',
    back: { label: '← Back to Documents', to: '/documents' },
    items: [
      { key: 'm-overview', label: 'Overview', icon: 'overview', to: '/memos', end: true },
      { key: 'm-waiting', label: 'Waiting for me', icon: 'waiting', to: '/memos/pending', badge: b.memoPending },
      { key: 'm-create', label: 'Create memo', icon: 'memo-create', to: '/memos/create', gate: gCreateMemo },
      { key: 'm-drafts', label: 'My drafts', icon: 'memo-drafts', to: '/memos/drafts', gate: gOwnMemos, badge: b.memoDrafts, quiet: true },
      { key: 'm-review', label: 'Sent for review', icon: 'memo-review', to: '/memos/draft-for-review', gate: gOwnMemos, badge: b.memoReview, quiet: true },
      { key: 'm-dept', label: 'Department memos', icon: 'building', to: '/memos/department' },
      { key: 'm-archive', label: 'Archive', icon: 'archive', to: '/memos/archived' },
    ],
  },

  minute: {
    key: 'minute',
    title: 'Minute',
    back: { label: '← Back to Documents', to: '/documents' },
    items: [
      { key: 'mi-overview', label: 'Overview', icon: 'overview', to: '/minutes', end: true },
      { key: 'mi-waiting', label: 'Waiting for me', icon: 'waiting', to: '/minutes/needs-me', badge: b.minuteAction },
      { key: 'mi-create', label: 'Create minute', icon: 'minute-create', to: '/minutes/create' },
      { key: 'mi-mine', label: 'My minutes', icon: 'minute-mine', to: '/minutes/mine', badge: b.minuteDrafts, quiet: true },
      { key: 'mi-all', label: 'All minutes', icon: 'folder', to: '/minutes/all' },
      { key: 'mi-archive', label: 'Archive', icon: 'archive', to: '/minutes/archived' },
    ],
  },

  task: {
    key: 'task',
    title: 'Tasks',
    back: { label: '← Back to workspace', to: '/' },
    // The specification's module structure, one rail entry each. Team Tasks is
    // gated because an Employee has no team and the endpoint correctly returns
    // nothing for them - a row that is always empty is a row that teaches
    // people the rail is unreliable.
    //
    // Phase TASK-MANAGEMENT-ASANA-MODEL names the seven: Dashboard, My Tasks,
    // Team Tasks, All Tasks, Task Board, Reviews, Reports. That is the whole
    // budget, so three items left the rail and are reached from the Dashboard
    // instead (Calendar, Overdue, Insights - see OFF_RAIL below). They were the
    // right ones to move: each is a view of work the first seven already list,
    // whereas every rail item here answers a different question about WHOSE
    // work it is.
    //
    // The alternative was raising the cap, which is how a rail becomes the
    // 76-link sidebar this navigation replaced.
    items: [
      { key: 't-overview', label: 'Dashboard', icon: 'overview', to: '/tasks', end: true },
      { key: 't-mine', label: 'My tasks', icon: 'task-mine', to: '/tasks/mine', badge: b.taskAction },
      { key: 't-team', label: 'Team tasks', icon: 'task-team', to: '/tasks/team', gate: gTaskTeam },
      { key: 't-all', label: 'All tasks', icon: 'task-all', to: '/tasks/all', gate: gTaskAll },
      { key: 't-board', label: 'Task board', icon: 'task-board', to: '/tasks/board' },
      { key: 't-reviews', label: 'Reviews', icon: 'task-review', to: '/tasks/review-queue', gate: gTaskReview, badge: b.taskReview },
      { key: 't-reports', label: 'Reports', icon: 'reports', to: '/tasks/reports', gate: gTaskTeam },
    ],
  },

  appraisal: {
    key: 'appraisal',
    title: 'Appraisal',
    // Reached from the People & Attendance overview, not from a GO TO entry
    // and not from the People rail. Both are full: the workspace is capped at
    // nine links per role and a module rail at seven, and those caps are a
    // budget for what somebody can hold at a glance rather than numbers to
    // raise whenever a module ships. Appraisal is a People subject anyway, so
    // it takes the route the HR command centre and conflicts already take —
    // an overview tile, recorded in UNLISTED, indexed for search. Once you are
    // inside /appraisals this rail is what you get.
    back: { label: '← Back to People', to: '/people' },
    // The four experiences the specification names, one rail entry each, in
    // the order somebody grows into them: your own, then your team's, then the
    // committee you sit on, then the organisation's.
    //
    // "My Appraisal" is UNGATED. Everybody has one, including Admin — a rail
    // that hides a person's own record from them because of their permission
    // role would be hiding the one page in this module that is unambiguously
    // theirs.
    //
    // The rest are gated only to avoid rows that are always empty: a row that
    // never has anything in it teaches people the rail is unreliable. Every one
    // is still scoped and enforced server-side.
    //
    // Phase APM-UX: "Calibration" became "Reviews I sit on" and gained a gate.
    // Committee membership is per-appraisal rather than a role, so no gate is
    // exact — but an employee was previously shown a rail item called
    // Calibration that opened an empty page, which is both a word they should
    // never have had to learn and a link that never worked for them. Gating on
    // appraisalTeam is the closest available approximation and errs towards
    // hiding it; anybody on a committee reaches it from the record and from
    // search, which is recorded in UNLISTED.
    items: [
      { key: 'ap-mine', label: 'My appraisal', icon: 'appraisal-mine', to: '/appraisals', end: true, badge: b.appraisalMine },
      { key: 'ap-goals', label: 'My goals', icon: 'goals', to: '/appraisals/goals' },
      { key: 'ap-evidence', label: 'My work record', icon: 'evidence', to: '/appraisals/evidence' },
      { key: 'ap-team', label: 'Team reviews', icon: 'review-team', to: '/appraisals/team', gate: gAppraisalTeam, badge: b.appraisalTeam },
      { key: 'ap-committee', label: 'Reviews I sit on', icon: 'team', to: '/appraisals/committee', gate: gAppraisalTeam },
      { key: 'ap-hr', label: 'Cycle dashboard', icon: 'overview', to: '/appraisals/hr', gate: gAppraisalHR },
      { key: 'ap-cycles', label: 'Cycles', icon: 'cycles', to: '/appraisals/cycles', gate: gAppraisalHR },
    ],
  },

  circular: {
    key: 'circular',
    title: 'Circular',
    back: { label: '← Back to Documents', to: '/documents' },
    // Ten entries become six. The four dropped - ready for issue, ready for
    // broadcast, broadcasted, all - are PUBLISHER states: relevant to the few
    // people who issue circulars, carried on the module Overview as tiles, and
    // reachable by search. Everyone else was carrying four rows that never
    // applied to them.
    items: [
      { key: 'c-overview', label: 'Overview', icon: 'overview', to: '/circulars', end: true },
      { key: 'c-ack', label: 'To acknowledge', icon: 'circular-ack', to: '/circulars/my-acknowledgements', badge: b.circAck },
      { key: 'c-unread', label: 'Unread', icon: 'circular-unread', to: '/circulars/unread', badge: b.circUnread },
      { key: 'c-create', label: 'Create circular', icon: 'circular-create', to: '/circulars/create' },
      { key: 'c-assigned', label: 'Assigned to me', icon: 'inbox', to: '/circulars/assigned', badge: b.circAssigned },
      { key: 'c-archive', label: 'Archive', icon: 'archive', to: '/circulars/archived' },
    ],
  },

  leave: {
    key: 'leave',
    title: 'Leave',
    back: { label: '← Back to People', to: '/people' },
    items: [
      { key: 'l-overview', label: 'Overview', icon: 'overview', to: '/leave', end: true },
      { key: 'l-apply', label: 'Apply for leave', icon: 'leave-apply', to: '/leave/apply', gate: gApply },
      { key: 'l-mine', label: 'My applications', icon: 'leave-mine', to: '/leave/my-applications', gate: gOwnApplications },
      { key: 'l-review', label: 'Review requests', icon: 'waiting', to: '/leave/pending', gate: gReview },
      { key: 'l-calendar', label: 'Team calendar', icon: 'calendar', to: '/leave/calendar' },
      { key: 'l-history', label: 'Leave records', icon: 'leave-records', to: '/leaves/my-history' },
      // Phase LEAVE-POLICY-ENTERPRISE-IMPLEMENTATION. Both were widgets partway
      // down the Overview page; the policy names them as destinations, and
      // "how much leave have I got left" is something people navigate to
      // rather than go hunting for.
      { key: 'l-balance', label: 'Leave balance', icon: 'leave-balance', to: '/leave/balance' },
      { key: 'l-policy', label: 'Leave policy', icon: 'leave-policy', to: '/leave/policy' },
    ],
  },

  people: {
    key: 'people',
    title: 'People & Attendance',
    back: { label: '← Back to workspace', to: '/' },
    // Merges five old sections - Leave Management, Workforce, My Records, Team,
    // Team Records, Attendance - which all describe one subject from different
    // angles. Weekly/monthly reports move to Reports; the HR command centre and
    // conflicts are Overview tiles, gated exactly as before.
    items: [
      { key: 'p-overview', label: 'Overview', icon: 'overview', to: '/people', end: true },
      { key: 'p-attendance', label: 'My attendance', icon: 'attendance', to: '/my-attendance' },
      { key: 'p-leave', label: 'My leave', icon: 'calendar', to: '/leave', end: true },
      { key: 'p-corrections', label: 'Corrections', icon: 'corrections', to: '/workforce/corrections', badge: b.corrections },
      { key: 'p-wfh', label: 'WFH & comp-off', icon: 'wfh', to: '/workforce/wfh' },
      { key: 'p-team', label: 'Team', icon: 'team', to: '/workforce/team', gate: gWorkforceTeam },
      { key: 'p-hr', label: 'HR command centre', icon: 'building', to: '/workforce/hr', gate: gWorkforceHR },
    ],
  },

  assets: {
    key: 'assets',
    title: 'Asset Management',
    back: { label: '← Back to workspace', to: '/' },
    // Phase ASSET-CUSTODY-TRANSFER. Exactly the brief's seven, in its order -
    // which is also this file's ceiling of seven per module.
    //
    // Reports is back in this rail. An earlier phase moved Inventory Reports out
    // to the Reports module on a "reports live in Reports" rule; the custody
    // brief asks for it here by name. It stays in Reports too.
    //
    // My assets, Take-outs, Assignment and Exit Clearance left the rail to make
    // room. None is orphaned: each is a card on the Assets launcher and is in the
    // search index, and all four are recorded in UNLISTED below.
    items: [
      { key: 'a-dashboard', label: 'Dashboard', icon: 'overview', to: '/inventory/dashboard' },
      // Gated like its route: employees were shown a link to a page that
      // answered "Unauthorized".
      { key: 'a-register', label: 'Asset Register', icon: 'register', to: '/inventory', end: true, gate: gAssetReports },
      { key: 'a-requests', label: 'Asset Requests', icon: 'requests', to: '/inventory/requests' },
      { key: 'a-transfer', label: 'Asset Transfer', icon: 'transfer', to: '/inventory/transfers' },
      { key: 'a-return', label: 'Asset Return', icon: 'assetReturn', to: '/inventory/returns' },
      { key: 'a-maint', label: 'Maintenance', icon: 'maintenance', to: '/inventory/maintenance' },
      // Gated: the server lets only managers and supervisors read asset reports,
      // and an ungated link sent every employee to a 403 - caught by a browser
      // sweep logged in as an employee.
      { key: 'a-reports', label: 'Reports', icon: 'reports', to: '/inventory/reports', gate: gAssetReports },
    ],
  },

  reports: {
    key: 'reports',
    title: 'Reports',
    back: { label: '← Back to workspace', to: '/' },
    // Merges Analytics and Reports & Analytics. The nine analytics dashboards
    // become a grid on the launcher rather than nine near-identical rows.
    items: [
      { key: 'r-overview', label: 'Overview', icon: 'overview', to: '/reports/overview', end: true },
      { key: 'r-build', label: 'Build a report', icon: 'reports', to: '/reports', end: true },
      { key: 'r-history', label: 'Report history', icon: 'history', to: '/reports/history' },
      { key: 'r-exec', label: 'Executive', icon: 'insights', to: '/analytics/executive', gate: gAnalyticsOrg },
      { key: 'r-hr', label: 'HR KPIs', icon: 'team', to: '/analytics/hr', gate: gAnalyticsOrg },
      { key: 'r-attendance', label: 'Attendance trends', icon: 'attendance', to: '/analytics/attendance', gate: gAnalytics },
      { key: 'r-leave', label: 'Leave analytics', icon: 'calendar', to: '/analytics/leave', gate: gAnalytics },
    ],
  },

  admin: {
    key: 'admin',
    title: 'Administration',
    back: { label: '← Back to workspace', to: '/' },
    items: [
      { key: 'ad-users', label: 'Users', icon: 'team', to: '/admin/users', gate: gAdmin },
      { key: 'ad-employees', label: 'Employees', icon: 'building', to: '/admin/leaves/employees', gate: gAdmin },
      // Leave policies, leave types, holidays and departments moved to the
      // Settings hub ("Workspace setup"): each is configured a few times a
      // year, and they were taking rail space from the pages an
      // administrator opens every day.
      { key: 'ad-biometric', label: 'Biometric devices', icon: 'biometric', to: '/admin/biometric-attendance', gate: gAdmin },
      { key: 'ad-bulk', label: 'Bulk actions', icon: 'bulk', to: '/admin/leaves/bulk-actions', gate: gAdmin },
      { key: 'ad-health', label: 'System health', icon: 'health', to: '/monitoring', gate: gMonitoring },
      // ONE entry for three account pages -- subscription, branding and
      // custom domain -- reached through the hub at /settings (Phase S9).
      //
      // Phase S8 put "Subscription & billing" here directly, and S9 would
      // have added two more beside it, taking this rail to ten links
      // against a budget of seven. The density test says what to do about
      // that in so many words: merge or demote, do not raise it again. The
      // three also belong together on their own terms -- each is about the
      // ACCOUNT rather than the people in it, which is a different
      // question from everything else under Administration.
      { key: 'ad-org-settings', label: 'Settings', icon: 'branding', to: '/settings', gate: gAdmin },
    ],
  },
};

/**
 * Route prefix -> context. Longest prefix wins, so /reports/overview resolves
 * to reports rather than being shadowed by a shorter match.
 */
const PREFIXES = [
  ['/memos', 'memo'],
  ['/minutes', 'minute'],
  ['/tasks', 'task'],
  ['/appraisals', 'appraisal'],
  ['/circulars', 'circular'],
  ['/leave/', 'leave'],
  ['/leaves/', 'people'],
  ['/my-attendance', 'people'],
  ['/workforce', 'people'],
  ['/people', 'people'],
  ['/inventory', 'assets'],
  ['/assets', 'assets'],
  ['/reports', 'reports'],
  ['/analytics', 'reports'],
  ['/admin', 'admin'],
  ['/attendance', 'admin'],
  ['/monitoring', 'admin'],
  // Settings sat on the workspace rail, so opening it from Administration
  // swapped the menu out from under the administrator.
  ['/settings', 'admin'],
];

/** Which rail belongs to a path. Unknown paths fall back to the workspace. */
export const contextForPath = (pathname = '/') => {
  if (pathname === '/leave') return 'leave';
  const hit = PREFIXES
    .filter(([p]) => pathname === p || pathname.startsWith(p))
    .sort((a, bb) => bb[0].length - a[0].length)[0];
  return hit ? hit[1] : 'workspace';
};

/**
 * Visible items for a context, after gating.
 *
 * Group headers whose every item is gated away are dropped too - an Employee
 * was seeing a "SYSTEM" heading with nothing under it, because Administration
 * is admin-only. A label for an empty set reads as a loading failure.
 */
export const visibleItems = (contextKey, role) => {
  const ctx = CONTEXTS[contextKey] || CONTEXTS.workspace;
  const allowed = ctx.items.filter((i) => i.group || !i.gate || i.gate(role));
  return allowed.filter((item, i) => {
    if (!item.group) return true;
    const next = allowed.slice(i + 1).find((x) => !x.group || x.group);
    return next && !next.group;
  });
};

/**
 * Routes deliberately NOT listed in any rail, and where they are reached.
 *
 * Every one is still registered in App.jsx and still in the search index. This
 * list exists so "why can I not see X in the menu" has a written answer, and so
 * a future phase can re-list one on purpose rather than by accident.
 */
export const UNLISTED = [
  { to: '/leaves/my-calendar', via: 'Leave records · search' },
  { to: '/workforce/conflicts', via: 'People overview tile · search', gate: 'workforceConflicts' },
  { to: '/workforce/reports', via: 'Reports launcher · search', gate: 'workforceReports' },
  { to: '/workforce/comp-off', via: 'WFH & comp-off rail item · search' },
  { to: '/leaves/team-attendance', via: 'Team rail item · search' },
  { to: '/leaves/weekly-report', via: 'Reports · search' },
  { to: '/leaves/monthly-report', via: 'Reports · search' },
  { to: '/memos/all', via: 'Memo overview · search' },
  { to: '/memos/inbox', via: 'Memo overview tile · search' },
  { to: '/memos/outbox', via: 'Memo overview tile · search' },
  { to: '/memos/approved', via: 'Memo overview tile · search' },
  { to: '/memos/rejected', via: 'Memo overview tile · search' },
  { to: '/circulars/all', via: 'Circular overview · search' },
  { to: '/circulars/under-review', via: 'Circular overview tile · search' },
  { to: '/circulars/ready-for-issue', via: 'Circular overview tile · search' },
  { to: '/circulars/ready-for-broadcast', via: 'Circular overview tile · search' },
  { to: '/circulars/broadcasted', via: 'Circular overview tile · search' },
  { to: '/circulars/drafts', via: 'Circular overview tile · search' },
  { to: '/minutes/drafts', via: 'Minute overview tile · search' },
  { to: '/tasks/calendar', via: 'Task dashboard · task board · search' },
  { to: '/tasks/overdue-screen', via: 'Task dashboard · search' },
  { to: '/tasks/analytics', via: 'Task dashboard · task reports · search', gate: 'tasksTeam' },
  { to: '/tasks/drafts', via: 'Task dashboard · search' },
  { to: '/tasks/needs-me', via: 'Task dashboard tile · workspace badge · search' },

  { to: '/tasks/templates', via: 'Task detail · Save as template · search' },
  { to: '/tasks/assigned-by-me', via: 'Task dashboard tile · search' },
  { to: '/tasks/due-today', via: 'Task dashboard tile · calendar · search' },
  { to: '/tasks/completed', via: 'Task dashboard tile · search' },
  { to: '/tasks/overdue', via: 'Overdue screen · dashboard tile · search' },

  // /tasks/create had no entry at all: it was reachable only by typing the URL
  // or finding a button on the dashboard. Phase V1.1 put it in the command
  // palette, which is now its documented way in.
  { to: '/tasks/create', via: 'Command palette (Ctrl+K) · task dashboard button' },
  { to: '/tasks/review-queue', via: 'Home Task Reviews tile, for a reviewer · task dashboard · search' },
  { to: '/tasks/workload', via: 'Insights · task reports · search', gate: 'tasksTeam' },
  { to: '/tasks/my-record', via: 'Task dashboard · search' },
  { to: '/tasks/team-evidence', via: 'Task reports · search' },
  { to: '/appraisals', via: 'People overview tile · Appraisal rail · My day, when a step is yours · search' },
  { to: '/appraisals/team', via: 'People overview tile · Appraisal rail · My day, when a review is yours · search', gate: 'appraisalTeam' },
  { to: '/appraisals/committee', via: 'Appraisal record · search — committee membership is per-appraisal, so the rail item is gated on appraisalTeam and this is the way in for anybody else' },
  { to: '/appraisals/hr', via: 'People overview tile · Appraisal rail · search', gate: 'appraisalHR' },
  { to: '/appraisals/cycles', via: 'Appraisal rail · People overview tile · search', gate: 'appraisalHR' },
  { to: '/appraisals/goals', via: 'Appraisal rail · My appraisal page · search' },
  { to: '/appraisals/evidence', via: 'Appraisal rail · search' },
  { to: '/appraisals/reports', via: 'Cycle dashboard · search', gate: 'appraisalTeam' },
  { to: '/appraisals/new', via: 'Cycle dashboard · cycles page · search', gate: 'appraisalHR' },
  { to: '/inventory/my-requests', via: 'Assets · search' },
  // Phase ASSET-CUSTODY-TRANSFER. /inventory/dashboard and /inventory/reports
  // left this list: both are now in the Asset Management rail. These four took
  // their rail places' room and are reached from the Assets launcher instead.
  { to: '/inventory/my-assets', via: 'Assets launcher card · employee dashboard · search' },
  { to: '/inventory/approvals', via: 'Assets launcher Take-outs card · search' },
  { to: '/inventory/assignment', via: 'Assets launcher card · search' },
  { to: '/inventory/exit-clearance', via: 'Assets launcher card · Exit Clearance report · search' },
  // The Assets rail is full at seven. Disposal is a launcher card and a
  // button on the asset's own Lifecycle panel, which is where someone
  // decides an asset should go.
  { to: '/inventory/disposals', via: "Assets launcher card · asset Lifecycle panel · search" },
  // Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD. The rail is still full at seven;
  // this is the Department Head's front door, so it is the first launcher card.
  { to: '/inventory/visibility', via: 'Assets launcher card · search' },
  { to: '/admin/leaves/leave-types', via: 'Policies page · search' },
  { to: '/admin/leaves/departments', via: 'Employees & departments page · search' },
  { to: '/attendance/records', via: 'Biometric devices page · search' },
  { to: '/admin/attendance-reports', via: 'Reports · search' },
  { to: '/admin/analytics', via: 'Reports · search' },
  { to: '/analytics/management', via: 'Reports launcher · search' },
  { to: '/analytics/departments', via: 'Reports launcher · search' },
  { to: '/analytics/wfh', via: 'Reports launcher · search' },
  { to: '/analytics/comp-off', via: 'Reports launcher · search' },
  { to: '/analytics/devices', via: 'Reports launcher · search' },
  { to: '/notifications', via: 'Header bell · search' },
  // The avatar and the rail identity block go to the HUB (/me); /profile is
  // the account form the hub links on to. Phase
  // UX-PRODUCTION-FINAL-IMPLEMENTATION moved the entry point, so this pair has
  // to say so — a reachability note that names a control which now goes
  // somewhere else is worse than no note.
  { to: '/me', via: 'Header avatar · sidebar identity block · mobile tab bar · search' },
  { to: '/profile', via: 'Profile hub, "Profile & settings" · search' },
];

/** Every destination the config can show, for the route-parity guard. */
export const allDestinations = () => Object.values(CONTEXTS)
  .flatMap((c) => c.items).filter((i) => i.to).map((i) => i.to);
