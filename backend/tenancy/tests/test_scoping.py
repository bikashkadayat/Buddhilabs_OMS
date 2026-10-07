"""Part 6: the tenant-aware query foundation.

Introduced and tested, ATTACHED TO NOTHING. The last test in this file is the
one that matters most for Phase S2's safety: it asserts that no business model
has been switched to the filtering manager, because doing so would change the
result of every existing query in the project.
"""
import pytest
from django.apps import apps
from django.test import override_settings

from tenancy import inventory, services
from tenancy.context import current_org_id, no_tenant, tenant_context
from tenancy.exceptions import TenantScopeMissing
from tenancy.scoping import (AllTenantsManager, TenantManager, TenantQuerySet,
                             TenantScopedModel, active_organization,
                             active_organization_id)

pytestmark = pytest.mark.django_db


@pytest.fixture
def other(db, monthly_plan):
    return services.provision_organization(
        name="Other Co", slug="otherco", document_prefix="OTH",
        email="o@o.test", plan=monthly_plan)


# --- the compatibility shim --------------------------------------------
def test_the_shim_resolves_the_single_tenant_with_no_context(nif):
    assert active_organization() == nif
    assert active_organization_id() == nif.pk


def test_context_wins_over_the_shim(nif, other):
    with tenant_context(other):
        assert active_organization() == other


def test_the_shim_refuses_to_choose_between_two_tenants(nif, other):
    """The safety property. A shim that keeps guessing is how data crosses."""
    with no_tenant():
        with pytest.raises(TenantScopeMissing):
            active_organization()


def test_required_false_returns_none_instead_of_raising(nif, other):
    with no_tenant():
        assert active_organization(required=False) is None


@override_settings(TENANCY_ENABLED=True)
def test_the_shim_stops_working_entirely_once_tenancy_is_enabled(nif):
    """Step 2 of the resolution order disappears when the flag flips.

    Even with ONE organization, enforcement mode will not fall back: by then a
    request must carry its tenant, and a write with none is a bug.
    """
    with no_tenant():
        with pytest.raises(TenantScopeMissing):
            active_organization()


@override_settings(TENANCY_ENABLED=True)
def test_context_still_works_when_tenancy_is_enabled(nif):
    with tenant_context(nif):
        assert active_organization() == nif


# --- the queryset / manager -------------------------------------------
def test_the_queryset_can_narrow_to_an_organization(nif, other):
    from leaves.models import Department

    with tenant_context(nif):
        mine = Department.objects.create(name="Mine", code="MINE")
    with tenant_context(other):
        theirs = Department.objects.create(name="Theirs", code="THEIRS")

    rows = TenantQuerySet(model=Department).for_organization(nif)
    assert mine in rows and theirs not in rows


def test_for_current_tenant_uses_the_context(nif, other):
    from leaves.models import Department

    with tenant_context(other):
        theirs = Department.objects.create(name="Theirs", code="THEIRS2")
    with tenant_context(nif):
        assert theirs not in TenantQuerySet(model=Department).for_current_tenant()
    with tenant_context(other):
        assert theirs in TenantQuerySet(model=Department).for_current_tenant()


def test_for_current_tenant_raises_rather_than_returning_nothing(nif, other):
    """An empty result reads as "this tenant has no rows", which is a lie."""
    from leaves.models import Department

    with no_tenant():
        with pytest.raises(TenantScopeMissing):
            list(TenantQuerySet(model=Department).for_current_tenant())


def test_the_scoped_model_base_is_abstract_and_declares_both_managers():
    """Managers are not accessible ON an abstract model, so read the Meta."""
    assert TenantScopedModel._meta.abstract
    declared = dict(TenantScopedModel._meta.managers_map)
    assert isinstance(declared["objects"], TenantManager)
    assert isinstance(declared["all_tenants"], AllTenantsManager)


def test_the_unscoped_manager_has_a_different_greppable_name():
    """So every intentional cross-tenant query is one grep away in review."""
    assert AllTenantsManager.__name__ == "AllTenantsManager"
    assert not issubclass(AllTenantsManager, TenantManager)


