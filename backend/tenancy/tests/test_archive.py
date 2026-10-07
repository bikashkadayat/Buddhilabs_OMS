"""Phase S6.5 Parts 2 and 3: a workspace closed, preserved, and reopened.

THE ONE ASSERTION THAT MATTERS MOST is that an archive changes nothing except
who may get in. Everything else here supports it: the counts are unchanged,
the ids are unchanged, the numbering continues rather than restarting, the
subscription is still whatever it was, and the audit trail still has every
entry it had. A restore is only a promise worth making if that is all true.
"""
import datetime

import pytest

from tenancy import archive, console, lifecycle
from tenancy.context import tenant_context
from tenancy.exceptions import TenancyError
from tenancy.models import Organization

pytestmark = pytest.mark.django_db

S = Organization.Status


@pytest.fixture
def half_built(db):
    """An Organization still in PROVISIONING.

    Created directly rather than through `provision_organization`, which
    finishes in TRIAL by design -- and PROVISIONING is deliberately not
    re-enterable, so there is no way back into it.
    """
    from tenancy.context import no_tenant

    with no_tenant():
        return Organization.objects.create(
            name="Half Built", slug="halfbuilt", document_prefix="HALF",
            email="nobody@halfbuilt.test")


def _populate(organization):
    """Give the tenant real transactional data, not just configuration.

    Archiving an empty workspace proves nothing: the question is whether a
    tenant with leave records, tasks, documents and their numbering comes
    back identical.
    """
    from django.contrib.auth import get_user_model

    from leaves.models import Department, Leave
    from memos.models import Memo
    from memos.services import generate_memo_number
    from tasks.models import Task
    from tasks.services import generate_task_number

    User = get_user_model()
    with tenant_context(organization):
        department = Department.objects.get(code="ICT")
        user = User(username=f"staff-{organization.slug}",
                    email=f"staff@{organization.slug}.test", role="maker",
                    employment_type=User.EmploymentType.PERMANENT,
                    department_ref=department, department=department.name,
                    date_of_joining=datetime.date(2020, 1, 1),
                    organization=organization)
        user.set_password("x-Archive-1")
        user.save()

        leave = Leave.objects.create(
            user=user, leave_type="annual", reason="Family",
            start_date=datetime.date(2026, 7, 6),
            end_date=datetime.date(2026, 7, 7))
        task = Task.objects.create(title="Something", created_by=user,
                                   task_number=generate_task_number(),
                                   due_date=datetime.date(2026, 8, 1))
        memo = Memo.objects.create(subject="Notice", created_by=user,
                                   memo_number=generate_memo_number(
                                       "administrative"))
        return {"user": user, "leave": leave, "task": task, "memo": memo}


def _census(organization):
    """Row counts for every tenant table. The archive must not move one."""
    from django.apps import apps

    from tenancy.inventory import TENANT_SCOPED

    counts = {}
    with tenant_context(organization):
        for label in sorted(TENANT_SCOPED):
            app_label, model_name = label.split(".")
            model = apps.get_model(app_label, model_name)
            counts[label] = model.objects.count()
    return counts


# --- archiving ----------------------------------------------------------
def test_archiving_locks_the_workspace_and_records_why(org, platform_user):
    before = org.status
    console.archive_organization(platform_user, org, reason="Customer closed")
    org.refresh_from_db()

    assert org.status == S.ARCHIVED
    assert org.is_admitted is False, "an archived tenant must not be admitted"
    assert org.archived_at is not None
    assert org.archived_reason == "Customer closed"
    assert org.status_before_archive == before


def test_an_archive_needs_a_reason(org, platform_user):
    """It is the action a customer rings up about months later."""
    with pytest.raises(TenancyError):
        console.archive_organization(platform_user, org, reason="   ")


def test_a_tenant_cannot_be_archived_twice(org, platform_user):
    console.archive_organization(platform_user, org, reason="Closed")
    with pytest.raises(archive.AlreadyArchived):
        console.archive_organization(platform_user, org, reason="Closed again")


def test_a_half_provisioned_workspace_is_cancelled_not_archived(half_built,
                                                                 platform_user):
    """There is nothing to preserve, and the operator wants `cancel`."""
    assert half_built.status == S.PROVISIONING
    with pytest.raises(TenancyError) as caught:
        console.archive_organization(platform_user, half_built,
                                     reason="Never used")
    assert "cancel" in str(caught.value).lower()


def test_archiving_deletes_nothing(org, platform_user):
    """Part 2, and the whole basis of the restore promise."""
    _populate(org)
    before = _census(org)

    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    assert _census(org) == before, "the archive moved rows"
    assert sum(before.values()) > 0, "this test needs data to be meaningful"


