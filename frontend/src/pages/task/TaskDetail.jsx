import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Ban, CheckCircle2, Copy, ExternalLink, ListChecks, MessageSquare, PauseCircle,
  Pencil, PlayCircle, Send, ShieldCheck, Undo2,
} from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { taskService } from '../../services/taskService';
import AssigneeList from '../../components/task/AssigneeList';
import AttachmentPanel from '../../components/task/AttachmentPanel';
import ChecklistPanel from '../../components/task/ChecklistPanel';
import DependencyPanel from '../../components/task/DependencyPanel';
import MilestoneTrack from '../../components/task/MilestoneTrack';
import CommentThread from '../../components/task/CommentThread';
import ProgressControl from '../../components/task/ProgressControl';
import SubtaskPanel from '../../components/task/SubtaskPanel';
import RichTextEditor from '../../components/memo/RichTextEditor';
import TaskTimeline from '../../components/task/TaskTimeline';
import TeamProgress from '../../components/task/TeamProgress';
import TemplateActions from '../../components/task/TemplateActions';
import Toast from '../../components/admin/Toast';
import NoAccessState from '../../components/share/NoAccessState';
import ShareMenu from '../../components/share/ShareMenu';
import { canonicalPath, canonicalUrl } from '../../components/share/shareLinks';
import { useCopyLink } from '../../components/share/useCopyLink';
import {
  LADDER_STAGE, OFF_LADDER, TASK_LADDER, dueLabel, fmtDate, fmtDateTime, priorityTone,
  statusTone,
} from '../../components/task/taskLabels';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The task detail page.
 *
 * Its sections are the ones the specification names: Task Information,
 * Checklist, Attachments, Comments, Activity Timeline, Progress.
 *
 * EVERY BUTTON COMES FROM `capabilities`
 * --------------------------------------
 * The action bar is rendered purely from the server's `capabilities` object.
 * Nothing here re-derives who may do what — the rules live in
 * tasks/permissions.py and are enforced there, so a button that appeared
 * wrongly would still be refused, and one that is hidden is hidden because the
 * server said so rather than because this file has its own opinion.
 */

const Ladder = ({ status }) => {
  if (OFF_LADDER.has(status)) {
    return (
      <div className="task-ladder-off">
        <span className={`min-status is-${statusTone(status)}`}>
          {status === 'blocked' ? 'Blocked' : 'Cancelled'}
        </span>
        <span className="task-sub">
          {status === 'blocked'
            ? 'Outside the normal ladder until it is unblocked.'
            : 'This task was stopped and will not continue.'}
        </span>
      </div>
    );
  }
  // Three stored statuses read as "Created", so the stage is looked up rather
  // than matched on the status itself (see taskLabels.LADDER_STAGE).
  const stage = LADDER_STAGE[status] || status;
  const reached = TASK_LADDER.findIndex((s) => s.value === stage);
  return (
    <ol className="task-ladder" aria-label="Workflow progress">
      {TASK_LADDER.map((step, index) => (
        <li key={step.value}
          className={index <= reached ? 'is-done' : ''}
          aria-current={index === reached ? 'step' : undefined}>
          <span className="task-ladder-dot" aria-hidden="true" />
          <span>{step.label}</span>
        </li>
      ))}
    </ol>
  );
};

/** A transition button that collects a mandatory reason before firing. */
const ReasonAction = ({ label, icon, placeholder, onSubmit, pending, danger }) => {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  if (!open) {
    return (
      <button type="button" className={`lr-btn ${danger ? 'lr-btn-danger' : ''}`}
        onClick={() => setOpen(true)}>{icon} {label}</button>
    );
  }
  return (
    <div className="task-reason">
      <label className="lr-field">
        <span className="sr-only">{label} — reason</span>
        <textarea value={reason} rows={2} placeholder={placeholder}
          aria-label={`${label} reason`}
          onChange={(e) => setReason(e.target.value)} />
      </label>
      <div className="task-form-actions">
        <button type="button" className="lr-btn lr-btn-ghost"
          onClick={() => { setOpen(false); setReason(''); }}>Cancel</button>
        <button type="button" className={`lr-btn ${danger ? 'lr-btn-danger' : 'lr-btn-primary'}`}
          disabled={pending || reason.trim().length < 10}
          onClick={() => onSubmit(reason.trim())}>
          {/* The server requires ten characters; saying so beats a 400 the
              user has to decode from a red box. */}
          {reason.trim().length < 10 ? 'Give a reason (10+ characters)' : label}
        </button>
      </div>
    </div>
  );
};

const TaskDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [actionError, setActionError] = useState('');
  const [toast, setToast] = useState(null);
  const [copyFallback, setCopyFallback] = useState('');
  const [copy] = useCopyLink();

  const { data: task, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', id],
    queryFn: () => taskService.getTask(id),
    // The canonical-URL rename below hands the new key the task we already
    // hold (setQueryData). That hand-off is only free while the primed entry
    // counts as fresh: with the client's default staleTime of 0 the observer
    // that mounts under the new key sees stale data and fetches the same task
    // a second time. Stated here rather than inherited from main.jsx so the
    // promise in the comment below does not depend on a global default.
    // Mutations still refresh on time: invalidateQueries ignores staleTime.
    staleTime: 15_000,
  });

  /**
   * THE URL BECOMES THE CANONICAL ONE (Phase TASK-DEEP-LINK-SHARING).
   *
   * A task opened by UUID — from an old link, a notification, or the list —
   * rewrites its address to the reference form, so whatever a person copies out
   * of the address bar is the shareable one. `replace`, not `push`: this is the
   * same page at its proper name, and it must not cost a press of Back.
   */
  const reference = task?.task_number;
  useEffect(() => {
    if (!reference || id === reference) return;
    // THE CACHE IS PRIMED FIRST. The route param is the query key, so changing
    // the address would otherwise start a SECOND fetch of the task already on
    // screen and drop the page back to its skeleton — a flash on every task
    // opened from the list, which is most of them. Handing the new key the
    // object we are already holding makes the rename free.
    queryClient.setQueryData(['tasks', reference], task);
    navigate(canonicalPath('tasks', reference, id), { replace: true });
  }, [reference, id, navigate, queryClient, task]);

  const run = useMutation({
    mutationFn: ({ fn }) => fn(),
    onSuccess: (_data, { message } = {}) => {
      setActionError('');
      // Said only once the SERVER has confirmed it. A toast fired on the click
      // would be a promise the request had not yet kept
      // (Phase TASK-PROGRESS-UX-HARDENING).
      if (message) setToast({ message, tone: 'success' });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
    onError: (err) => {
      const body = err?.response?.data;
      setActionError(body?.workflow || body?.detail
        || 'That action could not be completed.');
    },
  });
  const fire = (fn, message) => run.mutate({ fn, message });

  if (isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isError) {
    // A refusal is not a failure, and must not be offered a Retry: 404 and 403
    // are the two answers the server gives for "not yours", and no amount of
    // retrying changes either (Phase TASK-DEEP-LINK-SHARING).
    const code = error?.response?.status;
    if (code === 404 || code === 403) {
      return (
        <div className="page">
          {/* The reference is echoed back so the person can see WHICH link
              failed — but only when it is a human reference. A UUID from an
              old link tells them nothing and reads as noise. */}
          <NoAccessState kind="task" backTo="/tasks" backLabel="Go to my tasks"
            reference={/^[0-9a-f-]{36}$/i.test(id) ? null : id} />
        </div>
      );
    }
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  const cap = task.capabilities || {};
  // The server's acceptance tally. Defaulted so a payload from a cached older
  // response renders as "not pending" rather than crashing on a missing key.
  const acceptance = task.acceptance || {};
  const shared = (task.assignees || []).length > 1;
  // This viewer's own assignee row, when they are on the task at all.
  const mine = (task.assignees || []).find((row) => row.user?.id === user?.id);
  const hasChecklist = (task.checklist_total || 0) > 0;
  // Subtasks (Phase TASK-SUBTASKS). The tallies come from the server; the
  // rows are the fallback for a cached payload from before the field existed.
  const subtasks = task.subtasks || [];
  const subtaskTotal = task.subtask_total ?? subtasks.length;
  const subtaskDone = task.subtask_done ?? subtasks.filter((row) => row.is_done).length;
  const hasSubtasks = subtaskTotal > 0;
  const openSubtasks = Math.max(0, subtaskTotal - subtaskDone);
  const subtaskPercent = hasSubtasks
    ? (task.subtask_percent ?? Math.round((subtaskDone / subtaskTotal) * 100))
    : null;
  const openSubtaskNote = `${openSubtasks} subtask${openSubtasks === 1 ? '' : 's'} still open`;
  // The subtask assignee choice: the task's assignees, by user id.
  const subtaskPeople = (task.assignees || [])
    .filter((row) => row.user?.id)
    .map((row) => ({ id: row.user.id, name: row.user_name }));
  // `can_view_download_log` IS `is_owner(user, task)` on the server
  // (tasks/permissions.py), so it is the exact signal rather than a guess
  // assembled from four other flags that happen to correlate with it.
  const isOwner = Boolean(cap.can_view_download_log);
  // Always the REFERENCE form, whichever way this page was reached.
  const shareUrl = canonicalUrl(canonicalPath('tasks', task.task_number, task.id));
  const copyLink = async () => {
    const ok = await copy(shareUrl);
    if (ok) setToast({ message: '✅ Task link copied', tone: 'success' });
    else setCopyFallback(shareUrl);
  };
  // Whether the Task Actions card has anything to hold. Every flag the card
  // renders a control for, asked once — a card whose heading is the only thing
  // inside it is worse than no card.
  // While subtasks are open the server withholds `can_submit_for_review` and
  // `can_close`. The person who WOULD have had the button sees it disabled
  // with the count beside it, rather than a card that silently lost a button.
  const submitHeldBySubtasks = openSubtasks > 0 && !cap.can_submit_for_review
    && Boolean(mine) && ['assigned', 'accepted', 'in_progress'].includes(task.status);
  const closeHeldBySubtasks = openSubtasks > 0 && !cap.can_close
    && isOwner && task.status === 'completed';
  const hasActions = ['can_assign', 'can_accept', 'can_request_clarification',
    'can_submit_for_review', 'can_review', 'can_close', 'can_block',
    'can_unblock', 'can_cancel'].some((flag) => cap[flag])
    || (cap.can_update_progress && ['assigned', 'accepted'].includes(task.status))
    || submitHeldBySubtasks || closeHeldBySubtasks;

  return (
    <div className="page memo-page task-detail">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">{task.title}</h1>
          <p className="lr-page-sub">
            {task.department_name || 'No department'}
            {' · raised by '}{task.created_by_name || 'Unknown'}
          </p>
          {/* The reference, promoted out of the subtitle into a control of its
              own (Phase TASK-DEEP-LINK-SHARING). It is the thing people quote,
              so clicking it copies the LINK — the number alone leaves the
              recipient searching, which is the friction this phase removes. */}
          <button type="button" className="task-ref" onClick={copyLink}
            title="Copy link to this task">
            <span className="task-ref-num">{task.task_number}</span>
            <Copy size={13} aria-hidden="true" />
            <span className="sr-only">Copy link to this task</span>
          </button>
        </div>
        <div className="task-head-badges">
          <span className={`min-status is-${statusTone(task.status)}`}>
            {/* `workflow_label` is the server's reading of the stored status:
                "Pending Acceptance" while a shared task is still waiting, and
                "Ready to Start" once everybody has accepted. `status_label` is
                untouched underneath it, and is what the reports say. */}
            {task.workflow_label || task.status_label}
          </span>
          <span className={`min-status is-${priorityTone(task.priority)}`}>
            {task.priority_label}
          </span>
          <button type="button" className="lr-btn" onClick={copyLink}>
            <Copy size={14} /> Copy link
          </button>
          <ShareMenu kind="Task" title={task.title} reference={task.task_number}
            url={shareUrl}
            onCopied={() => setToast({ message: '✅ Task link copied', tone: 'success' })}
            onFailed={setCopyFallback} />
          {/* `window.open`, not an <a href>. src/test/downloads.guard.test.js
              forbids hrefs anywhere under components|pages/task, because task
              FILES are served by an authenticated view and an anchor to one
              downloads a 401 body. This particular URL is a page, not a file —
              but the guard is worth more as an absolute rule than as one with
              an exception a future variable name could slip through, and
              window.open does the identical thing. */}
          <button type="button" className="lr-btn"
            onClick={() => window.open(shareUrl, '_blank', 'noopener,noreferrer')}>
            <ExternalLink size={14} /> Open in new tab
          </button>
          {cap.can_edit && (
            <button type="button" className="lr-btn"
              onClick={() => navigate(`${canonicalPath('tasks', task.task_number, task.id)}/edit`)}>
              <Pencil size={14} /> Edit
            </button>
          )}
        </div>
      </div>

      {/* When even the fallback cannot reach the clipboard — a locked-down
          browser, or a permissions policy — the URL is shown for the person to
          copy by hand rather than a button that silently did nothing. */}
      {copyFallback && (
        <div className="task-callout is-warn" role="status">
          <b>Copy this link:</b>{' '}
          <input className="task-copy-fallback" readOnly value={copyFallback}
            aria-label="Task link" onFocus={(e) => e.target.select()} />
        </div>
      )}

      <Ladder status={task.status} />

      {/* Created / Started / Reviewed / Completed. Beside the ladder, which
          shows where the task IS, this shows how it got there and when. */}
      <MilestoneTrack milestones={task.milestones} />

      {/* Waiting on other work? Said HERE, above the action bar, because it is
          the reason a button below is about to refuse. */}
      {(task.dependencies || []).some((row) => !row.is_satisfied) && (
        <div className="task-callout is-warn" role="status">
          <b>Waiting on other work.</b>{' '}
          This task cannot be started, submitted or marked done until{' '}
          {(task.dependencies || []).filter((row) => !row.is_satisfied)
            .map((row) => `${row.depends_on_number} (${row.depends_on_status_label})`)
            .join(', ')}{' '}
          {(task.dependencies || []).filter((row) => !row.is_satisfied).length === 1
            ? 'is finished.' : 'are finished.'}
        </div>
      )}

      {/* Waiting for the team? Said HERE, above the action bar, for the same
          reason the dependency callout is: it is why the buttons below are
          missing (Phase TASK-MULTI-ASSIGNEE-COLLABORATION). */}
      {acceptance.is_pending && (
        <div className="task-callout is-warn" role="status">
          <b>Pending acceptance — {acceptance.accepted} of {acceptance.total} accepted.</b>{' '}
          Work cannot start until everybody has accepted. Waiting for{' '}
          {(acceptance.pending_names || []).join(', ')}.{' '}
          {/* Said here because the missing buttons below imply otherwise: the
              conversation is open even though the work is not
              (Phase TASK-COLLABORATION-HARDENING). */}
          You can still comment and ask for clarification.
        </div>
      )}

      {actionError && (
        <div className="lr-error" role="alert">
          <p className="lr-error-msg">{actionError}</p>
        </div>
      )}

      {task.status === 'blocked' && task.blocked_reason && (
        <div className="task-callout is-no" role="status">
          <b>Blocked:</b> {task.blocked_reason}
        </div>
      )}
      {task.clarification_note && (
        <div className="task-callout is-warn" role="status">
          <b>Clarification requested:</b> {task.clarification_note}
        </div>
      )}

      {/* --- Task Actions, rendered entirely from server capabilities ---
          A CARD, not a loose row (Phase TASK-DETAIL-UI-POLISH). Unlabelled
          buttons floating between the callouts and the content read as part of
          whichever block they happen to sit next to; inside a titled card with
          one gap value they are plainly the things this person can DO.
          The card disappears entirely when the server grants nothing, rather
          than leaving a heading over empty space. */}
      {hasActions && (
      <section className="task-section task-actions-card"
        aria-labelledby="task-actions-heading">
        <h2 id="task-actions-heading">Task Actions</h2>
        <div className="task-actions">
        {cap.can_assign && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => fire(() => taskService.assign(task.id))}>
            <Send size={14} /> Assign
          </button>
        )}
        {cap.can_accept && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => fire(() => taskService.accept(task.id))}>
            <CheckCircle2 size={14} /> Accept Task
            {/* On shared work the count is the point: accepting is not the last
                word, and the button should not imply that it is. */}
            {shared && ` (${acceptance.accepted} of ${acceptance.total} accepted)`}
          </button>
        )}
        {cap.can_request_clarification && (
          <ReasonAction label="Request Clarification" icon={<MessageSquare size={14} />}
            placeholder="What do you need to know before accepting?"
            pending={run.isPending}
            onSubmit={(reason) => fire(
              () => taskService.requestClarification(task.id, reason))} />
        )}
        {/* Straight from Created: there is no acceptance step in the five
            stages, so Start Work is the first thing on a new task
            (Phase TASK-SIMPLIFICATION). */}
        {cap.can_update_progress
          && ['assigned', 'accepted'].includes(task.status) && (
          <button type="button" className="lr-btn"
            onClick={() => fire(() => taskService.start(task.id))}>
            <PlayCircle size={14} /> Start Work
          </button>
        )}
        {cap.can_submit_for_review && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => fire(() => taskService.submitForReview(task.id))}>
            <Send size={14} /> Submit For Review
          </button>
        )}
        {submitHeldBySubtasks && (
          <span className="task-action-held">
            <button type="button" className="lr-btn lr-btn-primary" disabled
              aria-describedby="task-submit-held">
              <Send size={14} /> Submit For Review
            </button>
            <span id="task-submit-held" className="task-sub">{openSubtaskNote}</span>
          </span>
        )}
        {cap.can_review && (
          <>
            <button type="button" className="lr-btn lr-btn-primary"
              onClick={() => fire(() => taskService.approve(task.id))}>
              <CheckCircle2 size={14} /> Approve
            </button>
            <ReasonAction label="Need Rework" icon={<Undo2 size={14} />}
              placeholder="What has to change before this can be approved?"
              pending={run.isPending}
              onSubmit={(reason) => fire(
                () => taskService.requestRework(task.id, reason))} />
          </>
        )}
        {cap.can_close && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => fire(() => taskService.close(task.id))}>
            <ShieldCheck size={14} /> Verify & Close
          </button>
        )}
        {closeHeldBySubtasks && (
          <span className="task-action-held">
            <button type="button" className="lr-btn lr-btn-primary" disabled
              aria-describedby="task-close-held">
              <ShieldCheck size={14} /> Verify & Close
            </button>
            <span id="task-close-held" className="task-sub">{openSubtaskNote}</span>
          </span>
        )}
        {cap.can_block && (
          <ReasonAction label="Block" icon={<PauseCircle size={14} />}
            placeholder="What is stopping this task?"
            pending={run.isPending}
            onSubmit={(reason) => fire(() => taskService.block(task.id, reason))} />
        )}
        {cap.can_unblock && (
          <button type="button" className="lr-btn"
            onClick={() => fire(() => taskService.unblock(task.id))}>
            <PlayCircle size={14} /> Unblock
          </button>
        )}
        {cap.can_cancel && (
          <ReasonAction label="Cancel Task" icon={<Ban size={14} />} danger
            placeholder="Why is this task being stopped?"
            pending={run.isPending}
            onSubmit={(reason) => fire(() => taskService.cancel(task.id, reason))} />
        )}
        </div>
      </section>
      )}

      <div className="task-columns">
        <div className="task-col-main">
          {/* --- Task Information --- */}
          <section className="task-section">
            <h2>Task Information</h2>
            {/* TWO BANDS, NOT ONE AUTO-FIT GRID (Phase TASK-DETAIL-UI-POLISH).
                Assignees is a LIST and the other three are single values, so an
                auto-fit grid gave them equal columns and then let the list
                overflow its share. The list takes a full-width band of its own;
                Reviewer / Department / Due Date share an even three-column band
                below it that collapses to one column on a phone. */}
            <dl className="task-facts">
              <div className="task-fact-wide">
                <dt>Assigned To</dt>
                <dd><AssigneeList rows={task.assignees || []} /></dd>
              </div>
            </dl>
            <dl className="task-facts task-facts-trio">
              <div><dt>Reviewer</dt>
                <dd>{task.reviewer_name || `${task.created_by_name} (creator)`}</dd></div>
              <div><dt>Department</dt><dd>{task.department_name || '—'}</dd></div>
              <div><dt>Due Date</dt><dd>
                {task.due_date ? (
                  <>
                    {fmtDate(task.due_date)}
                    <span className={task.is_overdue ? 'task-late' : 'task-sub'}>
                      {dueLabel(task.due_date)}
                    </span>
                  </>
                ) : 'No due date'}
              </dd></div>
            </dl>
            {/* Created and Closed are provenance, not facts anybody scans the
                card for — demoted to one quiet line rather than given two of
                the four slots the eye lands on first. */}
            <p className="task-facts-foot">
              Created {fmtDateTime(task.created_at)}
              {task.closed_at && <> · Closed {fmtDateTime(task.closed_at)}</>}
            </p>
            {task.description && (task.description_format === 'html' ? (
              // Written by the rich editor (Phase TASK-SUBTASKS). Rendered
              // through the memo editor's read-only path, which sanitises with
              // the same allowlist the server applies on write.
              <div className="task-description is-html">
                <RichTextEditor readOnly value={task.description} />
              </div>
            ) : (
              // Plain text from before the rich editor, rendered as text with
              // the author's line breaks kept.
              <p className="task-description">{task.description}</p>
            ))}
          </section>

          {/* --- Subtasks (Phase TASK-SUBTASKS) --- */}
          <section className="task-section" aria-labelledby="task-subtasks-heading">
            <h2 id="task-subtasks-heading">
              <ListChecks size={15} aria-hidden="true" /> Subtasks
              {hasSubtasks && (
                <span className="task-section-tally"> · {subtaskDone} / {subtaskTotal} completed</span>
              )}
            </h2>
            <SubtaskPanel
              task={task}
              assignees={subtaskPeople}
              canManage={Boolean(cap.can_manage_subtasks)}
              canComment={Boolean(cap.can_comment)}
              canUpload={Boolean(cap.can_upload)}
              currentUserId={user?.id}
              busy={run.isPending}
              onToggle={(subtaskId, next) => fire(
                () => taskService.completeSubtask(task.id, subtaskId, next))}
              onCreate={(payload) => fire(
                () => taskService.createSubtask(task.id, payload), '✅ Subtask added')}
              onUpdate={(subtaskId, patch) => fire(
                () => taskService.updateSubtask(task.id, subtaskId, patch))}
              onDelete={(subtaskId) => fire(
                () => taskService.deleteSubtask(task.id, subtaskId), 'Subtask deleted')}
              onReorder={(ids) => fire(() => taskService.reorderSubtasks(task.id, ids))}
              onComment={(subtaskId, body, { parent, mentionIds } = {}) => fire(
                () => taskService.addComment(task.id, body,
                  { parent, mentionIds, subtask: subtaskId }))}
              onEditComment={(commentId, body) => fire(
                () => taskService.editComment(task.id, commentId, body))}
              onUpload={(subtaskId, files) => fire(
                () => taskService.uploadAttachments(task.id, files,
                  { isEvidence: true, subtask: subtaskId }))}
            />
          </section>

          {/* --- Progress (T2.2) --- */}
          <section className="task-section">
            <h2>Progress</h2>
            {hasSubtasks ? (
              // Derived, not reported: while subtasks exist the server refuses
              // a manual figure (409), so the control is not offered at all.
              <>
                <div className="task-bar task-bar-lg" role="img"
                  aria-label={`${subtaskPercent}% complete`}>
                  <span className="task-bar-fill" style={{ width: `${subtaskPercent}%` }} />
                </div>
                <p className="task-progress-line">
                  <b>{subtaskDone} / {subtaskTotal} completed · {subtaskPercent}%</b>
                </p>
                <p className="task-sub">Progress is derived from subtasks.</p>
                {openSubtasks > 0 && (
                  <p className="task-sub">
                    {openSubtaskNote} — the task can be submitted for review once
                    they are all done.
                  </p>
                )}
              </>
            ) : (
            <ProgressControl
              percent={task.progress_percent}
              // What THIS person last reported, which is what the segments
              // choose between — the bar above still shows the task's figure.
              selected={mine ? mine.progress_percent : null}
              checklistPercent={task.checklist_percent}
              isAuto={task.progress_is_auto}
              hasChecklist={hasChecklist}
              canUpdate={cap.can_update_progress}
              busy={run.isPending}
              onChange={(step) => fire(
                () => taskService.updateProgress(task.id, step),
                `✅ Progress updated to ${step}%`)}
            />
            )}
          </section>

          {/* --- Team Progress: its own card, shared work only ---
              On a solo task the breakdown would be the overall figure written
              twice (Phase TASK-MULTI-ASSIGNEE-COLLABORATION). */}
          {shared && (
            <TeamProgress rows={task.assignees || []}
              overall={task.progress_percent}
              subtasks={subtasks} />
          )}

          {/* --- Dependencies (TASK-GOVERNANCE-HARDENING) --- */}
          <DependencyPanel task={task} />

          {/* --- Checklist (T2.1) --- */}
          <section className="task-section">
            <h2>Checklist</h2>
            <ChecklistPanel
              items={task.checklist || []}
              groups={task.checklist_groups || []}
              canTick={cap.can_tick_checklist}
              canManage={cap.can_manage_checklist}
              busy={run.isPending}
              onTick={(itemId, next) => fire(
                () => taskService.tickChecklistItem(task.id, itemId, next))}
              onSave={(payload) => fire(
                () => taskService.setChecklist(task.id, payload))}
            />
            <TemplateActions
              canApply={cap.can_apply_template}
              canSave={cap.can_save_as_template}
              hasChecklist={hasChecklist}
              templateName={task.template_name}
              busy={run.isPending}
              onApply={(templateId) => fire(
                () => taskService.applyTemplate(task.id, templateId))}
              onSave={(payload) => fire(
                () => taskService.saveAsTemplate(task.id, payload))}
            />
          </section>

          {/* --- Evidence (T2.5): what was PRODUCED --- */}
          <section className="task-section">
            <h2>Evidence</h2>
            <AttachmentPanel
              taskId={task.id}
              files={task.evidence || []}
              removed={task.removed_attachments || []}
              variant="evidence"
              canUpload={cap.can_upload}
              canViewLog={cap.can_view_download_log}
              currentUserId={user?.id}
              isOwner={isOwner}
              busy={run.isPending}
              onUpload={(files, isEvidence) => fire(
                () => taskService.uploadAttachments(task.id, files, { isEvidence }))}
              onAddLink={(url, caption) => fire(
                () => taskService.addLink(task.id, url, { caption }))}
              onFlag={(fileId, isEvidence) => fire(
                () => taskService.flagAttachment(task.id, fileId, isEvidence))}
              onRemove={(fileId) => fire(
                () => taskService.removeAttachment(task.id, fileId))}
            />
          </section>

          {/* --- Attachments (T2.4): the brief it was produced against --- */}
          <section className="task-section">
            <h2>Attachments</h2>
            <AttachmentPanel
              taskId={task.id}
              files={task.attachments || []}
              variant="reference"
              canUpload={cap.can_upload}
              canViewLog={cap.can_view_download_log}
              currentUserId={user?.id}
              isOwner={isOwner}
              busy={run.isPending}
              onUpload={(files) => fire(
                () => taskService.uploadAttachments(task.id, files,
                  { isEvidence: false }))}
              onAddLink={(url, caption) => fire(
                () => taskService.addLink(task.id, url,
                  { caption, isEvidence: false }))}
              onFlag={(fileId, isEvidence) => fire(
                () => taskService.flagAttachment(task.id, fileId, isEvidence))}
              onRemove={(fileId) => fire(
                () => taskService.removeAttachment(task.id, fileId))}
            />
          </section>

          {/* --- Comments (T2.3) --- */}
          <section className="task-section">
            <h2>Comments</h2>
            <CommentThread
              comments={task.comments || []}
              currentUserId={user?.id}
              canComment={cap.can_comment}
              busy={run.isPending}
              onPost={(body, mentionIds) => fire(
                () => taskService.addComment(task.id, body, { mentionIds }))}
              onReply={(parent, body, mentionIds) => fire(
                () => taskService.addComment(task.id, body, { parent, mentionIds }))}
              onEdit={(commentId, body) => fire(
                () => taskService.editComment(task.id, commentId, body))}
            />
          </section>
        </div>

        {/* --- Activity Timeline --- */}
        <aside className="task-col-side">
          <section className="task-section">
            <h2>Activity Timeline</h2>
            <TaskTimeline rows={task.timeline || []} />
          </section>
        </aside>
      </div>

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default TaskDetail;
