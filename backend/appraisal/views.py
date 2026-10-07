"""
The appraisal API.

Every list is scoped by `appraisal.permissions.visible_appraisal_filter`, and
every write is gated by the stage-ownership rules in the same module. This file
routes and validates; it decides nothing about anybody.
"""
import logging

from django.db import transaction
from django.db.models import Count, Prefetch, Q
from rest_framework import status as http_status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import dashboards
from . import evidence_link
from . import permissions as perms
from . import workflow
from .models import (
    Appraisal, AppraisalCycle, Competency, CompetencyRating, DevelopmentPlan,
    Goal, TrainingPlan,
)
from . import events
from .serializers import (
    AppraisalCreateSerializer, AppraisalCycleSerializer,
    AppraisalDetailSerializer, AppraisalListSerializer, AppraisalWriteSerializer,
    AttachEvidenceSerializer, CompetencyRatingSerializer, CompetencySerializer,
    DevelopmentPlanSerializer, GoalSerializer, RateSerializer, ReopenSerializer,
    ReturnSerializer, TrainingDecisionSerializer, TrainingPlanSerializer,
)
from .services import record_audit, user_snapshot

logger = logging.getLogger("appraisal")
Status = Appraisal.Status


class CompetencyViewSet(viewsets.ReadOnlyModelViewSet):
    """The competency catalogue. Read-only through the API; HR edits it in admin."""
    permission_classes = [IsAuthenticated]
    serializer_class = CompetencySerializer

    def get_queryset(self):
        rows = Competency.objects.all()
        if self.request.query_params.get("include_inactive") not in ("1", "true"):
            rows = rows.filter(is_active=True)
        return rows


class AppraisalCycleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, perms.CanManageCycles]
    serializer_class = AppraisalCycleSerializer
    search_fields = ["name", "description"]
    ordering = ["-period_start"]

    def get_queryset(self):
        return AppraisalCycle.objects.annotate(
            appraisal_count=Count("appraisals", distinct=True))

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user,
                        created_by_name=user_snapshot(self.request.user)["name"])

    def destroy(self, request, *args, **kwargs):
        """
        A cycle holding appraisals is never deleted — those are records people
        may have to produce years later. It is closed instead.
        """
        cycle = self.get_object()
        if cycle.appraisals.exists():
            raise ValidationError(
                {"cycle": "This cycle holds appraisals and cannot be deleted. "
                          "Close it instead."})
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        cycle = self.get_object()
        cycle.status = AppraisalCycle.Status.ACTIVE
        cycle.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(cycle).data)

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        cycle = self.get_object()
        cycle.status = AppraisalCycle.Status.CLOSED
        cycle.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(cycle).data)


class AppraisalViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, perms.CanViewAppraisal]
    search_fields = ["employee_name", "department_name", "cycle__name"]
    ordering_fields = ["created_at", "status", "employee_name"]
    ordering = ["-created_at"]

    # ------------------------------------------------------------------
    # Queryset and scopes
    # ------------------------------------------------------------------
    def get_queryset(self):
        queryset = (Appraisal.objects
                    .select_related("cycle", "employee", "supervisor",
                                    "department")
                    .prefetch_related(
                        "goals", "committee",
                        Prefetch("competency_ratings",
                                 queryset=CompetencyRating.objects
                                 .select_related("competency")),
                        "development_plans", "training_plans",
                        "evidence_references", "audit_entries__actor"))
        visible = perms.visible_appraisal_filter(self.request.user)
        if visible is not None:
            queryset = queryset.filter(visible)
        return self._apply_scope(queryset).distinct()

    def _apply_scope(self, queryset):
        """
        A menu is a route plus a scope name — the pattern the task module uses,
        so a list's definition lives in exactly one place.
        """
        user = self.request.user
        scope = self.request.query_params.get("scope", "all")
        if scope == "mine":
            return queryset.filter(employee=user)
        if scope == "supervising":
            return queryset.filter(supervisor=user)
        if scope == "committee":
            return queryset.filter(committee=user)
        if scope == "needs_me":
            # The one queue that answers "is anything waiting on me?". Each
            # clause pairs a stage with the person whose turn it is — the same
            # pairing appraisal.permissions enforces.
            return queryset.filter(
                Q(employee=user, status=Status.SELF_ASSESSMENT)
                | Q(supervisor=user, status__in=[Status.GOAL_SETTING,
                                                 Status.MID_YEAR,
                                                 Status.SUPERVISOR_REVIEW])
                | Q(committee=user, status=Status.COMMITTEE)
                | Q(supervisor=user, status__in=[Status.FINAL_REVIEW,
                                                 Status.DEVELOPMENT_PLAN,
                                                 Status.TRAINING_PLAN]))
        if scope == "open":
            return queryset.exclude(status=Status.CLOSED)
        if scope == "closed":
            return queryset.filter(status=Status.CLOSED)
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return AppraisalCreateSerializer
        if self.action in ("update", "partial_update"):
            return AppraisalWriteSerializer
        if self.action == "list":
            return AppraisalListSerializer
        return AppraisalDetailSerializer

    def _detail(self, appraisal):
        fresh = self.get_queryset().filter(pk=appraisal.pk).first() or appraisal
        return AppraisalDetailSerializer(
            fresh, context=self.get_serializer_context()).data

    # ------------------------------------------------------------------
    # Create / update / delete
    # ------------------------------------------------------------------
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        if not perms.can_create_appraisal(request.user):
            raise PermissionDenied(
                "Only HR or an Admin may open an appraisal.")
        write = self.get_serializer(data=request.data)
        write.is_valid(raise_exception=True)

        employee = write.validated_data["employee"]
        supervisor = write.validated_data.get("supervisor")
        snapshot = user_snapshot(employee)
        appraisal = write.save(
            employee_name=snapshot["name"],
            designation=snapshot["designation"],
            department=getattr(employee, "department_ref", None),
            department_name=snapshot["department"],
            supervisor_name=user_snapshot(supervisor)["name"],
        )
        workflow.record_creation(appraisal, request.user, request=request)
        return Response(self._detail(appraisal),
                        status=http_status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        The written record, field by field — each guarded by whose turn it is.

        Checked per FIELD rather than per request: a supervisor may legitimately
        PATCH the appraisal at their stage, and must still not be able to reach
        into the employee's self-assessment in the same call.
        """
        appraisal = self.get_object()
        write = AppraisalWriteSerializer(appraisal, data=request.data,
                                         partial=True)
        write.is_valid(raise_exception=True)
        data = write.validated_data

        guards = {
            "self_assessment": perms.can_write_self_assessment,
            "supervisor_comments": perms.can_write_supervisor_review,
            "committee_comments": perms.can_write_committee_review,
            "final_summary": perms.can_finalise,
            "promotion_readiness": perms.can_finalise,
            "promotion_rationale": perms.can_finalise,
            "successor_for": perms.can_finalise,
        }
        for field, guard in guards.items():
            if field in data and not guard(request.user, appraisal):
                raise PermissionDenied(
                    f"You cannot write '{field}' at this stage of the appraisal.")

        # Reassigning the supervisor or committee is an HR act, at any stage.
        if ("supervisor" in data or "committee" in data) and not \
                perms.has_org_scope(request.user):
            raise PermissionDenied(
                "Only HR or an Admin may change who reviews an appraisal.")

        committee = data.pop("committee", None)
        previous_readiness = appraisal.promotion_readiness
        for field, value in data.items():
            setattr(appraisal, field, value)
        if "supervisor" in data:
            appraisal.supervisor_name = user_snapshot(data["supervisor"])["name"]
        appraisal.save()
        if committee is not None:
            appraisal.committee.set(committee)

        record_audit(appraisal, request.user, "updated",
                     remarks=f"Updated: {', '.join(sorted(data))}.",
                     request=request)
        # A promotion recommendation changing gets its OWN row, with the value
        # it moved from and to. Folded into the generic "Updated:
        # promotion_readiness" line above, the single most consequential field
        # in this record would be indistinguishable from a typo fix — and the
        # question asked afterwards is always "when did this become
        # Development Required, and who changed it".
        if "promotion_readiness" in data and previous_readiness != \
                appraisal.promotion_readiness:
            record_audit(
                appraisal, request.user, "promotion_recorded",
                remarks=(f"{Appraisal.PromotionReadiness(previous_readiness).label if previous_readiness else 'Not Considered'}"
                         f" -> {appraisal.get_promotion_readiness_display() or 'Not Considered'}"),
                metadata={"from": previous_readiness,
                          "to": appraisal.promotion_readiness},
                request=request)
        return Response(self._detail(appraisal))

    def destroy(self, request, *args, **kwargs):
        appraisal = self.get_object()
        if not perms.can_delete(request.user, appraisal):
            raise PermissionDenied(
                "An appraisal with anything written in it is corrected, never "
                "deleted. Only an untouched one can be removed, by HR.")
        record_audit(appraisal, request.user, "updated",
                     remarks="Untouched appraisal deleted.", request=request)
        appraisal.delete()
        return Response(status=http_status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="goals")
    def goals(self, request, pk=None):
        appraisal = self.get_object()
        if request.method == "GET":
            return Response(GoalSerializer(appraisal.goals.all(),
                                           many=True).data)
        if not perms.can_edit_goals(request.user, appraisal):
            raise PermissionDenied(
                "Objectives can only be changed while goals are being agreed or "
                "at the mid-year review.")
        write = GoalSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        owner = write.validated_data.get("owner") or appraisal.employee
        goal = write.save(appraisal=appraisal, owner=owner,
                          owner_name=user_snapshot(owner)["name"],
                          position=appraisal.goals.count())
        record_audit(appraisal, request.user, "goal_changed",
                     remarks=f"Objective added: {goal.objective}",
                     metadata={"weight_total": appraisal.goal_weight_total},
                     request=request)
        return Response(GoalSerializer(goal).data,
                        status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch", "delete"],
            url_path=r"goals/(?P<goal_id>[^/.]+)")
    def goal_detail(self, request, pk=None, goal_id=None):
        appraisal = self.get_object()
        goal = appraisal.goals.filter(pk=goal_id).first()
        if goal is None:
            raise NotFound("That objective is not on this appraisal.")

        if request.method == "DELETE":
            if not perms.can_edit_goals(request.user, appraisal):
                raise PermissionDenied("Objectives can no longer be changed.")
            record_audit(appraisal, request.user, "goal_changed",
                         remarks=f"Objective removed: {goal.objective}",
                         request=request)
            goal.delete()
            return Response(status=http_status.HTTP_204_NO_CONTENT)

        # Progress and achievement are the EMPLOYEE'S account and stay writable
        # into the self-assessment stage; the objective and its weight are fixed
        # once goals are agreed.
        editing_plan = set(request.data) - {"achievement", "progress_percent"}
        if editing_plan and not perms.can_edit_goals(request.user, appraisal):
            raise PermissionDenied(
                "The objective and its weight are fixed once goals are agreed.")
        if not editing_plan and not (
                perms.is_subject(request.user, appraisal)
                or perms.can_edit_goals(request.user, appraisal)):
            raise PermissionDenied("Only the employee records their achievement.")

        write = GoalSerializer(goal, data=request.data, partial=True)
        write.is_valid(raise_exception=True)
        write.save()
        record_audit(appraisal, request.user, "goal_changed",
                     remarks=f"Objective updated: {goal.objective}",
                     request=request)
        return Response(GoalSerializer(goal).data)

    # ------------------------------------------------------------------
    # Competency ratings
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="rate")
    def rate(self, request, pk=None):
        """
        Rate one competency, as one role.

        The employee's, the supervisor's and the committee's ratings COEXIST —
        each writes its own row and none overwrites another. The gap between a
        self-rating and a supervisor's is usually the most useful thing in the
        conversation, and averaging them away would destroy exactly that.
        """
        appraisal = self.get_object()
        write = RateSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        role = write.validated_data["role"]

        if not perms.can_rate(request.user, appraisal, role):
            raise PermissionDenied(
                f"You cannot record a {role} rating at this stage.")

        competency = write.validated_data["competency"]
        rating, _created = CompetencyRating.objects.update_or_create(
            appraisal=appraisal, competency=competency, rated_by_role=role,
            defaults={
                "level": write.validated_data["level"],
                "comment": write.validated_data["comment"],
                "competency_name": competency.name,
                "rated_by": request.user,
                "rated_by_name": user_snapshot(request.user)["name"],
            })
        record_audit(appraisal, request.user, "rated",
                     remarks=f"{competency.name}: {rating.get_level_display()} "
                             f"({role})",
                     metadata={"competency": competency.code, "role": role},
                     request=request)
        return Response(CompetencyRatingSerializer(rating).data)

    # ------------------------------------------------------------------
    # Development and training plans
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="development-plan")
    def development_plan(self, request, pk=None):
        appraisal = self.get_object()
        if request.method == "GET":
            return Response(DevelopmentPlanSerializer(
                appraisal.development_plans.all(), many=True).data)
        if not perms.can_manage_development_plan(request.user, appraisal):
            raise PermissionDenied(
                "The development plan is agreed at the development stage.")
        write = DevelopmentPlanSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        accountable = write.validated_data.get("accountable") or appraisal.employee
        row = write.save(appraisal=appraisal, accountable=accountable,
                         accountable_name=user_snapshot(accountable)["name"])
        record_audit(appraisal, request.user, "development_planned",
                     remarks=f"Development action: {row.area}", request=request)
        return Response(DevelopmentPlanSerializer(row).data,
                        status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="training-plan")
    def training_plan(self, request, pk=None):
        appraisal = self.get_object()
        if request.method == "GET":
            return Response(TrainingPlanSerializer(
                appraisal.training_plans.all(), many=True).data)
        if not perms.can_manage_training_plan(request.user, appraisal):
            raise PermissionDenied(
                "Training needs are recorded at the training stage.")
        write = TrainingPlanSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        # Snapshot the mentor's name beside the reference, as everything
        # historical in this codebase does: a mentor who leaves must not turn a
        # recorded pairing into a blank cell on somebody's appraisal.
        mentor = write.validated_data.get("mentor")
        row = write.save(
            appraisal=appraisal,
            mentor_name=user_snapshot(mentor)["name"] if mentor else "")
        record_audit(appraisal, request.user, "training_planned",
                     remarks=f"Training identified: {row.title}", request=request)
        return Response(TrainingPlanSerializer(row).data,
                        status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"],
            url_path=r"training-plan/(?P<training_id>[^/.]+)/decide")
    def decide_training(self, request, pk=None, training_id=None):
        """
        HR approves, schedules or declines. A declined request that nobody owns
        is how training needs quietly disappear, so the decision carries a name.
        """
        appraisal = self.get_object()
        if not perms.can_decide_training(request.user):
            raise PermissionDenied(
                "Only HR or an Admin may decide on a training request.")
        row = appraisal.training_plans.filter(pk=training_id).first()
        if row is None:
            raise NotFound("That training item is not on this appraisal.")

        write = TrainingDecisionSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        row.status = write.validated_data["status"]
        row.decision_note = write.validated_data.get("decision_note", "")
        row.decided_by = request.user
        row.decided_by_name = user_snapshot(request.user)["name"]
        row.save(update_fields=["status", "decision_note", "decided_by",
                                "decided_by_name", "updated_at"])
        record_audit(appraisal, request.user, "training_planned",
                     remarks=f"{row.title}: {row.get_status_display()}",
                     request=request)
        # The employee hears what happened to a request they made. A declined
        # request nobody hears about is how people stop asking.
        events.emit(events.TRAINING_DECIDED, appraisal, actor=request.user,
                    training=row)
        return Response(TrainingPlanSerializer(row).data)

    # ------------------------------------------------------------------
    # Evidence — consumed, never recomputed
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="evidence")
    def evidence(self, request, pk=None):
        """
        The live evidence pack for this appraisal's period.

        Every figure comes from the Phase T6 evidence registry — the same code
        that answers the task module's own screens. This module computes none of
        it; see appraisal/evidence_link.py.
        """
        appraisal = self.get_object()
        cycle = appraisal.cycle
        evidence_set = evidence_link.collect_task_evidence(
            appraisal.employee, cycle.period_start, cycle.period_end)

        return Response({
            "period_start": cycle.period_start,
            "period_end": cycle.period_end,
            "available": evidence_set is not None,
            "sources": evidence_link.evidence_sources(),
            "headline": evidence_link.headline_metrics(evidence_set),
            "low_volume": evidence_link.low_volume(evidence_set),
            "disclaimer": (evidence_set.as_dict()["disclaimer"]
                           if evidence_set else ""),
            "snapshots": [
                {"snapshot_date": row.snapshot_date,
                 "period_type": row.period_type,
                 "tasks_assigned": row.tasks_assigned,
                 "tasks_completed": row.tasks_completed,
                 "completion_percent": row.completion_percent,
                 "on_time_percent": row.on_time_percent}
                for row in evidence_link.snapshots_for(
                    appraisal.employee, cycle.period_start, cycle.period_end)
            ],
            "note": (
                "Activity evidence produced by the task module and cited here. "
                "It is context for a human judgement, not a measure of "
                "performance, and nothing in this appraisal is computed from it."
            ),
        })

    @action(detail=True, methods=["post"], url_path="attach-evidence")
    def attach_evidence(self, request, pk=None):
        """Freeze the current evidence onto the record as a citation."""
        appraisal = self.get_object()
        if not perms.can_attach_evidence(request.user, appraisal):
            raise PermissionDenied("You cannot attach evidence to this appraisal.")
        write = AttachEvidenceSerializer(data=request.data)
        write.is_valid(raise_exception=True)

        reference = evidence_link.attach_evidence(
            appraisal, request.user,
            goal=write.validated_data.get("goal"),
            note=write.validated_data.get("note", ""))
        if reference is None:
            raise ValidationError(
                {"evidence": "No evidence source is available on this "
                             "deployment."})
        record_audit(appraisal, request.user, "evidence_attached",
                     remarks="Task evidence cited.",
                     metadata={"source": reference.source,
                               "contract": reference.contract_version},
                     request=request)
        return Response(self._detail(appraisal))

    # ------------------------------------------------------------------
    # Workflow — one endpoint per named stage
    # ------------------------------------------------------------------
    def _transition(self, request, guard, run, refusal):
        appraisal = self.get_object()
        if not guard(request.user, appraisal):
            raise PermissionDenied(refusal)
        run(appraisal, request.user, request=request)
        return Response(self._detail(appraisal))

    @action(detail=True, methods=["post"], url_path="submit-goals")
    def submit_goals(self, request, pk=None):
        return self._transition(
            request, perms.can_submit_goals, workflow.submit_goals,
            "Only the employee, their supervisor or HR may submit objectives "
            "for approval.")

    @action(detail=True, methods=["post"], url_path="agree-goals")
    def agree_goals(self, request, pk=None):
        return self._transition(
            request, perms.can_agree_goals, workflow.agree_goals,
            "Only the supervisor or HR approves the objective set.")

    @action(detail=True, methods=["post"], url_path="record-mid-year")
    def record_mid_year(self, request, pk=None):
        return self._transition(
            request, perms.can_record_mid_year, workflow.record_mid_year,
            "Only the supervisor or HR records the mid-year review.")

    @action(detail=True, methods=["post"], url_path="submit-self-assessment")
    def submit_self_assessment(self, request, pk=None):
        return self._transition(
            request, perms.can_submit_self_assessment,
            workflow.submit_self_assessment,
            "Only the employee submits their own self assessment.")

    @action(detail=True, methods=["post"], url_path="record-supervisor-review")
    def record_supervisor_review(self, request, pk=None):
        return self._transition(
            request, perms.can_write_supervisor_review,
            workflow.record_supervisor_review,
            "This appraisal is not with you for supervisor review.")

    @action(detail=True, methods=["post"], url_path="record-committee-review")
    def record_committee_review(self, request, pk=None):
        return self._transition(
            request, perms.can_write_committee_review,
            workflow.record_committee_review,
            "This appraisal is not with you for committee review.")

    @action(detail=True, methods=["post"], url_path="record-final-review")
    def record_final_review(self, request, pk=None):
        return self._transition(
            request, perms.can_finalise, workflow.record_final_review,
            "You cannot record the final review on this appraisal.")

    @action(detail=True, methods=["post"], url_path="agree-development-plan")
    def agree_development_plan(self, request, pk=None):
        return self._transition(
            request, perms.can_manage_development_plan,
            workflow.agree_development_plan,
            "You cannot agree the development plan on this appraisal.")

    @action(detail=True, methods=["post"], url_path="agree-training-plan")
    def agree_training_plan(self, request, pk=None):
        return self._transition(
            request, perms.can_manage_training_plan,
            workflow.agree_training_plan,
            "You cannot agree the training plan on this appraisal.")

    @action(detail=True, methods=["post"], url_path="return")
    def return_stage(self, request, pk=None):
        appraisal = self.get_object()
        if not perms.can_return(request.user, appraisal):
            raise PermissionDenied(
                "Only a reviewer or HR can send an appraisal back.")
        write = ReturnSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.return_to(appraisal, request.user,
                           write.validated_data["to_status"],
                           write.validated_data["remarks"], request=request)
        return Response(self._detail(appraisal))

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        appraisal = self.get_object()
        if not perms.can_reopen(request.user, appraisal):
            raise PermissionDenied(
                "Only HR or an Admin may reopen a closed appraisal.")
        write = ReopenSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.reopen(appraisal, request.user,
                        write.validated_data["remarks"], request=request)
        return Response(self._detail(appraisal))

    # ------------------------------------------------------------------
    # Dashboards (three roles, one endpoint, chosen by the server)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        """
        Employee, manager and HR blocks — ADDITIVE, and decided by the SERVER.

        A supervisor is also somebody with their own appraisal, and HR are too.
        Replacing the personal block with the managerial one is why people keep a
        second list; each block is added to the last.

        Computed over `_visible()` — the caller's own visible set WITHOUT a menu
        scope, so a `?scope=` left on the URL cannot silently narrow a dashboard.
        """
        queryset = self._visible()
        user = request.user
        payload = {
            "perspective": "employee",
            "employee": dashboards.employee_dashboard(queryset, user),
        }
        if queryset.filter(supervisor=user).exists() or perms.has_org_scope(user):
            payload["perspective"] = "manager"
            payload["manager"] = dashboards.manager_dashboard(queryset, user)
        if perms.has_org_scope(user) or user.role == "bod":
            # The Board sees the organisation-wide summary, read-only (Phase BOD).
            payload["perspective"] = "hr"
            payload["hr"] = dashboards.hr_dashboard(queryset)
        return Response(payload)

    def _visible(self):
        """The caller's visible appraisals, with no menu scope applied."""
        queryset = (Appraisal.objects
                    .select_related("cycle", "employee", "supervisor")
                    .prefetch_related("goals", "development_plans",
                                      "training_plans", "committee"))
        visible = perms.visible_appraisal_filter(self.request.user)
        if visible is not None:
            queryset = queryset.filter(visible)
        cycle = self.request.query_params.get("cycle")
        if cycle:
            queryset = queryset.filter(cycle_id=cycle)
        return queryset.distinct()

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="reports")
    def reports(self, request):
        return Response([
            {"slug": slug, "label": spec["label"],
             "description": spec["description"]}
            for slug, spec in dashboards.REPORTS.items()
        ])

    @action(detail=False, methods=["get"], url_path=r"reports/(?P<slug>[a-z-]+)")
    def report_detail(self, request, slug=None):
        """
        One report over the caller's visible set. `?export=csv` or `pdf`.

        Reports carry no permission of their own: a report can never contain an
        appraisal the caller could not open individually.
        """
        spec = dashboards.REPORTS.get(slug)
        if spec is None:
            raise NotFound(f"No report called '{slug}'.")
        payload = spec["build"](self._visible())
        export = request.query_params.get("export")
        if export == "csv":
            return _csv_response(slug, payload)
        if export == "pdf":
            return _pdf_response(slug, spec["label"], payload, request)
        return Response({"slug": slug, "label": spec["label"], **payload})

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        from .serializers import AppraisalAuditLogSerializer

        appraisal = self.get_object()
        return Response(AppraisalAuditLogSerializer(
            appraisal.audit_entries.all(), many=True).data)


