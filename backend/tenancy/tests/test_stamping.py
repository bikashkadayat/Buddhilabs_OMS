"""The backward-compatibility layer, and the hole it deliberately leaves.

Phase S2 made `organization` NOT NULL on 27 tables while ~every existing caller
still creates those rows without one. `tenancy.stamping` fills the gap with a
`pre_save` receiver. These tests pin what it covers, what it does NOT, and the
guard that stops the uncovered case being reintroduced.
"""
import ast
import pathlib

import pytest
from django.apps import apps
from django.test import override_settings

from tenancy import inventory, stamping
from tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

BACKEND = pathlib.Path(__file__).resolve().parents[2]


def test_a_phase_a_row_saved_with_no_organization_is_stamped(nif):
    """Part 7: existing code keeps working, unmodified."""
    from leaves.models import Department

    dept = Department.objects.create(name="Compat", code="COMPAT")
    assert dept.organization_id == nif.pk


def test_stamping_never_overrides_an_explicit_organization(nif, monthly_plan):
    from leaves.models import Department

    from tenancy import services

    other = services.provision_organization(
        name="Other", slug="other", document_prefix="OTH",
        email="o@o.test", plan=monthly_plan)

    # THE DECISION, OBSERVED WITH THE WRONG TENANT IN CONTEXT.
    #
    # The claim is that an explicitly named organization survives the
    # stamping shim -- so the context has to be something else at the moment
    # the receiver runs. The receiver is called directly rather than through
    # a save, because under row-level security the database would refuse to
    # write another tenant's row from a NIF-bound connection, and that
    # refusal would mask the thing being tested.
    dept = Department(name="Explicit", code="EXP", organization=other)
    with tenant_context(nif):
        stamping.stamp_organization(Department, dept)
        assert dept.organization_id == other.pk, (
            "the shim overrode an explicitly named organization")

    # It then persists as its owner, unchanged.
    with tenant_context(other):
        dept.save()
        dept.refresh_from_db()
        assert dept.organization_id == other.pk


def test_every_tenant_scoped_model_except_user_has_a_receiver():
    connected = set(stamping.connect())
    expected = inventory.TENANT_SCOPED - {"users.User"}
    assert connected == expected, (
        f"missing receivers: {sorted(expected - connected)}; "
        f"unexpected: {sorted(connected - expected)}")


def test_user_is_excluded_because_it_stamps_in_save(nif):
    """It also has to skip platform accounts and cope with deferred fields."""
    assert "users.User" not in stamping.connect()
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="stamped", email="stamped@nif.test", password="x-Stamp-1")
    assert user.organization_id == nif.pk


# --- the hole, and the guard on it -------------------------------------
def test_no_phase_a_model_is_written_through_bulk_create():
    """`bulk_create` does not emit pre_save, so stamping never runs for it.

    This is not hypothetical: two real call sites in `tasks` wrote
    TaskTemplateItem through bulk_create and failed with
    `NOT NULL constraint failed: tasks_tasktemplateitem.organization_id` the
    first time the suite ran after the column landed. Both now pass
    `organization_id` from the parent template.

    So the rule is: a Phase A or Phase B model may be bulk-created ONLY in a
    function that also either names `organization` explicitly or routes the
    rows through `tenancy.stamping.stamp_all`, which runs the same derivation
    the signal does. This test enforces it across the whole backend.
    """
    phase_a_names = {label.split(".")[1]
                     for label in inventory.TENANT_SCOPED}
    offenders = []

    for path in BACKEND.rglob("*.py"):
        if "__pycache__" in str(path) or "/migrations/" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:                    # pragma: no cover
            continue

        # Map each bulk_create call to its enclosing function, because the rows
        # are often built a few lines above the call. Checking only the call
        # expression would flag `bulk_create(rows)` even when every row in
        # `rows` names an organization.
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Module)):
                continue
            scope_source = ast.unparse(scope)
            for node in ast.walk(scope):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "bulk_create"):
                    continue
                call_source = ast.unparse(node)
                model = call_source.split(".objects")[0].split()[-1].lstrip("(")
                if model not in phase_a_names:
                    continue
                if ("organization" in scope_source
                        or "stamp_all" in scope_source):
                    continue
                offenders.append(
                    f"{path.relative_to(BACKEND)}:{node.lineno} "
                    f"bulk_create({model}) without organization")

    assert sorted(set(offenders)) == [], (
        "Phase A/B models bulk-created without an organization and without "
        "stamp_all() (pre_save, and "
        f"therefore stamping, does not run): {sorted(set(offenders))}")


@override_settings(TENANCY_DEFAULT_SLUG="")
def test_stamping_declines_to_guess_between_two_tenants(nif, monthly_plan):
    """Fail closed. The database refuses rather than the shim guessing.

    TENANCY_DEFAULT_SLUG IS PINNED EMPTY (Phase S6), because that is the
    configuration this test is actually about. The shim's "refuse to guess"
    only applies when no default tenant is NAMED: with
    TENANCY_DEFAULT_SLUG set, there IS a safe answer and resolving it is
    correct, not a guess. Leaving it to the deployment made this test pass or
    fail on an environment variable.

    ``django.db.Error`` rather than ``IntegrityError``, because under
    PostgreSQL row-level security the refusal arrives EARLIER and from a
    different place: a row with no organization cannot satisfy the policy's
    WITH CHECK, so the insert is rejected as InsufficientPrivilege before the
    NOT NULL constraint is ever evaluated. Both are the same outcome -- the
    row does not exist -- and the test asserts the outcome.
    """
    from django.db import Error, transaction

    from leaves.models import Department
    from tenancy import services

    services.provision_organization(
        name="Second", slug="second", document_prefix="SEC",
        email="s@s.test", plan=monthly_plan)

    with pytest.raises(Error), transaction.atomic():
        Department.objects.create(name="Ambiguous", code="AMB")

    assert not Department.all_tenants.filter(code="AMB").exists()


def test_an_explicit_tenant_context_still_works_with_two_tenants(nif, monthly_plan):
    from leaves.models import Department
    from tenancy import services

    other = services.provision_organization(
        name="Second", slug="second", document_prefix="SEC",
        email="s@s.test", plan=monthly_plan)

    with tenant_context(other):
        dept = Department.objects.create(name="Scoped", code="SCOPED")
    assert dept.organization_id == other.pk


# --- the Phase A schema, as the database actually built it --------------
def test_the_organization_column_is_indexed_everywhere():
    """Every tenant-scoped read will filter on it; an unindexed FK would hurt."""
    unindexed = []
    for label in sorted(inventory.TENANT_SCOPED):
        field = apps.get_model(*label.split("."))._meta.get_field("organization")
        if not field.db_index:
            unindexed.append(label)
    assert unindexed == [], unindexed
