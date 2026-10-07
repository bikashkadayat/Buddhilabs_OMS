"""
The analytics and evidence API (Phase T5).

VISIBILITY IS THE FIRST THING THIS FILE DOES, NOT THE LAST
-----------------------------------------------------------
Every endpoint starts from `_scoped()`, which applies the SAME
`tasks.permissions.visible_task_filter` the task list uses. Analytics is where a
permission bug is least visible and most damaging: a number is an aggregate, so
a leak does not look like a leak — it looks like a slightly different total that
nobody can account for. Deriving the scope from the one existing rule means
there is no second definition to drift.

On top of that, three endpoints refuse outright rather than narrowing, because a
narrowed version of them would be misleading rather than merely smaller:

  * employees   — a per-person table of one person is not a per-person table.
  * reviewers   — same.
  * evidence for somebody else — an employee may read their OWN and nobody
    else's, and "nobody else's" has to be a refusal, not an empty list.

NOTHING HERE SCORES ANYBODY
---------------------------
See tasks/kpi.py and tasks/analytics.py. Employee rows come back ordered by
name, carry no composite, and are tested for both.
"""
import csv

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.uploads import harden_file_response

from evidence.registry import available as evidence_sources
from evidence.schema import CONTRACT_VERSION, PeriodType

from . import analytics, evidence as evidence_service, kpi
from . import permissions as perms
from .filters import TaskFilterSet
from .models import EmployeeTaskEvidenceSnapshot, Task


