import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Clock, GraduationCap, Info, Target, TrendingUp, Users,
} from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The HR cycle dashboard (Phase APM-03b).
 *
 * EVERY FIGURE IS ABOUT THE PROCESS, NOT ABOUT PEOPLE
 * ---------------------------------------------------
 * Completion, stage distribution, department progress, where the round is
 * stuck. None of it is an aggregate of anybody's performance, and there is
 * deliberately no organisation-wide competency or goal-achievement figure — an
 * "average performance by department" chart is a ranking of departments, and
 * within a month it becomes a ranking of the people in them.
 *
 * THE TWO PEOPLE-SHAPED LISTS
 * ---------------------------
 * Promotion readiness and succession carry only what a HUMAN recorded, with
 * their reasons, alphabetically. Somebody with no recommendation is ABSENT
 * rather than listed as a no: "not considered" and "development required" are
 * different answers, and putting the un-considered on the page as a negative is
 * the exact failure the four-state field was introduced to prevent. The
 * per-answer counts cover the three recorded answers only, for the same reason.
 *
 * DEPARTMENTS ARE LISTED, NOT LEAGUE-TABLED
 * -----------------------------------------
 * The server returns them alphabetically and this page keeps that order. A
 * completion percentage sorted descending is a league table of managers.
 */
const Tile = ({ label, value, hint, tone = 'neutral', to }) => {
  const body = (
    <>
      <span className="memo-tile-value">{value ?? '—'}</span>
      <span className="memo-tile-label">{label}</span>
      {hint && <span className="memo-tile-hint">{hint}</span>}
    </>
  );
  return to
    ? <Link to={to} className={`memo-tile tone-${tone}`}>{body}</Link>
    : <span className={`memo-tile tone-${tone}`}>{body}</span>;
};

