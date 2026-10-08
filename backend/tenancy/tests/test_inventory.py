"""Part 8: the multi-tenant audit inventory, checked against the real codebase.

The first test is the important one. It walks every model Django knows about
and fails if any is missing from tenancy.inventory. That is what stops the
Phase S2/S3 plan going stale: a model added next month cannot be silently
forgotten, because CI stops until somebody says which phase it belongs to.
"""
import pytest
from django.apps import apps

from tenancy import inventory

# Apps whose models are infrastructure rather than either tenant data or
# platform data. They are listed explicitly in inventory.PLATFORM_GLOBAL too;
# this set just makes the failure message useful.
INFRASTRUCTURE_APPS = {"contenttypes", "auth", "sessions", "admin",
                       "token_blacklist"}


def _all_labels():
    return {f"{m._meta.app_label}.{m.__name__}" for m in apps.get_models()}


def test_every_model_in_the_project_is_classified():
    """The guard that keeps the inventory true over time."""
    unclassified = sorted(_all_labels() - inventory.ALL_CLASSIFIED)
    assert unclassified == [], (
        "These models are not in tenancy/inventory.py. Add each to PHASE_A, "
        "PHASE_B, PHASE_C or PLATFORM_GLOBAL -- deciding which is the point of "
        f"this failure, not a formality: {unclassified}")


def test_the_inventory_names_no_model_that_does_not_exist():
    """A classification that has outlived its model is a stale plan."""
    phantom = sorted(inventory.ALL_CLASSIFIED - _all_labels())
    assert phantom == [], f"inventory names models that do not exist: {phantom}"


def test_the_phases_do_not_overlap():
    assert not (inventory.PHASE_A & inventory.PHASE_B)
    assert not (inventory.PHASE_B & inventory.PHASE_C)
    assert not (inventory.PHASE_A & inventory.PHASE_C)
    assert not (inventory.TENANT_SCOPED & inventory.PLATFORM_GLOBAL)


def test_every_tenancy_model_is_platform_global():
    """This app holds records ABOUT tenants, so none of it is tenant-scoped."""
    tenancy_models = {label for label in _all_labels()
                      if label.startswith("tenancy.")}
    assert tenancy_models <= inventory.PLATFORM_GLOBAL


def test_the_only_exemptions_are_tenancy_and_infrastructure():
    """The exemption list is the one thing a reviewer must read carefully."""
    for label in inventory.PLATFORM_GLOBAL:
        app_label = label.split(".")[0]
        assert app_label == "tenancy" or app_label in INFRASTRUCTURE_APPS, (
            f"{label} is exempted from tenant scoping but is neither a tenancy "
            f"record nor infrastructure. Exempting business data is how a leak "
            f"gets designed in.")


# --- the phase ordering invariants -------------------------------------
def test_identity_and_structure_are_in_phase_a():
    """Everything else references these, so they cannot migrate second."""
    for label in ("users.User", "leaves.Department"):
        assert inventory.classify(label) == "A"


def test_every_number_sequence_is_in_phase_a():
    """The highest-value items in the whole migration.

    While a sequence table is keyed on year alone, one tenant's document
    creation advances another tenant's counter -- so Company A develops gaps in
    its own numbering that it cannot explain, and the gap-free guarantee these
    tables were deliberately built for is silently destroyed.
    """
    sequences = [label for label in _all_labels()
                 if label.endswith("NumberSequence")
                 or label.endswith("InventorySequence")]
    assert sequences, "no sequence models found -- has the naming changed?"
    for label in sequences:
        assert inventory.classify(label) == "A", (
            f"{label} must be in PHASE_A: a shared counter corrupts document "
            f"numbering for every tenant.")


def test_the_transactional_records_named_in_the_brief_are_in_phase_b():
    for label in ("memos.Memo", "minutes.Minute", "circulars.Circular",
                  "inventory.InventoryItem", "tasks.Task", "leaves.Leave",
                  "attendance.Attendance"):
        assert inventory.classify(label) == "B", label


