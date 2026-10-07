"""
Django admin for appraisal — read-mostly on purpose.

`status` is NOT editable here. appraisal.workflow is the only thing that may
assign to it, and an admin dropdown that skipped the engine would produce an
appraisal in a stage with no timeline row explaining how it got there — in the
one record most likely to be contested.
"""
from django.contrib import admin

from .models import (
    Appraisal, AppraisalAuditLog, AppraisalCycle, Competency, CompetencyRating,
    DevelopmentPlan, EvidenceReference, Goal, TrainingPlan,
)


class GoalInline(admin.TabularInline):
    model = Goal
    extra = 0
    readonly_fields = ("owner_name",)


class RatingInline(admin.TabularInline):
    model = CompetencyRating
    extra = 0
    readonly_fields = ("competency_name", "rated_by_name", "rated_at")


class AuditInline(admin.TabularInline):
    model = AppraisalAuditLog
    extra = 0
    can_delete = False
    readonly_fields = ("action", "actor_name", "from_status", "to_status",
                       "remarks", "metadata", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AppraisalCycle)
class AppraisalCycleAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "period_start", "period_end")
    list_filter = ("status",)
    search_fields = ("name",)


@admin.register(Appraisal)
class AppraisalAdmin(admin.ModelAdmin):
    list_display = ("employee_name", "cycle", "status", "supervisor_name",
                    "department_name")
    list_filter = ("status", "cycle")
    search_fields = ("employee_name", "department_name")
    readonly_fields = ("id", "status", "employee_name", "supervisor_name",
                       "goals_agreed_at", "self_assessed_at",
                       "supervisor_reviewed_at", "finalised_at", "closed_at",
                       "created_at", "updated_at")
    inlines = [GoalInline, RatingInline, AuditInline]


@admin.register(Competency)
class CompetencyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "ordering", "is_active")
    list_editable = ("ordering", "is_active")


@admin.register(TrainingPlan)
class TrainingPlanAdmin(admin.ModelAdmin):
    list_display = ("title", "appraisal", "priority", "status",
                    "decided_by_name")
    list_filter = ("status", "priority")


@admin.register(DevelopmentPlan)
class DevelopmentPlanAdmin(admin.ModelAdmin):
    list_display = ("area", "appraisal", "status", "target_date")
    list_filter = ("status",)


@admin.register(EvidenceReference)
class EvidenceReferenceAdmin(admin.ModelAdmin):
    """Citations are immutable: they record what a source said at a moment."""
    list_display = ("appraisal", "source", "period_start", "period_end",
                    "captured_at")
    readonly_fields = tuple(f.name for f in EvidenceReference._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AppraisalAuditLog)
class AppraisalAuditLogAdmin(admin.ModelAdmin):
    list_display = ("appraisal", "action", "actor_name", "created_at")
    list_filter = ("action",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
