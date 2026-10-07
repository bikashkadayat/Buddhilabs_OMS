"""
The appraisal state machine — the specification's ten stages, exactly.

    Goal Setting -> Goal Approval -> Mid-Year Review -> Self Assessment
    -> Supervisor Review -> Review Committee -> Final Review
    -> Development Plan -> Training Plan -> Closed

TRANSITIONS ARE THE ONLY WAY STATUS CHANGES
-------------------------------------------
Every function here is atomic and writes its timeline row in the same
transaction as the state change, so a rolled-back transition cannot leave a
trail claiming it happened. Nothing outside this module assigns to
`appraisal.status` — not the serializers, not the views, not the admin.

THE ENGINE DECIDES NOTHING ABOUT A PERSON
-----------------------------------------
Each transition checks that the WORK OF THAT STAGE HAS BEEN DONE — goals total
100%, a self-assessment has been written, a supervisor has commented — and moves
the conversation on. It never evaluates the content. There is no rule here that
reads a completion percentage and decides anything, and there is no path by which
task evidence changes an appraisal's state. Evidence informs the humans; the
humans move the appraisal.

GOING BACKWARDS IS A FIRST-CLASS TRANSITION
-------------------------------------------
`return_to` exists because real appraisals go back: a supervisor reads a
self-assessment and asks for more detail, a committee sends something back for
rework. Without it people either approve things they did not mean to, or the
process leaves the system and happens over email where nothing is recorded. It
requires a written reason and cannot reach past a closed appraisal.
"""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import events
from .models import Appraisal, AppraisalAuditLog
from .services import record_audit, require_remarks, user_snapshot, validate_goal_weights

logger = logging.getLogger("appraisal")

Action = AppraisalAuditLog.Action
Status = Appraisal.Status


def _refuse(message):
    raise ValidationError({"workflow": message})


def _require(appraisal, expected, what):
    if appraisal.status != expected:
        _refuse(f"{what} is only possible while the appraisal is at "
                f"{Appraisal.Status(expected).label}. It is currently at "
                f"{appraisal.get_status_display()}.")


def _advance(appraisal, to_status, actor, action, *, remarks="", stamp=None,
             metadata=None, request=None):
    """Move one step, stamp it, record it. The single write path for status."""
    previous = appraisal.status
    appraisal.status = to_status
    fields = ["status", "updated_at"]
    if stamp:
        setattr(appraisal, stamp, timezone.now())
        fields.append(stamp)
    appraisal.save(update_fields=fields)
    record_audit(appraisal, actor, action, remarks=remarks,
                 from_status=previous, to_status=to_status,
                 metadata=metadata, request=request)
    return appraisal


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def record_creation(appraisal, actor, request=None):
    """Idempotent first timeline row."""
    if appraisal.audit_entries.filter(action=Action.CREATED).exists():
        return None
    row = record_audit(appraisal, actor, Action.CREATED,
                       remarks=f"Appraisal opened for {appraisal.employee_name} "
                               f"in {appraisal.cycle}.",
                       to_status=appraisal.status, request=request)
    # Emitted INSIDE the idempotency guard: an appraisal opened twice by a
    # retried request must not tell the employee twice.
    events.emit(events.APPRAISAL_OPENED, appraisal, actor=actor)
    return row


# ---------------------------------------------------------------------------
# 1. Goal Setting -> 2. Goal Approval
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_goals(appraisal, actor, remarks="", request=None):
    """
    1. Goal Setting -> 2. Goal Approval. The employee's objectives go to their
    supervisor for a decision.

    THE ONLY ARITHMETIC GATE IN THE ENGINE: weights must total exactly 100. That
    is a completeness check on the plan, not a judgement of the person — a set of
    goals weighted to 80% means somebody has not finished writing them, and
    discovering that at final review is too late to fix.

    The gate lives HERE rather than at approval so the person who can fix an
    incomplete set is the one who is told about it, at the moment they are
    trying to hand it over.
    """
    _require(appraisal, Status.GOAL_SETTING, "Submitting goals for approval")
    validate_goal_weights(appraisal)
    result = _advance(appraisal, Status.GOAL_APPROVAL, actor,
                      Action.GOALS_SUBMITTED,
                      remarks=remarks or "Objectives submitted for approval.",
                      metadata={"goals": appraisal.goals.count()},
                      request=request)
    events.emit(events.GOALS_SUBMITTED, appraisal, actor=actor)
    return result


