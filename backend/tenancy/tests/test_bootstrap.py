"""Part 4: a provisioned tenant is not empty.

THE FAILURE THESE TESTS EXIST FOR. At the end of Phase S5, a tenant created
through ``provision_organization`` had no leave types, so a leave request
against it died with ``Http404: No LeaveType matches 'annual'``. The product
was not usable by a customer who had just bought it.

So the assertions here are deliberately about CAPABILITY rather than row
counts: "this tenant has five departments" is easy to satisfy and proves
little, where "this tenant can resolve an attendance policy" is the thing that
was broken.
"""
import pytest

from tenancy import bootstrap, services
from tenancy.context import tenant_context

from .conftest import TODAY

pytestmark = pytest.mark.django_db


@pytest.fixture
def fresh(db, monthly_plan):
    """A tenant provisioned WITHOUT bootstrap -- the pre-Phase-S6 state."""
    return services.provision_organization(
        name="Bare Co", slug="bareco", document_prefix="BARE",
        email="a@bare.test", plan=monthly_plan, today=TODAY, bootstrap=False)


# --- the gap this phase closes -----------------------------------------
def test_a_tenant_provisioned_without_bootstrap_is_unusable(fresh):
    """The Phase S5 state, pinned so the regression is detectable.

    Not a complaint about the old behaviour -- it is the baseline that makes
    the next test mean something.
    """
    gaps = bootstrap.verify_organization(fresh)
    assert gaps, "a bare tenant must report configuration gaps"
    assert "leave_types" in gaps
    assert "departments" in gaps
    assert "attendance_policy" in gaps


def test_a_provisioned_tenant_reports_no_configuration_gaps(org):
    assert bootstrap.verify_organization(org) == {}


def test_provisioning_report_records_what_was_created(org):
    report = org.provisioning_report
    assert report["gaps"] == {}
    created = report["bootstrap"]
    assert created["departments"] == len(bootstrap.DEPARTMENTS)
    assert created["leave_types"] == len(bootstrap.LEAVE_TYPES)
    assert created["minute_types"] == len(bootstrap.MINUTE_TYPES)
    assert created["competencies"] == len(bootstrap.COMPETENCIES)
    assert created["shifts"] == len(bootstrap.SHIFTS)
    # The policy and its global assignment.
    assert created["attendance_policies"] == 2
    assert created["task_templates"] > len(bootstrap.TASK_TEMPLATES)


# --- capability, not counts --------------------------------------------
def test_the_leave_type_the_phase_s5_failure_looked_for_exists(org):
    """`No LeaveType matches 'annual'` -- the exact lookup that failed."""
    from leaves.models import LeaveType

    with tenant_context(org):
        assert LeaveType.objects.filter(code__iexact="annual").exists()
        assert LeaveType.objects.filter(code__iexact="sick").exists()
        assert LeaveType.objects.filter(code__iexact="unpaid").exists()


def test_annual_leave_has_an_entitlement_so_a_balance_is_not_zero(org):
    """The subtler half: leave that can be applied for but never counted.

    Without EntitlementRule rows, every allocation resolves to zero -- the
    module appears to work right up to the point somebody checks a balance.
    """
    from leaves.models import EntitlementRule, LeaveType

    with tenant_context(org):
        annual = LeaveType.objects.get(code="ANNUAL")
        rules = {rule.category: rule
                 for rule in EntitlementRule.objects.filter(leave_type=annual)}
        assert rules, "no annual-leave entitlement at all"

        # Every category the engine can resolve a user into must have a rule,
        # or that user's allocation silently falls to zero.
        from leaves.category_engine import ENTITLEMENT_MATRIX

        assert set(rules) == set(ENTITLEMENT_MATRIX)

        # Non-zero for every category that is entitled to annual leave.
        # PROBATION is deliberately zero -- staff under three months get no
        # annual allocation, which is policy, not a seeding gap.
        for category, rule in rules.items():
            if category == "PROBATION":
                assert rule.entitlement_days == 0
            else:
                assert rule.entitlement_days > 0, category


