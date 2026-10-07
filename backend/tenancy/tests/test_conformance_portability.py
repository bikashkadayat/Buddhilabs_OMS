"""Phase S6.5 Part 9: three tenants, real data, export → archive → restore.

    ABC School · XYZ Hospital · Demo NGO

WHAT THIS TEST IS FOR THAT THE UNIT TESTS ARE NOT. ``test_export.py`` and
``test_archive.py`` each prove one mechanism against one tenant. This proves
the SEQUENCE against three tenants holding the same shapes of data at the same
time -- which is the only arrangement in which "nothing lost" and "nothing
cross-tenant" can both be checked, because with one tenant there is nothing to
leak from and with two a wrong answer has the right cardinality.

Each tenant is given tasks, leaves, minutes, inventory and documents (Part 9's
list), then the full cycle is run and the census compared row by row.
"""
import datetime

import pytest

from tenancy import console, services
from tenancy.context import tenant_context
from tenancy.inventory import TENANT_SCOPED

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)

TENANTS = [
    dict(name="ABC School", slug="abc-school", document_prefix="ABCS",
         email="admin@abc-school.edu.np"),
    dict(name="XYZ Hospital", slug="xyz-hospital", document_prefix="XYZH",
         email="admin@xyz-hospital.org.np"),
    dict(name="Demo NGO", slug="demo-ngo", document_prefix="DNGO",
         email="admin@demo-ngo.org.np"),
]


@pytest.fixture
def three(db, monthly_plan):
    return [
        services.provision_organization(plan=monthly_plan, today=TODAY, **spec)
        for spec in TENANTS
    ]


def _populate(organization):
    """Part 9's five record types, created as the tenant.

    Returns the ids and numbers that a restore has to preserve exactly.
    """
    from django.contrib.auth import get_user_model

    from inventory.models import InventoryCategory, InventoryItem
    from inventory.services import generate_asset_code
    from leaves.models import Department, Leave
    from memos.models import Memo
    from memos.services import generate_memo_number
    from minutes.models import Minute, MinuteType
    from minutes.services import generate_minute_number
    from tasks.models import Task
    from tasks.services import generate_task_number

    User = get_user_model()
    with tenant_context(organization):
        department = Department.objects.get(code="ICT")
        staff = User(username=f"staff-{organization.slug}",
                     email=f"staff@{organization.slug}.test", role="admin",
                     employment_type=User.EmploymentType.PERMANENT,
                     department_ref=department, department=department.name,
                     date_of_joining=datetime.date(2020, 1, 1),
                     organization=organization)
        staff.set_password("x-Portable-1")
        staff.save()

        leave = Leave.objects.create(
            user=staff, leave_type="annual", reason="Family event",
            start_date=datetime.date(2026, 7, 6),
            end_date=datetime.date(2026, 7, 7))
        task = Task.objects.create(
            title=f"Task for {organization.name}", created_by=staff,
            task_number=generate_task_number(),
            due_date=datetime.date(2026, 8, 1))
        minute = Minute.objects.create(
            minute_type=MinuteType.objects.get(code="department"),
            minute_number=generate_minute_number(),
            meeting_date=datetime.date(2026, 7, 6),
            meeting_time=datetime.time(14, 0), created_by=staff,
            subject="Monthly review")
        item = InventoryItem.objects.create(
            name="Dell Latitude 5440", asset_code=generate_asset_code(),
            category=InventoryCategory.objects.get(name="Laptop"))
        memo = Memo.objects.create(
            subject="Office notice", created_by=staff,
            memo_number=generate_memo_number("administrative"))

    return {
        "ids": {"user": str(staff.pk), "leave": str(leave.pk),
                "task": str(task.pk), "minute": str(minute.pk),
                "item": str(item.pk), "memo": str(memo.pk)},
        "numbers": {"task": task.task_number, "minute": minute.minute_number,
                    "asset": item.asset_code, "memo": memo.memo_number},
    }


def _census(organization):
    from django.apps import apps

    counts = {}
    with tenant_context(organization):
        for label in sorted(TENANT_SCOPED):
            app_label, model_name = label.split(".")
            counts[label] = apps.get_model(app_label, model_name).objects.count()
    return counts


@pytest.fixture
def populated(three):
    """All three tenants, each holding the same shapes of data."""
    return [(organization, _populate(organization)) for organization in three]


