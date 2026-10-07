"""Part 3: the tenant / platform user split.

"Company admins must NEVER access platform administration" has to be a property
of the data, not a flag somebody remembers to check. These tests pin the three
layers that make it one: the database constraint, the model's save(), and the
serializer deny-list.
"""
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from tenancy.permissions import IsPlatformStaff, IsTenantUser

pytestmark = pytest.mark.django_db


# --- the shape of the two kinds of user --------------------------------
def test_a_platform_user_has_no_organization(platform_user):
    assert platform_user.is_platform_staff is True
    assert platform_user.organization_id is None
    assert platform_user.is_platform_user is True
    assert platform_user.is_tenant_user is False


def test_a_tenant_user_belongs_to_exactly_one_organization(tenant_admin, nif):
    assert tenant_admin.is_platform_staff is False
    assert tenant_admin.organization_id == nif.pk
    assert tenant_admin.is_tenant_user is True
    assert tenant_admin.is_platform_user is False


# --- enforcement, layer by layer ---------------------------------------
def test_the_model_refuses_a_platform_user_with_an_organization(
        django_user_model, nif):
    user = django_user_model(username="hybrid", email="h@x.test",
                              is_platform_staff=True, organization=nif)
    with pytest.raises(ValidationError):
        user.save()


def test_promoting_a_tenant_user_to_platform_staff_is_refused(tenant_admin):
    """The escalation that would hand a customer the whole platform."""
    tenant_admin.is_platform_staff = True
    with pytest.raises(ValidationError):
        tenant_admin.save()


def test_the_database_refuses_it_too(django_user_model, nif):
    """Bypassing save() with a bulk write must still fail."""
    user = django_user_model.objects.create_user(
        username="sneaky", email="s@x.test", password="x-Sneaky-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        django_user_model.objects.filter(pk=user.pk).update(
            is_platform_staff=True, organization=nif)


def test_the_constraint_is_named_and_present(django_user_model):
    names = {c.name for c in django_user_model._meta.constraints}
    assert "user_platform_staff_has_no_org" in names


# --- the serializer deny-list ------------------------------------------
#
# Phase S1 relies on a real property of this codebase: every serializer uses an
# explicit field allow-list and NONE uses fields = "__all__". These two tests
# keep that true, because the day one of them changes is the day a tenant admin
# can promote themselves.
PLATFORM_ONLY_USER_FIELDS = frozenset({
    "organization", "is_platform_staff", "is_staff", "is_superuser"})


def test_no_user_serializer_lets_a_platform_only_field_BE_WRITTEN():
    """The escalation is WRITING one of these, not reading one.

    TIGHTENED IN PHASE S6, after this test failed for the right reason on a
    change that was safe. The console needs the frontend to know whether the
    signed-in account is a platform operator -- a platform account has no
    tenant role, so routing them by `role` lands them in a workspace that is
    not theirs -- so `is_platform_staff` is now on the current-user payload,
    read-only.

    The old test checked whether the NAME appeared in `Meta.fields`, which
    could not tell those two cases apart. Worse, it could be satisfied by
    renaming the field to a SerializerMethodField and exposing exactly the same
    value, so it was a test somebody could pass without fixing anything.

    This instantiates each serializer and asks DRF what is writable, which is
    the question that matters: the day a tenant admin can SET
    `is_platform_staff` is the day they can promote themselves.
    """
    import inspect

    from rest_framework import serializers as drf

    from users import serializers as user_serializers

    offenders = []
    for name, cls in vars(user_serializers).items():
        if not (inspect.isclass(cls) and issubclass(cls, drf.Serializer)):
            continue
        if cls.__module__ != user_serializers.__name__:
            continue
        try:
            fields = cls().fields
        except Exception as exc:  # noqa: BLE001 - needs context; skip loudly
            offenders.append(f"{name}: could not be instantiated ({exc})")
            continue
        writable = {key for key, field in fields.items() if not field.read_only}
        leaked = PLATFORM_ONLY_USER_FIELDS.intersection(writable)
        if leaked:
            offenders.append(f"{name}: {sorted(leaked)} are WRITABLE")
    assert not offenders, (
        f"serializers let platform-only User fields be written: {offenders}")


