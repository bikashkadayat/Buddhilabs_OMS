import React, { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, GripVertical, Plus, RefreshCw, ShieldCheck, X,
} from 'lucide-react';

import { taskService } from '../../services/taskService';
import { useAuth } from '../../hooks/useAuth';
import { useDocumentDraft } from '../../hooks/useDocumentDraft';
import useExitGuard from '../../hooks/useExitGuard';
import SaveIndicator from '../../components/drafts/SaveIndicator';
import DraftRecoveryDialog from '../../components/drafts/DraftRecoveryDialog';
import RichTextEditor from '../../components/memo/RichTextEditor';
import PersonPicker from '../../components/task/PersonPicker';
import PriorityPicker from '../../components/task/PriorityPicker';
import Toast from '../../components/admin/Toast';
import RecordCreatedDialog from '../../components/share/RecordCreatedDialog';
import { canonicalPath, canonicalUrl } from '../../components/share/shareLinks';
import { fmtDateTime } from '../../components/task/taskLabels';
import {
  STARTER_HTML, isBlankHtml, textOf, textToHtml,
} from '../../components/task/taskDescription';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * Create / edit a task.
 *
 * THE ASSIGNEE PICKER IS A PERSON SEARCH, NOT A DEPARTMENT DROPDOWN
 * -----------------------------------------------------------------
 * The specification is explicit about this: "Do NOT assign only by Department.
 * Support employee search, employee selection, multi-assignee". So the field
 * searches people, shows who has been picked, and allows several. The department
 * is inherited from the first assignee on the server rather than being asked
 * for twice.
 *
 * ONE KIND OF TASK, TWO PEOPLE TO CHOOSE
 * --------------------------------------
 * Anybody may raise a task for anybody, and the creator picks BOTH the assignee
 * and the reviewer. Both are required (Phase TASK-REVIEWER-SELECTION): the
 * server no longer resolves a reviewer when one is missing, because a review
 * routed by the system is not a review the creator chose.
 *
 * The two fields arrive pre-filled - Assigned To with the creator, Reviewer
 * with their department head where the server knows of one. Both are
 * SUGGESTIONS: either can be replaced with any active user, and the server
 * decides the suggestions so this form does not have to know who is logged in
 * or what a department head is.
 *
 * THE ONE RULE ABOUT PEOPLE is that the reviewer cannot be an assignee. The
 * server refuses it in those words; this form says so before the request,
 * because an error you can see coming is better than one you are told about
 * afterwards.
 *
 * WHO MAY CHANGE WHAT
 * --------------------
 * Since Phase TASK-MULTI-ASSIGNEE-COLLABORATION the people ASSIGNED to a task
 * may edit it too, not only its creator — but only its details: title,
 * description, priority and due date. The people fieldset and the checklist are
 * the creator's, and the server refuses a change to either from anybody else.
 * So when the task says `can_edit_all_fields` is false, this form does not
 * render those sections at all: offering a picker whose every use would come
 * back 403 is worse than not offering it.
 *
 * AUTOSAVE, A RICH DESCRIPTION AND SUBTASKS (Phase TASK-SUBTASKS)
 * ----------------------------------------------------------------
 * The whole form is one draft snapshot, through the same engine Memo uses
 * (useDocumentDraft, kind "task"), saved 2.5 s after the last keystroke — the
 * fields here are short, so the memo's five seconds felt like nothing was
 * happening. The description is HTML from the rich editor and travels with
 * `description_format: "html"`; a task written before this phase is plain
 * text, which is converted to paragraphs for the editor and becomes HTML on
 * its next save.
 *
 * Subtasks are raised WITH the task on the create form and managed on the
 * task page afterwards: the detail page is where their comments and evidence
 * live, and a second editor for them here would be a second place for the two
 * to disagree.
 *
 * TWO PEOPLE EDITING ONE TASK
 * ---------------------------
 * An edit sends `expected_updated_at`, the stamp the task carried when it was
 * loaded. If somebody else saved in between, the server answers 409 with the
 * current copy, and the same banner appears when a background refetch (on
 * focus and every minute) reports a newer stamp. Reload takes the server's
 * values; Review shows which fields differ; Keep my version adopts the newer
 * stamp so the next save goes through. Nothing is merged silently.
 *
 * WHAT THIS FORM CANNOT DO
 * ------------------------
 * It cannot set a status. Creating a task with assignees produces an ASSIGNED
 * task and creating one without produces a DRAFT — decided by the server, in
 * tasks.views.TaskViewSet.create, because tasks.workflow is the only thing that
 * may assign to `status`.
 */

