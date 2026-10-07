import pytest
from rest_framework.test import APIClient

from audit.models import AuditLog
from users.models import User


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def maker(db):
    return User.objects.create_user(username="umaker", email="umaker@nif.test", password="pass12345", first_name="Uma", role=User.Roles.MAKER)


@pytest.fixture
def admin(db):
    return User.objects.create_user(username="uadmin", email="uadmin@nif.test", password="pass12345", role=User.Roles.ADMIN)


# --- login (kept + enhanced) ----------------------------------------------
@pytest.mark.django_db
def test_login_returns_tokens_and_user_block(api, maker):
    resp = api.post("/api/v1/auth/login/", {"email": "umaker@nif.test", "password": "pass12345"}, format="json")
    assert resp.status_code == 200
    assert "access" in resp.data and "refresh" in resp.data
    user = resp.data["user"]
    assert user["role"] == "maker"
    assert user["email"] == "umaker@nif.test"
    assert "must_change_password" in user
    assert AuditLog.objects.filter(changes__event="LOGIN_SUCCESS").exists()


@pytest.mark.django_db
def test_login_blocks_deactivated_user(api):
    User.objects.create_user(username="dead", email="dead@nif.test", password="pass12345", role=User.Roles.MAKER, is_active=False)
    resp = api.post("/api/v1/auth/login/", {"email": "dead@nif.test", "password": "pass12345"}, format="json")
    assert resp.status_code == 403
    assert "turned off" in resp.data["detail"].lower()   # plain words, not "deactivated"
    assert AuditLog.objects.filter(changes__event="LOGIN_BLOCKED_INACTIVE").exists()


@pytest.mark.django_db
def test_login_wrong_password(api, maker):
    resp = api.post("/api/v1/auth/login/", {"email": "umaker@nif.test", "password": "wrong"}, format="json")
    assert resp.status_code == 400


# --- registration removed --------------------------------------------------
@pytest.mark.django_db
def test_registration_endpoint_removed(api):
    resp = api.post("/api/v1/auth/register/", {"email": "x@y.z", "password": "pass12345"}, format="json")
    assert resp.status_code == 404


# --- change password -------------------------------------------------------
@pytest.mark.django_db
def test_change_password_flow(api, maker):
    assert maker.must_change_password is True
    api.force_authenticate(maker)
    bad = api.post("/api/v1/auth/change-password/", {"current_password": "nope", "new_password": "newpass123"}, format="json")
    assert bad.status_code == 400

    ok = api.post("/api/v1/auth/change-password/", {"current_password": "pass12345", "new_password": "newpass123"}, format="json")
    assert ok.status_code == 200
    maker.refresh_from_db()
    assert maker.must_change_password is False
    assert maker.check_password("newpass123")
    assert AuditLog.objects.filter(changes__event="PASSWORD_CHANGED").exists()


# --- admin user management -------------------------------------------------
@pytest.mark.django_db
def test_admin_creates_user_with_generated_credentials(api, admin):
    api.force_authenticate(admin)
    resp = api.post("/api/v1/users/admin/users/", {
        "email": "newemp@nif.test", "first_name": "New", "last_name": "Emp", "role": "checker",
    }, format="json")
    assert resp.status_code == 201, resp.data
    assert resp.data["employee_id"].startswith("NIFN-EMP-")
    assert resp.data["generated_password"]  # returned once for the admin to share

    created = User.objects.get(email="newemp@nif.test")
    assert created.must_change_password is True
    assert created.created_by_id == admin.id
    assert AuditLog.objects.filter(changes__event="USER_CREATED").exists()


@pytest.mark.django_db
def test_admin_reset_deactivate_activate_change_role(api, admin, maker):
    api.force_authenticate(admin)

    r = api.post(f"/api/v1/users/admin/users/{maker.id}/reset-password/", {}, format="json")
    assert r.status_code == 200 and r.data["generated_password"]
    maker.refresh_from_db(); assert maker.must_change_password is True

    r = api.post(f"/api/v1/users/admin/users/{maker.id}/deactivate/", {}, format="json")
    assert r.status_code == 200
    maker.refresh_from_db(); assert maker.is_active is False

    r = api.post(f"/api/v1/users/admin/users/{maker.id}/activate/", {}, format="json")
    maker.refresh_from_db(); assert maker.is_active is True

    r = api.post(f"/api/v1/users/admin/users/{maker.id}/change-role/", {"role": "approver"}, format="json")
    maker.refresh_from_db(); assert maker.role == "approver"

    assert AuditLog.objects.filter(changes__event="ROLE_CHANGED").exists()


@pytest.mark.django_db
def test_admin_cannot_deactivate_self(api, admin):
    api.force_authenticate(admin)
    r = api.post(f"/api/v1/users/admin/users/{admin.id}/deactivate/", {}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_user_management_requires_admin(api, maker):
    api.force_authenticate(maker)
    assert api.get("/api/v1/users/admin/users/").status_code == 403
    assert api.post("/api/v1/users/admin/users/", {"email": "z@z.z"}, format="json").status_code == 403


@pytest.mark.django_db
def test_current_user_endpoint_includes_new_fields(api, maker):
    api.force_authenticate(maker)
    resp = api.get("/api/v1/auth/user/")
    assert resp.status_code == 200
    for f in ["employee_id", "role", "must_change_password", "department_name"]:
        assert f in resp.data
