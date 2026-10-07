import React, { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import {
  ArrowDown, ArrowUp, CalendarDays, ChevronDown, ChevronRight, MessageSquare,
  Paperclip, Pencil, Plus, Trash2, Upload,
} from 'lucide-react';

import { taskService } from '../../services/taskService';
import { saveBlob } from '../../services/reportService';
import CommentThread from './CommentThread';
import { fmtDate } from './taskLabels';

/**
 * Subtasks (Phase TASK-SUBTASKS).
 *
 * A subtask is a checklist line that has grown up: it has an assignee, a due
 * date, its own comments and its own evidence. While any exist they ARE the
 * task's progress — the server derives the percentage from them and refuses a
 * manual figure, so this panel is also where the bar on the Progress card is
 * made.
 *
 * EVERY WRITE GOES THROUGH THE PAGE'S `fire()`
 * --------------------------------------------
 * The panel renders what it is handed and calls back for every change; the
 * detail page owns the mutation, the toast and the ['tasks'] invalidation, the
 * same way the checklist and comment panels do. One mutation path means one
 * place a refusal is reported.
 *
 * WHO MAY DO WHAT comes from the server twice over: `can_manage` (the owner —
 * add, edit, delete, reorder, assign) from the task's capabilities, and
 * `can_complete` on EACH ROW, because the person who may tick a subtask is its
 * assignee, not everybody who may tick the next one.
 *
 * DELETE CONFIRMS INLINE. No window.confirm: it cannot be styled, cannot be
 * read by every screen reader in every browser, and blocks the thread.
 */

const initials = (name) => (name || '?')
  .split(/\s+/).filter(Boolean).slice(0, 2)
  .map((part) => part[0]).join('').toUpperCase();

const Chip = ({ name }) => (name ? (
  <span className="task-subtask-who" title={name}>
    <span className="task-person-avatar is-sm" aria-hidden="true">{initials(name)}</span>
    <span className="task-subtask-who-name">{name}</span>
  </span>
) : (
  <span className="task-sub task-subtask-who">Unassigned</span>
));

/**
 * The fields of one subtask, used both for the add row and for an inline
 * edit. The assignee choice is limited to the TASK's assignees: a subtask
 * cannot be given to somebody who is not on the task.
 */
const RowEditor = ({
  initial = { title: '', assignee: null, due_date: '' }, assignees, busy,
  saveLabel, onSave, onCancel, idPrefix,
}) => {
  const [title, setTitle] = useState(initial.title || '');
  const [assignee, setAssignee] = useState(initial.assignee || '');
  const [dueDate, setDueDate] = useState(initial.due_date || '');
  const valid = title.trim().length > 0;

  const submit = () => {
    if (!valid) return;
    onSave({
      title: title.trim(),
      assignee: assignee || null,
      due_date: dueDate || null,
    });
  };

  return (
    <div className="task-subtask-editor">
      <label className="lr-field task-subtask-field is-title">
        <span className="sr-only">{idPrefix} title</span>
        <input value={title} aria-label={`${idPrefix} title`}
          placeholder="What is this part of the work?"
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            // Enter saves the row; it must not submit a surrounding form.
            if (e.key !== 'Enter') return;
            e.preventDefault();
            submit();
          }} />
      </label>
      <label className="lr-field task-subtask-field">
        <span className="sr-only">{idPrefix} assignee</span>
        <select value={assignee} aria-label={`${idPrefix} assignee`}
          onChange={(e) => setAssignee(e.target.value)}>
          <option value="">Unassigned</option>
          {assignees.map((person) => (
            <option key={person.id} value={person.id}>{person.name}</option>
          ))}
        </select>
      </label>
      <label className="lr-field task-subtask-field">
        <span className="sr-only">{idPrefix} due date</span>
        <input type="date" value={dueDate} aria-label={`${idPrefix} due date`}
          onChange={(e) => setDueDate(e.target.value)} />
      </label>
      <div className="task-form-actions">
        {onCancel && (
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button type="button" className="lr-btn lr-btn-primary"
          disabled={busy || !valid} onClick={submit}>
          {saveLabel}
        </button>
      </div>
    </div>
  );
};

/** One piece of evidence filed against a subtask. Fetched, never linked. */
const EvidenceRow = ({ file, taskId, busy }) => {
  const [downloading, setDownloading] = useState(false);
  const [failed, setFailed] = useState(false);
  const isLink = file.kind === 'link';

  const download = async () => {
    setDownloading(true);
    setFailed(false);
    try {
      const res = await taskService.downloadAttachment(taskId, file.id);
      saveBlob(res, file.original_name || 'attachment');
    } catch {
      setFailed(true);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <li className="task-file">
      <span className="task-file-main">
        <Paperclip size={13} aria-hidden="true" />
        {isLink ? (
          // An external address somebody pasted in: the one href that belongs.
          <a href={file.link_url} target="_blank" rel="noopener noreferrer">
            {file.original_name || file.link_url}
          </a>
        ) : (
          <button type="button" className="task-file-name" onClick={download}
            disabled={downloading || busy}>
            {file.original_name}
          </button>
        )}
      </span>
      <span className="task-sub">
        {file.uploaded_by_name} · {fmtDate(file.uploaded_at)}
      </span>
      {failed && (
        <span className="task-sub is-error" role="alert">
          That file could not be downloaded. Try again.
        </span>
      )}
    </li>
  );
};

const Row = ({
  row, index, count, taskId, assignees, canManage, canComment, canUpload,
  currentUserId, busy, highlighted, comments, evidence,
  onToggle, onUpdate, onDelete, onMove, onComment, onEditComment, onUpload,
}) => {
  // A row reached by its deep link opens itself: the link was to what is
  // inside it, not to the heading.
  const [open, setOpen] = useState(highlighted);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const late = row.is_overdue && !row.is_done;

  return (
    <li id={`subtask-${row.id}`}
      className={`task-subtask${row.is_done ? ' is-done' : ''}${highlighted ? ' is-highlight' : ''}`}>
      <div className="task-subtask-main">
        <input type="checkbox" className="task-subtask-tick"
          checked={Boolean(row.is_done)}
          disabled={!row.can_complete || busy}
          aria-label={`Mark ${row.title} ${row.is_done ? 'not done' : 'done'}`}
          onChange={(e) => onToggle(row.id, e.target.checked)} />

        <button type="button" className="task-subtask-title" aria-expanded={open}
          onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={14} aria-hidden="true" />
            : <ChevronRight size={14} aria-hidden="true" />}
          <span className={row.is_done ? 'is-done' : ''}>{row.title}</span>
        </button>

        <Chip name={row.assignee_name} />

        {row.due_date && (
          <span className={`task-subtask-due ${late ? 'task-late' : 'task-sub'}`}
            title={late ? 'Overdue' : 'Due date'}>
            <CalendarDays size={12} aria-hidden="true" /> {fmtDate(row.due_date)}
            {late && <span className="sr-only"> (overdue)</span>}
          </span>
        )}

        <span className="task-subtask-counts">
          <span title={`${row.comment_count || 0} comments`}>
            <MessageSquare size={12} aria-hidden="true" /> {row.comment_count || 0}
          </span>
          <span title={`${row.evidence_count || 0} pieces of evidence`}>
            <Paperclip size={12} aria-hidden="true" /> {row.evidence_count || 0}
          </span>
        </span>

        {canManage && (
          <span className="task-subtask-tools">
            <button type="button" className="task-subtask-tool"
              aria-label={`Move ${row.title} up`}
              disabled={busy || index === 0} onClick={() => onMove(row.id, -1)}>
              <ArrowUp size={14} />
            </button>
            <button type="button" className="task-subtask-tool"
              aria-label={`Move ${row.title} down`}
              disabled={busy || index === count - 1} onClick={() => onMove(row.id, 1)}>
              <ArrowDown size={14} />
            </button>
            <button type="button" className="task-subtask-tool"
              aria-label={`Edit ${row.title}`} aria-expanded={editing}
              disabled={busy} onClick={() => { setEditing(!editing); setConfirming(false); }}>
              <Pencil size={14} />
            </button>
            <button type="button" className="task-subtask-tool is-danger"
              aria-label={`Delete ${row.title}`}
              disabled={busy} onClick={() => { setConfirming(true); setEditing(false); }}>
              <Trash2 size={14} />
            </button>
          </span>
        )}
      </div>

      {row.is_done && row.done_by_name && (
        <span className="task-sub task-subtask-done">
          Done by {row.done_by_name} · {fmtDate(row.done_at)}
        </span>
      )}

      {confirming && (
        <div className="task-subtask-confirm" role="group"
          aria-label={`Delete ${row.title}?`}>
          <span>Delete <b>{row.title}</b>? Its comments and evidence go with it.</span>
          <div className="task-form-actions">
            <button type="button" className="lr-btn lr-btn-ghost"
              onClick={() => setConfirming(false)}>Keep it</button>
            <button type="button" className="lr-btn lr-btn-danger" disabled={busy}
              onClick={() => { onDelete(row.id); setConfirming(false); }}>
              Delete subtask
            </button>
          </div>
        </div>
      )}

      {editing && (
        <RowEditor
          initial={{ title: row.title, assignee: row.assignee, due_date: row.due_date || '' }}
          assignees={assignees} busy={busy} saveLabel="Save subtask"
          idPrefix={`Edit ${row.title}`}
          onCancel={() => setEditing(false)}
          onSave={(patch) => { onUpdate(row.id, patch); setEditing(false); }} />
      )}

      {open && (
        <div className="task-subtask-detail">
          {row.description && (
            <p className="task-description">{row.description}</p>
          )}

          <h4 className="task-subtask-subhead">Comments</h4>
          <CommentThread
            comments={comments}
            currentUserId={currentUserId}
            canComment={canComment}
            busy={busy}
            onPost={(body, mentionIds) => onComment(row.id, body, { mentionIds })}
            onReply={(parent, body, mentionIds) => onComment(
              row.id, body, { parent, mentionIds })}
            onEdit={onEditComment}
          />

          <h4 className="task-subtask-subhead">Evidence</h4>
          {evidence.length === 0 && (
            <p className="task-sub">No evidence on this subtask yet.</p>
          )}
          <ul className="task-files">
            {evidence.map((file) => (
              <EvidenceRow key={file.id} file={file} taskId={taskId} busy={busy} />
            ))}
          </ul>
          {canUpload && (
            <label className="lr-btn task-upload">
              <Upload size={14} /> Upload evidence
              <input type="file" multiple className="sr-only"
                aria-label={`Upload evidence for ${row.title}`}
                onChange={(e) => {
                  if (!e.target.files?.length) return;
                  // Copy before resetting: clearing `value` empties `files`.
                  const chosen = Array.from(e.target.files);
                  e.target.value = '';
                  onUpload(row.id, chosen);
                }} />
            </label>
          )}
        </div>
      )}
    </li>
  );
};

/**
 * @param {Object}  props
 * @param {Object}  props.task         the detail payload (subtasks, comments, evidence)
 * @param {Array}   props.assignees    [{id, name}] — the task's assignees
 * @param {boolean} props.canManage    capabilities.can_manage_subtasks
 * @param {boolean} props.canComment   capabilities.can_comment
 * @param {boolean} props.canUpload    capabilities.can_upload
 */
const SubtaskPanel = ({
  task, assignees = [], canManage = false, canComment = false, canUpload = false,
  currentUserId, busy = false,
  onToggle, onCreate, onUpdate, onDelete, onReorder, onComment, onEditComment, onUpload,
}) => {
  const rows = task.subtasks || [];
  const total = task.subtask_total ?? rows.length;
  const done = task.subtask_done ?? rows.filter((row) => row.is_done).length;
  const percent = total > 0
    ? (task.subtask_percent ?? Math.round((done / total) * 100))
    : null;
  const [adding, setAdding] = useState(false);

  // The deep link: /tasks/<ref>#subtask-<id> scrolls to the row and lights it.
  const { hash } = useLocation();
  const target = /^#subtask-(.+)$/.exec(hash || '')?.[1] || null;
  useEffect(() => {
    if (!target) return;
    const el = document.getElementById(`subtask-${target}`);
    // jsdom has no scrollIntoView; a browser does.
    el?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, [target, rows.length]);

  const move = (id, delta) => {
    const ids = rows.map((row) => row.id);
    const from = ids.indexOf(id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= ids.length) return;
    ids.splice(from, 1);
    ids.splice(to, 0, id);
    onReorder(ids);
  };

  const comments = task.comments || [];
  const evidence = task.evidence || [];

  return (
    <div className="task-subtasks">
      <div className="task-subtasks-head">
        <span className="task-subtasks-tally">
          {total === 0
            ? 'No subtasks'
            : `${done} / ${total} completed`}
        </span>
        {percent !== null && (
          <>
            <span className="task-bar" role="img" aria-label={`${percent}% of subtasks complete`}>
              <span className="task-bar-fill" style={{ width: `${percent}%` }} />
            </span>
            <span className="task-subtasks-pct">{percent}%</span>
          </>
        )}
      </div>

      {rows.length === 0 && (
        <p className="task-sub">
          {canManage
            ? 'Break the task into parts, each with its own owner and date.'
            : 'This task has no subtasks.'}
        </p>
      )}

      <ol className="task-subtask-list">
        {rows.map((row, index) => (
          <Row key={row.id} row={row} index={index} count={rows.length}
            taskId={task.id} assignees={assignees}
            canManage={canManage} canComment={canComment} canUpload={canUpload}
            currentUserId={currentUserId} busy={busy}
            highlighted={row.id === target}
            comments={comments.filter((c) => c.subtask === row.id)}
            evidence={evidence.filter((f) => f.subtask === row.id)}
            onToggle={onToggle} onUpdate={onUpdate} onDelete={onDelete}
            onMove={move} onComment={onComment} onEditComment={onEditComment}
            onUpload={onUpload} />
        ))}
      </ol>

      {canManage && !adding && (
        <button type="button" className="lr-btn" onClick={() => setAdding(true)}>
          <Plus size={14} /> Add subtask
        </button>
      )}
      {canManage && adding && (
        <RowEditor assignees={assignees} busy={busy} saveLabel="Add subtask"
          idPrefix="New subtask"
          onCancel={() => setAdding(false)}
          onSave={(payload) => { onCreate(payload); setAdding(false); }} />
      )}
    </div>
  );
};

export default SubtaskPanel;