def test_the_derived_and_log_models_named_in_the_brief_are_in_phase_c():
    for label in ("reports.ReportRun", "reports.ScheduledReport",
                  "notifications.NotificationPreference",
                  "drafts.DocumentDraft", "audit.AuditLog"):
        assert inventory.classify(label) == "C", label


# --- Phase A is DONE: verify it against the real schema -----------------
def test_every_phase_a_model_has_an_organization_column():
    """The core claim of Phase S2, checked against the models themselves."""
    missing = []
    for label in sorted(inventory.PHASE_A):
        model = apps.get_model(*label.split("."))
        if not any(f.name == "organization" for f in model._meta.local_fields):
            missing.append(label)
    assert missing == [], f"Phase A models with no organization column: {missing}"


def test_the_phase_a_column_is_not_null_everywhere_but_user():
    """27 of 28 Phase A models are NOT NULL. users.User is the exception.

    A platform account must have organization = NULL, so the column cannot be
    NOT NULL. `user_tenant_has_organization` enforces the rule instead, and it
    is strictly stronger: it also forbids the reverse mistake (a platform
    account WITH an organization).

    Phase S5 added five MORE nullable models, all in Phase C -- so this test is
    scoped to Phase A and the whole picture is asserted by
    `test_exactly_the_declared_models_are_nullable` in
    tenancy/tests/test_phase_c_isolation.py.
    """
    nullable = {label for label in inventory.PHASE_A
                if apps.get_model(*label.split("."))
                ._meta.get_field("organization").null}
    assert nullable == {"users.User"}
    assert nullable <= set(inventory.NULLABLE_ORGANIZATION)


def test_the_organization_fk_protects_the_tenant_row():
    """PROTECT, never CASCADE: deleting a tenant must not delete its data."""
    from django.db.models.deletion import PROTECT

    wrong = []
    for label in sorted(inventory.PHASE_A):
        field = apps.get_model(*label.split("."))._meta.get_field("organization")
        if field.remote_field.on_delete is not PROTECT:
            wrong.append(f"{label}: {field.remote_field.on_delete}")
    assert wrong == [], f"organization FK must be PROTECT: {wrong}"


def test_no_phase_a_field_is_globally_unique_any_more():
    """Every global unique Phase A held has been replaced.

    This is the inverse of the Phase S1 test: back then the list described
    defects to fix, and now it must describe nothing at all.
    """
    offenders = []
    for label in sorted(inventory.PHASE_A):
        model = apps.get_model(*label.split("."))
        for field in model._meta.local_fields:
            if field.unique and not field.primary_key:
                offenders.append(f"{label}.{field.name}")
    assert offenders == [], (
        f"these Phase A fields are still globally unique: {offenders}")


def test_every_phase_a_replacement_constraint_exists_by_name():
    """The rewrite record must describe the schema, not an intention."""
    declared = set()
    for label in inventory.PHASE_A:
        model = apps.get_model(*label.split("."))
        declared |= {c.name for c in model._meta.constraints}
        declared |= {f"unique_together({','.join(ut)})"
                     for ut in model._meta.unique_together}

    missing = []
    for old, replacement in sorted(inventory.PHASE_A_UNIQUE_REWRITES.items()):
        for name in replacement.split(" + "):
            if name not in declared:
                missing.append(f"{old} -> {name}")
    assert missing == [], f"replacement constraints not found: {missing}"


# The two partial constraints that cover the organization IS NULL case. They
# are the COMPLEMENT of the scoped ones, not composites, so they deliberately
# do not name `organization` in their fields -- it appears in their condition.
NULL_ORGANIZATION_CONSTRAINTS = {
    "uniq_platform_user_username", "uniq_platform_user_email_ci",
}


