"""
Search and filter for the circular list.

Every field the brief's Search section names - reference number, subject,
department, classification, status, date - is here, plus a date RANGE because
"date" on a governance archive nearly always means "between these two".
"""
import django_filters

from .models import Circular


class CircularFilter(django_filters.FilterSet):
    # Free-text across the three identifying fields. The viewset also honours a
    # bare `search` parameter for the same purpose; this one exists so the filter
    # form and the API agree on the name.
    search = django_filters.CharFilter(method="filter_search")
    department = django_filters.UUIDFilter(field_name="department_id")
    # The legacy free-text department label, for a half-migrated organisation whose
    # older users have no department FK at all.
    department_name = django_filters.CharFilter(
        field_name="department_name", lookup_expr="iexact")
    issue_date_from = django_filters.DateFilter(
        field_name="issue_date", lookup_expr="gte")
    issue_date_to = django_filters.DateFilter(
        field_name="issue_date", lookup_expr="lte")
    created_from = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = Circular
        fields = ["status", "classification", "category", "priority",
                  "acknowledgement_required", "circular_number"]

    def filter_search(self, queryset, name, value):
        from django.db.models import Q
        value = (value or "").strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(circular_number__icontains=value)
            | Q(subject__icontains=value)
            | Q(external_reference__icontains=value))
