"""
Memo governance flows added in Phase 49.5: the note round, approver
unavailability, step reassignment, and archived access sharing.

WHY A SEPARATE MODULE
---------------------
workflow.py is the approval engine and it has exactly one job: advance a
sequential chain safely. Everything here sits deliberately beside that engine
rather than inside it:

  * a note round never touches the chain at all;
  * unavailability and reassignment rewrite ONE step's assignee and never its
    sequence or role, so no approval already given is invalidated;
  * an archive grant adds readers, never approvers.

Keeping them out of workflow.py is what makes that claim checkable. It also means
the engine's core invariant - exactly one step ACTIVE, completing it activates the
next - is not weakened by any of this.

The design rules from workflow.py are inherited unchanged: one atomic block per
mutation, the memo re-read under select_for_update(), guard failures as DRF
ValidationError, and no state change without both a history row and an audit row.
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from users.models import User

from .models import (
    Memo, MemoApprovalStep, MemoArchiveAccess, MemoAssignmentTransfer, MemoNote,
    MemoNoteAudit, MemoNoteRecipient, MemoStepUnavailability, MemoWorkflowStep,
)
from .services import _record_step, create_audit_log

logger = logging.getLogger("memos")

RoleType = MemoWorkflowStep.RoleType
StepStatus = MemoWorkflowStep.StepStatus
NoteStatus = MemoNoteRecipient.NoteStatus
Action = MemoApprovalStep.Action

MIN_REASON_LENGTH = 10


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _lock(memo):
    return Memo.objects.select_for_update().get(pk=memo.pk)


def _describe(user):
    if user is None:
        return ""
    return user.get_full_name() or user.username


def _snapshot(user):
    """Designation and department as they are NOW, for a register printed later."""
    return {
        "user_name": _describe(user),
        "designation": getattr(user, "designation", "") or "",
        "department_label": (getattr(user, "department_name", None)
                            or getattr(user, "department", "") or ""),
    }


def _is_admin(user):
    return getattr(user, "role", None) == User.Roles.ADMIN


def _is_hr(user):
    return getattr(user, "role", None) == User.Roles.APPROVER


def _require_reason(reason, field="reason"):
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise ValidationError({field: (
            f"A reason of at least {MIN_REASON_LENGTH} characters is required. "
            "This is the only record of why the change was made."
        )})
    return reason


def _resolve_user(user_id, field):
    if not user_id:
        raise ValidationError({field: "This field is required."})
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise ValidationError({field: "No such active employee."})
    return user


# ===========================================================================
# Blocker 1 - the note round
# ===========================================================================
def note_round(memo, create=False, actor=None):
    """The memo's note round, created on first use when `create` is set."""
    existing = MemoNote.objects.filter(memo=memo).first()
    if existing or not create:
        return existing
    return MemoNote.objects.create(memo=memo, opened_by=actor)


def can_request_notes(user, memo):
    """
    Who may ask someone to note a memo.

    The brief's list is Recommender, Reviewer, Supporter, Approver - so anyone
    holding a step in this memo's chain, at any position and whether or not it is
    their turn. Reading a memo is not enough: a note request generates work for
    somebody else. The author and HR/Admin are included because the author raised
    the memo and HR/Admin already read everything.
    """
    if memo.status == Memo.Status.DRAFT:
        # Nobody can be asked to note a document that has not left its author.
        return False
    if _is_admin(user) or _is_hr(user):
        return True
    if memo.created_by_id == user.id:
        return True
    return memo.workflow_steps.filter(assignee_id=user.id).exclude(
        status=StepStatus.SKIPPED).exists()