def test_attendance_policy_resolves_for_a_new_tenants_employee(org):
    """A policy with no global assignment is never resolved for anybody."""
    from attendance.policy.models import AttendancePolicy, PolicyAssignment

    with tenant_context(org):
        policy = AttendancePolicy.objects.filter(is_active=True).first()
        assert policy is not None
        assert policy.default_shift is not None, (
            "a policy with no default shift has no expected start time")
        assignment = PolicyAssignment.objects.filter(
            scope="global", effective_until__isnull=True).first()
        assert assignment is not None
        assert assignment.policy_id == policy.pk
        # The floor has to predate any attendance a tenant could record, or
        # re-deriving an earlier month resolves nothing.
        assert assignment.effective_from <= bootstrap.POLICY_EFFECTIVE_FLOOR


def test_every_optional_attendance_behaviour_is_seeded_off(org):
    """A new tenant's first month must compute by the plainest rules.

    Same reasoning as attendance/0005 for NIF: an operator comparing the
    system against a paper register has to see the same answer, so overtime
    and comp-off are opt-in.
    """
    from attendance.policy.models import AttendancePolicy

    with tenant_context(org):
        policy = AttendancePolicy.objects.get(name=bootstrap.DEFAULT_POLICY_NAME)
        assert policy.comp_off_enabled is False
        assert policy.overtime_threshold_hours is None
        assert policy.deduct_breaks is False
        assert policy.grace_minutes == 0


def test_task_templates_carry_their_checklists(org):
    from tasks.models import TaskTemplate

    with tenant_context(org):
        for template in TaskTemplate.objects.all():
            assert template.groups.exists(), (
                f"template {template.name} has no checklist sections")
            assert template.items.exists(), (
                f"template {template.name} has no checklist items")


def test_settings_defaults_are_written_explicitly_not_left_null(org):
    """An operator cannot change a default they cannot see."""
    with tenant_context(org):
        row = org.settings
        for field, expected in bootstrap.DEFAULT_SETTINGS.items():
            assert getattr(row, field) == expected, field


# --- idempotence -------------------------------------------------------
def test_running_the_bootstrap_twice_creates_nothing_the_second_time(org):
    created = bootstrap.bootstrap_organization(org)
    assert sum(created.values()) == 0, (
        f"a re-run must be a no-op; it created {created}")


def test_a_re_run_does_not_revert_an_administrators_edit(org):
    """The difference between a repair action and a reset action."""
    from leaves.models import LeaveType

    with tenant_context(org):
        annual = LeaveType.objects.get(code="ANNUAL")
        annual.default_days_per_year = 25
        annual.name = "Yearly Holiday"
        annual.save()

    bootstrap.bootstrap_organization(org)

    with tenant_context(org):
        annual.refresh_from_db()
        assert annual.default_days_per_year == 25
        assert annual.name == "Yearly Holiday"


def test_a_re_run_does_not_overwrite_a_changed_setting(org):
    with tenant_context(org):
        row = org.settings
        row.escalation_hr_days = 30
        row.save()

    bootstrap.bootstrap_organization(org)

    row.refresh_from_db()
    assert row.escalation_hr_days == 30


def test_repair_fills_a_gap_on_a_tenant_provisioned_before_this_phase(fresh):
    """The remedy for every tenant that exists already."""
    assert bootstrap.verify_organization(fresh)
    created = bootstrap.bootstrap_organization(fresh)
    assert created["leave_types"] == len(bootstrap.LEAVE_TYPES)
    assert bootstrap.verify_organization(fresh) == {}


def test_repair_restores_only_what_was_deleted(org):
    from leaves.models import Department

    with tenant_context(org):
        Department.objects.filter(code="FIN").delete()
        assert Department.objects.count() == len(bootstrap.DEPARTMENTS) - 1

    created = bootstrap.bootstrap_organization(org)
    assert created["departments"] == 1
    assert sum(created.values()) == 1


# --- isolation ---------------------------------------------------------
def test_bootstrap_rows_belong_to_the_tenant_they_were_created_for(org, nif):
    """Every row stamped, and not one of them visible to the other tenant.

    Asked inside each tenant's own context. A cross-tenant query would read
    nothing under row-level security -- and "nothing" is indistinguishable
    from "correctly isolated", which is exactly the confusion to avoid.
    """
    from appraisal.models import Competency
    from leaves.models import Department, LeaveType
    from minutes.models import MinuteType
    from tasks.models import TaskTemplate

    for model in (Department, LeaveType, MinuteType, TaskTemplate, Competency):
        rows = {}
        for organization in (org, nif):
            with tenant_context(organization):
                rows[organization.slug] = set(
                    model.objects.values_list("pk", flat=True))
                assert all(
                    value == organization.pk for value in
                    model.objects.values_list("organization_id", flat=True)), (
                    f"{model.__name__} row stamped with the wrong tenant")
        assert rows[org.slug], f"{model.__name__} was not seeded for {org.slug}"
        assert not rows[org.slug] & rows[nif.slug], (
            f"{model.__name__} rows shared between the two tenants")


