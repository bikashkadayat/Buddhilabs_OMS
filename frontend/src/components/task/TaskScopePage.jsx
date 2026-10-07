import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { LayoutGrid, List, Plus } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { taskService } from '../../services/taskService';
import { can } from '../../services/roles';
import TaskTable from './TaskTable';
import TaskBoard from './TaskBoard';
import TaskFilters from './TaskFilters';
import { Skeleton, ErrorState, EmptyState } from '../leave-records/States';

/**
 * One page, bound to a server-side scope.
 *
 * Every menu renders this with a different `scope` and column set, so a menu's
 * definition lives in exactly one place (tasks.views._apply_scope) and adding a
 * menu is a route plus a scope name — not another list implementation with its
 * own filtering bugs.
 *
 * Phase T2.8 adds a Board view: the same rows, as cards in status columns. The
 * choice is remembered per menu in component state only — not persisted —
 * because a view preference that outlives the visit is the kind of hidden state
 * that makes two people on the same URL see different things.
 *
 * @param {{ scope:string, title:string, subtitle:string, columns:string[],
 *           emptyMessage:string, showFilters?:boolean, refetchInterval?:number,
 *           defaultView?:'list'|'board' }}
 */
const TaskScopePage = ({
  scope, title, subtitle, columns, emptyMessage,
  showFilters = false, refetchInterval, defaultView = 'list', boardVariant,
}) => {
  const navigate = useNavigate();
  const { role } = useAuth();
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState({});
  const [view, setView] = useState(defaultView);

  // Everybody raises tasks, so everybody is offered the button. There is one
  // kind of task, and the creator picks who does it and who reviews it
  // (Phase TASK-SIMPLIFICATION).
  const allowCreate = can(role, 'createTask');

  // One params object for both views, so the board and the list can never be
  // showing differently-filtered data behind the same controls.
  const params = { scope, ...filters };
  if (search.trim()) params.search = search.trim();

  const listQuery = useQuery({
    queryKey: ['tasks', scope, search, filters],
    queryFn: () => taskService.getTasks(params),
    refetchInterval,
    enabled: view === 'list',
  });

  const boardQuery = useQuery({
    queryKey: ['tasks', 'board', boardVariant || 'all', scope, search, filters],
    // `variant` is passed through to the server, which owns the column set.
    // The client never filters columns of its own, or the two would disagree
    // about where a card belongs.
    queryFn: () => taskService.getBoard(
      boardVariant ? { ...params, variant: boardVariant } : params),
    refetchInterval,
    enabled: view === 'board',
  });

  const active = view === 'board' ? boardQuery : listQuery;
  const { isLoading, isError, error, refetch } = active;
  const items = listQuery.data?.results ?? listQuery.data ?? [];
  const boardColumns = boardQuery.data?.columns ?? [];
  const isEmpty = view === 'board'
    ? (boardQuery.data?.total ?? 0) === 0
    : items.length === 0;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">{title}</h1>
          <p className="lr-page-sub">{subtitle}</p>
        </div>
        <div className="task-head-badges">
          <div className="task-view-toggle" role="group" aria-label="View">
            <button type="button" aria-pressed={view === 'list'}
              className={`lr-btn${view === 'list' ? ' is-on' : ''}`}
              onClick={() => setView('list')}>
              <List size={14} /> List
            </button>
            <button type="button" aria-pressed={view === 'board'}
              className={`lr-btn${view === 'board' ? ' is-on' : ''}`}
              onClick={() => setView('board')}>
              <LayoutGrid size={14} /> Board
            </button>
          </div>
          {allowCreate && (
            <button type="button" className="lr-btn lr-btn-primary"
              onClick={() => navigate('/tasks/create')}>
              <Plus size={14} /> Create Task
            </button>
          )}
        </div>
      </div>

      <div className="memo-filters">
        <label className="lr-field">
          <span className="sr-only">Search tasks</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by task number or title…" aria-label="Search tasks" />
        </label>
      </div>

      {showFilters && (
        <TaskFilters value={filters} onChange={setFilters} />
      )}

      {isLoading && <Skeleton rows={5} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && isEmpty && (
        // ctaTo={null} suppresses the shared component's default CTA, which is
        // "Apply for leave" — correct where it was written, wrong here.
        <EmptyState message={emptyMessage}
          ctaTo={allowCreate ? '/tasks/create' : null} ctaLabel="Create Task" />
      )}
      {!isLoading && !isError && !isEmpty && (
        view === 'board'
          ? <TaskBoard columns={boardColumns} />
          : <TaskTable items={items} columns={columns} />
      )}
    </div>
  );
};

export default TaskScopePage;
