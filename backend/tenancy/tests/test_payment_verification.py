"""Part 6: manual payment verification.

The money path. The tests that matter most here are the ones about what must
NOT happen: a client-supplied amount, a replayed verification, a tenant acting
as its own reviewer.
"""
import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from tenancy import payments, services
from tenancy.exceptions import IllegalPaymentTransition
from tenancy.models import Payment, Subscription, SubscriptionEvent
from tenancy.periods import add_months

from .conftest import TODAY

pytestmark = pytest.mark.django_db

P = Payment.Status
S = Subscription.Status


def _proof():
    return SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 receipt",
                               content_type="application/pdf")


def _submit(payment):
    return payments.submit_proof(
        payment, method=Payment.Method.BANK_TRANSFER,
        transaction_id="TXN-12345", paid_at=TODAY, proof=_proof())


# --- 1. create ----------------------------------------------------------
def test_create_payment_derives_the_amount_from_the_database(org, annual_plan):
    payment = payments.create_payment(org, annual_plan, today=TODAY)
    assert payment.amount_minor == annual_plan.prices.first().amount_minor
    assert payment.currency == "NPR"
    assert payment.status == P.AWAITING_PROOF
    # Pinned to the exact price row, so a later price change cannot restate it.
    assert payment.plan_price_id is not None


def test_the_amount_cannot_be_supplied_by_the_caller(org, annual_plan):
    """A tenant must not be able to declare they paid NPR 1 for an annual plan."""
    assert Payment._meta.get_field("amount_minor").editable is False
    assert Payment._meta.get_field("currency").editable is False
    assert Payment._meta.get_field("payment_reference").editable is False


def test_payment_reference_is_scoped_and_sequential_per_organization(org, nif,
                                                                      annual_plan):
    a = payments.create_payment(org, annual_plan, today=TODAY)
    b = payments.create_payment(org, annual_plan, today=TODAY)
    c = payments.create_payment(nif, annual_plan, today=TODAY)

    assert a.payment_reference.endswith("0001")
    assert b.payment_reference.endswith("0002")
    # NIF's counter is its own: one customer's purchases never advance another's.
    assert c.payment_reference.endswith("0001")
    assert c.payment_reference.startswith("PAY-NIFN-")
    assert a.payment_reference.startswith("PAY-ABCS-")


def test_a_price_change_does_not_restate_an_open_payment(org, monthly_plan):
    from tenancy.models import PlanPrice

    payment = payments.create_payment(org, monthly_plan, today=TODAY)
    original = payment.amount_minor
    PlanPrice.objects.create(plan=monthly_plan, currency="NPR",
                              amount_minor=original + 50000,
                              effective_from=TODAY + datetime.timedelta(days=1))
    payment.refresh_from_db()
    assert payment.amount_minor == original


# --- 2 & 3. submit and claim -------------------------------------------
def test_the_happy_path_walks_the_state_machine(org, annual_plan, platform_user):
    payment = payments.create_payment(org, annual_plan, today=TODAY)
    assert payment.status == P.AWAITING_PROOF

    payment = _submit(payment)
    assert payment.status == P.SUBMITTED
    assert payment.submitted_at is not None
    assert payment.proof.name

    payment = payments.claim_for_review(payment, platform_user)
    assert payment.status == P.UNDER_REVIEW
    assert payment.reviewer == platform_user

    payment, sub = payments.verify_payment(payment, platform_user, today=TODAY)
    assert payment.status == P.VERIFIED
    assert payment.verified_by == platform_user
    assert sub.status == S.ACTIVE


def test_steps_cannot_be_skipped(org, annual_plan, platform_user):
    payment = payments.create_payment(org, annual_plan, today=TODAY)
    # Verifying something with no proof attached is refused.
    with pytest.raises(IllegalPaymentTransition):
        payments.verify_payment(payment, platform_user)
    # So is claiming a payment that has not been submitted.
    with pytest.raises(IllegalPaymentTransition):
        payments.claim_for_review(payment, platform_user)


