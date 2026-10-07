"""
Task primitives that are not workflow-stage-specific: numbering, the audit
writer, notification, the user snapshot, and attachment validation.

WHAT THIS MODULE REUSES, AND WHY THAT IS NOT A DEPENDENCY ON ANOTHER MODULE
---------------------------------------------------------------------------
Task Management is required to stand alone. It does, and this file is where that
is easiest to check, because every import here is either the standard library,
Django, or project-wide plumbing:

  * config.uploads        - the ONE upload validator (size + extension + magic
    bytes). Reimplementing it here is how the attendance-correction attachment
    bug happened: validation written once, in the wrong place, silently not
    applied the next time the same feature was built.
  * config.nepali_dates   - the BS conversion wrapper, so the number format
    NIFN-TSK-2083-0001 means the same year everything else in the product does.
  * audit.services        - the global, cross-module trail.
  * notifications         - the shared bell and mailer.

There is no import of memos, minutes, circulars, leaves, attendance, inventory,
reports or analytics anywhere in this app. `Task.department` names
"leaves.Department" as a lazy string, which resolves through Django's app
registry without importing the leave module.
"""
import logging

from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

from audit.services import log_action
from config.nepali_dates import to_bs
from config.uploads import validate_attachment as _validate_upload
from notifications.dispatcher import notify

from .models import Task, TaskAuditLog, TaskDependency, TaskNumberSequence
from config.uploads import DOCUMENT_ATTACHMENT_EXTENSIONS

logger = logging.getLogger("tasks")

# What a task may carry. Passed EXPLICITLY into the shared validator rather than
# widening its defaults, so allowing images on a task does not silently also
# widen what memos and attendance corrections accept.
TASK_ATTACHMENT_EXTENSIONS = DOCUMENT_ATTACHMENT_EXTENSIONS | {
    # Phase T2.4. Evidence is regularly a bundle — a folder of screenshots, an
    # export with its assets. ZIP is opted into HERE rather than in the shared
    # defaults, because an archive hides its contents from the magic-byte check:
    # allowing it on tasks must not quietly allow it on memos and attendance
    # corrections too.
    "zip",
    # Slide decks and animated diagrams, as minutes allow.
    "pptx", "gif",
}
# Evidence is often a screen recording still or a scanned sign-off sheet, so this
# sits above the 10MB project default but below the 25MB minute board-pack cap.
MAX_TASK_ATTACHMENT_SIZE = 15 * 1024 * 1024

# A rejection or a block that says nothing leaves the assignee with no idea what
# to do next, which is the failure this length floor exists to prevent.
MIN_REMARK_LENGTH = 10

__all__ = [
    "MAX_TASK_ATTACHMENT_SIZE", "MIN_REMARK_LENGTH", "TASK_ATTACHMENT_EXTENSIONS",
    "current_bs_year", "generate_task_number", "notify_user", "recalculate_progress",
    "blocking_dependencies", "department_head", "departments_without_a_head",
    "record_audit", "resolve_department",
    "user_snapshot", "would_cycle",
    "validate_task_attachment",
]


def current_bs_year():
    """
    The Bikram Sambat year, for the task number.

    The specified format is NIFN-TSK-2083-0001 — 2083 BS, not 2026 AD — so the
    conversion is part of the identifier, not a display concern. If the BS
    library is unavailable the AD year is used rather than raising: a task that
    cannot be created is a worse outcome than a number with the wrong era, and
    the fallback is visible in the number itself.
    """
    stamp = to_bs(timezone.localdate())
    if stamp:
        try:
            return int(stamp.split("-")[0])
        except (ValueError, IndexError):  # pragma: no cover - defensive
            pass
    logger.warning("BS conversion unavailable; task number falls back to the AD year.")
    return timezone.localdate().year


@transaction.atomic
def generate_task_number():
    """
    Return the next task number in the form NIFN-TSK-2083-0001, resetting each
    BS year.

    Handed out by an authoritative per-year counter row locked with
    select_for_update(), so concurrent creations serialise on the lock rather
    than racing on a "max existing number" read. Four digits of padding, and
    because the counter is an integer the sequence keeps working past 9999 — it
    just widens rather than wrapping or colliding.
    """
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    year = current_bs_year()
    # Phase S2: the counter row is per (organization, year). Before this, one
    # row served the whole platform -- so Tenant A creating a task advanced
    # Tenant B's numbering and each tenant saw gaps it could not explain.
    #
    # get_or_create is safe under the unique constraint if two creators race for
    # the first number of the year; the loser retries the get.
    TaskNumberSequence.objects.get_or_create(organization=organization, year=year)
    seq = (TaskNumberSequence.objects.select_for_update()
           .get(organization=organization, year=year))
    seq.last_value += 1
    seq.save(update_fields=["last_value"])
    return numbering.format_number("TSK", year=year, value=seq.last_value,
                                   organization=organization)


