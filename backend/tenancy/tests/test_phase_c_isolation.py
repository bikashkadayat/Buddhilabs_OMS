"""Phase C isolation, and the AuditLog ownership rules (Phase S5, Parts 1-2, 9).

Phase C is the awkward tier: append-only logs, derived summaries, drafts and
report records. Six models here keep a NULLABLE organization, and each one has
a reason -- so most of this file is about proving the exceptions are the ones
that were declared, not ones that crept in.
"""
import pytest
from django.apps import apps
from django.db.models.deletion import PROTECT

from tenancy import inventory, ownership, services
from tenancy.context import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_b(db, monthly_plan):
    return services.provision_organization(
        name="Gamma Ltd", slug="gammaltd", document_prefix="GAM",
        email="admin@gamma.test", plan=monthly_plan)


def _user(organization, username, role="admin"):
    from django.contrib.auth import get_user_model

    with tenant_context(organization):
        return get_user_model().objects.create_user(
            username=username, email=f"{username}@{organization.slug}.test",
            password="x-PhaseC-1", role=role)


# ---------------------------------------------------------------------------
# Part 1: the column, everywhere
# ---------------------------------------------------------------------------
def test_all_106_business_models_carry_the_column():
    missing = [label for label in sorted(inventory.TENANT_SCOPED)
               if not any(f.name == "organization"
                          for f in apps.get_model(*label.split("."))._meta.local_fields)]
    assert missing == []
    assert len(inventory.TENANT_SCOPED) == 106
    assert inventory.PHASE_C_COMPLETE is True


def test_the_column_protects_the_tenant_and_is_indexed_everywhere():
    problems = []
    for label in sorted(inventory.TENANT_SCOPED):
        field = apps.get_model(*label.split("."))._meta.get_field("organization")
        if field.remote_field.on_delete is not PROTECT:
            problems.append(f"{label}: {field.remote_field.on_delete}")
        if not field.db_index:
            problems.append(f"{label}: not indexed")
    assert problems == []


def test_exactly_the_declared_models_are_nullable():
    """Six exceptions, each with a recorded reason. No others."""
    nullable = {label for label in inventory.TENANT_SCOPED
                if apps.get_model(*label.split("."))
                ._meta.get_field("organization").null}
    assert nullable == set(inventory.NULLABLE_ORGANIZATION), (
        f"undeclared nullable: {sorted(nullable - set(inventory.NULLABLE_ORGANIZATION))}; "
        f"declared but not nullable: "
        f"{sorted(set(inventory.NULLABLE_ORGANIZATION) - nullable)}")


def test_every_nullable_exemption_carries_a_real_reason():
    for label, reason in inventory.NULLABLE_ORGANIZATION.items():
        assert len(reason) > 40, f"{label} has no real justification recorded"


# ---------------------------------------------------------------------------
# Part 9: Phase C uniqueness
# ---------------------------------------------------------------------------
TENANT_OWNED_PARENTS = {
    "task", "memo", "minute", "circular", "appraisal", "item", "user", "owner",
    "employee", "device", "recipient", "comment", "note_round", "step",
    "transfer", "disposal", "leave_request", "cycle", "draft", "attachment",
}


def test_no_phase_c_constraint_depends_on_global_uniqueness():
    """Part 9. Every one already scopes through a tenant-owned parent.

    Nothing needed converting, which is worth asserting rather than assuming:
    a Phase C log keyed on, say, (date, kind) alone would have been a shared
    namespace and nobody would have noticed.
    """
    offenders = []
    for label in sorted(inventory.PHASE_C):
        model = apps.get_model(*label.split("."))
        for field in model._meta.local_fields:
            if field.unique and not field.primary_key \
                    and not getattr(field, "one_to_one", False):
                offenders.append(f"{label}.{field.name} (global field)")
        for constraint in model._meta.constraints:
            if type(constraint).__name__ != "UniqueConstraint":
                continue
            fields = set(getattr(constraint, "fields", ()) or ())
            condition = str(getattr(constraint, "condition", "") or "")
            if ("organization" in fields or "organization" in condition
                    or fields & TENANT_OWNED_PARENTS):
                continue
            offenders.append(f"{label}.{constraint.name} {sorted(fields)}")
        for ut in model._meta.unique_together:
            if "organization" not in ut and not set(ut) & TENANT_OWNED_PARENTS:
                offenders.append(f"{label}.unique_together{list(ut)}")
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# Part 2: AuditLog ownership
# ---------------------------------------------------------------------------
def test_the_subject_wins_over_the_actor(nif, org_b):
    """"Platform staff suspended tenant X" is an event in X's history.

    If the actor won, an impersonating platform operator would file every
    action under their own (nonexistent) organization -- and a tenant's audit
    trail would be missing exactly the events it most needs to show.
    """
    from leaves.models import Department

    actor = _user(nif, "audit-actor")
    with tenant_context(org_b):
        subject = Department.objects.create(name="Subject", code="SUBJ-B")

    assert ownership.resolve_for_audit(actor=actor, instance=subject) == org_b.pk


