import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import { goalStatusLabel } from '../../components/appraisal/appraisalLabels';

/**
 * My Goals (Phase APM-03b).
 *
 * WHY THIS IS A PAGE AND NOT A TAB ON THE RECORD
 * ----------------------------------------------
 * The record is where objectives are AGREED — a long sitting, once or twice a
 * year, with the whole appraisal loaded around it. This page is where they are
 * CHECKED, which somebody does in twenty seconds while thinking about something
 * else. It answers one question — where am I against what I agreed — and it is
 * bookmarkable, so it can be the thing on a second monitor during a quarter.
 *
 * All seven fields the specification names, side by side: objective, weight,
 * target, achievement, progress, evidence and due date. Target and achievement
 * are separate columns rather than one "outcome", because overwriting what was
 * agreed with what happened is how a year's objectives quietly become whatever
 * was delivered.
 *
 * THE WEIGHTS ARE THE HEADLINE
 * ----------------------------
 * Shown as a total with its shortfall named, because until they reach 100% the
 * objectives cannot be agreed at all — and somebody looking at this page is
 * exactly the person who can fix that.
 */
const MyGoals = () => {
  const dash = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard,
  });

  const id = dash.data?.employee?.current_appraisal;
  const record = useQuery({
    queryKey: ['appraisal', id],
    queryFn: () => appraisalService.getAppraisal(id),
    enabled: Boolean(id),
  });

  if (dash.isLoading) return <div className="page"><Skeleton rows={3} /></div>;
  if (dash.isError) {
    return (
      <div className="page">
        <ErrorState error={dash.error} onRetry={dash.refetch} />
      </div>
    );
  }

  if (!id) {
    return (
      <div className="page memo-page">
        <div className="lr-page-head">
          <div>
            <h1 className="lr-page-title">My Goals</h1>
            <p className="lr-page-sub">
              You have no appraisal open, so there are no goals to show.
              HR opens one when a cycle starts.
            </p>
          </div>
        </div>
      </div>
    );
  }

  // The record carries the evidence citations and the achievements; the
  // dashboard's goal list does not. Falls back to the dashboard's shorter list
  // while the record loads, so the page is never blank.
  const goals = record.data?.goals || dash.data.employee.goals || [];
  const total = record.data?.goal_weight_total
    ?? dash.data.employee.goal_weight_total ?? 0;
  const citations = record.data?.evidence_references || [];
  const gap = 100 - total;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">My Goals</h1>
          <p className="lr-page-sub">
            {dash.data.employee.cycle} · {dash.data.employee.stage}
          </p>
        </div>
        <Link className="btn btn-ghost btn-sm" to={`/appraisals/${id}`}>
          Open my appraisal
        </Link>
      </div>

      <div className="memo-dash-head">
        <h3>How your year is divided</h3>
        <span className={`apr-total ${total === 100 ? 'is-ok' : 'is-off'}`}
          role="status">
          {total === 100
            ? <><CheckCircle2 size={13} aria-hidden="true" /> Your year adds up to 100%</>
            : (
              <>
                <AlertTriangle size={13} aria-hidden="true" />
                Your goals add up to {total}% —{' '}
                {gap > 0 ? `add ${gap}% more`
                  : `that is ${Math.abs(gap)}% too much`}
              </>
            )}
        </span>
      </div>

      {total !== 100 && (
        <div className="task-callout is-warn" role="note">
          Your goals need to add up to 100% before you can send them to your manager.
        </div>
      )}

      {goals.length === 0 ? (
        <p className="lr-page-sub">No goals have been set yet.</p>
      ) : (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">
              My goals for this cycle, with their weights, targets,
              achievements, progress, cited evidence and dates
            </caption>
            <thead>
              <tr>
                <th scope="col">Goal</th>
                <th scope="col">Status</th>
                <th scope="col">Share of year</th>
                <th scope="col">Success looks like</th>
                <th scope="col">What happened</th>
                <th scope="col">Progress</th>
                <th scope="col">Evidence</th>
                <th scope="col">Due</th>
              </tr>
            </thead>
            <tbody>
              {goals.map((goal) => {
                const cited = citations.filter((row) => row.goal === goal.id);
                return (
                  <tr key={goal.id}>
                    <th scope="row">{goal.objective}</th>
                    <td>
                      <span className={`apr-goal-status is-${goal.status || 'draft'}`}>
                        {goal.status_label || goalStatusLabel(goal.status)}
                      </span>
                    </td>
                    <td>{goal.weight}%</td>
                    <td>{goal.target || '—'}</td>
                    <td>{goal.achievement || '—'}</td>
                    <td>
                      <span className="apr-bar" aria-hidden="true">
                        <span className="apr-bar-fill"
                          style={{ width: `${goal.progress_percent || 0}%` }} />
                      </span>
                      {goal.progress_percent || 0}%
                    </td>
                    <td>
                      {cited.length === 0 ? '—' : cited.map((row) => (
                        <div key={row.id} className="apr-cited-note">
                          {(row.metrics || []).map(
                            (m) => `${m.label}: ${m.value ?? '—'}`).join(' · ')
                            || row.source}
                        </div>
                      ))}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {goal.due_date || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default MyGoals;
