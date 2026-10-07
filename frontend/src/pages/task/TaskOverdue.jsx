import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Download } from 'lucide-react';

import { taskService } from '../../services/taskService';
import ExportButtons from '../../components/common/ExportButtons';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import { EMPTY } from '../../services/emptyStates';

/**
 * The overdue screen (Phase T3, Part 6).
 *
 * Worst first, because the question this page answers is "what needs chasing
 * now" and sorting by anything else buries it. The columns are the ones the
 * specification names — days late, assignee, department, priority, reviewer —
 * and reviewer is there for a reason: some of what looks overdue is waiting on
 * the reviewer, not the assignee, and a chasing list that cannot tell the
 * difference gets aimed at the wrong person.
 *
 * The screen is scoped like everything else: a department head sees their
 * department's, an employee sees their own.
 */
const severity = (days) => {
  if (days >= 14) return 'no';
  if (days >= 7) return 'warn';
  return 'wait';
};

const TaskOverdue = () => {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'overdue-screen'],
    queryFn: () => taskService.getOverdue(),
    refetchInterval: 60_000,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  const rows = data?.rows || [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Overdue Tasks</h1>
          <p className="lr-page-sub">
            Past their due date and still open — completed work is not chased
          </p>
        </div>
        {rows.length > 0 && (
          <ExportButtons
            download={(format) => taskService.downloadReport('overdue', format)}
            name="overdue-tasks" formats={['csv']} labelPrefix="Export " />
        )}
      </div>

      {rows.length === 0 && (
        <p className="task-sub">
          <AlertTriangle size={13} aria-hidden="true" /> {EMPTY.nothingOverdue}
        </p>
      )}

      {rows.length > 0 && (
        <>
          <div className="memo-tiles">
            <span className="memo-tile tone-danger">
              <span className="memo-tile-value">{data.summary.total}</span>
              <span className="memo-tile-label">Overdue tasks</span>
            </span>
            <span className="memo-tile tone-danger">
              <span className="memo-tile-value">{data.summary.worst_days}</span>
              <span className="memo-tile-label">Worst, in days</span>
            </span>
          </div>

          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead>
                <tr>
                  <th scope="col">Days Overdue</th>
                  <th scope="col">Task No</th>
                  <th scope="col">Task</th>
                  <th scope="col">Assignee</th>
                  <th scope="col">Department</th>
                  <th scope="col">Priority</th>
                  <th scope="col">Reviewer</th>
                  <th scope="col">Due</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.task_id}>
                    <td>
                      {/* The number in words as well as a colour — a red cell
                          alone is invisible when printed or to a colour-blind
                          reader, and this list gets printed. */}
                      <span className={`min-status is-${severity(row.overdue_days)}`}>
                        {row.overdue_days} day{row.overdue_days === 1 ? '' : 's'}
                      </span>
                    </td>
                    <td style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>
                      <Link to={`/tasks/${row.task_id}`}>{row.task_number}</Link>
                    </td>
                    <td>{row.title}</td>
                    <td>{row.assignee}</td>
                    <td>{row.department}</td>
                    <td>{row.priority}</td>
                    <td>{row.reviewer}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{row.due_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
};

export default TaskOverdue;
