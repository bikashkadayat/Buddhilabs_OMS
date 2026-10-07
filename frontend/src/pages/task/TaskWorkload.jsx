import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Users } from 'lucide-react';

import { taskService } from '../../services/taskService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';

/**
 * The workload view (Phase T3, Part 4): who is carrying what.
 *
 * WHY A BAR AND NOT A SCORE
 * -------------------------
 * The bar shows each person's open load against the busiest person's, which is
 * a comparison the data can actually support. It is NOT a percentage of
 * capacity: nothing in this system knows anybody's capacity — not their hours,
 * their other commitments, or how big any of these tasks are — and a number
 * presented as "83% loaded" would be invented. The flag beside it marks somebody
 * carrying more than 1.5x the median, which is the honest version of the same
 * signal: "this is out of line with the rest", not "this person is full".
 *
 * The server refuses this page to an employee, so there is no role check here —
 * the route is reachable and the API answers 403, which is the boundary.
 */

const Bar = ({ open, max }) => {
  const share = max > 0 ? Math.round((100 * open) / max) : 0;
  return (
    <div className="task-bar" role="img"
      aria-label={`${open} open task${open === 1 ? '' : 's'}`}>
      <span className="task-bar-fill" style={{ width: `${share}%` }} />
    </div>
  );
};

