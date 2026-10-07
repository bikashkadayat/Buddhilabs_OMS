import React, { useEffect, useMemo, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../../hooks/useAuth';
import { can } from '../../services/roles';
import { memoService } from '../../services/memoService';
import { minuteService } from '../../services/minuteService';
import { circularService } from '../../services/circularService';
import { useCorrectionCounts } from '../../hooks/useWorkforce';

// --- Accordion section state (persisted, multi-open) -----------------------
const STORAGE_KEY = 'nif-sidebar-sections';
const readStore = () => {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch { return {}; }
};
const writeStore = (id, open) => {
  try {
    const s = readStore();
    s[id] = open;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch { /* localStorage unavailable — degrade to in-memory only */ }
};

const Chevron = () => (
  <svg className="sb-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l6-6-6-6" /></svg>
);

const matchesActive = (child, pathname) => {
  const to = child?.props?.to;
  if (!to) return false;
  if (child.props.end) return pathname === to;
  return pathname === to || pathname.startsWith(to.endsWith('/') ? to : `${to}/`);
};

// Collapsible section. Multi-open (each toggles independently). Collapsed by
// default, but auto-expands when it contains the active route. State persists to
// localStorage. If it has no visible (role-gated) items, the whole header hides.
const Section = ({ id, title, children }) => {
  const { pathname } = useLocation();
  const kids = useMemo(() => React.Children.toArray(children).filter(Boolean), [children]);
  const hasActive = useMemo(() => kids.some((k) => matchesActive(k, pathname)), [kids, pathname]);

  const [open, setOpen] = useState(() => {
    const stored = readStore()[id];
    return stored === undefined ? hasActive : stored; // collapsed by default unless active
  });

  // Navigating INTO this section auto-expands it (only on the false→true edge, so
  // a user who manually collapsed the current section isn't fought). The edge is
  // detected during render against the last value seen; starting from `false`
  // means a section that mounts active expands, as the old mount-time effect
  // did. Persisting is a side effect, so that alone stays in an effect.
  const [wasActive, setWasActive] = useState(false);
  if (wasActive !== hasActive) {
    setWasActive(hasActive);
    if (hasActive) setOpen(true);
  }
  useEffect(() => {
    if (hasActive) writeStore(id, true);
  }, [hasActive, id]);

  if (kids.length === 0) return null; // role-gated to empty → hide section entirely

  const bodyId = `sb-body-${id}`;
  const toggle = () => setOpen((o) => { const n = !o; writeStore(id, n); return n; });

  return (
    <div className="sb-section">
      <button type="button" className="sb-hd-btn" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
        <span className="sb-hd-label">{title}</span>
        <Chevron />
      </button>
      <div id={bodyId} className={`sb-body ${open ? 'open' : ''}`}>
        <div className="sb-body-inner">{kids}</div>
      </div>
    </div>
  );
};

const LeaveSidebar = ({ open = false, onClose }) => {
  const { role } = useAuth();
  // Close the drawer whenever a nav LINK (not a section header) is activated.
  const handleNavClick = (e) => {
    if (e.target.closest('a.sb-item')) onClose?.();
  };
  const canApply = can(role, 'applyLeave');
  const canViewOwnApplications = can(role, 'myApplications');
  const canCreateMemo = can(role, 'createMemo');
  const canViewOwnMemos = can(role, 'myMemos');
  const canReview = ['approver', 'checker', 'admin'].includes(role);
  const canWorkforceTeam = can(role, 'workforceTeam');
  const canWorkforceHR = can(role, 'workforceHR');
  const canWorkforceConflicts = can(role, 'workforceConflicts');
  const canWorkforceReports = can(role, 'workforceReports');
  // Phase 10. `analyticsOrg` is the executive / HR / device tier; `analyticsView`
  // is everything a department head may see, department-scoped by the API.
  const canAnalytics = can(role, 'analyticsView');
  const canAnalyticsOrg = can(role, 'analyticsOrg');
  const canMonitoring = can(role, 'systemMonitoring');
  // Live count of what is waiting on this user. Failing quietly is deliberate:
  // a badge is never worth breaking navigation over.
  const { data: correctionCounts } = useCorrectionCounts();
  // Memo badges. Same posture as the correction badge above: a failure here is
  // swallowed and the badge simply does not render — navigation must never break
  // over a count. Polled rather than pushed so the numbers stay current while a
  // user works a queue in another tab.
  const { data: minuteCounts } = useQuery({
    queryKey: ['minutes', 'dashboard'],
    queryFn: minuteService.getDashboard,
    staleTime: 60_000,
  });

  // Circular badges. Same posture again: swallowed on failure, so a badge that
  // cannot load simply does not render rather than breaking navigation.
  const { data: circularCounts } = useQuery({
    queryKey: ['circulars', 'dashboard'],
    queryFn: circularService.getDashboard,
    staleTime: 60_000,
    retry: false,
  });

  const { data: memoCounts } = useQuery({
    queryKey: ['memos', 'dashboard'],
    queryFn: memoService.getDashboard,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
  const correctionBadge =
    (correctionCounts?.manager_stage || 0) + (correctionCounts?.hr_stage || 0)
    + (correctionCounts?.mine_open || 0);
  const canCheck = role === 'checker' || role === 'admin';
  const isManager = role === 'admin' || role === 'approver';
  const isAdmin = role === 'admin';

  return (
    <nav
      id="app-sidebar"
      className={`sidebar ${open ? 'open' : ''}`}
      role="navigation"
      aria-label="Main navigation"
      onClick={handleNavClick}
    >
      {/* Mobile-only close button (CSS hides it on desktop). */}
      <button type="button" className="sb-close" aria-label="Close menu" onClick={onClose}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>

      <Section id="overview" title="Overview">
        {/* Phase 203. Home and the Work Queue are ADDED here rather than
            replacing anything: the navigation restructure is a later phase, and
            these two must be reachable before it lands. */}
        <NavLink to="/" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>
          </span>
          Home
        </NavLink>
        <NavLink to="/queue" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 11l3 3 8-8"/><path d="M20 12v7H4V5h11"/></svg>
          </span>
          My Work Queue
        </NavLink>
        <NavLink to="/leave" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
          </span>
          Leave dashboard
        </NavLink>
        {/* Phase 111C. Sits in Overview rather than under one module, because a
            draft can belong to any of Memo, Minute or Circular and the person
            looking for it may not remember which. */}
        <NavLink to="/drafts" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
          </span>
          Unfinished Work
        </NavLink>
      </Section>

      <Section id="leave" title="Leave Management">
        {canApply && (
          <NavLink to="/leave/apply" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg>
            </span>
            Apply for Leave
          </NavLink>
        )}
        {canViewOwnApplications && (
          <NavLink to="/leave/my-applications" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/></svg>
            </span>
            My Applications
          </NavLink>
        )}
        {canReview && (
          <NavLink to="/leave/pending" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            </span>
            {canCheck ? 'Review Requests' : 'Pending Requests'}
          </NavLink>
        )}
      </Section>

      {/* Memo module (Phase 3). Every entry is a server-defined scope, so a menu
          shows exactly what the API says the user may see. Only the two personal
          entries are role-gated (Admin has no self-service memos); the rest are
          safe for every role because the API scopes their contents. */}
      <Section id="memos" title="Memo">
        {/* The manual's own menu, in its order (E-memo-manual pp. 2, 16):
            Dashboard, Create Memo, Draft Memo, Department Memo, Draft for Review
            Memo, Archived Memo.

            The narrower queues this module also has - My Pending Actions, Inbox,
            Outbox, Approved, All Memos - stay routed and are reachable from the
            dashboard tiles; they are simply not what the document puts in front of
            a new user. */}
        <NavLink to="/memos" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></span>
          Dashboard
        </NavLink>
        {canCreateMemo && (
          <NavLink to="/memos/create" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg></span>
            Create Memo
          </NavLink>
        )}
        {canViewOwnMemos && (
          <NavLink to="/memos/drafts" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></span>
            Draft Memo
            {memoCounts?.drafts > 0 && <span className="sb-badge is-quiet">{memoCounts.drafts}</span>}
          </NavLink>
        )}
        <NavLink to="/memos/department" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg></span>
          Department Memo
        </NavLink>
        {canViewOwnMemos && (
          <NavLink to="/memos/draft-for-review" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg></span>
            Draft for Review Memo
            {memoCounts?.draft_for_review > 0 && <span className="sb-badge is-quiet">{memoCounts.draft_for_review}</span>}
          </NavLink>
        )}
        <NavLink to="/memos/archived" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg></span>
          Archived Memo
        </NavLink>
        {memoCounts?.pending_actions > 0 && (
          // Kept in the menu only while something is actually waiting: the manual
          // has no such entry, but hiding a live task behind a dashboard tile is
          // worse than one extra row.
          <NavLink to="/memos/pending" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg></span>
            My Pending Actions
            <span className="sb-badge">{memoCounts.pending_actions}</span>
          </NavLink>
        )}
      </Section>

      {/* Minute module. Five entries, not eleven.

          The old menu listed every server scope, which made the reader do the sorting:
          four of the entries each answered part of "is anything wanted from me?", and
          two more split "minutes I wrote" by status. Those are now one entry each -
          Needs My Action and My Minutes - and the narrower queues stay routed for
          anyone who has bookmarked them. */}
      <Section id="minutes" title="Minute">
        <NavLink to="/minutes" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></span>
          Dashboard
        </NavLink>
        <NavLink to="/minutes/needs-me" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/><circle cx="12" cy="12" r="3"/></svg></span>
          Needs My Action
          {minuteCounts?.needs_my_action > 0 && (
            <span className="sb-badge">{minuteCounts.needs_my_action}</span>
          )}
        </NavLink>
        <NavLink to="/minutes/create" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg></span>
          Create Minute
        </NavLink>
        <NavLink to="/minutes/mine" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg></span>
          My Minutes
          {minuteCounts?.my_drafts > 0 && (
            <span className="sb-badge is-quiet">{minuteCounts.my_drafts}</span>
          )}
        </NavLink>
        <NavLink to="/minutes/all" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg></span>
          All Minutes
        </NavLink>
        <NavLink to="/minutes/archived" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg></span>
          Archived
        </NavLink>
      </Section>

      {/* Circular (Phase 50). A circular is an ANNOUNCEMENT rather than a request
          for a decision, so its menus split differently from the memo and minute
          ones: the first four follow a document towards its audience, and the last
          four are about what has reached ME. "Unread" and "My Acknowledgements" are
          separate entries because opening a circular and confirming it are separate
          facts - one the system observes, one the person states. */}
      <Section id="circulars" title="Circular">
        <NavLink to="/circulars" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></span>
          Dashboard
        </NavLink>
        <NavLink to="/circulars/create" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg></span>
          Create Circular
        </NavLink>
        <NavLink to="/circulars/drafts" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg></span>
          Draft Circular
          {circularCounts?.drafts > 0 && <span className="sb-badge is-quiet">{circularCounts.drafts}</span>}
        </NavLink>
        <NavLink to="/circulars/assigned" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg></span>
          Assigned Circular
          {circularCounts?.assigned > 0 && <span className="sb-badge">{circularCounts.assigned}</span>}
        </NavLink>
        <NavLink to="/circulars/ready-for-issue" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4z"/></svg></span>
          Ready For Issue
          {circularCounts?.ready_for_issue > 0 && <span className="sb-badge is-quiet">{circularCounts.ready_for_issue}</span>}
        </NavLink>
        <NavLink to="/circulars/ready-for-broadcast" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/></svg></span>
          Ready For Broadcast
          {circularCounts?.ready_for_broadcast > 0 && <span className="sb-badge is-quiet">{circularCounts.ready_for_broadcast}</span>}
        </NavLink>
        <NavLink to="/circulars/broadcasted" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 11v3a1 1 0 001 1h3l4 4V7L7 11H4a1 1 0 00-1 0z"/><path d="M16 8a5 5 0 010 8"/></svg></span>
          Broadcasted Circular
        </NavLink>
        <NavLink to="/circulars/unread" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4h16a2 2 0 012 2v12a2 2 0 01-2 2H4a2 2 0 01-2-2V6a2 2 0 012-2z"/><path d="M22 6l-10 7L2 6"/></svg></span>
          Unread Circulars
          {circularCounts?.unread > 0 && <span className="sb-badge">{circularCounts.unread}</span>}
        </NavLink>
        <NavLink to="/circulars/my-acknowledgements" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 6L9 17l-5-5"/></svg></span>
          My Acknowledgements
          {circularCounts?.pending_acknowledgement > 0 && <span className="sb-badge">{circularCounts.pending_acknowledgement}</span>}
        </NavLink>
        <NavLink to="/circulars/archived" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg></span>
          Archived Circular
        </NavLink>
        <NavLink to="/circulars/all" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg></span>
          All Circulars
        </NavLink>
      </Section>

      <Section id="inventory" title="Inventory">

        {/* Phase 70. Deliberately NOT role-gated in the sidebar, unlike the four
            entries above: the dashboard returns a personal view for an employee
            and the register for everybody else, so hiding it would lock people
            out of their own assets. The server decides what each page shows. */}
        <NavLink to="/inventory/dashboard" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></span>
          Asset Dashboard
        </NavLink>
        <NavLink to="/inventory/requests" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 2h6a1 1 0 011 1v2H8V3a1 1 0 011-1z"/><path d="M8 5H6a2 2 0 00-2 2v13a2 2 0 002 2h12a2 2 0 002-2V7a2 2 0 00-2-2h-2"/></svg></span>
          Asset Requests
        </NavLink>
        <NavLink to="/inventory/maintenance" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a4 4 0 01-5.6 5.6l-6 6a2 2 0 102.8 2.8l6-6a4 4 0 015.6-5.6l-2.6 2.6-2-2 2.6-2.6z"/></svg></span>
          Maintenance
        </NavLink>
        {['checker', 'approver', 'admin'].includes(role) && (
          <NavLink to="/inventory/reports" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v18h18"/><path d="M7 15v3M12 9v9M17 12v6"/></svg></span>
            Inventory Reports
          </NavLink>
        )}
        {['checker', 'approver', 'admin'].includes(role) && (
          <NavLink to="/inventory" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><path d="M3.27 6.96L12 12.01l8.73-5.05M12 22.08V12"/></svg></span>
            Inventory Items
          </NavLink>
        )}
        {['checker', 'approver', 'admin'].includes(role) && (
          <NavLink to="/inventory/assignment" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg></span>
            Asset Assignment
          </NavLink>
        )}
        {['maker', 'checker', 'approver'].includes(role) && (
          <NavLink to="/inventory/my-assets" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg></span>
            My Assigned Assets
          </NavLink>
        )}
        {['maker', 'checker', 'approver'].includes(role) && (
          <NavLink to="/inventory/my-requests" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M9 15l2 2 4-4"/></svg></span>
            My Take-Out Requests
          </NavLink>
        )}
        {['checker', 'approver', 'admin'].includes(role) && (
          <NavLink to="/inventory/approvals" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg></span>
            Take-Out Approvals
          </NavLink>
        )}
      </Section>

      {/* Phase 9.1 - Workforce. `Section` hides itself when every child is
          role-gated away, so no extra conditional is needed around it. */}
      <Section id="workforce" title="Workforce">
        <NavLink to="/workforce" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/></svg>
          </span>
          My Workforce
        </NavLink>
        <NavLink to="/workforce/corrections" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
          </span>
          Corrections
          {correctionBadge > 0 && <span className="sb-badge">{correctionBadge}</span>}
        </NavLink>
        <NavLink to="/workforce/wfh" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m3 10 9-7 9 7v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M9 21V12h6v9"/></svg>
          </span>
          Work From Home
        </NavLink>
        <NavLink to="/workforce/comp-off" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 8h1a4 4 0 1 1 0 8h-1"/><path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/></svg>
          </span>
          Comp Off
        </NavLink>
        {canWorkforceTeam && (
          <NavLink to="/workforce/team" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/></svg>
            </span>
            Team Dashboard
          </NavLink>
        )}
        {canWorkforceHR && (
          <NavLink to="/workforce/hr" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
            </span>
            HR Command Center
          </NavLink>
        )}
        {canWorkforceConflicts && (
          <NavLink to="/workforce/conflicts" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m10.3 3.9-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.1l-8-14a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></svg>
            </span>
            Conflicts
          </NavLink>
        )}
        {canWorkforceReports && (
          <NavLink to="/workforce/reports" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></svg>
            </span>
            Workforce Reports
          </NavLink>
        )}
      </Section>

      {/* Phase 10 - Executive analytics. `Section` hides itself when every
          child is hidden, so an employee never sees the group at all. */}
      <Section id="analytics" title="Analytics">
        {canAnalyticsOrg && (
          <NavLink to="/analytics/executive" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/></svg>
            </span>
            Executive Dashboard
          </NavLink>
        )}
        {canAnalyticsOrg && (
          <NavLink to="/analytics/hr" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            </span>
            HR KPIs
          </NavLink>
        )}
        {canAnalytics && (
          <NavLink to="/analytics/management" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20V10M18 20V4M6 20v-4"/></svg>
            </span>
            Management KPIs
          </NavLink>
        )}
        {canAnalytics && (
          <NavLink to="/analytics/attendance" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 17l6-6 4 4 8-8"/><path d="M21 7v6h-6"/></svg>
            </span>
            Attendance Trends
          </NavLink>
        )}
        {canAnalytics && (
          <NavLink to="/analytics/departments" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>
            </span>
            Departments
          </NavLink>
        )}
        {canAnalytics && (
          <NavLink to="/analytics/leave" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
            </span>
            Leave Analytics
          </NavLink>
        )}
        {canAnalytics && (
          <NavLink to="/analytics/wfh" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><path d="M9 22V12h6v10"/></svg>
            </span>
            WFH Analytics
          </NavLink>
        )}
        {canAnalytics && (
          <NavLink to="/analytics/comp-off" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 8h1a4 4 0 010 8h-1"/><path d="M2 8h16v9a4 4 0 01-4 4H6a4 4 0 01-4-4z"/><path d="M6 1v3M10 1v3M14 1v3"/></svg>
            </span>
            Comp Off Analytics
          </NavLink>
        )}
        {canAnalyticsOrg && (
          <NavLink to="/analytics/devices" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>
            </span>
            Device Analytics
          </NavLink>
        )}
      </Section>

      {/* Phase 11 - System monitoring. Hidden entirely for anyone who
          cannot act on it. */}
      <Section id="system" title="System">
        {canMonitoring && (
          <NavLink to="/monitoring" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            </span>
            System Health
          </NavLink>
        )}
      </Section>

      <Section id="records" title="My Records">
        <NavLink to="/my-attendance" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
          </span>
          My Attendance
        </NavLink>
        <NavLink to="/leaves/my-history" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 5-5"/></svg>
          </span>
          My History
        </NavLink>
        <NavLink to="/leaves/my-calendar" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
          </span>
          My Calendar
        </NavLink>
        <NavLink to="/leaves/weekly-report" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v18h18"/><rect x="7" y="10" width="3" height="7"/><rect x="14" y="6" width="3" height="11"/></svg>
          </span>
          Weekly Report
        </NavLink>
        <NavLink to="/leaves/monthly-report" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v18h18"/><path d="M19 9l-5 5-4-4-3 3"/></svg>
          </span>
          Monthly Report
        </NavLink>
      </Section>

      <Section id="team" title="Team">
        <NavLink to="/leave/calendar" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
          <span className="sb-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
          </span>
          Team Calendar
        </NavLink>
      </Section>

      {isManager && (
        <Section id="team-records" title="Team Records">
          <NavLink to="/leaves/team-attendance" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
            </span>
            Team Attendance
          </NavLink>
          <NavLink to="/leave/calendar" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
            </span>
            Team Calendar
          </NavLink>
        </Section>
      )}

      {isAdmin && (
        <Section id="admin" title="Administration">
          <NavLink to="/admin/users" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11h-6M19 8v6"/></svg></span>
            User Management
          </NavLink>
          <NavLink to="/admin/leaves/employees" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/></svg></span>
            Employees
          </NavLink>
          <NavLink to="/admin/leaves/policies" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/></svg></span>
            Policies
          </NavLink>
          <NavLink to="/admin/leaves/holidays" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg></span>
            Holidays
          </NavLink>
          <NavLink to="/admin/leaves/departments" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><path d="M6 10v4a2 2 0 002 2h6"/></svg></span>
            Departments
          </NavLink>
          <NavLink to="/admin/leaves/leave-types" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><circle cx="7" cy="7" r="1"/></svg></span>
            Leave Types
          </NavLink>
          <NavLink to="/admin/leaves/bulk-actions" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg></span>
            Bulk Actions
          </NavLink>
        </Section>
      )}

      {['checker', 'approver', 'admin'].includes(role) && (
        <Section id="attendance" title="Attendance">
          {isAdmin && (
            <NavLink to="/admin/biometric-attendance" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
              <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7"/><path d="M12 17a5 5 0 005-5 5 5 0 00-10 0 5 5 0 005 5z"/><circle cx="12" cy="12" r="1"/></svg></span>
              Biometric Attendance
            </NavLink>
          )}
          <NavLink to="/attendance/records" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/><circle cx="12" cy="10" r="3"/></svg></span>
            Attendance Records
          </NavLink>
          {(role === 'approver' || role === 'admin') && (
            <NavLink to="/admin/attendance-reports" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
              <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 5-5"/></svg></span>
              Attendance Reports
            </NavLink>
          )}
        </Section>
      )}

      {isAdmin && (
        <Section id="reports" title="Reports & Analytics">
          <NavLink to="/admin/analytics" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v18h18"/><path d="M18 9l-5 5-3-3-4 4"/></svg></span>
            Analytics
          </NavLink>
          <NavLink to="/reports" end className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M9 15h6M9 11h2"/></svg></span>
            Reports
          </NavLink>
          <NavLink to="/reports/history" className={({ isActive }) => `sb-item ${isActive ? 'on' : ''}`}>
            <span className="sb-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 106 5.3L3 8"/><path d="M12 7v5l4 2"/></svg></span>
            Report History
          </NavLink>
        </Section>
      )}
    </nav>
  );
};

export default LeaveSidebar;
