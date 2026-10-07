"""
The KPI registry (Phase T5.9).

WHY A REGISTRY RATHER THAN NUMBERS COMPUTED WHERE THEY ARE SHOWN
----------------------------------------------------------------
"Completion %" appears on the executive dashboard, in the department view, in
two reports and in the evidence layer. Computed at each of those five places it
would be five definitions, and the first time two of them disagreed on screen
nobody would be able to say which was right. Here, each KPI is defined ONCE —
its formula, its unit, its direction, and the sentence explaining it — and every
surface renders the same object.

`definition` is not documentation. It is shipped to the client and rendered
beside the number, because a percentage with no stated denominator is the most
reliable way to have a metric misread in a meeting.

DIRECTION IS DECLARED, NOT INFERRED
-----------------------------------
`higher_is_better` exists so the UI can colour a movement without guessing.
Overdue % going up is bad and completion % going up is good, and a component
that inferred that from the name would be wrong the first time somebody added
"Blocked %".

WHAT THIS REGISTRY DELIBERATELY HAS NO ENTRY FOR
------------------------------------------------
There is no employee score, no composite index, no weighting of one metric
against another. Phase T5's instruction is explicit — display metrics only, do
not generate performance scores, do not rank or evaluate employees — and the
absence is enforced by a test, not left to good intentions. A composite is the
exact thing that turns a work-management tool into a scoring tool: the moment
five honest numbers become one number, it gets compared, and nobody can explain
what it means.

DEPARTMENTS MAY BE RANKED. PEOPLE MAY NOT.
------------------------------------------
Part 1 asks for a department ranking, and that is a different act: a department
is a unit of work with a head accountable for it, not a person being appraised.
`tasks.analytics.department_ranking` sorts departments; nothing anywhere sorts
employees by a metric — see `employee_analytics`, which orders by NAME.
"""
from collections import OrderedDict


def _percent(part, whole):
    """Integer percent, and 0 rather than a division by zero on an empty set."""
    return round(100 * part / whole) if whole else 0


class KPI:
    """One metric: how to compute it, what it is called, and what it means."""

    __slots__ = ("key", "label", "unit", "higher_is_better", "definition", "compute")

    def __init__(self, key, label, unit, higher_is_better, definition, compute):
        self.key = key
        self.label = label
        self.unit = unit
        self.higher_is_better = higher_is_better
        self.definition = definition
        self.compute = compute

    def evaluate(self, stats):
        return self.compute(stats)

    def as_dict(self, stats=None):
        payload = {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "definition": self.definition,
        }
        if stats is not None:
            payload["value"] = self.evaluate(stats)
        return payload


# Every KPI reads the same `stats` dict, produced by
# tasks.analytics.metric_inputs. One shape in, so a KPI can be added without
# any caller learning what it needs.
REGISTRY = OrderedDict()


def register(kpi):
    REGISTRY[kpi.key] = kpi
    return kpi


register(KPI(
    "completion_percent", "Completion", "%", True,
    "Tasks completed or closed, as a share of every task in scope. Drafts are "
    "excluded — a task nobody has been given yet is not work in progress.",
    lambda s: _percent(s["completed"], s["total"]),
))

register(KPI(
    "on_time_percent", "On-Time Delivery", "%", True,
    "Of the tasks that finished AND had a due date, the share finished on or "
    "before it. Tasks with no due date are excluded rather than counted as "
    "on time — there was nothing to be on time for.",
    lambda s: _percent(s["on_time"], s["completed_with_due_date"]),
))

register(KPI(
    "overdue_percent", "Overdue", "%", False,
    "Open tasks past their due date, as a share of all open tasks. Completed "
    "work is never counted as overdue, however late it finished — the person "
    "did their part.",
    lambda s: _percent(s["overdue"], s["open"]),
))

register(KPI(
    "blocked_percent", "Blocked", "%", False,
    "Open tasks marked Blocked, as a share of all open tasks. A high figure is "
    "a dependency problem, not a productivity one.",
    lambda s: _percent(s["blocked"], s["open"]),
))

register(KPI(
    "rework_percent", "Rework", "%", False,
    "Tasks that were returned by a reviewer at least once, as a share of every "
    "task ever submitted for review. Counted from the timeline, so a task "
    "returned twice and then approved still counts as rework.",
    lambda s: _percent(s["reworked"], s["ever_submitted"]),
))

register(KPI(
    "review_delay_percent", "Review Delay", "%", False,
    "Submitted work that has been waiting longer than the review threshold, as "
    "a share of everything currently awaiting review. This is a queue measure, "
    "not a judgement of any reviewer.",
    lambda s: _percent(s["review_delayed"], s["awaiting_review"]),
))

register(KPI(
    "review_efficiency_percent", "Review Efficiency", "%", True,
    "Reviews decided within the threshold, as a share of all review decisions "
    "made in the period. The complement of a delay, over decisions actually "
    "taken rather than over the current queue.",
    lambda s: _percent(s["reviews_on_time"], s["reviews_decided"]),
))


def evaluate_all(stats, keys=None):
    """Every KPI (or a named subset) against one stats dict, ready to render."""
    chosen = [REGISTRY[key] for key in keys] if keys else list(REGISTRY.values())
    return [kpi.as_dict(stats) for kpi in chosen]


def catalogue():
    """The registry itself, with no values — for a definitions page."""
    return [kpi.as_dict() for kpi in REGISTRY.values()]