# --- the sequence -------------------------------------------------------
def test_export_archive_restore_loses_nothing_for_any_of_them(populated,
                                                               platform_user):
    """The whole cycle, with the census taken before and after."""
    before = {organization.slug: _census(organization)
              for organization, _ in populated}

    for organization, _ in populated:
        export_row = console.create_export(platform_user, organization)
        assert export_row.status == "ready", export_row.error

        console.archive_organization(platform_user, organization,
                                     reason="Customer closed for the season")
        organization.refresh_from_db()
        assert organization.is_admitted is False

        console.restore_organization(platform_user, organization)
        organization.refresh_from_db()
        assert organization.is_admitted is True

    for organization, _ in populated:
        assert _census(organization) == before[organization.slug], (
            f"{organization.slug} lost or gained rows across the cycle")


def test_every_id_and_number_survives_the_cycle(populated, platform_user):
    """Part 3's list, checked item by item: ids, references, numbering."""
    from inventory.models import InventoryItem
    from memos.models import Memo
    from minutes.models import Minute
    from tasks.models import Task

    for organization, recorded in populated:
        console.create_export(platform_user, organization)
        console.archive_organization(platform_user, organization,
                                     reason="Closed")
        organization.refresh_from_db()
        console.restore_organization(platform_user, organization)
        organization.refresh_from_db()

        with tenant_context(organization):
            task = Task.objects.get(pk=recorded["ids"]["task"])
            minute = Minute.objects.get(pk=recorded["ids"]["minute"])
            item = InventoryItem.objects.get(pk=recorded["ids"]["item"])
            memo = Memo.objects.get(pk=recorded["ids"]["memo"])

            assert task.task_number == recorded["numbers"]["task"]
            assert minute.minute_number == recorded["numbers"]["minute"]
            assert item.asset_code == recorded["numbers"]["asset"]
            assert memo.memo_number == recorded["numbers"]["memo"]

            # Relationships, not just rows.
            assert str(task.created_by_id) == recorded["ids"]["user"]
            assert item.category.name == "Laptop"
            assert minute.minute_type.code == "department"


def test_each_bundle_holds_exactly_one_customers_records(populated,
                                                          platform_user):
    """"Nothing cross-tenant", asserted with three tenants in the database.

    Every other tenant's record ids are searched for in the whole bundle --
    not just in the table they belong to -- because a scope leak would not
    announce which file it landed in.
    """
    import io
    import json
    import zipfile

    bundles = {}
    for organization, _ in populated:
        export_row = console.create_export(platform_user, organization)
        export_row.file.seek(0)
        bundles[organization.slug] = export_row.file.read()

    for organization, recorded in populated:
        others = [other for other, _ in populated if other.pk != organization.pk]
        with zipfile.ZipFile(io.BytesIO(bundles[organization.slug])) as archive:
            text = b"".join(archive.read(name) for name in archive.namelist()
                            if name.endswith(".json")).decode()
            manifest = json.loads(archive.read("manifest.json"))

        assert manifest["organization"]["slug"] == organization.slug
        for other in others:
            assert str(other.pk) not in text, (
                f"{organization.slug}'s bundle mentions {other.slug}")
        for other, other_recorded in populated:
            if other.pk == organization.pk:
                continue
            for name, value in other_recorded["ids"].items():
                assert value not in text, (
                    f"{organization.slug}'s bundle contains {other.slug}'s "
                    f"{name}")


def test_every_bundle_is_complete_and_verifies(populated, platform_user):
    """Part 6, for all three: every table, every checksum, no dangling ids."""
    import hashlib
    import io
    import json
    import zipfile

    for organization, _ in populated:
        export_row = console.create_export(platform_user, organization)
        assert export_row.status == "ready", export_row.error
        assert export_row.row_count > 0

        export_row.file.seek(0)
        payload = export_row.file.read()
        assert hashlib.sha256(payload).hexdigest() == export_row.sha256

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for path, entry in manifest["members"].items():
                assert hashlib.sha256(archive.read(path)).hexdigest() == \
                    entry["sha256"], f"{organization.slug}:{path}"

        assert manifest["totals"]["tables"] == len(TENANT_SCOPED)
        assert manifest["integrity"]["dangling_references"] == []
        assert manifest["integrity"]["cross_tenant_rows"] == []


