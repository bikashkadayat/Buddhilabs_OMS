"""Part 2: every tenant owns its own numbering.

THE DEFECT, RESTATED: before Phase S2 each sequence table held ONE counter row
per (year) or (key, year) for the whole platform. Tenant A creating a task
advanced Tenant B's counter, so Tenant B's own numbering developed gaps it
could not account for -- and the gap-free guarantee these tables were
deliberately built for (select_for_update on a counter row, not a max() read)
was silently destroyed.

These tests assert the two properties that fix it: counters are per-tenant, and
the strings two tenants mint can never collide.
"""
import pytest
from django.test import override_settings

from tenancy import numbering, services
from tenancy.context import tenant_context
from tenancy.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def other(db, monthly_plan):
    return services.provision_organization(
        name="XYZ Hospital", slug="xyzhospital", document_prefix="XYZ",
        email="admin@xyz.test", plan=monthly_plan)


def _generators():
    from circulars.services import generate_circular_number
    from inventory.disposals import generate_disposal_number
    from inventory.lifecycle import (generate_maintenance_reference,
                                     generate_request_reference,
                                     generate_return_reference)
    from inventory.services import (generate_asset_code,
                                    generate_takeout_reference)
    from inventory.transfers import generate_transfer_number
    from minutes.services import generate_minute_number
    from tasks.services import generate_task_number

    return {
        "task": generate_task_number,
        "minute": generate_minute_number,
        "circular": generate_circular_number,
        "asset_code": generate_asset_code,
        "takeout": generate_takeout_reference,
        "transfer": generate_transfer_number,
        "disposal": generate_disposal_number,
        "asset_request": generate_request_reference,
        "asset_return": generate_return_reference,
        "maintenance": generate_maintenance_reference,
    }


@pytest.mark.parametrize("kind", sorted(_generators()))
def test_two_tenants_never_mint_the_same_number(kind, nif, other):
    generate = _generators()[kind]

    with tenant_context(nif):
        mine = generate()
    with tenant_context(other):
        theirs = generate()

    assert mine != theirs, f"{kind}: both tenants minted {mine!r}"


@pytest.mark.parametrize("kind", sorted(_generators()))
def test_one_tenants_numbering_does_not_advance_anothers(kind, nif, other):
    """The exact defect Part 2 names, as a test."""
    generate = _generators()[kind]

    def tail(number):
        return int(number.rsplit("-", 1)[-1])

    with tenant_context(nif):
        first = generate()
        second = generate()

    # Tenant B's very first number must still be its FIRST, regardless of how
    # many Tenant A has taken.
    with tenant_context(other):
        theirs = generate()

    # CONSECUTIVE, not literally 0001 (Phase S6). NIF's counter row is seeded
    # by migration and survives whatever ran earlier in the session, so an
    # absolute assertion here measures test ordering rather than isolation.
    # What the defect was about is the RELATIONSHIP between the two tenants.
    assert tail(second) == tail(first) + 1, (first, second)
    assert theirs.endswith(("0001", "000001")), (
        f"{kind}: tenant B's first number is {theirs!r} -- its counter was "
        f"advanced by tenant A")


def test_memo_numbers_are_per_tenant_and_per_type(nif, other):
    from memos.services import generate_memo_number

    with tenant_context(nif):
        a1 = generate_memo_number("administrative")
        a2 = generate_memo_number("administrative")
    with tenant_context(other):
        b1 = generate_memo_number("administrative")

    assert a1 != b1
    assert a1.endswith("0001") and a2.endswith("0002")
    assert b1.endswith("0001")


def test_employee_ids_are_per_tenant(nif, other, django_user_model):
    from users.services import generate_employee_id

    with tenant_context(nif):
        mine = generate_employee_id()
    with tenant_context(other):
        theirs = generate_employee_id()

    assert mine.startswith("NIFN-EMP-")
    assert theirs.startswith("XYZ-EMP-")
    assert mine != theirs


def test_each_tenant_gets_its_own_counter_row(nif, other):
    from tasks.models import TaskNumberSequence
    from tasks.services import generate_task_number

    def counters():
        return dict(TaskNumberSequence.objects.values_list(
            "organization__slug", "last_value"))

    with tenant_context(nif):
        before = counters().get("nif", 0)
        generate_task_number(); generate_task_number(); generate_task_number()
        # Read inside the context, not across tenants afterwards: under
        # row-level security a NIF-bound connection cannot see the other
        # tenant's counter row at all, which is the property this test is
        # about. ONE row, advanced by exactly three -- relative, because NIF's
        # counter is migration-seeded and whatever ran earlier in the session
        # has already moved it.
        assert counters() == {"nif": before + 3}

    with tenant_context(other):
        generate_task_number()
        # Its own counter started at 1 rather than continuing from NIF's --
        # before Phase S2 one row served the whole platform, so tenant #2's
        # first task was numbered 0004 and each tenant saw gaps it could not
        # explain.
        assert counters() == {"xyzhospital": 1}


