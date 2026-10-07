"""
The three role dashboards and the six reports (APM-02).

WHAT NONE OF THESE PRODUCE
--------------------------
No score, no ranking, no leaderboard, no ordering of people by performance.
Every list of people in this file is ordered BY NAME, and the counts are counts
of PROCESS STATE — how many appraisals are at which stage, how many training
needs are unfunded — not measures of anybody.

"Promotion readiness" and "succession planning" appear because the specification
asks for them, and both are lists of what a HUMAN recorded, shown with the
rationale that human wrote. Neither is computed, and a person with no
recommendation is absent from the list rather than appearing as a negative.
"""
from collections import Counter, OrderedDict

from django.db.models import Count, Q
from django.utils import timezone

from .models import (Appraisal, CompetencyRating, DevelopmentPlan,
                     TrainingPlan)

Status = Appraisal.Status
OPEN_STATUSES = [s for s in Appraisal.LADDER if s != Status.CLOSED]


def _percent(part, whole):
    return round(100 * part / whole) if whole else 0


def _by_stage(queryset):
    """Counts per stage, every stage present — including the empty ones.

    A stage that vanishes when nothing is in it makes a cycle look further along
    than it is; "nothing is with the committee" is information.
    """
    counts = dict(queryset.values_list("status").annotate(n=Count("id")))
    return [
        {"status": value, "label": Appraisal.Status(value).label,
         "count": counts.get(value, 0)}
        for value in Appraisal.LADDER
    ]


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------
def employee_dashboard(queryset, user):
    """
    What is on this person's own plate. Their goals, their evidence, their
    stage — and nothing about anybody else.
    """
    mine = queryset.filter(employee=user)
    current = mine.exclude(status=Status.CLOSED).first()

    goals, development, training = [], [], []
    if current:
        goals = [
            {"id": str(goal.id), "objective": goal.objective,
             "weight": goal.weight, "progress_percent": goal.progress_percent,
             "due_date": goal.due_date, "achievement": goal.achievement}
            for goal in current.goals.all()
        ]
        development = [
            {"area": row.area, "action": row.action, "status": row.status,
             "target_date": row.target_date}
            for row in current.development_plans.all()
        ]
        training = [
            # `kind` travels with the row: "Mentoring from the finance lead"
            # and "ISO 27001" are different asks, and an employee reading their
            # own plan should not have to infer which from the title.
            {"title": row.title, "kind": row.kind,
             "kind_label": row.get_kind_display(), "priority": row.priority,
             "status": row.status, "target_period": row.target_period,
             "mentor_name": row.mentor_name}
            for row in current.training_plans.all()
        ]

    return {
        "current_appraisal": str(current.id) if current else None,
        "cycle": current.cycle.name if current else None,
        "stage": current.get_status_display() if current else None,
        "stage_index": current.stage_index if current else None,
        "total_stages": len(Appraisal.LADDER),
        "awaiting_me": bool(current
                            and current.status == Status.SELF_ASSESSMENT),
        "goals": goals,
        "goal_weight_total": current.goal_weight_total if current else 0,
        "development_plan": development,
        "training_recommendations": training,
        "history": [
            {"id": str(row.id), "cycle": row.cycle.name,
             "status": row.get_status_display(), "closed_at": row.closed_at}
            for row in mine.filter(status=Status.CLOSED)[:10]
        ],
        "note": "Your own appraisal record. Nothing here is scored or compared "
                "with anybody else's.",
    }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
