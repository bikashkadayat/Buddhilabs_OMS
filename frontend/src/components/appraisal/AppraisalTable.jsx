import React from 'react';
import { Link } from 'react-router-dom';

import { TOTAL_STAGES } from './appraisalLabels';

/**
 * A list of appraisals (Phase APM-03b).
 *
 * SERVER ORDER IS PRESERVED, AND THERE IS NO SORT CONTROL
 * -------------------------------------------------------
 * The server returns people alphabetically. This table renders them in the
 * order it received and offers no column sorting, because sorting a list of
 * PEOPLE by any appraisal attribute — stage, goal count, completion — produces
 * a ranking, and a ranking of colleagues is the one thing this module exists
 * not to produce. A manager who wants to know who is behind reads the "waiting
 * on you" flag, which is about the PROCESS rather than the person.
 *
 * Progress is shown as the stage, never as a percentage of a person.
 */
const AppraisalTable = ({ rows = [], showEmployee = true, emptyMessage }) => {
  if (!rows.length) {
    return (
      <p className="lr-page-sub">
        {emptyMessage || 'No appraisals here.'}
      </p>
    );
  }

  return (
    <div className="lr-table-wrap">
      <table className="lr-table">
        <caption className="sr-only">
          Appraisals, in the order the server returned them (alphabetical by
          name). Not ranked.
        </caption>
        <thead>
          <tr>
            {showEmployee && <th scope="col">Employee</th>}
            <th scope="col">Cycle</th>
            <th scope="col">Department</th>
            <th scope="col">Stage</th>
            <th scope="col">Goals</th>
            <th scope="col">Open</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {showEmployee && (
                <th scope="row">
                  {row.employee_name}
                  {row.designation && (
                    <div className="memo-tile-hint">{row.designation}</div>
                  )}
                </th>
              )}
              <td>{row.cycle_name}</td>
              <td>{row.department_name || '—'}</td>
              <td>
                {row.status_label}
                <div className="memo-tile-hint">
                  Stage {row.stage_index} of {TOTAL_STAGES}
                </div>
              </td>
              <td>
                {row.goal_count ?? 0}
                {row.goal_weight_total !== undefined
                  && row.goal_weight_total !== 100 && (
                  <span className="task-late">
                    weights total {row.goal_weight_total}%
                  </span>
                )}
              </td>
              <td>
                <Link className="btn btn-ghost btn-xs"
                  to={`/appraisals/${row.id}`}>
                  Open {row.employee_name}’s appraisal
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default AppraisalTable;
