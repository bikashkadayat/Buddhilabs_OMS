"""
Query filters for the task list.

Declared as a FilterSet rather than read ad hoc from request.query_params, so the
filterable surface is one declaration the schema can describe and an unknown
parameter cannot silently do nothing — a caller who gets a 200 and the wrong rows
is worse off than one who gets an error.
"""
import django_filters as filters
from django.db.models import Q

from .models import Task


class TaskFilterSet(filters.FilterSet):
    status = filters.CharFilter(field_name="status", lookup_expr="iexact")
    # Several statuses at once: ?status_in=assigned,in_progress
    status_in = filters.CharFilter(method="filter_status_in")
    priority = filters.CharFilter(field_name="priority", lookup_expr="iexact")
    department = filters.CharFilter(method="filter_department")
    department_id = filters.UUIDFilter(field_name="department_id")
    created_by = filters.UUIDFilter(field_name="created_by_id")
    reviewer = filters.UUIDFilter(field_name="reviewer_id")
    assignee = filters.UUIDFilter(field_name="assignees__user_id")
    # Inclusive on both ends: a user picking 1st–31st means the whole month.
    due_from = filters.DateFilter(field_name="due_date", lookup_expr="gte")
    due_to = filters.DateFilter(field_name="due_date", lookup_expr="lte")
    created_from = filters.DateFilter(field_name="created_at__date", lookup_expr="gte")
    created_to = filters.DateFilter(field_name="created_at__date", lookup_expr="lte")

    class Meta:
        model = Task
        fields = ["status", "priority", "department", "created_by", "reviewer",
                  "assignee", "due_from", "due_to"]

    def filter_status_in(self, queryset, name, value):
        wanted = [v.strip().lower() for v in (value or "").split(",") if v.strip()]
        valid = {choice for choice, _ in Task.Status.choices}
        wanted = [v for v in wanted if v in valid]
        return queryset.filter(status__in=wanted) if wanted else queryset

    def filter_department(self, queryset, name, value):
        """
        Matches either the department row or the snapshot, because a task raised
        for a free-text department only has the snapshot.
        """
        return queryset.filter(
            Q(department__name__iexact=value) | Q(department_name__iexact=value))
