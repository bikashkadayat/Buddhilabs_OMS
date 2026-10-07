import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, ClipboardCheck, Users } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import { TOTAL_STAGES } from '../../components/appraisal/appraisalLabels';
import { EMPTY } from '../../services/emptyStates';

/**
 * Team Reviews (Phase APM-03b) — the supervisor's workspace.
 *
 * DIRECT REPORTS ONLY, AND THE SERVER DECIDES WHO THOSE ARE
 * ---------------------------------------------------------
 * The list comes from `?scope=supervising`, which filters on the appraisal's
 * own supervisor field — not on department. A department head is NOT entitled
 * to the appraisal of everybody in their department, only of the people they
 * actually supervise. Appraisals hold self-assessments and promotion
 * recommendations; that is categorically more sensitive than a task list, and
 * the distinction is enforced on the server rather than by this page choosing
 * a gentler query.
 *
 * ALPHABETICAL, WITH NO SORT CONTROL
 * ----------------------------------
 * The team list arrives ordered by name and is rendered in that order. Sorting
 * colleagues by stage, goal count or completion produces a ranking, whatever
 * the column is called. What a supervisor needs — who is waiting on ME — is a
 * flag about the process, and it gets its own section at the top.
 */
const Tile = ({ label, value, hint }) => (
  <span className="memo-tile tone-neutral">
    <span className="memo-tile-value">{value ?? '—'}</span>
    <span className="memo-tile-label">{label}</span>
    {hint && <span className="memo-tile-hint">{hint}</span>}
  </span>
);

/**
 * One row and the table around it, declared OUTSIDE the page component.
 *
 * Declared inside, React sees a brand-new component type on every render and
 * unmounts the whole table rather than updating it — every row's state is
 * thrown away and focus is lost. Two sections share this markup, which is why
 * it is a component rather than a loop in each.
 */
const Row = ({ row }) => (
  <tr>
    <th scope="row">{row.employee}</th>
    <td>
      {row.stage}
      <div className="memo-tile-hint">
        Stage {row.stage_index} of {TOTAL_STAGES}
      </div>
    </td>
    <td>
      {row.goals}
      {row.goal_weight_total !== 100 && (
        <span className="task-late">
          weights total {row.goal_weight_total}%
        </span>
      )}
    </td>
    <td>
      {row.awaiting_me
        ? <b>Waiting on you</b>
        : <span className="memo-tile-hint">With somebody else</span>}
    </td>
    <td>
      <Link className="btn btn-ghost btn-xs"
        to={`/appraisals/${row.appraisal_id}`}>
        Open {row.employee}’s appraisal
      </Link>
    </td>
  </tr>
);

const Table = ({ rows, caption }) => (
  <div className="lr-table-wrap">
    <table className="lr-table">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Employee</th>
          <th scope="col">Stage</th>
          <th scope="col">Goals</th>
          <th scope="col">Whose turn</th>
          <th scope="col">Open</th>
        </tr>
      </thead>
      <tbody>{rows.map((row) => <Row key={row.appraisal_id} row={row} />)}</tbody>
    </table>
  </div>
);

const TeamAppraisals = () => {
  const dash = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard,
  });

  if (dash.isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (dash.isError) {
    return (
      <div className="page">
        <ErrorState error={dash.error} onRetry={dash.refetch} />
      </div>
    );
  }

  const manager = dash.data?.manager;
  if (!manager) {
    return (
      <div className="page memo-page">
        <div className="lr-page-head">
          <div>
            <h1 className="lr-page-title">Team Reviews</h1>
            <p className="lr-page-sub">
              Nobody reports to you for appraisal purposes, so there is nothing
              here.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const team = manager.team || [];
  // Three lists from one payload, in the order a supervisor works: what needs
  // them, what is moving elsewhere, what is finished.
  //
  // `is_closed` comes from the SERVER. Matching the display label "Closed"
  // would have worked today and broken silently the day somebody reworded a
  // stage — a finished appraisal would slide back into the live list with no
  // error anywhere to notice.
  const closed = team.filter((row) => row.is_closed);
  const waiting = team.filter((row) => row.awaiting_me && !row.is_closed);
  const rest = team.filter((row) => !row.awaiting_me && !row.is_closed);

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Team Reviews</h1>
          <p className="lr-page-sub">{manager.note}</p>
        </div>
      </div>

      <div className="memo-tiles">
        <Tile label="Direct Reports" value={manager.team_size} />
        <Tile label="Pending With You" value={manager.pending_reviews}
          hint="Appraisals at a stage you own" />
        <Tile label="Closed" value={manager.completed} />
        <Tile label="Cycle Completion"
          value={`${manager.completion_percent ?? 0}%`}
          hint={`${manager.completed} of ${manager.team_size} closed`} />
      </div>

      <section className="memo-dash-section" aria-labelledby="apr-wait-h">
        <div className="memo-dash-head">
          <h3 id="apr-wait-h">
            <ClipboardCheck size={15} aria-hidden="true" /> Pending Reviews
          </h3>
        </div>
        {waiting.length
          ? <Table rows={waiting} caption="Appraisals waiting on you" />
          : (
            <p className="lr-page-sub">
              {EMPTY.nothingWaiting}
            </p>
          )}
      </section>

      <section className="memo-dash-section" aria-labelledby="apr-team-h">
        <div className="memo-dash-head">
          <h3 id="apr-team-h">
            <Users size={15} aria-hidden="true" /> My Team
          </h3>
        </div>
        {rest.length
          ? <Table rows={rest} caption="Direct reports, alphabetically" />
          : <p className="lr-page-sub">Every appraisal is waiting on you.</p>}
      </section>

      <section className="memo-dash-section" aria-labelledby="apr-done-h">
        <div className="memo-dash-head">
          <h3 id="apr-done-h">
            <CheckCircle2 size={15} aria-hidden="true" /> Completed Reviews
          </h3>
        </div>
        {/* Closed appraisals stay reachable, and stay listed here rather than
            disappearing the moment they close. A supervisor is asked about last
            cycle far more often than they are asked about this one, and a
            record you cannot find is a record you cannot stand behind. */}
        {closed.length
          ? <Table rows={closed} caption="Appraisals closed this cycle" />
          : (
            <p className="lr-page-sub">
              No appraisal on your team has closed yet.
            </p>
          )}
      </section>

      <section className="memo-dash-section" aria-labelledby="apr-need-h">
        <div className="memo-dash-head">
          <h3 id="apr-need-h">Development Needs Across the Team</h3>
        </div>
        <p className="lr-page-sub">
          A count of development ACTIONS by area, so a shared gap is visible.
          Not a judgement of individuals, and nobody is named.
        </p>
        {manager.development_needs?.length ? (
          <ul className="apr-cited">
            {manager.development_needs.map((row) => (
              <li key={row.area}>{row.area} — {row.count}</li>
            ))}
          </ul>
        ) : (
          <p className="lr-page-sub">No development actions recorded yet.</p>
        )}
      </section>
    </div>
  );
};

export default TeamAppraisals;