@transaction.atomic
def request_notes(memo, actor, user_ids, remarks="", request=None):
    """
    Ask one or more people to note this memo.

    Additive and idempotent: someone already on the list is left alone rather
    than reset to Pending, because clearing a note somebody has already given
    would destroy the record it exists to keep.
    """
    memo = _lock(memo)
    if not can_request_notes(actor, memo):
        raise PermissionDenied(
            "Only the memo author, someone in its approval chain, or HR/Admin may "
            "request a note.")
    if not user_ids:
        raise ValidationError({"users": "Name at least one person to note this memo."})

    round_ = note_round(memo, create=True, actor=actor)
    added = []
    for user_id in user_ids:
        user = _resolve_user(user_id, "users")
        if MemoNoteRecipient.objects.filter(note_round=round_, user=user).exists():
            continue
        recipient = MemoNoteRecipient.objects.create(
            note_round=round_, user=user, added_by=actor, **_snapshot(user))
        MemoNoteAudit.objects.create(
            note_round=round_, recipient=recipient, action=MemoNoteAudit.Action.REQUESTED,
            actor=actor, actor_name=_describe(actor),
            subject_name=recipient.user_name, remarks=remarks)
        added.append(recipient)

    for recipient in added:
        _notify_note_request(recipient, memo)

    if added:
        names = ", ".join(r.user_name for r in added)
        _record_step(memo, actor, Action.COMMENTED,
                     comment=f"Note requested from {names}.")
        create_audit_log(actor, "update", instance=memo, request=request,
                         metadata={"transition": "note_requested",
                                   "recipients": [r.user_name for r in added],
                                   "remarks": remarks})
    return round_, added


def _notify_note_request(recipient, memo):
    from .workflow import _notify
    if recipient.user is None:
        return
    _notify(
        recipient.user, "MEMO_NOTE_REQUIRED",
        f"Please note memo {memo.memo_number}",
        f"You have been asked to note '{memo.subject}'. Noting records that you "
        "have read it; it is not an approval and it does not block the workflow.",
        memo,
    )
    MemoNoteRecipient.objects.filter(pk=recipient.pk).update(notified_at=timezone.now())


@transaction.atomic
def record_note(memo, actor, remarks="", request=None):
    """
    Record that `actor` has noted this memo.

    Never advances the workflow and never changes the memo's status - that is the
    whole point of the feature, and it is guaranteed here by simply not calling
    anything in workflow.py.
    """
    memo = _lock(memo)
    round_ = note_round(memo)
    recipient = (round_.recipients.filter(user=actor).first() if round_ else None)
    if recipient is None:
        raise ValidationError({"note": "You have not been asked to note this memo."})
    if recipient.status == NoteStatus.NOTED:
        raise ValidationError({"note": "You have already noted this memo."})

    recipient.status = NoteStatus.NOTED
    recipient.remarks = (remarks or "").strip()
    recipient.noted_at = timezone.now()
    recipient.save(update_fields=["status", "remarks", "noted_at"])

    MemoNoteAudit.objects.create(
        note_round=round_, recipient=recipient, action=MemoNoteAudit.Action.NOTED,
        actor=actor, actor_name=_describe(actor), subject_name=recipient.user_name,
        remarks=recipient.remarks)
    _record_step(memo, actor, Action.NOTED, comment=recipient.remarks)
    create_audit_log(actor, "update", instance=memo, request=request,
                     metadata={"transition": "noted", "remarks": recipient.remarks})
    return recipient


@transaction.atomic
def withdraw_note_request(memo, actor, recipient_id, request=None):
    """
    Withdraw an outstanding note request.

    A note already GIVEN cannot be withdrawn - it is a statement of fact by the
    person who gave it, and deleting it would be rewriting their record.
    """
    memo = _lock(memo)
    if not can_request_notes(actor, memo):
        raise PermissionDenied("You cannot change this memo's note list.")
    round_ = note_round(memo)
    recipient = (round_.recipients.filter(pk=recipient_id).first() if round_ else None)
    if recipient is None:
        raise ValidationError({"note": "No such note request on this memo."})
    if recipient.status == NoteStatus.NOTED:
        raise ValidationError({"note": (
            "This person has already noted the memo. A note that has been given "
            "is part of the record and cannot be withdrawn.")})

    name = recipient.user_name
    MemoNoteAudit.objects.create(
        note_round=round_, recipient=None, action=MemoNoteAudit.Action.REMOVED,
        actor=actor, actor_name=_describe(actor), subject_name=name)
    recipient.delete()
    create_audit_log(actor, "update", instance=memo, request=request,
                     metadata={"transition": "note_withdrawn", "subject": name})
    return round_