def user_snapshot(user):
    """
    Name / designation / department for a user, frozen at the moment of
    recording. Every historical row keeps these beside a SET_NULL foreign key,
    so a closed task still names who did it after the account is deleted.
    """
    if user is None:
        return {"name": "", "designation": "", "department": ""}
    department = ""
    # `department_name` is a property on User that prefers the structured
    # department and falls back to the legacy free-text one. Read defensively so
    # this helper cannot be the thing that breaks on an unusual account.
    try:
        department = getattr(user, "department_name", "") or ""
    except Exception:  # pragma: no cover - defensive
        department = getattr(user, "department", "") or ""
    return {
        "name": (user.get_full_name() or user.username or "")[:150],
        "designation": (getattr(user, "designation", "") or "")[:120],
        "department": department[:150],
    }


def resolve_department(task):
    """
    Fill in a task's department from its primary assignee, then its creator, when
    it was not chosen explicitly.

    Set on the row rather than derived at read time: a task belongs to the
    department it was raised for at the time, and re-deriving it later would
    silently move historical work when somebody transfers.
    """
    if task.department_id or task.department_name:
        if task.department_id and not task.department_name:
            task.department_name = task.department.name
        return task

    for user in (_primary_assignee_user(task), task.created_by):
        if user is None:
            continue
        ref_id = getattr(user, "department_ref_id", None)
        if ref_id:
            task.department_id = ref_id
            task.department_name = user.department_ref.name
            return task
        legacy = getattr(user, "department", "") or ""
        if legacy:
            task.department_name = legacy[:150]
            return task
    return task


def department_head(department_id):
    """
    The head of a department, or None (Phase TASK-GOVERNANCE-HARDENING).

    Read through the app registry rather than by importing the leave module -
    see tasks/models.py on module independence.
    """
    if not department_id:
        return None
    from django.apps import apps

    Department = apps.get_model("leaves", "Department")
    head = (Department.objects.filter(pk=department_id)
            .values_list("head_id", flat=True).first())
    if not head:
        return None
    User = get_user_model()
    return User.objects.filter(pk=head, is_active=True).first()


def departments_without_a_head():
    """
    Active departments nobody answers for, by name.

    Governance depends on this list being empty: every routing decision the task
    module makes for departmental work ends at a department head, and a
    department without one cannot take work on at all (see
    tasks.serializers.TaskWriteSerializer). Surfaced rather than worked around.
    """
    from django.apps import apps

    Department = apps.get_model("leaves", "Department")
    return list(Department.objects.filter(is_active=True)
                .filter(models.Q(head__isnull=True) | models.Q(head__is_active=False))
                .order_by("name").values_list("name", flat=True))


# resolve_reviewer() and its HR fallback were removed in Phase
# TASK-REVIEWER-SELECTION. The creator chooses the reviewer and the field is
# mandatory, so there is no blank left to fill - and a function that quietly
# picked somebody would be exactly the auto-routing the phase removed.
#
# The DEFAULT the create form offers (the caller's department head) is served by
# tasks.views._default_reviewer. It is a suggestion on a form, never a decision
# taken on the server after the fact.


def would_cycle(task, prerequisite):
    """
    True when making `task` wait for `prerequisite` would close a loop
    (Phase TASK-GOVERNANCE-HARDENING).

    Walks FORWARD from the proposed prerequisite through what it is itself
    waiting for. If `task` is anywhere in there, the edge would create a ring of
    tasks each waiting for the next - a deadlock nobody can see and only an
    Admin could break.

    Breadth-first with a `seen` set, so an existing cycle in the data (which
    cannot be created through this API, but could be through a shell or a
    restore) makes this function return rather than hang.
    """
    if str(task.pk) == str(prerequisite.pk):
        return True
    frontier, seen = [prerequisite.pk], set()
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        waits_for = TaskDependency.objects.filter(
            task_id=current).values_list("depends_on_id", flat=True)
        for target in waits_for:
            if target == task.pk:
                return True
            frontier.append(target)
    return False


