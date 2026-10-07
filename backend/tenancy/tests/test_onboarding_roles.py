"""The setup checklist belongs to the administrator.

Found on an employee's home page: every member of a workspace was shown the
administrator's setup checklist, and could dismiss it for everybody or tick
steps on the administrator's behalf.
"""
import pytest
from rest_framework.test import APIClient

from tenancy.context import tenant_context
from tenancy.models import OrganizationSettings

pytestmark = pytest.mark.django_db


def _member(org, django_user_model, role, username):
    with tenant_context(org):
        return django_user_model.objects.create_user(
            username=username, email=f"{username}@abc.test", password="x-Pass-123",
            role=role, organization=org)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.parametrize("role", ["maker", "checker", "approver"])
def test_non_administrators_are_not_shown_the_setup_checklist(org, django_user_model, role):
    member = _member(org, django_user_model, role, f"m-{role}")
    body = _client(member).get("/api/v1/tenant/onboarding/").json()
    assert body["applicable"] is False
    assert "checklist" not in body


@pytest.mark.parametrize("payload", [{"dismissed": True}, {"step_done": "leave_policy"}])
def test_non_administrators_cannot_change_it(org, django_user_model, payload):
    member = _member(org, django_user_model, "maker", "emp1")
    response = _client(member).post("/api/v1/tenant/onboarding/", payload, format="json")
    assert response.status_code == 403
    row = OrganizationSettings.objects.get(organization=org)
    assert row.onboarding_dismissed_at is None
    assert not (row.onboarding_steps or {}).get("leave_policy")


def test_the_administrator_still_gets_it(org, django_user_model):
    admin = _member(org, django_user_model, "admin", "adm1")
    client = _client(admin)
    body = client.get("/api/v1/tenant/onboarding/").json()
    assert body["applicable"] is True and body["checklist"]
    assert client.post("/api/v1/tenant/onboarding/", {"dismissed": True},
                       format="json").status_code == 200