@transaction.atomic
def remind_notes(memo, actor, request=None):
    """Re-notify everyone whose note is still outstanding."""
    memo = _lock(memo)
    if not can_request_notes(actor, memo):
        raise PermissionDenied("You cannot send reminders on this memo.")
    round_ = note_round(memo)
    if round_ is None:
        return 0
    outstanding = list(round_.recipients.filter(status=NoteStatus.PENDING))
    for recipient in outstanding:
        _notify_note_request(recipient, memo)
        MemoNoteAudit.objects.create(
            note_round=round_, recipient=recipient,
            action=MemoNoteAudit.Action.REMINDED, actor=actor,
            actor_name=_describe(actor), subject_name=recipient.user_name)
    MemoNoteRecipient.objects.filter(
        pk__in=[r.pk for r in outstanding]).update(reminded_at=timezone.now())
    if outstanding:
        create_audit_log(actor, "update", instance=memo, request=request,
                         metadata={"transition": "note_reminded",
                                   "count": len(outstanding)})
    return len(outstanding)


def note_summary(memo):
    """Register + tally for the detail payload and the PDF."""
    round_ = note_round(memo)
    if round_ is None:
        return {"is_open": False, "total": 0, "pending": 0, "completed": 0,
                "is_complete": False, "opened_at": None, "recipients": []}
    recipients = list(round_.recipients.select_related("user"))
    completed = sum(1 for r in recipients if r.status == NoteStatus.NOTED)
    return {
        "is_open": True,
        "total": len(recipients),
        "completed": completed,
        "pending": len(recipients) - completed,
        "is_complete": bool(recipients) and completed == len(recipients),
        "opened_at": round_.opened_at,
        "recipients": [{
            "id": str(r.id),
            "user_id": str(r.user_id) if r.user_id else None,
            "name": r.display_name,
            "designation": r.designation,
            "department": r.department_label,
            "status": r.status,
            "status_label": r.get_status_display(),
            "remarks": r.remarks,
            "noted_at": r.noted_at,
            "reminded_at": r.reminded_at,
        } for r in recipients],
    }


# ===========================================================================
# Blockers 2 and 3 - unavailability and reassignment
# ===========================================================================
def can_manage_assignments(user, memo):
    """
    Who may declare an absence or move a step.

    The brief names Supervisor, Department Head and HR/Admin. `role` carries
    permissions and `employee_type` carries org rank, and the Phase 49 audit
    found the memo module reading only the former - so a Supervisor had no memo
    authority at all. Both are consulted here, which is what makes the brief's
    word "Supervisor" mean something.

    The memo's own author is included: they own the routing they built.
    """
    if _is_admin(user) or _is_hr(user):
        return True
    if memo.created_by_id == user.id:
        return True
    if getattr(user, "role", None) == User.Roles.CHECKER:
        return True
    return getattr(user, "employee_type", None) in {
        User.EmployeeType.SUPERVISOR, User.EmployeeType.MANAGER,
        User.EmployeeType.DEPARTMENT_HEAD, User.EmployeeType.HR_OFFICER,
    }


def _live_step(memo, step_id=None):
    """
    The step a reassignment may target: the active one, or a named pending one.

    Completed and rejected steps are refused. Moving a step somebody has already
    acted on would reassign an approval that has been given, which is precisely
    the thing the frozen-matrix rule exists to prevent.
    """
    steps = memo.workflow_steps.all()
    if step_id:
        step = steps.filter(pk=step_id).first()
        if step is None:
            raise ValidationError({"step": "No such step on this memo."})
    else:
        step = next((s for s in steps if s.status == StepStatus.ACTIVE), None)
        if step is None:
            raise ValidationError(
                {"step": "This memo has no step awaiting action."})
    if step.status not in (StepStatus.ACTIVE, StepStatus.PENDING):
        raise ValidationError({"step": (
            f"Step {step.sequence} is {step.get_status_display().lower()}. "
            "A step that has already been acted on cannot be reassigned - the "
            "approval given on it would be reassigned with it.")})
    return step


