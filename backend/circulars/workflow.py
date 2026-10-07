"""
The circular workflow engine (Phase 50).

    DRAFT -> UNDER_REVIEW -> READY_FOR_ISSUE -> ISSUED
          -> READY_FOR_BROADCAST -> BROADCASTED -> ARCHIVED

REVIEW IS OPTIONAL, AND THAT IS A STRUCTURAL FACT
-------------------------------------------------
The brief marks review optional, which means the engine cannot assume a reviewer
exists. Rather than branch on that at every transition, the chain is built with the
reviewers the author named (possibly none) followed by exactly one issuer, and
`send_for_review` activates the first step whatever its role is. A circular with no
reviewers therefore goes straight to the issuer and its status goes straight to
READY_FOR_ISSUE - no special case, no skipped-stage bookkeeping.

DESIGN RULES, inherited from the memo engine because they were right
-------------------------------------------------------------------
  * every mutating transition is one @transaction.atomic block;
  * the circular row is re-read under select_for_update() so two actors racing on
    the same circular cannot both advance it;
  * a guard failure raises DRF ValidationError, which is a 400 with a message a
    person can act on;
  * no state changes without an audit row - services.record_audit writes both the
    module's own history and the shared forensic log.

WHAT IS NOT HERE
----------------
The audience and the recipients live in broadcast.py. That separation is the whole
architectural claim of this module: the chain is small and sequential, the audience
is large and set-shaped, and one file that did both would end up treating a
recipient as a workflow step.
"""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from users.models import User

from .models import Circular, CircularAuditLog, CircularWorkflowStep
from .services import MIN_REMARK_LENGTH, describe, record_audit, step_snapshot

logger = logging.getLogger("circulars")

RoleType = CircularWorkflowStep.RoleType
StepStatus = CircularWorkflowStep.StepStatus
Action = CircularAuditLog.Action
Status = Circular.Status

# Statuses from which the chain may still be rebuilt. Once a circular is out for
# review, reordering it would invalidate a review already given.
CHAIN_EDITABLE_STATUSES = frozenset({Status.DRAFT, Status.REJECTED})

# A circular chain is a handful of reviewers and one issuer. The cap is a guard
# against a client posting nonsense, not a business rule anybody will meet.
MAX_CHAIN_STEPS = 12


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lock(circular):
    return Circular.objects.select_for_update().get(pk=circular.pk)


def _is_admin(user):
    return getattr(user, "role", None) == User.Roles.ADMIN


def _is_hr(user):
    """HR is the APPROVER role - the stored values are maker/checker/approver/admin."""
    return getattr(user, "role", None) == User.Roles.APPROVER


def _require_remark(remark, field="remarks"):
    remark = (remark or "").strip()
    if len(remark) < MIN_REMARK_LENGTH:
        raise ValidationError({field: (
            f"A remark of at least {MIN_REMARK_LENGTH} characters is required.")})
    return remark


def _notify(recipient, category, title, body, circular):
    """
    Best-effort notification. A delivery failure must never roll back a workflow
    transition - the circular has moved whether or not the email left.
    """
    if recipient is None:
        return
    try:
        from notifications.dispatcher import notify
        notify(recipient, category, title, body,
               action_url=f"/circulars/{circular.id}",
               # Ties the delivery-log row back to this circular. Without it a
               # send is recorded but cannot be traced to what caused it, which
               # is most of the value of having an audit log at all.
               object_id=str(circular.id),
               email_context={"reference": circular.circular_number})
    except Exception:  # pragma: no cover - deliberately swallowed
        logger.exception("circular notification failed category=%s circular=%s",
                         category, circular.circular_number)