# ---------------------------------------------------------------------------
# 2. Goal Approval -> 3. Mid-Year Review
# ---------------------------------------------------------------------------
@transaction.atomic
def agree_goals(appraisal, actor, remarks="", request=None):
    """
    The supervisor accepts the objectives as the basis for the year.

    This is the moment somebody is held to, so it gets its own stage and its own
    timeline row. Every goal moves DRAFT -> APPROVED here: before it, an
    employee looking at their objectives cannot tell a draft from an agreement,
    and "we never actually agreed that" is the argument this stage exists to
    prevent.

    The weights were checked on the way in (see `submit_goals`), and goals are
    frozen at this stage, so re-checking here would be checking a set nobody
    could have changed.
    """
    _require(appraisal, Status.GOAL_APPROVAL, "Approving goals")
    validate_goal_weights(appraisal)
    from .models import Goal
    approved = appraisal.goals.update(status=Goal.Status.APPROVED)
    result = _advance(appraisal, Status.MID_YEAR, actor, Action.GOALS_AGREED,
                      remarks=remarks or "Objectives approved.",
                      stamp="goals_agreed_at",
                      metadata={"goals": approved}, request=request)
    events.emit(events.GOALS_APPROVED, appraisal, actor=actor)
    return result


# ---------------------------------------------------------------------------
# 2. Mid-Year Review -> 3. Self Assessment
# ---------------------------------------------------------------------------
@transaction.atomic
def record_mid_year(appraisal, actor, remarks="", request=None):
    """
    The mid-year checkpoint. Goals may still be reshaped up to this point —
    priorities genuinely change — and after it they are fixed, because they are
    what the person was measured against.
    """
    _require(appraisal, Status.MID_YEAR, "Recording the mid-year review")
    # Goals become LOCKED here, in the same transaction as the transition. This
    # is the last point at which an objective may be reshaped, so the lock and
    # the move past it are one fact, not two that could disagree.
    from .models import Goal
    locked = appraisal.goals.update(status=Goal.Status.LOCKED)
    if locked:
        record_audit(appraisal, actor, Action.GOALS_LOCKED,
                     remarks=f"{locked} objective(s) locked at mid-year.",
                     metadata={"goals": locked}, request=request)
    result = _advance(appraisal, Status.SELF_ASSESSMENT, actor,
                      Action.MID_YEAR_RECORDED,
                      remarks=remarks or "Mid-year review recorded.",
                      stamp="mid_year_at", request=request)
    events.emit(events.SELF_ASSESSMENT_DUE, appraisal, actor=actor)
    return result


# ---------------------------------------------------------------------------
# 3. Self Assessment -> 4. Supervisor Review
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_self_assessment(appraisal, actor, request=None):
    """
    The employee's own account, in their own words, goes to their supervisor.

    Refused when empty. An appraisal that proceeds with a blank self-assessment
    is one where the person's own view was never on the record, and the rest of
    the process then happens about them rather than with them.
    """
    _require(appraisal, Status.SELF_ASSESSMENT, "Submitting a self assessment")
    if not (appraisal.self_assessment or "").strip():
        _refuse("Write your self assessment before submitting it.")
    result = _advance(appraisal, Status.SUPERVISOR_REVIEW, actor,
                      Action.SELF_ASSESSED,
                      remarks="Self assessment submitted.",
                      stamp="self_assessed_at", request=request)
    events.emit(events.REVIEW_PENDING, appraisal, actor=actor)
    return result


# ---------------------------------------------------------------------------
# 4. Supervisor Review -> 5. Review Committee
# ---------------------------------------------------------------------------
@transaction.atomic
def record_supervisor_review(appraisal, actor, request=None):
    """
    The supervisor's assessment. Requires their written comments — a review with
    no reasoning gives the employee nothing to respond to, and gives the
    committee nothing to review.
    """
    _require(appraisal, Status.SUPERVISOR_REVIEW, "Recording a supervisor review")
    if not (appraisal.supervisor_comments or "").strip():
        _refuse("Record your review comments before submitting the review.")
    result = _advance(appraisal, Status.COMMITTEE, actor,
                      Action.SUPERVISOR_REVIEWED,
                      remarks="Supervisor review recorded.",
                      stamp="supervisor_reviewed_at", request=request)
    events.emit(events.COMMITTEE_REVIEW, appraisal, actor=actor)
    return result


# ---------------------------------------------------------------------------
# 5. Review Committee -> 6. Final Review
# ---------------------------------------------------------------------------
@transaction.atomic
def record_committee_review(appraisal, actor, request=None):
    """The committee's view, added without erasing the supervisor's."""
    _require(appraisal, Status.COMMITTEE, "Recording a committee review")
    if not (appraisal.committee_comments or "").strip():
        _refuse("Record the committee's comments before moving on.")
    return _advance(appraisal, Status.FINAL_REVIEW, actor,
                    Action.COMMITTEE_REVIEWED,
                    remarks="Committee review recorded.",
                    stamp="committee_reviewed_at", request=request)


# ---------------------------------------------------------------------------
# 6. Final Review -> 7. Development Plan
# ---------------------------------------------------------------------------
@transaction.atomic
def record_final_review(appraisal, actor, request=None):
    """
    The final written summary.

    Note what is NOT required here and never will be: a grade, a band or a
    score. The outcome of this stage is prose plus, optionally, a human's
    promotion recommendation with its rationale.
    """
    _require(appraisal, Status.FINAL_REVIEW, "Recording the final review")
    if not (appraisal.final_summary or "").strip():
        _refuse("Write the final summary before completing the review.")
    if appraisal.promotion_readiness and not (
            appraisal.promotion_rationale or "").strip():
        # A recommendation with no reasoning is the kind of record that cannot
        # be defended later, in ANY of the three directions — "development
        # required" needs to say what development just as much as "ready" needs
        # to say why.
        _refuse("A promotion recommendation needs its rationale recorded.")
    result = _advance(appraisal, Status.DEVELOPMENT_PLAN, actor, Action.FINALISED,
                      remarks="Final review recorded.", stamp="finalised_at",
                      request=request)
    events.emit(events.FEEDBACK_READY, appraisal, actor=actor)
    return result