def test_an_explicit_organization_wins_over_everything(nif, org_b):
    from leaves.models import Department

    actor = _user(nif, "audit-actor-2")
    with tenant_context(nif):
        subject = Department.objects.create(name="S", code="SUBJ-A")
    assert ownership.resolve_for_audit(
        actor=actor, instance=subject, organization=org_b) == org_b.pk


def test_the_actor_is_used_when_there_is_no_subject(nif):
    """A failed login names an email address, not a row."""
    actor = _user(nif, "audit-actor-3")
    assert ownership.resolve_for_audit(actor=actor) == nif.pk


def test_nothing_resolves_to_none_rather_than_guessing(nif):
    """A probe against an address belonging to nobody has no tenant.

    Inventing one would file the probe in some innocent customer's audit trail
    and hide it from the platform.
    """
    from tenancy.context import no_tenant

    with no_tenant():
        assert ownership.resolve_for_audit() is None


def test_log_action_stamps_the_subjects_tenant(nif, org_b):
    from audit.models import AuditLog
    from audit.services import log_action
    from leaves.models import Department

    from tenancy.ownership import resolve_for_audit

    actor = _user(nif, "audit-actor-4")
    with tenant_context(org_b):
        subject = Department.objects.create(name="Subj", code="SUBJ-LOG")

    # THE SUBJECT'S TENANT WINS OVER THE ACTOR'S, and that is the rule being
    # tested: "platform staff suspended tenant X" is an event in X's history.
    # Resolved with NO tenant in context, so neither the context nor the
    # database can be what supplied the answer.
    assert resolve_for_audit(actor=actor, instance=subject) == org_b.pk

    # The row is then written as its owner, because a connection bound to
    # another tenant cannot insert it -- see test_rls.py.
    with tenant_context(org_b):
        entry = log_action(actor, AuditLog.Action.UPDATE, instance=subject)
    assert entry.organization_id == org_b.pk


def test_log_action_tolerates_having_no_tenant_at_all(nif):
    """The failed-login path: no subject, no actor, no context."""
    from audit.models import AuditLog
    from audit.services import log_action
    from tenancy.context import no_tenant

    with no_tenant():
        entry = log_action(None, AuditLog.Action.OTHER,
                           changes={"event": "LOGIN_FAILED"})
    assert entry.organization_id is None


def test_drafts_no_longer_write_the_audit_log_directly():
    """It was the ONE caller bypassing the sanctioned writer.

    The AuditLog docstring already claimed log_action was the only writer; it
    was not, and draft lifecycle events would have been the single kind of
    audit row with no owner.
    """
    import pathlib

    source = pathlib.Path("drafts/services.py").read_text()
    assert "AuditLog.objects.create(" not in source
    assert "log_action(" in source


def test_log_action_is_now_the_only_auditlog_writer():
    import pathlib
    import re

    root = pathlib.Path(".")
    offenders = []
    for path in root.rglob("*.py"):
        sp = str(path)
        if any(x in sp for x in ("__pycache__", "/migrations/", "/tests/",
                                 "test_", "audit/services.py")):
            continue
        # A WORD BOUNDARY, not a substring: "TaskAuditLog.objects.create("
        # contains "AuditLog.objects.create(" and the per-module trails are a
        # different thing entirely -- they have concrete parents and inherit
        # from them.
        if re.search(r"(?<![A-Za-z])AuditLog\.objects\.create\(",
                     path.read_text(errors="ignore")):
            offenders.append(str(path))
    assert offenders == [], (
        f"these write AuditLog directly and so bypass the organization "
        f"stamping in audit.services.log_action: {offenders}")


