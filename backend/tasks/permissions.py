"""
Who may see and do what with a task.

The rules live here and ONLY here. The API exposes them to the client as
capability flags on the detail payload (`can_edit`, `can_accept`, `can_review`,
...) so the UI renders its action bar from the server's answer instead of
re-deriving authorization in JavaScript, where it would drift.

THE FOUR ROLES, AS SPECIFIED
----------------------------
  Employee        view own tasks, update progress, comment, upload evidence
  Department Head assign tasks, review tasks, close tasks   (within their scope)
  HR              assign organization-wide, review, monitor completion
  Admin           full access

The stored role values are maker/checker/approver/admin with the business labels
Employee/Department Head/HR/Admin on top of them, so `Roles.HR` does not exist —
reaching for it raises AttributeError. That is why the helpers below exist.

SCOPE IS NOT THE SAME AS ROLE
-----------------------------
A Department Head may assign and review, but only within the departments they
head plus their own. HR and Admin are organisation-wide. Without that split,
"Department Head" would mean "second admin", and every department head in the
organisation would be able to close every other department's work.

SHARED WORK WAITS FOR EVERYBODY
------------------------------
A task with SEVERAL assignees does no WORK until all of them have accepted
(Phase TASK-MULTI-ASSIGNEE-COLLABORATION). Progress, evidence, ticking the
checklist and submitting for review each ask `task.work_may_start` before
saying yes, and the workflow engine refuses the same thing again behind them.

TALKING IS NOT WORKING (Phase TASK-COLLABORATION-HARDENING). Comments and
clarification requests are open to everybody who can read the task, at every
point in its life, including while it waits to be accepted. Deciding whether to
accept is precisely when the people on a shared task need to talk to each
other, and a gate across that conversation moves it to email — where the task
cannot see it and the audit trail does not record it.

TERMINAL IS ENFORCED, NOT STYLED
--------------------------------
A closed or cancelled task is read-only because every mutating permission
returns False for it and the workflow engine refuses the transition — not
because the buttons are hidden. Hiding a button stops the honest user; the guard
stops the request. There are tests that PATCH and DELETE a closed task directly
and expect 403.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import permissions

from .models import Task
from users.roles import has_org_wide_read as _org_wide_read, is_bod

User = get_user_model()


# ---------------------------------------------------------------------------
# Role predicates
# ---------------------------------------------------------------------------
def is_admin(user):
    return (getattr(user, "role", None) == User.Roles.ADMIN
            or getattr(user, "is_superuser", False))


def is_hr(user):
    """HR is the APPROVER role. See the module docstring."""
    return getattr(user, "role", None) == User.Roles.APPROVER


def is_department_head(user):
    """Department Head is the CHECKER role."""
    return getattr(user, "role", None) == User.Roles.CHECKER


def has_org_wide_read(user):
    """HR, Admin and the Board read every task; monitoring completion is their job."""
    return is_admin(user) or is_hr(user) or _org_wide_read(user)


def department_ids_in_scope(user):
    """
    Departments a department head answers for: the ones they are recorded as head
    of, plus their own. Queried through the app registry rather than by importing
    the leave module — see tasks/models.py on module independence.
    """
    from django.apps import apps

    Department = apps.get_model("leaves", "Department")
    ids = set(Department.objects.filter(head=user).values_list("id", flat=True))
    if getattr(user, "department_ref_id", None):
        ids.add(user.department_ref_id)
    return ids


def can_assign(user):
    """
    Who may hand work to SOMEBODY ELSE: Department Head, HR, Admin.

    Phase TASK-MANAGEMENT-ASANA-MODEL opened task CREATION to everybody (see
    `can_create_task`), and this predicate kept the other half of its old job:
    giving a task to another person. The two came apart because they are two
    different powers. Raising your own work is collaboration; putting work on
    another person's list is management, and an Employee who could do it would
    be able to task their own Department Head.
    """
    return is_admin(user) or is_hr(user) or is_department_head(user)


def can_create_task(user):
    """
    Everybody raises tasks, and everybody chooses who does them and who reviews
    them (Phase TASK-SIMPLIFICATION).

    The three kinds of task - Personal, Department, Assigned - are gone, and
    with them the rules about which kinds each role could raise. What is left is
    one task, created by anybody, with an assignee and a reviewer the creator
    picks.
    """
    return bool(getattr(user, "is_authenticated", False))


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def visible_task_filter(user):
    """
    A Q object selecting the tasks `user` may read, or None for an org-wide
    reader (the caller then skips filtering entirely rather than building a
    tautology that the query planner has to unpick).

    Four grounds: you are assigned it, you raised it, you are its reviewer, or
    you head the department it belongs to.
    """
    if has_org_wide_read(user):
        return None

    visible = Q(assignees__user=user) | Q(created_by=user) | Q(reviewer=user)

    if is_department_head(user):
        dept_ids = department_ids_in_scope(user)
        dept_scope = Q()
        if dept_ids:
            dept_scope |= Q(department_id__in=dept_ids)
        if getattr(user, "department", ""):
            dept_scope |= Q(department_name__iexact=user.department)
        if dept_scope:
            # A draft is nobody's business but its author's until it is assigned.
            visible |= dept_scope & ~Q(status=Task.Status.DRAFT)

    return visible


def can_read(user, task):
    if has_org_wide_read(user):
        return True
    if task.created_by_id == user.id or task.reviewer_id == user.id:
        return True
    if is_assignee(user, task):
        return True
    if is_department_head(user) and task.status != Task.Status.DRAFT:
        dept_ids = department_ids_in_scope(user)
        if task.department_id and task.department_id in dept_ids:
            return True
        own = getattr(user, "department", "") or ""
        if own and task.department_name:
            return task.department_name.lower() == own.lower()
    return False


# `can_view` is the name the serializer's flag reads; one rule, two callers.
can_view = can_read


# ---------------------------------------------------------------------------
# Ownership — "may I change what this task ASKS FOR?"
# ---------------------------------------------------------------------------
def is_owner(user, task):
    """
    The task's owner: whoever raised it, its named reviewer, HR, Admin, or the
    head of the department it belongs to.

    This is the authority to define and dispose of the task — assign it, review
    it, close it, cancel it — as distinct from the authority to DO it, which is
    what being an assignee confers.
    """
    if is_admin(user) or is_hr(user):
        return True
    if task.created_by_id == user.id or task.reviewer_id == user.id:
        return True
    if is_department_head(user):
        dept_ids = department_ids_in_scope(user)
        if task.department_id and task.department_id in dept_ids:
            return True
        own = getattr(user, "department", "") or ""
        if own and task.department_name:
            return task.department_name.lower() == own.lower()
    return False


def is_assignee(user, task):
    """
    Is this person one of the people doing the task?

    Read off the PREFETCHED assignee rows rather than asked of the database
    (Phase TASK-COLLABORATION-HARDENING). `capabilities()` calls the predicates
    below a dozen times to build one payload, and nearly all of them come back
    here; as an `.exists()` that was eleven identical queries per detail render,
    on rows the same response had already loaded to draw the assignee chips.

    Where the rows are NOT prefetched this costs exactly what the old query
    cost, so there is no caller it makes worse.
    """
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    return any(row.user_id == user_id for row in task.assignees.all())


def acceptance_is_pending(task):
    """
    True while shared work is still waiting for somebody to accept it.

    One question, asked by every capability below that gates on it, so a page
    that offers "Upload evidence" but refuses "Report progress" cannot happen.
    The rule itself lives on the model (Task.work_may_start), beside the
    acceptance data it reads.
    """
    return not task.work_may_start


def can_do_work(user, task):
    """
    May this person DO the task right now: they are on it, and everybody on it
    has accepted.

    The four work capabilities below are this plus a list of statuses.
    """
    return not acceptance_is_pending(task) and is_assignee(user, task)


# ---------------------------------------------------------------------------
# Mutating capabilities. Every one refuses a terminal task first.
# ---------------------------------------------------------------------------
def _live(task):
    return task.status not in Task.TERMINAL_STATUSES


# What an ASSIGNEE may change about a task they did not raise
# (Phase TASK-MULTI-ASSIGNEE-COLLABORATION). Everything else on the form —
# the reviewer, the department, the goal, who else is on it — stays with the
# owner: those are the terms the work was given out under, and a person
# doing the work may not rewrite who signs it off.
ASSIGNEE_EDITABLE_FIELDS = frozenset({"title", "description", "priority", "due_date"})


def can_edit(user, task):
    """
    Change the task's definition.

    THE OWNER, and now also ANYBODY ASSIGNED TO IT
    (Phase TASK-MULTI-ASSIGNEE-COLLABORATION). A shared task's details are
    discovered by the people doing it — a due date that was always going to
    slip, a title that describes the wrong half of the job — and a correction
    that only a manager could make is a correction that gets made in a comment
    instead, where no report reads it.

    WHICH FIELDS each of them may change is a separate question, answered by
    `can_edit_all_fields` and enforced in the view: an assignee edits the four
    in ASSIGNEE_EDITABLE_FIELDS, an owner edits everything.

    OVERDUE TASKS STAY RESCUABLE (policy update). A task that has run past its
    due date must remain editable by its OWNER whatever open stage it is in -
    including once it has been submitted for review - so the date can be extended
    and the details corrected. Otherwise a late task strands where nobody can
    move it. `is_overdue` is only ever true for open work, so this never touches
    a closed or cancelled task.
    """
    if not _live(task):
        return False
    owner = is_owner(user, task)
    if owner and task.is_overdue:
        return True
    return (task.status in Task.EDITABLE_STATUSES
            and (owner or is_assignee(user, task)))


def can_edit_all_fields(user, task):
    """
    Whether this editor may change the terms as well as the details: the
    reviewer, the department, the goal, and the assignee list.

    Owner authority, unchanged. It is reported as its own capability so the form
    can hide the pickers rather than offering fields the server would refuse.
    """
    return can_edit(user, task) and is_owner(user, task)


def can_delete(user, task):
    """
    Only an unassigned draft can be deleted, and only by whoever raised it or an
    Admin. Once a task has been given to somebody it is part of their record and
    is cancelled, not erased.
    """
    if task.status != Task.Status.DRAFT:
        return False
    return task.created_by_id == user.id or is_admin(user)


def can_manage_assignees(user, task):
    return (_live(task)
            and task.status not in (Task.Status.UNDER_REVIEW, Task.Status.COMPLETED)
            and is_owner(user, task))


def can_assign_task(user, task):
    return (_live(task)
            and task.status in (Task.Status.DRAFT, Task.Status.ASSIGNED)
            and is_owner(user, task)
            and task.assignees.exists())


def can_accept(user, task):
    if not _live(task) or task.status != Task.Status.ASSIGNED:
        return False
    # The prefetched rows, for the reason given on `is_assignee`.
    user_id = getattr(user, "id", None)
    return any(row.user_id == user_id and row.accepted_at is None
               for row in task.assignees.all())


def can_request_clarification(user, task):
    """
    Ask for more information before the work starts.

    Deliberately NOT gated on acceptance (Phase TASK-COLLABORATION-HARDENING):
    a question asked before accepting is the whole point of the channel, and on
    a shared task an assignee who has already accepted may still be waiting on
    an answer somebody else needs. It stays open for every assignee for as long
    as the task is Assigned — which, on shared work, is exactly the window in
    which it is pending acceptance.
    """
    return (_live(task)
            and task.status == Task.Status.ASSIGNED
            and is_assignee(user, task))


def can_update_progress(user, task):
    """
    The assignee does the work, so the assignee reports it. Owners are excluded
    deliberately: a progress number a manager typed on somebody else's behalf is
    not a report, and the checklist and evidence beside it would disagree with it.
    """
    return (_live(task)
            # ASSIGNED included since Phase TASK-SIMPLIFICATION: starting work
            # IS the first progress report, and the ladder has no acceptance
            # step to pass through first. The engine says the same
            # (tasks.workflow.start), and a permission that disagreed with it
            # would refuse a button the workflow would have allowed.
            and task.status in (Task.Status.ASSIGNED, Task.Status.ACCEPTED,
                                Task.Status.IN_PROGRESS, Task.Status.BLOCKED)
            # ...but shared work waits for everybody to accept first
            # (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).
            and can_do_work(user, task))


def can_submit_for_review(user, task):
    """
    Every task is reviewed now, so the only question is who is doing it - and
    whether they have started. Submitting work that was never started skips the
    middle of the ladder the phase specifies (Created -> In Progress ->
    Submitted), and the engine refuses it; offering the button here would be
    offering an action the API would reject.
    """
    return (_live(task)
            and task.status in (Task.Status.ACCEPTED, Task.Status.IN_PROGRESS)
            and can_do_work(user, task)
            # Phase TASK-AUTOSAVE-AND-SUBTASKS: the decomposition IS the work,
            # so an open subtask is unfinished work and cannot be submitted as
            # done. The engine refuses it too (tasks.workflow.submit_for_review).
            and not has_open_subtasks(task))


def has_open_subtasks(task):
    """Any subtask still open? Reads the prefetched rows."""
    return any(not row.is_done for row in task.subtasks.all())


def can_review(user, task):
    """
    Approve, or send back for rework. THE SELECTED REVIEWER, and nobody else
    (Phase TASK-REVIEWER-SELECTION).

    It used to be owner authority - the creator, the reviewer, HR, an Admin, or
    the head of the task's department - which meant a reviewer the creator chose
    could be overruled by somebody who was never asked. The phase makes the
    choice mean something: whoever was named on the form is the approver.

    ADMIN IS KEPT as an override. A named reviewer who leaves, or is away for a
    month, would otherwise strand the work with no way forward but editing the
    database; an Admin unsticking it leaves an audit row saying who did.

    A TASK WITH NO REVIEWER falls back to the old owner rule. Only rows created
    before this phase can be in that state - the field is mandatory now - and
    the alternative would be historical work nobody can finish.

    The assignee is excluded throughout, even when they are also the reviewer or
    an Admin: a signature from the applicant on their own certificate is what
    the review step exists to prevent.
    """
    if not _live(task) or task.status != Task.Status.UNDER_REVIEW:
        return False
    if is_assignee(user, task):
        return False
    if task.reviewer_id:
        return user.id == task.reviewer_id or is_admin(user)
    return is_owner(user, task)


def can_close(user, task):
    """
    HR / HOD verification. Explicitly NOT the assignee — a task closed by the
    person who did it is unverified work wearing a verified label. The creator
    may close it only when they are not also an assignee, for the same reason.
    """
    if not _live(task) or task.status != Task.Status.COMPLETED:
        return False
    if is_assignee(user, task):
        return False
    # A subtask reopened after approval leaves verified work with an open
    # piece under it; closing would file it as finished. See can_submit_for_review.
    if has_open_subtasks(task):
        return False
    return is_owner(user, task)


def can_block(user, task):
    """Either side may block: the assignee hits the blocker, the owner knows of one."""
    return (_live(task)
            and task.status in (Task.Status.ACCEPTED, Task.Status.IN_PROGRESS)
            and (is_assignee(user, task) or is_owner(user, task)))


def can_unblock(user, task):
    return (_live(task)
            and task.status == Task.Status.BLOCKED
            and (is_assignee(user, task) or is_owner(user, task)))


def can_cancel(user, task):
    return _live(task) and is_owner(user, task)


def can_manage_dependencies(user, task):
    """
    Say what this task waits for: the people doing it, and the people who own it
    (Phase TASK-GOVERNANCE-HARDENING).

    The assignee is included deliberately. They are the person who discovers,
    halfway through, that they cannot finish until somebody else's work lands -
    and a dependency only a manager could record is one that gets recorded in a
    comment instead, where nothing enforces it.
    """
    return _live(task) and (is_assignee(user, task) or is_owner(user, task))


def can_comment(user, task):
    """
    Anyone who can read a live task can comment on it. Comments are how a
    question gets asked, and restricting them to the two named parties is how
    questions end up in email instead.

    NOT GATED ON ACCEPTANCE (Phase TASK-COLLABORATION-HARDENING). The previous
    phase silenced assignees until everybody had accepted, reading "no task
    activity" strictly. That was wrong in practice: deciding whether to accept
    is exactly when three people need to talk to each other — "can we do this by
    Friday?", "I can take the cabling if you take the config" — and a team that
    cannot hold that conversation on the task holds it somewhere the task cannot
    see. The acceptance gate belongs on DOING the work, not on discussing it.

    Comments change nothing about the work: no progress, no evidence, no
    transition. Nothing that the gate exists to prevent can happen through one.
    """
    return _live(task) and can_read(user, task)


def can_upload_attachment(user, task):
    """
    Evidence and reference files: the people doing the work, and the people who
    own it. An assignee may not upload to shared work nobody has accepted yet —
    see `can_comment` for why the owner still may.
    """
    return _live(task) and (can_do_work(user, task) or is_owner(user, task))


def can_manage_checklist(user, task):
    """Add or remove checklist rows — defining the work, so owner authority."""
    return _live(task) and is_owner(user, task)


def can_tick_checklist(user, task):
    """
    Tick a row off — doing the work, so assignee authority (owners too), and
    not before shared work has been accepted by everybody on it.
    """
    return _live(task) and (can_do_work(user, task) or is_owner(user, task))


# ---------------------------------------------------------------------------
# Subtasks (Phase TASK-AUTOSAVE-AND-SUBTASKS). Composed from the predicates
# above, like the checklist: defining the pieces is owner authority, doing them
# is assignee authority. No new idea of ownership.
# ---------------------------------------------------------------------------
# Completing a subtask changes the derived progress, so it is allowed exactly
# where a progress report is allowed (see can_update_progress): never on a task
# under review, where the forced 100 would be fought by the derived number.
SUBTASK_WORK_STATUSES = frozenset({
    Task.Status.ASSIGNED, Task.Status.ACCEPTED, Task.Status.IN_PROGRESS,
    Task.Status.BLOCKED,
})


def can_manage_subtasks(user, task):
    """Add, edit, assign, reorder or delete subtasks - owner authority."""
    return can_manage_checklist(user, task)


def can_complete_subtasks(user, task):
    """May this person tick ANY subtask on the task (owners and workers)."""
    return (_live(task) and task.status in SUBTASK_WORK_STATUSES
            and (can_do_work(user, task) or is_owner(user, task)))


def can_complete_subtask(user, subtask):
    """
    Tick one subtask off. Its own assignee first; then anybody who may do the
    work, and the owner - the same people who may tick a checklist row.
    """
    task = subtask.task
    if not _live(task) or task.status not in SUBTASK_WORK_STATUSES:
        return False
    if subtask.assignee_id == getattr(user, "id", None):
        return can_do_work(user, task) or is_owner(user, task)
    return can_do_work(user, task) or is_owner(user, task)


def can_edit_subtask(user, subtask):
    """
    Change a subtask's definition. The owner may change everything; the
    subtask's own assignee may edit its notes (`description`) - that is
    reporting on their piece, not redefining it. The view screens the fields.
    """
    task = subtask.task
    if can_manage_subtasks(user, task):
        return True
    return (_live(task) and subtask.assignee_id == getattr(user, "id", None)
            and can_do_work(user, task))


# ---------------------------------------------------------------------------
# Phase T2 capabilities. Each one reuses the T1 predicates above rather than
# introducing a second idea of who owns a task — the RBAC design is unchanged.
# ---------------------------------------------------------------------------
def can_edit_comment(user, comment):
    """
    Only the AUTHOR may edit their own comment, and only while the task is live.

    Not the owner, not HR, not an Admin. An edit is presented to every reader as
    the author's own words with an "edited" marker beside them; letting somebody
    else rewrite them under that marker would make the marker a lie.
    """
    if comment.author_id != user.id:
        return False
    return _live(comment.task)


def can_remove_attachment(user, attachment):
    """
    The person who attached it, or the task's owner.

    An assignee may withdraw a file they uploaded by mistake; an owner may
    remove anything from a task they answer for. An assignee may NOT remove
    somebody else's evidence, which would let one member of a shared task delete
    another's work.
    """
    task = attachment.task
    if not _live(task) or attachment.is_removed:
        return False
    return attachment.uploaded_by_id == user.id or is_owner(user, task)


def can_flag_evidence(user, attachment):
    """Re-labelling evidence vs. reference is the same authority as removing it."""
    task = attachment.task
    if not _live(task) or attachment.is_removed:
        return False
    return attachment.uploaded_by_id == user.id or is_owner(user, task)


def can_view_download_log(user, task):
    """
    Who opened a file is oversight information, so it is owner-only.

    An assignee seeing the full read history of a task they are on would learn
    who has been checking up on them, which is not theirs to know and would
    change how the log gets used.
    """
    return is_owner(user, task)


def can_manage_templates(user):
    """
    Creating and retiring templates is the same authority as creating tasks.

    A template is a task shape somebody else will be held to; the roles that may
    hand out work are the roles that may define it.
    """
    return can_assign(user)


def can_apply_template(user, task):
    """Applying a template writes the checklist, so it is the checklist authority."""
    return can_manage_checklist(user, task)


def capabilities(user, task):
    """Every flag the detail payload carries, computed in one place."""
    return {
        "can_view": can_read(user, task),
        "can_edit": can_edit(user, task),
        # Phase TASK-MULTI-ASSIGNEE-COLLABORATION: an assignee edits the
        # details, an owner edits the terms as well. The form reads this to
        # decide whether to show the reviewer and assignee pickers.
        "can_edit_all_fields": can_edit_all_fields(user, task),
        "can_delete": can_delete(user, task),
        "can_manage_assignees": can_manage_assignees(user, task),
        "can_assign": can_assign_task(user, task),
        "can_accept": can_accept(user, task),
        "can_request_clarification": can_request_clarification(user, task),
        "can_update_progress": can_update_progress(user, task),
        "can_submit_for_review": can_submit_for_review(user, task),
        "can_manage_dependencies": can_manage_dependencies(user, task),
        "can_review": can_review(user, task),
        "can_close": can_close(user, task),
        "can_block": can_block(user, task),
        "can_unblock": can_unblock(user, task),
        "can_cancel": can_cancel(user, task),
        "can_comment": can_comment(user, task),
        "can_upload": can_upload_attachment(user, task),
        "can_manage_checklist": can_manage_checklist(user, task),
        "can_tick_checklist": can_tick_checklist(user, task),
        # Phase TASK-AUTOSAVE-AND-SUBTASKS.
        "can_manage_subtasks": can_manage_subtasks(user, task),
        "can_complete_subtasks": can_complete_subtasks(user, task),
        "open_subtask_count": sum(1 for row in task.subtasks.all() if not row.is_done),
        # Phase T2.
        "can_apply_template": can_apply_template(user, task),
        "can_save_as_template": can_manage_templates(user),
        "can_view_download_log": can_view_download_log(user, task),
    }


# ---------------------------------------------------------------------------
# DRF permission classes
# ---------------------------------------------------------------------------
class CanViewTask(permissions.BasePermission):
    """Object-level read gate. The queryset is scoped too; this is the backstop."""
    message = "You do not have access to this task."

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return can_read(request.user, obj)
        return True  # writes are gated by the classes below and by the views


class CanCreateTask(permissions.BasePermission):
    """
    Everybody may create a task. WHICH KIND is checked in the serializer, where
    the requested type is known and the refusal can name it - a permission class
    that only sees `view.action` could say no but could not say why.
    """
    message = "You cannot create tasks."

    def has_permission(self, request, view):
        if view.action == "create":
            return can_create_task(request.user)
        return True


class CanMutateTask(permissions.BasePermission):
    message = "This task can no longer be edited."

    def has_object_permission(self, request, view, obj):
        return can_edit(request.user, obj)


class CanDeleteTask(permissions.BasePermission):
    message = "Only an unassigned draft can be deleted."

    def has_object_permission(self, request, view, obj):
        return can_delete(request.user, obj)