def test_proof_is_stored_outside_the_tenant_media_tree(org, annual_plan):
    """It is read by platform staff, so it must not be tenant-servable."""
    payment = _submit(payments.create_payment(org, annual_plan, today=TODAY))
    assert payment.proof.name.startswith(f"platform/payments/{org.pk}/")
    assert not payment.proof.name.startswith("org/")


# --- 4a. verification activates the subscription -----------------------
def test_verification_activates_and_sets_the_paid_period(org, annual_plan,
                                                          platform_user):
    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    _, sub = payments.verify_payment(payment, platform_user, today=TODAY)

    assert sub.status == S.ACTIVE
    assert sub.plan == annual_plan
    assert sub.current_period_start == TODAY
    assert sub.current_period_end == add_months(TODAY, 12)
    assert sub.plan_price_id == payment.plan_price_id

    org.refresh_from_db()
    assert org.subscription_status == S.ACTIVE
    assert services.mirror_drift(org) is None


def test_verification_writes_an_activation_event_citing_the_payment(
        org, annual_plan, platform_user):
    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    payments.verify_payment(payment, platform_user, today=TODAY)

    event = SubscriptionEvent.objects.get(payment=payment)
    assert event.event == SubscriptionEvent.Event.ACTIVATED
    # `actor_id`, not `actor`. The actor is a PLATFORM operator, whose User row
    # has `organization IS NULL` -- so dereferencing the FK from a connection
    # that has not declared platform scope cannot load it. The foreign key
    # value is the fact being asserted, and it needs no join.
    assert event.actor_id == platform_user.pk
    assert event.period_end == add_months(TODAY, 12)


def test_verifying_the_same_payment_twice_cannot_extend_the_period_twice(
        org, annual_plan, platform_user):
    """The idempotency guard. A double click must not buy a free term."""
    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    _, sub = payments.verify_payment(payment, platform_user, today=TODAY)
    first_end = sub.current_period_end

    with pytest.raises(IllegalPaymentTransition):
        payments.verify_payment(payment, platform_user, today=TODAY)

    sub.refresh_from_db()
    assert sub.current_period_end == first_end
    assert SubscriptionEvent.objects.filter(payment=payment).count() == 1


def test_the_database_refuses_a_second_activation_from_one_payment(
        org, annual_plan, platform_user):
    """Belt and braces: the constraint holds even if the service logic were wrong."""
    from django.db import IntegrityError, transaction

    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    _, sub = payments.verify_payment(payment, platform_user, today=TODAY)

    with pytest.raises(IntegrityError), transaction.atomic():
        SubscriptionEvent.objects.create(
            organization=org, subscription=sub,
            event=SubscriptionEvent.Event.ACTIVATED, payment=payment)


def test_renewal_appends_to_an_unexpired_period(org, monthly_plan, platform_user):
    first = payments.claim_for_review(
        _submit(payments.create_payment(org, monthly_plan, today=TODAY)),
        platform_user)
    _, sub = payments.verify_payment(first, platform_user, today=TODAY)
    assert sub.current_period_end == add_months(TODAY, 1)

    second = payments.create_payment(org, monthly_plan, today=TODAY)
    second = payments.submit_proof(second, method=Payment.Method.ESEWA,
                                    transaction_id="TXN-67890", paid_at=TODAY,
                                    proof=_proof())
    second = payments.claim_for_review(second, platform_user)
    _, sub = payments.verify_payment(second, platform_user, today=TODAY)

    # Two months, not one: the customer keeps the days they already paid for.
    assert sub.current_period_end == add_months(TODAY, 2)
    assert sub.events.filter(event=SubscriptionEvent.Event.RENEWED).exists()


# --- 4b. rejection ------------------------------------------------------
def test_rejection_requires_a_reason(org, annual_plan, platform_user):
    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    with pytest.raises(IllegalPaymentTransition):
        payments.reject_payment(payment, platform_user, reason="   ")