# ---------------------------------------------------------------------------
# Exports
#
# The same {columns, rows, summary} envelope the task module's reports use, so
# one renderer serves both and a reader gets the same shape wherever they are.
# ---------------------------------------------------------------------------
def _csv_response(slug, payload):
    import csv

    from django.http import HttpResponse
    from django.utils import timezone as _tz

    from config.uploads import harden_file_response

    # UTF-8 WITH A BOM, and a charset on the content type.
    #
    # Excel on Windows does not sniff encodings: it reads a CSV as the system
    # codepage unless the file opens with a byte-order mark. Without one, a
    # Nepali name — or any Devanagari, or a smart quote — arrives as mojibake in
    # the one application most of these exports are opened in, and the person
    # looking at it has no way to tell whether the data or the export is wrong.
    #
    # `analytics/exports.py` and `reports/workforce_reports.py` have done this
    # since they were written; these two writers were the ones that diverged.
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="appraisal-{slug}-{_tz.localdate()}.csv"')
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow([column["label"] for column in payload["columns"]])
    for row in payload["rows"]:
        writer.writerow([row.get(column["key"], "")
                         for column in payload["columns"]])
    return harden_file_response(response)


def _pdf_response(slug, label, payload, request):
    """
    Reuses the task module's print TEMPLATE, not its code.

    A template is a rendering asset — the same relationship this module has to
    WeasyPrint itself. Copying it would give the organisation two subtly
    different printed layouts for the same kind of table.
    """
    from django.http import HttpResponse
    from django.template.loader import render_to_string
    from django.utils import timezone as _tz

    from config.uploads import harden_file_response

    try:
        from weasyprint import HTML
    except Exception:  # pragma: no cover - present in the project image
        logger.error("WeasyPrint unavailable; appraisal PDF export refused.")
        return Response(
            {"detail": "PDF export is unavailable on this server. Export as "
                       "CSV instead."},
            status=http_status.HTTP_503_SERVICE_UNAVAILABLE)

    html = render_to_string("tasks/report.html", {
        "label": f"Appraisal — {label}",
        "columns": payload["columns"],
        "rows": [[row.get(column["key"]) for column in payload["columns"]]
                 for row in payload["rows"]],
        "summary": payload["summary"],
        "generated_at": _tz.now(),
        "generated_by": user_snapshot(request.user)["name"],
    })
    response = HttpResponse(HTML(string=html).write_pdf(),
                            content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="appraisal-{slug}-{_tz.localdate()}.pdf"')
    return harden_file_response(response)
