"""Part 11: Tenant A cannot see Tenant B's business records.

One test per business domain, each written as the question the brief asks:
can one tenant reach the other's rows? Plus the ownership-chain tests, which
are the part that makes the column PROVE ownership rather than merely record
it.
"""
import datetime

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from tenancy import inventory, ownership, services
from tenancy.context import tenant_context
from tenancy.exceptions import CrossTenantWrite

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_a(nif):
    return nif


@pytest.fixture
def org_b(db, monthly_plan):
    return services.provision_organization(
        name="Beta Corp", slug="betacorp", document_prefix="BETA",
        email="admin@beta.test", plan=monthly_plan)


def _user(organization, username, role="admin", **extra):
    from django.contrib.auth import get_user_model

    with tenant_context(organization):
        return get_user_model().objects.create_user(
            username=username, email=f"{username}@{organization.slug}.test",
            password="x-PhaseB-1", role=role, **extra)


def _dept(organization, code):
    from leaves.models import Department

    with tenant_context(organization):
        return Department.objects.create(name=f"Dept {code}", code=code)


# ---------------------------------------------------------------------------
# The column exists everywhere, and is shaped the same everywhere
# ---------------------------------------------------------------------------
def test_all_83_tenant_models_in_the_completed_tiers_carry_the_column():
    missing = []
    for label in sorted(inventory.PHASE_A | inventory.PHASE_B):
        model = apps.get_model(*label.split("."))
        if not any(f.name == "organization" for f in model._meta.local_fields):
            missing.append(label)
    assert missing == []
    assert len(inventory.PHASE_A | inventory.PHASE_B) == 83


def test_the_phase_b_column_is_not_null_and_protects_the_tenant():
    from django.db.models.deletion import PROTECT

    problems = []
    for label in sorted(inventory.PHASE_B):
        field = apps.get_model(*label.split("."))._meta.get_field("organization")
        if field.null:
            problems.append(f"{label} is nullable")
        if field.remote_field.on_delete is not PROTECT:
            problems.append(f"{label} is not PROTECT")
        if not field.db_index:
            problems.append(f"{label} is not indexed")
    assert problems == []


# ---------------------------------------------------------------------------
# Per-domain isolation (Part 11's list)
# ---------------------------------------------------------------------------
def assert_isolated(model, org_a, org_b, *, each=1, **filters):
    """Each tenant holds `each` matching row(s), and not one of the other's.

    ASKED INSIDE EACH TENANT, not as a cross-tenant count (Phase S6).

    These assertions used to read
    ``Model.objects.filter(organization=org_a).count() == 1`` from whatever
    tenant the test happened to be bound to. That works only while nothing
    enforces the boundary: once PostgreSQL row-level security is on, a
    connection bound to one tenant cannot count another tenant's rows -- which
    is the property under test, so an assertion that spanned both tenants
    required it to be absent.

    Asking each tenant separately is backend-agnostic AND strictly stronger:
    it proves each tenant sees exactly its own rows, and that the two sets do
    not overlap, rather than that a total came to two.
    """
    seen = {}
    for org in (org_a, org_b):
        with tenant_context(org):
            seen[org.slug] = set(model.objects.filter(**filters)
                                 .values_list("pk", flat=True))
            assert len(seen[org.slug]) == each, (
                f"{org.slug} sees {len(seen[org.slug])} "
                f"{model.__name__} row(s), expected {each}")
    assert not seen[org_a.slug] & seen[org_b.slug], (
        f"the two tenants share a {model.__name__} row")


def test_task_isolation(org_a, org_b):
    from tasks.models import Task, TaskComment

    made = {}
    for org in (org_a, org_b):
        with tenant_context(org):
            author = _user(org, f"task-{org.slug}")
            task = Task.objects.create(
                title="Quarterly report", created_by=author,
                task_number=f"{org.document_prefix}-TSK-2083-0001")
            TaskComment.objects.create(task=task, author=author, body="note")
            made[org.slug] = task

    assert_isolated(Task, org_a, org_b)
    assert_isolated(TaskComment, org_a, org_b)
    with tenant_context(org_a):
        assert made[org_b.slug] not in Task.objects.all()


