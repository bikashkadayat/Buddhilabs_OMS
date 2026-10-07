import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2 } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { taskService } from '../../services/taskService';

/**
 * My tasks on Home (Phase T4.2, redesigned).
 *
 * One card: a completion ring and the four personal figures. Reads the
 * `/tasks/dashboard/` payload as given — the server decides what it sends —
 * and derives nothing a second time.
 *
 * THE RING'S DENOMINATOR is completed + open (my_tasks): "of the tasks that
 * were ever yours and are not drafts, how many are done". It is stated on the
 * ring itself so it cannot be mistaken for the Insights page's rate, which is
 * computed over a different set.
 *
 * A FAILURE IS SILENT
 * -------------------
 * Home aggregates half a dozen sources; one that will not load must cost its
 * own widget and nothing else. That is the posture the sidebar badges and the
 * work queue already take on this page.
 */

/** The count for one status value from the dashboard's by_status rows. */
const statusCount = (rows, value) => {
  if (!Array.isArray(rows)) return 0;
  const row = rows.find((r) => r?.value === value);
  return Number(row?.count) || 0;
};

const completionPercent = (completed = 0, open = 0) => {
  const total = (completed || 0) + (open || 0);
  if (!total) return 0;
  return Math.round(((completed || 0) / total) * 100);
};

const Figure = ({ n, label, to, tone }) => (
  <Link to={to} className="hm-tstat">
    <i className={`hm-dot is-${tone}`} aria-hidden="true" />
    <b>{n ?? 0}</b>
    <span>{label}</span>
  </Link>
);

const TaskWidgets = () => {
  const { user } = useAuth();

  const { data, isLoading, isError } = useQuery({
    queryKey: ['tasks', 'dashboard'],
    queryFn: taskService.getDashboard,
    staleTime: 60_000,
    retry: false,
    enabled: Boolean(user),
  });

  // Silent on failure. While loading, the card's own outline in shimmer
  // (Phase HOME-POLISH): the aside then keeps its height for the common case
  // — the card arriving — instead of the feed jumping down when it does.
  if (isLoading) {
    return (
      <section className="hm-sec" aria-busy="true" aria-hidden="true">
        {/* Hidden from assistive tech as a whole: a heading over a card with
            nothing in it is not yet a landmark worth announcing. */}
        <div className="hm-sec-h">
          <h2>My tasks</h2>
        </div>
        <div className="hm-card hm-tasks is-skeleton">
          <span className="hm-skel hm-skel-ring" />
          <div className="hm-tstats">
            {Array.from({ length: 4 }, (_, i) => (
              <span className="hm-tstat" key={i}>
                <span className="hm-skel hm-skel-sm" style={{ width: 24 }} />
                <span className="hm-skel" style={{ width: '60%' }} />
              </span>
            ))}
          </div>
        </div>
      </section>
    );
  }
  if (isError || !data) return null;

  const open = data.my_tasks ?? 0;
  const completed = data.completed ?? 0;
  const toAccept = data.to_accept ?? 0;
  const overdue = data.overdue ?? 0;
  const review = statusCount(data.by_status, 'under_review');

  const hasAny = open + completed + toAccept + overdue + (data.due_today ?? 0) > 0;
  const pct = completionPercent(completed, open);

  return (
    <section className="hm-sec" aria-labelledby="hm-tasks-h">
      <div className="hm-sec-h">
        <h2 id="hm-tasks-h">My tasks</h2>
        <Link className="hm-sec-link" to="/tasks">Task workspace →</Link>
      </div>

      {!hasAny ? (
        <Link to="/tasks" className="hm-card hm-strip-row">
          <CheckCircle2 size={18} strokeWidth={1.75} className="hm-strip-ok" aria-hidden="true" />
          <span>No tasks assigned to you</span>
          <span className="hm-strip-link">Open the workspace →</span>
        </Link>
      ) : (
        <div className="hm-card hm-tasks">
          <div
            className="hm-ring"
            style={{ '--p': pct }}
            role="img"
            aria-label={`${pct}% of my tasks completed`}
          >
            <div>
              <b>{pct}%</b>
              <small>complete</small>
            </div>
          </div>
          {/* Legend dots from the status set: open blue, pending acceptance
              purple, waiting review amber, overdue red. */}
          <div className="hm-tstats">
            <Figure n={open} label="Open" to="/tasks/mine" tone="blue" />
            <Figure n={toAccept} label="Pending acceptance" to="/tasks/mine" tone="purple" />
            <Figure n={review} label="Waiting review" to="/tasks/review-queue" tone="amber" />
            <Figure n={overdue} label="Overdue" to="/tasks/overdue-screen" tone="red" />
          </div>
        </div>
      )}
    </section>
  );
};

export default TaskWidgets;