def blocking_dependencies(task):
    """
    The prerequisites standing in this task's way, in the order they were added.

    Read once, here, so the permission layer, the workflow engine and the API
    payload all answer the question the same way.
    """
    rows = (TaskDependency.objects.filter(task=task)
            .select_related("depends_on").order_by("created_at"))
    return [row for row in rows if row.blocks_progress]


def recalculate_progress(task):
    """
    Re-derive `progress_percent` from the checklist (Phase T2.1/T2.2).

    Applies only while `progress_is_auto` is set, and only when the task
    actually has a checklist. Two guards, for two different reasons:

      * No checklist — there is nothing to derive a percentage FROM, and
        resetting a hand-reported 60% to 0 because nobody wrote a checklist
        would be actively wrong.
      * Not auto — somebody has typed a figure themselves, and the point of
        letting them do that is that their judgement then stands. Overwriting it
        on the next tick would make the control they just used pointless.

    Returns True when the stored number changed, so the caller can decide
    whether the change is worth a timeline row.
    """
    from django.db.models import Count, Q

    # SUBTASKS FIRST (Phase TASK-AUTOSAVE-AND-SUBTASKS). Unlike the checklist,
    # subtasks override a hand-reported figure: a checklist is a memory aid
    # that may not cover the work, a subtask list is the decomposition of the
    # work by definition, so a number that disagrees with it is the wrong one.
    # While subtasks exist the task is forced back onto automatic and the
    # manual endpoint refuses (tasks.workflow.update_progress).
    subtasks = task.subtasks.aggregate(
        total=Count("id"), done=Count("id", filter=Q(is_done=True)))
    if subtasks["total"]:
        derived = round(100 * subtasks["done"] / subtasks["total"])
        fields = []
        if not task.progress_is_auto:
            task.progress_is_auto = True
            fields.append("progress_is_auto")
        if derived != task.progress_percent:
            task.progress_percent = derived
            fields.append("progress_percent")
        if fields:
            task.save(update_fields=fields + ["updated_at"])
        return "progress_percent" in fields

    if not task.progress_is_auto:
        return False
    # A fresh aggregate, NOT `task.checklist_percent`. That property reads
    # `self.checklist.all()`, which the detail view has already prefetched — so
    # after a tick it returns the list as it was BEFORE the tick, and the
    # recalculated figure would be one change behind, permanently. `.aggregate()`
    # always goes to the database.
    tally = task.checklist.aggregate(
        total=Count("id"), done=Count("id", filter=Q(is_done=True)))
    if not tally["total"]:
        return False
    derived = round(100 * tally["done"] / tally["total"])
    if derived == task.progress_percent:
        return False
    task.progress_percent = derived
    task.save(update_fields=["progress_percent", "updated_at"])
    return True


def subtask_tally(task):
    """Fresh (done, total) for the task's subtasks - a query, not the prefetch."""
    from django.db.models import Count, Q

    row = task.subtasks.aggregate(
        total=Count("id"), done=Count("id", filter=Q(is_done=True)))
    return row["done"], row["total"]


def record_assignee_progress(task, user, percent):
    """
    Store what ONE assignee reports, and re-derive the task's overall figure
    (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).

    Returns the overall percentage the task should now carry, or None when the
    actor is not an assignee (an owner correcting a figure, say) — in which case
    the caller's own number stands and no per-person row is touched. Attributing
    a manager's correction to one of the people on the task would put words in
    their mouth.
    """
    row = task.assignees.filter(user=user).first()
    if row is None:
        return None
    row.progress_percent = int(percent)
    row.progress_updated_at = timezone.now()
    row.save(update_fields=["progress_percent", "progress_updated_at"])
    return overall_progress(task)


def overall_progress(task):
    """
    The task's figure across everybody on it: the mean of what each person who
    HAS reported says, rounded.

    Rows that have reported nothing are left out rather than counted as zero —
    see TaskAssignee.progress_percent. Returns None when nobody has reported,
    and the caller then leaves the stored number as it was, which is what keeps
    tasks that pre-date per-person progress reading correctly.

    A fresh query, not the prefetched list: this runs immediately after a row
    was written, and a cached list would be one report behind.
    """
    reported = [value for value in task.assignees.filter(
        progress_percent__isnull=False).values_list("progress_percent", flat=True)]
    if not reported:
        return None
    return round(sum(reported) / len(reported))