def record_creation(circular, actor, request=None):
    """Open the timeline at 'Created by ...'."""
    return record_audit(circular, actor, Action.CREATED,
                        remarks="Circular created.", request=request)


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------
def _resolve_chain(circular, rows):
    """
    Validate a proposed chain and resolve its assignees.

    Four rules, all of them enforced HERE rather than re-checked at each
    transition - if the chain cannot express an out-of-order issue, the engine
    cannot perform one:

      1. exactly one issuer, and it is the last step;
      2. reviewers, if any, come before it;
      3. the author is not in their own chain;
      4. nobody appears twice.
    """
    if not rows:
        raise ValidationError({"workflow": (
            "Name at least an issuer. A circular is issued by somebody.")})
    if len(rows) > MAX_CHAIN_STEPS:
        raise ValidationError({"workflow": (
            f"A circular chain is at most {MAX_CHAIN_STEPS} steps.")})

    issuers = [i for i, row in enumerate(rows)
               if row["role_type"] == RoleType.ISSUER]
    if len(issuers) != 1:
        raise ValidationError({"workflow": (
            "A circular has exactly one issuer - the person whose authority it "
            f"carries. Found {len(issuers)}.")})
    if issuers[0] != len(rows) - 1:
        raise ValidationError({"workflow": (
            f"Step {issuers[0] + 1} is the Issuer but is not the last step. "
            "Review comes before issue.")})

    seen, resolved = set(), []
    for position, row in enumerate(rows, start=1):
        assignee = User.objects.filter(pk=row["assignee_id"], is_active=True).first()
        if assignee is None:
            raise ValidationError({"workflow": (
                f"Step {position}: no such active employee.")})
        if assignee.id == circular.created_by_id:
            raise ValidationError({"workflow": (
                "The author cannot review or issue their own circular.")})
        if assignee.id in seen:
            raise ValidationError({"workflow": (
                f"{describe(assignee)} appears twice. One person cannot hold two "
                "control points in the same chain.")})
        seen.add(assignee.id)
        resolved.append({"assignee": assignee, "role_type": row["role_type"]})
    return resolved


@transaction.atomic
def set_chain(circular, actor, rows, request=None):
    """
    Replace the circular's review/issue chain. Draft or returned only.

    Once the chain is live, reordering it would invalidate a review already given -
    the same reason the memo module freezes its matrix.
    """
    circular = _lock(circular)
    if circular.created_by_id != actor.id and not _is_admin(actor):
        raise PermissionDenied("Only the author can set this circular's chain.")
    if circular.status not in CHAIN_EDITABLE_STATUSES:
        raise ValidationError({"workflow": (
            f"This circular is {circular.get_status_display().lower()}; its chain "
            "is fixed. Reordering it now would invalidate a review already given.")})

    resolved = _resolve_chain(circular, rows)
    circular.workflow_steps.all().delete()
    for sequence, row in enumerate(resolved, start=1):
        CircularWorkflowStep.objects.create(
            circular=circular, sequence=sequence, assignee=row["assignee"],
            role_type=row["role_type"], **step_snapshot(row["assignee"]))
    record_audit(circular, actor, Action.EDITED,
                 remarks=f"Chain set: {len(resolved)} step(s).",
                 metadata={"steps": [
                     {"sequence": i, "role": r["role_type"],
                      "assignee": describe(r["assignee"])}
                     for i, r in enumerate(resolved, start=1)]},
                 request=request)
    return circular


def _activate(step, circular):
    """Make `step` the active one and tell its holder."""
    step.status = StepStatus.ACTIVE
    step.activated_at = timezone.now()
    step.save(update_fields=["status", "activated_at"])
    _notify(step.assignee,
            CircularWorkflowStep.ASSIGNMENT_CATEGORY[step.role_type],
            f"Circular {circular.circular_number} needs your "
            f"{step.get_role_type_display().lower()}",
            f"'{circular.subject}' is with you for "
            f"{step.get_role_type_display().lower()}.",
            circular)