def _transfer_action(role_type):
    """Which timeline event a move of this role produces."""
    if role_type == RoleType.RECOMMENDER:
        return Action.RECOMMENDER_CHANGED
    return Action.APPROVER_CHANGED


@transaction.atomic
def mark_unavailable(memo, actor, reason, reason_note="", step_id=None, request=None):
    """
    Declare that the holder of a live step cannot act.

    The memo is "returned" in the sense the brief means: its step stays exactly
    where it is in the sequence but is flagged as blocked pending a stand-in, and
    the memo owner is notified that it needs one. The status ladder does not move
    backwards, because a memo that reached Supported did reach Supported and
    rewriting that would be a lie about the record.
    """
    memo = _lock(memo)
    if memo.is_read_only or memo.status in Memo.TERMINAL_STATUSES:
        raise ValidationError({"memo": (
            f"This memo is {memo.get_status_display().lower()} and its workflow is "
            "closed.")})
    step = _live_step(memo, step_id)

    # The absent person may declare their own absence; otherwise the caller needs
    # standing. Anything looser would let a colleague park someone else's work.
    if step.assignee_id != getattr(actor, "id", None) and not can_manage_assignments(actor, memo):
        raise PermissionDenied(
            "Only the assignee, the memo author, a supervisor, a department head "
            "or HR/Admin may mark a step unavailable.")
    if reason not in MemoStepUnavailability.Reason.values:
        raise ValidationError({"reason": (
            "Choose one of: "
            + ", ".join(MemoStepUnavailability.Reason.values))})
    if memo.unavailabilities.filter(step=step, resolved_at__isnull=True).exists():
        raise ValidationError({"reason": (
            "This step is already marked unavailable and is waiting for a "
            "replacement.")})

    record = MemoStepUnavailability.objects.create(
        memo=memo, step=step, original_assignee=step.assignee,
        original_assignee_name=step.display_name, role_type=step.role_type,
        reason=reason, reason_note=(reason_note or "").strip(),
        marked_by=actor, marked_by_name=_describe(actor))

    _record_step(memo, actor, Action.COMMENTED, comment=(
        f"{record.original_assignee_name} is unavailable "
        f"({record.get_reason_display()}) at step {step.sequence} "
        f"({step.get_role_type_display()}). Awaiting a replacement."))
    create_audit_log(actor, "update", instance=memo, request=request, metadata={
        "transition": "marked_unavailable", "step": step.sequence,
        "role_type": step.role_type, "reason": reason,
        "absent": record.original_assignee_name, "marked_by": _describe(actor),
    })

    # Tell the person who has to fix it.
    if memo.created_by_id and memo.created_by_id != getattr(actor, "id", None):
        from .workflow import _notify
        _notify(memo.created_by, "MEMO_APPROVER_UNAVAILABLE",
                f"{memo.memo_number} needs a replacement approver",
                f"{record.original_assignee_name} is unavailable "
                f"({record.get_reason_display()}) at step {step.sequence}. "
                "Assign an alternate or acting approver to continue.", memo)
    return record


