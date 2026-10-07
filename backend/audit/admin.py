from django.contrib import admin
from .models import AuditLog

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'content_type', 'object_repr')
    list_filter = ('action', 'content_type', 'created_at')
    search_fields = ('object_repr', 'actor__username', 'actor__first_name', 'actor__last_name')
    ordering = ('-created_at',)
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