const TaskWorkload = () => {
  // Reviewer load comes from the analytics endpoint that already serves it —
  // this page adds no calculation of its own. `retry: false` and a silent
  // failure: a reviewer chart that will not load must cost its own card, not
  // the workload page it sits on.
  const reviewers = useQuery({
    queryKey: ['tasks', 'analytics', 'reviewers'],
    queryFn: () => taskService.getReviewerAnalytics(),
    retry: false,
  });

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'workload'],
    queryFn: () => taskService.getWorkload(),
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  // The SERVER decides which perspective came back; the page renders what it
  // was given rather than guessing from the caller's role, which is how a client
  // ends up asking for a block that was never sent.
  const personal = data?.personal;
  const people = data?.by_employee || [];
  const departments = data?.by_department || [];
  const utilisation = data?.utilisation;
  // Reviewers come back alphabetically from the server; only those with a queue
  // are charted, because a bar of zero for everybody who reviews nothing makes
  // the chart unreadable and says nothing.
  const reviewerRows = (reviewers.data?.reviewers || [])
    .filter((r) => (r.awaiting || 0) + (r.delayed || 0) > 0);
  const busiest = Math.max(...people.map((p) => p.open), 0);
  const overloaded = people.filter((p) => p.is_overloaded).length;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Workload</h1>
          <p className="lr-page-sub">
            Open work per person and per department, across everything you can see
          </p>
        </div>
      </div>

      {/* Employee perspective — everybody gets this, managers included. A
          workload page that hides your own load is one you keep a list beside. */}
      {personal && (
        <section className="memo-dash-section">
          <div className="memo-dash-head"><h3>My Workload</h3></div>
          <div className="memo-tiles">
            <span className="memo-tile tone-info">
              <span className="memo-tile-value">{personal.assigned}</span>
              <span className="memo-tile-label">Assigned</span>
            </span>
            <span className="memo-tile tone-info">
              <span className="memo-tile-value">{personal.open}</span>
              <span className="memo-tile-label">Open</span>
            </span>
            <span className="memo-tile tone-ok">
              <span className="memo-tile-value">{personal.completed}</span>
              <span className="memo-tile-label">Completed</span>
              <span className="memo-tile-hint">
                {personal.completion_percent}% of everything assigned
              </span>
            </span>
            <span className="memo-tile tone-danger">
              <span className="memo-tile-value">{personal.overdue}</span>
              <span className="memo-tile-label">Overdue</span>
            </span>
            <span className="memo-tile tone-neutral">
              <span className="memo-tile-value">
                {/* null is "nothing has completed yet", not "same day". */}
                {personal.average_completion_days ?? '—'}
              </span>
              <span className="memo-tile-label">Avg days to complete</span>
            </span>
          </div>
        </section>
      )}

      {utilisation && (
        <section className="memo-dash-section">
          <div className="memo-dash-head"><h3>Resource Utilisation</h3></div>
          <div className="memo-tiles">
            <span className="memo-tile tone-info">
              <span className="memo-tile-ico" aria-hidden="true">
                <Users size={18} />
              </span>
              <span className="memo-tile-value">{utilisation.people}</span>
              <span className="memo-tile-label">People with work</span>
            </span>
            <span className="memo-tile tone-info">
              <span className="memo-tile-value">{utilisation.open}</span>
              <span className="memo-tile-label">Open tasks</span>
            </span>
            <span className="memo-tile tone-urgent">
              <span className="memo-tile-value">
                {utilisation.busiest_quarter_share_percent}%
              </span>
              <span className="memo-tile-label">Held by the busiest quarter</span>
              {/* Deliberately NOT "% utilised": nothing here knows anybody's
                  hours, so that number would be invented. Concentration is a
                  fact the data supports. */}
              <span className="memo-tile-hint">Concentration, not capacity</span>
            </span>
            <span className="memo-tile tone-danger">
              <span className="memo-tile-ico" aria-hidden="true">
                <AlertTriangle size={18} />
              </span>
              <span className="memo-tile-value">{utilisation.overloaded}</span>
              <span className="memo-tile-label">Outlier loads</span>
              <span className="memo-tile-hint">More than 1.5x the median</span>
            </span>
            <span className="memo-tile tone-warn">
              <span className="memo-tile-value">{utilisation.unassigned_open}</span>
              <span className="memo-tile-label">Open, nobody carrying</span>
            </span>
          </div>
        </section>
      )}

      {people.length > 0 && (
        <div className="memo-tiles">
          <span className="memo-tile tone-info">
            <span className="memo-tile-ico" aria-hidden="true"><Users size={18} /></span>
            <span className="memo-tile-value">{people.length}</span>
            <span className="memo-tile-label">People with work</span>
          </span>
          <span className="memo-tile tone-urgent">
            <span className="memo-tile-ico" aria-hidden="true">
              <AlertTriangle size={18} />
            </span>
            <span className="memo-tile-value">{overloaded}</span>
            <span className="memo-tile-label">Carrying an outlier load</span>
            <span className="memo-tile-hint">More than 1.5x the median</span>
          </span>
          {data.pending_review !== undefined && (
            <span className="memo-tile tone-warn">
              <span className="memo-tile-value">{data.pending_review}</span>
              <span className="memo-tile-label">Pending reviews</span>
            </span>
          )}
        </div>
      )}

      {people.length > 0 && (
      <section className="memo-dash-section">
        <div className="memo-dash-head"><h3>Team Workload</h3></div>

        {/* The chart the page was missing (T5.1). Same rows the table below
            renders, in the same server order — a chart that sorted differently
            from the table beneath it would be two answers to one question.
            Open and overdue only: "completed" on a workload chart invites
            reading it as output per person, which is a ranking of people. */}
        {people.length > 0 && (
          <ChartFrame title="Tasks by assignee"
            subtitle="Open and overdue work each person is carrying."
            note="A count of work in hand, not a measure of anybody's output."
            rows={people} columns={[
              { key: 'name', label: 'Employee' },
              { key: 'open', label: 'Open', numeric: true },
              { key: 'overdue', label: 'Overdue', numeric: true },
            ]}>
            <ComparisonBarChart data={people} labelKey="name" height={320}
              bars={[{ key: 'open', label: 'Open' },
                { key: 'overdue', label: 'Overdue' }]} />
          </ChartFrame>
        )}

        {people.length > 0 && (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead>
                <tr>
                  <th scope="col">Employee</th>
                  <th scope="col">Department</th>
                  <th scope="col">Open Load</th>
                  <th scope="col">Open</th>
                  <th scope="col">Overdue</th>
                  <th scope="col">Completed</th>
                  <th scope="col">Completion</th>
                </tr>
              </thead>
              <tbody>
                {people.map((person) => (
                  <tr key={person.user_id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{person.name}</div>
                      {person.designation && (
                        <div className="task-sub">{person.designation}</div>
                      )}
                    </td>
                    <td>{person.department || '—'}</td>
                    <td style={{ minWidth: 140 }}>
                      <Bar open={person.open} max={busiest} />
                      {person.is_overloaded && (
                        // Stated in words as well as colour: a coloured bar
                        // alone is invisible to a colour-blind reader.
                        <span className="task-late">Outlier load</span>
                      )}
                    </td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {person.open}
                    </td>
                    <td className={person.overdue > 0 ? 'task-late' : undefined}
                      style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {person.overdue}
                    </td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {person.completed}
                    </td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {person.completion_percent}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      )}

      {departments.length > 0 && (
      <section className="memo-dash-section">
        <div className="memo-dash-head"><h3>Department Workload</h3></div>

        {reviewerRows.length > 0 && (
          <ChartFrame title="Tasks by reviewer"
            subtitle="What is waiting on each reviewer's decision."
            note="Queue depth, not a judgement of how anybody reviews."
            rows={reviewerRows} columns={[
              { key: 'reviewer', label: 'Reviewer' },
              { key: 'awaiting', label: 'Awaiting', numeric: true },
              { key: 'delayed', label: 'Delayed', numeric: true },
            ]}>
            <ComparisonBarChart data={reviewerRows} labelKey="reviewer"
              height={280} bars={[{ key: 'awaiting', label: 'Awaiting' },
                { key: 'delayed', label: 'Delayed' }]} />
          </ChartFrame>
        )}

        {departments.length > 0 && (
          <ChartFrame title="Tasks by department"
            subtitle="Open and overdue work by department."
            rows={departments} columns={[
              { key: 'department', label: 'Department' },
              { key: 'open', label: 'Open', numeric: true },
              { key: 'overdue', label: 'Overdue', numeric: true },
            ]}>
            <ComparisonBarChart data={departments} labelKey="department"
              height={280} bars={[{ key: 'open', label: 'Open' },
                { key: 'overdue', label: 'Overdue' }]} />
          </ChartFrame>
        )}

        {departments.length > 0 && (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead>
                <tr>
                  <th scope="col">Department</th>
                  <th scope="col">Open</th>
                  <th scope="col">Overdue</th>
                  <th scope="col">Completed</th>
                  <th scope="col">Total</th>
                  <th scope="col">Completion</th>
                </tr>
              </thead>
              <tbody>
                {departments.map((row) => (
                  <tr key={row.department}>
                    <td style={{ fontWeight: 600 }}>{row.department}</td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>{row.open}</td>
                    <td className={row.overdue > 0 ? 'task-late' : undefined}
                      style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {row.overdue}
                    </td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {row.completed}
                    </td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>{row.total}</td>
                    <td style={{ minWidth: 120 }}>
                      <div className="task-bar" role="img"
                        aria-label={`${row.completion_percent}% complete`}>
                        <span className="task-bar-fill"
                          style={{ width: `${row.completion_percent}%` }} />
                      </div>
                      <span className="task-sub">{row.completion_percent}%</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      )}
    </div>
  );
};

export default TaskWorkload;
