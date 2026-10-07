import React from 'react';
import { Link } from 'react-router-dom';
import {
  Building2, CalendarDays, MessageSquare, Paperclip, ShieldCheck,
} from 'lucide-react';

import { dueLabel, fmtDate, priorityTone, statusTone } from './taskLabels';

/**
 * A board card (Phase T2.8).
 *
 * Carries exactly what the specification asks a card to carry: title,
 * description, owner, department, priority, due date, status, evidence count and
 * comment count, plus the progress and checklist tally T2.8 added. The task
 * KIND left the card with the three kinds themselves (TASK-SIMPLIFICATION). Everything else belongs on the detail page — a
 * card that tries to be the task is one nobody can scan.
 *
 * The description is a SERVER-trimmed `summary`, not the full text clipped in
 * CSS: a board of fifty cards would otherwise ship fifty full briefs to draw
 * fifty two-line excerpts.
 *
 * The counts come from the LIST payload, annotated on the queryset server-side,
 * so a board of fifty cards is one query rather than a hundred and fifty. A
 * count of zero renders nothing at all rather than "0": a row of zeroes is
 * noise, and the absence already says it.
 *
 * The whole card is one link. The counts are not separately clickable — there is
 * one place to go from a card, and that is the task.
 *
 * CLASS NAMES ARE `.task-board-card*`, NOT `.task-card*`: the create / edit
 * form's section cards own `.task-card`, and when both were called that the
 * form cards inherited this card's hover lift and padding.
 */
const TaskCard = ({ task, showStatus = true }) => {
  const assignees = task.assignee_names || [];
  const overdue = task.is_overdue;

  return (
    <Link to={`/tasks/${task.id}`} className="task-board-card">
      <span className="task-card-top">
        <span className={`min-status is-${priorityTone(task.priority)}`}>
          {task.priority_label}
        </span>
        {showStatus && (
          <span className={`min-status is-${statusTone(task.status)}`}>
            {task.status_label}
          </span>
        )}
      </span>

      <span className="task-board-card-title">{task.title}</span>
      <span className="task-card-number">{task.task_number}</span>
      {task.summary && <span className="task-card-summary">{task.summary}</span>}
      <span className="task-card-top">
        {task.department_name && (
          <span className="task-kind-tag">
            <Building2 size={11} aria-hidden="true" /> {task.department_name}
          </span>
        )}
      </span>

      <span className="task-bar" role="img"
        aria-label={`${task.progress_percent}% complete`}>
        <span className="task-bar-fill" style={{ width: `${task.progress_percent}%` }} />
      </span>
      <span className="task-card-progress">
        {task.progress_percent}%
        {task.checklist_total > 0
          && ` · ${task.checklist_done}/${task.checklist_total} checklist`}
        {/* The list payload carries the subtask tally too (Phase
            TASK-SUBTASKS); while any exist they are what the bar is made of. */}
        {task.subtask_total > 0
          && ` · ${task.subtask_done}/${task.subtask_total} subtasks`}
      </span>

      {task.reviewer_name && (
        // The reviewer, because a card that shows only the assignee cannot
        // answer "who is this waiting on" — and for anything in Review, the
        // reviewer IS the answer.
        <span className="task-card-reviewer">
          <ShieldCheck size={11} aria-hidden="true" /> {task.reviewer_name}
        </span>
      )}

      <span className="task-card-foot">
        <span className="task-card-who">
          {/* Whose task it is. With nobody assigned the owner is whoever raised
              it — naming them beats "Unassigned", which says who it ISN'T. */}
          {assignees.length === 0 && (
            task.owner_name ? <em>{task.owner_name} · unassigned</em> : <em>Unassigned</em>
          )}
          {assignees.length === 1 && assignees[0]}
          {/* Two names is still readable; beyond that the count is the useful
              fact, and the detail page has the list. */}
          {assignees.length === 2 && assignees.join(', ')}
          {assignees.length > 2 && `${assignees[0]} +${assignees.length - 1}`}
        </span>

        <span className="task-card-meta">
          {task.due_date && (
            <span className={overdue ? 'task-late' : 'task-sub'}
              title={dueLabel(task.due_date)}>
              <CalendarDays size={12} aria-hidden="true" /> {fmtDate(task.due_date)}
            </span>
          )}
          {task.comment_count > 0 && (
            <span className="task-sub" title={`${task.comment_count} comments`}>
              <MessageSquare size={12} aria-hidden="true" /> {task.comment_count}
            </span>
          )}
          {task.attachment_count > 0 && (
            <span className="task-sub" title={`${task.attachment_count} attachments`}>
              <Paperclip size={12} aria-hidden="true" /> {task.attachment_count}
            </span>
          )}
          {task.evidence_count > 0 && (
            <span className="task-sub" title={`${task.evidence_count} pieces of evidence`}>
              <ShieldCheck size={12} aria-hidden="true" /> {task.evidence_count}
            </span>
          )}
        </span>
      </span>
    </Link>
  );
};

export default TaskCard;