@transaction.atomic
def send_for_review(circular, actor, rows=None, request=None):
    """
    Submit the circular into its chain.

    Named send_for_review for continuity with the other two modules, but it is
    really "submit": if the author named no reviewers the first step is the issuer
    and the status goes straight to READY_FOR_ISSUE. That is what makes review
    optional without a branch.
    """
    circular = _lock(circular)
    if circular.created_by_id != actor.id and not _is_admin(actor):
        raise PermissionDenied("Only the author can submit this circular.")
    if circular.status not in CHAIN_EDITABLE_STATUSES:
        raise ValidationError({"workflow": (
            f"This circular is {circular.get_status_display().lower()} and has "
            "already been submitted.")})
    if not (circular.subject or "").strip():
        raise ValidationError({"subject": "A circular needs a subject."})
    if not (circular.content or "").strip():
        raise ValidationError({"content": "A circular needs content."})

    if rows:
        set_chain(circular, actor, rows, request=request)
        circular = _lock(circular)
    steps = list(circular.workflow_steps.order_by("sequence"))
    if not steps:
        raise ValidationError({"workflow": (
            "Set a chain before submitting: a circular needs an issuer.")})

    first = steps[0]
    circular.status = (Status.UNDER_REVIEW if first.role_type == RoleType.REVIEWER
                       else Status.READY_FOR_ISSUE)
    circular.submitted_at = timezone.now()
    if circular.issue_date is None:
        circular.issue_date = timezone.localdate()
    circular.save(update_fields=["status", "submitted_at", "issue_date",
                                 "updated_at"])
    _activate(first, circular)
    record_audit(circular, actor, Action.SENT_FOR_REVIEW,
                 remarks=f"Submitted to {first.display_name} "
                         f"({first.get_role_type_display()}).",
                 request=request)
    return circular


@transaction.atomic
def act_on_step(circular, actor, decision, remarks="", request=None):
    """
    Complete or return at the caller's step.

    One endpoint rather than two verbs, because the active step's role already
    determines what "proceed" means - which removes any chance of a client calling
    `issue` on a reviewer's step.
    """
    circular = _lock(circular)
    step = next((s for s in circular.workflow_steps.order_by("sequence")
                 if s.status == StepStatus.ACTIVE), None)
    if step is None:
        raise ValidationError({"workflow": (
            "This circular has no step awaiting action.")})
    if step.assignee_id != actor.id and not _is_admin(actor):
        holder = step.display_name
        raise PermissionDenied(
            f"This circular is with {holder} at step {step.sequence}. It is not "
            "your turn.")

    if decision == "reject":
        return _return_to_author(circular, actor, step, remarks, request=request)
    if decision != "proceed":
        raise ValidationError({"decision": "Choose 'proceed' or 'reject'."})

    # A reviewer is being asked to say something; an issuer's signature speaks for
    # itself, so remarks there are optional.
    if step.role_type == RoleType.REVIEWER:
        remarks = _require_remark(remarks)

    step.status = StepStatus.COMPLETED
    step.acted_at = timezone.now()
    step.remarks = (remarks or "").strip()
    step.save(update_fields=["status", "acted_at", "remarks"])

    action = (Action.REVIEWED if step.role_type == RoleType.REVIEWER
              else Action.ISSUED)
    following = circular.workflow_steps.filter(
        sequence__gt=step.sequence, status=StepStatus.PENDING).order_by(
            "sequence").first()

    if following is not None:
        circular.status = (Status.UNDER_REVIEW
                           if following.role_type == RoleType.REVIEWER
                           else Status.READY_FOR_ISSUE)
        circular.save(update_fields=["status", "updated_at"])
        _activate(following, circular)
    else:
        # The last step completed. Only an issuer can be last - _resolve_chain
        # guarantees it - so this is the issue.
        circular.status = Status.ISSUED
        circular.issued_at = timezone.now()
        circular.issued_by = step.assignee
        circular.issued_by_name = step.display_name
        circular.save(update_fields=["status", "issued_at", "issued_by",
                                     "issued_by_name", "updated_at"])
        _notify(circular.created_by, "CIRCULAR_ISSUED",
                f"Circular {circular.circular_number} has been issued",
                f"'{circular.subject}' was issued by {step.display_name}. "
                "Define its audience to broadcast it.", circular)

    record_audit(circular, actor, action, remarks=step.remarks,
                 metadata={"step": step.sequence, "role": step.role_type},
                 request=request)
    return circular