def test_two_tenants_may_both_hold_the_same_leave_type_code(org, nif):
    """The per-tenant uniqueness Phase S2 introduced, exercised for real."""
    from leaves.models import LeaveType

    found = {}
    for organization in (nif, org):
        with tenant_context(organization):
            row = LeaveType.objects.filter(code="ANNUAL").first()
            assert row is not None, f"{organization.slug} has no ANNUAL type"
            found[organization.slug] = row.pk
    assert found[nif.slug] != found[org.slug], (
        "both tenants are pointing at the same LeaveType row")


# --- the two copies of the catalogues agree ----------------------------
def test_the_leave_type_catalogue_matches_the_migration_it_duplicates():
    """Two copies of a policy table, compared automatically.

    ``bootstrap.LEAVE_TYPES`` necessarily restates rows that
    ``leaves/0005`` wrote for NIF -- a data migration runs once per database
    and bootstrap runs once per tenant, so neither can be derived from the
    other. The honest way to hold two copies is to name them and diff them,
    which is this test.
    """
    import importlib

    # A module whose name starts with a digit cannot be imported with the
    # `import` statement, which is why every comparison below goes through
    # importlib rather than a plain import.
    migration = importlib.import_module(
        "leaves.migrations.0005_seed_leave_types_and_holidays")

    # SPECIAL is deliberately NOT in the NIF seed, which is what this
    # carve-out records. The brief names four platform defaults (Annual,
    # Sick, Unpaid, Special); NIF's own five predate the platform and do not
    # include Special, and backfilling it into a migration that has already
    # run everywhere is not available. So it is a platform default with no
    # NIF counterpart, and the test says so rather than being loosened.
    PLATFORM_ONLY = {"SPECIAL"}

    by_code = {row[0]: row for row in migration.LEAVE_TYPES}
    for row in bootstrap.LEAVE_TYPES:
        code = row[0]
        if code in PLATFORM_ONLY:
            assert code not in by_code, (
                f"'{code}' is now in the NIF seed as well, so it is no longer "
                f"platform-only -- compare the rows instead of excusing them")
            continue
        assert code in by_code, (
            f"bootstrap offers a leave type '{code}' that the NIF seed does "
            f"not describe; if that is deliberate, say so here")
        assert row == by_code[code], (
            f"leave type '{code}' differs between bootstrap and "
            f"leaves/0005:\n  bootstrap {row}\n  migration {by_code[code]}")


def test_the_competency_catalogue_matches_the_migration_it_duplicates():
    import importlib

    migration = importlib.import_module(
        "appraisal.migrations.0002_seed_competencies")
    assert bootstrap.COMPETENCIES == migration.COMPETENCIES


def test_the_minute_type_catalogue_matches_the_migration_it_duplicates():
    import importlib

    migration = importlib.import_module(
        "minutes.migrations.0009_seed_manual_minute_types")
    assert bootstrap.MINUTE_TYPES == migration.MANUAL_TYPES


def test_the_inventory_category_catalogue_matches_the_migration_it_duplicates():
    """The same two-copies problem, for the categories added this phase.

    ``inventory/0006`` seeded these ten for NIF before tenancy existed, so a
    tenant provisioned afterwards had none at all -- and an asset cannot be
    registered without a category. Bootstrap now writes the same ten, and
    this is what keeps the two lists from drifting apart.
    """
    import importlib

    migration = importlib.import_module(
        "inventory.migrations.0006_seed_default_categories")
    assert bootstrap.INVENTORY_CATEGORIES == migration.CATEGORIES


