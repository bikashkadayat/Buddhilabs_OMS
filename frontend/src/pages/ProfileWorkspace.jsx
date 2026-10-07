import React from 'react';
import UserAvatar from '../components/common/UserAvatar.jsx';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../hooks/useAuth';
import { roleLabel } from '../services/roles';
import { attendanceService } from '../services/attendanceService';
import { leaveService } from '../services/leaveService';
import { inventoryService } from '../services/inventoryService';
import { draftService } from '../services/draftService';
import { taskService } from '../services/taskService';
import { appraisalService } from '../services/appraisalService';
import { EMPTY } from '../services/emptyStates';

/**
 * The Profile tab (Phase F), mounted at /me.
 *
 * A personal hub, not a settings page: attendance, leave, assets and unfinished
 * drafts are the four things somebody checks about themselves on a phone.
 *
 * /profile is UNTOUCHED and still does what it always did - this links to it.
 * Replacing it would have meant deleting a page, which the brief forbids.
 */
const ProfileWorkspace = () => {
  const navigate = useNavigate();
  const { user, role, logout } = useAuth();
  const opts = { staleTime: 60_000, retry: false };

  const { data: today } = useQuery({ queryKey: ['attendance', 'today'], queryFn: attendanceService.today, ...opts });
  const { data: balances } = useQuery({ queryKey: ['leaves', 'balances'], queryFn: leaveService.getBalances, ...opts });
  const { data: assets } = useQuery({ queryKey: ['inventory', 'my-assets'], queryFn: inventoryService.myAssets, ...opts });
  const { data: drafts } = useQuery({ queryKey: ['drafts', 'list'], queryFn: draftService.listDrafts, ...opts });
  // Both reuse query keys the rest of the app already populates, so opening
  // this page adds no request when either has been fetched elsewhere.
  const { data: tasks } = useQuery({ queryKey: ['tasks', 'dashboard'], queryFn: taskService.getDashboard, ...opts });
  const { data: appraisal } = useQuery({ queryKey: ['appraisal', 'dashboard'], queryFn: appraisalService.getDashboard, ...opts });

  const rows = balances?.data ?? [];
  const annual = rows.find((b) => /annual/i.test(b.leave_type || ''));
  const sick = rows.find((b) => /sick/i.test(b.leave_type || ''));
  const assetCount = Array.isArray(assets) ? assets.length : 0;
  const draftCount = Array.isArray(drafts) ? drafts.length : (drafts?.results?.length ?? 0);
  const mine = appraisal?.employee;

  const signOut = () => {
    localStorage.setItem('justLoggedOut', 'true');
    logout();
    navigate('/login');
  };

  return (
    <div className="page pw-page">
      <header className="pw-head">
        {/* The same avatar the header and rail show (Phase
            OMS-USER-AVATAR-CONSISTENCY). This page is where the header avatar
            LINKS TO, so showing initials here contradicted the photo the user
            had just clicked. */}
        <UserAvatar className="pw-av" size={48} radius="14px" fontSize={19} />
        <div>
          <h1 className="pw-name">{user?.full_name || user?.username || 'User'}</h1>
          <p className="pw-role">{roleLabel(role)}{user?.email ? ` · ${user.email}` : ''}</p>
        </div>
      </header>

      <section className="pw-card">
        <h2>Attendance today</h2>
        <p className="pw-big">
          In <strong>{today?.check_in_local || '—'}</strong> · Out <strong>{today?.check_out_local || '—'}</strong>
        </p>
        <p className="pw-sub is-status">{today?.status ? today.status.replace(/_/g, ' ') : 'No record yet'}</p>
        <Link to="/my-attendance" className="pw-link">My attendance →</Link>
      </section>

      <section className="pw-card">
        <h2>Leave balance</h2>
        <p className="pw-big">
          Annual <strong>{annual?.remaining ?? '—'}</strong>
          {sick && <> · Sick <strong>{sick.remaining}</strong></>}
        </p>
        <Link to="/leave" className="pw-link">My leave →</Link>
      </section>

      <section className="pw-card">
        <h2>My assets</h2>
        <p className="pw-big"><strong>{assetCount}</strong> in your care</p>
        <Link to="/inventory/my-assets" className="pw-link">View assets →</Link>
      </section>

      <section className="pw-card">
        <h2>Unfinished drafts</h2>
        <p className="pw-big"><strong>{draftCount}</strong> autosaved</p>
        <Link to="/drafts" className="pw-link">Resume →</Link>
      </section>

      {/* Task summary and appraisal status (Phase
          UX-PRODUCTION-FINAL-IMPLEMENTATION). Both read from endpoints the app
          already calls, and both state counts rather than a percentage — there
          is no denominator here that would agree with the Insights page. */}
      <section className="pw-card">
        <h2>My tasks</h2>
        {tasks ? (
          <>
            <p className="pw-big">
              <strong>{tasks.my_tasks ?? 0}</strong> open
              {' \u00b7 '}<strong>{tasks.due_today ?? 0}</strong> due today
              {' \u00b7 '}<strong>{tasks.overdue ?? 0}</strong> late
            </p>
            <p className="pw-sub">{tasks.completed ?? 0} completed</p>
          </>
        ) : (
          <p className="pw-sub">{EMPTY.noTasksAssigned}</p>
        )}
        <Link to="/tasks/mine" className="pw-link">My tasks &rarr;</Link>
      </section>

      <section className="pw-card">
        <h2>My appraisal</h2>
        {mine?.current_appraisal ? (
          <>
            <p className="pw-big">{mine.stage}</p>
            <p className="pw-sub">
              {mine.cycle}
              {mine.awaiting_me ? ' \u00b7 your turn' : ''}
            </p>
          </>
        ) : (
          <p className="pw-sub">{EMPTY.noAppraisalOpen}</p>
        )}
        <Link to="/appraisals" className="pw-link">My appraisal &rarr;</Link>
      </section>

      <div className="pw-actions">
        <Link to="/profile" className="pw-btn">Profile &amp; settings</Link>
        <button type="button" className="pw-btn is-out" onClick={signOut}>Sign out</button>
      </div>
    </div>
  );
};

export default ProfileWorkspace;