@transaction.atomic
def assign_replacement(memo, actor, user_id, reason, kind=None, step_id=None,
                       request=None):
    """
    Put a stand-in on a step whose holder is unavailable, and continue.

    The step keeps its sequence, its role and its activation time; only the
    assignee and the snapshots change. So the chain is not reordered, nothing
    behind it is invalidated, and if the step was ACTIVE it stays ACTIVE - which
    is what "Continue" means here: the new holder can act immediately.
    """
    memo = _lock(memo)
    if not can_manage_assignments(actor, memo):
        raise PermissionDenied(
            "Only the memo author, a supervisor, a department head or HR/Admin may "
            "assign a replacement.")
    reason = _require_reason(reason)
    step = _live_step(memo, step_id)
    open_record = memo.unavailabilities.filter(
        step=step, resolved_at__isnull=True).first()
    if open_record is None:
        raise ValidationError({"step": (
            "This step is not marked unavailable. Mark it unavailable first, so "
            "the record says why it changed hands.")})

    kind = kind or MemoAssignmentTransfer.Kind.ALTERNATE
    if kind not in (MemoAssignmentTransfer.Kind.ALTERNATE,
                    MemoAssignmentTransfer.Kind.ACTING):
        raise ValidationError({"kind": "Choose 'alternate' or 'acting'."})

    replacement = _resolve_user(user_id, "user_id")
    _guard_new_assignee(memo, step, replacement)

    previous_name = step.display_name
    previous_user = step.assignee
    snapshot = _snapshot(replacement)
    step.assignee = replacement
    step.assignee_name = snapshot["user_name"]
    step.designation = snapshot["designation"]
    step.department_label = snapshot["department_label"]
    step.save(update_fields=["assignee", "assignee_name", "designation",
                             "department_label"])

    open_record.resolved_at = timezone.now()
    open_record.resolved_by = actor
    open_record.resolved_by_name = _describe(actor)
    open_record.save(update_fields=["resolved_at", "resolved_by", "resolved_by_name"])

    transfer = MemoAssignmentTransfer.objects.create(
        memo=memo, step=step, sequence=step.sequence, role_type=step.role_type,
        kind=kind, from_user=previous_user, from_user_name=previous_name,
        to_user=replacement, to_user_name=snapshot["user_name"], reason=reason,
        actor=actor, actor_name=_describe(actor), unavailability=open_record)

    _record_step(memo, actor, _transfer_action(step.role_type), comment=(
        f"Step {step.sequence} ({step.get_role_type_display()}) reassigned from "
        f"{previous_name} to {snapshot['user_name']} "
        f"({transfer.get_kind_display()}). {reason}"))
    create_audit_log(actor, "update", instance=memo, request=request, metadata={
        "transition": "approver_replaced", "step": step.sequence,
        "role_type": step.role_type, "kind": kind, "from": previous_name,
        "to": snapshot["user_name"], "reason": reason,
        "authorised_by": _describe(actor),
    })

    # If the step is the live one, the stand-in needs telling it is their turn.
    if step.status == StepStatus.ACTIVE:
        from .workflow import _notify
        _notify(replacement,
                MemoWorkflowStep.ASSIGNMENT_CATEGORY.get(
                    step.role_type, "MEMO_APPROVAL_REQUIRED"),
                f"{memo.memo_number} is with you for {step.get_role_type_display()}",
                f"You are standing in for {previous_name}, who is unavailable "
                f"({open_record.get_reason_display()}).", memo)
    return transfer


@transaction.atomic
def reassign_step(memo, actor, user_id, reason, kind, step_id=None, request=None):
    """
    Move a live step to somebody else WITHOUT an unavailability record
    (Phase 49.5 blocker 3): self-assign, reassign, or transfer ownership.

    Distinct from assign_replacement because the reasons differ and an audit that
    conflates them cannot answer "was this person absent, or was the work simply
    moved". Same safety rules: one live step, sequence and role untouched.
    """
    memo = _lock(memo)
    if not can_manage_assignments(actor, memo):
        raise PermissionDenied(
            "Only the memo author, a supervisor, a department head or HR/Admin may "
            "reassign a step.")
    if memo.is_read_only or memo.status in Memo.TERMINAL_STATUSES:
        raise ValidationError({"memo": (
            f"This memo is {memo.get_status_display().lower()} and its workflow is "
            "closed.")})
    if kind not in (MemoAssignmentTransfer.Kind.SELF_ASSIGN,
                    MemoAssignmentTransfer.Kind.ASSIGN_NEW,
                    MemoAssignmentTransfer.Kind.TRANSFER_OWNERSHIP):
        raise ValidationError({"kind": (
            "Choose 'self_assign', 'assign_new' or 'transfer_ownership'.")})
    reason = _require_reason(reason)
    step = _live_step(memo, step_id)

    if kind == MemoAssignmentTransfer.Kind.SELF_ASSIGN:
        replacement = actor
    else:
        replacement = _resolve_user(user_id, "user_id")
    _guard_new_assignee(memo, step, replacement)

    previous_name = step.display_name
    previous_user = step.assignee
    snapshot = _snapshot(replacement)
    step.assignee = replacement
    step.assignee_name = snapshot["user_name"]
    step.designation = snapshot["designation"]
    step.department_label = snapshot["department_label"]
    step.save(update_fields=["assignee", "assignee_name", "designation",
                             "department_label"])

    transfer = MemoAssignmentTransfer.objects.create(
        memo=memo, step=step, sequence=step.sequence, role_type=step.role_type,
        kind=kind, from_user=previous_user, from_user_name=previous_name,
        to_user=replacement, to_user_name=snapshot["user_name"], reason=reason,
        actor=actor, actor_name=_describe(actor))

    _record_step(memo, actor, _transfer_action(step.role_type), comment=(
        f"Step {step.sequence} ({step.get_role_type_display()}) moved from "
        f"{previous_name} to {snapshot['user_name']} "
        f"({transfer.get_kind_display()}). {reason}"))
    create_audit_log(actor, "update", instance=memo, request=request, metadata={
        "transition": "step_reassigned", "step": step.sequence,
        "role_type": step.role_type, "kind": kind, "from": previous_name,
        "to": snapshot["user_name"], "reason": reason,
    })

    if step.status == StepStatus.ACTIVE and replacement.id != getattr(previous_user, "id", None):
        from .workflow import _notify
        _notify(replacement,
                MemoWorkflowStep.ASSIGNMENT_CATEGORY.get(
                    step.role_type, "MEMO_APPROVAL_REQUIRED"),
                f"{memo.memo_number} is with you for {step.get_role_type_display()}",
                f"This step was reassigned to you from {previous_name}. {reason}",
                memo)
    return transfer


