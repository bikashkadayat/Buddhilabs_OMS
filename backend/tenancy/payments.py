"""Manual payment verification.

NO GATEWAY. V1 is: the customer transfers money by eSewa or bank transfer,
uploads a receipt, and a platform operator looks at it. The ``gateway*`` fields
on ``Payment`` are reserved and untouched.

THE STATE MACHINE
-----------------
    AWAITING_PROOF ──▶ SUBMITTED ──▶ UNDER_REVIEW ──┬─▶ VERIFIED
                                                     └─▶ REJECTED ──▶ (resubmit)

``UNDER_REVIEW`` is not ceremony: a payment must be *claimed* before it can be
decided, so two operators working the queue at the same time cannot both act on
the same receipt and reach different conclusions.

WHY VERIFICATION IS THE DANGEROUS PART
--------------------------------------
Verifying a payment extends a paid period. Doing it twice gives away a free
term; doing it from a replayed request gives away as many as the attacker
likes. Three things stop that, and all three are deliberate:

  * the payment row is locked with ``select_for_update()`` and its status is
    RE-READ inside the transaction, so a double submit serialises and the
    second one finds the status already moved;
  * ``SubscriptionEvent`` carries a unique constraint on
    ``(payment)`` for activation events, so the database refuses a second
    activation from one payment even if the application logic were wrong;
  * ``amount_minor`` is server-derived at creation and ``editable=False``, so
    the amount being verified is the amount we quoted, not one the client sent.
"""
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import plans, services
from .exceptions import IllegalPaymentTransition
from .models import Payment, PaymentInstruction, Subscription, SubscriptionEvent

logger = logging.getLogger(__name__)

P = Payment.Status
S = Subscription.Status

ALLOWED_PAYMENT_TRANSITIONS = {
    P.AWAITING_PROOF: {P.SUBMITTED, P.CANCELLED},
    P.SUBMITTED: {P.UNDER_REVIEW, P.NEEDS_INFO, P.CANCELLED},
    P.UNDER_REVIEW: {P.VERIFIED, P.REJECTED, P.SUBMITTED, P.NEEDS_INFO},
    # The customer answers a question by sending the proof again.
    P.NEEDS_INFO: {P.SUBMITTED, P.CANCELLED},
    # A rejected payment is resubmitted by uploading new proof, which returns it
    # to SUBMITTED rather than creating a second payment for the same term.
    P.REJECTED: {P.SUBMITTED, P.CANCELLED},
    P.VERIFIED: set(),      # terminal, by design
    P.CANCELLED: set(),
}


def _check(payment, to_status):
    allowed = ALLOWED_PAYMENT_TRANSITIONS.get(payment.status, set())
    if to_status not in allowed:
        raise IllegalPaymentTransition(
            f"Payment {payment.payment_reference} is '{payment.status}'; "
            f"cannot move to '{to_status}'. Allowed: "
            f"{sorted(allowed) or 'nothing (terminal)'}.")


# ---------------------------------------------------------------------------
# Reference allocation
# ---------------------------------------------------------------------------
def _next_reference(organization):
    """``PAY-<PREFIX>-<YYYY>-<NNNN>``, unique per organization.

    Quoted on the transfer so a received payment can be matched to the tenant
    that sent it. Counted per organization, so one customer's purchases never
    advance another's numbering -- the same reason the Phase S2 inventory moves
    every business number sequence to ``(organization, year)``.
    """
    year = timezone.localdate().year
    prefix = f"PAY-{organization.document_prefix}-{year}-"
    taken = (Payment.objects
             .filter(organization=organization,
                     payment_reference__startswith=prefix)
             .count())
    # Count-based rather than max-based is safe here because the unique
    # constraint on (organization, payment_reference) is the authority: a race
    # raises IntegrityError, and the caller retries. Payments are a
    # low-frequency, human-initiated act, so a lock row would be over-built.
    return f"{prefix}{taken + 1:04d}"


# ---------------------------------------------------------------------------
# 1. Create
# ---------------------------------------------------------------------------
@transaction.atomic
def create_payment(organization, plan, *, actor=None, currency="NPR", today=None):
    """Open a payment for ``plan``, priced from the database.

    The amount is resolved here, server-side, from the effective ``PlanPrice``
    and stored on the row. Nothing downstream may change it.
    """
    price = plans.current_price(plan, on=today, currency=currency)
    payment = Payment(
        organization=organization,
        plan=plan,
        plan_price=price,
        payment_reference=_next_reference(organization),
        amount_minor=price.amount_minor,
        currency=price.currency,
        status=P.AWAITING_PROOF,
        created_by=actor,
    )
    payment.save()
    logger.info("payment %s opened for %s (%s %s minor)",
                payment.payment_reference, organization.slug,
                payment.currency, payment.amount_minor)
    return payment


def instructions_for(method):
    """The active payment instructions for a method, or None."""
    return (PaymentInstruction.objects
            .filter(method=method, is_active=True)
            .order_by("sort_order", "label")
            .first())


