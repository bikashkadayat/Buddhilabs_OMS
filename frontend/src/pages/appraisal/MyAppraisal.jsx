import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ClipboardCheck } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import StageTracker from '../../components/appraisal/StageTracker';
import { stepForIndex } from '../../components/appraisal/appraisalLabels';
import { EMPTY } from '../../services/emptyStates';

/**
 * My Appraisal (Phase APM-03b).
 *
 * THE EMPLOYEE'S OWN LANDING
 * --------------------------
 * Where a person's appraisal is, what is waiting on them, their objectives and
 * their progress — and a way through to the record itself. Everything here is
 * about ONE person's own appraisal; there is no comparison, no team figure and
 * no cohort average, because none of those is information an employee can act
 * on and all of them invite the reading this module exists to prevent.
 *
 * WHY GOALS ARE LISTED HERE AND EDITED THERE
 * ------------------------------------------
 * This page is the answer to "where am I", which somebody checks often. The
 * record is the answer to "let me work on this", which they do rarely and at
 * length. Putting the editor on the landing page would mean loading the whole
 * appraisal to answer a question a summary answers.
 *
 * WHAT "AWAITING YOU" MEANS
 * -------------------------
 * The server sets `awaiting_me`. It is the only judgement on this page and it
 * is about the PROCESS — whose turn it is — not about the person.
 */
const MyAppraisal = () => {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  const me = data.employee || {};

  if (!me.current_appraisal) {
    return (
      <div className="page memo-page">
        <div className="lr-page-head">
          <div>
            <h1 className="lr-page-title">My Appraisal</h1>
            <p className="lr-page-sub">
              {EMPTY.noAppraisalOpen} HR opens one when a
              cycle starts.
            </p>
          </div>
        </div>
        {me.history?.length > 0 && (
          <section className="memo-dash-section">
            <div className="memo-dash-head"><h3>Earlier years</h3></div>
            <ul className="apr-cited">
              {me.history.map((row) => (
                <li key={row.id}>
                  <Link to={`/appraisals/${row.id}`}>{row.cycle}</Link> ·{' '}
                  {row.status}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    );
  }

  const weightOff = me.goal_weight_total !== 100;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">My Appraisal</h1>
          {/* The employee's own STEP, not the backend stage. "My Review"
              tells them whose turn it is; "Self Assessment" asks them to learn
              a ten-stage vocabulary first. */}
          <p className="lr-page-sub">
            {me.cycle} · {stepForIndex(me.stage_index)?.label ?? me.stage}
          </p>
        </div>
        <Link className="btn btn-primary btn-sm"
          to={`/appraisals/${me.current_appraisal}`}>
          Open my appraisal
        </Link>
      </div>

      {/* `simple`: this is the EMPLOYEE'S landing page, so it shows the five
          steps rather than the ten stages the engine runs. Without it, the one
          page most likely to be somebody's first encounter with appraisal
          opened on a ten-item ladder. */}
      <StageTracker status={null} stageIndex={me.stage_index} simple />

      {me.awaiting_me && (
        <div className="task-callout is-warn" role="note">
          <ClipboardCheck size={13} aria-hidden="true" /> <b>Waiting on you.</b>{' '}
          Writing your review is the next step.{' '}
          <Link to={`/appraisals/${me.current_appraisal}`}>Write it now</Link>.
        </div>
      )}

      <p className="lr-page-sub">{me.note}</p>

      <section className="memo-dash-section" aria-labelledby="my-goals-h">
        <div className="memo-dash-head">
          <h3 id="my-goals-h">My Goals</h3>
          <span className={`apr-total ${weightOff ? 'is-off' : 'is-ok'}`}>
            {me.goal_weight_total}% of your year
          </span>
        </div>
        {weightOff && (
          <div className="task-callout is-warn" role="note">
            <AlertTriangle size={13} aria-hidden="true" /> Your goals need to
            add up to 100% before you can send them to your manager.
          </div>
        )}
        {me.goals?.length ? (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">My goals for this cycle</caption>
              <thead>
                <tr>
                  <th scope="col">Goal</th>
                  <th scope="col">Share of year</th>
                  <th scope="col">Progress</th>
                  <th scope="col">Due</th>
                  <th scope="col">What happened</th>
                </tr>
              </thead>
              <tbody>
                {me.goals.map((goal) => (
                  <tr key={goal.id}>
                    <th scope="row">{goal.objective}</th>
                    <td>{goal.weight}%</td>
                    <td>
                      <span className="apr-bar" aria-hidden="true">
                        <span className="apr-bar-fill"
                          style={{ width: `${goal.progress_percent || 0}%` }} />
                      </span>
                      {goal.progress_percent || 0}%
                    </td>
                    <td>{goal.due_date || '—'}</td>
                    <td>{goal.achievement || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="lr-page-sub">No goals have been set yet.</p>
        )}
      </section>

      <section className="memo-dash-section" aria-labelledby="my-dev-h">
        <div className="memo-dash-head">
          <h3 id="my-dev-h">My Growth Plan</h3>
        </div>
        {me.development_plan?.length ? (
          <ul className="apr-cited">
            {me.development_plan.map((row, i) => (
              <li key={`${row.area}-${i}`}>
                <b>{row.area}</b> — {row.action}
                <div className="memo-tile-hint">
                  {row.status}{row.target_date ? ` · by ${row.target_date}` : ''}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="lr-page-sub">Nothing agreed yet.</p>
        )}
      </section>

      <section className="memo-dash-section" aria-labelledby="my-tra-h">
        <div className="memo-dash-head">
          <h3 id="my-tra-h">Training &amp; Support</h3>
        </div>
        {me.training_recommendations?.length ? (
          <ul className="apr-cited">
            {me.training_recommendations.map((row, i) => (
              <li key={`${row.title}-${i}`}>
                <b>{row.title}</b> — {row.kind_label || 'Training'}
                <div className="memo-tile-hint">
                  {row.status}
                  {row.target_period ? ` · ${row.target_period}` : ''}
                  {row.mentor_name ? ` · Mentor: ${row.mentor_name}` : ''}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="lr-page-sub">Nothing requested yet.</p>
        )}
      </section>

      {me.history?.length > 0 && (
        <section className="memo-dash-section" aria-labelledby="my-hist-h">
          <div className="memo-dash-head">
            <h3 id="my-hist-h">Earlier years</h3>
          </div>
          <ul className="apr-cited">
            {me.history.map((row) => (
              <li key={row.id}>
                <Link to={`/appraisals/${row.id}`}>{row.cycle}</Link> ·{' '}
                {row.status}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
};

export default MyAppraisal;