const clientId = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

const PRIORITY_LABEL = { low: 'Low', medium: 'Medium', high: 'High', urgent: 'Urgent' };

const TaskForm = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isEdit = Boolean(id);

  const [form, setForm] = useState({
    title: '',
    // A new task starts from the brief's headings; an edit starts from the
    // stored text once it arrives.
    description: isEdit ? '' : STARTER_HTML,
    description_format: 'html',
    priority: 'medium',
    due_date: '',
  });
  const [assignees, setAssignees] = useState([]);   // [{id, full_name, ...}]
  const [reviewer, setReviewer] = useState(null);
  const [checklist, setChecklist] = useState([]);
  const [checklistDraft, setChecklistDraft] = useState('');
  // Create form only: [{client_id, title, assignee, due_date}].
  const [subtasks, setSubtasks] = useState([]);
  // Edit form only: the task's `updated_at` as loaded, sent back as
  // `expected_updated_at` so a save over somebody else's change is refused.
  const [baseUpdatedAt, setBaseUpdatedAt] = useState(null);
  // {task, stamp, reviewing} while the server copy is newer than this form.
  const [conflict, setConflict] = useState(null);
  const [errors, setErrors] = useState({});
  // The task the server just created, held so its link can be offered before
  // we navigate away (Phase TASK-DEEP-LINK-SHARING).
  const [created, setCreated] = useState(null);
  const [toast, setToast] = useState(null);
  const [copyFallback, setCopyFallback] = useState('');

  const { data: existing, isLoading, isError, error } = useQuery({
    queryKey: ['tasks', id],
    queryFn: () => taskService.getTask(id),
    enabled: isEdit,
    // While the edit form is open the task is re-read on focus and every
    // minute, so a colleague's save shows up as a banner here rather than as
    // a 409 at the end of twenty minutes of editing.
    refetchOnWindowFocus: isEdit,
    refetchInterval: isEdit ? 60_000 : false,
  });

  /** Put the server's copy into the form. Used on load and on Reload. */
  const applyServer = (task) => {
    setForm({
      title: task.title || '',
      description: task.description_format === 'html'
        ? (task.description || '')
        : textToHtml(task.description),
      description_format: 'html',
      priority: task.priority || 'medium',
      due_date: task.due_date || '',
    });
    setAssignees((task.assignees || []).map((row) => ({
      id: row.user?.id, full_name: row.user_name,
      designation: row.designation, department: row.department_label,
    })).filter((row) => row.id));
    setReviewer(task.reviewer
      ? { id: task.reviewer.id, full_name: task.reviewer.full_name }
      : null);
    setChecklist((task.checklist || []).map((row) => row.text));
    setBaseUpdatedAt(task.updated_at || null);
  };

  // Seed the form once the task arrives. Keyed on the task's id so a navigation
  // between two edit pages re-seeds rather than keeping the first one's values,
  // while a refetch of the same task never overwrites the user's edits. Done
  // during render against the last id seeded, so the form never shows a blank
  // frame after the task has loaded.
  const [seededId, setSeededId] = useState(null);
  if (existing && existing.id !== seededId) {
    setSeededId(existing.id);
    applyServer(existing);
  }

  // What this caller may raise, and whether they may name anybody else.
  const { data: options, isError: optionsFailed } = useQuery({
    queryKey: ['tasks', 'filter-options'],
    queryFn: taskService.getFilterOptions,
    staleTime: 300_000,
  });
  // Pre-fill both people ONCE, and only on a new task: re-applying them would
  // undo a deliberate change every time the options query refreshed.
  //
  // Assigned To defaults to the creator - most tasks a person writes are their
  // own, and the field is mandatory. Both are defaults: the assignee can be
  // changed to any active user and the reviewer to anybody or to nobody.
  const [defaultsSeeded, setDefaultsSeeded] = React.useState(false);
  React.useEffect(() => {
    if (isEdit || defaultsSeeded || !options) return;
    setDefaultsSeeded(true);
    if (options.default_assignee) setAssignees([options.default_assignee]);
    if (options.default_reviewer) setReviewer(options.default_reviewer);
  }, [options, isEdit, defaultsSeeded]);

  /* ---------------------------------------------------------------- *
   * Autosave (Phase TASK-SUBTASKS)
   *
   * The snapshot is the whole form. People are stored by id, as the API
   * takes them, plus the names needed to draw the rows again on recovery —
   * an id alone cannot be shown to anybody.
   * ---------------------------------------------------------------- */
  const draftPayload = useMemo(() => ({
    title: form.title,
    description: form.description,
    description_format: form.description_format,
    priority: form.priority,
    due_date: form.due_date,
    reviewer: reviewer?.id || null,
    assignee_ids: assignees.map((a) => a.id),
    checklist,
    subtasks,
    base_updated_at: baseUpdatedAt,
    people: { assignees, reviewer },
  }), [form, reviewer, assignees, checklist, subtasks, baseUpdatedAt]);

  // The key is the task's UUID, not the reference in the address bar: the
  // draft endpoint and Unfinished Work both resume by it.
  const documentKey = isEdit ? (existing?.id || id) : 'new';
  const draft = useDocumentDraft({
    kind: 'task',
    documentKey,
    payload: draftPayload,
    userId: user?.id,
    // Withheld until the form has its starting values — the loaded task on an
    // edit, the suggested people on a create — or the baseline would be an
    // empty form and every visit would offer to "recover" the defaults.
    enabled: Boolean(user?.id) && !created
      && (isEdit ? Boolean(existing) : (defaultsSeeded || optionsFailed)),
    idleMs: 2500,
  });

  useExitGuard(draft.unsaved && !created);

  const applySnapshot = useCallback((payload) => {
    if (!payload) return;
    setForm((prev) => ({
      ...prev,
      title: payload.title ?? prev.title,
      description: payload.description ?? prev.description,
      description_format: 'html',
      priority: payload.priority || prev.priority,
      due_date: payload.due_date || '',
    }));
    if (Array.isArray(payload.people?.assignees)) setAssignees(payload.people.assignees);
    if (payload.people && 'reviewer' in payload.people) setReviewer(payload.people.reviewer);
    if (Array.isArray(payload.checklist)) setChecklist(payload.checklist);
    if (Array.isArray(payload.subtasks)) {
      setSubtasks(payload.subtasks.map((row) => ({ ...row, client_id: row.client_id || clientId() })));
    }
    // The draft was written against THIS version of the task. If the server
    // has moved on since, the conflict check below says so straight away.
    if (payload.base_updated_at) setBaseUpdatedAt(payload.base_updated_at);
  }, []);

  /* ---------------------------------------------------------------- *
   * Conflicts (edit only)
   * ---------------------------------------------------------------- */
  const save = useMutation({
    mutationFn: (payload) => (isEdit
      ? taskService.updateTask(id, payload)
      : taskService.createTask(payload)),
    onSuccess: async (task) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      // Committed, so the snapshot goes — or the next visit offers to restore
      // content that is already saved.
      await draft.complete();
      const path = canonicalPath('tasks', task.task_number, task.id);
      // AN EDIT GOES STRAIGHT BACK (nothing new to share); a CREATION stops to
      // hand over the reference and the link, which is what somebody does next
      // nine times out of ten and used to mean copying the number out of the
      // header by hand.
      if (isEdit) {
        navigate(path);
        return;
      }
      setCreated(task);
    },
    onError: (err) => {
      const status = err?.response?.status;
      const body = err?.response?.data;
      if (status === 409 && body?.task) {
        // Somebody saved first. Their copy is the one on the server now, so
        // it is what the cache should hold and what the banner compares to.
        queryClient.setQueryData(['tasks', id], body.task);
        setConflict({ task: body.task, stamp: body.task.updated_at, reviewing: false });
        return;
      }
      setErrors(body || { detail: 'The task could not be saved.' });
    },
  });

  // A refetch reported a newer stamp than the one this form was loaded (or
  // reloaded) against. Set during render, like the seeding above, guarded so
  // it fires once per server version — and not after our own save, whose
  // invalidation produces exactly such a refetch on the way out.
  const serverStamp = existing?.updated_at || null;
  if (isEdit && existing && existing.id === seededId && !save.isSuccess
    && baseUpdatedAt && serverStamp && serverStamp !== baseUpdatedAt
    && conflict?.stamp !== serverStamp) {
    setConflict({ task: existing, stamp: serverStamp, reviewing: false });
  }

  const reloadFromServer = () => {
    if (!conflict?.task) return;
    applyServer(conflict.task);
    setConflict(null);
    setErrors({});
  };
  const keepMine = () => {
    if (!conflict) return;
    setBaseUpdatedAt(conflict.stamp);
    setConflict(null);
  };

  /** Which of the visible fields differ between this form and the server copy. */
  const conflictDiffs = useMemo(() => {
    const server = conflict?.task;
    if (!server) return [];
    const serverText = server.description_format === 'html'
      ? textOf(server.description) : String(server.description || '').trim();
    const rows = [
      ['Title', form.title.trim(), String(server.title || '').trim()],
      ['Description', textOf(form.description), serverText.replace(/\s+/g, ' ')],
      ['Priority', PRIORITY_LABEL[form.priority] || form.priority,
        server.priority_label || PRIORITY_LABEL[server.priority] || server.priority],
      ['Due date', form.due_date || 'No due date', server.due_date || 'No due date'],
      ['Reviewer', reviewer?.full_name || 'Nobody',
        server.reviewer?.full_name || server.reviewer_name || 'Nobody'],
    ];
    return rows.filter(([, mine, theirs]) => mine !== theirs);
  }, [conflict, form, reviewer]);

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });

  const addAssignee = (person) => {
    if (assignees.some((a) => a.id === person.id)) return;
    setAssignees([...assignees, person]);
  };
  const removeAssignee = (personId) => {
    setAssignees(assignees.filter((a) => a.id !== personId));
    // A subtask cannot be given to somebody who is no longer on the task.
    setSubtasks(subtasks.map((row) => (
      row.assignee === personId ? { ...row, assignee: null } : row)));
  };

  const setSubtask = (clientKey, patch) => setSubtasks(
    subtasks.map((row) => (row.client_id === clientKey ? { ...row, ...patch } : row)));

  // The two rules, said before the request rather than after it.
  const reviewerIsAssignee = Boolean(
    reviewer && assignees.some((person) => person.id === reviewer.id));
  const nobodyAssigned = assignees.length === 0;
  const noReviewer = !reviewer;

  // On a NEW task the creator chooses everybody, so the sections are always
  // shown; on an edit the server says whether this person may change the terms
  // as well as the details.
  const mayEditTerms = !isEdit
    || Boolean(existing?.capabilities?.can_edit_all_fields);

  const submit = (e) => {
    e.preventDefault();
    setErrors({});
    const payload = {
      title: form.title,
      // Always HTML now, even for a task first written as text: the editor
      // held it as paragraphs, and that is what the author just saw.
      description: isBlankHtml(form.description) ? '' : form.description,
      description_format: 'html',
      priority: form.priority,
      due_date: form.due_date || null,
    };
    // The terms of the task travel only when this person may set them. Sending
    // them unchanged would pass the server's check, but an editor who cannot
    // see the pickers should not be sending their values back at all.
    if (mayEditTerms) {
      payload.reviewer = reviewer?.id || null;
      payload.assignee_ids = assignees.map((a) => a.id);
      payload.checklist = checklist;
    }
    if (!isEdit) {
      // Rows without a title are unfinished thoughts, not subtasks.
      payload.subtasks = subtasks
        .filter((row) => row.title.trim())
        .map((row) => ({
          title: row.title.trim(),
          assignee: assignees.some((a) => a.id === row.assignee) ? row.assignee : null,
          due_date: row.due_date || null,
        }));
    }
    if (isEdit && baseUpdatedAt) payload.expected_updated_at = baseUpdatedAt;
    save.mutate(payload);
  };

  if (isEdit && isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isEdit && isError) {
    return <div className="page"><ErrorState error={error} /></div>;
  }

  const fieldError = (name) => (Array.isArray(errors[name])
    ? errors[name][0] : errors[name]);

  return (
    <div className="page memo-page">
      {draft.recoverable && (
        <DraftRecoveryDialog
          draft={draft.recoverable}
          label="task"
          onRestore={() => { applySnapshot(draft.recoverable.payload); draft.acceptRecovery(); }}
          onContinue={draft.dismissRecovery}
          onDiscard={draft.discard}
        />
      )}

      <div className="lr-page-head task-form-head">
        <div>
          <h1 className="lr-page-title">{isEdit ? 'Edit Task' : 'Create Task'}</h1>
          <p className="lr-page-sub">
            {isEdit
              ? existing?.task_number
              : 'Anybody can raise a task. Choose who does it and who reviews it.'}
          </p>
        </div>
        <SaveIndicator status={draft.status} lastSavedAt={draft.lastSavedAt}
          savedLabel="Draft saved" />
      </div>

      {created && (
        <RecordCreatedDialog
          kind="Task"
          reference={created.task_number}
          title={created.title}
          url={canonicalUrl(canonicalPath('tasks', created.task_number, created.id))}
          onOpen={() => navigate(canonicalPath('tasks', created.task_number, created.id))}
          onClose={() => navigate(canonicalPath('tasks', created.task_number, created.id))}
          onCopied={() => setToast({ message: '✅ Task link copied', tone: 'success' })}
          onFailed={setCopyFallback}
        />
      )}

      {copyFallback && (
        <div className="task-callout is-warn" role="status">
          <b>Copy this link:</b>{' '}
          <input className="task-copy-fallback" readOnly value={copyFallback}
            aria-label="Task link" onFocus={(e) => e.target.select()} />
        </div>
      )}

      {conflict && (
        <div className="task-conflict" role="alert">
          <div className="task-conflict-head">
            <AlertTriangle size={16} aria-hidden="true" />
            <div>
              <b>Task updated by another user.</b>{' '}
              <span>
                The copy you are editing is older than the one on the server
                {conflict.stamp ? ` (changed ${fmtDateTime(conflict.stamp)})` : ''}.
                Saving now would be refused.
              </span>
            </div>
          </div>
          <div className="task-form-actions task-conflict-actions">
            <button type="button" className="lr-btn lr-btn-primary" onClick={reloadFromServer}>
              <RefreshCw size={14} /> Reload
            </button>
            <button type="button" className="lr-btn" aria-expanded={conflict.reviewing}
              onClick={() => setConflict({ ...conflict, reviewing: !conflict.reviewing })}>
              Review changes
            </button>
            <button type="button" className="lr-btn lr-btn-ghost" onClick={keepMine}>
              Keep my version
            </button>
          </div>
          {conflict.reviewing && (
            conflictDiffs.length === 0 ? (
              <p className="task-sub">
                Your copy matches the server&rsquo;s on every field shown here;
                something you cannot see from this form changed. Reload to pick
                it up, or keep your version to save over it.
              </p>
            ) : (
              <dl className="task-conflict-diff">
                {conflictDiffs.map(([label, mine, theirs]) => (
                  <div key={label} className="task-conflict-row">
                    <dt>{label}</dt>
                    <dd><span className="task-sub">Yours</span> {mine || '—'}</dd>
                    <dd><span className="task-sub">Server</span> {theirs || '—'}</dd>
                  </div>
                ))}
              </dl>
            )
          )}
        </div>
      )}

      {(errors.detail || errors.workflow) && (
        <div className="lr-error" role="alert">
          <p className="lr-error-msg">{errors.detail || errors.workflow}</p>
        </div>
      )}

      <form onSubmit={submit} className="task-form">
        {/* --- what the task IS ------------------------------------------- */}
        <section className="task-card">
          <h2 className="task-card-title">Task Details</h2>

          <label className="lr-field">
            <span>Task Title *</span>
            <input value={form.title} onChange={set('title')} required
              placeholder="What has to be done?" />
            {fieldError('title') && (
              <em className="task-field-error">{fieldError('title')}</em>
            )}
          </label>

          <div className="lr-field task-description-field">
            <span id="task-description-label">Description</span>
            {/* HELPER TEXT ABOVE THE BOX, not a placeholder inside it: a
                placeholder disappears the moment somebody starts typing, which
                is exactly when they are deciding what to write
                (Phase TASK-CREATE-UI-POLISH). */}
            <span className="task-field-help">
              What does &ldquo;done&rdquo; look like? Scope, context, and
              anything the person doing this will otherwise have to come back
              and ask for. The headings are a starting point — keep the ones
              that apply.
            </span>
            <RichTextEditor
              value={form.description}
              onChange={(html) => setForm((prev) => ({
                ...prev, description: html, description_format: 'html',
              }))}
              placeholder="Optional, but it saves a round trip."
              minHeight={220}
              ariaLabel="Task description"
            />
            {fieldError('description') && (
              <em className="task-field-error">{fieldError('description')}</em>
            )}
          </div>

          {/* Priority and Due Date share a row on desktop, stack on a phone. */}
          <div className="task-form-grid">
            <div className="lr-field">
              <span>Priority</span>
              <PriorityPicker value={form.priority}
                onChange={(value) => setForm({ ...form, priority: value })} />
            </div>

            <label className="lr-field">
              <span>Due Date</span>
              <input type="date" value={form.due_date} onChange={set('due_date')} />
              <span className="task-field-help">
                Leave empty if there is no deadline.
              </span>
              {fieldError('due_date') && (
                <em className="task-field-error">{fieldError('due_date')}</em>
              )}
            </label>
          </div>
        </section>

        {/* --- who does it, and who signs it off --------------------------- */}
        {!mayEditTerms && (
          <p className="task-card-note">
            You are assigned to this task, so you can change its title,
            description, priority and due date. Its assignees, reviewer,
            checklist and subtasks stay with {existing?.created_by_name || 'whoever raised it'}.
          </p>
        )}

        {mayEditTerms && (
          <div className="task-form-grid">
            {/* --- Assignees ------------------------------------------- */}
            <section className="task-card">
              <h2 className="task-card-title">
                Assigned To *
                <span className="task-card-count">{assignees.length}</span>
              </h2>
              <p className="task-field-help">
                Several people may share one task. The first listed is the
                primary assignee — the person answerable for it.
              </p>

              {assignees.length === 0 ? (
                <p className="task-field-error">
                  Choose who is doing this task — assign it to yourself if it is
                  yours.
                </p>
              ) : (
                <ul className="task-people is-editable">
                  {assignees.map((person, index) => (
                    <li key={person.id} className="task-person">
                      <span className="task-person-avatar" aria-hidden="true">
                        <GripVertical size={13} />
                      </span>
                      <span className="task-person-id">
                        <span className="task-person-name">{person.full_name}</span>
                        {index === 0 && (
                          <span className="task-person-tag is-primary">primary</span>
                        )}
                      </span>
                      <button type="button" className="task-person-drop"
                        aria-label={`Remove ${person.full_name}`}
                        onClick={() => removeAssignee(person.id)}>
                        <X size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <PersonPicker label="Search employees to assign"
                placeholder="Search employees…"
                excludeIds={assignees.map((a) => a.id)}
                onPick={addAssignee} />
            </section>

            {/* --- Reviewer -------------------------------------------- */}
            <section className="task-card">
              <h2 className="task-card-title">Reviewer *</h2>
              <p className="task-field-help">
                Who decides Approved or Needs Rework once the work is submitted.
              </p>

              {reviewer ? (
                <ul className="task-people is-editable">
                  <li className="task-person">
                    <span className="task-person-avatar is-review" aria-hidden="true">
                      <ShieldCheck size={14} />
                    </span>
                    <span className="task-person-id">
                      <span className="task-person-name">{reviewer.full_name}</span>
                      {/* The approval role: what this person is in the
                          organisation, which is what makes them the right
                          signature on this work. */}
                      <span className="task-person-role">
                        {reviewer.designation || 'Approver'}
                        {reviewer.department && ` · ${reviewer.department}`}
                      </span>
                    </span>
                    <button type="button" className="task-person-drop"
                      aria-label="Remove reviewer"
                      onClick={() => setReviewer(null)}>
                      <X size={14} />
                    </button>
                  </li>
                </ul>
              ) : (
                <p className="task-field-error">
                  Choose who approves this work.
                </p>
              )}

              {/* The exact wording Phase TASK-REVIEWER-SELECTION specifies, and
                  the same sentence the server refuses with — said here before
                  the request rather than after it. Not reworded: two phrasings
                  of one rule is how a person starts wondering whether they are
                  two different rules. */}
              {reviewerIsAssignee && (
                <p className="task-field-error" role="alert">
                  Reviewer cannot be the same as the assignee.
                </p>
              )}

              <PersonPicker label="Search employees to review"
                placeholder="Search employees…"
                excludeIds={reviewer ? [reviewer.id] : []}
                onPick={setReviewer} />
            </section>
          </div>
        )}

        {/* --- subtasks (create) / where they live (edit) ------------------ */}
        {mayEditTerms && !isEdit && (
        <section className="task-card" aria-labelledby="task-form-subtasks-title">
          <h2 className="task-card-title" id="task-form-subtasks-title">
            Subtasks
            <span className="task-card-count">{subtasks.length}</span>
          </h2>
          <p className="task-field-help">
            Optional. Break the work into parts, each with its own owner and
            date. While a task has subtasks its progress is derived from them,
            and it cannot be submitted for review until every one is done.
          </p>

          {subtasks.length > 0 && (
            <ol className="task-form-subtasks">
              {subtasks.map((row, index) => (
                <li key={row.client_id} className="task-form-subtask">
                  <span className="task-steps-num" aria-hidden="true">{index + 1}</span>
                  <label className="lr-field task-form-subtask-title">
                    <span className="sr-only">Subtask {index + 1} title</span>
                    <input value={row.title} aria-label={`Subtask ${index + 1} title`}
                      placeholder="What is this part of the work?"
                      onChange={(e) => setSubtask(row.client_id, { title: e.target.value })}
                      onKeyDown={(e) => {
                        // Enter must not submit the whole form from a row.
                        if (e.key === 'Enter') e.preventDefault();
                      }} />
                  </label>
                  <label className="lr-field task-form-subtask-who">
                    <span className="sr-only">Subtask {index + 1} assignee</span>
                    <select value={row.assignee || ''}
                      aria-label={`Subtask ${index + 1} assignee`}
                      onChange={(e) => setSubtask(row.client_id,
                        { assignee: e.target.value || null })}>
                      <option value="">Unassigned</option>
                      {assignees.map((person) => (
                        <option key={person.id} value={person.id}>{person.full_name}</option>
                      ))}
                    </select>
                  </label>
                  <label className="lr-field task-form-subtask-due">
                    <span className="sr-only">Subtask {index + 1} due date</span>
                    <input type="date" value={row.due_date || ''}
                      aria-label={`Subtask ${index + 1} due date`}
                      onChange={(e) => setSubtask(row.client_id, { due_date: e.target.value })} />
                  </label>
                  <button type="button" className="task-person-drop"
                    aria-label={`Remove subtask ${index + 1}`}
                    onClick={() => setSubtasks(
                      subtasks.filter((r) => r.client_id !== row.client_id))}>
                    <X size={14} />
                  </button>
                </li>
              ))}
            </ol>
          )}

          <button type="button" className="lr-btn"
            onClick={() => setSubtasks([...subtasks,
              { client_id: clientId(), title: '', assignee: null, due_date: '' }])}>
            <Plus size={14} /> Add subtask
          </button>
        </section>
        )}
        {mayEditTerms && isEdit && (
          <p className="task-card-note">
            Subtasks are managed on the task page, where each one has its own
            comments and evidence.
          </p>
        )}

        {/* --- checklist --------------------------------------------------- */}
        {mayEditTerms && (
        <section className="task-card">
          <h2 className="task-card-title">
            Checklist
            <span className="task-card-count">{checklist.length}</span>
          </h2>
          <p className="task-field-help">
            Optional. Break the task into steps and progress follows the ticks.
          </p>

          {checklist.length > 0 && (
            <ol className="task-steps-list">
              {checklist.map((text, index) => (
                // The text is not unique (two items may legitimately read the
                // same), so the index is part of the key. These rows are only
                // added and removed while the form is open, so React never has
                // to reconcile a reordered list against a stale index.
                <li key={`${text}-${index}`}>
                  <span className="task-steps-num">{index + 1}</span>
                  <span className="task-steps-text">{text}</span>
                  <button type="button" aria-label={`Remove ${text}`}
                    className="task-person-drop"
                    onClick={() => setChecklist(
                      checklist.filter((_, i) => i !== index))}>
                    <X size={14} />
                  </button>
                </li>
              ))}
            </ol>
          )}

          <div className="task-steps-add">
            <label className="lr-field">
              <span className="sr-only">New checklist item</span>
              <input value={checklistDraft} aria-label="New checklist item"
                onChange={(e) => setChecklistDraft(e.target.value)}
                placeholder="Add a step…"
                onKeyDown={(e) => {
                  // Enter adds the item; it must NOT submit the whole form,
                  // which is what a bare <input> inside a <form> does.
                  if (e.key !== 'Enter') return;
                  e.preventDefault();
                  if (!checklistDraft.trim()) return;
                  setChecklist([...checklist, checklistDraft.trim()]);
                  setChecklistDraft('');
                }} />
            </label>
            <button type="button" className="lr-btn"
              disabled={!checklistDraft.trim()}
              onClick={() => {
                if (!checklistDraft.trim()) return;
                setChecklist([...checklist, checklistDraft.trim()]);
                setChecklistDraft('');
              }}>
              <Plus size={14} /> Add
            </button>
          </div>
        </section>
        )}

        {/* --- the footer, pinned ------------------------------------------
            A long form pushes its own Save button off the screen, and the
            person filling it in has to scroll back to a control they can
            already see the top of. Sticky, so the two decisions — abandon or
            create — are always to hand (Phase TASK-CREATE-UI-POLISH). */}
        <div className="task-form-bar">
          <p className="task-form-bar-note">
            {nobodyAssigned || noReviewer || reviewerIsAssignee
              ? 'Assignee and reviewer are both required, and cannot be the same person.'
              : (isEdit ? 'Changes are saved to this task.'
                : 'Creating this task assigns it straight away.')}
          </p>
          <div className="task-form-actions">
            <button type="button" className="lr-btn lr-btn-ghost"
              onClick={() => navigate(-1)}>Cancel</button>
            <button type="submit" className="lr-btn lr-btn-primary"
              disabled={save.isPending || reviewerIsAssignee || Boolean(conflict)
                || (!isEdit && (nobodyAssigned || noReviewer))}>
              {/* Creating and assigning are one action now: a task always has
                  somebody on it (Phase TASK-SIMPLIFICATION). */}
              {save.isPending ? 'Saving…' : (isEdit ? 'Save Changes' : 'Create Task')}
            </button>
          </div>
        </div>
      </form>

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default TaskForm;
