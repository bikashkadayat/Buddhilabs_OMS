"""
Notification delivery for appraisal events (Phase RELEASE).

WHAT THIS FIXES
---------------
The module could run a complete ten-stage cycle and tell nobody. An employee
discovered their self-assessment was due by visiting the page; a supervisor
found a review waiting by looking for it. Every other module in this system —
memo, minute, circular, leave, inventory, task — notifies. This one did not, and
the failure mode was silence, which nobody reports as a bug.

WHO IS TOLD, AND WHO IS NOT
---------------------------
One rule decides every recipient: **tell the person whose turn it has just
become, and the person who was waiting on the answer.** Nobody else.

That deliberately excludes HR from the per-appraisal traffic. HR runs a hundred
of these; a notification per stage per person would be a hundred a week, and the
predictable result is a filter rule that hides the category — including the ones
HR does need. HR's view is the cycle dashboard, which is a pull, not a push.

The actor is never told about their own action.

WHAT THE EMPLOYEE IS TOLD, AND IN WHOSE WORDS
---------------------------------------------
Plain language, no stage names. "Your self assessment is ready to write" rather
than "Appraisal moved to SELF_ASSESSMENT". The one exception is a RETURN, which
carries the reviewer's own written reason verbatim — that reason is the only
thing the person receiving it back will actually read, and paraphrasing it would
lose the specifics they need to act on.

IDEMPOTENCY
-----------
Every send carries a key built from the appraisal, the event and the stage
stamp, so a retried transition cannot notify twice. Where an event can genuinely
recur — a return, which may happen more than once — the key includes a timestamp
so the second one is correctly a new message.

CONNECTED IN AppConfig.ready(), ONCE
------------------------------------
`dispatch_uid` on each connection makes the once-only guarantee explicit rather
than relying on Django importing `ready()` a single time.
"""
import logging

from notifications.models import Category

from . import events
from .services import notify_user

logger = logging.getLogger("appraisal")


def _label(appraisal):
    return f"{appraisal.cycle.name} — {appraisal.employee_name}"


def _stamp(appraisal):
    """A key component that changes when the appraisal genuinely moves on."""
    return appraisal.status


def _tell(user, category, title, body, appraisal, *, exclude=None, key=None):
    """Send to one person, never to the actor who caused the event."""
    if user is None:
        return None
    if exclude is not None and user.pk == getattr(exclude, "pk", None):
        return None
    if not getattr(user, "is_active", True):
        return None
    return notify_user(user, category, title, body, appraisal,
                       idempotency_key=key)


def _committee(appraisal):
    return [u for u in appraisal.committee.all() if u]


# ---------------------------------------------------------------------------
# Receivers
# ---------------------------------------------------------------------------
def on_opened(sender, appraisal, actor=None, **kwargs):
    """The employee learns a cycle has opened for them, and what to do first."""
    _tell(appraisal.employee, Category.APPRAISAL_OPENED,
          "Your appraisal has opened",
          f"{appraisal.cycle.name} has started. Your first step is to write "
          f"your goals and send them to your manager.",
          appraisal, exclude=actor, key=f"apr-{appraisal.pk}-opened")


def on_goals_submitted(sender, appraisal, actor=None, **kwargs):
    """The supervisor is now the one blocking."""
    _tell(appraisal.supervisor, Category.APPRAISAL_GOALS_SUBMITTED,
          "Goals waiting for your approval",
          f"{appraisal.employee_name} has sent their goals for approval.",
          appraisal, exclude=actor,
          key=f"apr-{appraisal.pk}-goals-submitted")


def on_goals_approved(sender, appraisal, actor=None, **kwargs):
    _tell(appraisal.employee, Category.APPRAISAL_GOALS_APPROVED,
          "Your goals have been approved",
          f"Your manager has approved your goals for {appraisal.cycle.name}. "
          f"They are what your review will be about.",
          appraisal, exclude=actor, key=f"apr-{appraisal.pk}-goals-approved")


def on_self_assessment_due(sender, appraisal, actor=None, **kwargs):
    """
    The one notification this module most needed and did not have. Carries the
    cycle's advisory deadline when there is one — a date is what turns a
    reminder into something somebody schedules.
    """
    deadline = appraisal.cycle.self_assessment_deadline
    when = f" It is due by {deadline}." if deadline else ""
    _tell(appraisal.employee, Category.APPRAISAL_SELF_ASSESSMENT_DUE,
          "Your self assessment is ready to write",
          f"Your appraisal has reached your part of it.{when}",
          appraisal, exclude=actor, key=f"apr-{appraisal.pk}-self-due")


