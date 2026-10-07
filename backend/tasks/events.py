"""
The notification seam (Phase T3, Part 7).

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This module declares the six task events the specification names and emits them
at the points they occur. It DELIVERS NOTHING. There are no receivers connected
here, no email, no bell entry, no template — by instruction, T3 prepares the
hooks and a later phase decides what listens.

The in-app and email notifications that Phase T1 and T2 already send are
UNCHANGED and are not routed through here. They are sent directly by
tasks.workflow via notifications.dispatcher, and they keep working exactly as
before. Rewiring them through a signal would have been a behavioural change to a
working notification path, which is not what "prepare hooks/events only" asks
for. When a delivery layer is built, the choice of which of the two paths to
retire is a decision for that phase, taken deliberately.

WHY DJANGO SIGNALS AND NOT A CUSTOM BUS
---------------------------------------
Receivers can be connected from anywhere without this module knowing about them,
which is the whole point of a seam; the project already depends on Django's
dispatcher; and a receiver connected in one app's `ready()` is a pattern this
codebase already uses (notifications/signals.py). A hand-rolled registry would
be a second mechanism doing the same job.

EMITTING CAN NEVER BREAK A TRANSITION
-------------------------------------
`emit()` swallows and logs everything a receiver raises. A workflow transition is
the real work; a listener that fails must not roll it back. This is the opposite
of the rule for the audit trail — which is allowed to fail a transition, because
a state change with no record of it is an evidence gap, whereas a missed
notification is an inconvenience.
"""
import logging

import django.dispatch

logger = logging.getLogger("tasks")

# Every signal carries `sender=Task`, `task=<Task>`, `actor=<User|None>`, and
# whatever else the emit point knows. Receivers must accept **kwargs.
TASK_CREATED = task_created = django.dispatch.Signal()
"""Raised. kwargs: task, actor. Everybody creates tasks now (Phase
TASK-MANAGEMENT-ASANA-MODEL), so the people accountable for a department's work
learn about the work being taken on in it without having to go looking."""

TASK_ASSIGNED = task_assigned = django.dispatch.Signal()
"""Handed to its assignees. kwargs: task, actor, assignees."""

TASK_REVIEW_REQUIRED = task_review_required = django.dispatch.Signal()
"""Submitted; a reviewer is now blocking. kwargs: task, actor, reviewer."""

TASK_COMPLETED = task_completed = django.dispatch.Signal()
"""Finished. kwargs: task, actor, closed (bool) — True once HR has verified it."""

TASK_OVERDUE = task_overdue = django.dispatch.Signal()
"""Past its due date and still open. kwargs: task, overdue_days. No actor: nobody did this."""

TASK_RETURNED = task_returned = django.dispatch.Signal()
"""Sent back for rework. kwargs: task, actor, remarks — always populated, the engine requires one."""

TASK_BLOCKED = task_blocked = django.dispatch.Signal()
"""Work cannot continue. kwargs: task, actor, reason — always populated."""


# The catalogue, for the tests and for whoever builds the delivery layer.
#
# Keyed by the specification's own SCREAMING_CASE names, because those are what
# a delivery layer's configuration will be written against — a preference row
# reading "TASK_OVERDUE" is legible to whoever has to support it, and a lowercase
# module attribute is not. The lowercase aliases above exist so emit points read
# as ordinary Python.
EVENTS = {
    "TASK_CREATED": task_created,
    "TASK_ASSIGNED": task_assigned,
    "TASK_REVIEW_REQUIRED": task_review_required,
    "TASK_COMPLETED": task_completed,
    "TASK_OVERDUE": task_overdue,
    "TASK_RETURNED": task_returned,
    "TASK_BLOCKED": task_blocked,
}


def emit(signal, task, **payload):
    """
    Send one event. Never raises.

    `send_robust` rather than `send`: it collects a receiver's exception instead
    of propagating it, so one broken listener cannot take down the transition
    that triggered it — nor stop the OTHER listeners from running, which plain
    `send` would.
    """
    from .models import Task

    try:
        results = signal.send_robust(sender=Task, task=task, **payload)
    except Exception:  # pragma: no cover - send_robust should not itself raise
        logger.exception("Task event dispatch failed for %s", getattr(task, "pk", None))
        return []

    for receiver, response in results:
        if isinstance(response, Exception):
            logger.error("Task event receiver %r failed: %s", receiver, response,
                         exc_info=response)
    return results


def emit_overdue(task, overdue_days):
    """
    The one event with no transition behind it: nothing happened, a date passed.

    Emitted by the `detect_overdue_tasks` management command rather than by any
    request, because "became overdue" is a property of the clock. See that
    command for why it is idempotent per day.
    """
    return emit(task_overdue, task, actor=None, overdue_days=overdue_days)
