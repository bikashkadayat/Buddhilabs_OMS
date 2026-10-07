import React from 'react';
import { Lock } from 'lucide-react';
import { HEALTH_COLOURS, fmtHours, fmtNum, fmtPct, healthBand } from './chartTheme';

/**
 * Departments ranked by attendance compliance, and nothing else.
 *
 * Three rules the markup enforces rather than assumes:
 *
 * * **Compliance orders the table.** The health score is shown and charted but
 *   never sorts it — the phase brief ranks on compliance alone.
 * * **An unranked row still appears**, with the reason visible. A small
 *   department that silently vanished would read as a bug; one labelled
 *   "too small to rank" reads as a decision, which it is.
 * * **A redacted row shows its rank and nothing else.** A department head sees
 *   where they sit without learning another team's numbers.
 */
const HealthPill = ({ score }) => {
  const band = healthBand(score);
  return (
    <span className="an-health" title={`Health score ${score ?? 'unavailable'}`}>
      <span className="an-health-dot" style={{ background: HEALTH_COLOURS[band.tone] }}
            aria-hidden="true" />
      {score === null || score === undefined ? '—' : fmtNum(score)}
      <span className="sr-only"> — {band.label}</span>
    </span>
  );
};

const DepartmentRankTable = ({ rows = [], showHealth = true, orgAverage }) => {
  if (!rows.length) {
    return <p className="wf-muted">No departments in scope.</p>;
  }

  return (
    <div className="an-table-wrap">
      <table className="an-table an-rank-table">
        <caption className="sr-only">
          Departments ranked by attendance compliance
        </caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Department</th>
            <th scope="col">Headcount</th>
            <th scope="col">Compliance</th>
            <th scope="col">Present</th>
            <th scope="col">Late</th>
            <th scope="col">Absent</th>
            <th scope="col">Overtime</th>
            {showHealth && <th scope="col">Health</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={row.department_id ?? `${row.department}-${index}`}
              className={row.redacted ? 'an-row-redacted' : undefined}
            >
              <td>{row.rank ?? '—'}</td>
              <th scope="row">
                {row.department}
                {row.redacted && (
                  <span className="an-redacted-note">
                    <Lock size={11} aria-hidden="true" /> outside your scope
                  </span>
                )}
                {row.small_sample && !row.redacted && (
                  <span className="wf-badge wf-badge-info wf-badge-sm">
                    too small to rank
                  </span>
                )}
              </th>
              <td>{row.headcount ?? '—'}</td>
              <td className="an-num">{fmtPct(row.compliance_pct)}</td>
              <td className="an-num">{fmtPct(row.present_pct)}</td>
              <td className="an-num">{fmtPct(row.late_pct)}</td>
              <td className="an-num">{fmtPct(row.absent_pct)}</td>
              <td className="an-num">{fmtHours(row.overtime_hours)}</td>
              {showHealth && <td><HealthPill score={row.health_score} /></td>}
            </tr>
          ))}
        </tbody>
        {orgAverage !== null && orgAverage !== undefined && (
          <tfoot>
            <tr>
              <td colSpan={3}>Organisation average</td>
              <td className="an-num">{fmtPct(orgAverage)}</td>
              <td colSpan={showHealth ? 5 : 4} />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
};

export default DepartmentRankTable;