def test_a_draft_event_is_owned_through_its_draft(nif):
    from audit.models import AuditLog
    from drafts.models import DocumentDraft
    from drafts.services import _log

    owner = _user(nif, "draft-owner")
    with tenant_context(nif):
        draft = DocumentDraft.objects.create(
            kind="memo", document_key="k1", owner=owner, payload={})
    _log(owner, "draft_saved", draft)
    entry = AuditLog.objects.filter(changes__event="draft_saved").first()
    assert entry is not None
    assert entry.organization_id == nif.pk


# ---------------------------------------------------------------------------
# Phase C ownership chains
# ---------------------------------------------------------------------------
def test_every_phase_c_model_is_chained_or_generic():
    covered = (set(ownership.OWNERSHIP) | ownership.GENERIC_OWNED
               | ownership.CONTEXT_OWNED)
    assert inventory.PHASE_C - covered == set()


def test_only_auditlog_is_generic_owned():
    """Everything else has a concrete parent to inherit from."""
    assert ownership.GENERIC_OWNED == {"audit.AuditLog"}


def test_a_phase_c_log_inherits_its_parents_tenant(nif, org_b):
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
    from tasks.models import Task, TaskAuditLog

    with tenant_context(org_b):
        creator = _user(org_b, "logc-creator")
        task = Task.objects.create(title="T", created_by=creator,
                                    task_number="GAM-TSK-2083-0001")

    entry = TaskAuditLog(task=task, action="created")
    assert ownership.enforce(entry) == org_b.pk

    with tenant_context(org_b):
        entry.save()
        entry.refresh_from_db()
        assert entry.organization_id == org_b.pk


def test_phase_c_logs_are_isolated_between_tenants(nif, org_b):
    from tasks.models import Task, TaskAuditLog

    for org, number in ((nif, "NIFN-TSK-2083-9001"),
                        (org_b, "GAM-TSK-2083-9001")):
        with tenant_context(org):
            creator = _user(org, f"iso-{org.slug}")
            task = Task.objects.create(title="T", created_by=creator,
                                        task_number=number)
            TaskAuditLog.objects.create(task=task, action="created")

    # Asked inside each tenant, not as a cross-tenant count: a connection
    # bound to one tenant cannot see the other's rows, which is the property
    # under test.
    seen = {}
    for org in (nif, org_b):
        with tenant_context(org):
            seen[org.slug] = set(TaskAuditLog.objects.values_list("pk",
                                                                  flat=True))
            assert len(seen[org.slug]) == 1, (org.slug, seen[org.slug])
    assert not seen[nif.slug] & seen[org_b.slug]


# ---------------------------------------------------------------------------
# Part 7: the raw SQL audit must describe the code
# ---------------------------------------------------------------------------
def test_every_raw_sql_site_is_classified():
    """A cursor.execute() nobody classified is a tenant leak nobody reviewed."""
    import ast
    import pathlib

    root = pathlib.Path(".")
    found = set()
    for path in root.rglob("*.py"):
        sp = str(path)
        if any(x in sp for x in ("__pycache__", "/migrations/", "/tests/",
                                 "test_", "/scripts/")):
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:                    # pragma: no cover
            continue
        # PARSED, not grepped. A text search also matches the prose that
        # DESCRIBES raw SQL -- this module's own audit table and a comment in
        # the middleware both say "cursor.execute()" without calling it, and a
        # detector that cannot tell code from a comment trains people to
        # ignore it.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in ("execute", "executemany", "raw", "RawSQL"):
                found.add(str(path))
                break

    classified = {key.split(":")[0] for key in inventory.RAW_SQL_AUDIT}
    unclassified = sorted(f for f in found
                          if not any(f.startswith(c) for c in classified))
    assert unclassified == [], (
        f"raw SQL with no recorded classification: {unclassified}")