def manager_dashboard(queryset, user):
    """
    A supervisor's view of their direct reports.

    `pending_reviews` is what is WITH THEM — the actionable queue. Team progress
    is by stage, not by anybody's figures, and the team list is alphabetical.
    """
    team = queryset.filter(supervisor=user)
    pending = team.filter(
        status__in=[Status.GOAL_SETTING, Status.MID_YEAR,
                    Status.SUPERVISOR_REVIEW, Status.FINAL_REVIEW,
                    Status.DEVELOPMENT_PLAN, Status.TRAINING_PLAN])

    development_needs = Counter()
    for appraisal in team.prefetch_related("development_plans__competency"):
        for row in appraisal.development_plans.all():
            if row.competency_id:
                development_needs[row.competency.name] += 1

    return {
        "team_size": team.count(),
        "pending_reviews": pending.count(),
        "by_stage": _by_stage(team),
        "completed": team.filter(status=Status.CLOSED).count(),
        "completion_percent": _percent(
            team.filter(status=Status.CLOSED).count(), team.count()),
        # Alphabetical. A team list sorted by anything else is a ranking.
        "team": sorted([
            {"appraisal_id": str(row.id), "employee": row.employee_name,
             "stage": row.get_status_display(), "stage_index": row.stage_index,
             # Stated, not inferred. The supervisor's page splits closed
             # appraisals from live ones, and deriving that from the display
             # LABEL ("Closed") would break silently the day somebody rewords a
             # stage — a row would quietly move from Completed Reviews back into
             # the live list with no error anywhere.
             "is_closed": row.status == Status.CLOSED,
             "goals": len(row.goals.all()),
             "goal_weight_total": row.goal_weight_total,
             "awaiting_me": row.status in (
                 Status.GOAL_SETTING, Status.MID_YEAR, Status.SUPERVISOR_REVIEW,
                 Status.FINAL_REVIEW, Status.DEVELOPMENT_PLAN,
                 Status.TRAINING_PLAN)}
            for row in team
        ], key=lambda r: r["employee"].lower()),
        # Aggregated across the team so a shared gap is visible — a count of
        # development ACTIONS by area, not a judgement of individuals.
        "development_needs": [
            {"area": name, "count": count}
            for name, count in development_needs.most_common(10)
        ],
        "note": "Your direct reports, ordered by name. Stages and counts only — "
                "nothing here scores or ranks anybody.",
    }