def test_leave_isolation(org_a, org_b):
    from leaves.models import Leave

    from leaves.models import LeaveType

    for org in (org_a, org_b):
        with tenant_context(org):
            # Every tenant needs its OWN leave types, because Phase A scoped
            # LeaveType per organization. Phase S6 closed the onboarding gap
            # this comment used to record -- provisioning now seeds a tenant's
            # configuration (tenancy/bootstrap.py), so a tenant created
            # through the console HAS an "ANNUAL" type. This fixture builds
            # its tenants directly rather than through provisioning, so it
            # still creates the one leave type it needs; the provisioned path
            # is covered by test_provisioning_conformance.py.
            LeaveType.objects.get_or_create(
                organization=org, code="annual",
                defaults={"name": "Annual Leave", "default_days_per_year": 12})
            person = _user(org, f"leave-{org.slug}", role="maker")
            Leave.objects.create(
                user=person, leave_type="annual", reason="holiday",
                start_date=datetime.date(2026, 6, 1),
                end_date=datetime.date(2026, 6, 2))

    assert_isolated(Leave, org_a, org_b)


def test_attendance_isolation(org_a, org_b):
    from attendance.models import Attendance

    for org in (org_a, org_b):
        with tenant_context(org):
            person = _user(org, f"att-{org.slug}", role="maker")
            Attendance.objects.create(employee=person,
                                      date=datetime.date(2026, 6, 1))

    assert_isolated(Attendance, org_a, org_b)


def test_memo_isolation(org_a, org_b):
    from memos.models import Memo, MemoSection

    for org in (org_a, org_b):
        with tenant_context(org):
            author = _user(org, f"memo-{org.slug}")
            memo = Memo.objects.create(
                subject="Policy", created_by=author,
                memo_number=f"{org.document_prefix}-ADM-2026-0001")
            MemoSection.objects.create(memo=memo, title="A")

    assert_isolated(Memo, org_a, org_b)
    assert_isolated(MemoSection, org_a, org_b)


def test_minute_isolation(org_a, org_b):
    from minutes.models import Minute, MinuteType

    for org in (org_a, org_b):
        with tenant_context(org):
            author = _user(org, f"min-{org.slug}")
            mtype = MinuteType.objects.filter(organization=org).first() or \
                MinuteType.objects.create(organization=org, code=f"t-{org.slug}",
                                          name="Dept")
            Minute.objects.create(
                minute_type=mtype, created_by=author,
                minute_number=f"{org.document_prefix}-MIN-2026-000001",
                meeting_time=datetime.time(10, 0))

    assert_isolated(Minute, org_a, org_b)


def test_circular_isolation(org_a, org_b):
    from circulars.models import Circular

    for org in (org_a, org_b):
        with tenant_context(org):
            author = _user(org, f"cir-{org.slug}")
            Circular.objects.create(
                subject="Notice", content="x", created_by=author,
                circular_number=f"{org.document_prefix}-CIR-2026-000001")

    assert_isolated(Circular, org_a, org_b)


def test_inventory_isolation(org_a, org_b):
    from inventory.models import InventoryCategory, InventoryItem

    for org in (org_a, org_b):
        with tenant_context(org):
            category = InventoryCategory.objects.create(
                organization=org, name=f"Laptop-{org.slug}")
            InventoryItem.objects.create(
                category=category, name="Dell", serial_number=f"SN-{org.slug}",
                asset_code=f"{org.document_prefix}-INV-0001")

    assert_isolated(InventoryItem, org_a, org_b)


def test_notification_isolation(org_a, org_b):
    """Part 8: a notification must never be visible to another tenant."""
    from notifications.models import Notification

    for org in (org_a, org_b):
        recipient = _user(org, f"notif-{org.slug}", role="maker")
        with tenant_context(org):
            Notification.objects.create(
                recipient=recipient, category="leave",
                title=f"{org.slug} only")

    titles = {}
    for org in (org_a, org_b):
        with tenant_context(org):
            titles[org.slug] = set(
                Notification.objects.values_list("title", flat=True))
    assert titles[org_a.slug] == {f"{org_a.slug} only"}
    assert titles[org_b.slug] == {f"{org_b.slug} only"}
    assert not titles[org_a.slug] & titles[org_b.slug]


