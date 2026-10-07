"""
Appraisal serializers.

CAPABILITY FLAGS, NOT CLIENT-SIDE RULES
---------------------------------------
The detail payload carries `capabilities` computed from appraisal.permissions,
so the UI renders its action bar from the server's answer and cannot offer a
step the API would refuse.

NOTHING HERE COMPUTES A VERDICT
-------------------------------
No serializer produces an average of competency levels, a total of ratings, or
any aggregate that reads as an overall assessment. `goal_weight_total` exists
because the process requires goals to sum to 100 — it is a completeness check on
the plan, not a measure of the person. `appraisal/tests/test_fairness.py` asserts
the absence.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from . import permissions as perms
from .models import (
    Appraisal, AppraisalAuditLog, AppraisalCycle, Competency, CompetencyRating,
    DevelopmentPlan, EvidenceReference, Goal, TrainingPlan,
)
from .services import GOAL_WEIGHT_TOTAL

User = get_user_model()


class UserMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "designation", "department"]
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class CompetencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Competency
        fields = ["id", "code", "name", "description", "ordering", "is_active"]
        read_only_fields = fields


class AppraisalCycleSerializer(serializers.ModelSerializer):
    appraisal_count = serializers.IntegerField(read_only=True)
    status_label = serializers.CharField(source="get_status_display",
                                         read_only=True)

    class Meta:
        model = AppraisalCycle
        fields = ["id", "name", "description", "status", "status_label",
                  "period_start", "period_end", "goal_setting_deadline",
                  "self_assessment_deadline", "review_deadline",
                  "created_by_name", "created_at", "appraisal_count"]
        read_only_fields = ["id", "created_by_name", "created_at",
                            "appraisal_count", "status_label"]

    def validate(self, attrs):
        start = attrs.get("period_start",
                          getattr(self.instance, "period_start", None))
        end = attrs.get("period_end", getattr(self.instance, "period_end", None))
        if start and end and start > end:
            raise serializers.ValidationError(
                {"period_end": "The period must end after it starts."})
        return attrs


class GoalSerializer(serializers.ModelSerializer):
    # Read-only: a goal's status is DERIVED from the appraisal's stage by the
    # workflow. Writable, somebody could mark their own objective Approved
    # without anybody approving it.
    status_label = serializers.CharField(source="get_status_display",
                                         read_only=True)
    owner_detail = UserMiniSerializer(source="owner", read_only=True)

    class Meta:
        model = Goal
        fields = ["id", "objective", "description", "status", "status_label",
                  "weight", "target",
                  "achievement", "progress_percent", "due_date", "owner",
                  "owner_detail", "owner_name", "position", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "status", "status_label", "owner_name",
                            "owner_detail", "created_at",
                            "updated_at"]

    def validate_objective(self, value):
        text = (value or "").strip()
        if len(text) < 5:
            raise serializers.ValidationError(
                "Give the objective at least 5 characters.")
        return text

    def validate_weight(self, value):
        # The 100% rule is checked at the TRANSITION, not per goal — a
        # half-written set legitimately does not total 100 and refusing each
        # keystroke would make the form unusable. What is refused here is a
        # single goal that could never be part of a valid set.
        if not 1 <= value <= GOAL_WEIGHT_TOTAL:
            raise serializers.ValidationError(
                f"A weight must be between 1 and {GOAL_WEIGHT_TOTAL}.")
        return value


class CompetencyRatingSerializer(serializers.ModelSerializer):
    level_label = serializers.CharField(source="get_level_display",
                                        read_only=True)
    rated_by_label = serializers.CharField(source="get_rated_by_role_display",
                                           read_only=True)

    class Meta:
        model = CompetencyRating
        fields = ["id", "competency", "competency_name", "level", "level_label",
                  "comment", "rated_by_role", "rated_by_label", "rated_by_name",
                  "rated_at"]
        read_only_fields = ["id", "competency_name", "level_label",
                            "rated_by_label", "rated_by_name", "rated_at",
                            "rated_by_role"]

    def validate_comment(self, value):
        """
        Mandatory, and long enough to be a reason.

        A level with no reasoning is a number in disguise, and it is the
        reasoning the person being appraised can actually respond to.
        """
        text = (value or "").strip()
        if len(text) < 10:
            raise serializers.ValidationError(
                "Say why, in at least 10 characters. A rating without reasoning "
                "is not something the employee can respond to.")
        return text


class DevelopmentPlanSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display",
                                         read_only=True)

    class Meta:
        model = DevelopmentPlan
        fields = ["id", "area", "action", "support_required", "competency",
                  "target_date", "status", "status_label", "accountable",
                  "accountable_name", "created_at"]
        read_only_fields = ["id", "accountable_name", "status_label",
                            "created_at"]

    def validate_action(self, value):
        text = (value or "").strip()
        if len(text) < 5:
            # A development plan listing weaknesses with no actions is an
            # assessment wearing a plan's clothes.
            raise serializers.ValidationError(
                "Say what will actually be done.")
        return text


class TrainingPlanSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display",
                                         read_only=True)
    priority_label = serializers.CharField(source="get_priority_display",
                                           read_only=True)
    kind_label = serializers.CharField(source="get_kind_display",
                                       read_only=True)

    class Meta:
        model = TrainingPlan
        fields = ["id", "title", "kind", "kind_label", "justification",
                  "competency", "priority", "priority_label", "status",
                  "status_label", "target_period", "mentor", "mentor_name",
                  "decided_by_name", "decision_note", "created_at"]
        read_only_fields = ["id", "kind_label", "status_label",
                            "priority_label", "mentor_name", "decided_by_name",
                            "created_at"]

    def validate(self, attrs):
        # A mentor named against a course is a field the reader has to guess at,
        # so it is refused rather than silently kept and never shown.
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if attrs.get("mentor") and kind != TrainingPlan.Kind.MENTORSHIP:
            raise serializers.ValidationError(
                {"mentor": "A mentor belongs to a mentorship item."})
        return attrs


class EvidenceReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvidenceReference
        fields = ["id", "goal", "source", "contract_version", "period_start",
                  "period_end", "period_type", "metrics", "disclaimer", "note",
                  "captured_at", "attached_by_name"]
        read_only_fields = fields


class AppraisalAuditLogSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display",
                                         read_only=True)

    class Meta:
        model = AppraisalAuditLog
        fields = ["id", "action", "action_label", "actor_name", "from_status",
                  "to_status", "remarks", "metadata", "created_at"]
        read_only_fields = fields


class AppraisalListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display",
                                         read_only=True)
    cycle_name = serializers.CharField(source="cycle.name", read_only=True)
    goal_count = serializers.SerializerMethodField()
    stage_index = serializers.IntegerField(read_only=True)

    class Meta:
        model = Appraisal
        fields = ["id", "cycle", "cycle_name", "employee", "employee_name",
                  "designation", "department_name", "supervisor",
                  "supervisor_name", "status", "status_label", "stage_index",
                  "goal_count", "created_at", "updated_at"]
        read_only_fields = fields

    def get_goal_count(self, obj):
        # Reads the prefetch, never a fresh .filter() — that would bypass
        # prefetch_related and fire a query per row.
        return len(obj.goals.all())


class AppraisalDetailSerializer(AppraisalListSerializer):
    employee_detail = UserMiniSerializer(source="employee", read_only=True)
    supervisor_detail = UserMiniSerializer(source="supervisor", read_only=True)
    committee_detail = UserMiniSerializer(source="committee", many=True,
                                          read_only=True)
    goals = GoalSerializer(many=True, read_only=True)
    competency_ratings = CompetencyRatingSerializer(many=True, read_only=True)
    development_plans = DevelopmentPlanSerializer(many=True, read_only=True)
    training_plans = TrainingPlanSerializer(many=True, read_only=True)
    evidence_references = EvidenceReferenceSerializer(many=True, read_only=True)
    timeline = AppraisalAuditLogSerializer(source="audit_entries", many=True,
                                           read_only=True)
    goal_weight_total = serializers.IntegerField(read_only=True)
    promotion_readiness_label = serializers.CharField(
        source="get_promotion_readiness_display", read_only=True)
    capabilities = serializers.SerializerMethodField()

    class Meta(AppraisalListSerializer.Meta):
        fields = AppraisalListSerializer.Meta.fields + [
            "employee_detail", "supervisor_detail", "committee_detail",
            "self_assessment", "supervisor_comments", "committee_comments",
            "final_summary", "promotion_readiness",
            "promotion_readiness_label", "promotion_rationale",
            "successor_for", "goals_agreed_at", "mid_year_at",
            "self_assessed_at", "supervisor_reviewed_at",
            "committee_reviewed_at", "finalised_at", "closed_at",
            "goals", "competency_ratings", "development_plans",
            "training_plans", "evidence_references", "timeline",
            "goal_weight_total", "capabilities",
        ]
        read_only_fields = fields

    def get_capabilities(self, obj):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return {}
        return perms.capabilities(request.user, obj)


class AppraisalCreateSerializer(serializers.ModelSerializer):
    """Raise an appraisal. HR only — see appraisal.permissions."""
    committee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True), many=True, required=False)

    class Meta:
        model = Appraisal
        fields = ["cycle", "employee", "supervisor", "committee"]

    def validate(self, attrs):
        employee = attrs.get("employee")
        supervisor = attrs.get("supervisor")
        if supervisor and employee and supervisor.pk == employee.pk:
            # Nobody supervises their own appraisal. The whole process rests on
            # there being a second person in it.
            raise serializers.ValidationError(
                {"supervisor": "Somebody cannot supervise their own appraisal."})
        if employee and attrs.get("committee"):
            if any(member.pk == employee.pk for member in attrs["committee"]):
                raise serializers.ValidationError(
                    {"committee": "Somebody cannot sit on the committee "
                                  "reviewing their own appraisal."})
        return attrs


class AppraisalWriteSerializer(serializers.ModelSerializer):
    """
    The written record — each field guarded by stage ownership in the view.

    `promotion_readiness` is blankable on purpose: "not considered" and
    "development required" are different answers, and collapsing them would put
    a negative on every record that simply had not reached the question.
    """
    class Meta:
        model = Appraisal
        fields = ["self_assessment", "supervisor_comments",
                  "committee_comments", "final_summary",
                  "promotion_readiness", "promotion_rationale",
                  "successor_for", "supervisor", "committee"]


class ReturnSerializer(serializers.Serializer):
    to_status = serializers.ChoiceField(choices=Appraisal.Status.choices)
    remarks = serializers.CharField(min_length=10, max_length=2000)


class ReopenSerializer(serializers.Serializer):
    remarks = serializers.CharField(min_length=10, max_length=2000)


class RateSerializer(serializers.Serializer):
    competency = serializers.PrimaryKeyRelatedField(
        queryset=Competency.objects.filter(is_active=True))
    level = serializers.ChoiceField(choices=CompetencyRating.Level.choices)
    comment = serializers.CharField(min_length=10, max_length=4000)
    role = serializers.ChoiceField(choices=CompetencyRating.RatedBy.choices)


class AttachEvidenceSerializer(serializers.Serializer):
    goal = serializers.PrimaryKeyRelatedField(
        queryset=Goal.objects.all(), required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True,
                                 max_length=2000)


class TrainingDecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TrainingPlan.Status.choices)
    decision_note = serializers.CharField(required=False, allow_blank=True,
                                          max_length=2000)