# ---------------------------------------------------------------------------
# HR
# ---------------------------------------------------------------------------
def hr_dashboard(queryset, today=None):
    """
    Cycle progress across the organisation.

    Every figure is about the PROCESS: how far the round has got, where it is
    stuck, what training has been asked for. The two people-shaped lists —
    promotion readiness and succession — carry only what a human recorded,
    with their reasons.
    """
    today = today or timezone.localdate()
    total = queryset.count()
    closed = queryset.filter(status=Status.CLOSED).count()

    by_department = OrderedDict()
    for row in queryset.values("department_name").annotate(
            total=Count("id"),
            closed=Count("id", filter=Q(status=Status.CLOSED))).order_by(
            "department_name"):
        name = row["department_name"] or "Unassigned"
        by_department[name] = {
            "department": name, "total": row["total"], "closed": row["closed"],
            "completion_percent": _percent(row["closed"], row["total"]),
        }

    training = TrainingPlan.objects.filter(appraisal__in=queryset)
    training_by_status = dict(
        training.values_list("status").annotate(n=Count("id")))
    # GROUPED, not one COUNT per choice. Eleven values across three breakdowns
    # would otherwise be eleven round trips for figures the database can group
    # in three — and every choice added later would silently cost another. The
    # dicts are read with .get(..., 0) below so a value nobody has used yet
    # still renders as a zero rather than vanishing from the chart.
    training_by_kind = dict(
        training.values_list("kind").annotate(n=Count("id")))
    training_by_priority = dict(
        training.values_list("priority").annotate(n=Count("id")))
    readiness_counts = dict(
        queryset.exclude(promotion_readiness="")
        .values_list("promotion_readiness").annotate(n=Count("id")))

    promotion = queryset.exclude(promotion_readiness="").select_related(
        "employee", "cycle")
    succession = queryset.exclude(successor_for="").select_related("employee")

    # How long each appraisal has been sitting at a review stage.
    #
    # Computed HERE rather than on the client for one reason: the reports export
    # to CSV and PDF from the server, and a figure the screen works out for
    # itself is a figure the export cannot reproduce. Two numbers for one
    # question is worse than none.
    #
    # It is a measure of the PROCESS, not of a person — "this record has waited
    # eleven days" — so it is ordered by the WAIT, which is the only ordering on
    # this dashboard that is not a ranking of people. The reviewer is named
    # because a delay nobody owns is a delay nobody clears.
    review_stages = [Status.SUPERVISOR_REVIEW, Status.COMMITTEE,
                     Status.FINAL_REVIEW]
    delays = []
    for row in queryset.filter(status__in=review_stages).select_related("cycle"):
        waiting = (today - timezone.localdate(row.updated_at)).days
        delays.append({
            "employee": row.employee_name,
            "department": row.department_name or "—",
            "stage": row.get_status_display(),
            "with_whom": row.supervisor_name or "—",
            "days_waiting": max(0, waiting),
        })
    delays.sort(key=lambda r: (-r["days_waiting"], r["employee"].lower()))

    development = DevelopmentPlan.objects.filter(appraisal__in=queryset)
    development_by_status = dict(
        development.values_list("status").annotate(n=Count("id")))

    return {
        "cycle_completion": {
            "total": total, "closed": closed,
            "percent": _percent(closed, total),
            "in_progress": total - closed,
        },
        "by_stage": _by_stage(queryset),
        "by_department": list(by_department.values()),
        "review_status": {
            "with_supervisor": queryset.filter(
                status=Status.SUPERVISOR_REVIEW).count(),
            "with_committee": queryset.filter(status=Status.COMMITTEE).count(),
            "awaiting_self_assessment": queryset.filter(
                status=Status.SELF_ASSESSMENT).count(),
        },
        "training_needs": {
            "total": training.count(),
            "identified": training_by_status.get(
                TrainingPlan.Status.IDENTIFIED, 0),
            "approved": training_by_status.get(TrainingPlan.Status.APPROVED, 0),
            "by_kind": [
                {"kind": value, "label": label,
                 "count": training_by_kind.get(value, 0)}
                for value, label in TrainingPlan.Kind.choices
            ],
            "by_priority": [
                {"priority": value, "label": label,
                 "count": training_by_priority.get(value, 0)}
                for value, label in TrainingPlan.Priority.choices
            ],
            "top_requests": [
                {"title": row["title"], "count": row["n"]}
                for row in training.values("title").annotate(
                    n=Count("id")).order_by("-n")[:10]
            ],
        },
        # Human recommendations only, alphabetical, each with its rationale.
        # Somebody with no recommendation is ABSENT rather than listed as a no —
        # "not considered" and "not ready" are different, and conflating them
        # would put a negative on every record that never reached the question.
        "promotion_readiness": sorted([
            {"employee": row.employee_name,
             "department": row.department_name or "—",
             "readiness": row.promotion_readiness,
             "readiness_label": row.get_promotion_readiness_display(),
             "rationale": row.promotion_rationale,
             "recommended_in": row.cycle.name}
            for row in promotion
        ], key=lambda r: r["employee"].lower()),
        # A count per recorded answer, so HR can see the shape of the round
        # without the list being reordered by it. "Not considered" is
        # deliberately not a bucket here: it is everybody else, and giving the
        # absence a tile invites reading it as a fourth verdict.
        "promotion_by_readiness": [
            {"value": value, "label": label,
             "count": readiness_counts.get(value, 0)}
            for value, label in Appraisal.PromotionReadiness.choices
        ],
        "succession": sorted([
            {"employee": row.employee_name, "role": row.successor_for,
             "department": row.department_name or "—"}
            for row in succession
        ], key=lambda r: r["employee"].lower()),
        # Ordered by how long the record has waited, longest first — the one
        # ordering here that ranks WORK rather than people.
        "review_delays": delays[:20],
        "longest_wait_days": delays[0]["days_waiting"] if delays else 0,
        "development_plans": {
            "total": development.count(),
            "by_status": [
                {"status": value, "label": label,
                 "count": development_by_status.get(value, 0)}
                for value, label in DevelopmentPlan.Status.choices
            ],
            "top_areas": [
                {"area": row["area"], "count": row["n"]}
                for row in development.values("area").annotate(
                    n=Count("id")).order_by("-n", "area")[:10]
            ],
        },
        "note": "Process progress across the organisation. Promotion and "
                "succession entries are recommendations recorded by people, "
                "with their reasons — nothing here is computed or ranked.",
    }