def test_document_isolation(org_a, org_b):
    from documents.models import IssuedDocument

    for org in (org_a, org_b):
        with tenant_context(org):
            IssuedDocument.objects.create(
                document_number=f"{org.document_prefix}-LV-2026-0001",
                doc_type="leave_application", target_type="leave",
                target_id=org.pk)

    assert_isolated(IssuedDocument, org_a, org_b)


# ---------------------------------------------------------------------------
# Ownership chains -- the part that makes the column PROVE ownership
# ---------------------------------------------------------------------------
def test_every_phase_b_model_is_either_chained_or_a_declared_root():
    covered = set(ownership.OWNERSHIP) | ownership.CONTEXT_OWNED
    assert inventory.PHASE_B - covered == set()


def test_the_ownership_registry_covers_phase_b_and_phase_c_exactly():
    """Phase S5 added the 23 Phase C chains; nothing may be left over."""
    covered = (set(ownership.OWNERSHIP) | ownership.CONTEXT_OWNED
               | ownership.GENERIC_OWNED)
    expected = inventory.PHASE_B | inventory.PHASE_C
    assert expected - covered == set(), sorted(expected - covered)
    assert covered - expected == set(), sorted(covered - expected)


def test_every_declared_chain_resolves_to_a_tenant_scoped_parent():
    """A chain pointing at something unscoped would prove nothing."""
    broken = []
    for label, path in sorted(ownership.OWNERSHIP.items()):
        model = apps.get_model(*label.split("."))
        target = model
        for attribute in path.split("."):
            field = target._meta.get_field(attribute)
            target = field.related_model
            if target is None:
                broken.append(f"{label}.{path}: {attribute} is not a relation")
                break
        else:
            parent_label = f"{target._meta.app_label}.{target.__name__}"
            if parent_label not in inventory.TENANT_SCOPED:
                broken.append(f"{label} -> {parent_label} is not tenant-scoped")
            elif not any(f.name == "organization"
                         for f in target._meta.local_fields):
                broken.append(f"{label} -> {parent_label} has no column")
    assert broken == [], broken


def test_every_declared_chain_is_non_null_unless_declared_otherwise():
    """A nullable parent cannot be relied on to supply the owner.

    Four Phase C chains ARE nullable -- AssetLifecycleEvent.item,
    NotificationLog.recipient, ReportRun.requested_by,
    ScheduledReport.created_by -- so those models keep a nullable
    `organization` and fall back to context. They are listed in
    `ownership.NULLABLE_CHAINS` so the exception is declared rather than
    discovered.
    """
    unexpected = []
    for label, path in sorted(ownership.OWNERSHIP.items()):
        model = apps.get_model(*label.split("."))
        field = model._meta.get_field(path.split(".")[0])
        if field.null and label not in ownership.NULLABLE_CHAINS:
            unexpected.append(f"{label}.{path}")
    assert unexpected == [], unexpected


def test_every_declared_nullable_chain_really_is_nullable():
    """The exemption list must describe the code, not a memory of it."""
    wrong = []
    for label in sorted(ownership.NULLABLE_CHAINS):
        model = apps.get_model(*label.split("."))
        path = ownership.OWNERSHIP[label]
        if not model._meta.get_field(path.split(".")[0]).null:
            wrong.append(f"{label}.{path} is not nullable")
        if not model._meta.get_field("organization").null:
            wrong.append(f"{label}.organization must be nullable too")
    assert wrong == [], wrong


