import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Filter, X } from 'lucide-react';

import { taskService } from '../../services/taskService';
import { TASK_PRIORITIES, TASK_STATUSES } from './taskLabels';

/**
 * The advanced filter (Phase T3, Part 2): status, assignee, department,
 * priority and a date range.
 *
 * THE OPTIONS COME FROM WHAT THE CALLER CAN SEE
 * ---------------------------------------------
 * Departments and assignees are fetched from `/tasks/filter-options/`, which
 * derives them from the caller's OWN visible task set rather than from the staff
 * directory. Two reasons, and the second is the important one: a filter listing
 * departments somebody has no tasks in is a list of dead ends, and a filter
 * built from the directory would show an employee the shape of an organisation
 * they have no business with.
 *
 * Status and priority are enumerations rather than data, so they come from the
 * shared constants and need no request.
 *
 * FILTERS ARE APPLIED ON CHANGE, NOT ON A BUTTON
 * ----------------------------------------------
 * Every control writes straight through to the query. An "Apply" button means a
 * state where the controls and the results disagree, and somebody always reads
 * the table before pressing it.
 */
const EMPTY = {};

const TaskFilters = ({ value = EMPTY, onChange }) => {
  const [open, setOpen] = useState(false);

  const { data: options } = useQuery({
    queryKey: ['tasks', 'filter-options'],
    queryFn: taskService.getFilterOptions,
    staleTime: 5 * 60 * 1000,
    // A filter that cannot load its options must not break the page it sits on;
    // status and priority still work from the local constants.
    retry: false,
  });

  const set = (key) => (e) => {
    const next = { ...value };
    if (e.target.value) next[key] = e.target.value;
    else delete next[key];
    onChange(next);
  };

  const activeCount = Object.keys(value).length;

  return (
    <div className="task-filters">
      <div className="task-filters-head">
        <button type="button" className="lr-btn" aria-expanded={open}
          onClick={() => setOpen(!open)}>
          <Filter size={14} /> Filters
          {activeCount > 0 && (
            <span className="task-filter-count">{activeCount}</span>
          )}
        </button>
        {activeCount > 0 && (
          <button type="button" className="task-link-btn"
            onClick={() => onChange({})}>
            <X size={12} /> Clear all
          </button>
        )}
      </div>

      {open && (
        <div className="task-filters-grid">
          <label className="lr-field">
            <span>Status</span>
            <select value={value.status || ''} onChange={set('status')}>
              <option value="">All statuses</option>
              {TASK_STATUSES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </label>

          <label className="lr-field">
            <span>Priority</span>
            <select value={value.priority || ''} onChange={set('priority')}>
              <option value="">All priorities</option>
              {TASK_PRIORITIES.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </label>

          <label className="lr-field">
            <span>Assignee</span>
            <select value={value.assignee || ''} onChange={set('assignee')}>
              <option value="">Anyone</option>
              {(options?.assignees || []).map((person) => (
                <option key={person.id} value={person.id}>{person.name}</option>
              ))}
            </select>
          </label>

          <label className="lr-field">
            <span>Reviewer</span>
            {/* The same people list as Assignee: whoever appears on a task the
                caller can see is whoever they can filter by, on either side. */}
            <select value={value.reviewer || ''} onChange={set('reviewer')}>
              <option value="">Anyone</option>
              {(options?.reviewers || options?.assignees || []).map((person) => (
                <option key={person.id} value={person.id}>{person.name}</option>
              ))}
            </select>
          </label>

          <label className="lr-field">
            <span>Department</span>
            <select value={value.department || ''} onChange={set('department')}>
              <option value="">All departments</option>
              {(options?.departments || []).map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </label>

          <label className="lr-field">
            <span>Due from</span>
            {/* Inclusive on both ends: somebody picking the 1st to the 31st
                means the whole month, which is what the server does too. */}
            <input type="date" value={value.due_from || ''}
              onChange={set('due_from')} />
          </label>

          <label className="lr-field">
            <span>Due to</span>
            <input type="date" value={value.due_to || ''}
              onChange={set('due_to')} />
          </label>
        </div>
      )}
    </div>
  );
};

export default TaskFilters;
