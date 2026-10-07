import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link2, Search, X } from 'lucide-react';

import { taskService } from '../../services/taskService';
import { statusTone } from './taskLabels';
import { describeApiError } from '../../services/apiErrors';

/**
 * What this task is waiting for, and what is waiting on it
 * (Phase TASK-GOVERNANCE-HARDENING).
 *
 * TWO DIRECTIONS, AND THEY ARE NOT THE SAME THING
 * -----------------------------------------------
 * "Waiting for" is a gate on THIS task: while any row there is unfinished, this
 * task cannot start, be submitted, or be marked done. "Blocking" is the mirror -
 * other people's work held up by this one - and it carries no controls at all,
 * because it is not this task's to change. Showing only the first would hide the
 * cost of letting this task slip, which is the thing a reader most needs.
 *
 * THE ROWS SAY WHY THEY STILL COUNT
 * ---------------------------------
 * Each row carries the prerequisite's number, title and CURRENT STATUS, so
 * "waiting" is legible rather than a claim. A satisfied row stays visible,
 * struck through and marked done, rather than disappearing: a dependency that
 * vanishes when it is met leaves somebody wondering whether they imagined it.
 */
const DependencyRow = ({ row, onRemove, canManage, removing }) => (
  <li className={`dep-row${row.is_satisfied ? ' is-done' : ''}`}>
    <span className={`min-status is-${statusTone(row.depends_on_status)}`}>
      {row.depends_on_status_label}
    </span>
    <Link to={`/tasks/${row.depends_on}`} className="dep-link">
      <b>{row.depends_on_number}</b> {row.depends_on_title}
    </Link>
    <span className="task-sub dep-kind">{row.kind_label}</span>
    {canManage && (
      <button type="button" className="dep-remove" disabled={removing}
        aria-label={`Stop waiting for ${row.depends_on_number}`}
        onClick={() => onRemove(row.id)}>
        <X size={13} />
      </button>
    )}
  </li>
);

const DependencyPanel = ({ task }) => {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState('blocked_by');
  const [error, setError] = useState('');

  const canManage = Boolean(task.capabilities?.can_manage_dependencies);
  const waitingFor = task.dependencies || [];
  const blocking = task.dependents || [];

  // Candidates come from the ordinary task search, so a person can only ever
  // pick work they are already allowed to see - the server refuses anything
  // else, and offering it here would be an invitation to a 403.
  const { data: results } = useQuery({
    queryKey: ['tasks', 'dependency-search', search],
    queryFn: () => taskService.getTasks({ search: search.trim(), scope: 'all' }),
    enabled: canManage && search.trim().length >= 2,
    staleTime: 30_000,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['tasks'] });

  const add = useMutation({
    mutationFn: (id) => taskService.addDependency(task.id, id, kind),
    onSuccess: () => { setSearch(''); setError(''); refresh(); },
    onError: (err) => setError(describeApiError(err, 'The dependency could not be added.')),
  });
  const remove = useMutation({
    mutationFn: (rowId) => taskService.removeDependency(task.id, rowId),
    onSuccess: () => { setError(''); refresh(); },
    onError: (err) => setError(describeApiError(err, 'The dependency could not be removed.')),
  });

  const candidates = (results?.results || results || [])
    .filter((row) => row.id !== task.id
      && !waitingFor.some((dep) => dep.depends_on === row.id));

  return (
    <section className="task-section" aria-labelledby="dep-title">
      <h3 id="dep-title"><Link2 size={15} aria-hidden="true" /> Dependencies</h3>

      <h4 className="dep-heading">Waiting for</h4>
      {waitingFor.length === 0 && (
        <p className="task-sub">Nothing is holding this task up.</p>
      )}
      {waitingFor.length > 0 && (
        <ul className="dep-list">
          {waitingFor.map((row) => (
            <DependencyRow key={row.id} row={row} canManage={canManage}
              removing={remove.isPending} onRemove={remove.mutate} />
          ))}
        </ul>
      )}

      {canManage && (
        <div className="dep-add">
          <label className="lr-field">
            <span className="sr-only">Search for the task this one waits for</span>
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by task number or title…"
              aria-label="Search for the task this one waits for" />
          </label>
          <label className="lr-field">
            <span className="sr-only">Relationship</span>
            <select value={kind} onChange={(e) => setKind(e.target.value)}
              aria-label="Relationship">
              <option value="blocked_by">Blocked by</option>
              <option value="depends_on">Depends on</option>
            </select>
          </label>
        </div>
      )}

      {canManage && search.trim().length >= 2 && (
        <ul className="task-picker" aria-label="Search results">
          {candidates.length === 0 && (
            <li className="task-sub">
              <Search size={12} aria-hidden="true" /> No task you can see matches that.
            </li>
          )}
          {candidates.slice(0, 8).map((row) => (
            <li key={row.id}>
              <button type="button" disabled={add.isPending}
                onClick={() => add.mutate(row.id)}>
                <b>{row.task_number}</b>
                <span className="task-sub">
                  {row.title} · {row.status_label}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="task-field-error" role="alert">{error}</p>}

      {blocking.length > 0 && (
        <>
          <h4 className="dep-heading">Blocking</h4>
          <ul className="dep-list">
            {blocking.map((row) => (
              <li key={row.id} className="dep-row">
                <span className="task-sub dep-kind">{row.task_status_label}</span>
                <Link to={`/tasks/${row.task}`} className="dep-link">
                  <b>{row.task_number}</b> {row.task_title}
                </Link>
                <span className="task-sub dep-kind">waits for this</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
};

export default DependencyPanel;
