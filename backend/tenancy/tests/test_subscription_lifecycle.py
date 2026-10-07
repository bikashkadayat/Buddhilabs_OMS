"""Part 5: the subscription engine.

The two properties that matter: every legal move works and records itself, and
no illegal or unaudited move is possible at all.
"""
import datetime

import pytest

from tenancy import services
from tenancy.exceptions import DirectStatusChangeForbidden, IllegalTransition
from tenancy.models import Organization, Subscription, SubscriptionEvent
from tenancy.periods import add_months

from .conftest import TODAY

pytestmark = pytest.mark.django_db

S = Subscription.Status
E = SubscriptionEvent.Event


# --- provisioning -------------------------------------------------------
def test_provisioning_creates_organization_settings_branding_and_subscription(org):
    assert org.status == Organization.Status.TRIAL
    assert org.settings is not None
    assert org.branding is not None
    sub = org.subscription
    assert sub.status == S.TRIAL
    assert sub.trial_start == TODAY
    assert sub.trial_end == TODAY + datetime.timedelta(days=14)
    # The creation event exists from birth, so the record is never silent.
    assert org.subscription_events.filter(event=E.CREATED).exists()


def test_provisioning_refuses_a_reserved_slug():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        services.provision_organization(
            name="Bad", slug="admin", document_prefix="BAD",
            email="a@b.test")


# --- the direct-assignment guard ---------------------------------------
def test_assigning_status_directly_is_refused(org):
    """Part 5: "Direct status modifications forbidden." Enforced, not documented."""
    sub = org.subscription
    sub.status = S.ACTIVE
    with pytest.raises(DirectStatusChangeForbidden):
        sub.save()


def test_direct_assignment_is_refused_even_with_update_fields(org):
    sub = Subscription.objects.get(pk=org.subscription.pk)
    sub.status = S.SUSPENDED
    with pytest.raises(DirectStatusChangeForbidden):
        sub.save(update_fields=["status"])


def test_saving_without_touching_status_still_works(org):
    """The guard must not make ordinary writes impossible."""
    sub = org.subscription
    sub.seats = 42
    sub.save()
    sub.refresh_from_db()
    assert sub.seats == 42
    assert sub.status == S.TRIAL


def test_the_guard_rearms_after_a_legal_transition(org):
    """One authorised move must not leave the instance permanently unlocked."""
    sub = services.transition(org.subscription, S.ACTIVE,
                               period_start=TODAY,
                               period_end=add_months(TODAY, 1))
    sub.status = S.CANCELLED
    with pytest.raises(DirectStatusChangeForbidden):
        sub.save()


# --- the transition matrix ---------------------------------------------
@pytest.mark.parametrize("to_status", [S.ACTIVE, S.EXPIRED, S.SUSPENDED,
                                        S.CANCELLED])
def test_legal_transitions_from_trial(org, to_status):
    sub = services.transition(org.subscription, to_status)
    assert sub.status == to_status


def test_illegal_transition_is_refused(org):
    """An illegal move is refused. TRIAL -> GRACE is no longer one of them.

    INVERTED IN PHASE S6, AND THE REASON IS A BUG THIS TEST WAS PROTECTING.

    This test used to assert that TRIAL -> GRACE is refused, on the reasoning
    that "a trial has not been paid for". Two other parts of the same phase
    assumed the opposite:

      * ``provision_organization`` sets ``grace_until = trial_end +
        plan.grace_days`` -- a grace window computed for a trial, which only
        means something if a trial can enter one;
      * ``advance_expired`` selects ``status__in=[ACTIVE, TRIAL]`` past its
        period end and moves it to GRACE.

    So the FIRST trial on the platform to expire would raise
    IllegalTransition inside the nightly sweep and abort it before it advanced
    any tenant at all. Nothing caught it because every
    ``advance_expired`` test moved the subscription to ACTIVE first -- see
    test_advance_expired_moves_an_expiring_trial_into_grace below, which is the
    test that was missing.

    The resolution keeps the grace window, because a prospect whose trial
    lapses while their bank transfer clears should not lose their workspace
    that morning. What is genuinely illegal is tested instead.
    """
    assert S.GRACE in services.ALLOWED_TRANSITIONS[S.TRIAL]

    # EXPIRED -> GRACE is the move that makes no sense: grace is the window
    # before expiry, not after it.
    services.transition(org.subscription, S.EXPIRED)
    sub = Subscription.objects.get(pk=org.subscription.pk)
    with pytest.raises(IllegalTransition):
        services.transition(sub, S.GRACE)