def _return_to_author(circular, actor, step, remarks, request=None):
    """
    Send the circular back for revision.

    A rejection needs a reason - it is the only thing the author has to work from -
    and the whole chain stands down rather than pausing, because a revised circular
    should be reviewed afresh rather than resumed halfway.
    """
    remarks = _require_remark(remarks)
    step.status = StepStatus.REJECTED
    step.acted_at = timezone.now()
    step.remarks = remarks
    step.save(update_fields=["status", "acted_at", "remarks"])
    circular.workflow_steps.filter(status__in=[StepStatus.PENDING,
                                               StepStatus.ACTIVE]).update(
        status=StepStatus.SKIPPED)

    circular.status = Status.REJECTED
    circular.save(update_fields=["status", "updated_at"])
    record_audit(circular, actor, Action.REJECTED, remarks=remarks,
                 metadata={"step": step.sequence}, request=request)
    _notify(circular.created_by, "CIRCULAR_RETURNED",
            f"Circular {circular.circular_number} was returned",
            f"{describe(actor)} returned it for revision: {remarks}", circular)
    return circular


@transaction.atomic
def cancel(circular, actor, remarks="", request=None):
    """
    Withdraw a circular before it is broadcast.

    Refused once broadcast: people have already been told. An issued circular that
    turns out to be wrong is corrected by issuing an amended one, which is a
    governance act with its own record - not by deleting the evidence that the
    first one went out.
    """
    circular = _lock(circular)
    if circular.status == Status.BROADCASTED:
        raise ValidationError({"cancel": (
            "This circular has been broadcast - recipients have already been told. "
            "Issue an amended circular instead; cancelling would remove the record "
            "that the first one went out.")})
    if circular.status in Circular.TERMINAL_STATUSES:
        raise ValidationError({"cancel": (
            f"This circular is already {circular.get_status_display().lower()}.")})
    if circular.created_by_id != actor.id and not (_is_admin(actor) or _is_hr(actor)):
        raise PermissionDenied(
            "Only the author, HR or an administrator can cancel a circular.")
    remarks = _require_remark(remarks)

    circular.workflow_steps.filter(status__in=[StepStatus.PENDING,
                                               StepStatus.ACTIVE]).update(
        status=StepStatus.SKIPPED)
    circular.status = Status.CANCELLED
    circular.save(update_fields=["status", "updated_at"])
    record_audit(circular, actor, Action.CANCELLED, remarks=remarks, request=request)
    return circular


@transaction.atomic
def archive(circular, actor, request=None):
    """
    File a broadcast circular to the permanent record.

    Only from BROADCASTED, and only once the acknowledgement round has closed if
    one was required - archiving mid-round would freeze a register people were
    still responding to, and the deadline is what closes it.
    """
    circular = _lock(circular)
    if circular.status == Status.ARCHIVED:
        return circular
    if circular.status != Status.BROADCASTED:
        raise ValidationError({"archive": (
            "Only a broadcast circular can be archived. This one is "
            f"{circular.get_status_display().lower()}.")})
    if not (_is_admin(actor) or _is_hr(actor) or circular.created_by_id == actor.id):
        raise PermissionDenied(
            "Only the author, HR or an administrator can archive a circular.")

    circular.status = Status.ARCHIVED
    circular.archived_at = timezone.now()
    circular.save(update_fields=["status", "archived_at", "updated_at"])
    record_audit(circular, actor, Action.ARCHIVED,
                 remarks="Filed to the permanent record.", request=request)
    return circular


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------
# The stages a reader sees. `review` is reported as SKIPPED when the chain has no
# reviewer, never as pending - a circular nobody asked to review is not waiting on
# one. `acknowledged` is skipped when the circular does not require it.
TRACKER_STAGES = [
    ("created", "Created"),
    ("reviewed", "Reviewed"),
    ("issued", "Issued"),
    ("broadcast", "Broadcast"),
    ("acknowledged", "Acknowledged"),
    ("archived", "Archived"),
]