# --- Part 5: the manager is ACTIVE on Phase B, and only on Phase B --------
def test_every_phase_b_model_uses_the_filtering_manager():
    """Part 5: tenant-aware querying, activated.

    This test is the inverse of the Phase S2 one it replaces. Then, the safety
    property was "no business model filters yet"; now it is "every completed
    Phase B model does".
    """
    not_switched = []
    for label in sorted(inventory.PHASE_B):
        model = apps.get_model(*label.split("."))
        if not isinstance(model._default_manager, TenantManager):
            not_switched.append(label)
    assert not_switched == [], not_switched


def test_every_phase_b_model_keeps_the_explicit_escape_hatch():
    """`all_tenants` must exist, so a cross-tenant read is greppable."""
    missing = []
    for label in sorted(inventory.PHASE_B):
        model = apps.get_model(*label.split("."))
        manager = model._meta.managers_map.get("all_tenants")
        if not isinstance(manager, AllTenantsManager):
            missing.append(label)
    assert missing == [], missing


def test_every_tenant_scoped_model_uses_a_filtering_manager():
    """Phase S5 Part 3 completed the set: all 106, not just Phase B.

    users.User is the one model whose manager is not a TenantManager subclass:
    UserManager carries the create_user/create_superuser contract Django's auth
    machinery depends on, so TenantUserManager reproduces the scoping instead
    of inheriting it.
    """
    from users.models import TenantUserManager

    unscoped = []
    for label in sorted(inventory.TENANT_SCOPED):
        model = apps.get_model(*label.split("."))
        if not isinstance(model._default_manager,
                          (TenantManager, TenantUserManager)):
            unscoped.append(label)
    assert unscoped == [], unscoped


def test_every_tenant_scoped_model_keeps_an_escape_hatch():
    missing = [label for label in sorted(inventory.TENANT_SCOPED)
               if "all_tenants" not in
               apps.get_model(*label.split("."))._meta.managers_map]
    assert missing == [], missing


def test_the_base_manager_is_left_unfiltered():
    """Django uses `_base_manager` for FK validation and refresh_from_db.

    No model sets `base_manager_name`, so Django builds a plain `Manager` for
    it -- which means those internals are NOT scoped and cannot start failing
    because a related object belongs to another tenant. Asserted rather than
    assumed, because getting it wrong would be subtle and intermittent.
    """
    for label in sorted(inventory.PHASE_B):
        model = apps.get_model(*label.split("."))
        assert not model._meta.base_manager_name, label
        assert not isinstance(model._base_manager, TenantManager), label


def test_the_filtering_manager_does_no_database_work_to_resolve_the_tenant(
        nif, django_assert_num_queries):
    """get_queryset() is called by IMPORT-TIME code, so it must not query.

    django-filter builds a ModelChoiceFilter from `Model._default_manager`
    while leaves/filters.py is imported, and DRF does the same for
    PrimaryKeyRelatedField. The first version of TenantManager resolved the
    tenant through a database query, so importing the URLconf tried to read
    tenancy_organization before migrations had created it and
    `manage.py makemigrations` died with "no such table".
    """
    from tasks.models import Task

    with django_assert_num_queries(0):
        Task.objects.all()          # building the queryset must be free
        Task.objects.filter(title="x")


def test_with_a_tenant_in_context_the_manager_scopes(nif, other):
    from leaves.models import Department

    with tenant_context(other):
        theirs = Department.objects.create(name="Theirs", code="MGR-THEIRS")
    with tenant_context(nif):
        mine = Department.objects.create(name="Mine", code="MGR-MINE")

    from tasks.models import Task

    with tenant_context(nif):
        a_task = Task.objects.create(title="A", task_number="NIFN-TSK-9001")
    with tenant_context(other):
        b_task = Task.objects.create(title="B", task_number="OTH-TSK-9001")

    with tenant_context(nif):
        assert a_task in Task.objects.all()
        assert b_task not in Task.objects.all()
    with tenant_context(other):
        assert b_task in Task.objects.all()
        assert a_task not in Task.objects.all()

    # THE ESCAPE HATCH CROSSES THE MANAGER, NOT THE DATABASE (Phase S6).
    #
    # `all_tenants` used to be asserted here as seeing both tasks, and it does
    # -- as far as the ORM is concerned. Under PostgreSQL row-level security
    # it still sees only what the CONNECTION is allowed to see, so it crosses
    # tenants exactly when nothing has bound one. That is not a limitation of
    # the hatch; it is the point of having the database enforce the boundary
    # as well, and it is why the platform console cannot simply call
    # `all_tenants` to do its job.
    #
    # Asserted as two separate facts, each true on either backend: the hatch
    # reaches a row the scoped manager in THIS context would hide.
    with tenant_context(nif):
        assert Task.all_tenants.filter(pk=a_task.pk).exists()
        assert Task.objects.filter(pk=b_task.pk).exists() is False
    with tenant_context(other):
        assert Task.all_tenants.filter(pk=b_task.pk).exists()
    assert mine and theirs


