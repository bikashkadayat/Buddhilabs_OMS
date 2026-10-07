import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Clock, ShieldCheck, Users } from 'lucide-react';

import { taskService } from '../../services/taskService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The review queue and review dashboard (Phase T4.6).
 *
 * WHY AGE IS MEASURED FROM SUBMISSION
 * -----------------------------------
 * Not from the due date, and not from creation. The question this page answers
 * is "how long has this been sitting with a reviewer". A task submitted
 * yesterday against a deadline three months away is not a bottleneck; one
 * submitted three weeks ago is, whatever its due date says. A review backlog is
 * invisible on a due-date report, which is exactly why it gets its own page.
 *
 * THE BOTTLENECK HAS A NAME
 * -------------------------
 * The per-reviewer table is the point of the page. "Fourteen tasks awaiting
 * review" is a number; "eleven of them are with one person, the oldest for
 * three weeks" is something somebody can act on this afternoon.
 */
const aging = (days) => {
  if (days >= 14) return 'no';
  if (days >= 7) return 'warn';
  if (days >= 3) return 'wait';
  return 'ok';
};

const TaskReviewQueue = () => {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'review-queue'],
    queryFn: () => taskService.getReviewQueue(),
    refetchInterval: 60_000,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  const rows = data?.rows || [];
  const byReviewer = data?.by_reviewer || [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Review Queue</h1>
          <p className="lr-page-sub">
            Submitted work waiting on a decision — oldest first, aged from when
            it was submitted
          </p>
        </div>
      </div>

      {rows.length === 0 && (
        <p className="task-sub">
          <ShieldCheck size={13} aria-hidden="true" /> Nothing is waiting for
          review.
        </p>
      )}

      {rows.length > 0 && (
        <>
          <div className="memo-tiles">
            <span className="memo-tile tone-info">
              <span className="memo-tile-ico" aria-hidden="true">
                <ShieldCheck size={18} />
              </span>
              <span className="memo-tile-value">{data.summary.waiting}</span>
              <span className="memo-tile-label">Awaiting review</span>
            </span>
            <span className="memo-tile tone-danger">
              <span className="memo-tile-ico" aria-hidden="true">
                <Clock size={18} />
              </span>
              <span className="memo-tile-value">{data.summary.oldest_days}</span>
              <span className="memo-tile-label">Oldest, in days</span>
            </span>
            <span className="memo-tile tone-warn">
              <span className="memo-tile-value">{data.summary.average_days}</span>
              <span className="memo-tile-label">Average wait, in days</span>
            </span>
            <span className="memo-tile tone-neutral">
              <span className="memo-tile-ico" aria-hidden="true">
                <Users size={18} />
              </span>
              <span className="memo-tile-value">{data.summary.reviewers}</span>
              <span className="memo-tile-label">Reviewers holding work</span>
            </span>
          </div>

          <section className="memo-dash-section">
            <div className="memo-dash-head"><h3>Reviewer Backlog</h3></div>
            <div className="lr-table-wrap">
              <table className="lr-table">
                <thead>
                  <tr>
                    <th scope="col">Reviewer</th>
                    <th scope="col">Waiting</th>
                    <th scope="col">Oldest</th>
                  </tr>
                </thead>
                <tbody>
                  {byReviewer.map((entry) => (
                    <tr key={entry.reviewer}>
                      <td style={{ fontWeight: 600 }}>{entry.reviewer}</td>
                      <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {entry.waiting}
                      </td>
                      <td>
                        {/* Aged in words as well as colour — this page is
                            read in meetings and gets printed. */}
                        <span className={`min-status is-${aging(entry.oldest_days)}`}>
                          {entry.oldest_days} day
                          {entry.oldest_days === 1 ? '' : 's'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="memo-dash-section">
            <div className="memo-dash-head"><h3>Waiting for Review</h3></div>
            <div className="lr-table-wrap">
              <table className="lr-table">
                <thead>
                  <tr>
                    <th scope="col">Waiting</th>
                    <th scope="col">Task No</th>
                    <th scope="col">Task</th>
                    <th scope="col">Assignee</th>
                    <th scope="col">Reviewer</th>
                    <th scope="col">Department</th>
                    <th scope="col">Priority</th>
                    <th scope="col">Due</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.task_id}>
                      <td>
                        <span className={`min-status is-${aging(row.waiting_days)}`}>
                          {row.waiting_days} day{row.waiting_days === 1 ? '' : 's'}
                        </span>
                      </td>
                      <td style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>
                        <Link to={`/tasks/${row.task_id}`}>{row.task_number}</Link>
                      </td>
                      <td>{row.title}</td>
                      <td>{row.assignee}</td>
                      <td>{row.reviewer}</td>
                      <td>{row.department}</td>
                      <td>{row.priority}</td>
                      <td className={row.is_overdue ? 'task-late' : undefined}
                        style={{ whiteSpace: 'nowrap' }}>
                        {row.due_date || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
};

export default TaskReviewQueue;
