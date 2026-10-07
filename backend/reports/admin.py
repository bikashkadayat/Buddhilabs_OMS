from django.contrib import admin

from .models import ReportRun, ScheduledReport


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    list_display = ("report_type", "status", "requested_by", "created_at", "completed_at")
    list_filter = ("report_type", "status", "created_at")
    readonly_fields = ("id", "created_at", "completed_at")


@admin.register(ScheduledReport)
class ScheduledReportAdmin(admin.ModelAdmin):
    list_display = ("report_type", "cron_expression", "is_active", "last_run_at")
    list_filter = ("report_type", "is_active")