def _guard_new_assignee(memo, step, replacement):
    """
    The two rules a replacement must satisfy.

    Both mirror constraints the matrix builder already enforces, so a reassignment
    cannot produce a chain the matrix builder would have refused.
    """
    if memo.created_by_id == replacement.id:
        raise ValidationError({"user_id": (
            "The memo's author cannot hold a step in its own approval chain.")})
    clash = memo.workflow_steps.filter(assignee=replacement).exclude(pk=step.pk)
    if clash.exists():
        raise ValidationError({"user_id": (
            f"{_describe(replacement)} already holds step "
            f"{clash.first().sequence} of this memo. One person cannot occupy two "
            "control points in the same chain.")})


def assignment_history(memo):
    """Every absence and every hand-over, for the detail payload and the PDF."""
    return {
        "unavailabilities": [{
            "id": str(u.id), "step": u.step_id and u.step.sequence,
            "role_type": u.role_type, "absent": u.original_assignee_name,
            "reason": u.reason, "reason_label": u.get_reason_display(),
            "reason_note": u.reason_note, "marked_by": u.marked_by_name,
            "marked_at": u.marked_at, "resolved_at": u.resolved_at,
            "resolved_by": u.resolved_by_name, "is_open": u.is_open,
        } for u in memo.unavailabilities.select_related("step").all()],
        "transfers": [{
            "id": str(t.id), "step": t.sequence, "role_type": t.role_type,
            # The human label too, so neither the PDF nor the UI has to map a raw
            # role code - the first generated PDF printed "reviewer" in a column
            # headed Role, next to properly-cased values everywhere else.
            "role_label": (MemoWorkflowStep.RoleType(t.role_type).label
                           if t.role_type in MemoWorkflowStep.RoleType.values
                           else t.role_type),
            "kind": t.kind, "kind_label": t.get_kind_display(),
            "from": t.from_user_name, "to": t.to_user_name,
            "reason": t.reason, "actor": t.actor_name, "at": t.at,
            "caused_by_absence": t.unavailability_id is not None,
        } for t in memo.assignment_transfers.all()],
    }


# ===========================================================================
# Blocker 4 - archived access sharing
# ===========================================================================
def can_share_archive(user, memo):
    """
    Who may widen an archived memo's readership.

    Deliberately narrow: the author, HR and Admin. A department head can read
    their department's archive but cannot hand it to another department, because
    that is a disclosure decision rather than a reading one.
    """
    if memo.status != Memo.Status.ARCHIVED:
        return False
    return _is_admin(user) or _is_hr(user) or memo.created_by_id == user.id