const HRAppraisalDashboard = () => {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard,
  });

  if (isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  const hr = data?.hr;
  if (!hr) {
    return (
      <div className="page memo-page">
        <div className="lr-page-head">
          <div>
            <h1 className="lr-page-title">Appraisal Cycle</h1>
            <p className="lr-page-sub">
              Organisation-wide appraisal figures are available to HR and
              Admin only.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const completion = hr.cycle_completion || {};
  const training = hr.training_needs || {};

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Appraisal Cycle</h1>
          <p className="lr-page-sub">{hr.note}</p>
        </div>
        <Link className="btn btn-ghost btn-sm" to="/appraisals/reports">
          Reports →
        </Link>
      </div>

      <section className="memo-dash-section" aria-labelledby="hr-comp-h">
        <div className="memo-dash-head">
          <h3 id="hr-comp-h">
            <TrendingUp size={15} aria-hidden="true" /> Cycle Completion
          </h3>
        </div>
        <div className="memo-tiles">
          <Tile label="Appraisals" value={completion.total} tone="info" />
          <Tile label="Closed" value={completion.closed} tone="ok" />
          <Tile label="In Progress" value={completion.in_progress} />
          <Tile label="Completion" value={`${completion.percent ?? 0}%`}
            tone="ok"
            hint={`${completion.closed ?? 0} of ${completion.total ?? 0} closed`} />
        </div>
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-rev-h">
        <div className="memo-dash-head">
          <h3 id="hr-rev-h">Pending Reviews</h3>
        </div>
        <div className="memo-tiles">
          <Tile label="Awaiting Self Assessment"
            value={hr.review_status?.awaiting_self_assessment} tone="urgent" />
          <Tile label="With Supervisor"
            value={hr.review_status?.with_supervisor} tone="warn" />
          <Tile label="With Committee"
            value={hr.review_status?.with_committee} tone="warn" />
        </div>
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-stage-h">
        <div className="memo-dash-head">
          <h3 id="hr-stage-h">Where the round is</h3>
        </div>
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">
              Appraisals by stage, in workflow order
            </caption>
            <thead>
              <tr><th scope="col">Stage</th><th scope="col">Appraisals</th></tr>
            </thead>
            <tbody>
              {(hr.by_stage || []).map((row) => (
                <tr key={row.status}>
                  <th scope="row">{row.label}</th>
                  <td>{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-dept-h">
        <div className="memo-dash-head">
          <h3 id="hr-dept-h">
            <Users size={15} aria-hidden="true" /> Department Progress
          </h3>
        </div>
        <p className="lr-page-sub">
          Alphabetical. Process completion only — this is not a comparison of
          how departments performed.
        </p>
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">
              Appraisal completion by department, alphabetically
            </caption>
            <thead>
              <tr>
                <th scope="col">Department</th>
                <th scope="col">Appraisals</th>
                <th scope="col">Closed</th>
                <th scope="col">Completion</th>
              </tr>
            </thead>
            <tbody>
              {(hr.by_department || []).map((row) => (
                <tr key={row.department}>
                  <th scope="row">{row.department}</th>
                  <td>{row.total}</td>
                  <td>{row.closed}</td>
                  <td>
                    {row.completion_percent}%
                    <span className="memo-tile-hint">
                      {row.closed} of {row.total}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-train-h">
        <div className="memo-dash-head">
          <h3 id="hr-train-h">
            <GraduationCap size={15} aria-hidden="true" /> Training Needs
          </h3>
        </div>
        <div className="memo-tiles">
          <Tile label="Requests" value={training.total} tone="info" />
          <Tile label="Identified" value={training.identified} />
          <Tile label="Approved" value={training.approved} tone="ok" />
        </div>
        {/* Split by kind because only two of the four cost money. Before the
            field existed this total was quoted as a training spend, and it
            included every mentoring pairing and stretch assignment. */}
        <h4 className="apr-sub-h">By type</h4>
        <div className="memo-tiles">
          {(training.by_kind || []).map((row) => (
            <Tile key={row.kind} label={row.label} value={row.count} />
          ))}
        </div>
        <h4 className="apr-sub-h">By priority</h4>
        <div className="memo-tiles">
          {(training.by_priority || []).map((row) => (
            <Tile key={row.priority} label={row.label} value={row.count} />
          ))}
        </div>
        {training.top_requests?.length > 0 && (
          <>
            <h4 className="apr-sub-h">Most requested</h4>
            <ul className="apr-cited">
              {training.top_requests.map((row) => (
                <li key={row.title}>{row.title} — {row.count}</li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-delay-h">
        <div className="memo-dash-head">
          <h3 id="hr-delay-h">
            <Clock size={15} aria-hidden="true" /> Review Delays
          </h3>
        </div>
        {/* The ONE list on this page ordered by a number, and deliberately so:
            it ranks RECORDS by how long they have waited, not people by
            anything. The reviewer is named because a delay nobody owns is a
            delay nobody clears — and the person waiting is usually the employee
            whose appraisal it is. */}
        <p className="lr-page-sub">
          Appraisals sitting at a review stage, longest wait first. This
          measures the process, not the people in it.
        </p>
        {hr.review_delays?.length ? (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">
                Appraisals awaiting a review decision, longest wait first
              </caption>
              <thead>
                <tr>
                  <th scope="col">Employee</th>
                  <th scope="col">Department</th>
                  <th scope="col">Stage</th>
                  <th scope="col">With</th>
                  <th scope="col">Waiting</th>
                </tr>
              </thead>
              <tbody>
                {hr.review_delays.map((row) => (
                  <tr key={`${row.employee}-${row.stage}`}>
                    <th scope="row">{row.employee}</th>
                    <td>{row.department}</td>
                    <td>{row.stage}</td>
                    <td>{row.with_whom}</td>
                    <td className={row.days_waiting >= 14 ? 'task-late' : undefined}>
                      {row.days_waiting} day{row.days_waiting === 1 ? '' : 's'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="lr-page-sub">
            Nothing is sitting at a review stage.
          </p>
        )}
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-dev-h">
        <div className="memo-dash-head">
          <h3 id="hr-dev-h">
            <Target size={15} aria-hidden="true" /> Growth Plans
          </h3>
        </div>
        {/* Counted by AREA, never by person. "Six people need presentation
            skills" is a training decision; "these six people" is a list that
            would be read as a ranking. */}
        <p className="lr-page-sub">
          Development actions across the organisation, grouped by area. Nobody
          is named — a shared gap is a training decision, not a list of people.
        </p>
        <div className="memo-tiles">
          <Tile label="Actions" value={hr.development_plans?.total}
            tone="info" />
          {(hr.development_plans?.by_status || []).map((row) => (
            <Tile key={row.status} label={row.label} value={row.count} />
          ))}
        </div>
        {hr.development_plans?.top_areas?.length > 0 && (
          <>
            <h4 className="apr-sub-h">Most common areas</h4>
            <ul className="apr-cited">
              {hr.development_plans.top_areas.map((row) => (
                <li key={row.area}>{row.area} — {row.count}</li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-promo-h">
        <div className="memo-dash-head">
          <h3 id="hr-promo-h">Promotion Readiness</h3>
        </div>
        <div className="task-callout is-warn" role="note">
          <Info size={13} aria-hidden="true" /> Recommendations recorded by
          people, with their reasons. Nothing here is computed or ranked, and
          anybody without a recommendation is simply absent — not marked
          unready.
        </div>
        <div className="memo-tiles">
          {(hr.promotion_by_readiness || []).map((row) => (
            <Tile key={row.value} label={row.label} value={row.count} />
          ))}
        </div>
        {hr.promotion_readiness?.length ? (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">
                Promotion recommendations, alphabetically by name. Not ranked.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Employee</th>
                  <th scope="col">Department</th>
                  <th scope="col">Recommendation</th>
                  <th scope="col">Rationale</th>
                  <th scope="col">Cycle</th>
                </tr>
              </thead>
              <tbody>
                {hr.promotion_readiness.map((row) => (
                  <tr key={`${row.employee}-${row.recommended_in}`}>
                    <th scope="row">{row.employee}</th>
                    <td>{row.department}</td>
                    <td>{row.readiness_label}</td>
                    <td>{row.rationale || '—'}</td>
                    <td>{row.recommended_in}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="lr-page-sub">No recommendations recorded yet.</p>
        )}
      </section>

      <section className="memo-dash-section" aria-labelledby="hr-succ-h">
        <div className="memo-dash-head">
          <h3 id="hr-succ-h">Succession Planning</h3>
        </div>
        <p className="lr-page-sub">
          Roles people are being developed towards, as recorded by their
          supervisor. Not a shortlist and not an ordering.
        </p>
        {hr.succession?.length ? (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">
                Succession notes, alphabetically by name
              </caption>
              <thead>
                <tr>
                  <th scope="col">Employee</th>
                  <th scope="col">Department</th>
                  <th scope="col">Being developed towards</th>
                </tr>
              </thead>
              <tbody>
                {hr.succession.map((row) => (
                  <tr key={`${row.employee}-${row.role}`}>
                    <th scope="row">{row.employee}</th>
                    <td>{row.department}</td>
                    <td>{row.role}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="lr-page-sub">Nothing recorded yet.</p>
        )}
      </section>
    </div>
  );
};

export default HRAppraisalDashboard;