def test_a_child_inherits_its_parents_tenant(org_a, org_b):
    """The parent decides, not the context.

    THE DERIVATION AND THE WRITE ARE NOW TESTED SEPARATELY (Phase S6).

    "Created with no tenant in context" is a statement about APPLICATION
    logic -- the ownership chain supplies the organization rather than the
    context doing it. Under PostgreSQL row-level security the WRITE is a
    different question, and the database answers it first: a connection bound
    to one tenant cannot insert a row owned by another, whatever the
    application derived. That refusal is correct and is tested in
    test_rls.py.

    So the chain is checked with no context at all (``ownership.enforce``,
    which is exactly what the pre_save receiver calls), and the row is then
    saved as its owner. Both halves are now explicit instead of one being
    implied by the other.
    """
    from tasks.models import Task, TaskComment

    with tenant_context(org_b):
        author = _user(org_b, "chain-author")
        task = Task.objects.create(title="T", created_by=author,
                                   task_number="BETA-TSK-2083-0009")

    # No tenant in context: the chain alone supplies the organization.
    comment = TaskComment(task=task, author=author, body="x")
    assert ownership.enforce(comment) == org_b.pk
    assert comment.organization_id == org_b.pk

    # Saved AND re-read as its owner: a connection bound elsewhere can see
    # neither the insert nor the row afterwards.
    with tenant_context(org_b):
        comment.save()
        comment.refresh_from_db()
        assert comment.organization_id == org_b.pk


def test_filing_a_child_against_another_tenants_parent_is_refused(org_a, org_b):
    """The check that turns "the column is filled in" into "it is correct"."""
    from tasks.models import Task, TaskComment

    with tenant_context(org_b):
        b_author = _user(org_b, "victim-author")
        b_task = Task.objects.create(title="B's task", created_by=b_author,
                                     task_number="BETA-TSK-2083-0010")

    with pytest.raises(CrossTenantWrite):
        TaskComment.objects.create(task=b_task, author=b_author, body="x",
                                   organization=org_a)


def test_the_chain_wins_over_an_incorrect_context(org_a, org_b):
    """Context cannot be used to misattribute a child row.

    The DERIVATION is what this is about, and it is checked while the wrong
    tenant is in context. The write then happens as the right one, because
    under row-level security a connection bound to org_a cannot insert a row
    owned by org_b -- a second, independent refusal of the same mistake, and
    the one that holds even if the chain logic is wrong.
    """
    from memos.models import Memo, MemoSection

    with tenant_context(org_b):
        author = _user(org_b, "ctx-author")
        memo = Memo.objects.create(subject="S", created_by=author,
                                   memo_number="BETA-ADM-2026-0002")

    section = MemoSection(memo=memo, title="t")
    with tenant_context(org_a):          # deliberately the WRONG tenant
        assert ownership.enforce(section) == org_b.pk

    with tenant_context(org_b):
        section.save()
        section.refresh_from_db()
        assert section.organization_id == org_b.pk


# ---------------------------------------------------------------------------
# Per-tenant constraints (Part 4)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model_path,field,value", [
    ("tasks.Task", "task_number", "SHARED-TSK-0001"),
    ("memos.Memo", "memo_number", "SHARED-ADM-0001"),
    ("minutes.Minute", "minute_number", "SHARED-MIN-0001"),
    ("circulars.Circular", "circular_number", "SHARED-CIR-0001"),
    ("documents.IssuedDocument", "document_number", "SHARED-LV-0001"),
])
def test_two_tenants_may_hold_the_same_document_number(model_path, field, value,
                                                        org_a, org_b):
    """Was unique=True platform-wide; one tenant could refuse another's insert."""
    model = apps.get_model(*model_path.split("."))
    for org in (org_a, org_b):
        with tenant_context(org):
            _make(model, org, **{field: value})
            # Asserted inside each tenant's context rather than as one
            # cross-tenant count of 2 (Phase S6). A connection bound to one
            # tenant cannot see the other's row under row-level security --
            # which IS the boundary being tested, so a count that spanned
            # both required the boundary to be absent. "Each tenant holds
            # exactly one" is the same claim, plus the proof that neither
            # sees the other's.
            assert model.objects.filter(**{field: value}).count() == 1