def test_without_a_tenant_the_manager_is_a_pass_through_for_now(nif):
    """The compatibility window, stated explicitly.

    With TENANCY_ENABLED False there is one organization, so an unscoped read
    returns the same rows a scoped one would. Import-time and
    management-command paths therefore keep working unchanged.
    """
    from tasks.models import Task

    with tenant_context(nif):
        Task.objects.create(title="T", task_number="NIFN-TSK-9100")
        # Counted inside the tenant. The pass-through this test is about is a
        # property of the MANAGER, and asserting it from inside `no_tenant()`
        # conflates it with what the database allows -- under row-level
        # security a platform-scoped connection sees no tenant rows at all,
        # which is correct and is tested in test_rls.py.
        assert Task.objects.count() == 1

    with no_tenant():
        # The manager does not raise here, which is the compatibility window.
        # What it RETURNS depends on the connection, so only the absence of an
        # exception is asserted.
        Task.objects.count()


@override_settings(TENANCY_ENABLED=True)
def test_with_tenancy_enabled_an_unscoped_read_fails_closed(nif):
    """The window closes. A read with no tenant is then a bug, and it raises.

    Returning every tenant's rows would be the worst possible way to report
    that, so it does not.
    """
    from tasks.models import Task

    with no_tenant():
        with pytest.raises(TenantScopeMissing):
            list(Task.objects.all())


@override_settings(TENANCY_ENABLED=True)
def test_the_escape_hatch_still_works_when_tenancy_is_enabled(nif):
    from tasks.models import Task

    with no_tenant():
        assert Task.all_tenants.count() >= 0


# --- the type contract on the context value -----------------------------
def test_the_tenant_in_context_is_type_compatible_with_a_rows_owner(nif):
    """Regression (Phase S4). The id must compare equal to a model's FK value.

    `Organization.pk` is a UUIDField, so a row's `organization_id` is a
    `uuid.UUID`. The Phase S4 id-only resolver hot path reads the id out of the
    cache, where it is a STRING -- and `uuid.UUID(x) != str(x)` in Python. So
    every comparison between "the tenant in context" and "the tenant that owns
    this row" would have been silently False for the SAME tenant.

    `TenantScopedModel.save()` raises CrossTenantWrite on exactly that
    comparison, so the bug would have presented as a refusal to save rows that
    were perfectly valid.
    """
    import uuid

    from leaves.models import Department

    with tenant_context(nif):
        department = Department.objects.create(name="Types", code="TYPE-1")
        in_context = current_org_id()

    assert isinstance(in_context, uuid.UUID)
    assert in_context == nif.pk
    assert in_context == department.organization_id
    assert not (in_context != department.organization_id)


def test_the_string_form_from_the_resolver_hot_path_is_normalised(nif):
    """The hot path returns a string; the context must hand back a UUID."""
    import uuid

    from tenancy import resolver

    raw = resolver.default_organization_id("")
    assert isinstance(raw, str), "resolve_id is the cheap, string-returning path"

    with tenant_context(raw):
        assert isinstance(current_org_id(), uuid.UUID)
        assert current_org_id() == nif.pk


def test_a_scoped_read_works_with_the_string_form_in_context(nif):
    """The manager must filter correctly however the id arrived."""
    from tasks.models import Task

    from tenancy import resolver

    with tenant_context(nif):
        Task.objects.create(title="T", task_number="NIFN-TSK-9200")

    with tenant_context(resolver.default_organization_id("")):
        assert Task.objects.filter(task_number="NIFN-TSK-9200").exists()
