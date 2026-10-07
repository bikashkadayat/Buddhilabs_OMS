from django.contrib import admin
from .models import (
    Memo,
    MemoApprovalStep,
    MemoAttachment,
    MemoTemplate,
    MemoWorkflowStep,
)


class MemoWorkflowStepInline(admin.TabularInline):
    """The approval matrix, inline on the memo it routes."""
    model = MemoWorkflowStep
    extra = 0
    fields = ('sequence', 'assignee', 'assignee_name', 'role_type', 'status',
              'designation', 'department_label', 'activated_at', 'acted_at', 'remarks')
    readonly_fields = ('acted_at',)
    ordering = ('sequence',)


class MemoAttachmentInline(admin.TabularInline):
    model = MemoAttachment
    extra = 0
    fields = ('original_name', 'file', 'size', 'uploaded_by', 'uploaded_at')
    readonly_fields = ('uploaded_at',)


@admin.register(Memo)
class MemoAdmin(admin.ModelAdmin):
    inlines = [MemoWorkflowStepInline, MemoAttachmentInline]
    list_display = ('memo_number', 'memo_type', 'status',
                    'created_by', 'pending_with', 'created_at')
    list_filter = ('status', 'memo_type', 'created_at')

    search_fields = ('memo_number', 'subject', 'created_by__username', 'created_by__first_name', 'created_by__last_name')
    ordering = ('-created_at',)
    readonly_fields = ('memo_number', 'created_at', 'updated_at',
                       'approved_at', 'archived_at')

    @admin.display(description='Pending with')
    def pending_with(self, obj):
        """
        Replaces the old `current_approver` column. Routing lives in the matrix
        now, so the answer comes from the active step rather than from a field.
        """
        step = obj.active_step
        if step is None:
            return '—'
        return f'{step.display_name} ({step.get_role_type_display()})'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(created_by=request.user)

@admin.register(MemoApprovalStep)
class MemoApprovalStepAdmin(admin.ModelAdmin):
    list_display = ('memo', 'step_order', 'actor', 'action', 'acted_at')
    list_filter = ('action', 'acted_at')
    search_fields = ('memo__memo_number', 'actor__username', 'actor__first_name', 'actor__last_name')
    ordering = ('memo', 'step_order')
    readonly_fields = ('acted_at',)

@admin.register(MemoWorkflowStep)
class MemoWorkflowStepAdmin(admin.ModelAdmin):
    list_display = ('memo', 'sequence', 'assignee', 'role_type', 'status', 'acted_at')
    list_filter = ('role_type', 'status')
    search_fields = ('memo__memo_number', 'assignee__username',
                     'assignee__first_name', 'assignee__last_name')
    ordering = ('memo', 'sequence')
    readonly_fields = ('created_at',)


@admin.register(MemoAttachment)
class MemoAttachmentAdmin(admin.ModelAdmin):
    list_display = ('memo', 'original_name', 'size', 'uploaded_by', 'uploaded_at')
    search_fields = ('memo__memo_number', 'original_name')
    ordering = ('-uploaded_at',)
    readonly_fields = ('uploaded_at',)


@admin.register(MemoTemplate)
class MemoTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'memo_type', 'is_active')
    list_filter = ('memo_type', 'is_active')
    search_fields = ('name', 'subject_template')
    ordering = ('name',)
