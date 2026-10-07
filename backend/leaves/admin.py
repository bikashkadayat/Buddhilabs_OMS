from django.contrib import admin
from .models import Leave, LeaveBalance, CalendarEvent

@admin.register(Leave)
class LeaveAdmin(admin.ModelAdmin):
    list_display = ('user', 'leave_type', 'start_date', 'end_date', 'status', 'approver', 'created_at')
    list_filter = ('status', 'leave_type', 'start_date', 'created_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'reason')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)

@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ('user', 'leave_type', 'year', 'total_allocated', 'used_so_far', 'remaining')
    list_filter = ('leave_type', 'year')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    ordering = ('user', 'leave_type', 'year')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)

@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    """HR edits the Nepali calendar here; the loader is for bulk years."""
    list_display = ("date", "display_name", "event_type", "tithi", "is_public_holiday", "is_active")
    list_filter = ("event_type", "is_public_holiday", "is_active")
    search_fields = ("name", "name_np", "tithi")
    ordering = ("-date",)
    date_hierarchy = "date"
