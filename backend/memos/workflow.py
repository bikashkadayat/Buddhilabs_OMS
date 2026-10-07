"""
The enterprise memo workflow engine (Phases 2, 4, 5, 6, 7).

    Draft -> Draft For Review -> Under Review -> Recommended -> Supported
          -> Approved -> Archived        (Rejected reachable from any stage)

THE ONLY WORKFLOW ENGINE
------------------------
The original two-slot flow (Memo.current_reviewer -> Memo.current_approver) has
been removed. It could express exactly two approvers and could not represent
"waiting on the supporter at position 3 of 5", so extending it was never an
option; this module replaced it. Migration 0010 converted every memo it had
routed into MemoWorkflowStep rows and 0011 dropped its two columns, so routing now
has a single representation and there is no second path to keep in step.
services.py retains only what is not stage-specific: memo numbering, the audit
helper, and the history-row writer.

DESIGN RULES (inherited from the original engine - they were correct)
--------------------------------------------------------------------
  * Every mutating transition is one @transaction.atomic block.
  * The memo row is re-read under select_for_update() so two actors racing on
    the same memo cannot both advance it.
  * A guard failure raises DRF ValidationError -> HTTP 400 with a clear message.
  * State is never mutated without also writing a MemoApprovalStep (history)
    and an AuditLog row. The two together are the audit trail Phase 5 requires.
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.models import AuditLog
from common import certification
from .models import Memo, MemoApprovalStep, MemoWorkflowStep
from .services import MIN_COMMENT_LENGTH, _record_step, create_audit_log
from tenancy.stamping import stamp_all

logger = logging.getLogger("memos")
User = get_user_model()

RoleType = MemoWorkflowStep.RoleType

# Role -> the shared audit log's verb for completing that role's step.
# APPROVER maps to the long-standing APPROVE value so historical rows and any
# saved report filter keep matching; the other three gain verbs of their own.
_AUDIT_ACTION_FOR_ROLE = {
    RoleType.REVIEWER: AuditLog.Action.REVIEWED,
    RoleType.RECOMMENDER: AuditLog.Action.RECOMMENDED,
    RoleType.SUPPORTER: AuditLog.Action.SUPPORTED,
    RoleType.APPROVER: AuditLog.Action.APPROVE,
}
StepStatus = MemoWorkflowStep.StepStatus

# Statuses from which a memo's matrix may still be edited.
MATRIX_EDITABLE_STATUSES = frozenset({Memo.Status.DRAFT, Memo.Status.REJECTED})

MAX_MATRIX_STEPS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lock(memo):
    return Memo.objects.select_for_update().get(pk=memo.pk)


def _notify(recipient, category, title, body, memo):
    """
    Best-effort notification. A delivery failure must never roll back a workflow
    transition, but it is logged at ERROR with the memo number so it is
    alertable rather than silent.
    """
    if recipient is None:
        return
    try:
        from notifications.dispatcher import notify
        notify(recipient, category, title, body, action_url=f"/memos/{memo.id}")
    except Exception:  # noqa: BLE001 - notifications must not fail the workflow
        logger.error(
            "memo notification failed memo=%s category=%s recipient=%s",
            memo.memo_number, category, getattr(recipient, "id", None), exc_info=True,
        )


def _is_admin(actor):
    return getattr(actor, "role", None) == User.Roles.ADMIN


def _describe(user):
    if user is None:
        return "—"
    return user.get_full_name() or user.username


def record_creation(memo, actor, request=None):
    """
    Write the "Created" history row.

    Without it the timeline opened at "Sent for Review" and the memo appeared to
    have no author event at all - but Phase 7's own example starts at
    "08:30 Created by Employee", and an audit trail that omits creation is
    missing the one event every other event depends on. Idempotent, so calling it
    twice on the same memo cannot double the row.
    """
    if memo.approval_steps.filter(action=MemoApprovalStep.Action.CREATED).exists():
        return None
    return _record_step(memo, actor, MemoApprovalStep.Action.CREATED)


# ---------------------------------------------------------------------------
# Phase 4 - building the approval matrix
# ---------------------------------------------------------------------------
def _validate_matrix_rows(rows, memo):
    """
    Validate a proposed matrix and return it normalised into
    [{assignee, role_type}] ordered by the caller's sequence.

    Phase 4 is explicit that any employee may be picked for any role, so there
    is deliberately NO role/designation restriction on who can be a reviewer,
    recommender, supporter or approver. What IS enforced is the structural
    integrity the workflow depends on.
    """
    if not rows:
        raise ValidationError(
            {"workflow": "Add at least one approver to the workflow before sending for review."}
        )
    if len(rows) > MAX_MATRIX_STEPS:
        raise ValidationError(
            {"workflow": f"A workflow may not exceed {MAX_MATRIX_STEPS} steps."}
        )

    seen_ids = set()
    resolved = []
    for index, row in enumerate(rows, start=1):
        assignee_id = row.get("assignee_id") or row.get("assignee")
        role_type = row.get("role_type")

        if role_type not in RoleType.values:
            raise ValidationError(
                {"workflow": f"Step {index}: '{role_type}' is not a valid role type."}
            )

        user = User.objects.filter(pk=assignee_id).first() if assignee_id else None
        if user is None:
            raise ValidationError({"workflow": f"Step {index}: the selected employee does not exist."})
        if not user.is_active:
            raise ValidationError(
                {"workflow": f"Step {index}: {_describe(user)} is not an active employee."}
            )
        if user.id == memo.created_by_id:
            raise ValidationError(
                {"workflow": f"Step {index}: you cannot place yourself in your own approval workflow."}
            )
        if user.id in seen_ids:
            raise ValidationError(
                {"workflow": f"{_describe(user)} appears more than once. "
                             "One person cannot hold two positions in the same workflow."}
            )
        seen_ids.add(user.id)
        resolved.append({"assignee": user, "role_type": role_type})

    # The chain must be able to reach Approved, and nothing may follow the final
    # approval - otherwise the memo would sit "approved" with steps outstanding.
    if resolved[-1]["role_type"] != RoleType.APPROVER:
        raise ValidationError(
            {"workflow": "The final step must be an Approver - that is the step that approves the memo."}
        )
    for position, row in enumerate(resolved[:-1], start=1):
        if row["role_type"] == RoleType.APPROVER:
            raise ValidationError(
                {"workflow": f"Step {position} is an Approver but is not the last step. "
                             "Approval must come last in the chain."}
            )

    # Phase 15: the hierarchy is Recommended -> Supported -> Approved, so a
    # chain's role ranks must never decrease. Enforced on the MATRIX rather than
    # re-checked at each transition: if the chain cannot express an out-of-order
    # approval, the engine cannot perform one, and there is no second rule to keep
    # in step. Equal ranks are fine - two supporters in a row is a real chain, and
    # the sequence still makes them act one after the other.
    ranks = MemoWorkflowStep.ROLE_RANK
    for position in range(1, len(resolved)):
        previous, current = resolved[position - 1], resolved[position]
        if ranks[current["role_type"]] < ranks[previous["role_type"]]:
            raise ValidationError({"workflow": (
                f"Step {position + 1} is a "
                f"{MemoWorkflowStep.RoleType(current['role_type']).label} but follows a "
                f"{MemoWorkflowStep.RoleType(previous['role_type']).label}. "
                "The order must be Reviewer, then Recommender, then Supporter, "
                "then Approver - a recommendation cannot come after a support."
            )})

    return resolved


@transaction.atomic
def set_matrix(memo, actor, rows, request=None):
    """
    Replace a memo's approval matrix. Allowed only while the memo is a draft (or
    was rejected and is being revised) - once the chain is live, reordering it
    would invalidate approvals already given.
    """
    memo = _lock(memo)
    if memo.created_by_id != actor.id and not _is_admin(actor):
        raise ValidationError("Only the memo author can set its approval workflow.")
    if memo.status not in MATRIX_EDITABLE_STATUSES:
        raise ValidationError(
            "The approval workflow can only be changed while the memo is a draft. "
            f"This memo is {memo.get_status_display()}."
        )

    resolved = _validate_matrix_rows(rows, memo)

    memo.workflow_steps.all().delete()
    steps = [
        MemoWorkflowStep(
            memo=memo,
            sequence=index,
            assignee=row["assignee"],
            role_type=row["role_type"],
            status=StepStatus.PENDING,
            # Snapshots, so the matrix keeps reading correctly after a promotion,
            # a transfer, or the account being deleted altogether.
            assignee_name=_describe(row["assignee"]),
            designation=getattr(row["assignee"], "designation", "") or "",
            department_label=getattr(row["assignee"], "department_name", "") or "",
        )
        for index, row in enumerate(resolved, start=1)
    ]
    MemoWorkflowStep.objects.bulk_create(stamp_all(steps))

    create_audit_log(
        actor, AuditLog.Action.UPDATE, instance=memo,
        metadata={
            "transition": "workflow_matrix_set",
            "steps": [
                {"sequence": s.sequence, "assignee": str(s.assignee_id), "role_type": s.role_type}
                for s in steps
            ],
        },
        request=request,
    )
    return memo


# ---------------------------------------------------------------------------
# Phase 2 / 5 - transitions
# ---------------------------------------------------------------------------
def _activate(memo, step):
    """
    Make `step` the active one and notify its assignee.

    `activated_at` is stamped here rather than derived later: the inbox measures
    how long a memo has been waiting on a person from this moment, and there is
    no other record of when a step reached them.
    """
    step.status = StepStatus.ACTIVE
    step.activated_at = timezone.now()
    step.save(update_fields=["status", "activated_at"])
    _notify(
        step.assignee,
        MemoWorkflowStep.ASSIGNMENT_CATEGORY[step.role_type],
        f"Memo {memo.memo_number} needs your {step.get_role_type_display().lower()} action",
        memo.subject,
        memo,
    )


@transaction.atomic
def send_for_review(memo, actor, rows=None, remarks="", request=None):
    """
    Draft -> Draft For Review. Activates step 1 of the matrix.

    `rows`, when supplied, replaces the matrix in the same transaction so the
    UI can build the chain and submit in one call - and so a rejected matrix
    leaves no half-submitted memo behind.
    """
    memo = _lock(memo)
    if memo.created_by_id != actor.id and not _is_admin(actor):
        raise ValidationError("Only the memo author can send this memo for review.")
    if memo.status not in MATRIX_EDITABLE_STATUSES:
        raise ValidationError(
            f"Only draft memos can be sent for review. This memo is {memo.get_status_display()}."
        )

    if rows is not None:
        set_matrix(memo, actor, rows, request=request)

    steps = list(memo.workflow_steps.order_by("sequence").select_related("assignee"))
    if not steps:
        raise ValidationError(
            {"workflow": "Add at least one approver to the workflow before sending for review."}
        )

    # A resubmission after rejection reuses the same chain from the top.
    memo.workflow_steps.update(
        status=StepStatus.PENDING, acted_at=None, activated_at=None, remarks="",
    )
    steps = list(memo.workflow_steps.order_by("sequence").select_related("assignee"))

    memo.status = Memo.Status.DRAFT_FOR_REVIEW
    memo.submitted_at = timezone.now()
    memo.finalized_at = None
    _activate(memo, steps[0])
    memo.save(update_fields=["status", "submitted_at", "finalized_at", "updated_at"])

    _record_step(memo, actor, MemoApprovalStep.Action.SENT_FOR_REVIEW, comment=remarks)
    create_audit_log(
        actor, AuditLog.Action.SUBMIT, instance=memo,
        metadata={"transition": "sent_for_review", "first_assignee": str(steps[0].assignee_id)},
        request=request,
    )
    _notify(
        memo.created_by, "MEMO_SUBMITTED",
        f"Memo {memo.memo_number} was sent for review",
        f"Now with {steps[0].display_name} for {steps[0].get_role_type_display().lower()} action.",
        memo,
    )
    return memo


def _resolve_actor_step(memo, actor):
    """
    Return the step `actor` may act on, or raise with a message that explains
    why they cannot. This is where Phase 5's ordering rules are enforced.
    """
    steps = list(memo.workflow_steps.order_by("sequence").select_related("assignee"))
    if not steps:
        raise ValidationError("This memo has no approval workflow.")

    active = next((s for s in steps if s.status == StepStatus.ACTIVE), None)
    if active is None:
        raise ValidationError(
            f"This memo is not awaiting any action (it is {memo.get_status_display()})."
        )

    if active.assignee_id == actor.id:
        return active, False

    # An admin may act on behalf of the pending assignee; it is recorded as an
    # override in the audit trail rather than passing silently.
    if _is_admin(actor):
        return active, True

    own = next((s for s in steps if s.assignee_id == actor.id), None)
    if own is None:
        raise ValidationError("You are not part of this memo's approval workflow.")
    if own.status == StepStatus.COMPLETED:
        raise ValidationError("You have already completed your step on this memo.")
    if own.status == StepStatus.PENDING:
        raise ValidationError(
            f"Step {active.sequence} ({active.display_name} - "
            f"{active.get_role_type_display()}) must be completed before your "
            f"step {own.sequence} can be actioned."
        )
    raise ValidationError("You cannot act on this memo at its current stage.")


def _archive(memo, actor, request=None):
    """Phase 6: an approved memo is archived immediately and becomes read-only."""
    memo.status = Memo.Status.ARCHIVED
    memo.archived_at = timezone.now()
    memo.save(update_fields=["status", "archived_at", "updated_at"])

    _record_step(memo, actor, MemoApprovalStep.Action.ARCHIVED)
    create_audit_log(
        # ARCHIVED, not OTHER. Filing a memo to the read-only archive is the
        # single most consequential row in its history — it is the moment the
        # record becomes immutable — and it was indistinguishable from every
        # other uncategorised event in the log.
        actor, AuditLog.Action.ARCHIVED, instance=memo,
        metadata={"transition": "archived", "auto": True}, request=request,
    )
    _notify(
        memo.created_by, "MEMO_ARCHIVED",
        f"Memo {memo.memo_number} was archived",
        "The memo is approved and has been filed to the archive (read-only).",
        memo,
    )
    return memo


@transaction.atomic
def act_on_step(memo, actor, decision, remarks="", request=None):
    """
    Complete or reject the active step of a memo's approval matrix.

    `decision` is "proceed" (review / recommend / support / approve, whichever
    the active step's role type calls for) or "reject".
    """
    if decision not in ("proceed", "reject"):
        raise ValidationError({"decision": "Must be 'proceed' or 'reject'."})

    memo = _lock(memo)
    if memo.is_read_only:
        raise ValidationError(
            f"This memo is {memo.get_status_display().lower()} and can no longer be actioned."
        )

    step, admin_override = _resolve_actor_step(memo, actor)
    remarks = (remarks or "").strip()

    if decision == "reject":
        if len(remarks) < MIN_COMMENT_LENGTH:
            raise ValidationError(
                {"remarks": f"A reason of at least {MIN_COMMENT_LENGTH} characters is "
                            "required when rejecting, so the author knows what to change."}
            )
        return _reject(memo, actor, step, remarks, admin_override, request=request)

    if step.requires_comment and len(remarks) < MIN_COMMENT_LENGTH:
        raise ValidationError(
            {"remarks": f"A comment of at least {MIN_COMMENT_LENGTH} characters is required "
                        f"for the {step.get_role_type_display()} step."}
        )

    now = timezone.now()
    step.status = StepStatus.COMPLETED
    step.acted_at = now
    step.remarks = remarks
    step.save(update_fields=["status", "acted_at", "remarks"])

    memo.status = MemoWorkflowStep.COMPLETION_STATUS[step.role_type]
    _record_step(
        memo, actor,
        MemoWorkflowStep.COMPLETION_ACTION[step.role_type],
        comment=remarks,
    )

    next_step = memo.workflow_steps.filter(
        status=StepStatus.PENDING, sequence__gt=step.sequence,
    ).order_by("sequence").select_related("assignee").first()

    if next_step is not None:
        _activate(memo, next_step)
        memo.save(update_fields=["status", "updated_at"])
    else:
        # Final step done: approve, then archive in the same transaction.
        memo.approved_at = now
        memo.finalized_at = now
        memo.save(update_fields=["status", "approved_at", "finalized_at", "updated_at"])
        _notify(
            memo.created_by, "MEMO_APPROVED",
            f"Memo {memo.memo_number} was approved",
            f"Approved by {step.display_name}.", memo,
        )

    create_audit_log(
        actor,
        # The role's own verb, not UPDATE. COMPLETION_ACTION already holds
        # "reviewed"/"recommended"/"supported"/"approved" and it was only being
        # written into metadata; the indexed `action` column — the one a
        # compliance query filters on — now carries it too. APPROVER keeps
        # mapping to the pre-existing APPROVE value rather than a new
        # "approved", so every historical approve row still matches.
        _AUDIT_ACTION_FOR_ROLE.get(step.role_type, AuditLog.Action.UPDATE),
        instance=memo,
        metadata={
            "transition": MemoWorkflowStep.COMPLETION_ACTION[step.role_type],
            "sequence": step.sequence,
            "role_type": step.role_type,
            "assignee": str(step.assignee_id),
            # Remarks belong in the audit row as well as the history row: the
            # audit log is the one place that also carries the actor's IP, and a
            # decision without its stated reason is half a record.
            "remarks": remarks,
            "admin_override": admin_override,
        },
        request=request,
    )

    if next_step is None:
        _archive(memo, actor, request=request)
    return memo


def _reject(memo, actor, step, remarks, admin_override, request=None):
    """
    Reject at `step`. Phase 2: the memo goes back to the author with comments -
    it is marked Rejected (so it is visible as such and reportable), the chain is
    stood down, and the author regains edit rights plus the ability to resubmit
    the same chain from the top.
    """
    now = timezone.now()
    step.status = StepStatus.REJECTED
    step.acted_at = now
    step.remarks = remarks
    step.save(update_fields=["status", "acted_at", "remarks"])

    # Steps after the rejection never ran; mark them skipped rather than leaving
    # them "pending" forever, so the matrix table reads truthfully.
    memo.workflow_steps.filter(
        status=StepStatus.PENDING, sequence__gt=step.sequence,
    ).update(status=StepStatus.SKIPPED)

    memo.status = Memo.Status.REJECTED
    memo.finalized_at = now
    memo.save(update_fields=["status", "finalized_at", "updated_at"])

    _record_step(memo, actor, MemoApprovalStep.Action.REJECTED, comment=remarks)
    create_audit_log(
        actor, AuditLog.Action.REJECT, instance=memo,
        metadata={
            "transition": "rejected", "sequence": step.sequence,
            "role_type": step.role_type, "assignee": str(step.assignee_id),
            "remarks": remarks, "comment": remarks,
            "admin_override": admin_override,
        },
        request=request,
    )
    _notify(
        memo.created_by, "MEMO_REJECTED",
        f"Memo {memo.memo_number} was returned as rejected",
        remarks, memo,
    )
    return memo


@transaction.atomic
def withdraw_memo(memo, actor, remarks="", request=None):
    """
    The author withdraws their own memo (status Cancelled).

    This is the one transition outside the approval ladder, and it is kept
    because it is the only way an author can stop a memo they have already sent
    without asking an approver to reject it. Any outstanding steps are stood down
    so the matrix reads truthfully rather than showing someone as still pending.
    """
    memo = _lock(memo)
    if memo.created_by_id != actor.id and not _is_admin(actor):
        raise ValidationError("Only the author can withdraw this memo.")
    # TERMINAL_STATUSES covers approved, archived and cancelled - all three are
    # past the point where withdrawing means anything.
    if memo.status in Memo.TERMINAL_STATUSES:
        raise ValidationError(
            f"This memo is {memo.get_status_display().lower()} and can no longer be withdrawn."
        )

    memo.workflow_steps.filter(
        status__in=[StepStatus.ACTIVE, StepStatus.PENDING],
    ).update(status=StepStatus.SKIPPED)

    memo.status = Memo.Status.CANCELLED
    memo.finalized_at = timezone.now()
    memo.save(update_fields=["status", "finalized_at", "updated_at"])

    _record_step(memo, actor, MemoApprovalStep.Action.CANCELLED, comment=remarks)
    create_audit_log(
        actor, AuditLog.Action.UPDATE, instance=memo,
        metadata={"transition": "cancelled", "remarks": remarks}, request=request,
    )
    return memo


@transaction.atomic
def archive_memo(memo, actor, request=None):
    """
    Manual archive for an approved memo that predates auto-archiving (or whose
    auto-archive was interrupted). Admin/HR only; idempotent.
    """
    memo = _lock(memo)
    if memo.status == Memo.Status.ARCHIVED:
        return memo
    if memo.status != Memo.Status.APPROVED:
        raise ValidationError("Only approved memos can be archived.")
    if not (_is_admin(actor) or getattr(actor, "role", None) == User.Roles.APPROVER):
        raise ValidationError("Only HR or an administrator can archive a memo.")
    return _archive(memo, actor, request=request)


# ---------------------------------------------------------------------------
# Phase 7 - timeline
# ---------------------------------------------------------------------------
def build_timeline(memo):
    """
    Merged activity timeline: every action already taken, followed by the steps
    still outstanding.

    Past entries come from MemoApprovalStep (immutable history). Future entries
    come from MemoWorkflowStep rows that are still active/pending - which is
    exactly what the previous design could not show, because nothing existed in
    the database until after someone acted.
    """
    entries = []

    for record in memo.approval_steps.select_related("actor").order_by("step_order"):
        entries.append({
            "kind": "history",
            "id": str(record.id),
            "sequence": record.step_order,
            "action": record.action,
            "label": record.get_action_display(),
            "actor_name": _describe(record.actor),
            "actor_id": str(record.actor_id) if record.actor_id else None,
            "designation": getattr(record.actor, "designation", "") or "",
            "at": record.acted_at,
            "remarks": record.comment,
            "state": "done",
        })

    pending = memo.workflow_steps.filter(
        status__in=[StepStatus.ACTIVE, StepStatus.PENDING],
    ).order_by("sequence").select_related("assignee")
    for step in pending:
        entries.append({
            "kind": "upcoming",
            "id": str(step.id),
            "sequence": step.sequence,
            "action": step.role_type,
            "label": (
                f"Awaiting {step.get_role_type_display()}"
                if step.status == StepStatus.ACTIVE
                else f"{step.get_role_type_display()} (queued)"
            ),
            "actor_name": step.display_name,
            "actor_id": str(step.assignee_id) if step.assignee_id else None,
            "designation": step.designation,
            "at": None,
            "remarks": "",
            "state": "active" if step.status == StepStatus.ACTIVE else "pending",
        })

    return entries


# ---------------------------------------------------------------------------
# Phase 8 - PDF approval matrix rows
# ---------------------------------------------------------------------------
def pdf_matrix(memo):
    """
    Approval matrix rows for the PDF, in workflow order. Every memo has a matrix
    (migration 0010 converted the legacy-routed ones), so there is no fallback
    shape to maintain here any more.
    """
    steps = memo.workflow_steps.order_by("sequence").select_related("assignee")
    return [{
        "sequence": step.sequence,
        "role_type": step.role_type,
        "role_label": step.get_role_type_display(),
        "name": step.display_name,
        "designation": step.designation or "—",
        "department": step.department_label or "—",
        "status_label": step.get_status_display(),
        "acted_at": step.acted_at,
        "remarks": step.remarks,
        "signed": step.status == StepStatus.COMPLETED,
        "rejected": step.status == StepStatus.REJECTED,
    } for step in steps]


# Phase 31: the stamp vocabulary, the row grouping and the verification-ID scheme
# moved to common.certification so minutes could print the same approval section
# without a second copy. Re-exported under their original names because this
# module's callers and tests refer to them here.
STAMP_WORDS = certification.STAMP_WORDS
_UNSIGNED_STAMPS = certification.UNSIGNED_STAMPS
MAX_BLOCKS_PER_ROW = certification.MAX_BLOCKS_PER_ROW
DEPARTMENT_FITS_UP_TO = certification.DEPARTMENT_FITS_UP_TO
signature_rows = certification.signature_rows


def verification_id(memo, step=None):
    """
    The Digital Verification ID for one memo signature. The hashing and formatting
    live in common.certification; this only decides WHICH identifiers stand for a
    memo signature.
    """
    if step is None:
        return certification.verification_id(
            memo.pk, "author", memo.created_by_id,
            certification.stamp_iso(memo.created_at))
    return certification.verification_id(
        memo.pk, step.pk, step.assignee_id,
        certification.stamp_iso(step.acted_at))


def signature_blocks(memo):
    """
    The approval certification blocks, in hierarchy order:

        Created By -> Recommended By -> Supported By -> Approved By

    Built here, once, and used by BOTH the detail page and the PDF. The two
    rendered the approval section from different data before, which is how the PDF
    ended up with four role panels and no "Created By" card at all while the spec
    asked for five.

    "Created By" is synthesised from the memo rather than read off the matrix: the
    author is never a step in their own chain, but they are the first block on the
    document and their card is always complete by definition.

    Phase 26 added `stamp` (the certification word for the block's stamp band) and
    `verification_id`. `initials` is still returned even though neither renderer
    draws an avatar any more - dropping it would be a breaking payload change for
    no gain.
    """
    author = memo.created_by
    blocks = [{
        "key": "created",
        "heading": "Created By",
        "name": _describe(author),
        "designation": getattr(author, "designation", "") or "—",
        "department": memo.resolved_department_name() or "—",
        "at": memo.created_at,
        "status_label": "Created",
        "stamp": STAMP_WORDS["created"],
        "state": "done",
        "remarks": "",
        "initials": _initials(_describe(author)),
        "verified": True,
        "verification_id": verification_id(memo),
    }]

    headings = [
        (RoleType.REVIEWER, "Reviewed By"),
        (RoleType.RECOMMENDER, "Recommended By"),
        (RoleType.SUPPORTER, "Supported By"),
        (RoleType.APPROVER, "Approved By"),
    ]
    steps = list(memo.workflow_steps.order_by("sequence").select_related("assignee"))

    for role_type, heading in headings:
        for step in (s for s in steps if s.role_type == role_type):
            if step.status == StepStatus.COMPLETED:
                state, status_label = "done", "Approved"
            elif step.status == StepStatus.REJECTED:
                state, status_label = "rejected", "Rejected"
            elif step.status == StepStatus.ACTIVE:
                state, status_label = "active", "Awaiting Action"
            elif step.status == StepStatus.SKIPPED:
                state, status_label = "skipped", "Not Required"
            else:
                state, status_label = "pending", "Pending"

            signed = step.status == StepStatus.COMPLETED
            blocks.append({
                "key": role_type,
                "heading": heading,
                "name": step.display_name,
                "designation": step.designation or "—",
                "department": step.department_label or "—",
                "at": step.acted_at,
                "status_label": status_label,
                "stamp": (STAMP_WORDS.get(role_type, status_label) if signed
                          else _UNSIGNED_STAMPS.get(state, status_label)),
                "state": state,
                "remarks": step.remarks,
                "initials": _initials(step.display_name),
                # The verification badge means "this person actually signed off",
                # so it is only true for a completed step - never for one that is
                # merely queued.
                "verified": signed,
                # An unsigned step has nothing to verify, so it carries no ID
                # rather than one that would later change when it is signed.
                "verification_id": verification_id(memo, step) if signed else "",
            })

    return blocks


def approval_certificate(memo):
    """
    The "this document is approved, by whom, when" summary (Phase 26).

    Returns None for anything not fully approved, so a caller can render the seal
    on presence alone and there is no way to print an APPROVED banner over a memo
    that is still in flight.

    The signer is read off the completed Approver step rather than from a column on
    the memo: `approved_at` records WHEN a memo was approved but the model has
    never stored WHO did it, and deriving it keeps one source of truth (the step
    that actually recorded the decision) instead of a denormalised copy that could
    disagree with the matrix printed just above it.
    """
    if memo.status not in (Memo.Status.APPROVED, Memo.Status.ARCHIVED):
        return None

    # Python-side selection over the prefetched rows, never .filter(): a queryset
    # filter on a related manager ignores prefetch_related and would fire a query
    # per memo in the archive list.
    step = next(
        (s for s in sorted(memo.workflow_steps.all(), key=lambda s: s.sequence)
         if s.role_type == RoleType.APPROVER and s.status == StepStatus.COMPLETED),
        None,
    )
    at = (step.acted_at if step else None) or memo.approved_at
    return {
        "stamp": STAMP_WORDS[RoleType.APPROVER],
        "approved_at": at,
        "approved_by": step.display_name if step else "—",
        "designation": (step.designation if step else "") or "—",
        "department": (step.department_label if step else "")
        or memo.resolved_department_name() or "—",
        "verification_id": verification_id(memo, step) if step else "",
        "archived_at": memo.archived_at,
    }


def _initials(name):
    return certification.initials(name)


# ---------------------------------------------------------------------------
# Phase 18 - the visual workflow tracker
# ---------------------------------------------------------------------------
# The five milestones the tracker draws, and the memo status each one is reached
# at. Fixed rather than derived from the matrix, because the tracker's job is to
# show the standard hierarchy - a memo with no supporter still shows Supported,
# greyed, so the reader can see the stage was skipped rather than wonder whether
# the tracker is broken.
# Phase 49.5: `reviewed` and `noted` added.
#
# The Phase 49 audit found a memo that had genuinely been reviewed showing a
# tracker with no review on it - the reviewer's action appeared in the matrix and
# the timeline but not in the progress indicator, so a reader who trusted the
# tracker concluded no review took place. Reviewer is also the one role the module
# requires remarks from, which made it the worst stage to omit.
#
# `reviewed` sits FIRST among the role stages, matching ROLE_RANK where reviewer is
# rank 0. `noted` sits after `approved` and before `archived`: noting is
# information given about a decided memo, and it is the one stage that is not part
# of the approval chain - build_tracker reads it from the note round, not from a
# workflow step.
TRACKER_STAGES = [
    ("created", "Created", None),
    ("reviewed", "Reviewed", Memo.Status.UNDER_REVIEW),
    ("recommended", "Recommended", Memo.Status.RECOMMENDED),
    ("supported", "Supported", Memo.Status.SUPPORTED),
    ("approved", "Approved", Memo.Status.APPROVED),
    ("noted", "Noted", None),
    ("archived", "Archived", Memo.Status.ARCHIVED),
]

# Which role type completing marks each stage reached.
_STAGE_ROLE = {
    "reviewed": RoleType.REVIEWER,
    "recommended": RoleType.RECOMMENDER,
    "supported": RoleType.SUPPORTER,
    "approved": RoleType.APPROVER,
}


def build_tracker(memo):
    """
    Five milestones with a state each: done / active / pending / rejected /
    skipped.

    Derived from the matrix and the memo's own timestamps rather than from status
    alone, because status is a single value and the tracker has to say something
    about every stage at once. A stage the chain does not contain reads "skipped",
    not "pending" - a memo routed straight to an approver is not forever waiting
    on a recommendation it never asked for.
    """
    steps = list(memo.workflow_steps.order_by("sequence"))
    rejected_at = next((s for s in steps if s.status == StepStatus.REJECTED), None)
    active = next((s for s in steps if s.status == StepStatus.ACTIVE), None)

    stages = []
    for key, label, _status in TRACKER_STAGES:
        if key == "created":
            stages.append({"key": key, "label": label, "state": "done",
                           "at": memo.created_at, "actor": _describe(memo.created_by)})
            continue

        if key == "archived":
            state = "done" if memo.archived_at else (
                "rejected" if memo.status == Memo.Status.REJECTED else "pending")
            stages.append({"key": key, "label": label, "state": state,
                           "at": memo.archived_at, "actor": ""})
            continue

        # Noted is read from the note round, not from a workflow step - it is the
        # one stage that is not part of the approval chain. "skipped" when nobody
        # was asked to note, exactly as for a role the chain never included: a memo
        # nobody was asked to note is not forever waiting on a note.
        if key == "noted":
            round_ = getattr(memo, "note_round", None)
            recipients = list(round_.recipients.all()) if round_ else []
            if not recipients:
                state, at, actor = "skipped", None, ""
            else:
                noted = [r for r in recipients if r.noted_at is not None]
                if len(noted) == len(recipients):
                    last = max(noted, key=lambda r: r.noted_at)
                    state, at, actor = "done", last.noted_at, last.display_name
                else:
                    state, at, actor = "active", None, ""
                    actor = f"{len(noted)} of {len(recipients)} noted"
            stages.append({"key": key, "label": label, "state": state,
                           "at": at, "actor": actor})
            continue

        role = _STAGE_ROLE[key]
        role_steps = [s for s in steps if s.role_type == role]

        if not role_steps:
            # The chain never included this stage.
            state, at, actor = "skipped", None, ""
        elif all(s.status == StepStatus.COMPLETED for s in role_steps):
            done = role_steps[-1]
            state, at, actor = "done", done.acted_at, done.display_name
        elif any(s.status == StepStatus.REJECTED for s in role_steps):
            bad = next(s for s in role_steps if s.status == StepStatus.REJECTED)
            state, at, actor = "rejected", bad.acted_at, bad.display_name
        elif active is not None and active.role_type == role:
            state, at, actor = "active", None, active.display_name
        elif rejected_at is not None:
            # A rejection earlier in the chain means this stage never ran.
            state, at, actor = "skipped", None, ""
        else:
            state, at, actor = "pending", None, role_steps[0].display_name

        stages.append({"key": key, "label": label, "state": state,
                       "at": at, "actor": actor})

    return stages


# ---------------------------------------------------------------------------
# Inbox ageing (Phase 12 item 7)
# ---------------------------------------------------------------------------
def step_ageing(memo, step):
    """
    How long `step` has been waiting and how that compares to the SLA.

    Returned as data rather than a formatted string so the API stays the single
    source of the numbers and the UI only chooses how to draw them.

    `state` is one of on_track / due_soon / overdue, which is what the inbox
    colour-codes on. Ageing is measured from `activated_at` - the moment the memo
    reached this person - never from the memo's creation date, or every step
    behind a slow one would inherit its delay.
    """
    if step is None or step.activated_at is None:
        return {"pending_since": None, "pending_days": None,
                "sla_days": None, "due_days": None, "state": "on_track"}

    sla = Memo.DEFAULT_SLA_DAYS
    elapsed = (timezone.now() - step.activated_at).total_seconds() / 86400
    pending_days = int(elapsed)
    due_days = sla - pending_days

    if due_days < 0:
        state = "overdue"
    elif due_days == 0:
        state = "due_soon"
    else:
        state = "on_track"

    return {
        "pending_since": step.activated_at,
        "pending_days": pending_days,
        "sla_days": sla,
        "due_days": due_days,
        "state": state,
    }