def test_advance_expired_moves_an_expiring_trial_into_grace(org):
    """The sweep's TRIAL branch, which no test had ever run.

    `org` is provisioned on a trial, so this is the ordinary path for every
    customer the platform acquires -- and it was the one path the scheduled
    job could not survive.
    """
    org.refresh_from_db()
    assert org.subscription.status == S.TRIAL
    trial_end = org.subscription.current_period_end

    counts = services.advance_expired(today=trial_end + datetime.timedelta(days=1))
    assert counts == {"entered_grace": 1, "suspended": 0}

    org.refresh_from_db()
    assert org.subscription.status == S.GRACE
    # Still admitted: that is what a grace period is for.
    assert org.is_admitted is True

    # ...and the grace window lapsing suspends them, as for any other tenant.
    counts = services.advance_expired(
        today=org.subscription.grace_until + datetime.timedelta(days=1))
    assert counts == {"entered_grace": 0, "suspended": 1}
    org.refresh_from_db()
    assert org.is_admitted is False


def test_the_sweep_survives_a_mixed_population(org, nif, monthly_plan):
    """One tenant that cannot advance must not stop the others.

    The crash this replaces was not "one tenant got stuck": `advance_expired`
    iterates, so the exception aborted the whole sweep and every tenant after
    the first expiring trial went unprocessed.
    """
    second = services.provision_organization(
        name="Also Trialling", slug="alsotrial", document_prefix="ALSO",
        email="a@also.test", plan=monthly_plan, today=TODAY)
    services.transition(second.subscription, S.ACTIVE,
                        period_start=datetime.date(2026, 1, 1),
                        period_end=datetime.date(2026, 2, 1))

    counts = services.advance_expired(today=datetime.date(2026, 7, 2))
    # Both the trial and the paid tenant moved, in one run.
    assert counts["entered_grace"] == 2


def test_verified_terminal_states_cannot_move_except_by_win_back(org):
    services.transition(org.subscription, S.CANCELLED, note="left")
    sub = Subscription.objects.get(pk=org.subscription.pk)
    # Reactivation is allowed on purpose -- a returning customer is a real case.
    sub = services.transition(sub, S.ACTIVE, period_start=TODAY,
                               period_end=add_months(TODAY, 1))
    assert sub.status == S.ACTIVE
    assert sub.events.filter(event=E.REACTIVATED).exists()


def test_force_bypasses_legality_but_still_records_the_event(org):
    sub = services.transition(org.subscription, S.GRACE, force=True,
                               note="data repair")
    assert sub.status == S.GRACE
    event = sub.events.order_by("-effective_at").first()
    assert event.from_status == S.TRIAL and event.to_status == S.GRACE
    assert event.note == "data repair"


# --- every transition writes an event and syncs the mirror -------------
def test_every_transition_writes_an_event_and_syncs_the_mirror(org):
    before = org.subscription_events.count()
    sub = services.transition(org.subscription, S.ACTIVE, period_start=TODAY,
                               period_end=add_months(TODAY, 12))
    assert org.subscription_events.count() == before + 1

    org.refresh_from_db()
    assert org.subscription_status == S.ACTIVE
    assert org.subscription_start == TODAY
    assert org.subscription_expiry == add_months(TODAY, 12)
    assert services.mirror_drift(org) is None


def test_suspension_drives_the_organization_status_and_closes_the_door(org):
    services.transition(org.subscription, S.SUSPENDED, note="non-payment")
    org.refresh_from_db()
    assert org.status == Organization.Status.SUSPENDED
    assert org.is_admitted is False


def test_cancellation_deletes_no_data(nif, org, django_user_model):
    """Suspension and cancellation are gates, never deletions."""
    from leaves.models import Department

    from tenancy.context import tenant_context

    # Explicit tenant: two organizations exist here, so Phase S2's
    # compatibility shim refuses to guess which one owns a new Department.
    # The user belongs to the CANCELLED tenant, not to NIF -- the claim is
    # that this customer's own data survives their cancellation.
    with tenant_context(org):
        dept = Department.objects.create(name="HR", code="HR-ABC")
        member = django_user_model.objects.create_user(
            username="abc-staff", email="staff@abc.test",
            password="x-Cancel-1")
    services.transition(org.subscription, S.CANCELLED, note="left")
    org.refresh_from_db()
    assert org.status == Organization.Status.CANCELLED

    # Read AS the cancelled tenant. Cancellation is a gate, not a delete, so
    # the rows are all still there -- but a connection bound to a different
    # tenant cannot see them under row-level security, which would make this
    # assertion pass or fail on which tenant the reader happened to be.
    with tenant_context(org):
        assert Department.objects.filter(pk=dept.pk).exists()
        assert django_user_model.objects.filter(pk=member.pk).exists()


def test_grace_period_is_stored_so_the_nightly_job_can_index_it(org):
    sub = services.transition(org.subscription, S.ACTIVE, period_start=TODAY,
                               period_end=add_months(TODAY, 1))
    expected = add_months(TODAY, 1) + datetime.timedelta(days=sub.plan.grace_days)
    assert sub.grace_until == expected


