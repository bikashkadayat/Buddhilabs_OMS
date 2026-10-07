"""
The appraisal notification seam.

WHY A SEAM AND NOT DIRECT CALLS
-------------------------------
The workflow engine's job is to change state and record it. What anybody is TOLD
about that is a separate concern, and keeping it separate is what makes a
notification failure unable to roll back a transition that has already happened.

This mirrors the task module's seam exactly (tasks/events.py, tasks/receivers.py)
rather than inventing a second mechanism — a digest, a Slack bridge or an export
can listen here later without the engine knowing.

ONE SIGNAL PER MOMENT SOMEBODY'S TURN CHANGES
---------------------------------------------
Not one per transition. Ten stages produce ten transitions, but several of them
change whose turn it is without anybody needing to hear: an appraisal moving
from Development Plan to Training Plan is the same two people continuing the
same conversation. A notification for every state change is how people learn to
filter the whole category out, and then miss the one that mattered.

EMITTING CAN NEVER BREAK A TRANSITION
-------------------------------------
`emit()` swallows and logs whatever a receiver raises. This is the opposite of
the rule for the audit trail, which IS allowed to fail a transition: a state
change with no record of it is an evidence gap, whereas a missed notification is
an inconvenience.
"""
import logging

import django.dispatch

logger = logging.getLogger("appraisal")

# Every signal carries `sender=Appraisal`, `appraisal=<Appraisal>` and
# `actor=<User|None>`. Receivers must accept **kwargs.
APPRAISAL_OPENED = django.dispatch.Signal()
"""HR has raised an appraisal. kwargs: appraisal, actor."""

GOALS_SUBMITTED = django.dispatch.Signal()
"""Objectives are with the supervisor for a decision. kwargs: appraisal, actor."""

GOALS_APPROVED = django.dispatch.Signal()
"""The supervisor accepted them. kwargs: appraisal, actor."""

SELF_ASSESSMENT_DUE = django.dispatch.Signal()
"""It is the employee's turn to write. kwargs: appraisal, actor."""

REVIEW_PENDING = django.dispatch.Signal()
"""A self-assessment is waiting on the supervisor. kwargs: appraisal, actor."""

COMMITTEE_REVIEW = django.dispatch.Signal()
"""The committee's turn. kwargs: appraisal, actor."""

FEEDBACK_READY = django.dispatch.Signal()
"""The final summary is written and the employee may read it. kwargs: appraisal, actor."""

APPRAISAL_RETURNED = django.dispatch.Signal()
"""Sent back a stage. kwargs: appraisal, actor, to_status, remarks."""

APPRAISAL_CLOSED = django.dispatch.Signal()
"""The cycle is complete for this person. kwargs: appraisal, actor."""

TRAINING_DECIDED = django.dispatch.Signal()
"""HR approved, scheduled or declined a request. kwargs: appraisal, actor, training."""


def emit(signal, appraisal, **kwargs):
    """
    Fire one signal. Never raises.

    `send_robust` collects receiver exceptions rather than propagating them, and
    they are logged here with the appraisal they came from — a listener that
    fails silently is worse than one that fails loudly, but neither may take the
    transition down with it.
    """
    from .models import Appraisal

    try:
        results = signal.send_robust(sender=Appraisal, appraisal=appraisal,
                                     **kwargs)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Appraisal signal dispatch failed for %s", appraisal.pk)
        return []
    for receiver, response in results:
        if isinstance(response, Exception):
            logger.exception("Appraisal receiver %r failed for %s",
                             receiver, appraisal.pk, exc_info=response)
    return results
