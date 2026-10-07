import React from 'react';
import TaskScopePage from '../../components/task/TaskScopePage';
import { EMPTY } from '../../services/emptyStates';

/**
 * The task lists — the module's menus, from the specification:
 *
 *   Dashboard · My Tasks · Assigned By Me · Team Tasks · Due Today · Overdue ·
 *   Completed
 *
 * Each page is the same component bound to a different server-side scope, so a
 * list's definition lives in exactly one place (tasks.views._apply_scope).
 */

/** The one queue that answers "is anything wanted from me?". */
export const NeedsMyAction = () => (
  <TaskScopePage
    scope="needs_me"
    title="Waiting for Me"
    subtitle="Tasks to accept, work in flight, and reviews sitting with you"
    columns={['priority', 'status', 'due', 'creator']}
    emptyMessage={EMPTY.nothingWaiting}
    refetchInterval={30_000}
  />
);

export const MyTasks = () => (
  <TaskScopePage
    scope="mine"
    title="My Tasks"
    subtitle="Everything assigned to you that is still live"
    columns={['priority', 'status', 'due', 'progress']}
    emptyMessage="You have no open tasks."
    showFilters
  />
);

export const AssignedByMe = () => (
  <TaskScopePage
    scope="assigned_by_me"
    title="Assigned By Me"
    subtitle="Tasks you raised or are named reviewer on"
    columns={['assignees', 'reviewer', 'priority', 'status', 'due', 'progress',
      'creator']}
    emptyMessage="You have not assigned any tasks yet."
    showFilters
  />
);

/**
 * The Department Board (Phase TASK-GOVERNANCE-HARDENING).
 *
 * A Department Head's view of everything their department is carrying, as
 * To Do / In Progress / Review / Done - the same four columns every board has
 * carried since Phase TASK-SIMPLIFICATION.
 */
export const TeamTasks = () => (
  <TaskScopePage
    scope="team"
    title="Department Board"
    subtitle="Everything your department is carrying, and where it has got to"
    columns={['assignees', 'reviewer', 'department', 'priority', 'status', 'due',
      'progress']}
    emptyMessage="Your department has no tasks yet."
    showFilters
    defaultView="board"
    boardVariant="department"
  />
);

export const DueToday = () => (
  <TaskScopePage
    scope="due_today"
    title="Due Today"
    subtitle="Your tasks with today's date on them"
    columns={['priority', 'status', 'progress']}
    emptyMessage="Nothing of yours is due today."
    refetchInterval={60_000}
  />
);

export const OverdueTasks = () => (
  <TaskScopePage
    scope="overdue"
    title="Overdue"
    subtitle="Past their due date and still open — completed work is not chased"
    columns={['assignees', 'priority', 'status', 'due', 'progress']}
    emptyMessage={EMPTY.nothingOverdue}
    refetchInterval={60_000}
  />
);

export const CompletedTasks = () => (
  <TaskScopePage
    scope="completed"
    title="Completed"
    subtitle="Approved work, and work verified and closed"
    columns={['assignees', 'creator', 'status', 'due']}
    emptyMessage="Nothing has been completed yet."
  />
);

/**
 * The Task Board (Phase TASK-MANAGEMENT-ASANA-MODEL).
 *
 * To Do / In Progress / Review / Done, over every task the viewer can see - the
 * same scope their All Tasks or My Tasks list would show, since the server
 * decides that and this page does not ask for more. It opens on the board
 * rather than the list because it is the board's rail item; the list toggle is
 * still there, because a board is a poor way to read forty rows.
 */
export const TaskBoardPage = () => (
  <TaskScopePage
    scope="all"
    title="Task Board"
    subtitle="To Do to Done, across everything you can see"
    columns={['assignees', 'priority', 'status', 'due', 'progress']}
    emptyMessage="No tasks are visible to you yet."
    showFilters
    defaultView="board"
  />
);

/* --- narrower menus: routed, not in the rail ------------------------------- */

export const AllTasks = () => (
  <TaskScopePage
    scope="all"
    title="All Tasks"
    subtitle="Every task you are allowed to see"
    columns={['assignees', 'reviewer', 'department', 'priority', 'status', 'due',
      'progress', 'creator']}
    emptyMessage="No tasks are visible to you yet."
    showFilters
  />
);

export const DraftTasks = () => (
  <TaskScopePage
    scope="drafts"
    title="Draft Tasks"
    subtitle="Started but not yet assigned to anybody"
    columns={['priority', 'due']}
    emptyMessage="You have no draft tasks."
  />
);

export const PendingReview = () => (
  <TaskScopePage
    scope="pending_review"
    title="Pending Review"
    subtitle="Submitted work waiting on a review decision"
    columns={['assignees', 'priority', 'due', 'creator']}
    emptyMessage={EMPTY.nothingToReview}
    refetchInterval={30_000}
  />
);