# --- NIF's formats are unchanged. This is the Part 7 guarantee. ---------
def test_nif_number_formats_are_byte_identical_to_before_phase_s2(nif):
    """The formats the system emitted before Phase S2, asserted literally.

    If any of these changes, NIF's documents start carrying numbers in a format
    its staff and its filing do not recognise -- which the phase brief forbids.
    """
    expected = {
        "TSK":  ("NIFN-TSK-2083-0001", dict(year=2083, value=1)),
        "MEMO": ("NIFN-HR-2026-0042",  dict(year=2026, value=42, type_code="HR")),
        "MIN":  ("MIN-2026-000001",    dict(year=2026, value=1, width=6)),
        "CIR":  ("CIR-2026-000001",    dict(year=2026, value=1, width=6)),
        "INV":  ("NIF-INV-0001",       dict(year=None, value=1)),
        "OUT":  ("NIF-OUT-2083-0001",  dict(year=2083, value=1)),
        "AR":   ("NIF-AR-2083-0001",   dict(year=2083, value=1)),
        "RT":   ("NIF-RT-2083-0001",   dict(year=2083, value=1)),
        "MT":   ("NIF-MT-2083-0001",   dict(year=2083, value=1)),
        "TRF":  ("TRF-2026-0001",      dict(year=2026, value=1)),
        "DSP":  ("DSP-2026-0001",      dict(year=2026, value=1)),
        "EMP":  ("NIFN-EMP-2026-0001", dict(year=2026, value=1)),
        "LV":   ("NIFN-LV-2026-0001",  dict(year=2026, value=1)),
        "CERT": ("NIFN-CERT-2026-0001", dict(year=2026, value=1)),
    }
    for kind, (want, kwargs) in expected.items():
        got = numbering.format_number(kind, organization=nif, **kwargs)
        assert got == want, f"{kind}: {got!r} != {want!r}"


def test_a_new_tenant_gets_one_consistent_prefixed_scheme(other):
    """No legacy carve-outs for anybody but NIF."""
    for kind, width in (("TSK", 4), ("MIN", 6), ("CIR", 6), ("TRF", 4),
                        ("DSP", 4), ("INV", 4)):
        got = numbering.format_number(kind, year=2026, value=1, width=width,
                                      organization=other)
        assert got.startswith("XYZ-"), f"{kind}: {got!r} is not prefixed"


def test_only_nif_uses_the_legacy_formats(other):
    assert other.legacy_number_formats is False
    assert Organization.objects.filter(legacy_number_formats=True).count() == 1


# --- the shim stops guessing once a second tenant exists ----------------
@override_settings(TENANCY_DEFAULT_SLUG="")
def test_minting_a_number_with_two_tenants_and_no_context_is_refused(nif, other):
    """Fail closed. With two tenants there is no safe default, so there is none.

    TENANCY_DEFAULT_SLUG is pinned empty, because that is the condition the
    sentence above describes: naming a default tenant gives the shim a safe
    answer, and resolving it is then correct rather than a guess. Without the
    override this test asserted a property of the environment it happened to
    run in.
    """
    from tasks.services import generate_task_number
    from tenancy.exceptions import TenantScopeMissing

    with pytest.raises(TenantScopeMissing):
        generate_task_number()