class TaskAnalyticsViewSet(viewsets.ViewSet):
    """
    Read-only analytics over the caller's own visible task set.

    A ViewSet with no queryset and no model: every action is a computation, and
    there is no object to retrieve. It lives apart from TaskViewSet because that
    class is already the module's largest and these share none of its
    machinery — not its serializer, not its object permissions, not its scopes.
    """
    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------
    def _scoped(self, request):
        """The caller's visible tasks, with the URL's filters applied."""
        queryset = Task.objects.all()
        visible = perms.visible_task_filter(request.user)
        if visible is not None:
            queryset = queryset.filter(visible)
        return TaskFilterSet(request.query_params, queryset=queryset.distinct(),
                             request=request).qs

    def _require_manager(self, request, what):
        if not (perms.is_department_head(request.user)
                or perms.has_org_wide_read(request.user)):
            raise PermissionDenied(
                f"{what} is available to a Department Head, HR or an Admin. "
                f"Your own figures are on your dashboard.")

    # ------------------------------------------------------------------
    # Dashboards (Parts 1, 2, 3, 4, 5)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="executive")
    def executive(self, request):
        """
        The organisation performance dashboard.

        Open to everybody, but SCOPED — an employee gets these figures over
        their own tasks, which is a legitimate and useful view of their own
        work. What they cannot get is anybody else's, and the department
        ranking they see contains only departments they can already see tasks
        in (usually one, usually their own).
        """
        return Response(analytics.executive_summary(self._scoped(request)))

    @action(detail=False, methods=["get"], url_path="health")
    def health(self, request):
        """The five health ratios, each with the definition it is read by."""
        return Response(analytics.task_health(self._scoped(request)))

    @action(detail=False, methods=["get"], url_path="departments")
    def departments(self, request):
        return Response({
            "departments": analytics.department_analytics(self._scoped(request)),
            "ranking": analytics.department_ranking(self._scoped(request)),
        })

    @action(detail=False, methods=["get"], url_path="employees")
    def employees(self, request):
        """
        Per-person metrics. Manager-only, and METRICS ONLY.

        Rows come back ordered by NAME — a table sorted by completion rate is a
        ranking whatever the header says, and the person at the bottom will be
        asked about it. See tasks.analytics.employee_analytics.
        """
        self._require_manager(request, "The per-employee metrics view")
        return Response({
            "employees": analytics.employee_analytics(self._scoped(request)),
            "note": (
                "Metrics only. These figures are not scored, ranked or weighted, "
                "and are ordered alphabetically by design."
            ),
        })

    @action(detail=False, methods=["get"], url_path="reviewers")
    def reviewers(self, request):
        self._require_manager(request, "The reviewer metrics view")
        return Response({
            "reviewers": analytics.reviewer_analytics(self._scoped(request)),
            "note": (
                "A queue measure, reported so a bottleneck can be found and "
                "unblocked. Not an assessment of any reviewer."
            ),
        })

    # ------------------------------------------------------------------
    # Trends (Part 6)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="trend")
    def trend(self, request):
        period = (request.query_params.get("period") or "weekly").lower()
        if period not in analytics.PERIODS:
            raise ValidationError(
                {"period": "Choose weekly, monthly or quarterly."})
        try:
            buckets = int(request.query_params.get("buckets", 12))
        except ValueError:
            raise ValidationError({"buckets": "Give a whole number."})
        # Capped: an uncapped bucket count is a request that scans the whole
        # table once per bucket.
        buckets = max(2, min(buckets, 24))
        return Response(analytics.trend(self._scoped(request), period=period,
                                        buckets=buckets))

    # ------------------------------------------------------------------
    # KPI registry (Part 9)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="kpis")
    def kpis(self, request):
        """
        Every KPI, valued against the caller's scope, with its definition.

        The catalogue travels with the values so a client never has to hold its
        own copy of what a metric means — which is how two screens end up
        explaining the same number differently.
        """
        stats = analytics.metric_inputs(self._scoped(request))
        return Response({
            "kpis": kpi.evaluate_all(stats),
            "inputs": stats,
            "catalogue": kpi.catalogue(),
        })

    # ------------------------------------------------------------------
    # Evidence (Parts 7, 8)
    # ------------------------------------------------------------------
    def _evidence_subject(self, request):
        """
        Whose evidence is being asked for, and whether the caller may have it.

        Default is the caller. Asking for somebody else requires being a manager
        AND that person being inside the caller's scope — checked by looking for
        any task of theirs the caller can see, rather than by re-deriving the
        org chart here.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()
        wanted = request.query_params.get("employee")
        if not wanted or str(wanted) == str(request.user.pk):
            return request.user

        self._require_manager(request, "Another employee's evidence")
        subject = User.objects.filter(pk=wanted).first()
        if subject is None:
            raise NotFound("No such employee.")
        if not self._scoped(request).filter(assignees__user=subject).exists():
            # A refusal, not an empty result: an empty evidence page reads as
            # "this person has done nothing", which is a different and damaging
            # claim.
            raise PermissionDenied(
                "You do not have access to that employee's task evidence.")
        return subject

    @action(detail=False, methods=["get"], url_path="evidence")
    def evidence(self, request):
        """
        One employee's evidence over a window, computed live (Part 7).

        Read-only. Nothing about anybody's record changes because this was
        called, and the payload carries its own disclaimer — see
        tasks/evidence.py on why this is evidence and not an assessment.
        """
        subject = self._evidence_subject(request)
        start, end = self._window(request)
        payload = evidence_service.build_evidence(
            self._scoped(request), subject, start, end)
        # Phase T6.6: consumers pin the contract version, and Phase T6.8's
        # low-volume guard travels with the numbers rather than beside them.
        payload["contract_version"] = CONTRACT_VERSION
        payload["low_volume"] = (payload["tasks_assigned"]
                                 < evidence_service.LOW_VOLUME_THRESHOLD)
        return Response(payload)

    @action(detail=False, methods=["get"], url_path="evidence/contract")
    def evidence_contract(self, request):
        """
        The evidence contract itself (Phase T6.6).

        A consumer — the appraisal module that does not exist yet — reads this
        to discover the metric keys, their units, their definitions and the
        contract version it is coding against, rather than hard-coding a list it
        cannot tell has changed. It also reports which of the seven declared
        sources actually have a provider, so absent evidence is distinguishable
        from empty evidence.

        Open to any authenticated caller: it is a schema, and contains nobody's
        data.
        """
        sample = evidence_service.standardised_metrics({
            "tasks_assigned": 0, "tasks_completed": 0, "tasks_open": 0,
            "tasks_overdue": 0, "completion_percent": 0, "completion_of": 0,
            "completed_with_due_date": 0, "completed_on_time": 0,
            "on_time_percent": 0, "average_completion_days": None,
            "reviews_performed": 0, "checklist_items_total": 0,
            "checklist_items_completed": 0, "evidence_files_submitted": 0,
            "comments_posted": 0,
        })
        return Response({
            "contract_version": CONTRACT_VERSION,
            "period_types": [p.value for p in PeriodType],
            "sources": evidence_sources(),
            "metrics": [
                {k: v for k, v in metric.as_dict().items() if k != "value"}
                for metric in sample
            ],
            "guarantees": [
                "Every percentage names the count it was calculated over.",
                "A null value means no data, never zero.",
                "No metric is a composite, a score or a weighting.",
                "Nothing is ranked, ordered by performance, or compared "
                "between people.",
                "Low task volume is flagged on the evidence itself.",
            ],
        })

    @action(detail=False, methods=["get"], url_path="evidence/standard")
    def evidence_standard(self, request):
        """
        One person's evidence in the SHARED contract shape (Phase T6.2).

        The same numbers as `evidence/`, expressed as the schema every future
        source will use — so a consumer reads task evidence the same way it will
        read leave or memo evidence. `evidence/` keeps its original shape because
        that is what this module's own UI reads; reshaping it for a consumer that
        does not exist yet would break a working page for a hypothetical one.
        """
        subject = self._evidence_subject(request)
        start, end = self._window(request)
        period_type = (request.query_params.get("period_type")
                       or PeriodType.DAILY.value)
        try:
            period_type = PeriodType(period_type)
        except ValueError:
            raise ValidationError(
                {"period_type": "Choose daily, monthly, quarterly or annual."})

        evidence_set = evidence_service.task_evidence_provider(
            subject, start, end, period_type, queryset=self._scoped(request))
        return Response(evidence_set.as_dict())

    @action(detail=False, methods=["get"], url_path="evidence/team")
    def evidence_team(self, request):
        """
        Evidence for a manager's direct reports (Phase T6.5).

        ORDERED BY NAME, and the payload says so. This is the surface where a
        ranking would be most tempting and most damaging: a manager opening a
        list of their reports sorted by completion rate has been handed a
        judgement they did not make and cannot see the basis of. Every row
        carries its own low-volume flag for the same reason.
        """
        self._require_manager(request, "Team evidence")
        start, end = self._window(request)
        queryset = self._scoped(request)

        from django.contrib.auth import get_user_model

        User = get_user_model()
        user_ids = (queryset.filter(assignees__user__isnull=False)
                    .values_list("assignees__user_id", flat=True).distinct())
        people = User.objects.filter(pk__in=list(user_ids), is_active=True)

        rows = []
        for person in people:
            raw = evidence_service.build_evidence(queryset, person, start, end)
            rows.append({
                "employee_id": str(person.pk),
                "employee_name": raw["employee_name"],
                "department": raw["department"],
                "tasks_assigned": raw["tasks_assigned"],
                "tasks_completed": raw["tasks_completed"],
                "tasks_open": raw["tasks_open"],
                "tasks_overdue": raw["tasks_overdue"],
                "completion_percent": raw["completion_percent"],
                "completion_of": raw["completion_of"],
                "on_time_percent": raw["on_time_percent"],
                "completed_with_due_date": raw["completed_with_due_date"],
                "average_completion_days": raw["average_completion_days"],
                "reviews_performed": raw["reviews_performed"],
                "evidence_files_submitted": raw["evidence_files_submitted"],
                "comments_posted": raw["comments_posted"],
                "low_volume": raw["tasks_assigned"]
                < evidence_service.LOW_VOLUME_THRESHOLD,
            })

        return Response({
            "period_start": start,
            "period_end": end,
            "contract_version": CONTRACT_VERSION,
            # BY NAME. See the docstring.
            "employees": sorted(rows, key=lambda r: r["employee_name"].lower()),
            "note": (
                "Activity evidence for your reports, ordered alphabetically. "
                "These figures are not scored, ranked or weighted, and a row "
                "flagged low_volume has too few tasks for its percentages to "
                "mean anything."
            ),
        })

    def _window(self, request):
        """The requested window, or the default rolling year."""
        start, end = evidence_service.default_period()
        raw_start = request.query_params.get("from")
        raw_end = request.query_params.get("to")
        try:
            if raw_start:
                start = timezone.datetime.fromisoformat(raw_start).date()
            if raw_end:
                end = timezone.datetime.fromisoformat(raw_end).date()
        except ValueError:
            raise ValidationError({"from": "Give dates as YYYY-MM-DD."})
        if start > end:
            raise ValidationError({"from": "The start must precede the end."})
        return start, end

    @action(detail=False, methods=["get"], url_path="evidence/snapshots")
    def evidence_snapshots(self, request):
        """
        The frozen daily record (Part 8).

        What the data SAID on each day, so an appraisal months later is not
        reading a figure recomputed against tasks that have since moved.
        """
        subject = self._evidence_subject(request)
        rows = EmployeeTaskEvidenceSnapshot.objects.filter(employee=subject)
        cadence = request.query_params.get("period_type")
        if cadence:
            rows = rows.filter(period_type=cadence)
        rows = rows.order_by("-snapshot_date")[:400]
        return Response({
            "employee_id": str(subject.pk),
            "employee_name": subject.get_full_name() or subject.username,
            "snapshots": [
                {
                    "snapshot_date": row.snapshot_date,
                    "period_type": row.period_type,
                    "period_start": row.period_start,
                    "period_end": row.period_end,
                    "tasks_assigned": row.tasks_assigned,
                    "tasks_completed": row.tasks_completed,
                    "tasks_open": row.tasks_open,
                    "tasks_overdue": row.tasks_overdue,
                    "completion_percent": row.completion_percent,
                    "completed_with_due_date": row.completed_with_due_date,
                    "completed_on_time": row.completed_on_time,
                    "on_time_percent": row.on_time_percent,
                    "average_completion_days": row.average_completion_days,
                    "reviews_performed": row.reviews_performed,
                    "checklist_items_total": row.checklist_items_total,
                    "checklist_items_completed": row.checklist_items_completed,
                    "evidence_files_submitted": row.evidence_files_submitted,
                    "comments_posted": row.comments_posted,
                }
                for row in rows
            ],
        })