# --- period arithmetic --------------------------------------------------
def test_extending_an_active_period_appends_rather_than_restarting(org):
    """Paying early must never cost a customer days they already hold."""
    sub = services.transition(org.subscription, S.ACTIVE, period_start=TODAY,
                               period_end=add_months(TODAY, 1))
    start, end = services.extend_period(sub, sub.plan, today=TODAY)
    assert start == add_months(TODAY, 1)
    assert end == add_months(TODAY, 2)


def test_extending_a_lapsed_period_starts_today(org):
    sub = services.transition(org.subscription, S.SUSPENDED)
    start, end = services.extend_period(sub, sub.plan, today=TODAY)
    assert start == TODAY
    assert end == add_months(TODAY, 1)


@pytest.mark.parametrize("start,months,expected", [
    (datetime.date(2026, 1, 31), 1, datetime.date(2026, 2, 28)),
    (datetime.date(2028, 1, 31), 1, datetime.date(2028, 2, 29)),   # leap year
    (datetime.date(2026, 3, 31), 1, datetime.date(2026, 4, 30)),
    (datetime.date(2026, 1, 15), 12, datetime.date(2027, 1, 15)),
    (datetime.date(2026, 12, 15), 3, datetime.date(2027, 3, 15)),
])
def test_month_addition_clamps_to_the_shorter_month(start, months, expected):
    assert add_months(start, months) == expected


# --- the scheduled sweep ------------------------------------------------
def test_advance_expired_moves_active_to_grace_then_to_suspended(org):
    services.transition(org.subscription, S.ACTIVE,
                        period_start=datetime.date(2026, 1, 1),
                        period_end=datetime.date(2026, 2, 1))

    counts = services.advance_expired(today=datetime.date(2026, 2, 2))
    assert counts == {"entered_grace": 1, "suspended": 0}
    org.refresh_from_db()
    assert org.subscription.status == S.GRACE
    assert org.is_admitted is True          # grace still admits

    counts = services.advance_expired(today=datetime.date(2026, 3, 1))
    assert counts == {"entered_grace": 0, "suspended": 1}
    org.refresh_from_db()
    assert org.subscription.status == S.SUSPENDED
    assert org.is_admitted is False


def test_advance_expired_is_idempotent(org):
    services.transition(org.subscription, S.ACTIVE,
                        period_start=datetime.date(2026, 1, 1),
                        period_end=datetime.date(2026, 2, 1))
    services.advance_expired(today=datetime.date(2026, 2, 2))
    assert services.advance_expired(today=datetime.date(2026, 2, 2)) == {
        "entered_grace": 0, "suspended": 0}


def test_advance_expired_leaves_nif_alone(nif):
    """NIF is not billed; its far-future expiry must never trip the sweep."""
    assert services.advance_expired(today=datetime.date(2026, 6, 15)) == {
        "entered_grace": 0, "suspended": 0}
    nif.refresh_from_db()
    assert nif.subscription.status == S.ACTIVE


# ---------------------------------------------------------------------------
# Phase S11 Part 7: a countdown has to fit inside the period
# ---------------------------------------------------------------------------
class TestCountdownNoticesFitThePeriod:
    """A monthly period is 30 or 31 days, so the 30-day notice fired on the
    first or second morning of it: a monthly customer was emailed "Your
    subscription ends in 30 days" the day after renewing. Alarming, useless,
    and it trains people to ignore the 3-day notice that matters."""

    @staticmethod
    def _run(organization, *, period_days, days_remaining):
        import datetime

        from django.utils import timezone

        from tenancy import expiry_notices
        from tenancy.models import Subscription

        today = timezone.localdate()
        end = today + datetime.timedelta(days=days_remaining)
        Subscription.objects.filter(organization=organization).update(
            status=Subscription.Status.ACTIVE,
            current_period_start=end - datetime.timedelta(days=period_days),
            current_period_end=end)
        return expiry_notices.run(today=today, dry_run=True)

    def test_a_monthly_plan_gets_no_thirty_day_notice(self, org):
        result = self._run(org, period_days=30, days_remaining=30)
        assert result["notices"] == [], result["notices"]

    def test_but_it_does_get_the_seven_day_one(self, org):
        result = self._run(org, period_days=30, days_remaining=7)
        assert any(":d7" in key for key in result["notices"]), result

    def test_and_the_three_day_one(self, org):
        result = self._run(org, period_days=30, days_remaining=3)
        assert any(":d3" in key for key in result["notices"])

    def test_an_annual_plan_does_get_the_thirty_day_notice(self, org):
        """Which is the whole point of having a 30-day threshold: on a
        twelve-month period it is a month's warning, not a greeting."""
        result = self._run(org, period_days=365, days_remaining=30)
        assert any(":d30" in key for key in result["notices"]), result

    def test_an_operator_extension_widens_the_window_it_earns(self, org):
        """`period_days` is the length of THIS period, not the plan's
        nominal interval, so an extension is measured as what it is."""
        result = self._run(org, period_days=60, days_remaining=30)
        assert any(":d30" in key for key in result["notices"])
