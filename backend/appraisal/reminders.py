"""
Appraisal deadline reminders (Phase RELEASE).

WHY THIS EXISTS
---------------
The event notifications tell somebody the moment it becomes their turn. That is
necessary and not sufficient: an appraisal stage lasts weeks, and a person told
once on day one will have forgotten by day twenty. The cycle already stores
three advisory deadlines and, until now, nothing read them — so the dates were
decoration.

WHAT IT WILL NOT DO
-------------------
It does not chase people daily. Three nudges per deadline — a week out, three
days out, and on the day — and then it stops, because a reminder that arrives
every morning is one people filter, and the filter catches the last one too.

It also never escalates to somebody's manager's manager. Appraisal is a
conversation between two people; turning a late self-assessment into an
escalation chain changes what the process is for. Lateness is visible on HR's
cycle dashboard, which is where a human can decide whether it matters.

DEADLINES ARE ADVISORY, AND STAY ADVISORY
-----------------------------------------
Nothing here blocks, locks or penalises. A cycle with no deadline set sends no
reminders for that stage rather than inventing one.

SAFE TO RUN TWICE
-----------------
Every send carries an idempotency key of (appraisal, stage, days-out), so a
second run the same afternoon sends nothing, and a retry after a failed night
sends each thing once.
"""
import logging

from django.utils import timezone

from notifications.models import Category

from .models import Appraisal
from .services import notify_user

logger = logging.getLogger("appraisal")

Status = Appraisal.Status

# How many days before a deadline to nudge. Three, then silence.
LEAD_DAYS = (7, 3, 0)

# Which deadline governs which stage, and who is chased for it. Only stages
# with a real owner and a real date appear here — there is no "everybody"
# reminder, because a reminder addressed to nobody in particular is ignored by
# everybody in particular.
STAGE_DEADLINES = (
    # (stages, cycle field, recipient attribute, subject)
    ((Status.GOAL_SETTING,), "goal_setting_deadline", "employee",
     "Your goals are due"),
    ((Status.GOAL_APPROVAL,), "goal_setting_deadline", "supervisor",
     "Goals are waiting for your approval"),
    ((Status.SELF_ASSESSMENT,), "self_assessment_deadline", "employee",
     "Your self assessment is due"),
    ((Status.SUPERVISOR_REVIEW,), "review_deadline", "supervisor",
     "An appraisal review is due"),
)


def _phrase(days):
    if days > 1:
        return f"in {days} days"
    if days == 1:
        return "tomorrow"
    if days == 0:
        return "today"
    return f"{abs(days)} day{'' if abs(days) == 1 else 's'} ago"


def run_deadline_reminders(today=None):
    """
    Nudge whoever owns a stage that has a deadline coming up.

    Returns the number of notifications sent, so the command can report it and
    a silent night is distinguishable from a night that ran and found nothing.
    """
    today = today or timezone.localdate()
    sent = 0

    for stages, field, who, subject in STAGE_DEADLINES:
        rows = (Appraisal.objects
                .filter(status__in=stages, cycle__status="active")
                .exclude(**{f"cycle__{field}__isnull": True})
                .select_related("cycle", "employee", "supervisor"))
        for appraisal in rows:
            deadline = getattr(appraisal.cycle, field)
            days_out = (deadline - today).days
            if days_out not in LEAD_DAYS:
                continue
            target = getattr(appraisal, who, None)
            if target is None or not target.is_active:
                continue
            body = (f"{appraisal.cycle.name}: this is due {_phrase(days_out)} "
                    f"({deadline}).")
            if who == "supervisor":
                body = f"{appraisal.employee_name} — {body}"
            result = notify_user(
                target, Category.APPRAISAL_DEADLINE_REMINDER, subject, body,
                appraisal,
                idempotency_key=f"apr-{appraisal.pk}-{field}-{days_out}")
            if result is not None:
                sent += 1
    return sent


def run_all(today=None):
    return {"deadline_reminders": run_deadline_reminders(today=today)}
