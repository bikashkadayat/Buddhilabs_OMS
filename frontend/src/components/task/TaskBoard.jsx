import React from 'react';

import TaskCard from './TaskCard';

/**
 * The board (Phase T3, Part 1): six columns, cards, read-only.
 *
 * THE COLUMNS COME FROM THE SERVER
 * --------------------------------
 * `GET /api/v1/tasks/board/` returns the columns WITH the statuses each one
 * covers, so the status-to-column mapping exists once (tasks/board.py) instead
 * of once here and once there. Two copies of a mapping is two copies that can
 * disagree, and the symptom — a task claimed by neither side and therefore
 * shown nowhere — reads as data loss rather than a configuration gap.
 *
 * READ-ONLY BY DESIGN — THERE IS NO DRAG AND DROP
 * -----------------------------------------------
 * Dragging a card between columns would be a status change, and every status
 * change in this system runs through a named workflow transition with its own
 * guard, its own mandatory reason where one applies, and its own timeline row
 * (tasks/workflow.py). A drag has none of that: it cannot ask a reviewer WHY
 * they are sending work back, and it would happily move a task into a state the
 * engine refuses, leaving the board and the database disagreeing until the next
 * refresh. So the board shows where everything is, and the task's own page is
 * where it moves — which is also where the reason box lives.
 *
 * Empty columns are KEPT. An empty Review column says nothing is waiting on a
 * reviewer, which is exactly what a manager opening the board wants to know.
 */
/**
 * Column counts above the board (Phase T5.1, Part 4).
 *
 * Straight from the board payload the columns are already rendered from — no
 * second request and no arithmetic here, so the summary can never disagree with
 * the columns underneath it.
 *
 * Empty columns are KEPT, for the same reason the board keeps them: an empty
 * Review column says nothing is waiting on a decision, which is information.
 * A summary that dropped the zeroes would make "nothing in review" and "no
 * review column" look identical.
 *
 * NO ARIA LIST ROLES. The board below is already a list of these same six
 * columns; a second list means a screen reader announces every column twice.
 * The count and its label sit adjacent in the DOM, which reads correctly with
 * no role at all.
 */
const BoardSummary = ({ columns }) => {
  const total = columns.reduce((sum, c) => sum + (c.count || 0), 0);
  if (!total) return null;
  return (
    <div className="tsk-board-summary">
      {columns.map((column) => (
        <span key={column.key} className="tsk-board-stat">
          <span className="tsk-board-stat-value">{column.count ?? 0}</span>
          <span className="tsk-board-stat-label">{column.label}</span>
        </span>
      ))}
    </div>
  );
};

const TaskBoard = ({ columns = [] }) => (
  <>
    <BoardSummary columns={columns} />
    <div className="task-board" role="list" aria-label="Tasks by status">
    {columns.map((column) => (
      <section key={column.key} className="task-board-col" role="listitem"
        aria-label={`${column.label}: ${column.count} tasks`}>
        <header className="task-board-head">
          <h3>{column.label}</h3>
          <span className="task-board-count">{column.count}</span>
        </header>
        <div className="task-board-cards">
          {(column.tasks || []).map((task) => (
            /* showStatus, even though the column already implies it: two
               statuses share the Assigned column and two share Completed, and
               a card that hides which one it is makes the reader guess. */
            <TaskCard key={task.id} task={task} showStatus />
          ))}
          {column.count === 0 && (
            <p className="task-board-empty">Nothing here.</p>
          )}
        </div>
      </section>
    ))}
    </div>
  </>
);

export default TaskBoard;
