"""
Memo list filters.

A FilterSet rather than plain `filterset_fields` because the Department Memo and
Archive screens need a created-date range, which the field shorthand cannot
express. Everything the UI offers as a control is declared here, so a filter that
appears in the interface always has a server-side implementation behind it - the
gap between those two is what made every earlier memo list filter client-side and
therefore wrong past the first page.
"""
import django_filters as filters

from .models import Memo


class MemoFilter(filters.FilterSet):
    # Multi-select status, so the Outbox can ask for several stages at once
    # (?status=under_review&status=recommended) without N requests.
    status = filters.MultipleChoiceFilter(choices=Memo.Status.choices)
    memo_type = filters.MultipleChoiceFilter(choices=Memo.MemoType.choices)
    reference_number = filters.CharFilter(
        field_name="reference_number", lookup_expr="icontains")

    department = filters.UUIDFilter(field_name="department_id")
    # Free-text department, for authors whose unit exists only as the legacy
    # CharField and never got a Department row.
    department_name = filters.CharFilter(
        field_name="department_name", lookup_expr="iexact")

    created_from = filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    approved_from = filters.DateFilter(field_name="approved_at", lookup_expr="date__gte")
    approved_to = filters.DateFilter(field_name="approved_at", lookup_expr="date__lte")

    created_by = filters.UUIDFilter(field_name="created_by_id")

    class Meta:
        model = Memo
        fields = [
            "status", "memo_type",
            "reference_number", "department", "department_name",
            "created_from", "created_to", "approved_from", "approved_to",
            "created_by",
        ]
