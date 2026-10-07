import pytest
from datetime import date, timedelta
from rest_framework.test import APIClient
from users.models import User
from leaves.models import Leave
from .models import AuditLog
from .services import log_action


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(username="admin1", email="admin1@nif.test", password="pass12345", role=User.Roles.ADMIN)


@pytest.fixture
def maker_user(db):
    from datetime import date
    # Permanent + tenured so the category engine grants a non-zero Annual balance
    # (otherwise the apply guard blocks the leave used to exercise the audit log).
    return User.objects.create_user(
        username="maker1", email="maker1@nif.test", password="pass12345", role=User.Roles.MAKER,
        employment_type=User.EmploymentType.PERMANENT, date_of_joining=date(2018, 1, 1),
    )


@pytest.mark.django_db
def test_log_action_creates_entry(maker_user):
    leave = Leave.objects.create(
        user=maker_user,
        leave_type="annual",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=1),
        reason="test",
    )
    log_action(maker_user, AuditLog.Action.CREATE, instance=leave)

    entry = AuditLog.objects.get()
    assert entry.actor == maker_user
    assert entry.action == AuditLog.Action.CREATE
    assert entry.object_repr == str(leave)
    assert str(entry.object_id) == str(leave.pk)


@pytest.mark.django_db
def test_log_action_with_unauthenticated_actor_stores_null_actor():
    from django.contrib.auth.models import AnonymousUser
    log_action(AnonymousUser(), AuditLog.Action.LOGIN)
    entry = AuditLog.objects.get()
    assert entry.actor is None


@pytest.mark.django_db
def test_audit_log_is_immutable(maker_user):
    log_action(maker_user, AuditLog.Action.CREATE)
    entry = AuditLog.objects.get()

    entry.action = AuditLog.Action.DELETE
    with pytest.raises(ValueError):
        entry.save()

    with pytest.raises(ValueError):
        entry.delete()


@pytest.mark.django_db
def test_leave_creation_writes_audit_entry(maker_user):
    client = APIClient()
    client.force_authenticate(user=maker_user)

    response = client.post("/api/v1/leaves/", {
        "leave_type": "annual",
        "start_date": str(date.today()),
        "end_date": str(date.today() + timedelta(days=1)),
        "reason": "vacation",
    })

    assert response.status_code == 201
    assert AuditLog.objects.filter(action=AuditLog.Action.SUBMIT).count() == 1


@pytest.mark.django_db
def test_audit_log_endpoint_requires_admin(maker_user, admin_user):
    client = APIClient()
    client.force_authenticate(user=maker_user)
    response = client.get("/api/v1/audit/")
    assert response.status_code == 403

    client.force_authenticate(user=admin_user)
    response = client.get("/api/v1/audit/")
    assert response.status_code == 200