def test_every_phase_a_replacement_constraint_includes_the_organization():
    """A composite that forgot the tenant column would be the same bug again."""
    bad = []
    for label in sorted(inventory.PHASE_A):
        model = apps.get_model(*label.split("."))
        for constraint in model._meta.constraints:
            if constraint.name not in _phase_a_replacement_names():
                continue
            if constraint.name in NULL_ORGANIZATION_CONSTRAINTS:
                assert "organization" in str(constraint.condition), (
                    f"{constraint.name} must scope by organization in its "
                    f"condition")
                continue
            fields = set(getattr(constraint, "fields", ()) or ())
            expressions = str(getattr(constraint, "expressions", ""))
            if "organization" not in fields and "organization" not in expressions:
                bad.append(f"{label}.{constraint.name}")
        for ut in model._meta.unique_together:
            if "organization" not in ut:
                bad.append(f"{label}.unique_together{ut}")
    assert bad == [], f"replacement constraints missing `organization`: {bad}"


def _phase_a_replacement_names():
    names = set()
    for replacement in inventory.PHASE_A_UNIQUE_REWRITES.values():
        names |= {n for n in replacement.split(" + ")
                  if not n.startswith("unique_together")}
    return names


def test_the_user_identity_constraints_are_all_present():
    from django.contrib.auth import get_user_model

    declared = {c.name for c in get_user_model()._meta.constraints}
    missing = [n for n in inventory.USER_IDENTITY_CONSTRAINTS if n not in declared]
    assert missing == [], f"missing User identity constraints: {missing}"


def test_nothing_is_deferred_any_more():
    """Phase S1 left one constraint waiting on the user backfill. S2 added it."""
    assert inventory.DEFERRED_CONSTRAINTS == {}


# --- what is still outstanding, and why it is safe for now --------------
def test_nothing_is_outstanding_after_phase_b():
    """Phase S4 emptied both to-do lists. They must stay empty."""
    assert inventory.GLOBAL_UNIQUE_FIELDS == {}
    assert inventory.BROKEN_COMPOSITE_CONSTRAINTS == {}
    assert inventory.PHASE_B_COMPLETE is True


def test_the_only_globally_unique_field_left_is_the_one_that_must_be():
    """Sweeps every tenant-scoped model and allows exactly one exemption.

    A one-to-one field is skipped: its uniqueness is over a FK to a row that
    already belongs to one tenant, so it is structural, not a namespace.
    """
    offenders = []
    for label in sorted(inventory.TENANT_SCOPED):
        model = apps.get_model(*label.split("."))
        for field in model._meta.local_fields:
            if not field.unique or field.primary_key:
                continue
            if getattr(field, "one_to_one", False):
                continue
            if label == "users.User" and field.name == "username":
                continue      # handled by the per-org constraints (Phase S2)
            offenders.append(f"{label}.{field.name}")
    assert offenders == [], (
        f"globally unique fields with no recorded justification: {offenders}")


def test_no_completed_tier_constraint_is_global_except_the_one_exemption():
    """Sweeps Meta UniqueConstraints on the COMPLETED tiers (A and B).

    Phase C is excluded because those 23 models have no organization column
    yet -- their constraints cannot name a tenant until Phase S5 gives them
    one. `test_no_phase_c_model_has_an_organization_column_yet` is the guard
    that keeps that honest in the other direction.
    """
    offenders = []
    for label in sorted(inventory.PHASE_A | inventory.PHASE_B):
        model = apps.get_model(*label.split("."))
        for constraint in model._meta.constraints:
            if type(constraint).__name__ != "UniqueConstraint":
                continue
            fields = set(getattr(constraint, "fields", ()) or ())
            exprs = str(getattr(constraint, "expressions", ""))
            # The tenant may also appear in the CONDITION -- that is how the
            # two users.User partials cover the organization IS NULL case.
            condition = str(getattr(constraint, "condition", "") or "")
            if any("organization" in x for x in (fields, exprs, condition)):
                continue
            # Scoped through a tenant-owned parent instead (one X per Y).
            if fields & _TENANT_OWNED_PARENTS:
                continue
            key = f"{label}:{constraint.name}"
            if key not in inventory.INTENTIONALLY_GLOBAL:
                offenders.append(key)
    assert offenders == [], (
        f"unique constraints with neither a tenant column, a tenant-owned "
        f"parent, nor a recorded justification: {offenders}")


