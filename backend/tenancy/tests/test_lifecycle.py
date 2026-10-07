"""Part 6: the organization lifecycle, enforced.

    PROVISIONING -> TRIAL -> ACTIVE -> GRACE -> SUSPENDED -> CANCELLED

Two things are under test, and the second is the one that matters:

  1. the legal moves are the declared ones, and an illegal one is REFUSED;
  2. ``Organization.status`` cannot be changed by assignment at all --
     because that column decides whether anybody may use the workspace, and
     it is reachable from a serializer, the Django admin and a shell.
"""
import pytest

from tenancy import console, lifecycle, services
from tenancy.lifecycle import IllegalOrganizationTransition
from tenancy.models import Organization, Subscription

pytestmark = pytest.mark.django_db

O = Organization.Status
S = Subscription.Status


# --- the declared machine ----------------------------------------------
def test_every_status_appears_in_the_transition_table():
    """A status with no entry is a state nothing can ever leave."""
    table = lifecycle.allowed_transitions()
    for status in O:
        assert status in table, f"{status} has no declared successors"


def test_provisioning_is_never_re_enterable():
    """A workspace is built once.

    Re-entering PROVISIONING would mean a live tenant with real data is
    treated as half-built -- every user locked out, with no record of why
    beyond a status column.
    """
    table = lifecycle.allowed_transitions()
    for status, successors in table.items():
        assert O.PROVISIONING not in successors, (
            f"{status} may re-enter PROVISIONING")


def test_cancelled_is_not_terminal():
    """A customer who comes back is reactivated, not recreated.

    Recreating would mean a second Organization row and the loss of their
    history -- and refusing it in code would only mean somebody did it in a
    shell.
    """
    assert lifecycle.is_legal(O.CANCELLED, O.ACTIVE)
    assert lifecycle.is_legal(O.CANCELLED, O.TRIAL)


def test_the_happy_path_is_legal_end_to_end():
    chain = [O.PROVISIONING, O.TRIAL, O.ACTIVE, O.GRACE, O.SUSPENDED,
             O.CANCELLED]
    for before, after in zip(chain, chain[1:]):
        assert lifecycle.is_legal(before, after), f"{before} -> {after}"


def test_a_no_op_move_is_always_legal():
    for status in O:
        assert lifecycle.is_legal(status, status)


@pytest.mark.parametrize("before,after", [
    (O.ACTIVE, O.TRIAL),        # a paying customer does not go back on trial
    (O.CANCELLED, O.GRACE),     # grace is for a lapsed payment, not a leaver
    (O.CANCELLED, O.SUSPENDED),  # already gone; suspending adds nothing
    (O.TRIAL, O.PROVISIONING),
])
def test_an_illegal_move_is_refused(before, after):
    assert not lifecycle.is_legal(before, after)
    with pytest.raises(IllegalOrganizationTransition):
        lifecycle.assert_legal(before, after)


# --- enforcement at the model ------------------------------------------
def test_direct_assignment_to_status_is_refused(org):
    """The guard that makes the lifecycle mean something.

    Mirrors the Subscription guard, and for the same reason: a bare
    ``org.status = "active"; org.save()`` would let a tenant in with no
    legality check and no record of who did it.
    """
    fetched = Organization.objects.get(pk=org.pk)
    fetched.status = O.SUSPENDED
    with pytest.raises(IllegalOrganizationTransition) as exc:
        fetched.save()
    assert "transition_organization" in str(exc.value)

    fetched.refresh_from_db()
    assert fetched.status == O.TRIAL


def test_saving_without_touching_status_is_unaffected(org):
    """The guard must not make an ordinary edit impossible."""
    fetched = Organization.objects.get(pk=org.pk)
    fetched.phone = "+977-1-0000000"
    fetched.save()
    fetched.refresh_from_db()
    assert fetched.phone == "+977-1-0000000"


def test_the_lifecycle_mover_is_permitted(org):
    lifecycle.transition_organization(org, O.SUSPENDED, reason="testing")
    org.refresh_from_db()
    assert org.status == O.SUSPENDED


def test_an_illegal_move_through_the_mover_writes_nothing(org):
    with pytest.raises(IllegalOrganizationTransition):
        lifecycle.transition_organization(org, O.PROVISIONING)
    org.refresh_from_db()
    assert org.status == O.TRIAL


def test_the_guard_re_arms_after_a_permitted_move(org):
    """One authorised save must not leave the instance permanently unguarded."""
    lifecycle.transition_organization(org, O.ACTIVE)
    org.status = O.CANCELLED
    with pytest.raises(IllegalOrganizationTransition):
        org.save()


# --- the two status machines ------------------------------------------
def test_a_lapsed_subscription_shows_the_tenant_in_grace(org):
    """GRACE on the organization is new in Phase S6.

    Before it, a tenant inside its grace period displayed as ACTIVE -- which
    is the one state an operator most needs to see, because it is the
    difference between "paying" and "phone them today".
    """
    services.transition(org.subscription, S.GRACE)
    org.refresh_from_db()
    assert org.status == O.GRACE


