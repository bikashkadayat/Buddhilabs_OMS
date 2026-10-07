import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { History, Scale } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import AppraisalTable from '../../components/appraisal/AppraisalTable';

/**
 * Calibration Queue (Phase APM-03b) — the review committee's workspace.
 *
 * ONLY ASSIGNED REVIEWS ARE VISIBLE
 * ---------------------------------
 * `?scope=committee` returns the appraisals this person has been placed on, and
 * nothing else. Membership is EXPLICIT: sitting on one committee grants no view
 * of any other appraisal, in this department or any other. That is the whole
 * reason this is its own scope rather than a filter on an organisation-wide
 * list — a filter can be removed by a query parameter, a scope cannot.
 *
 * WHAT CALIBRATION MEANS HERE, AND WHAT IT DOES NOT
 * -------------------------------------------------
 * The committee's job in this system is to read appraisals across teams and
 * record their own view, so that "exceeds expectations" means something
 * comparable between one supervisor and another. It is NOT a moderation exercise
 * that adjusts anybody's rating to fit a distribution: there is no curve, no
 * quota, no forced ranking, and no aggregate of the queue for the committee to
 * balance. Every appraisal is read on its own record.
 *
 * The queue is therefore a list, not a scoreboard — split only by whether the
 * committee's decision is the next step.
 */
const CommitteeQueue = () => {
  const waiting = useQuery({
    queryKey: ['appraisals', 'committee', 'needs_me'],
    queryFn: () => appraisalService.getAppraisals({ scope: 'needs_me' }),
  });
  const assigned = useQuery({
    queryKey: ['appraisals', 'committee'],
    queryFn: () => appraisalService.getAppraisals({ scope: 'committee' }),
  });

  if (assigned.isLoading) {
    return <div className="page"><Skeleton rows={4} /></div>;
  }
  if (assigned.isError) {
    return (
      <div className="page">
        <ErrorState error={assigned.error} onRetry={assigned.refetch} />
      </div>
    );
  }

  const rows = (payload) => payload?.results || payload || [];
  const mine = rows(assigned.data);
  const mineIds = new Set(mine.map((r) => r.id));
  // Intersected with the committee's own list: `needs_me` also covers the
  // stages a person owns as a SUPERVISOR, and those belong on Team Reviews.
  // A committee queue that quietly included them would be a second, differently
  // ordered copy of somebody else's page.
  const now = rows(waiting.data).filter((r) => mineIds.has(r.id));
  const closed = mine.filter((r) => r.status === 'closed');
  const open = mine.filter(
    (r) => r.status !== 'closed' && !now.some((n) => n.id === r.id));

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Calibration Queue</h1>
          <p className="lr-page-sub">
            Appraisals you have been placed on, across teams. Each is read on
            its own record — there is no curve, quota or forced distribution.
          </p>
        </div>
      </div>

      <section className="memo-dash-section" aria-labelledby="cq-now-h">
        <div className="memo-dash-head">
          <h3 id="cq-now-h">
            <Scale size={15} aria-hidden="true" /> Waiting for the committee
          </h3>
        </div>
        <AppraisalTable rows={now}
          emptyMessage="Nothing is waiting for a committee decision." />
      </section>

      <section className="memo-dash-section" aria-labelledby="cq-all-h">
        <div className="memo-dash-head">
          <h3 id="cq-all-h">Assigned Reviews</h3>
        </div>
        <AppraisalTable rows={open}
          emptyMessage="You are not on any other open appraisal." />
      </section>

      <section className="memo-dash-section" aria-labelledby="cq-hist-h">
        <div className="memo-dash-head">
          <h3 id="cq-hist-h">
            <History size={15} aria-hidden="true" /> Review History
          </h3>
        </div>
        {/* Closed appraisals this person sat on. The committee's COMMENT is
            deliberately not inlined here: list rows are shared by the
            supervisor's team view and HR's organisation-wide view, so carrying
            review prose on them would put every committee comment in the system
            into one HR response. Each row opens the record, where the comment
            already sits under Committee review. */}
        {closed.length === 0 ? (
          <p className="lr-page-sub">
            No appraisal you sat on has closed yet.
          </p>
        ) : (
          <>
            <p className="lr-page-sub">
              The committee&rsquo;s comments are on each record, under Committee
              review.
            </p>
            <ul className="apr-cited">
              {closed.map((row) => (
                <li key={row.id}>
                  <Link to={`/appraisals/${row.id}`}>{row.employee_name}</Link>
                  {' · '}{row.cycle_name}
                  {row.department_name ? ` · ${row.department_name}` : ''}
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  );
};

export default CommitteeQueue;
