"""
Django admin for tasks — read-mostly on purpose.

Status is NOT editable here. tasks.workflow is the only thing that may assign to
it, and an admin dropdown that skipped the engine would produce a task in a state
with no timeline row explaining how it got there, which is exactly the evidence
gap the module exists to close.
"""
from django.contrib import admin

from .models import (
    Task, TaskAssignee, TaskAttachment, TaskAttachmentDownload, TaskAuditLog,
    TaskChecklistGroup, TaskChecklistItem, TaskComment, TaskNumberSequence,
    TaskTemplate, TaskTemplateGroup, TaskTemplateItem,
)


class TaskAssigneeInline(admin.TabularInline):
    model = TaskAssignee
    extra = 0
    readonly_fields = ("user_name", "designation", "department_label", "accepted_at",
                       "added_at")


class TaskChecklistGroupInline(admin.TabularInline):
    model = TaskChecklistGroup
    extra = 0


class TaskChecklistInline(admin.TabularInline):
    model = TaskChecklistItem
    extra = 0
    readonly_fields = ("done_by_name", "done_at")


class TaskAuditInline(admin.TabularInline):
    model = TaskAuditLog
    extra = 0
    can_delete = False
    readonly_fields = ("action", "actor_name", "from_status", "to_status", "remarks",
                       "metadata", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("task_number", "title", "status", "priority", "department_name",
                    "due_date", "progress_percent", "created_by_name")
    list_filter = ("status", "priority", "due_date")
    search_fields = ("task_number", "title", "description", "department_name")
    readonly_fields = ("id", "task_number", "status", "created_by", "created_by_name",
                       "assigned_at", "accepted_at", "started_at", "submitted_at",
                       "completed_at", "closed_at", "cancelled_at", "created_at",
                       "updated_at")
    inlines = [TaskAssigneeInline, TaskChecklistGroupInline,
               TaskChecklistInline, TaskAuditInline]
    date_hierarchy = "created_at"


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = ("task", "author_name", "created_at")
    search_fields = ("body", "author_name")
    readonly_fields = ("id", "task", "author", "author_name", "body", "created_at")


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "task", "is_evidence", "uploaded_by_name",
                    "uploaded_at")
    list_filter = ("is_evidence",)
    readonly_fields = ("id", "size", "content_type", "uploaded_by", "uploaded_by_name",
                       "uploaded_at")


@admin.register(TaskAuditLog)
class TaskAuditLogAdmin(admin.ModelAdmin):
    list_display = ("task", "action", "actor_name", "from_status", "to_status",
                    "created_at")
    list_filter = ("action",)
    search_fields = ("task__task_number", "actor_name", "remarks")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TaskNumberSequence)
class TaskNumberSequenceAdmin(admin.ModelAdmin):
    list_display = ("year", "last_value")
    readonly_fields = ("year", "last_value")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TaskAttachmentDownload)
class TaskAttachmentDownloadAdmin(admin.ModelAdmin):
    """
    Read-only, like the audit log. A download record that could be edited or
    deleted would not be an access log — it would be a suggestion.
    """
    list_display = ("attachment", "user_name", "ip_address", "downloaded_at")
    search_fields = ("user_name", "attachment__original_name")
    readonly_fields = ("attachment", "user", "user_name", "ip_address",
                       "downloaded_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TaskTemplateGroupInline(admin.TabularInline):
    model = TaskTemplateGroup
    extra = 0


class TaskTemplateItemInline(admin.TabularInline):
    model = TaskTemplateItem
    extra = 0


@admin.register(TaskTemplate)
class TaskTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "priority", "default_due_in_days", "department_name",
                    "usage_count", "is_active")
    list_filter = ("is_active", "priority")
    search_fields = ("name", "description", "department_name")
    readonly_fields = ("id", "created_by", "created_by_name", "usage_count",
                       "created_at", "updated_at")
    inlines = [TaskTemplateGroupInline, TaskTemplateItemInline]
