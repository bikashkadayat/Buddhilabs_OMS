import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Download, FileText, Info } from 'lucide-react';

import { taskService } from '../../services/taskService';
import ExportButtons from '../../components/common/ExportButtons';
import ChartFrame from '../../components/analytics/ChartFrame';
import TrendChart from '../../components/analytics/TrendChart';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import DistributionPieChart from '../../components/analytics/DistributionPieChart';
import { seriesColour } from '../../components/analytics/chartTheme';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The task performance dashboard (Phase T5, Parts 1, 5, 6, 11).
 *
 * SCOPED, NOT ROLE-GATED
 * ----------------------
 * Every figure comes back scoped to the caller's own visible tasks, so this page
 * is open to everybody and simply shows less to somebody who can see less. An
 * employee looking at it sees these metrics over their OWN work, which is a
 * legitimate view of it. The two per-person tables are the exception — the
 * server refuses those rather than narrowing, and the page renders them only
 * when they came back.
 *
 * EVERY NUMBER CARRIES ITS DEFINITION
 * -----------------------------------
 * The KPI payload ships the sentence each metric is read by, and it is rendered
 * beside the figure rather than kept in documentation. A percentage with no
 * stated denominator is the most reliable way to have a metric misread in a
 * meeting, and these are metrics that get quoted.
 *
 * NOTHING HERE RANKS A PERSON
 * ---------------------------
 * Departments are ranked, because a department is a unit of work with a head
 * accountable for it. The employee table is ordered by name, by the server, and
 * this page does not re-sort it — a table sorted by completion rate is a ranking
 * whatever the header says.
 */

const PERIODS = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
];

const Kpi = ({ metric }) => (
  <span className={`memo-tile tone-${metric.higher_is_better ? 'ok' : 'warn'}`}>
    <span className="memo-tile-value">
      {metric.value === null || metric.value === undefined
        ? '—' : `${metric.value}${metric.unit}`}
    </span>
    <span className="memo-tile-label">
      {metric.label}
      {/* The definition travels with the number, not in a wiki nobody opens. */}
      <span className="task-kpi-info" tabIndex={0} role="note"
        aria-label={metric.definition}>
        <Info size={11} aria-hidden="true" />
        <span className="task-kpi-tip">{metric.definition}</span>
      </span>
    </span>
    <span className="memo-tile-hint">
      {metric.higher_is_better ? 'Higher is better' : 'Lower is better'}
    </span>
  </span>
);