# Columns that are themselves tenant-scoped, so a constraint including one is
# transitively safe -- "one X per Y" where Y belongs to exactly one tenant.
_TENANT_OWNED_PARENTS = {
    "task", "memo", "minute", "circular", "appraisal", "item", "user",
    "owner", "employee", "device", "recipient", "comment", "note_round",
    "step", "transfer", "disposal", "leave_request", "cycle", "policy",
    "template", "attachment", "note", "subtask", "group", "broadcast",
    "assignment", "leave_type", "department", "shift", "competency",
    "depends_on",
}


def test_every_intentional_global_carries_its_reason():
    for key, reason in inventory.INTENTIONALLY_GLOBAL.items():
        label, name = key.split(":")
        model = apps.get_model(*label.split("."))
        names = {c.name for c in model._meta.constraints}
        assert name in names, f"{key} does not exist"
        assert len(reason) > 80, f"{key} has no real justification recorded"


def test_every_phase_b_replacement_constraint_exists_and_names_the_tenant():
    missing, unscoped = [], []
    for old_field, name in sorted(inventory.PHASE_B_UNIQUE_REWRITES.items()):
        label = old_field.rsplit(".", 1)[0]
        model = apps.get_model(*label.split("."))
        constraint = next((c for c in model._meta.constraints if c.name == name),
                          None)
        if constraint is None:
            missing.append(f"{old_field} -> {name}")
            continue
        fields = set(getattr(constraint, "fields", ()) or ())
        exprs = str(getattr(constraint, "expressions", ""))
        if "organization" not in fields and "organization" not in exprs:
            unscoped.append(name)
    assert missing == [], f"missing: {missing}"
    assert unscoped == [], f"constraints missing `organization`: {unscoped}"


def test_the_outstanding_document_numbers_are_safe_because_the_prefix_differs(db):
    """Why Phase B's global uniques are not an active defect.

    Every outstanding entry is a document-number column. They are still
    globally unique, but tenancy.numbering gives each tenant its own prefix, so
    two tenants cannot mint the same string in the first place.
    """
    from tenancy import numbering
    from tenancy.models import Organization

    nif = Organization.objects.get(slug="nif")
    other = Organization(slug="other", document_prefix="OTH",
                          legacy_number_formats=False)

    for kind in ("TSK", "MIN", "CIR", "INV", "TRF", "DSP", "LV"):
        mine = numbering.format_number(kind, year=2083, value=1, organization=nif)
        theirs = numbering.format_number(kind, year=2083, value=1,
                                         organization=other)
        assert mine != theirs, f"{kind} collides between tenants: {mine}"


def test_the_counts_match_the_phase_report():
    """Pins the numbers the S1/S2 reports quote, so a drift is visible."""
    summary = inventory.summary()
    assert summary["phase_a"] == 28
    assert summary["phase_b"] == 55
    assert summary["phase_c"] == 23
    assert summary["tenant_scoped_total"] == 106
    assert summary["phase_a_complete"] is True
    assert summary["phase_a_unique_rewrites_applied"] == 24
    assert summary["phase_b_unique_rewrites_applied"] == 13
    assert summary["phase_b_complete"] is True
    # Nothing outstanding; one field is globally unique on purpose.
    assert summary["global_unique_fields_outstanding"] == 0
    # 1 at Phase S3 (ADMS serial); 2 since the legacy device integration
    # pinned each pulled terminal's own serial platform-wide.
    assert summary["intentionally_global"] == 2


def test_the_tenant_scoped_total_equals_the_projects_business_models():
    business = {label for label in _all_labels()
                if label not in inventory.PLATFORM_GLOBAL}
    assert len(business) == 106
    assert business == inventory.TENANT_SCOPED
