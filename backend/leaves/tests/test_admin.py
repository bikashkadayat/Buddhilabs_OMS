from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from audit.models import AuditLog
from leaves.models import (
    Department, EnterpriseLeaveBalance, Holiday, Leave, LeavePolicy, LeaveType,
)
from .conftest import MONDAY


# --- permissions -----------------------------------------------------------
@pytest.mark.django_db
def test_admin_endpoints_reject_non_admin(api, maker):
    api.force_authenticate(maker)
    assert api.get('/api/v1/leaves/admin/employees/').status_code == 403
    assert api.get('/api/v1/leaves/admin/policies/').status_code == 403
    assert api.get('/api/v1/leaves/admin/reports/').status_code == 403


# --- employees + adjust balance -------------------------------------------
@pytest.mark.django_db
def test_employee_list_and_detail(api, admin, maker):
    api.force_authenticate(admin)
    resp = api.get('/api/v1/leaves/admin/employees/')
    assert resp.status_code == 200
    emails = [e['email'] for e in resp.data['results']]
    assert maker.email in emails

    detail = api.get(f'/api/v1/leaves/admin/employees/{maker.id}/')
    assert detail.status_code == 200
    assert set(['employee', 'balances', 'applications', 'monthly_summaries']).issubset(detail.data.keys())


@pytest.mark.django_db
def test_adjust_balance_writes_audit_and_changes_available(api, admin, maker, annual):
    api.force_authenticate(admin)
    resp = api.post(f'/api/v1/leaves/admin/employees/{maker.id}/adjust-balance/', {
        'leave_type': 'ANNUAL', 'year': 2026, 'delta': '5.00', 'reason': 'Annual bonus grant',
    })
    assert resp.status_code == 200, resp.data
    assert resp.data['adjustment_days'] == '5.00'
    # entitled 18 + adjustment 5 = available 23
    assert Decimal(resp.data['available_days']) == Decimal('23')

    entry = AuditLog.objects.filter(changes__event='LEAVE_BALANCE_ADJUSTED').first()
    assert entry is not None
    assert entry.changes['reason'] == 'Annual bonus grant'


@pytest.mark.django_db
def test_adjust_balance_requires_reason(api, admin, maker):
    api.force_authenticate(admin)
    resp = api.post(f'/api/v1/leaves/admin/employees/{maker.id}/adjust-balance/', {
        'leave_type': 'ANNUAL', 'year': 2026, 'delta': '5.00', 'reason': 'x',
    })
    assert resp.status_code == 400  # reason min_length 5


# --- bulk action -----------------------------------------------------------
@pytest.mark.django_db
def test_bulk_approve(api, admin, maker):
    l1 = Leave.objects.create(user=maker, leave_type='annual', reason='a', start_date=MONDAY, end_date=MONDAY)
    l2 = Leave.objects.create(user=maker, leave_type='sick', reason='b', start_date=MONDAY + timedelta(days=1), end_date=MONDAY + timedelta(days=1))
    api.force_authenticate(admin)
    resp = api.post('/api/v1/leaves/admin/leaves/bulk-action/', {
        'leave_ids': [str(l1.id), str(l2.id)], 'action': 'approve',
    }, format='json')
    assert resp.status_code == 200, resp.data
    assert resp.data['succeeded'] == 2
    l1.refresh_from_db(); l2.refresh_from_db()
    assert l1.status == 'approved' and l2.status == 'approved'


@pytest.mark.django_db
def test_bulk_reject_requires_comment(api, admin, maker):
    l1 = Leave.objects.create(user=maker, leave_type='annual', reason='a', start_date=MONDAY, end_date=MONDAY)
    api.force_authenticate(admin)
    resp = api.post('/api/v1/leaves/admin/leaves/bulk-action/', {
        'leave_ids': [str(l1.id)], 'action': 'reject', 'comment': 'no',
    }, format='json')
    assert resp.status_code == 400