def test_archiving_preserves_the_subscription(org, platform_user):
    """Part 2 names it explicitly, and the reason is practical: ending the
    subscription would destroy what a restore needs to put them back."""
    subscription = org.subscription
    status, period_end = subscription.status, subscription.current_period_end

    console.archive_organization(platform_user, org, reason="Closed")

    subscription.refresh_from_db()
    assert subscription.status == status
    assert subscription.current_period_end == period_end


def test_archiving_preserves_the_audit_trail(org, platform_user):
    from tenancy.models import PlatformAuditLog

    before = PlatformAuditLog.objects.filter(organization=org).count()
    console.archive_organization(platform_user, org, reason="Closed")
    after = PlatformAuditLog.objects.filter(organization=org).count()
    assert after == before + 1, "the archive itself is the only new entry"


def test_the_archive_is_audited_with_what_it_is_holding(org, platform_user):
    from tenancy.models import PlatformAuditLog

    console.archive_organization(platform_user, org, reason="Customer closed")
    entry = PlatformAuditLog.objects.filter(
        organization=org,
        action=PlatformAuditLog.Action.TENANT_ARCHIVED).first()
    assert entry is not None
    assert entry.actor_email == platform_user.email
    assert "Customer closed" in entry.note
    assert "Nothing was deleted" in entry.note
    assert entry.changes["preserved"]["subscription_status"]


# --- an archived tenant generates no activity ---------------------------
def test_an_archived_tenants_requests_are_refused(org, platform_user, client,
                                                   settings):
    """Part 2: "cannot sign in", "cannot generate activity"."""
    from django.contrib.auth import get_user_model

    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    User = get_user_model()
    with tenant_context(org):
        user = User.objects.create_user(
            username="locked-out", email="locked@abc.test",
            password="x-Locked-1", organization=org)

    console.archive_organization(platform_user, org, reason="Closed")

    response = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": "x-Locked-1"},
        content_type="application/json",
        HTTP_HOST=f"{org.slug}.platform.test")
    assert response.status_code in (400, 402, 403), response.content[:200]


def test_a_renewal_cannot_reopen_an_archive(org, platform_user):
    """The subscription has no opinion about archiving and must not end one."""
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    lifecycle.apply_derived(org, S.ACTIVE)
    assert org.status == S.ARCHIVED


def test_clearing_the_override_still_does_not_reopen_an_archive(org,
                                                                 platform_user):
    """The belt-and-braces half.

    Archiving pins the status, so the pin alone would normally stop a
    derived move -- but `clear_override` exists, and a tenant whose pin was
    cleared while archived would have been reopened by the next renewal,
    silently, mid-preservation.
    """
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()
    lifecycle.clear_override(org)
    org.refresh_from_db()

    lifecycle.apply_derived(org, S.ACTIVE)
    assert org.status == S.ARCHIVED


@pytest.mark.parametrize("call", [
    pytest.param(lambda actor, org: console.activate(actor, org), id="activate"),
    pytest.param(lambda actor, org: console.suspend(actor, org, reason="x"),
                 id="suspend"),
    pytest.param(lambda actor, org: console.cancel(actor, org, reason="x"),
                 id="cancel"),
    pytest.param(lambda actor, org: console.extend(actor, org, months=1),
                 id="extend"),
    pytest.param(lambda actor, org: console.set_branding(actor, org,
                                                          display_name="X"),
                 id="set_branding"),
    pytest.param(lambda actor, org: console.update_organization(actor, org,
                                                                 name="X"),
                 id="update"),
    pytest.param(lambda actor, org: console.repair_configuration(actor, org),
                 id="repair"),
])
def test_the_console_refuses_to_change_an_archived_tenant(org, platform_user,
                                                           call):
    """`activate` is the one that mattered.

    It clears the status pin and moves the subscription, so an operator
    reaching for the button they use every day would have left a tenant whose
    billing was ACTIVE, whose workspace was ARCHIVED, and whose pin -- the
    thing stopping the next renewal reopening it -- was gone.
    """
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    with pytest.raises(TenancyError) as caught:
        call(platform_user, org)
    assert "archived" in str(caught.value).lower()
    org.refresh_from_db()
    assert org.status == S.ARCHIVED


def test_an_archived_tenant_can_still_be_exported(org, platform_user):
    """Deliberately NOT refused: handing a departed customer their data is
    the main reason to archive rather than cancel, and it usually happens
    weeks later."""
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    export_row = console.create_export(platform_user, org)
    assert export_row.status == "ready", export_row.error
    assert export_row.row_count > 0


def test_an_archived_tenant_can_still_be_inspected(org, platform_user):
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    health = console.tenant_health(platform_user, org)
    assert health["archive_state"]["is_archived"] is True
    assert health["archive_state"]["can_restore"] is True
    assert health["is_admitted"] is False
    assert console.organization_usage(platform_user, org)["slug"] == org.slug