def test_archiving_one_tenant_does_not_touch_the_others(populated,
                                                         platform_user):
    """The isolation question the archive raises, which is a different one
    from the export's: a lockout must land on one customer."""
    first, *rest = [organization for organization, _ in populated]
    censuses = {organization.slug: _census(organization)
                for organization in rest}

    console.archive_organization(platform_user, first, reason="Closed")

    for organization in rest:
        organization.refresh_from_db()
        assert organization.is_admitted is True, (
            f"{organization.slug} was locked out by another tenant's archive")
        assert _census(organization) == censuses[organization.slug]


def test_a_restored_tenant_works_while_another_is_still_archived(populated,
                                                                  platform_user):
    """Mixed states, which is the normal condition of a real platform."""
    first, second, third = [organization for organization, _ in populated]

    console.archive_organization(platform_user, first, reason="Closed")
    console.archive_organization(platform_user, second, reason="Closed")
    second.refresh_from_db()
    console.restore_organization(platform_user, second)

    for organization, expected in ((first, True), (second, False),
                                   (third, False)):
        organization.refresh_from_db()
        assert (organization.status == organization.Status.ARCHIVED) is expected

    health = console.tenant_health(platform_user, second)
    assert health["verdict"] == "Tenant Ready"
    assert health["archive_state"]["is_archived"] is False


# --- the audit trail for the whole cycle --------------------------------
def test_the_trail_records_the_cycle_for_each_tenant(populated, platform_user):
    """Part 8: export created, downloaded, archived, restored."""
    from tenancy.models import PlatformAuditLog

    A = PlatformAuditLog.Action
    for organization, _ in populated:
        export_row = console.create_export(platform_user, organization)
        console.record_export_download(platform_user, export_row)
        console.archive_organization(platform_user, organization, reason="Closed")
        organization.refresh_from_db()
        console.restore_organization(platform_user, organization)
        organization.refresh_from_db()

    for organization, _ in populated:
        actions = set(PlatformAuditLog.objects
                      .filter(organization=organization)
                      .values_list("action", flat=True))
        assert {A.EXPORT_CREATED, A.EXPORT_DOWNLOADED, A.TENANT_ARCHIVED,
                A.TENANT_RESTORED} <= actions, organization.slug
        # And each of THOSE four names the operator who decided it.
        #
        # Scoped to the four, because this fixture provisions without an
        # actor -- so `tenant_created` and `tenant_bootstrapped` are
        # attributed to "system", which is correct for a tenant nobody
        # created by hand. The assertion is about the actions this test
        # performed, every one of which was an operator decision.
        assert all(
            PlatformAuditLog.objects
            .filter(organization=organization,
                    action__in=[A.EXPORT_CREATED, A.EXPORT_DOWNLOADED,
                                A.TENANT_ARCHIVED, A.TENANT_RESTORED])
            .values_list("actor_email", flat=True)), organization.slug


def test_tenant_health_reports_the_whole_picture_for_each(populated,
                                                           platform_user):
    """Part 7: storage, drift, subscription, archive state, export readiness."""
    for organization, _ in populated:
        organization.refresh_from_db()
        health = console.tenant_health(platform_user, organization)
        assert health["export_readiness"]["can_export"] is True
        assert health["export_readiness"]["last_export"] is None
        assert health["archive_state"]["is_archived"] is False
        assert health["archive_state"]["can_archive"] is True
        assert health["subscription_state"]["status"]
        assert health["pending_drift"]["has_drift"] is False
        # SHAPE, NOT VALUE, and the reason is worth recording: the seat
        # counter is applied by `transaction.on_commit`, which never runs
        # inside a test wrapped in a transaction that is rolled back. So the
        # figure here is 0 for every tenant regardless of the staff
        # `_populate` created. The counter's own correctness is covered by
        # `test_counters.py`, which commits.
        assert isinstance(health["storage"]["seats"], int)
        assert isinstance(health["storage"]["bytes"], int)

        console.create_export(platform_user, organization)
        health = console.tenant_health(platform_user, organization)
        last = health["export_readiness"]["last_export"]
        assert last["status"] == "ready"
        assert last["downloadable"] is True
        assert last["rows"] > 0
        assert health["export_readiness"]["exports_held"] == 1