def on_review_pending(sender, appraisal, actor=None, **kwargs):
    _tell(appraisal.supervisor, Category.APPRAISAL_REVIEW_PENDING,
          "An appraisal is waiting for your review",
          f"{appraisal.employee_name} has submitted their self assessment.",
          appraisal, exclude=actor, key=f"apr-{appraisal.pk}-review-pending")


def on_committee_review(sender, appraisal, actor=None, **kwargs):
    for member in _committee(appraisal):
        _tell(member, Category.APPRAISAL_COMMITTEE_REVIEW,
              "An appraisal is ready for the review committee",
              f"{appraisal.employee_name}'s appraisal is with the committee "
              f"you sit on.",
              appraisal, exclude=actor,
              key=f"apr-{appraisal.pk}-committee-{member.pk}")


def on_feedback_ready(sender, appraisal, actor=None, **kwargs):
    _tell(appraisal.employee, Category.APPRAISAL_FEEDBACK_READY,
          "Your appraisal feedback is ready",
          "Your review has been completed and you can read the feedback on "
          "your appraisal.",
          appraisal, exclude=actor, key=f"apr-{appraisal.pk}-feedback")


def on_returned(sender, appraisal, actor=None, to_status=None, remarks="",
                **kwargs):
    """
    Sent back for changes. Whoever now owns the stage is told, with the reason
    VERBATIM — paraphrasing it would lose the specifics they need to act on, and
    that reason is the only part they will actually read.

    Keyed on the AUDIT ROW, not on a timestamp. A second return to the same
    stage is a genuinely new message, and two returns a fraction of a second
    apart — which is exactly what a test, a retry or an impatient reviewer
    produces — would share a to-the-second key and silently collapse into one.
    One recorded return, one notification.
    """
    from .models import Appraisal, AppraisalAuditLog

    target = (appraisal.employee
              if to_status in (Appraisal.Status.GOAL_SETTING,
                               Appraisal.Status.SELF_ASSESSMENT)
              else appraisal.supervisor)
    entry = appraisal.audit_entries.filter(
        action=AppraisalAuditLog.Action.RETURNED).order_by("-created_at").first()
    key = f"apr-{appraisal.pk}-returned-{entry.pk if entry else 'now'}"
    _tell(target, Category.APPRAISAL_RETURNED,
          "Your appraisal has been sent back for changes",
          remarks or "It has been returned for changes.",
          appraisal, exclude=actor, key=key)


def on_closed(sender, appraisal, actor=None, **kwargs):
    _tell(appraisal.employee, Category.APPRAISAL_CLOSED,
          "Your appraisal is complete",
          f"{appraisal.cycle.name} is finished. Your feedback, development "
          f"plan and any agreed training are on your record.",
          appraisal, exclude=actor, key=f"apr-{appraisal.pk}-closed")


def on_training_decided(sender, appraisal, actor=None, training=None, **kwargs):
    """
    The employee hears what happened to a request they made. A declined request
    nobody hears about is how people stop asking.
    """
    if training is None:
        return
    _tell(appraisal.employee, Category.APPRAISAL_TRAINING_DECIDED,
          f"Training request {training.get_status_display().lower()}",
          f"“{training.title}” — {training.get_status_display()}."
          + (f" {training.decision_note}" if training.decision_note else ""),
          appraisal, exclude=actor,
          key=f"apr-{appraisal.pk}-training-{training.pk}-{training.status}")


_CONNECTIONS = (
    (events.APPRAISAL_OPENED, on_opened, "appraisal.opened"),
    (events.GOALS_SUBMITTED, on_goals_submitted, "appraisal.goals_submitted"),
    (events.GOALS_APPROVED, on_goals_approved, "appraisal.goals_approved"),
    (events.SELF_ASSESSMENT_DUE, on_self_assessment_due, "appraisal.self_due"),
    (events.REVIEW_PENDING, on_review_pending, "appraisal.review_pending"),
    (events.COMMITTEE_REVIEW, on_committee_review, "appraisal.committee"),
    (events.FEEDBACK_READY, on_feedback_ready, "appraisal.feedback"),
    (events.APPRAISAL_RETURNED, on_returned, "appraisal.returned"),
    (events.APPRAISAL_CLOSED, on_closed, "appraisal.closed"),
    (events.TRAINING_DECIDED, on_training_decided, "appraisal.training"),
)


def connect():
    """Idempotent: `dispatch_uid` makes a second call a no-op rather than a
    second delivery of everything."""
    for signal, receiver, uid in _CONNECTIONS:
        signal.connect(receiver, dispatch_uid=uid)


def disconnect():
    """Used by tests that need the seam bare. Not called in production."""
    for signal, receiver, uid in _CONNECTIONS:
        signal.disconnect(receiver, dispatch_uid=uid)