# ---------------------------------------------------------------------------
# Reports (six, as specified) — same envelope as the task module's, so one
# table component renders them all and CSV/PDF export is written once.
# ---------------------------------------------------------------------------
def report_appraisal_summary(queryset, today=None):
    rows = sorted([
        {"employee": row.employee_name,
         "department": row.department_name or "—",
         "cycle": row.cycle.name,
         "supervisor": row.supervisor_name or "—",
         "stage": row.get_status_display(),
         "goals": len(row.goals.all()),
         "goal_weight_total": row.goal_weight_total,
         "closed_at": row.closed_at.date() if row.closed_at else None}
        for row in queryset.select_related("cycle").prefetch_related("goals")
    ], key=lambda r: r["employee"].lower())
    return {
        "columns": [
            {"key": "employee", "label": "Employee"},
            {"key": "department", "label": "Department"},
            {"key": "cycle", "label": "Cycle"},
            {"key": "supervisor", "label": "Supervisor"},
            {"key": "stage", "label": "Stage"},
            {"key": "goals", "label": "Goals", "numeric": True},
            {"key": "goal_weight_total", "label": "Weight %", "numeric": True},
            {"key": "closed_at", "label": "Closed"},
        ],
        "rows": rows,
        "summary": {"appraisals": len(rows),
                    "note": "Ordered by name. Not scored or ranked."},
    }


def report_department_summary(queryset, today=None):
    data = hr_dashboard(queryset, today=today)
    return {
        "columns": [
            {"key": "department", "label": "Department"},
            {"key": "total", "label": "Appraisals", "numeric": True},
            {"key": "closed", "label": "Closed", "numeric": True},
            {"key": "completion_percent", "label": "Completion %",
             "numeric": True},
        ],
        "rows": data["by_department"],
        "summary": {"departments": len(data["by_department"]),
                    "note": "Process completion, not performance."},
    }


def report_goal_completion(queryset, today=None):
    """
    Goal-by-goal, with the employee's OWN account of progress and achievement.

    Deliberately row-level rather than an average per person: an average of
    weighted goal progress is a performance score, which this module does not
    produce. The reader sees the goals and forms their own view.
    """
    rows = []
    for appraisal in queryset.select_related("cycle").prefetch_related("goals"):
        for goal in appraisal.goals.all():
            rows.append({
                "employee": appraisal.employee_name,
                "cycle": appraisal.cycle.name,
                "objective": goal.objective,
                "weight": goal.weight,
                "progress_percent": goal.progress_percent,
                "due_date": goal.due_date,
                "achievement": (goal.achievement or "")[:300],
            })
    rows.sort(key=lambda r: (r["employee"].lower(), -r["weight"]))
    return {
        "columns": [
            {"key": "employee", "label": "Employee"},
            {"key": "cycle", "label": "Cycle"},
            {"key": "objective", "label": "Objective"},
            {"key": "weight", "label": "Weight %", "numeric": True},
            {"key": "progress_percent", "label": "Progress %", "numeric": True},
            {"key": "due_date", "label": "Due"},
            {"key": "achievement", "label": "Achievement"},
        ],
        "rows": rows,
        "summary": {"goals": len(rows),
                    "note": "Progress is the employee's own account, reviewed "
                            "by their supervisor. Not computed, not averaged."},
    }


def report_training_needs(queryset, today=None):
    rows = []
    for row in TrainingPlan.objects.filter(
            appraisal__in=queryset).select_related("appraisal", "competency"):
        rows.append({
            "employee": row.appraisal.employee_name,
            "department": row.appraisal.department_name or "—",
            "title": row.title,
            "kind": row.get_kind_display(),
            "competency": row.competency.name if row.competency_id else "—",
            "mentor": row.mentor_name or "—",
            "priority": row.get_priority_display(),
            "status": row.get_status_display(),
            "target_period": row.target_period or "—",
        })
    rows.sort(key=lambda r: (r["employee"].lower(), r["title"].lower()))
    return {
        "columns": [
            {"key": "employee", "label": "Employee"},
            {"key": "department", "label": "Department"},
            {"key": "title", "label": "Training"},
            {"key": "kind", "label": "Type"},
            {"key": "competency", "label": "Competency"},
            {"key": "mentor", "label": "Mentor"},
            {"key": "priority", "label": "Priority"},
            {"key": "status", "label": "Status"},
            {"key": "target_period", "label": "Wanted"},
        ],
        "rows": rows,
        "summary": {"requests": len(rows)},
    }