def _primary_assignee_user(task):
    row = task.assignees.filter(is_primary=True).select_related("user").first()
    if row is None:
        row = task.assignees.select_related("user").first()
    return row.user if row else None


def _shared_action(action):
    """
    Map a task action onto the SHARED audit vocabulary.

    `audit.AuditLog.action` is a deliberately small enum — eight verbs, in a
    varchar(20) — because every app in the system writes to that one table. A
    task's own eighteen actions do not fit it and must not try: writing
    "task.assignees_changed" there is both too long for the column and outside
    the choice list, so the row would be unreadable by the audit endpoint even if
    it fitted.

    The precise action is preserved in the `transition` metadata key instead,
    which is the convention the memo and circular modules already follow and
    which the audit API already reads.
    """
    from audit.models import AuditLog

    Action = TaskAuditLog.Action
    if action == Action.CREATED:
        return AuditLog.Action.CREATE
    if action == Action.DELETED:
        return AuditLog.Action.DELETE
    if action in (Action.REVIEW_APPROVED, Action.ACCEPTED, Action.CLOSED):
        return AuditLog.Action.APPROVE
    if action in (Action.REWORK_REQUESTED, Action.CANCELLED, Action.BLOCKED):
        return AuditLog.Action.REJECT
    if action in (Action.SUBMITTED_FOR_REVIEW, Action.ASSIGNED):
        return AuditLog.Action.SUBMIT
    if action in (Action.UPDATED, Action.ASSIGNEES_CHANGED, Action.STARTED,
                  Action.UNBLOCKED, Action.PROGRESS_UPDATED,
                  Action.CHECKLIST_UPDATED, Action.EVIDENCE_UPLOADED):
        return AuditLog.Action.UPDATE
    return AuditLog.Action.OTHER


def record_audit(task, actor, action, *, remarks="", from_status="", to_status="",
                 metadata=None, request=None):
    """
    Write one row of the task's activity timeline, AND one row of the global
    audit log.

    Two logs because they answer different questions: the global one is "what has
    this user done across the system", the per-task one is "what happened to this
    task" and is what the detail page renders. Both are append-only.

    Neither write is wrapped in a try/except. A transition is recorded inside the
    same transaction that performs it, so a failed audit write must fail the
    transition too: a state change with no trail explaining it is the exact
    evidence gap this module exists to close. Swallowing the error would not even
    work — a failed statement poisons the surrounding atomic block, so the next
    query raises anyway, just somewhere less honest.
    """
    entry = TaskAuditLog.objects.create(
        task=task,
        actor=actor if actor is not None and getattr(actor, "is_authenticated", False) else None,
        actor_name=user_snapshot(actor)["name"],
        action=action,
        from_status=from_status or "",
        to_status=to_status or "",
        remarks=remarks or "",
        metadata=metadata or {},
    )
    log_action(actor, _shared_action(action), instance=task,
               changes={"transition": str(action), "from": from_status or "",
                        "to": to_status or "", "remarks": remarks or "",
                        **(metadata or {})},
               request=request)
    return entry


def notify_user(user, category, title, body, task, *, idempotency_key=None):
    """
    Send one in-app/email notification about a task, respecting the recipient's
    own preferences (the dispatcher handles that).

    Never raises. A notification backend that is down must not roll back a
    transition that has already been recorded — the work happened, and the
    timeline is the record of it; the bell is a convenience.
    """
    if user is None:
        return None
    try:
        return notify(
            user, category, title, body,
            action_url=f"/tasks/{task.pk}",
            idempotency_key=idempotency_key,
            object_id=str(task.pk),
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Task notification failed for %s", getattr(user, "pk", None))
        return None


def validate_task_attachment(uploaded):
    """
    Validate one uploaded file against the task allowlist.

    Delegates to the project validator, which checks size, extension AND the
    leading bytes — so a .docx that is really an HTML file is refused rather than
    stored and served back later.
    """
    return _validate_upload(
        uploaded,
        max_size=MAX_TASK_ATTACHMENT_SIZE,
        extensions=TASK_ATTACHMENT_EXTENSIONS,
    )