# --- holidays CSV bulk import ---------------------------------------------
@pytest.mark.django_db
def test_holiday_bulk_import(api, admin):
    csv_content = (
        'date,name,type,description\n'
        '2026-12-25,Christmas,public,Holiday\n'
        ',Missing Date,public,bad row\n'
        '2026-12-31,New Year Eve,optional,\n'
    )
    upload = SimpleUploadedFile('holidays.csv', csv_content.encode('utf-8'), content_type='text/csv')
    api.force_authenticate(admin)
    resp = api.post('/api/v1/leaves/admin/holidays/bulk-import/', {'file': upload}, format='multipart')
    assert resp.status_code in (200, 207)
    assert resp.data['created'] == 2
    assert len(resp.data['errors']) == 1
    assert resp.data['errors'][0]['line'] == 3  # the row with a missing date
    assert Holiday.objects.filter(date='2026-12-25').exists()


# --- policies --------------------------------------------------------------
@pytest.mark.django_db
def test_policy_create_warns_on_overlap_and_deprecate_on_delete(api, admin, annual, eng_department):
    api.force_authenticate(admin)
    base = {
        'leave_type': str(annual.id), 'department': str(eng_department.id),
        'role': '', 'days_per_year': '20.00', 'effective_from': '2026-01-01',
    }
    first = api.post('/api/v1/leaves/admin/policies/', base, format='json')
    assert first.status_code == 201
    assert 'warning' not in first.data

    # Different effective_from (so the unique constraint is satisfied) but an
    # overlapping open-ended date range => should warn.
    second = api.post('/api/v1/leaves/admin/policies/', {**base, 'days_per_year': '25.00', 'effective_from': '2026-06-01'}, format='json')
    assert second.status_code == 201
    assert 'warning' in second.data  # overlaps the first

    # "delete" deprecates rather than removing
    pid = second.data['id']
    dele = api.delete(f'/api/v1/leaves/admin/policies/{pid}/')
    assert dele.status_code == 200
    assert LeavePolicy.objects.filter(pk=pid).exists()
    assert LeavePolicy.objects.get(pk=pid).effective_until is not None


# --- CRUD + audit ----------------------------------------------------------
@pytest.mark.django_db
def test_department_crud_is_audited(api, admin, checker):
    api.force_authenticate(admin)
    # An active department needs a head (Phase DEPARTMENT-GOVERNANCE-HARDENING).
    resp = api.post('/api/v1/leaves/admin/departments/',
                    {'name': 'Finance', 'code': 'FIN', 'head': str(checker.id)},
                    format='json')
    assert resp.status_code == 201, resp.data
    assert Department.objects.filter(code='FIN').exists()
    assert AuditLog.objects.filter(action=AuditLog.Action.CREATE, object_id=resp.data['id']).exists()


@pytest.mark.django_db
def test_leave_type_crud(api, admin):
    api.force_authenticate(admin)
    resp = api.post('/api/v1/leaves/admin/leave-types/', {
        'code': 'BEREAVEMENT', 'name': 'Bereavement Leave', 'default_days_per_year': '5.00',
    }, format='json')
    assert resp.status_code == 201
    assert LeaveType.objects.filter(code='BEREAVEMENT').exists()


# --- filters ---------------------------------------------------------------
@pytest.mark.django_db
def test_employee_filter_by_role(api, admin, maker, checker):
    api.force_authenticate(admin)
    resp = api.get('/api/v1/leaves/admin/employees/?role=checker')
    roles = {e['role'] for e in resp.data['results']}
    assert roles == {'checker'}


@pytest.mark.django_db
def test_admin_leaves_filter_by_status_and_ordering(api, admin, maker):
    Leave.objects.create(user=maker, leave_type='annual', reason='a', start_date=MONDAY, end_date=MONDAY, status='pending')
    api.force_authenticate(admin)
    resp = api.get('/api/v1/admin/leaves/?status=pending&ordering=-created_at')
    assert resp.status_code == 200
    assert all(row['status'] == 'pending' for row in resp.data['results'])


@pytest.mark.django_db
def test_reports_metadata(api, admin):
    api.force_authenticate(admin)
    resp = api.get('/api/v1/leaves/admin/reports/')
    assert resp.status_code == 200
    keys = {r['key'] for r in resp.data['reports']}
    assert 'monthly_attendance' in keys
