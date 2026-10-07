import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle, CalendarClock, CheckCircle2, ClipboardList, Building2,
  Inbox, Plus, ShieldCheck, Users,
} from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { taskService } from '../../services/taskService';
import ChartFrame from '../../components/analytics/ChartFrame';
import DistributionPieChart from '../../components/analytics/DistributionPieChart';
import { can } from '../../services/roles';
import { statusTone } from '../../components/task/taskLabels';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * A dashboard tile. `tone` drives only the accent; the number and label carry
 * the meaning, so the tiles stay readable without colour.
 */
const Tile = ({ label, value, to, icon, tone = 'neutral', hint }) => (
  <Link to={to} className={`memo-tile tone-${tone}`}>
    <span className="memo-tile-ico" aria-hidden="true">{icon}</span>
    <span className="memo-tile-value">{value ?? '—'}</span>
    <span className="memo-tile-label">{label}</span>
    {hint && <span className="memo-tile-hint">{hint}</span>}
  </Link>
);

/**
 * Task module dashboard.
 *
 * The specification gives four different dashboards — Employee, HR, Department
 * Head, Admin. They are not four pages: they are one page whose ROLE SECTIONS
 * are additive, because the roles are additive in real life. An HR officer is
 * also an employee with tasks of their own, and a dashboard that replaced their
 * personal tiles with organisational ones is why people keep a second list on
 * paper.
 *
 * Which sections appear is decided by the SERVER — the payload simply does not
 * carry `team_tasks` for an employee — so the page cannot show a tile the API
 * would not fill, and the counts are computed over the caller's own visible set.
 */