@transaction.atomic
def grant_archive_access(memo, actor, target, reason, department_id=None,
                         user_id=None, group_id=None, include_children=True,
                         request=None):
    """
    Grant read access to an archived memo.

    Only archived memos: sharing a memo still in flight would add a reader to a
    document whose content can still change, and the approval chain is the right
    mechanism for that.
    """
    memo = _lock(memo)
    if memo.status != Memo.Status.ARCHIVED:
        raise ValidationError({"memo": (
            "Only an archived memo's access can be shared. A memo still in its "
            "workflow is shared by routing it.")})
    if not can_share_archive(actor, memo):
        raise PermissionDenied(
            "Only the memo author, HR or an administrator may share an archived "
            "memo.")
    reason = _require_reason(reason)

    fields = {"memo": memo, "target": target, "reason": reason,
              "granted_by": actor, "granted_by_name": _describe(actor),
              "include_children": False}

    if target == MemoArchiveAccess.Target.DEPARTMENT:
        from leaves.models import Department
        department = Department.objects.filter(pk=department_id).first()
        if department is None:
            raise ValidationError({"department_id": "No such department."})
        fields.update(department=department, include_children=bool(include_children),
                      target_label=department.name)
    elif target == MemoArchiveAccess.Target.EMPLOYEE:
        user = _resolve_user(user_id, "user_id")
        fields.update(user=user, target_label=_describe(user))
    elif target == MemoArchiveAccess.Target.GROUP:
        from django.contrib.auth.models import Group
        group = Group.objects.filter(pk=group_id).first()
        if group is None:
            raise ValidationError({"group_id": "No such employee group."})
        fields.update(group=group, target_label=group.name)
    else:
        raise ValidationError({"target": (
            "Choose 'department', 'employee' or 'group'. Units and sub-units are "
            "departments - the department tree is one hierarchy, so a unit is "
            "shared as the department row it is.")})

    duplicate = MemoArchiveAccess.objects.filter(
        memo=memo, target=target, revoked_at__isnull=True,
        department=fields.get("department"), user=fields.get("user"),
        group=fields.get("group")).first()
    if duplicate is not None:
        raise ValidationError({"target": (
            f"{duplicate.target_label} already has access, granted by "
            f"{duplicate.granted_by_name}.")})

    grant = MemoArchiveAccess.objects.create(**fields)
    _record_step(memo, actor, Action.ACCESS_GRANTED, comment=(
        f"Archive access granted to {grant.target_label} "
        f"({grant.get_target_display()}). {reason}"))
    create_audit_log(actor, "update", instance=memo, request=request, metadata={
        "transition": "archive_access_granted", "target": target,
        "target_label": grant.target_label, "reason": reason,
        "include_children": grant.include_children,
    })
    return grant


@transaction.atomic
def revoke_archive_access(memo, actor, grant_id, reason="", request=None):
    """
    Withdraw a grant.

    Revoked, never deleted: "who could read this in March" has to stay answerable
    in June, which is the only reason an auditable share is worth more than an
    email.
    """
    memo = _lock(memo)
    if not can_share_archive(actor, memo):
        raise PermissionDenied("You cannot change this memo's sharing.")
    grant = memo.archive_grants.filter(pk=grant_id, revoked_at__isnull=True).first()
    if grant is None:
        raise ValidationError({"grant": "No such live grant on this memo."})

    grant.revoked_at = timezone.now()
    grant.revoked_by = actor
    grant.revoked_by_name = _describe(actor)
    grant.revoke_reason = (reason or "").strip()
    grant.save(update_fields=["revoked_at", "revoked_by", "revoked_by_name",
                              "revoke_reason"])
    _record_step(memo, actor, Action.ACCESS_REVOKED, comment=(
        f"Archive access for {grant.target_label} withdrawn. {grant.revoke_reason}"))
    create_audit_log(actor, "update", instance=memo, request=request, metadata={
        "transition": "archive_access_revoked", "target_label": grant.target_label,
        "reason": grant.revoke_reason,
    })
    return grant