def report_development_plans(queryset, today=None):
    rows = []
    for appraisal in queryset.prefetch_related("development_plans__competency"):
        for row in appraisal.development_plans.all():
            rows.append({
                "employee": appraisal.employee_name,
                "area": row.area,
                "action": (row.action or "")[:300],
                "competency": row.competency.name if row.competency_id else "—",
                "status": row.get_status_display(),
                "target_date": row.target_date,
                "accountable": row.accountable_name or "—",
            })
    rows.sort(key=lambda r: (r["employee"].lower(), r["area"].lower()))
    return {
        "columns": [
            {"key": "employee", "label": "Employee"},
            {"key": "area", "label": "Development Area"},
            {"key": "action", "label": "Action"},
            {"key": "competency", "label": "Competency"},
            {"key": "status", "label": "Status"},
            {"key": "target_date", "label": "Target"},
            {"key": "accountable", "label": "Accountable"},
        ],
        "rows": rows,
        "summary": {"actions": len(rows)},
    }


def report_promotion_readiness(queryset, today=None):
    """
    Who a HUMAN has recommended, and why.

    Not a computed readiness score and not a shortlist ordered by anything —
    alphabetical, with the recorded answer and its rationale beside each name.
    Somebody with no recommendation is absent rather than listed as a negative,
    and the three answers are NOT sorted best-first: ordering by readiness is a
    ranking whatever the column is called.
    """
    rows = sorted([
        {"employee": row.employee_name,
         "department": row.department_name or "—",
         "cycle": row.cycle.name,
         "readiness": row.get_promotion_readiness_display(),
         "recommended_by": row.supervisor_name or "—",
         "rationale": (row.promotion_rationale or "")[:400],
         "successor_for": row.successor_for or "—"}
        for row in queryset.exclude(
            promotion_readiness="").select_related("cycle")
    ], key=lambda r: r["employee"].lower())
    return {
        "columns": [
            {"key": "employee", "label": "Employee"},
            {"key": "department", "label": "Department"},
            {"key": "cycle", "label": "Cycle"},
            {"key": "readiness", "label": "Readiness"},
            {"key": "recommended_by", "label": "Recommended By"},
            {"key": "rationale", "label": "Rationale"},
            {"key": "successor_for", "label": "Successor For"},
        ],
        "rows": rows,
        "summary": {
            "recommendations": len(rows),
            "note": "Human recommendations with their stated reasons. Nothing "
                    "here is computed, scored or ranked, and anyone without a "
                    "recommendation is simply absent — not marked as unready.",
        },
    }


REPORTS = OrderedDict([
    ("appraisal-summary", {
        "label": "Appraisal Summary",
        "description": "Every appraisal, its stage and its goals.",
        "build": report_appraisal_summary,
    }),
    ("department-summary", {
        "label": "Department Summary",
        "description": "Cycle completion by department.",
        "build": report_department_summary,
    }),
    ("goal-completion", {
        "label": "Goal Completion",
        "description": "Goal by goal, with the employee's own account.",
        "build": report_goal_completion,
    }),
    ("training-needs", {
        "label": "Training Needs",
        "description": "Training identified, and where it has got to.",
        "build": report_training_needs,
    }),
    ("development-plans", {
        "label": "Development Plans",
        "description": "Agreed development actions and who is accountable.",
        "build": report_development_plans,
    }),
    ("promotion-readiness", {
        "label": "Promotion Readiness",
        "description": "Human recommendations, with their rationale.",
        "build": report_promotion_readiness,
    }),
])
