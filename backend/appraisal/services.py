"""Appraisal primitives: audit, snapshots, and the goal-weight rule."""
import logging

from rest_framework.exceptions import ValidationError

from audit.services import log_action

from .models import AppraisalAuditLog


def notify_user(user, category, title, body, appraisal, *,
                idempotency_key=None):
    """
    Send one in-app/email notification about an appraisal, respecting the
    recipient's own preferences (the dispatcher handles that).

    Never raises. A notification backend that is down must not roll back a
    transition that has already been recorded — the conversation happened, and
    the timeline is the record of it; the bell is a convenience.

    `action_url` points at the record rather than a stage-specific screen: an
    appraisal moves on, and a link to "the self-assessment page" is wrong the
    moment somebody submits it.
    """
    from notifications.dispatcher import notify

    if user is None:
        return None
    try:
        return notify(
            user, category, title, body,
            action_url=f"/appraisals/{appraisal.pk}",
            idempotency_key=idempotency_key,
            object_id=str(appraisal.pk),
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Appraisal notification failed for %s",
                         getattr(user, "pk", None))
        return None

logger = logging.getLogger("appraisal")

GOAL_WEIGHT_TOTAL = 100
MIN_REMARK_LENGTH = 10

# Mapping onto the project-wide audit vocabulary, which is eight verbs in a
# varchar(20) shared by every app. The precise action stays in `transition`,
# the convention the memo, circular and task modules already follow.
_SHARED = {
    AppraisalAuditLog.Action.CREATED: "create",
    AppraisalAuditLog.Action.CLOSED: "approve",
    AppraisalAuditLog.Action.FINALISED: "approve",
    AppraisalAuditLog.Action.RETURNED: "reject",
    AppraisalAuditLog.Action.SELF_ASSESSED: "submit",
    AppraisalAuditLog.Action.SUPERVISOR_REVIEWED: "approve",
    AppraisalAuditLog.Action.COMMITTEE_REVIEWED: "approve",
}


def user_snapshot(user):
    """Name / designation / department, frozen at the moment of recording."""
    if user is None:
        return {"name": "", "designation": "", "department": ""}
    department = ""
    try:
        department = getattr(user, "department_name", "") or ""
    except Exception:  # pragma: no cover - defensive
        department = getattr(user, "department", "") or ""
    return {
        "name": (user.get_full_name() or user.username or "")[:150],
        "designation": (getattr(user, "designation", "") or "")[:120],
        "department": department[:150],
    }


def record_audit(appraisal, actor, action, *, remarks="", from_status="",
                 to_status="", metadata=None, request=None):
    """
    One row of the appraisal's timeline, and one of the global trail.

    Not wrapped in a try/except: an appraisal transition with no record of it is
    an evidence gap in the most contestable record this system holds, so a failed
    audit write must fail the transition.
    """
    from audit.models import AuditLog

    entry = AppraisalAuditLog.objects.create(
        appraisal=appraisal,
        actor=actor if actor is not None and getattr(
            actor, "is_authenticated", False) else None,
        actor_name=user_snapshot(actor)["name"],
        action=action,
        from_status=from_status or "",
        to_status=to_status or "",
        remarks=remarks or "",
        metadata=metadata or {},
    )
    log_action(actor, _SHARED.get(action, AuditLog.Action.UPDATE),
               instance=appraisal,
               changes={"transition": str(action), "from": from_status or "",
                        "to": to_status or "", "remarks": remarks or ""},
               request=request)
    return entry


def require_remarks(value, field="remarks"):
    """
    A reason that is present and actually says something.

    Used where a decision sends work back to somebody. "No" is not a reason
    anybody can act on, and this is the field the employee reads to find out
    what is expected.
    """
    text = (value or "").strip()
    if len(text) < MIN_REMARK_LENGTH:
        raise ValidationError(
            {field: f"Give a reason of at least {MIN_REMARK_LENGTH} characters."})
    return text


def validate_goal_weights(appraisal):
    """
    Goal weights must total exactly 100 before objectives are agreed.

    Checked at the TRANSITION rather than on every save: a half-built set of
    goals legitimately does not total 100 while somebody is still typing, and
    refusing each keystroke would make the form unusable. The gate is the moment
    the goals become the thing the person is measured against.
    """
    goals = list(appraisal.goals.all())
    if not goals:
        raise ValidationError(
            {"goals": "Add at least one objective before agreeing goals."})
    total = sum(goal.weight for goal in goals)
    if total != GOAL_WEIGHT_TOTAL:
        raise ValidationError({
            "goals": f"Goal weights must total {GOAL_WEIGHT_TOTAL}%. "
                     f"They currently total {total}%."})
    return total
