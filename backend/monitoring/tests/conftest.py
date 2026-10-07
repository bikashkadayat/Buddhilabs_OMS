"""Fixtures for the monitoring and alerting suite."""
from datetime import date, datetime, timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from users.models import User


def make_user(username, role):
    user = User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.title(), last_name="Ops", role=role,
        date_of_joining=date(2020, 1, 1))
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(2020, 1, 1, 9, 0)))
    return user


@pytest.fixture
def hr_user(db):
    return make_user("mon_hr", User.Roles.APPROVER)


@pytest.fixture
def admin_user(db):
    return make_user("mon_admin", User.Roles.ADMIN)


@pytest.fixture
def manager(db):
    return make_user("mon_head", User.Roles.CHECKER)


@pytest.fixture
def employee(db):
    return make_user("mon_emp", User.Roles.MAKER)


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def auth(api):
    def _login(user):
        api.force_authenticate(user=user)
        return api
    return _login


@pytest.fixture
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def beat(db):
    """Write a heartbeat at a chosen age, so 'overdue' can be tested without
    waiting for real time to pass."""
    from audit.models import AuditLog
    from monitoring import heartbeat

    def _beat(job, minutes_ago=0, ok=True):
        entry = heartbeat.record(job, ok=ok)
        if minutes_ago:
            AuditLog.objects.filter(pk=entry.pk).update(
                created_at=timezone.now() - timedelta(minutes=minutes_ago))
        return entry
    return _beat


@pytest.fixture
def alert_recipients(settings):
    settings.ALERT_EMAILS = "ops@nif.test"
    settings.ALERT_WEBHOOK_URL = ""
    return ["ops@nif.test"]