def test_the_current_user_payload_exposes_the_platform_flag_read_only():
    """The console depends on it being there, and on it being unwritable."""
    from users.serializers import UserSerializer

    fields = UserSerializer().fields
    assert "is_platform_staff" in fields
    assert fields["is_platform_staff"].read_only is True
    assert fields["organization_slug"].read_only is True
    # And the raw `organization` FK is still absent entirely: a slug is enough
    # to brand a page, where the id invites somebody to post it back.
    assert "organization" not in fields


def test_no_serializer_in_the_project_uses_fields_all():
    """`fields = "__all__"` would auto-expose organization and is_platform_staff."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("serializers.py")
        if "__pycache__" not in str(p)
        and '"__all__"' in p.read_text(errors="ignore").replace("'", '"')
    ]
    assert not offenders, f"fields = '__all__' found in: {offenders}"


def test_the_tenant_admin_endpoint_cannot_set_platform_fields(tenant_admin, nif):
    """End to end: POST the forbidden fields at the tenant admin API."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(tenant_admin)
    response = client.post("/api/v1/users/admin/users/", {
        "email": "newhire@nif.test",
        "first_name": "New", "last_name": "Hire",
        "role": "maker",
        # The escalation attempt.
        "is_platform_staff": True,
        "is_superuser": True,
        "is_staff": True,
    }, format="json")

    assert response.status_code in (200, 201), response.data
    created = type(tenant_admin).objects.get(email="newhire@nif.test")
    assert created.is_platform_staff is False
    assert created.is_superuser is False
    assert created.is_staff is False


# --- the permission classes --------------------------------------------
class _Req:
    def __init__(self, user):
        self.user = user


def test_is_platform_staff_admits_only_platform_users(platform_user, tenant_admin):
    permission = IsPlatformStaff()
    assert permission.has_permission(_Req(platform_user), None) is True
    assert permission.has_permission(_Req(tenant_admin), None) is False


def test_is_platform_staff_does_not_accept_django_superusers(django_user_model):
    """Every admin guard in this project is an allow-list; this one too.

    A Django superuser created for an unrelated reason must not inherit the
    platform console.
    """
    root = django_user_model.objects.create_superuser(
        username="root", email="root@x.test", password="x-Root-1")
    assert IsPlatformStaff().has_permission(_Req(root), None) is False


def test_is_tenant_user_rejects_platform_staff(platform_user, tenant_admin):
    permission = IsTenantUser()
    assert permission.has_permission(_Req(tenant_admin), None) is True
    assert permission.has_permission(_Req(platform_user), None) is False


def test_anonymous_is_refused_by_both():
    from django.contrib.auth.models import AnonymousUser

    anon = _Req(AnonymousUser())
    assert IsPlatformStaff().has_permission(anon, None) is False
    assert IsTenantUser().has_permission(anon, None) is False


# --- no user rows were migrated in this phase --------------------------
def test_phase_s1_attaches_no_existing_user_to_an_organization(django_user_model):
    """Part 3: "Do not migrate all users yet."

    Nothing in the S1 migrations may set User.organization. The fixtures in this
    test module set it explicitly; the seeded database must not.
    """
    from django.db.migrations.executor import MigrationExecutor
    from django.db import connection

    loader = MigrationExecutor(connection).loader
    assert ("users", "0012_user_is_platform_staff_user_organization_and_more") \
        in loader.graph.nodes
    # The migration adds columns only -- no RunPython that touches user rows.
    migration = loader.get_migration(
        "users", "0012_user_is_platform_staff_user_organization_and_more")
    from django.db import migrations as m
    assert not any(isinstance(op, (m.RunPython, m.RunSQL))
                   for op in migration.operations)
