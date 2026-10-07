"""django-filter FilterSets for advanced admin list filtering."""
import django_filters as df
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import Leave, LeaveDayRecord

User = get_user_model()


class LeaveFilter(df.FilterSet):
    department = df.CharFilter(method='filter_department')
    date_from = df.DateFilter(field_name='start_date', lookup_expr='gte')
    date_to = df.DateFilter(field_name='end_date', lookup_expr='lte')
    created_from = df.DateFilter(field_name='created_at', lookup_expr='date__gte')
    created_to = df.DateFilter(field_name='created_at', lookup_expr='date__lte')

    class Meta:
        model = Leave
        fields = ['user', 'status', 'leave_type', 'approver']

    def filter_department(self, queryset, name, value):
        return queryset.filter(
            Q(user__department__iexact=value) | Q(user__department_ref__code__iexact=value)
        )


class LeaveDayRecordFilter(df.FilterSet):
    department = df.CharFilter(method='filter_department')
    date_from = df.DateFilter(field_name='date', lookup_expr='gte')
    date_to = df.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = LeaveDayRecord
        fields = ['user', 'status', 'leave_type', 'week_number', 'month', 'year']

    def filter_department(self, queryset, name, value):
        return queryset.filter(
            Q(user__department__iexact=value) | Q(user__department_ref__code__iexact=value)
        )


class UserFilter(df.FilterSet):
    department = df.CharFilter(method='filter_department')
    joined_after = df.DateFilter(field_name='date_joined', lookup_expr='date__gte')

    class Meta:
        model = User
        fields = ['role', 'is_active']

    def filter_department(self, queryset, name, value):
        return queryset.filter(
            Q(department__iexact=value) | Q(department_ref__code__iexact=value)
        )
