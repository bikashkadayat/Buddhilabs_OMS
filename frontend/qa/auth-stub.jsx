/* eslint-disable react-refresh/only-export-components */
/**
 * `useAuth` for the QA harness (Phase 47 item 1).
 *
 * The real hook is `useContext(AuthContext)` with no default, so outside an AuthProvider
 * it returns undefined and `const { user } = useAuth()` throws. That is how the first
 * Phase 47 capture produced a minute-detail page that reported NO OVERFLOW FINDINGS while
 * rendering nothing at all - a blank page has nothing to overflow. The screenshot was
 * 18KB against the dashboard's 240KB, which is the only reason it was caught. Every
 * "clean" result from a harness has to be checked against evidence that the page rendered.
 *
 * Aliased in over the real module by qa/vite.qa.config.js. The identity is the initiator
 * from fixtures.json, so `currentUserId` comparisons in the approval matrix and the action
 * register resolve against a person who really is in the payload.
 */
import React from 'react';
import fixtures from './fixtures.json';
import leaveFixtures from './leave-fixtures.json';
import memoFixtures from './memo-fixtures.json';

/*
 * Phase 100.1: the identity has to depend on the screen.
 *
 * A single fixed user was fine while the harness only covered Memo, Minute, Circular
 * and Inventory, where the fixture initiator can see every screen. The leave module
 * gates on role, and the first run of the new screens proved it: `leave-pending`
 * rendered "Access Denied — Only reviewers may view pending leave requests" in 121
 * characters, and the probe reported it clean, because an access-denied page has
 * nothing to overflow. Measuring the wrong page is indistinguishable from measuring
 * nothing.
 *
 * So each leave screen runs as the person who really opens it: the applicant for
 * their own applications and the apply form, the department head for the approval
 * queue and the review drawer.
 */
const screen = new URLSearchParams(window.location.search).get('screen') || '';

const REVIEWER_SCREENS = new Set(['leave-pending', 'leave-review-drawer']);
const APPLICANT_SCREENS = new Set([
  'leave-dashboard', 'leave-my-applications', 'leave-apply', 'notifications',
]);
/*
 * Phase D1: the same lesson, one module over.
 *
 * The asset register gates its Add button, its Actions column and its whole
 * lifecycle row on MANAGER_ROLES. Captured as the default `maker`, the harness
 * measured a five-column read-only table and reported it clean - and the modal
 * matrix reported `click "Add asset": NOT FOUND` rather than a defect, because
 * there was nothing to click. Run it as whoever actually runs the store.
 */
const STORE_SCREENS = new Set(['inventory-register']);

const asUser = (row, role) => ({
  id: row.id,
  full_name: row.full_name,
  first_name: (row.full_name || '').split(' ')[0],
  username: row.username || row.full_name,
  email: `${(row.username || 'qa')}@nif.test`,
  role: role || row.role,
  department: row.department || 'ENG',
  designation: row.designation || '',
});

let user = fixtures.user;
if (REVIEWER_SCREENS.has(screen)) {
  user = asUser(leaveFixtures.head, 'checker');
} else if (APPLICANT_SCREENS.has(screen)) {
  user = asUser(leaveFixtures.maker, 'maker');
} else if (STORE_SCREENS.has(screen)) {
  user = asUser(fixtures.user, 'admin');
} else if (screen === 'reports-hub') {
  user = asUser(leaveFixtures.hr, 'approver');
} else if (screen === 'memo-actionable') {
  /* MemoDetail decides "is this with me" by comparing the active step's assignee id
     against the logged-in user, so the actionable fixture is only actionable if the
     harness is signed in as that person. */
  const acting = memoFixtures.detail.workflow_steps
    .find((step) => step.sequence === 3)?.assignee;
  if (acting) user = asUser({ ...acting, username: 'qa_memo_supporter' }, 'admin');
}

const value = {
  user: { ...user, name: user.full_name, initials: 'MI', color: '#10B981' },
  role: user.role,
  isAuthenticated: true,
  mustChangePassword: false,
  loading: false,
  error: null,
  login: () => Promise.resolve(user),
  logout: () => Promise.resolve(),
  completePasswordChange: () => {},
};

export const useAuth = () => value;
export const AuthProvider = ({ children }) => <>{children}</>;
export default useAuth;