def test_a_tenant_in_grace_is_still_admitted(org):
    """That is what a grace period IS. Showing it must not lock anybody out."""
    services.transition(org.subscription, S.GRACE)
    org.refresh_from_db()
    assert org.is_admitted is True


def test_a_suspended_tenant_is_not_admitted(org):
    services.transition(org.subscription, S.SUSPENDED)
    org.refresh_from_db()
    assert org.is_admitted is False


def test_a_provisioning_tenant_is_not_admitted(db, monthly_plan):
    """The workspace is not finished being built, whatever the subscription says."""
    from .conftest import TODAY

    org = services.provision_organization(
        name="Half Built", slug="halfbuilt", document_prefix="HALF",
        email="a@half.test", plan=monthly_plan, today=TODAY,
        status=O.PROVISIONING, bootstrap=False)
    assert org.status == O.PROVISIONING
    assert org.subscription.status == S.TRIAL
    assert org.is_admitted is False


def test_the_mirror_never_moves_a_provisioning_tenant(db, monthly_plan):
    from .conftest import TODAY

    org = services.provision_organization(
        name="Still Building", slug="stillbuilding", document_prefix="STIL",
        email="a@still.test", plan=monthly_plan, today=TODAY,
        status=O.PROVISIONING, bootstrap=False)
    services.transition(org.subscription, S.ACTIVE)
    org.refresh_from_db()
    assert org.status == O.PROVISIONING, (
        "a subscription must not open a workspace that is not built")


def test_every_subscription_status_maps_to_a_reachable_organization_status():
    """The two machines must not be able to disagree.

    An illegal DERIVED move means the subscription map and this lifecycle
    contradict each other, which is a platform bug that would surface as an
    exception in the middle of a customer's renewal. Checked exhaustively
    here instead.
    """
    from tenancy.services import ALLOWED_TRANSITIONS, ORG_STATUS_FOR

    for from_sub, to_statuses in ALLOWED_TRANSITIONS.items():
        from_org = ORG_STATUS_FOR[from_sub]
        for to_sub in to_statuses:
            to_org = ORG_STATUS_FOR[to_sub]
            assert lifecycle.is_legal(from_org, to_org), (
                f"subscription {from_sub} -> {to_sub} implies organization "
                f"{from_org} -> {to_org}, which the lifecycle refuses")


# --- the operator override --------------------------------------------
def test_a_manual_suspension_survives_a_renewal(platform_user, org):
    """The whole reason Organization.status exists separately.

    If the operator suspended a customer for abuse, a payment clearing the
    next morning must not quietly re-open the workspace.
    """
    console.set_organization_status(platform_user, org, O.SUSPENDED,
                                    reason="Abuse report #41")
    org.refresh_from_db()
    assert org.status == O.SUSPENDED
    assert org.status_override is True

    # The subscription renews -- the customer pays, and the bank transfer
    # clears. ACTIVE is a legal successor of SUSPENDED, so nothing refuses
    # the derived move; it is the pin that refuses it.
    services.transition(org.subscription, S.ACTIVE)
    org.refresh_from_db()

    assert org.status == O.SUSPENDED, (
        "a payment must not undo an operator's suspension")
    assert org.is_admitted is False
    # The billing fact is still recorded truthfully: the console shows a
    # paid-up tenant that is suspended anyway, and the recorded reason.
    assert org.subscription_status == S.ACTIVE
    assert org.status_override_reason == "Abuse report #41"


def test_an_operator_activation_clears_the_pin(platform_user, org):
    """Otherwise the tenant is admitted now and re-suspended at the next sync."""
    console.set_organization_status(platform_user, org, O.SUSPENDED,
                                    reason="Billing dispute")
    console.activate(platform_user, org, note="Dispute settled")

    org.refresh_from_db()
    assert org.status_override is False
    assert org.status == O.ACTIVE
    assert org.is_admitted is True

    # And it stays that way through the next mirror refresh.
    services.sync_subscription_mirror(org.subscription)
    org.refresh_from_db()
    assert org.status == O.ACTIVE


def test_console_suspend_also_pins(platform_user, org):
    """`suspend()` is the normal path, and it must pin like the override does.

    Without this, the sticky behaviour would only apply to the rarely-used
    direct status override and not to the button an operator actually presses.
    """
    console.suspend(platform_user, org, reason="Non-payment, 3 reminders")
    org.refresh_from_db()
    assert org.status == O.SUSPENDED
    assert org.status_override is True

    services.transition(org.subscription, S.ACTIVE)
    org.refresh_from_db()
    assert org.status == O.SUSPENDED


def test_an_operator_can_finish_a_deliberately_open_provisioning(platform_user,
                                                                  db,
                                                                  monthly_plan):
    from .conftest import TODAY

    org = services.provision_organization(
        name="Import First", slug="importfirst", document_prefix="IMPF",
        email="a@imp.test", plan=monthly_plan, today=TODAY,
        status=O.PROVISIONING, bootstrap=False)
    assert org.is_admitted is False

    console.set_organization_status(platform_user, org, O.TRIAL,
                                    reason="Data import finished")
    org.refresh_from_db()
    assert org.status == O.TRIAL
    assert org.is_admitted is True
