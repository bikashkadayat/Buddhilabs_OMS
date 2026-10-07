import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Boxes, CalendarCheck, ClipboardList, Eye, FileText, Gavel, ListChecks, Users,
} from 'lucide-react';

import { analyticsService } from '../../services/analyticsService';
import { attendanceService } from '../../services/attendanceService';
import { assetLifecycle } from '../../services/inventoryService';
import { leaveService } from '../../services/leaveService';
import { memoService } from '../../services/memoService';
import { taskService } from '../../services/taskService';
import { useAuth } from '../../hooks/useAuth';
import KpiTile, { KpiGrid } from '../../components/analytics/KpiTile';
import { fmtPct } from '../../components/analytics/chartTheme';

/**
 * The Executive Dashboard - the Board of Directors' front door
 * (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).
 *
 * READ-ONLY BY CONSTRUCTION. There is no control on this page that changes
 * anything: every section is a set of figures and a link to the report behind
 * them. The one decision the Board takes - a Department Head's leave - is shown
 * as a count with a link to the review queue, where the decision actually
 * happens, rather than as a button here.
 *
 * NOTHING IS COMPUTED IN THE BROWSER. Each figure is a field the server already
 * returns from an existing endpoint, scoped by the server to the viewer. For the
 * Board that scope is the whole organisation; the page never asks for it and
 * could not widen it if it did.
 *
 * EACH SECTION FAILS ON ITS OWN. Six independent requests: if one module is
 * slow or refuses, its card says so and the other five still render - an
 * executive picture that goes blank because asset analytics timed out would be
 * the wrong failure.
 */
const Section = ({ icon: Icon, title, to, linkLabel, query, children, testId }) => (
  <section className="exec-card" data-testid={testId} aria-labelledby={`${testId}-title`}>
    <header className="exec-card-head">
      <h2 id={`${testId}-title`}><Icon size={16} aria-hidden="true" /> {title}</h2>
      {to && <Link to={to} className="exec-card-link">{linkLabel}</Link>}
    </header>
    {query.isLoading && <p className="exec-muted">Loading…</p>}
    {query.isError && (
      <p className="exec-muted" role="status">
        Not available right now{query.error?.response?.status === 403 ? ' — not in your view' : ''}.
      </p>
    )}
    {query.data && children(query.data)}
  </section>
);

const Figure = ({ label, value, hint }) => (
  <div className="exec-figure">
    <span className="exec-figure-value">{value ?? '—'}</span>
    <span className="exec-figure-label">{label}</span>
    {hint && <span className="exec-figure-hint">{hint}</span>}
  </div>
);