# --- restoring ----------------------------------------------------------
def test_a_restore_returns_the_tenant_to_where_it_was(org, platform_user):
    before = org.status
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    console.restore_organization(platform_user, org)
    org.refresh_from_db()

    assert org.status == before
    assert org.is_admitted is True
    assert org.archived_at is None
    assert org.archived_reason == ""
    assert org.status_before_archive == ""


def test_a_suspended_tenant_is_restored_suspended_unless_told_otherwise(
        org, platform_user):
    """Faithful by default. An archive is not a way to launder a suspension."""
    console.suspend(platform_user, org, reason="Non-payment")
    org.refresh_from_db()
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    console.restore_organization(platform_user, org)
    org.refresh_from_db()
    assert org.status == S.SUSPENDED
    assert org.is_admitted is False


def test_an_operator_may_restore_somewhere_else_on_purpose(org, platform_user):
    """The real case: archived while suspended, dispute since settled."""
    console.suspend(platform_user, org, reason="Non-payment")
    org.refresh_from_db()
    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()

    console.restore_organization(platform_user, org, to_status=S.ACTIVE,
                                 note="Dispute settled")
    org.refresh_from_db()
    assert org.status == S.ACTIVE


def test_restoring_something_that_is_not_archived_is_refused(org,
                                                              platform_user):
    with pytest.raises(archive.NotArchived):
        console.restore_organization(platform_user, org)


def test_a_restore_is_audited_and_names_the_archive_it_undid(org,
                                                              platform_user):
    from tenancy.models import PlatformAuditLog

    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()
    console.restore_organization(platform_user, org, note="Customer returned")

    entry = PlatformAuditLog.objects.filter(
        organization=org,
        action=PlatformAuditLog.Action.TENANT_RESTORED).first()
    assert entry is not None
    assert entry.changes["archived_at"], "the restore does not say what it undid"
    assert entry.actor_email == platform_user.email


# --- Part 3: preserve ids, references, numbering, documents -------------
def test_a_restore_preserves_every_id_and_relationship(org, platform_user):
    created = _populate(org)
    before = _census(org)
    ids = {key: str(value.pk) for key, value in created.items()}
    numbers = {"task": created["task"].task_number,
               "memo": created["memo"].memo_number}

    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()
    console.restore_organization(platform_user, org)
    org.refresh_from_db()

    assert _census(org) == before

    from leaves.models import Leave
    from memos.models import Memo
    from tasks.models import Task

    with tenant_context(org):
        task = Task.objects.get(pk=ids["task"])
        memo = Memo.objects.get(pk=ids["memo"])
        leave = Leave.objects.get(pk=ids["leave"])
        assert task.task_number == numbers["task"]
        assert memo.memo_number == numbers["memo"]
        # The relationship, not just the row: the leave still points at the
        # same user, and that user is still in the same department.
        assert str(leave.user_id) == ids["user"]
        assert leave.user.department_ref is not None


def test_numbering_continues_after_a_restore_rather_than_restarting(
        org, platform_user):
    """A restarted sequence would issue a document number that already exists
    -- and under the per-tenant unique constraint, the second one simply
    fails to save."""
    from memos.services import generate_memo_number

    _populate(org)
    with tenant_context(org):
        before = generate_memo_number("administrative")

    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()
    console.restore_organization(platform_user, org)
    org.refresh_from_db()

    with tenant_context(org):
        after = generate_memo_number("administrative")

    assert after != before
    assert int(after.rsplit("-", 1)[-1]) == int(before.rsplit("-", 1)[-1]) + 1


def test_the_tenants_own_api_works_again_after_a_restore(org, platform_user):
    """"After restore, tenant operates normally" -- asserted by using it."""
    from leaves.models import LeaveType

    console.archive_organization(platform_user, org, reason="Closed")
    org.refresh_from_db()
    console.restore_organization(platform_user, org)
    org.refresh_from_db()

    with tenant_context(org):
        assert LeaveType.objects.filter(code="ANNUAL").exists()
    health = console.tenant_health(platform_user, org)
    assert health["verdict"] == "Tenant Ready"
    assert health["archive_state"]["is_archived"] is False


# --- the lifecycle table itself -----------------------------------------
def test_archived_is_reachable_from_every_live_state_and_returns():
    allowed = lifecycle.allowed_transitions()
    for state in (S.TRIAL, S.ACTIVE, S.GRACE, S.SUSPENDED, S.CANCELLED):
        assert S.ARCHIVED in allowed[state], state
        assert state in allowed[S.ARCHIVED], state
    # A workspace is built once, and an unfinished one has nothing to keep.
    assert S.ARCHIVED not in allowed[S.PROVISIONING]
    assert S.PROVISIONING not in allowed[S.ARCHIVED]