def test_every_classification_names_a_real_category():
    allowed = {"ALLOWED GLOBAL", "PLATFORM ADMIN ONLY", "MUST OBEY RLS"}
    for key, entry in inventory.RAW_SQL_AUDIT.items():
        assert entry["class"] in allowed, f"{key}: {entry['class']}"
        assert len(entry["why"]) > 40, f"{key} has no real reasoning recorded"


# ---------------------------------------------------------------------------
# A platform operator's own actions belong to NO tenant (Phase S6)
# ---------------------------------------------------------------------------
#
# THE BUG THESE PIN WAS SILENT ON SQLITE, which is why it survived Phase S5.
#
# `resolve_for_audit` ends in the ambient context, and while TENANCY_ENABLED is
# False the ambient context is the single-tenant default. So a platform
# operator signing in -- no subject with a tenant, no organization of their
# own -- had their LOGIN event written into NIF's audit trail: a row saying a
# person who does not work for NIF signed into NIF's workspace. No error, no
# warning, just a wrong row in a customer's compliance record.
#
# Row-level security is what surfaced it (the write was refused on a
# connection serving the platform), and there were TWO places to fix: the
# resolver, which fell through to the context, and the stamping receiver,
# which treated the resolver's deliberate None as "not filled in yet" and
# stamped the context over it anyway.
def test_a_platform_operators_login_is_not_filed_under_a_tenant(nif):
    from audit.models import AuditLog
    from audit.services import log_action

    from tenancy.context import no_tenant

    from django.contrib.auth import get_user_model

    User = get_user_model()
    with no_tenant():
        operator = User.objects.create_user(
            username="auditless-ops", email="auditless@platform.test",
            password="x-Audit-1", is_platform_staff=True, organization=None)

        entry = log_action(operator, AuditLog.Action.LOGIN, instance=operator)

    assert entry.organization_id is None, (
        "a platform operator's own action was filed under a tenant: "
        f"{entry.organization_id}")


def test_the_resolver_refuses_to_invent_a_tenant_for_a_platform_actor(nif):
    """The first of the two fixes, in isolation."""
    from tenancy.ownership import resolve_for_audit

    from django.contrib.auth import get_user_model

    from tenancy.context import no_tenant

    User = get_user_model()
    with no_tenant():
        operator = User.objects.create_user(
            username="resolver-ops", email="resolver@platform.test",
            password="x-Audit-1", is_platform_staff=True, organization=None)

    # Called with NIF resolvable from context, as the single-tenant shim makes
    # it. The answer must still be None.
    assert resolve_for_audit(actor=operator) is None


def test_the_subject_still_wins_for_a_platform_actor(nif, org_b):
    """What was NOT changed: an action ON a tenant is that tenant's event.

    "Platform staff suspended tenant X" belongs in X's history. Only
    INVENTING a tenant for an action that names none is refused.
    """
    from leaves.models import Department

    from tenancy.context import no_tenant
    from tenancy.ownership import resolve_for_audit

    from django.contrib.auth import get_user_model

    User = get_user_model()
    with no_tenant():
        operator = User.objects.create_user(
            username="subject-ops", email="subject@platform.test",
            password="x-Audit-1", is_platform_staff=True, organization=None)
    with tenant_context(org_b):
        subject = Department.objects.create(name="Theirs", code="SUBJ-PLAT")

    assert resolve_for_audit(actor=operator, instance=subject) == org_b.pk


def test_the_stamping_receiver_respects_a_decided_none(nif):
    """The second fix: a nullable column's None is an answer, not a gap."""
    from audit.models import AuditLog

    from tenancy.context import no_tenant

    entry = AuditLog(action=AuditLog.Action.OTHER, object_repr="probe",
                      changes={})
    entry._organization_decided = True
    # Written in platform scope, which is where an unowned row belongs -- see
    # audit.services.log_action. The assertion is about the RECEIVER not
    # overwriting the None, so the write has to be allowed to happen.
    with no_tenant():
        entry.save()
        entry.refresh_from_db()
        assert entry.organization_id is None, (
            "the pre_save receiver stamped the ambient tenant over a "
            "decided None")