const ExecutiveDashboard = () => {
  const { role } = useAuth();
  const opts = { staleTime: 60_000, retry: false };

  const decisions = useQuery({ queryKey: ['executive', 'board-decisions'],
    queryFn: leaveService.getPendingApprovals, ...opts });
  const executive = useQuery({ queryKey: ['executive', 'analytics'],
    queryFn: () => analyticsService.executive({ preset: 'mtd' }), ...opts });
  const today = useQuery({ queryKey: ['executive', 'attendance-today'],
    queryFn: attendanceService.dashboard, ...opts });
  const leave = useQuery({ queryKey: ['executive', 'leave'],
    queryFn: () => analyticsService.leave({ preset: 'ytd' }), ...opts });
  const assets = useQuery({ queryKey: ['executive', 'assets'],
    queryFn: assetLifecycle.dashboard, ...opts });
  const memos = useQuery({ queryKey: ['executive', 'memos'],
    queryFn: memoService.getDashboard, ...opts });
  // Phase TASK-GOVERNANCE-HARDENING. Every figure below is a field this
  // endpoint already returns, scoped by the server to the viewer - the Board's
  // scope being the whole organisation. Nothing is summed or averaged here.
  const tasks = useQuery({ queryKey: ['executive', 'tasks'],
    queryFn: () => taskService.getExecutive(), ...opts });

  return (
    <div className="page exec-page" data-testid="executive-dashboard">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Executive Dashboard</h1>
          <p className="lr-page-sub">
            The whole organisation at a glance. Every figure links to the report it
            comes from.
          </p>
        </div>
        <span className="min-status is-muted exec-readonly">
          <Eye size={13} aria-hidden="true" /> Read-only
        </span>
      </div>

      {/* The Board's one decision. */}
      <Section icon={Gavel} testId="exec-decisions" query={decisions}
        title={role === 'bod' ? 'Awaiting the Board' : 'Leave awaiting your decision'}
        to="/leave/pending" linkLabel="Open the review queue">
        {(result) => {
          const rows = result.data || [];
          return (
            <>
              <div className="exec-figures">
                <Figure label="Department Head leave requests" value={rows.length}
                  hint={rows.length ? 'Decided in the review queue' : 'Nothing is waiting'} />
              </div>
              {rows.length > 0 && (
                <ul className="exec-list">
                  {rows.slice(0, 5).map((l) => (
                    <li key={l.id}>
                      <strong>{l.employee}</strong>
                      <span className="exec-muted"> · {l.type} · {l.start} → {l.end}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          );
        }}
      </Section>

      <Section icon={Users} testId="exec-organisation" query={executive}
        title="Organisation this month" to="/analytics/executive" linkLabel="Executive analytics">
        {(result) => {
          const k = result.data?.kpis || {};
          return (
            <KpiGrid>
              <KpiTile label="Headcount" value={k.headcount} />
              <KpiTile label="Departments" value={result.scope?.departments} />
              <KpiTile label="Attendance compliance" value={k.compliance_pct} suffix="%" />
              <KpiTile label="On leave" value={k.leave_pct} suffix="%" />
              <KpiTile label="Department health" value={k.department_health_score} />
            </KpiGrid>
          );
        }}
      </Section>

      {/* Tasks: the five figures the phase names. Department Progress is the
          server's own per-department rows, not a total divided here. */}
      <Section icon={ListChecks} testId="exec-tasks" query={tasks}
        title="Tasks across the organisation" to="/tasks/reports"
        linkLabel="Task reports">
        {(result) => {
          const totals = result.totals || {};
          const completion = (result.kpis || [])
            .find((k) => k.key === 'completion_percent');
          return (
            <>
              <div className="exec-figures">
                <Figure label="Total tasks" value={totals.total} />
                <Figure label="Overdue" value={totals.overdue}
                  hint={totals.overdue ? 'Past due and still open' : 'Nothing is late'} />
                <Figure label="Pending reviews" value={totals.review_backlog}
                  hint={totals.oldest_review_days
                    ? `Oldest waiting ${totals.oldest_review_days} days` : undefined} />
                <Figure label="Completion rate"
                  value={completion ? `${completion.value}%` : undefined}
                  hint={completion?.label} />
              </div>
              <h3 className="exec-subhead">Department progress</h3>
              <ul className="exec-list">
                {(result.department_ranking || []).slice(0, 6).map((row) => (
                  <li key={row.department}>
                    <strong>{row.department}</strong>
                    <span className="exec-muted">
                      {' · '}{row.completed} of {row.total} done
                      {' · '}{fmtPct(row.completion_percent)}
                      {row.overdue ? ` · ${row.overdue} overdue` : ''}
                      {row.low_volume ? ' · few tasks' : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          );
        }}
      </Section>

      <div className="exec-grid">
        <Section icon={CalendarCheck} testId="exec-attendance" query={today}
          title="Attendance today" to="/analytics/attendance" linkLabel="Attendance analytics">
          {(d) => (
            <div className="exec-figures">
              <Figure label="Checked in" value={d.present_now} hint={`of ${d.total_employees}`} />
              <Figure label="On leave" value={d.counts?.on_leave} />
              <Figure label="Work from home" value={d.counts?.wfh} />
              <Figure label="Late" value={d.counts?.late} />
            </div>
          )}
        </Section>

        <Section icon={ClipboardList} testId="exec-leave" query={leave}
          title="Leave this year" to="/analytics/leave" linkLabel="Leave analytics">
          {(result) => (
            <>
              {/* Per type and per department, exactly as the server returns them -
                  not summed here: a total is a figure, and figures come from the
                  server (project rule). */}
              <ul className="exec-list">
                {(result.data?.by_type || []).map((t) => (
                  <li key={t.code}>
                    <strong>{t.label}</strong>
                    <span className="exec-muted"> · {t.days} days · {fmtPct(t.share_pct)}</span>
                  </li>
                ))}
              </ul>
              <ul className="exec-list">
                {(result.data?.by_department || []).slice(0, 4).map((d) => (
                  <li key={d.department_id || d.department}>
                    <strong>{d.department}</strong>
                    <span className="exec-muted"> · {d.leave_days} days · {fmtPct(d.share_pct)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Section>

        <Section icon={Boxes} testId="exec-assets" query={assets}
          title="Assets" to="/inventory/visibility" linkLabel="Asset visibility">
          {(d) => {
            const c = d.counts || {};
            return (
              <div className="exec-figures">
                <Figure label="On the books" value={c.total_assets} />
                <Figure label="Assigned" value={c.assigned} />
                <Figure label="Nobody accountable" value={c.assets_with_no_owner} />
                <Figure label="Transfers awaiting HR" value={c.pending_transfers} />
              </div>
            );
          }}
        </Section>

        <Section icon={FileText} testId="exec-memos" query={memos}
          title="Memos" to="/memos" linkLabel="Memo dashboard">
          {(d) => (
            <div className="exec-figures">
              <Figure label="In circulation" value={d.total_visible} />
              <Figure label="Awaiting approval" value={d.pending_approval} />
              <Figure label="Approved" value={d.approved} />
              <Figure label="Rejected" value={d.rejected} />
            </div>
          )}
        </Section>
      </div>

      <nav className="exec-reports" aria-label="Reports">
        <h2>Reports</h2>
        <ul>
          <li><Link to="/analytics/executive">Executive Dashboard analytics</Link></li>
          <li><Link to="/analytics/departments">Department Dashboard</Link></li>
          <li><Link to="/analytics/leave">Leave Analytics</Link></li>
          <li><Link to="/tasks/reports">Department Performance · Cycle Time · Review Turnaround</Link></li>
          <li><Link to="/analytics/attendance">Attendance Analytics</Link></li>
          <li><Link to="/inventory/reports/department_assets">Asset Analytics</Link></li>
          <li><Link to="/memos">Memo Analytics</Link></li>
        </ul>
      </nav>
    </div>
  );
};

export default ExecutiveDashboard;