@pytest.mark.parametrize("model_path,field,value", [
    ("tasks.Task", "task_number", "DUP-TSK-0001"),
    ("memos.Memo", "memo_number", "DUP-ADM-0001"),
    ("documents.IssuedDocument", "document_number", "DUP-LV-0001"),
])
def test_one_tenant_still_cannot_reuse_a_document_number(model_path, field,
                                                          value, org_a):
    """Scoping must not have weakened the within-tenant guarantee."""
    model = apps.get_model(*model_path.split("."))
    with tenant_context(org_a):
        _make(model, org_a, **{field: value})
        with pytest.raises(IntegrityError), transaction.atomic():
            _make(model, org_a, **{field: value})


def _make(model, organization, **kwargs):
    """Create a minimal valid instance of one of the numbered models."""
    label = f"{model._meta.app_label}.{model.__name__}"
    author = _user(organization, f"mk-{organization.slug}-{abs(hash(str(kwargs)))%10000}")
    if label == "tasks.Task":
        return model.objects.create(title="T", created_by=author, **kwargs)
    if label == "memos.Memo":
        return model.objects.create(subject="S", created_by=author, **kwargs)
    if label == "minutes.Minute":
        from minutes.models import MinuteType

        mtype = (MinuteType.objects.filter(organization=organization).first()
                 or MinuteType.objects.create(organization=organization,
                                              code=f"mt-{organization.slug}",
                                              name="T"))
        return model.objects.create(minute_type=mtype, created_by=author,
                                     meeting_time=datetime.time(10, 0),
                                     **kwargs)
    if label == "circulars.Circular":
        return model.objects.create(subject="C", content="b",
                                     created_by=author, **kwargs)
    if label == "documents.IssuedDocument":
        return model.objects.create(doc_type="leave_application",
                                     target_type="leave",
                                     target_id=organization.pk, **kwargs)
    raise AssertionError(label)


def test_two_tenants_may_register_the_same_asset_serial(org_a, org_b):
    """Part 7: the OPPOSITE decision to BiometricDevice.serial_number."""
    from inventory.models import InventoryCategory, InventoryItem

    for org in (org_a, org_b):
        with tenant_context(org):
            category = InventoryCategory.objects.create(
                organization=org, name=f"Cat-{org.slug}")
            InventoryItem.objects.create(
                category=category, name="Dell", serial_number="SHARED-SERIAL",
                asset_code=f"{org.document_prefix}-INV-0007")
    assert_isolated(InventoryItem, org_a, org_b,
                    serial_number="SHARED-SERIAL")


def test_one_tenant_still_cannot_register_a_serial_twice(org_a):
    from inventory.models import InventoryCategory, InventoryItem

    with tenant_context(org_a):
        category = InventoryCategory.objects.create(organization=org_a,
                                                     name="Cat-dup")
        InventoryItem.objects.create(category=category, name="A",
                                      serial_number="ONE-ONLY",
                                      asset_code="NIF-INV-0101")
        with pytest.raises(IntegrityError), transaction.atomic():
            InventoryItem.objects.create(category=category, name="B",
                                          serial_number="one-only",
                                          asset_code="NIF-INV-0102")


# ---------------------------------------------------------------------------
# Part 6: verification by ownership, not by prefix
# ---------------------------------------------------------------------------
def test_verification_uses_ownership_not_the_number_prefix(org_a, org_b):
    """R7' closed. A document whose number LOOKS foreign is still ours."""
    from django.test import override_settings
    from rest_framework.test import APIClient

    from documents.models import IssuedDocument

    # Deliberately a number carrying the OTHER tenant's prefix, owned by A.
    with tenant_context(org_a):
        IssuedDocument.objects.create(
            organization=org_a, document_number="BETA-LV-2026-0042",
            doc_type="leave_application", target_type="leave",
            target_id=org_a.pk)

    with override_settings(TENANCY_BASE_DOMAIN="platform.test",
                           TENANCY_PLATFORM_HOSTS=""):
        mine = APIClient().get("/api/v1/verify/BETA-LV-2026-0042/?format=json",
                               HTTP_HOST="nif.platform.test")
        theirs = APIClient().get("/api/v1/verify/BETA-LV-2026-0042/?format=json",
                                 HTTP_HOST="betacorp.platform.test")

    assert mine.status_code == 200, "ownership should decide, not the prefix"
    assert theirs.status_code == 404, "the prefix must not grant access"