# ---------------------------------------------------------------------------
# 7. Development Plan -> 8. Training Plan
# ---------------------------------------------------------------------------
@transaction.atomic
def agree_development_plan(appraisal, actor, request=None):
    """
    At least one development action. An appraisal that identifies nothing to
    develop has either not been thought about or is not being used for what it
    is for — and either way the conversation should happen before it closes.
    """
    _require(appraisal, Status.DEVELOPMENT_PLAN, "Agreeing a development plan")
    if not appraisal.development_plans.exists():
        _refuse("Add at least one development action before moving on.")
    return _advance(appraisal, Status.TRAINING_PLAN, actor,
                    Action.DEVELOPMENT_PLANNED,
                    remarks="Development plan agreed.",
                    metadata={"actions": appraisal.development_plans.count()},
                    request=request)


# ---------------------------------------------------------------------------
# 8. Training Plan -> 9. Closed
# ---------------------------------------------------------------------------
@transaction.atomic
def agree_training_plan(appraisal, actor, request=None):
    """
    Training may legitimately be empty — not every appraisal identifies a course
    — so this stage records that the question was ASKED rather than that an
    answer was found. Forcing a training row would produce invented ones, which
    is worse than none.
    """
    _require(appraisal, Status.TRAINING_PLAN, "Agreeing a training plan")
    result = _advance(appraisal, Status.CLOSED, actor, Action.TRAINING_PLANNED,
                      remarks="Training plan agreed; appraisal closed.",
                      stamp="closed_at",
                      metadata={"training": appraisal.training_plans.count()},
                      request=request)
    events.emit(events.APPRAISAL_CLOSED, appraisal, actor=actor)
    return result


# ---------------------------------------------------------------------------
# Going back
# ---------------------------------------------------------------------------
@transaction.atomic
def return_to(appraisal, actor, to_status, remarks, request=None):
    """
    Send the appraisal back to an earlier stage, with a written reason.

    Real appraisals go backwards, and a process that cannot will either be
    approved dishonestly or will leave the system for email — where nothing is
    recorded and the employee has no copy. Constraints: the target must be
    EARLIER on the ladder (this is not a shortcut forward), and a closed
    appraisal is a permanent record and cannot be reopened here.
    """
    if appraisal.is_closed:
        _refuse("A closed appraisal cannot be reopened.")
    if to_status not in Appraisal.LADDER:
        _refuse("That is not a stage of this process.")
    # Compared as LADDER POSITIONS on both sides. `stage_index` is one-based
    # for display, so mixing the two here would let an appraisal be returned to
    # the stage it is already at.
    if Appraisal.LADDER.index(to_status) >= Appraisal.LADDER.index(
            appraisal.status):
        _refuse("An appraisal can only be returned to an EARLIER stage.")

    reason = require_remarks(remarks)
    # Sending objectives back for rework must UNDO their approval. Left as
    # APPROVED, the employee is asked to rewrite a set the record still claims
    # was agreed — and whichever version is quoted later, one of the two is
    # wrong. Only DRAFT is restored: goals locked at mid-year stay locked,
    # because a return from a later stage is not an invitation to rewrite the
    # objectives the year was measured against.
    from .models import Goal
    if to_status == Status.GOAL_SETTING:
        reverted = appraisal.goals.exclude(
            status=Goal.Status.LOCKED).update(status=Goal.Status.DRAFT)
        if reverted:
            appraisal.goals_agreed_at = None
            appraisal.save(update_fields=["goals_agreed_at", "updated_at"])
    result = _advance(appraisal, to_status, actor, Action.RETURNED,
                      remarks=reason, request=request)
    events.emit(events.APPRAISAL_RETURNED, appraisal, actor=actor,
                to_status=to_status, remarks=reason)
    return result


@transaction.atomic
def reopen(appraisal, actor, remarks, request=None):
    """
    HR reopening a closed appraisal — for a genuine correction, not a change of
    mind.

    Deliberately separate from `return_to` and separately permissioned: reopening
    a closed record is the single most sensitive action in this module, and it
    must be visibly different in the audit trail from an ordinary return.
    """
    if not appraisal.is_closed:
        _refuse("That appraisal is not closed.")
    reason = require_remarks(remarks)
    appraisal.closed_at = None
    appraisal.save(update_fields=["closed_at", "updated_at"])
    return _advance(appraisal, Status.FINAL_REVIEW, actor, Action.RETURNED,
                    remarks=f"Reopened: {reason}", request=request)