def test_rejection_leaves_the_subscription_untouched(org, annual_plan,
                                                      platform_user):
    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    payment = payments.reject_payment(payment, platform_user,
                                       reason="Receipt is illegible.")
    assert payment.status == P.REJECTED
    assert payment.rejection_reason == "Receipt is illegible."
    org.refresh_from_db()
    assert org.subscription.status == S.TRIAL


def test_a_rejected_payment_can_be_resubmitted(org, annual_plan, platform_user):
    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    payment = payments.reject_payment(payment, platform_user, reason="wrong amount")
    payment = payments.submit_proof(payment, method=Payment.Method.ESEWA,
                                     transaction_id="TXN-RETRY", paid_at=TODAY,
                                     proof=_proof())
    assert payment.status == P.SUBMITTED
    assert payment.rejection_reason == ""
    assert payment.reviewer is None


# --- authority ----------------------------------------------------------
def test_a_tenant_user_cannot_verify_its_own_payment(org, annual_plan,
                                                      tenant_admin):
    payment = _submit(payments.create_payment(org, annual_plan, today=TODAY))
    with pytest.raises(IllegalPaymentTransition):
        payments.claim_for_review(payment, tenant_admin)
    with pytest.raises(IllegalPaymentTransition):
        payments.verify_payment(payment, tenant_admin)


def test_an_anonymous_caller_cannot_verify(org, annual_plan):
    payment = _submit(payments.create_payment(org, annual_plan, today=TODAY))
    with pytest.raises(IllegalPaymentTransition):
        payments.verify_payment(payment, None)


# --- duplicate transaction ids -----------------------------------------
def test_the_same_transaction_id_cannot_be_claimed_twice_by_one_tenant(
        org, annual_plan):
    from django.db import IntegrityError, transaction

    _submit(payments.create_payment(org, annual_plan, today=TODAY))
    second = payments.create_payment(org, annual_plan, today=TODAY)
    with pytest.raises(IntegrityError), transaction.atomic():
        payments.submit_proof(second, method=Payment.Method.BANK_TRANSFER,
                               transaction_id="TXN-12345", paid_at=TODAY,
                               proof=_proof())


def test_two_payments_may_both_be_awaiting_proof(org, annual_plan):
    """The blank transaction_id must not collide -- the constraint is conditional."""
    payments.create_payment(org, annual_plan, today=TODAY)
    payments.create_payment(org, annual_plan, today=TODAY)
    assert Payment.objects.filter(organization=org,
                                   status=P.AWAITING_PROOF).count() == 2


# --- the queue ----------------------------------------------------------
def test_the_pending_queue_shows_only_what_needs_platform_action(
        org, annual_plan, platform_user):
    awaiting = payments.create_payment(org, annual_plan, today=TODAY)
    submitted = _submit(payments.create_payment(org, annual_plan, today=TODAY))

    queue = list(payments.pending_queue())
    assert submitted in queue
    assert awaiting not in queue        # nothing uploaded yet


def test_payment_instructions_are_seeded_inactive_until_configured():
    """Blank transfer details must never be shown to a customer."""
    from tenancy.models import PaymentInstruction

    assert PaymentInstruction.objects.filter(method="esewa").exists()
    assert PaymentInstruction.objects.filter(method="bank_transfer").exists()
    assert payments.instructions_for("esewa") is None
    assert payments.instructions_for("bank_transfer") is None


def test_no_gateway_is_called_and_the_gateway_fields_stay_empty(
        org, annual_plan, platform_user):
    payment = payments.claim_for_review(
        _submit(payments.create_payment(org, annual_plan, today=TODAY)),
        platform_user)
    payment, _ = payments.verify_payment(payment, platform_user, today=TODAY)
    assert payment.gateway == ""
    assert payment.gateway_ref == ""
    assert payment.gateway_payload is None
