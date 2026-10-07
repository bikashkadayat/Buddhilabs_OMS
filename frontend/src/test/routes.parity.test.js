/**
 * Route parity guard (Phase 203).
 *
 * The whole programme rests on one promise: the workspace changes what is
 * LISTED, never what is REACHABLE. Bookmarks, printed references and
 * notification deep-links must keep working permanently, not for a deprecation
 * window.
 *
 * This test pins every route that existed before Phase A. If a later phase
 * removes one - during the sidebar restructure, most likely - this fails loudly
 * and names it, which is the failure mode worth guarding.
 *
 * Adding routes is fine and expected. Removing one is a deliberate decision that
 * must be made here, in this list, rather than silently in App.jsx.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(join(here, '..', 'App.jsx'), 'utf8');

const declaredPaths = () => {
  const out = new Set();
  for (const m of appSource.matchAll(/path="([^"]*)"/g)) out.add(m[1]);
  return out;
};

/** Every route registered before Phase 203. Do not edit without a decision. */
const BASELINE = [
  '/',
  '/auth/first-login-change-password',
  '/login',
  'admin/analytics',
  'admin/attendance-reports',
  'admin/biometric-attendance',
  'admin/leaves/bulk-actions',
  'admin/leaves/departments',
  'admin/leaves/employees',
  'admin/leaves/employees/:id',
  'admin/leaves/holidays',
  'admin/leaves/leave-types',
  'admin/leaves/policies',
  'admin/users',
  'analytics/attendance',
  'analytics/comp-off',
  'analytics/departments',
  'analytics/devices',
  'analytics/executive',
  'analytics/hr',
  'analytics/leave',
  'analytics/management',
  'analytics/wfh',
  'attendance/records',
  'circulars',
  'circulars/:id',
  'circulars/:id/edit',
  'circulars/all',
  'circulars/archived',
  'circulars/assigned',
  'circulars/broadcasted',
  'circulars/create',
  'circulars/drafts',
  'circulars/my-acknowledgements',
  'circulars/ready-for-broadcast',
  'circulars/ready-for-issue',
  'circulars/under-review',
  'circulars/unread',
  'drafts',
  'inventory',
  'inventory/approvals',
  'inventory/assignment',
  'inventory/dashboard',
  'inventory/items/:id',
  'inventory/maintenance',
  'inventory/my-assets',
  'inventory/my-requests',
  'inventory/reports',
  'inventory/reports/:name',
  'inventory/requests',
  'leave',
  'leave/apply',
  'leave/calendar',
  'leave/my-applications',
  'leave/pending',
  'leaves/monthly-report',
  'leaves/my-calendar',
  'leaves/my-history',
  'leaves/team-attendance',
  'leaves/weekly-report',
  'memos',
  'memos/:id',
  'memos/:id/edit',
  'memos/all',
  'memos/approved',
  'memos/archived',
  'memos/create',
  'memos/department',
  'memos/draft-for-review',
  'memos/drafts',
  'memos/inbox',
  'memos/my',
  'memos/outbox',
  'memos/pending',
  'memos/rejected',
  'minutes',
  'minutes/:id',
  'minutes/:id/edit',
  'minutes/acknowledged',
  'minutes/all',
  'minutes/archived',
  'minutes/create',
  'minutes/draft-for-review',
  'minutes/drafts',
  'minutes/initiated',
  'minutes/involvement',
  'minutes/mine',
  'minutes/my-acknowledgements',
  'minutes/needs-me',
  'minutes/under-process',
  'monitoring',
  'my-attendance',
  'notifications',
  'profile',
  'reports',
  'reports/build/:type',
  'reports/history',
  'unauthorized',
  'workforce',
  'workforce/comp-off',
  'workforce/conflicts',
  'workforce/corrections',
  'workforce/hr',
  'workforce/reports',
  'workforce/team',
  'workforce/wfh',
];

describe('route parity', () => {
  const paths = declaredPaths();

  it('still registers every route that existed before Phase 203', () => {
    const missing = BASELINE.filter((p) => !paths.has(p));
    expect(missing).toEqual([]);
  });

  it('registers the same number of baseline routes it always did', () => {
    expect(BASELINE.length).toBe(106);
  });

  it('adds the Work Queue without removing anything', () => {
    expect(paths.has('queue')).toBe(true);
  });

  it('renders Home at the index rather than redirecting into a module', () => {
    expect(appSource).toMatch(/<Route index element=\{<Home \/>\} \/>/);
    // RoleLanding must no longer be wired in - it is kept on disk only as the
    // one-line rollback for Phase A.
    expect(appSource).not.toMatch(/element=\{<RoleLanding \/>\}/);
  });
});