const TaskAnalytics = () => {
  const [period, setPeriod] = useState('weekly');

  const executive = useQuery({
    queryKey: ['tasks', 'analytics', 'executive'],
    queryFn: () => taskService.getExecutive(),
  });
  const health = useQuery({
    queryKey: ['tasks', 'analytics', 'health'],
    queryFn: () => taskService.getHealth(),
  });
  const trend = useQuery({
    queryKey: ['tasks', 'analytics', 'trend', period],
    queryFn: () => taskService.getTrend({ period }),
  });
  // The two per-person views are refused for an employee. `retry: false` and a
  // silent failure, so the page renders without them rather than showing an
  // error for something they were never meant to see.
  const employees = useQuery({
    queryKey: ['tasks', 'analytics', 'employees'],
    queryFn: () => taskService.getEmployeeAnalytics(),
    retry: false,
  });
  const reviewers = useQuery({
    queryKey: ['tasks', 'analytics', 'reviewers'],
    queryFn: () => taskService.getReviewerAnalytics(),
    retry: false,
  });

  if (executive.isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (executive.isError) {
    return (
      <div className="page">
        <ErrorState error={executive.error} onRetry={executive.refetch} />
      </div>
    );
  }

  const totals = executive.data?.totals || {};
  const ranking = executive.data?.department_ranking || [];
  const buckets = trend.data?.buckets || [];
  const statusRows = health.data?.inputs
    ? [
      { label: 'Open', value: health.data.inputs.open },
      { label: 'Completed', value: health.data.inputs.completed },
      { label: 'Blocked', value: health.data.inputs.blocked },
      { label: 'Awaiting review', value: health.data.inputs.awaiting_review },
    ].filter((r) => r.value > 0)
    : [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Task Performance</h1>
          <p className="lr-page-sub">
            Organisation, department and task health — over everything you can see
          </p>
        </div>
        <div className="task-head-badges">
          <ExportButtons
            download={(format) =>
              taskService.downloadReport('executive', format)}
            name="task-executive" />
        </div>
      </div>

      {/* --- Part 1: the headline counts --- */}
      <div className="memo-tiles">
        <span className="memo-tile tone-info">
          <span className="memo-tile-value">{totals.total ?? '—'}</span>
          <span className="memo-tile-label">Total Tasks</span>
          <span className="memo-tile-hint">Drafts excluded</span>
        </span>
        <span className="memo-tile tone-ok">
          <span className="memo-tile-value">{totals.completed ?? '—'}</span>
          <span className="memo-tile-label">Completed</span>
        </span>
        <span className="memo-tile tone-info">
          <span className="memo-tile-value">{totals.open ?? '—'}</span>
          <span className="memo-tile-label">Open</span>
        </span>
        <span className="memo-tile tone-danger">
          <span className="memo-tile-value">{totals.overdue ?? '—'}</span>
          <span className="memo-tile-label">Overdue</span>
        </span>
        <span className="memo-tile tone-warn">
          <span className="memo-tile-value">{totals.blocked ?? '—'}</span>
          <span className="memo-tile-label">Blocked</span>
        </span>
        <span className="memo-tile tone-warn">
          <span className="memo-tile-value">{totals.review_backlog ?? '—'}</span>
          <span className="memo-tile-label">Review Backlog</span>
          <span className="memo-tile-hint">
            Oldest {totals.oldest_review_days ?? 0} days
          </span>
        </span>
        <span className="memo-tile tone-neutral">
          <span className="memo-tile-value">
            {/* Null is "nothing has completed", not "same day". */}
            {totals.average_completion_days ?? '—'}
          </span>
          <span className="memo-tile-label">Avg Completion (days)</span>
        </span>
      </div>

      {/* --- Part 5: health ratios, each with its definition --- */}
      <section className="memo-dash-section">
        <div className="memo-dash-head"><h3>Task Health</h3></div>
        <div className="memo-tiles">
          {(health.data?.kpis || []).map((metric) => (
            <Kpi key={metric.key} metric={metric} />
          ))}
        </div>
      </section>

      {/* --- Part 6 / 11: trends --- */}
      <section className="memo-dash-section">
        <div className="memo-dash-head">
          <h3>Trends</h3>
          <div className="task-view-toggle" role="group" aria-label="Trend period">
            {PERIODS.map((option) => (
              <button key={option.value} type="button"
                aria-pressed={period === option.value}
                className={`lr-btn${period === option.value ? ' is-on' : ''}`}
                onClick={() => setPeriod(option.value)}>
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="memo-charts">
          <ChartFrame
            title="Task Volume" height={260}
            subtitle="Created and completed, by period"
            isEmpty={buckets.length === 0}
            rows={buckets}
            columns={[{ key: 'label', label: 'Period' },
              { key: 'created', label: 'Created' },
              { key: 'completed', label: 'Completed' }]}>
            <TrendChart
              data={buckets} height={260}
              series={[
                { key: 'created', name: 'Created' },
                { key: 'completed', name: 'Completed' },
              ]} />
          </ChartFrame>

          <ChartFrame
            title="Overdue Trend" height={260}
            /* Said on the chart, not only in the payload: overdue is a STATE,
               so the last bar means "right now" while the others mean "as at
               the end of that period". */
            note="Overdue is a state, so each period is measured as at its end."
            isEmpty={buckets.length === 0}
            rows={buckets}
            columns={[{ key: 'label', label: 'Period' },
              { key: 'overdue', label: 'Overdue' }]}>
            <TrendChart data={buckets} height={260} area
              series={[{ key: 'overdue', name: 'Overdue' }]} />
          </ChartFrame>

          <ChartFrame
            title="Task Status Distribution" height={260}
            isEmpty={statusRows.length === 0}
            rows={statusRows}
            columns={[{ key: 'label', label: 'Status' },
              { key: 'value', label: 'Tasks' }]}>
            <DistributionPieChart data={statusRows} height={260} />
          </ChartFrame>

          <ChartFrame
            title="Department Progress" height={280}
            subtitle="Completion rate by department"
            isEmpty={ranking.length === 0}
            rows={ranking}
            columns={[{ key: 'department', label: 'Department' },
              { key: 'completion_percent', label: 'Completion %' }]}>
            <ComparisonBarChart
              data={ranking.map((r) => ({
                label: r.department, value: r.completion_percent,
              }))}
              bars={[{ key: 'value', name: 'Completion %' }]}
              layout="vertical" height={280}
              colourFor={(row, index) => seriesColour(index)} />
          </ChartFrame>
        </div>
      </section>

      {/* --- Part 1: department ranking --- */}
      {ranking.length > 0 && (
        <section className="memo-dash-section">
          <div className="memo-dash-head"><h3>Department Comparison</h3>
            {/* Workload left the rail to make room for this page (T5.1), so the
                way through to it lives here — the deeper view behind the
                broader one. A page dropped from a rail must keep a documented
                route in; this is it, and navConfig's UNLISTED records it. */}
            <Link className="memo-dash-more" to="/tasks/workload">
              Workload detail →
            </Link></div>
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Department</th>
                  <th scope="col">Total</th>
                  <th scope="col">Completed</th>
                  <th scope="col">Overdue</th>
                  <th scope="col">Completion</th>
                  <th scope="col">Avg Resolution</th>
                </tr>
              </thead>
              <tbody>
                {ranking.map((row) => (
                  <tr key={row.department}>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {row.position}
                    </td>
                    <td style={{ fontWeight: 600 }}>
                      {row.department}
                      {row.low_volume && (
                        /* Marked, because a department that finished its one
                           task is not "100% complete" in any sense worth
                           putting at the top of a list. */
                        <span className="task-sub">too few tasks to compare</span>
                      )}
                    </td>
                    <td>{row.total}</td>
                    <td>{row.completed}</td>
                    <td className={row.overdue > 0 ? 'task-late' : undefined}>
                      {row.overdue}
                    </td>
                    <td>{row.completion_percent}%</td>
                    <td>{row.average_resolution_days ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* --- Parts 3 / 4: per-person tables, only when the server sent them --- */}
      {employees.data?.employees?.length > 0 && (
        <section className="memo-dash-section">
          <div className="memo-dash-head"><h3>Employee Metrics</h3></div>
          <p className="task-sub">{employees.data.note}</p>
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead>
                <tr>
                  <th scope="col">Employee</th>
                  <th scope="col">Department</th>
                  <th scope="col">Assigned</th>
                  <th scope="col">Completed</th>
                  <th scope="col">Open</th>
                  <th scope="col">Overdue</th>
                  <th scope="col">Completion</th>
                  <th scope="col">Avg Close</th>
                  <th scope="col">Review Wait</th>
                </tr>
              </thead>
              <tbody>
                {/* Rendered in the order the server sent — alphabetical. This
                    page does not re-sort, and offers no sort control. */}
                {employees.data.employees.map((row) => (
                  <tr key={row.user_id}>
                    <td style={{ fontWeight: 600 }}>{row.name}</td>
                    <td>{row.department || '—'}</td>
                    <td>{row.assigned}</td>
                    <td>{row.completed}</td>
                    <td>{row.open}</td>
                    <td className={row.overdue > 0 ? 'task-late' : undefined}>
                      {row.overdue}
                    </td>
                    <td>{row.completion_percent}%</td>
                    <td>{row.average_close_days ?? '—'}</td>
                    <td>{row.review_delay_days ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {reviewers.data?.reviewers?.length > 0 && (
        <section className="memo-dash-section">
          <div className="memo-dash-head"><h3>Reviewer Metrics</h3></div>
          <p className="task-sub">{reviewers.data.note}</p>
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead>
                <tr>
                  <th scope="col">Reviewer</th>
                  <th scope="col">Awaiting</th>
                  <th scope="col">Delayed</th>
                  <th scope="col">Decided</th>
                  <th scope="col">Oldest</th>
                  <th scope="col">Avg Review</th>
                </tr>
              </thead>
              <tbody>
                {reviewers.data.reviewers.map((row) => (
                  <tr key={row.reviewer}>
                    <td style={{ fontWeight: 600 }}>{row.reviewer}</td>
                    <td>{row.awaiting}</td>
                    <td className={row.delayed > 0 ? 'task-late' : undefined}>
                      {row.delayed}
                    </td>
                    <td>{row.decided}</td>
                    <td>{row.oldest_days} days</td>
                    <td>{row.average_review_days ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
};

export default TaskAnalytics;