def archive_sharing_history(memo):
    """Every grant ever made, live and revoked, for the payload and the PDF."""
    return [{
        "id": str(g.id),
        "target": g.target,
        "target_label_kind": g.get_target_display(),
        "granted_to": g.target_label,
        "include_children": g.include_children,
        "reason": g.reason,
        "granted_by": g.granted_by_name,
        "granted_at": g.granted_at,
        "is_live": g.is_live,
        "revoked_at": g.revoked_at,
        "revoked_by": g.revoked_by_name,
        "revoke_reason": g.revoke_reason,
    } for g in memo.archive_grants.all()]


# ---------------------------------------------------------------------------
# The permission hook - this is what makes grants take effect
# ---------------------------------------------------------------------------
# How many levels of department nesting an `include_children` grant reaches.
#
# Bounded and expressed in the ORM rather than walked in Python, for two reasons.
# First correctness: a Python walk had to issue queries while BUILDING the filter,
# and a query issued during filter construction runs on every list request and
# varies with the data - which an existing query-count test caught immediately
# (9 queries for six rows, 7 for twelve). Second cost: this Q is OR-ed into the
# visibility filter of every memo list endpoint, so it must add no queries at all.
#
# Four levels is deliberate slack over the two or three the organisation actually
# uses (Department > Unit > Sub-unit). A grant deeper than this simply does not
# cascade, which fails closed.
_DEPARTMENT_DEPTH = 4


def _department_grant_paths(field_prefix, department_id):
    """
    Q matching department grants that cover `department_id`, directly or as an
    ancestor with include_children set.

    Built by walking DOWN the `children` relation from the granted department, so
    the traversal happens inside one SQL statement instead of one query per level.
    """
    direct = Q(**{f"{field_prefix}department_id": department_id})
    path = f"{field_prefix}department__children"
    for _ in range(_DEPARTMENT_DEPTH):
        direct |= Q(**{f"{field_prefix}include_children": True,
                       f"{path}__id": department_id})
        path += "__children"
    return direct


def granted_memo_filter(user):
    """
    Q object matching archived memos this user can read through a LIVE grant.

    Evaluated on every request rather than materialised anywhere, so revoking a
    grant takes effect on the next read - which is what the brief means by
    "permissions must re-evaluate dynamically". There is no cached ACL to
    invalidate because there is no cached ACL.

    Issues NO queries: every clause is a join expressed in the Q, including group
    membership (`group__user`) and department headship (`department__head`), both of
    which a naive implementation would have looked up first.
    """
    live = Q(archive_grants__revoked_at__isnull=True)
    target = Q(archive_grants__target=MemoArchiveAccess.Target.EMPLOYEE,
               archive_grants__user=user)
    target |= Q(archive_grants__target=MemoArchiveAccess.Target.GROUP,
                archive_grants__group__user=user)

    # Departments the user belongs to: their own, and any they head.
    department = Q(archive_grants__department__head=user)
    own_id = getattr(user, "department_ref_id", None)
    if own_id:
        department |= _department_grant_paths("archive_grants__", own_id)
    target |= Q(archive_grants__target=MemoArchiveAccess.Target.DEPARTMENT) & department

    return live & target & Q(status=Memo.Status.ARCHIVED)


def has_grant(user, memo):
    """
    Object-level counterpart to granted_memo_filter.

    Two implementations of one rule, deliberately - the same pattern the module
    already uses for visibility: the queryset keeps lists cheap, the object check
    closes IDOR on detail endpoints. They must agree, and
    test_the_queryset_filter_and_the_object_permission_agree_about_grants asserts
    it at every state including after a revocation.

    Shares _department_grant_paths with the filter above so the two cannot drift on
    the subtle half of the rule.
    """
    if memo.status != Memo.Status.ARCHIVED:
        return False
    grants = memo.archive_grants.filter(revoked_at__isnull=True)
    matched = (
        Q(target=MemoArchiveAccess.Target.EMPLOYEE, user=user)
        | Q(target=MemoArchiveAccess.Target.GROUP, group__user=user)
        | Q(target=MemoArchiveAccess.Target.DEPARTMENT, department__head=user)
    )
    own_id = getattr(user, "department_ref_id", None)
    if own_id:
        matched |= (Q(target=MemoArchiveAccess.Target.DEPARTMENT)
                    & _department_grant_paths("", own_id))
    return grants.filter(matched).exists()
