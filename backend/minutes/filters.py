"""
Query filters for the minute list.

Declared as a FilterSet rather than read ad hoc from request.query_params, so the
filterable surface is one declaration the schema can describe and an unknown parameter
cannot silently do nothing. The memo module shipped its filter backends before it had
any filterset fields, which meant `?status=draft` was accepted and ignored - the worst
kind of API behaviour, because the caller gets a 200 and the wrong rows.

The set matches the archive's own filter bar (manual p.10): Date from, Date to, Search
By, and Initiated By.
"""
import django_filters as filters

from .models import Minute


class MinuteFilterSet(filters.FilterSet):
    status = filters.CharFilter(field_name="status", lookup_expr="iexact")
    minute_type = filters.CharFilter(
        field_name="minute_type__code", lookup_expr="iexact")
    department = filters.CharFilter(method="filter_department")
    created_by = filters.UUIDFilter(field_name="created_by_id")
    reference_number = filters.CharFilter(
        field_name="reference_number", lookup_expr="icontains")
    # Inclusive on both ends: a user picking 1st-31st means the whole month.
    date_from = filters.DateFilter(field_name="meeting_date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="meeting_date", lookup_expr="lte")
    created_from = filters.DateFilter(field_name="created_at__date", lookup_expr="gte")
    created_to = filters.DateFilter(field_name="created_at__date", lookup_expr="lte")

    class Meta:
        model = Minute
        fields = ["status", "minute_type", "department", "created_by",
                  "reference_number", "date_from", "date_to"]

    def filter_department(self, queryset, name, value):
        """
        Matches either the department row or the free-text snapshot, because a minute
        raised before the department table existed only has the snapshot.
        """
        return queryset.filter(department__name__iexact=value) | queryset.filter(
            department_name__iexact=value)