# ---------------------------------------------------------------------------
# Part 2: the backfill verifies itself and REFUSES on an orphan
# ---------------------------------------------------------------------------
class _FakeQuerySet:
    def __init__(self, store, key, remaining_after_update=0):
        self._store = store
        self._key = key
        self._remaining = remaining_after_update

    def filter(self, **kwargs):
        return self

    def update(self, **kwargs):
        self._store.setdefault("updated", []).append((self._key, kwargs))
        return 7

    def count(self):
        return self._remaining


class _FakeModel:
    def __init__(self, store, key, remaining=0):
        self.objects = _FakeQuerySet(store, key, remaining)


class _FakeApps:
    """Stands in for the historical model registry a migration is handed."""

    def __init__(self, organizations, remaining_by_model=None):
        self.store = {}
        self._orgs = organizations
        self._remaining = remaining_by_model or {}

    def get_model(self, app_label, model_name):
        if (app_label, model_name) == ("tenancy", "Organization"):
            return _FakeOrganization(self._orgs)
        return _FakeModel(self.store, model_name,
                          self._remaining.get(model_name, 0))


class _FakeOrganization:
    def __init__(self, organizations):
        self.objects = _FakeOrgQuerySet(organizations)


class _FakeOrgQuerySet:
    def __init__(self, organizations):
        self._orgs = organizations

    def filter(self, **kwargs):
        slug = kwargs.get("slug")
        return _FakeOrgQuerySet([o for o in self._orgs if o.slug == slug])

    def first(self):
        return self._orgs[0] if self._orgs else None

    def all(self):
        return self

    def __getitem__(self, item):
        return self._orgs[item]


class _Org:
    def __init__(self, pk, slug):
        self.pk = pk
        self.slug = slug


def test_the_backfill_assigns_every_model_to_the_default_organization():
    from tenancy.migration_utils import backfill

    apps_stub = _FakeApps([_Org("org-1", "nif")])
    updated = backfill(apps_stub, "tasks", ["Task", "TaskComment"])
    assert updated == {"Task": 7, "TaskComment": 7}
    assert [key for key, _ in apps_stub.store["updated"]] == ["Task",
                                                              "TaskComment"]


def test_the_backfill_refuses_when_an_orphan_row_survives():
    """Part 2: "Reject migration if orphan exists."

    The NOT NULL step that follows would fail anyway -- but with an opaque
    database error naming a column, one migration later. Refusing here names
    the MODEL and the COUNT, which is the difference between a five-minute fix
    and an afternoon.
    """
    from tenancy.migration_utils import backfill

    apps_stub = _FakeApps([_Org("org-1", "nif")],
                          remaining_by_model={"TaskComment": 3})
    with pytest.raises(RuntimeError) as caught:
        backfill(apps_stub, "tasks", ["Task", "TaskComment"])
    assert "TaskComment" in str(caught.value)
    assert "3" in str(caught.value)
    assert "orphan" in str(caught.value).lower()


def test_the_backfill_refuses_to_guess_between_two_organizations():
    """A backfill must never pick which tenant owns the existing rows."""
    from tenancy.migration_utils import default_organization_id

    apps_stub = _FakeApps([_Org("a", "alpha"), _Org("b", "beta")])
    with pytest.raises(RuntimeError) as caught:
        default_organization_id(apps_stub)
    assert "more than one" in str(caught.value).lower()


def test_the_backfill_refuses_when_no_organization_exists_at_all():
    from tenancy.migration_utils import default_organization_id

    with pytest.raises(RuntimeError) as caught:
        default_organization_id(_FakeApps([]))
    assert "No Organization" in str(caught.value)