def build_tracker(circular):
    """Six milestones with a state each: done / active / pending / skipped / rejected."""
    steps = list(circular.workflow_steps.all())
    stages = []
    for key, label in TRACKER_STAGES:
        if key == "created":
            stages.append({"key": key, "label": label, "state": "done",
                           "at": circular.created_at,
                           "actor": describe(circular.created_by)})
            continue

        if key in ("reviewed", "issued"):
            role = (RoleType.REVIEWER if key == "reviewed" else RoleType.ISSUER)
            role_steps = [s for s in steps if s.role_type == role]
            if not role_steps:
                stages.append({"key": key, "label": label, "state": "skipped",
                               "at": None, "actor": ""})
                continue
            if all(s.status == StepStatus.COMPLETED for s in role_steps):
                last = role_steps[-1]
                state, at, actor = "done", last.acted_at, last.display_name
            elif any(s.status == StepStatus.REJECTED for s in role_steps):
                bad = next(s for s in role_steps
                           if s.status == StepStatus.REJECTED)
                state, at, actor = "rejected", bad.acted_at, bad.display_name
            elif any(s.status == StepStatus.ACTIVE for s in role_steps):
                active = next(s for s in role_steps
                              if s.status == StepStatus.ACTIVE)
                state, at, actor = "active", None, active.display_name
            else:
                state, at, actor = "pending", None, ""
            stages.append({"key": key, "label": label, "state": state, "at": at,
                           "actor": actor})
            continue

        if key == "broadcast":
            latest = circular.latest_broadcast
            if latest is not None:
                state, at = "done", latest.broadcast_at
                actor = f"{latest.recipient_count} recipient(s)"
            elif circular.status in (Status.ISSUED, Status.READY_FOR_BROADCAST):
                state, at, actor = "active", None, ""
            elif circular.status in Circular.TERMINAL_STATUSES:
                state, at, actor = "skipped", None, ""
            else:
                state, at, actor = "pending", None, ""
            stages.append({"key": key, "label": label, "state": state, "at": at,
                           "actor": actor})
            continue

        if key == "acknowledged":
            if not circular.acknowledgement_required:
                stages.append({"key": key, "label": label, "state": "skipped",
                               "at": None,
                               "actor": "Not required for this circular"})
                continue
            from .broadcast import acknowledgement_summary
            summary = acknowledgement_summary(circular)
            if summary["total"] == 0:
                state, at, actor = "pending", None, ""
            elif summary["pending"] == 0:
                state, at = "done", None
                actor = f"{summary['acknowledged']} of {summary['total']}"
            else:
                state, at = "active", None
                actor = f"{summary['acknowledged']} of {summary['total']} acknowledged"
            stages.append({"key": key, "label": label, "state": state, "at": at,
                           "actor": actor})
            continue

        # archived
        state = "done" if circular.archived_at else (
            "rejected" if circular.status in (Status.REJECTED, Status.CANCELLED)
            else "pending")
        stages.append({"key": key, "label": label, "state": state,
                       "at": circular.archived_at, "actor": ""})
    return stages


def build_timeline(circular):
    """
    The activity timeline: what happened, then what is still outstanding.

    HIGH_VOLUME_ACTIONS are excluded. A four-hundred-recipient circular writes four
    hundred `read` rows, and a timeline that included them would bury every workflow
    event under them. The read register answers that question properly, per person.
    """
    entries = []
    audit = circular.audit_entries.select_related("actor").exclude(
        action__in=CircularAuditLog.HIGH_VOLUME_ACTIONS).order_by("at", "sequence")
    for row in audit:
        entries.append({
            "kind": "history",
            "id": str(row.id),
            "sequence": row.sequence,
            "action": row.action,
            "label": row.get_action_display(),
            "actor_name": row.actor_name or describe(row.actor),
            "at": row.at,
            "remarks": row.remarks,
            "state": "done",
        })

    outstanding = circular.workflow_steps.filter(
        status__in=[StepStatus.ACTIVE, StepStatus.PENDING]).order_by("sequence")
    for step in outstanding:
        entries.append({
            "kind": "pending",
            "id": str(step.id),
            "sequence": step.sequence,
            "action": step.role_type,
            "label": f"{step.get_role_type_display()} — {step.display_name}",
            "actor_name": step.display_name,
            "at": None,
            "remarks": "",
            "state": "active" if step.status == StepStatus.ACTIVE else "pending",
        })
    return entries


def pending_with(circular):
    """Who the circular is waiting on, for the list and the detail header."""
    step = circular.active_step
    if step is not None:
        return {"name": step.display_name, "role": step.role_type,
                "role_label": step.get_role_type_display(),
                "sequence": step.sequence}
    if circular.status in (Status.ISSUED, Status.READY_FOR_BROADCAST):
        return {"name": describe(circular.created_by), "role": "broadcaster",
                "role_label": "Awaiting broadcast", "sequence": None}
    return None