const TaskDashboard = () => {
  const navigate = useNavigate();
  const { role } = useAuth();

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'dashboard'],
    queryFn: taskService.getDashboard,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  // The donut (Phase T5.1, Part 2). Built from the SAME dashboard payload the
  // tiles above it read — no second request and no arithmetic, so the chart can
  // never disagree with the numbers beside it.
  //
  // Zero-value slices are dropped: a donut with a 0% "Blocked" wedge draws a
  // label pointing at nothing, and "no blocked work" is already said by the
  // tile. An all-zero dataset renders the frame's empty state instead.
  const distribution = [
    { label: 'Open', value: data?.my_tasks ?? 0 },
    { label: 'Completed', value: data?.completed ?? 0 },
    { label: 'In review', value: data?.pending_review ?? 0 },
    { label: 'Overdue', value: data?.overdue ?? 0 },
    { label: 'Blocked', value: data?.blocked ?? 0 },
  ].filter((slice) => slice.value > 0);

  const hasTeam = data?.team_tasks !== undefined;
  const hasOrg = data?.org_total !== undefined;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Tasks</h1>
          <p className="lr-page-sub">
            Your work, your team's, and what is waiting on whom
          </p>
        </div>
        {can(role, 'createTask') && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => navigate('/tasks/create')}>
            <Plus size={14} /> Create Task
          </button>
        )}
      </div>

      {/* Employee — the specification's four, plus the queue the badge reads. */}
      <div className="memo-tiles">
        <Tile label="Waiting for Me" value={data?.needs_my_action}
          to="/tasks/needs-me" icon={<Inbox size={18} />} tone="urgent"
          hint="To accept, or in flight" />
        <Tile label="My Tasks" value={data?.my_tasks} to="/tasks/mine"
          icon={<ClipboardList size={18} />} tone="info"
          hint="Everything live that is yours" />
        <Tile label="Due Today" value={data?.due_today} to="/tasks/due-today"
          icon={<CalendarClock size={18} />} tone="urgent"
          hint="Yours, dated today" />
        <Tile label="Overdue" value={data?.overdue} to="/tasks/overdue"
          icon={<AlertTriangle size={18} />} tone="danger"
          hint="Past due and still open" />
        <Tile label="Completed" value={data?.completed} to="/tasks/completed"
          icon={<CheckCircle2 size={18} />} tone="ok"
          hint="Approved or closed" />
        {data?.assigned_by_me > 0 && (
          <Tile label="Assigned By Me" value={data?.assigned_by_me}
            to="/tasks/assigned-by-me" icon={<Users size={18} />} tone="neutral"
            hint="Still open" />
        )}
      </div>

      {/* Part 2 (T5.1): the shape of the work, beside the counts of it. The
          tiles answer "how many"; this answers "how is it distributed", which
          is the question a tile grid cannot. Placed AFTER the tiles rather than
          instead of them — the tiles are scoped links people navigate by, and
          a chart is not a substitute for a way through. */}
      {distribution.length > 1 && (
        <ChartFrame title="Where your work sits"
          subtitle="Your open, completed, in-review, overdue and blocked tasks."
          rows={distribution} columns={[
            { key: 'label', label: 'Status' },
            { key: 'value', label: 'Tasks', numeric: true },
          ]}>
          <DistributionPieChart data={distribution} valueKey="value"
            labelKey="label" unit=" tasks" height={240} />
        </ChartFrame>
      )}

      {/* Department Head — team, completion, pending review. */}
      {hasTeam && (
        <section className="memo-dash-section">
          <div className="memo-dash-head">
            <h3>{hasOrg ? 'Organisation Oversight' : 'My Team'}</h3>
            <Link className="memo-dash-more" to="/tasks/team">Team tasks →</Link>
          </div>
          <div className="memo-tiles">
            <Tile label="Team Tasks" value={data?.team_tasks} to="/tasks/team"
              icon={<Users size={18} />} tone="info" hint="Open across the team" />
            <Tile label="Completion" value={`${data?.team_completion_percent ?? 0}%`}
              to="/tasks/completed" icon={<CheckCircle2 size={18} />} tone="ok"
              hint="Closed or approved, of all team tasks" />
            <Tile label="Pending Review" value={data?.pending_review}
              to="/tasks/review-queue" icon={<ShieldCheck size={18} />}
              tone="urgent" hint="Submitted, awaiting a decision" />
            <Tile label="Pending Closure" value={data?.pending_closure}
              to="/tasks/completed" icon={<ShieldCheck size={18} />} tone="warn"
              hint="Approved, awaiting verification" />
            <Tile label="Team Overdue" value={data?.team_overdue} to="/tasks/overdue"
              icon={<AlertTriangle size={18} />} tone="danger"
              hint="Past due across the team" />
          </div>
        </section>
      )}

      {/* HR / Admin — the organisation summary and the department breakdown. */}
      {hasOrg && (
        <section className="memo-dash-section">
          <div className="memo-dash-head">
            <h3>Organisation Summary</h3>
            <Link className="memo-dash-more" to="/tasks/all">All tasks →</Link>
          </div>
          <div className="memo-tiles">
            <Tile label="Open Tasks" value={data?.org_open} to="/tasks/all"
              icon={<ClipboardList size={18} />} tone="info"
              hint="Assigned and not yet finished" />
            <Tile label="Completed Tasks" value={data?.org_completed}
              to="/tasks/completed" icon={<CheckCircle2 size={18} />} tone="ok" />
            <Tile label="Overdue Tasks" value={data?.org_overdue} to="/tasks/overdue"
              icon={<AlertTriangle size={18} />} tone="danger" />
            <Tile label="Completion" value={`${data?.org_completion_percent ?? 0}%`}
              to="/tasks/all" icon={<CheckCircle2 size={18} />} tone="ok"
              hint={`of ${data?.org_total ?? 0} tasks`} />
          </div>

          {data?.by_department?.length > 0 && (
            <div className="lr-table-wrap" style={{ marginTop: 16 }}>
              <table className="lr-table">
                <caption className="sr-only">Tasks by department</caption>
                <thead>
                  <tr>
                    <th scope="col">Department</th>
                    <th scope="col">Open</th>
                    <th scope="col">Total</th>
                    <th scope="col">Completion</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_department.map((row) => {
                    const done = row.total - row.open;
                    const pct = row.total ? Math.round((100 * done) / row.total) : 0;
                    return (
                      <tr key={row.label}>
                        <td style={{ fontWeight: 600 }}>{row.label}</td>
                        <td>{row.open}</td>
                        <td>{row.total}</td>
                        <td style={{ minWidth: 140 }}>
                          <div className="task-bar" role="img"
                            aria-label={`${pct}% complete`}>
                            <span className="task-bar-fill"
                              style={{ width: `${pct}%` }} />
                          </div>
                          <span className="task-sub">{pct}%</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* The three views that left the rail in TASK-MANAGEMENT-ASANA-MODEL, so
          the seven menus the phase names would fit. Off the rail is not out of
          reach: this is their documented way in (navConfig OFF_RAIL). */}
      <nav className="task-more-views" aria-label="More task views">
        <Link to="/tasks/calendar">Calendar</Link>
        <Link to="/tasks/overdue-screen">Overdue work</Link>
        {can(role, 'tasksTeam') && <Link to="/tasks/analytics">Insights</Link>}
        {can(role, 'tasksTeam') && <Link to="/tasks/workload">Workload</Link>}
        <Link to="/tasks/my-record">My task record</Link>
      </nav>

      {/* The breakdown everybody gets, over their own visible set. */}
      {data?.by_status?.length > 0 && (
        <section className="memo-dash-section">
          <div className="memo-dash-head">
            <h3>By Status</h3>
            <span className="memo-tile-hint">
              <Building2 size={12} aria-hidden="true" /> across everything you can see
            </span>
          </div>
          <div className="task-chips">
            {data.by_status.map((row) => (
              <span key={row.value} className={`min-status is-${statusTone(row.value)}`}>
                {row.label}: <b>{row.count}</b>
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  );
};

export default TaskDashboard;