# --- the constraints, enforced by the database -------------------------
def test_each_tenant_may_hold_its_own_sequence_row_for_the_same_year(nif, other):
    """What the old global `unique=True` on `year` made impossible."""
    from circulars.models import CircularNumberSequence
    from inventory.models import InventorySequence
    from memos.models import MemoNumberSequence
    from minutes.models import MinuteNumberSequence
    from tasks.models import TaskNumberSequence

    rows = {}
    for org in (nif, other):
        # Created AS the tenant, and counted as the tenant. A counter row is
        # tenant data: a connection bound elsewhere can neither insert nor see
        # it, which is the isolation these per-tenant constraints exist for.
        # Year 2998, which no generator will ever mint -- see the note above
        # the parametrised test below for why a real year collides.
        with tenant_context(org):
            TaskNumberSequence.objects.create(organization=org, year=2998)
            MinuteNumberSequence.objects.create(organization=org, year=2998)
            CircularNumberSequence.objects.create(organization=org, year=2998)
            MemoNumberSequence.objects.create(organization=org,
                                               type_code="SHARED", year=2998)
            InventorySequence.objects.create(organization=org, key="SHARED",
                                              year=2998)

            rows[org.slug] = (
                TaskNumberSequence.objects.filter(year=2998).count(),
                MinuteNumberSequence.objects.filter(year=2998).count(),
                CircularNumberSequence.objects.filter(year=2998).count(),
                MemoNumberSequence.objects.filter(year=2998).count(),
                InventorySequence.objects.filter(year=2998).count(),
            )

    # Each tenant holds exactly one row of each kind for the same key. Before
    # Phase S2 the second tenant's inserts were refused outright by a global
    # unique on `year`.
    assert rows[nif.slug] == (1, 1, 1, 1, 1), rows[nif.slug]
    assert rows[other.slug] == (1, 1, 1, 1, 1), rows[other.slug]


# YEARS AND KEYS NOTHING ELSE USES (Phase S6).
#
# These were the real ones -- 2083 for tasks, 2026 for minutes. The
# application's own `get_or_create` makes a (tenant, current year) counter row
# the first time anybody mints a number, and a `transaction=True` test
# elsewhere in the session COMMITS it. A test that then creates the same row by
# hand fails on a duplicate it did not make, which looks like a broken
# constraint and is really a test owning data it shares.
@pytest.mark.parametrize("model_path,kwargs", [
    ("tasks.models.TaskNumberSequence", {"year": 2999}),
    ("minutes.models.MinuteNumberSequence", {"year": 2999}),
    ("circulars.models.CircularNumberSequence", {"year": 2999}),
    ("memos.models.MemoNumberSequence", {"type_code": "UNIQ", "year": 2999}),
    ("inventory.models.InventorySequence", {"key": "UNIQ", "year": 2999}),
])
def test_one_tenant_still_cannot_hold_two_rows_for_the_same_key(
        model_path, kwargs, nif):
    """Scoping must not have weakened the within-tenant guarantee.

    The counter is what makes numbering gap-free; two rows for one tenant would
    hand out the same number twice.
    """
    import importlib

    from django.db import IntegrityError, transaction

    module_name, class_name = model_path.rsplit(".", 1)
    model = getattr(importlib.import_module(module_name), class_name)

    model.objects.create(organization=nif, **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(organization=nif, **kwargs)


def test_two_tenants_may_both_hold_an_open_global_policy_assignment(nif, other):
    """`uniq_open_global_policy_assignment` allowed exactly ONE row
    platform-wide, so only one tenant in the entire platform could ever
    configure a global attendance policy -- tenant #2's first policy setup
    failed on an opaque integrity error.

    NIF already has one from the attendance migrations, so the test is: can a
    SECOND tenant now have its own? And is the within-tenant rule still "at
    most one"?

    PHASE S6 CHANGED THE FIRST HALF OF THIS TEST, AND MADE IT STRONGER. The
    second tenant no longer has to be given a global assignment by the test,
    because provisioning now gives it one (tenancy/bootstrap.py) -- which is
    the point of Part 4: a tenant whose attendance policy resolves to nothing
    is a tenant whose attendance module does not work. So the assertion is
    now that BOTH tenants hold one without anybody doing anything, which is
    what the constraint had to permit all along.
    """
    from django.db import IntegrityError, transaction

    from attendance.policy.models import PolicyAssignment

    nif_global = PolicyAssignment.objects.filter(
        organization=nif, scope="global", effective_until__isnull=True)
    assert nif_global.exists(), "expected NIF's seeded global assignment"

    # Read as the tenant that owns it: a connection bound to NIF cannot see
    # another tenant's assignment, which is the boundary working.
    with tenant_context(other):
        other_global = PolicyAssignment.objects.filter(
            scope="global", effective_until__isnull=True)
        assert other_global.exists(), (
            "provisioning must leave a new tenant with a global attendance "
            "policy assignment")

        # Exactly one, within this tenant. Before Phase S2 a single row was
        # allowed platform-wide, so the SECOND tenant to configure a global
        # policy was refused outright -- this insert could not have happened
        # at all.
        assert other_global.count() == 1
        other_policy = other_global.first().policy

        # ...and within ONE tenant it is still at most one, so policy
        # resolution can never be ambiguous.
        with pytest.raises(IntegrityError), transaction.atomic():
            PolicyAssignment.objects.create(
                organization=other, policy=other_policy,
                scope="global", effective_from="2027-01-01")
