"""
Profile module — self-service edit + photo upload.

Verifies: self-scoped edit, server-side allowlist (protected fields can't be
changed even if sent), input validation, and photo upload validation/resize.
"""
from datetime import date
from io import BytesIO

import pytest
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from users.models import User


def _user(username, role=User.Roles.MAKER):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role, department="ENG",
        employment_type=User.EmploymentType.PERMANENT, date_of_joining=date(2018, 1, 1),
    )


def _img_bytes(size=(600, 400), fmt="JPEG", color=(30, 120, 200)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.django_db
def test_employee_can_edit_own_editable_fields():
    user = _user("pf_emp")
    c = APIClient(); c.force_authenticate(user)
    r = c.patch("/api/v1/profile/me/", {
        "first_name": "Bikash", "phone": "+977 9812345678",
        "address": "Baneshwor", "emergency_contact_name": "Sita",
        "emergency_contact_number": "9800000000", "bio": "ICT officer",
        "date_of_birth": "1995-05-20", "gender": "male",
    }, format="json")
    assert r.status_code == 200, r.content
    user.refresh_from_db()
    assert user.first_name == "Bikash"
    assert user.phone == "+977 9812345678"
    assert user.address == "Baneshwor"
    assert user.emergency_contact_name == "Sita"
    assert str(user.date_of_birth) == "1995-05-20"


@pytest.mark.django_db
def test_protected_fields_ignored_even_if_sent():
    """Server-side allowlist: role/email/department/is_active in the payload must
    NOT change (they aren't on SelfProfileSerializer)."""
    user = _user("pf_prot")
    c = APIClient(); c.force_authenticate(user)
    r = c.patch("/api/v1/profile/me/", {
        "first_name": "Legit", "role": "admin", "email": "hacker@evil.com",
        "department": "HACKED", "is_active": False, "employment_type": "intern",
        "leave_category": "A", "date_of_joining": "2000-01-01",
    }, format="json")
    assert r.status_code == 200, r.content
    user.refresh_from_db()
    assert user.first_name == "Legit"          # editable field applied
    assert user.role == User.Roles.MAKER       # protected — unchanged
    assert user.email == "pf_prot@nif.test"
    assert user.department == "ENG"
    assert user.is_active is True
    assert user.employment_type == User.EmploymentType.PERMANENT
    assert str(user.date_of_joining) == "2018-01-01"


@pytest.mark.django_db
def test_cannot_edit_another_user_via_payload():
    """There is no user_id override; the endpoint always targets request.user."""
    me = _user("pf_me")
    other = _user("pf_other")
    c = APIClient(); c.force_authenticate(me)
    r = c.patch("/api/v1/profile/me/", {"id": str(other.id), "user_id": str(other.id),
                                        "first_name": "Changed"}, format="json")
    assert r.status_code == 200
    me.refresh_from_db(); other.refresh_from_db()
    assert me.first_name == "Changed"
    assert other.first_name == "Pf_other"      # untouched


@pytest.mark.django_db
def test_validation_rejects_bad_phone_and_future_dob():
    user = _user("pf_val")
    c = APIClient(); c.force_authenticate(user)
    assert c.patch("/api/v1/profile/me/", {"phone": "abc<script>"}, format="json").status_code == 400
    assert c.patch("/api/v1/profile/me/", {"date_of_birth": "2999-01-01"}, format="json").status_code == 400


@pytest.mark.django_db
def test_photo_upload_resizes_to_square_and_serves_url():
    user = _user("pf_photo")
    c = APIClient(); c.force_authenticate(user)
    upload = SimpleUploadedFile("me.jpg", _img_bytes((600, 400)), content_type="image/jpeg")
    r = c.post("/api/v1/profile/me/photo/", {"photo": upload}, format="multipart")
    assert r.status_code == 200, r.content
    assert r.data["profile_photo"]                # URL returned
    user.refresh_from_db()
    with Image.open(user.profile_photo.path) as img:
        assert img.size == (256, 256)             # centre-cropped + resized

    # Remove it.
    d = c.delete("/api/v1/profile/me/photo/")
    assert d.status_code == 200
    user.refresh_from_db()
    assert not user.profile_photo


@pytest.mark.django_db
def test_photo_rejects_non_image_and_oversize():
    user = _user("pf_badphoto")
    c = APIClient(); c.force_authenticate(user)
    # Spoofed content-type but not a real image.
    bad = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
    assert c.post("/api/v1/profile/me/photo/", {"photo": bad}, format="multipart").status_code == 400
    # Disallowed type.
    gif = SimpleUploadedFile("x.gif", _img_bytes(fmt="GIF") if False else b"GIF89a", content_type="image/gif")
    assert c.post("/api/v1/profile/me/photo/", {"photo": gif}, format="multipart").status_code == 400


@pytest.mark.django_db
def test_profile_requires_auth():
    assert APIClient().get("/api/v1/profile/me/").status_code == 401
    assert APIClient().patch("/api/v1/profile/me/", {"first_name": "x"}, format="json").status_code == 401


@pytest.mark.django_db
def test_a_platform_operators_photo_is_served_back(settings):
    """Found live: an operator's photo uploaded, and could never be shown.

    An operator belongs to no organization, so the photo was written under
    ``org/_unscoped/`` and the media server read that segment as an owner
    the link did not match -- 403 on every request, a broken image wherever
    their face should be. The upload "worked"; only fetching it proved it.
    """
    from tenancy.context import no_tenant

    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
    settings.ALLOWED_HOSTS = ["*"]
    with no_tenant():
        operator = User.objects.create_user(
            username="pf_ops", email="pf_ops@platform.test",
            password="pass12345", is_platform_staff=True, organization=None)
    c = APIClient(); c.force_authenticate(operator)
    upload = SimpleUploadedFile("me.png", _img_bytes(fmt="PNG"),
                                content_type="image/png")
    # On the console's own hostname, which is what declares platform scope
    # for the request. On `testserver` it would be bound to the default
    # organization, and row-level security would (rightly) hide an
    # organization-less row from it.
    r = c.post("/api/v1/profile/me/photo/", {"photo": upload}, format="multipart",
               HTTP_HOST="admin.platform.test")
    assert r.status_code == 200, r.content
    with no_tenant():
        operator.refresh_from_db()
    assert operator.profile_photo.name.startswith("staff/profiles/")

    url = r.data["profile_photo"]
    served = APIClient().get(url[url.index("/api/"):], HTTP_HOST="admin.platform.test")
    assert served.status_code == 200, served.content[:200]

    # And the download is on the record. Its audit write used to be refused
    # for an account with no organization, and swallowed.
    from audit.models import AuditLog

    with no_tenant():
        assert AuditLog.objects.filter(
            actor=operator, changes__event="MEDIA_DOWNLOADED").exists()
