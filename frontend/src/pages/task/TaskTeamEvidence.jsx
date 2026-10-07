import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Download, FileText, Info } from 'lucide-react';

import { taskService } from '../../services/taskService';
import ExportButtons from '../../components/common/ExportButtons';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * Team task evidence, for a manager (Phase T6.5).
 *
 * THIS IS THE PAGE WHERE A RANKING WOULD BE MOST TEMPTING
 * -------------------------------------------------------
 * A manager opening a list of their reports is one sort control away from a
 * league table. So there is no sort control, the rows arrive ordered by name
 * from the server, and this component does not re-sort them. A table ordered by
 * completion rate hands somebody a judgement they did not make and cannot see
 * the basis of — and the person at the bottom of it will be asked about it.
 *
 * WHY EVERY ROW SHOWS ITS DENOMINATOR
 * -----------------------------------
 * "75%" over four tasks and "75%" over four hundred are not the same claim.
 * Each percentage is rendered with the count it was taken over, and a row with
 * too few tasks to mean anything is marked rather than quietly included.
 *
 * WHAT IT IS NOT
 * --------------
 * Not an appraisal, not a score, not an input to one without a human deciding
 * so. The note the server sends says exactly that, and it is rendered at the
 * top rather than as a footnote.
 */
const Cell = ({ value, basis }) => (
  <>
    {value === null || value === undefined ? '—' : value}
    {basis !== undefined && basis !== null && (
      <span className="task-sub">of {basis}</span>
    )}
  </>
);

const TaskTeamEvidence = () => {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'evidence', 'team'],
    queryFn: () => taskService.getTeamEvidence(),
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  const rows = data?.employees || [];
  const flagged = rows.filter((r) => r.low_volume).length;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Team Task Evidence</h1>
          <p className="lr-page-sub">
            {data.period_start} to {data.period_end} · contract v
            {data.contract_version}
          </p>
        </div>
        <div className="task-head-badges">
          <ExportButtons
            download={(format) =>
              taskService.downloadReport('employee-evidence', format)}
            name="team-evidence" />
        </div>
      </div>

      <div className="task-callout is-warn" role="note">
        <Info size={13} aria-hidden="true" /> <b>Activity evidence, not an
        assessment.</b> {data.note}
      </div>

      {flagged > 0 && (
        <div className="task-callout is-no" role="note">
          <AlertTriangle size={13} aria-hidden="true" /> {flagged} of {rows.length}{' '}
          {flagged === 1 ? 'person has' : 'people have'} too few tasks in this
          period for their percentages to mean anything. Those rows are marked.
        </div>
      )}

      {rows.length === 0 && (
        <p className="task-sub">Nobody in your scope has task activity yet.</p>
      )}

      {rows.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">
              Team task evidence, ordered alphabetically
            </caption>
            <thead>
              <tr>
                {/* Plain headers. No sort buttons anywhere — see the file
                    docstring. */}
                <th scope="col">Employee</th>
                <th scope="col">Department</th>
                <th scope="col">Assigned</th>
                <th scope="col">Completed</th>
                <th scope="col">Open</th>
                <th scope="col">Overdue</th>
                <th scope="col">Completion</th>
                <th scope="col">On Time</th>
                <th scope="col">Avg Days</th>
                <th scope="col">Reviews</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.employee_id}
                  className={row.low_volume ? 'is-low-volume' : undefined}>
                  <td style={{ fontWeight: 600 }}>
                    {row.employee_name}
                    {row.low_volume && (
                      <span className="task-sub">too few tasks to read</span>
                    )}
                  </td>
                  <td>{row.department || '—'}</td>
                  <td>{row.tasks_assigned}</td>
                  <td>{row.tasks_completed}</td>
                  <td>{row.tasks_open}</td>
                  <td className={row.tasks_overdue > 0 ? 'task-late' : undefined}>
                    {row.tasks_overdue}
                  </td>
                  <td>
                    <Cell value={`${row.completion_percent}%`}
                      basis={`${row.completion_of} tasks`} />
                  </td>
                  <td>
                    <Cell value={`${row.on_time_percent}%`}
                      basis={`${row.completed_with_due_date} dated`} />
                  </td>
                  <td>{row.average_completion_days ?? '—'}</td>
                  <td>{row.reviews_performed}</td>
                  <td>{row.evidence_files_submitted}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default TaskTeamEvidence;
