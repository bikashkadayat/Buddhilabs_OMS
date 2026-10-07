import React from 'react';
import { useNavigate } from 'react-router-dom';

import {
  dueLabel, fmtDate, priorityTone, statusTone,
} from './taskLabels';

/**
 * Shared task list table.
 *
 * `columns` picks which optional columns render, so one table serves every menu
 * without a variant per menu. The column sets differ because the question each
 * menu answers differs: "Assigned By Me" needs who is doing it, "My Tasks" does
 * not, and Overdue needs how late rather than the raw date.
 *
 * @param {{ items:Object[], columns?:string[] }} props
 */
const TaskTable = ({
  items = [],
  columns = ['priority', 'status', 'due', 'progress'],
}) => {
  const navigate = useNavigate();
  const has = (name) => columns.includes(name);
  const open = (id) => navigate(`/tasks/${id}`);

  return (
    <div className="lr-table-wrap">
      <table className="lr-table task-register">
        <thead>
          <tr>
            <th scope="col">Task No</th>
            <th scope="col">Title</th>
            {has('assignees') && <th scope="col">Assigned To</th>}
            {has('creator') && <th scope="col">Assigned By</th>}
            {has('reviewer') && <th scope="col">Reviewer</th>}
            {has('department') && <th scope="col">Department</th>}
            {has('priority') && <th scope="col">Priority</th>}
            {has('status') && <th scope="col">Status</th>}
            {has('due') && <th scope="col">Due</th>}
            {has('progress') && <th scope="col">Progress</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((task) => (
            <tr key={task.id} tabIndex={0} className="lr-month-row"
              onClick={() => open(task.id)}
              onKeyDown={(e) => { if (e.key === 'Enter') open(task.id); }}>
              <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                {task.task_number}
              </td>
              <td>
                <div style={{ fontWeight: 600 }}>{task.title}</div>
                {task.checklist_total > 0 && (
                  <div className="task-sub">
                    {task.checklist_done} of {task.checklist_total} checklist items
                  </div>
                )}
              </td>
              {has('assignees') && (
                <td>{task.assignee_names?.length
                  ? task.assignee_names.join(', ') : '—'}</td>
              )}
              {has('creator') && <td>{task.created_by_name || '—'}</td>}
              {has('reviewer') && <td>{task.reviewer_name || '—'}</td>}
              {has('department') && <td>{task.department_name || '—'}</td>}
              {has('priority') && (
                <td>
                  <span className={`min-status is-${priorityTone(task.priority)}`}>
                    {task.priority_label}
                  </span>
                </td>
              )}
              {has('status') && (
                <td>
                  <span className={`min-status is-${statusTone(task.status)}`}>
                    {task.status_label}
                  </span>
                </td>
              )}
              {has('due') && (
                <td style={{ whiteSpace: 'nowrap' }}>
                  {task.due_date ? (
                    <div className="task-sub-stack">
                      <span>{fmtDate(task.due_date)}</span>
                      {/* Overdue is stated in words as well as colour: a red
                          cell alone is invisible to a colour-blind reader and
                          to anyone printing the list. */}
                      <span className={task.is_overdue ? 'task-late' : 'task-sub'}>
                        {task.is_overdue && task.overdue_days
                          ? `${task.overdue_days} day${task.overdue_days === 1 ? '' : 's'} overdue`
                          : dueLabel(task.due_date)}
                      </span>
                    </div>
                  ) : '—'}
                </td>
              )}
              {has('progress') && (
                <td style={{ minWidth: 120 }}>
                  <div className="task-bar" role="img"
                    aria-label={`${task.progress_percent}% complete`}>
                    <span className="task-bar-fill"
                      style={{ width: `${task.progress_percent}%` }} />
                  </div>
                  <span className="task-sub">{task.progress_percent}%</span>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default TaskTable;
