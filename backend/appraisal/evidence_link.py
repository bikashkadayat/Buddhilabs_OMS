"""
How the appraisal module consumes task evidence (Phase APM-02).

THIS MODULE COMPUTES NOTHING
----------------------------
Not one metric is calculated here. Every figure comes from the Phase T6 evidence
registry, which asks the task module's own provider — the same code that answers
the task module's own screens. There is exactly one place these numbers are
produced, and it is not this one.

That is the whole point of the instruction not to rebuild evidence collection. A
second implementation would drift from the first within a release, and the two
would disagree in front of the person being appraised, which is the worst
possible place to discover it.

HOW IT REACHES THE TASK MODULE — AND WHY NOT DIRECTLY
------------------------------------------------------
Through `evidence.registry`, the contract app. This module imports the CONTRACT,
never `tasks`. Two consequences worth having:

  * Nothing in the task module changes, or needs to know this module exists.
  * When memo, leave or attendance evidence is wired up, `collect_all` starts
    returning it and the appraisal pack grows with no change here.

Historical snapshots are read through `apps.get_model`, deliberately, rather
than by importing `tasks.models`: it keeps the dependency to a single runtime
lookup that Django resolves, and it means this file has no import-time knowledge
of the task module at all.

FROZEN, NOT LIVE
----------------
`attach_evidence` stores what the source said at that moment. An appraisal is a
conversation about a fixed period held on a particular day; evidence that shifted
underneath it would leave the figure in the record and the figure in the
discussion no longer matching, and the person appraised with no way to show what
they were actually shown.
"""
import logging

from django.apps import apps
from django.utils import timezone

from evidence.registry import available, collect, collect_all
from evidence.schema import CONTRACT_VERSION, PeriodType, Source

logger = logging.getLogger("appraisal")

# The seven figures the specification asks the appraisal to display. They are
# metric KEYS from the task provider's contract — named here so a change to the
# contract shows up as a missing key rather than a silently empty panel.
HEADLINE_KEYS = [
    "tasks_assigned",
    "tasks_completed",
    "task_completion_percent",
    "on_time_percent",
    "review_participation",
    "evidence_uploaded",
    "checklist_completion",
]


def collect_task_evidence(user, period_start, period_end,
                          period_type=PeriodType.ANNUAL, queryset=None):
    """
    Ask the registry for this person's task evidence over the appraisal window.

    Returns the `EvidenceSet` the task provider produced, or None when the task
    source has no provider registered — which is a real state (a deployment with
    the task module disabled) and must be distinguishable from "this person did
    nothing".
    """
    return collect(Source.TASK, user, period_start, period_end, period_type,
                   queryset=queryset)


def collect_all_evidence(user, period_start, period_end,
                         period_type=PeriodType.ANNUAL):
    """
    Every registered source. Today that is the task module alone; the six others
    are reported as unavailable rather than omitted, so an appraisal pack says
    "leave evidence is not connected" instead of implying there was none.
    """
    return collect_all(user, period_start, period_end, period_type)


def evidence_sources():
    """Which sources can answer, for the UI to explain what it is showing."""
    return available()


def headline_metrics(evidence_set):
    """
    The seven the appraisal displays, in the specification's order, each with the
    definition and denominator the contract carries.

    A key the contract no longer provides comes back with `value: None` and
    `missing: True` rather than being dropped — a panel that silently loses a row
    is how somebody ends up appraised against six figures believing they saw
    seven.
    """
    if evidence_set is None:
        return []
    by_key = {metric.key: metric for metric in evidence_set.metrics}
    out = []
    for key in HEADLINE_KEYS:
        metric = by_key.get(key)
        if metric is None:
            logger.warning("Evidence contract no longer provides %r.", key)
            out.append({"key": key, "label": key.replace("_", " ").title(),
                        "unit": "", "value": None, "definition": "",
                        "basis_of": None, "missing": True})
        else:
            row = metric.as_dict()
            row["missing"] = False
            out.append(row)
    return out


def low_volume(evidence_set):
    """
    Whether the task module flagged this window as too thin to read.

    Surfaced prominently in the appraisal pack: percentages over a handful of
    tasks are noise, and an appraisal is precisely where noise gets mistaken for
    a pattern about a person.
    """
    if evidence_set is None:
        return False
    for metric in evidence_set.metrics:
        if metric.key == "low_volume":
            return bool(metric.value)
    return False


def snapshots_for(user, period_start, period_end, period_type=None, limit=24):
    """
    The frozen daily/monthly/quarterly/annual rows the task module already
    writes (Phase T6.3), read through the app registry.

    Read-only, and read at arm's length: this module never writes a snapshot and
    never asks the task module to write one.
    """
    Snapshot = apps.get_model("tasks", "EmployeeTaskEvidenceSnapshot")
    rows = Snapshot.objects.filter(
        employee=user, snapshot_date__gte=period_start,
        snapshot_date__lte=period_end)
    if period_type:
        rows = rows.filter(period_type=period_type)
    return list(rows.order_by("-snapshot_date")[:limit])


def attach_evidence(appraisal, actor, *, goal=None, note="", queryset=None,
                    period_type=PeriodType.ANNUAL):
    """
    Freeze the current task evidence onto an appraisal as a citation.

    Idempotent per (appraisal, goal, source, window): re-attaching does not
    create a second citation of the same thing, because two rows of the same
    evidence captured minutes apart would just be noise in the record. Deliberate
    re-capture after the period changes produces a genuinely different window and
    therefore a genuinely different row.
    """
    from .models import EvidenceReference

    cycle = appraisal.cycle
    evidence_set = collect_task_evidence(
        appraisal.employee, cycle.period_start, cycle.period_end,
        period_type=period_type, queryset=queryset)
    if evidence_set is None:
        return None

    payload = evidence_set.as_dict()
    reference, _created = EvidenceReference.objects.get_or_create(
        appraisal=appraisal,
        goal=goal,
        source=payload["source"],
        period_start=cycle.period_start,
        period_end=cycle.period_end,
        defaults={
            "contract_version": payload.get("contract_version",
                                            CONTRACT_VERSION),
            "period_type": payload.get("period_type",
                                       getattr(period_type, "value",
                                               period_type)),
            "metrics": payload["metrics"],
            "disclaimer": payload["disclaimer"],
            "note": note,
            "captured_at": timezone.now(),
            "attached_by": actor,
            "attached_by_name": (actor.get_full_name() or actor.username)
            if actor else "",
        },
    )
    return reference