def test_the_attendance_defaults_and_the_settings_defaults_agree():
    """Two places name an office start time. They must not disagree.

    The policy drives attendance derivation and the settings row drives the
    tenant-facing policy screen, so a mismatch is a tenant whose displayed
    policy is not the one being applied.
    """
    assert (bootstrap.DEFAULT_POLICY["office_start_time"]
            == bootstrap.DEFAULT_SETTINGS["office_start"])
    assert (bootstrap.DEFAULT_POLICY["absent_cutoff_time"]
            == bootstrap.DEFAULT_SETTINGS["absent_cutoff"])
    assert (bootstrap.DEFAULT_POLICY["full_day_hours"]
            == bootstrap.DEFAULT_SETTINGS["full_day_hours"])
    assert (bootstrap.DEFAULT_POLICY["half_day_hours"]
            == bootstrap.DEFAULT_SETTINGS["half_day_hours"])


# --- the two sections this brief added ----------------------------------
def test_special_leave_is_offered_and_allocates_nothing(org):
    """Part 3 names four leave types. The fourth needed a decision.

    Special leave is granted by arrangement -- bereavement, marriage, study --
    so there is no yearly allocation to guess at, and a non-zero default here
    would hand every employee of every new customer an entitlement nobody
    agreed to. It is therefore seeded offerable and unallocated, which is
    exactly how UNPAID is treated.
    """
    from leaves.models import EntitlementRule, LeaveType

    with tenant_context(org):
        special = LeaveType.objects.get(code="SPECIAL")
        assert special.is_active, "a seeded type nobody can pick is not seeded"
        assert special.default_days_per_year == 0
        assert not EntitlementRule.objects.filter(
            leave_type=special).exists(), (
            "SPECIAL must allocate nothing until somebody decides what it is "
            "worth")


def test_a_new_tenant_can_categorise_an_asset_on_day_one(org):
    """The gap: `inventory/0006` ran once, for NIF, before tenancy.

    So every tenant provisioned afterwards opened Inventory with an empty
    category list. `InventoryItem.category` is nullable, so this did not stop
    an asset being registered -- it meant every asset a new customer
    registered was uncategorised, with nothing to filter or report by, and no
    `default_useful_life_months` for depreciation to inherit. The store
    officer's alternative was to invent a taxonomy before entering their first
    laptop.
    """
    from inventory.models import InventoryCategory, InventoryItem

    with tenant_context(org):
        assert InventoryCategory.objects.count() == len(
            bootstrap.INVENTORY_CATEGORIES)
        item = InventoryItem.objects.create(
            name="Dell Latitude 5440",
            category=InventoryCategory.objects.get(name="Laptop"))
        assert item.category.name == "Laptop"


def test_a_missing_category_is_reported_as_a_gap_and_repaired(org):
    """Health has to cover the new section, or a gap is invisible again."""
    from inventory.models import InventoryCategory

    with tenant_context(org):
        InventoryCategory.objects.filter(name="Laptop").delete()

    gaps = bootstrap.verify_organization(org)
    assert gaps.get("inventory_categories") == ["Laptop"]

    bootstrap.bootstrap_organization(org, audit=False)
    assert bootstrap.verify_organization(org) == {}


# --- Part 5: the verdict ------------------------------------------------
def test_the_health_check_answers_in_the_two_words_the_brief_asks_for(
        org, platform_user):
    """"Tenant Ready" or "Provisioning Incomplete", plus the reason.

    A verdict with no detail is not actionable, so the gaps are still
    reported; a page full of gaps with no verdict makes an operator work out
    the answer themselves. Both, therefore.
    """
    from tenancy import console

    health = console.tenant_health(platform_user, org)
    assert health["verdict"] == "Tenant Ready"
    assert health["ready"] is True

    with tenant_context(org):
        from leaves.models import Department
        Department.objects.filter(code="HR").delete()

    health = console.tenant_health(platform_user, org)
    assert health["verdict"] == "Provisioning Incomplete"
    assert health["ready"] is False
    assert health["configuration_gaps"]["departments"] == ["HR"]


def test_a_complete_but_locked_out_tenant_is_not_reported_ready(
        org, platform_user):
    """Configuration is only half the question a customer experiences.

    A perfectly bootstrapped workspace nobody can sign in to is not ready,
    and reporting it ready would be answering the half the customer cannot
    see.
    """
    from tenancy import console

    console.suspend(platform_user, org, reason="non-payment")
    org.refresh_from_db()

    health = console.tenant_health(platform_user, org)
    assert health["configuration_gaps"] == {}
    assert health["verdict"] == "Provisioning Incomplete"
    assert health["is_admitted"] is False