# ---------------------------------------------------------------------------
# 2. Submit proof
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_proof(payment, *, method, transaction_id="", paid_at=None,
                 proof=None, payer_note="", actor=None):
    """Attach the customer's receipt and put the payment in the review queue."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    _check(payment, P.SUBMITTED)

    payment.method = method
    payment.transaction_id = (transaction_id or "").strip()
    payment.paid_at = paid_at
    payment.payer_note = payer_note
    if proof is not None:
        payment.proof = proof
    payment.status = P.SUBMITTED
    payment.submitted_at = timezone.now()
    if actor is not None and getattr(actor, "email", ""):
        payment.submitted_by_email = actor.email
    # A resubmission after rejection starts the review afresh.
    payment.reviewer = None
    payment.review_started_at = None
    payment.rejection_reason = ""
    payment.review_message = ""
    payment.save()
    return payment


# ---------------------------------------------------------------------------
# 3. Claim for review
# ---------------------------------------------------------------------------
@transaction.atomic
def claim_for_review(payment, platform_user):
    """Take the payment off the queue so only one operator decides it."""
    _require_platform(platform_user)
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    _check(payment, P.UNDER_REVIEW)

    payment.status = P.UNDER_REVIEW
    payment.reviewer = platform_user
    payment.review_started_at = timezone.now()
    payment.save()
    return payment


# ---------------------------------------------------------------------------
# 4a. Verify -> activate
# ---------------------------------------------------------------------------
@transaction.atomic
def verify_payment(payment, platform_user, *, note="", today=None):
    """Accept the payment and extend the subscription. Idempotent by lock.

    Returns ``(payment, subscription)``.
    """
    _require_platform(platform_user)

    # Re-read UNDER the lock. This is the idempotency guard: a second concurrent
    # or replayed call blocks here, then finds the status already VERIFIED and
    # is refused by _check rather than extending the period a second time.
    payment = (Payment.objects.select_for_update()
               .select_related("organization", "plan", "plan_price")
               .get(pk=payment.pk))
    _check(payment, P.VERIFIED)

    payment.status = P.VERIFIED
    payment.verified_by = platform_user
    payment.verified_at = timezone.now()
    payment.save()

    subscription = (Subscription.objects
                    .select_for_update()
                    .select_related("organization", "plan")
                    .get(organization=payment.organization))

    start, end = services.extend_period(subscription, payment.plan, today=today)
    was_renewal = subscription.status == S.ACTIVE

    subscription = services.transition(
        subscription, S.ACTIVE,
        event=(SubscriptionEvent.Event.RENEWED if was_renewal
               else SubscriptionEvent.Event.ACTIVATED),
        actor=platform_user,
        payment=payment,
        plan=payment.plan,
        plan_price=payment.plan_price,
        period_start=start,
        period_end=end,
        note=note or f"Payment {payment.payment_reference} verified.",
    )
    logger.info("payment %s verified by %s; %s active until %s",
                payment.payment_reference, platform_user.pk,
                payment.organization.slug, end)
    return payment, subscription


# ---------------------------------------------------------------------------
# 4b. Reject
# ---------------------------------------------------------------------------
@transaction.atomic
def reject_payment(payment, platform_user, *, reason):
    """Refuse the payment. A reason is mandatory -- the customer must be told why."""
    _require_platform(platform_user)
    if not (reason or "").strip():
        raise IllegalPaymentTransition(
            "A rejection reason is required: the customer has to be told what "
            "to fix before they can resubmit.")

    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    _check(payment, P.REJECTED)

    payment.status = P.REJECTED
    payment.rejection_reason = reason.strip()
    payment.verified_by = None
    payment.verified_at = None
    payment.save()
    return payment


# ---------------------------------------------------------------------------
# 4c. Ask the customer for more
# ---------------------------------------------------------------------------
@transaction.atomic
def request_information(payment, platform_user, *, message):
    """Send the payment back to the customer with a question.

    For when the reviewer cannot decide yet: an unreadable screenshot, an
    amount that does not match, a transaction ID the bank does not show.
    Rejecting would tell the customer they did something wrong; this tells
    them what is missing. The customer answers by submitting again.
    """
    _require_platform(platform_user)
    if not (message or "").strip():
        raise IllegalPaymentTransition(
            "Say what you need from the customer: they will see this "
            "message and nothing else.")
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    _check(payment, P.NEEDS_INFO)
    payment.status = P.NEEDS_INFO
    payment.review_message = message.strip()
    payment.reviewer = platform_user
    payment.save()
    return payment


# ---------------------------------------------------------------------------
def pending_queue():
    """Everything awaiting platform action, oldest first."""
    return (Payment.objects
            .filter(status__in=[P.SUBMITTED, P.UNDER_REVIEW, P.NEEDS_INFO])
            .select_related("organization", "plan", "reviewer", "created_by")
            .order_by("submitted_at", "created_at"))


def _require_platform(user):
    """Only platform staff may decide a payment.

    Checked in the service layer, not only in a permission class, because a
    management command and a shell session reach these functions without
    passing through DRF at all.
    """
    if user is None or not getattr(user, "is_platform_staff", False):
        raise IllegalPaymentTransition(
            "Only platform staff may review or decide a payment.")
    if getattr(user, "organization_id", None) is not None:
        raise IllegalPaymentTransition(
            "A user bound to an organization cannot act as platform staff.")
