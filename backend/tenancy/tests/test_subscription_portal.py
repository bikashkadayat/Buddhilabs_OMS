"""Phase S8: the customer's own subscription page, and the money behind it.

THREE PROPERTIES, AND THEY FAIL INDEPENDENTLY:

  AUTHORITY   only an administrator of that organization, and only about
              that organization. An employee, another tenant's admin and a
              platform operator are all refused, for three different
              reasons.
  INERTNESS   nothing a customer does activates anything. Every tenant-side
              operation stops at SUBMITTED, and the subscription moves only
              when a human verifies the money.
  TRUTHFULNESS the page shows what the database says, now -- including
              straight after a payment is verified, which is the moment a
              stale read is most damaging and least visible.
"""
import datetime

import pytest

from tenancy import payments, portal
from tenancy.context import tenant_context
from tenancy.models import Payment, PlatformAuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(db, org):
    """An administrator of the `org` fixture."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    with tenant_context(org):
        return User.objects.create_user(
            username="abc-admin", email="admin@abcschool.test",
            password="x-Admin-1", role="admin", organization=org)


@pytest.fixture
def employee(db, org):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    with tenant_context(org):
        return User.objects.create_user(
            username="abc-staff", email="staff@abcschool.test",
            password="x-Staff-1", role="maker", organization=org)


@pytest.fixture
def api(admin):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(admin)
    return client


@pytest.fixture
def instructions(db):
    from tenancy.models import PaymentInstruction

    return [
        PaymentInstruction.objects.create(
            method="bank_transfer", label="Bank transfer",
            account_name="Platform Pvt Ltd", account_number="01234567891",
            bank_name="Nepal Bank", branch="Kathmandu", sort_order=1),
        PaymentInstruction.objects.create(
            method="esewa", label="eSewa", esewa_id="9800000000",
            instructions_html="<p>Send to the eSewa id above.</p>",
            sort_order=2),
    ]


def _price_of(plan):
    """The plan's current price, read from the database.

    NOT THE LITERAL. `test_plans.py` greps the whole backend for the seeded
    amounts and allows them in exactly two files -- the seed migration and
    itself -- and this file restated two of them, which is the very habit
    that guard exists to stop: a price written into a test is a test that
    breaks when the price changes, for no reason, and it is one more place
    somebody has to remember to edit.
    """
    from tenancy import plans as plan_service

    return plan_service.current_price(plan)


# --- AUTHORITY ----------------------------------------------------------
def test_an_employee_cannot_see_what_their_employer_pays(employee):
    """`IsTenantUser` would have been too wide: every employee would see the
    bill and could submit payment proof in their employer's name."""
    with pytest.raises(portal.NotTenantAdmin):
        portal.subscription_state(employee)


def test_a_platform_operator_has_no_subscription_of_their_own(platform_user):
    """Refused as firmly as an employee, for a different reason: they belong
    to no organization, so "their subscription" has no answer."""
    with pytest.raises(portal.NotTenantAdmin):
        portal.subscription_state(platform_user)


def test_an_anonymous_caller_is_refused(client):
    assert client.get("/api/v1/tenant/subscription/").status_code == 401


def test_an_employee_is_refused_by_every_portal_route(org, employee):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(employee)
    for method, path, body in (
            ("get", "/api/v1/tenant/subscription/", None),
            ("get", "/api/v1/tenant/plans/", None),
            ("get", "/api/v1/tenant/payment-instructions/", None),
            ("get", "/api/v1/tenant/payments/", None),
            ("post", "/api/v1/tenant/subscription/request/",
             {"plan_code": "annual"}),
    ):
        response = getattr(client, method)(path, body, format="json")
        assert response.status_code == 403, f"{method} {path}"


def test_one_tenants_admin_cannot_reach_another_tenants_payment(api, nif,
                                                                 annual_plan):
    """A payment reference is sequential per tenant, so it would otherwise
    be a way to confirm that another organization has one."""
    with tenant_context(nif):
        theirs = payments.create_payment(nif, annual_plan)

    response = api.post(
        f"/api/v1/tenant/payments/{theirs.payment_reference}/proof/",
        {"method": "bank_transfer"}, format="multipart")
    assert response.status_code == 400
    assert "No such payment" in str(response.json())
    theirs.refresh_from_db()
    assert theirs.status == Payment.Status.AWAITING_PROOF


# --- Part 1: what the customer sees -------------------------------------
def test_the_page_reports_the_subscription_the_database_holds(api, org):
    body = api.get("/api/v1/tenant/subscription/").json()

    assert body["available"] is True
    assert body["organization"]["slug"] == org.slug
    assert body["plan"]["code"] == org.subscription.plan.code
    assert body["status"] == org.subscription.status
    assert body["starts_on"] == str(org.subscription.current_period_start)
    assert body["expires_on"] == str(org.subscription.current_period_end)
    assert body["days_remaining"] == org.subscription.days_until_expiry()
    assert body["trial"]["is_trial"] is True
    assert body["trial"]["ends_on"] == str(org.subscription.trial_end)


def test_it_shows_the_workspace_status_as_well_as_the_billing_status(
        api, org, platform_user):
    """They differ exactly when an operator has intervened, and a customer
    locked out while their subscription says ACTIVE needs to see both or the
    page is lying to them."""
    from tenancy import console

    console.suspend(platform_user, org, reason="Non-payment")

    body = api.get("/api/v1/tenant/subscription/").json()
    assert body["is_admitted"] is False
    assert body["workspace_status"] == "suspended"
    assert "support" in body["explanation"].lower()


@pytest.mark.parametrize("status,phrase", [
    ("trial", "trial"),
    ("grace", "grace period"),
    ("expired", "expired"),
    ("suspended", "suspended"),
])
def test_every_state_is_explained_in_a_sentence(api, org, status, phrase):
    """Part 7. Written server-side so the page and the support runbook agree
    about what each state means."""
    from tenancy.models import Subscription

    Subscription.objects.filter(pk=org.subscription.pk).update(status=status)
    body = api.get("/api/v1/tenant/subscription/").json()
    assert phrase in body["explanation"].lower(), body["explanation"]


# --- TRUTHFULNESS -------------------------------------------------------
def test_the_page_is_correct_immediately_after_a_payment_is_verified(
        api, org, annual_plan, platform_user):
    """The bug this pins.

    `subscription_state` used to read `user.organization`, which hands back
    whatever Django cached on that user instance -- including its
    `subscription` relation. A customer who paid, had it verified, and
    reloaded the page was shown their OLD plan and OLD status: stale exactly
    when it mattered most, and invisible because every other field looked
    right.
    """
    before = api.get("/api/v1/tenant/subscription/").json()
    assert before["plan"]["code"] == "monthly"
    assert before["status"] == "trial"

    payment = portal.request_plan(api.handler._force_user, "annual")
    portal.submit_payment_proof(api.handler._force_user,
                                payment.payment_reference,
                                method="bank_transfer",
                                transaction_id="TXN-9")
    payments.claim_for_review(payment, platform_user)
    payments.verify_payment(payment, platform_user)

    after = api.get("/api/v1/tenant/subscription/").json()
    assert after["plan"]["code"] == "annual"
    assert after["status"] == "active"
    assert after["trial"]["is_trial"] is False


# --- Part 2: the plans --------------------------------------------------
def test_the_plans_are_priced_from_the_database_not_the_front_end(api):
    """The four figures the brief lists appear in no template or component.

    `PlanPrice` rows are immutable and superseded by insertion, so "the
    price" is a question about a date -- and a number typed into a page is
    one that goes stale on the first change, shown to the one audience that
    must never see a stale price.
    """
    rows = api.get("/api/v1/tenant/plans/").json()
    by_code = {row["code"]: row for row in rows}

    assert set(by_code) >= {"monthly", "quarterly", "halfyearly", "annual"}
    for row in rows:
        assert row["amount_minor"] > 0
        assert row["currency"] == "NPR"
    # Comparable at a glance (Part 2: "compare plans"), computed server-side
    # so every caller compares the same way.
    assert (by_code["annual"]["monthly_equivalent_minor"]
            < by_code["monthly"]["monthly_equivalent_minor"])
    assert by_code["monthly"]["is_current"] is True
    assert by_code["annual"]["is_upgrade"] is True


def test_a_plan_with_no_effective_price_is_not_offered(api, monthly_plan):
    """It cannot be sold, so it is not shown -- and "misconfigured plan" is
    not a customer's problem to read about."""
    from tenancy.models import PlanPrice

    PlanPrice.objects.filter(plan=monthly_plan).delete()
    codes = {row["code"] for row in api.get("/api/v1/tenant/plans/").json()}
    assert "monthly" not in codes


# --- Part 3: how to pay -------------------------------------------------
def test_the_payment_instructions_come_from_the_console_rows(api,
                                                              instructions):
    rows = api.get("/api/v1/tenant/payment-instructions/").json()
    by_method = {row["method"]: row for row in rows}

    assert by_method["bank_transfer"]["account_number"] == "01234567891"
    assert by_method["bank_transfer"]["bank_name"] == "Nepal Bank"
    assert by_method["bank_transfer"]["branch"] == "Kathmandu"
    assert by_method["esewa"]["esewa_id"] == "9800000000"
    assert "eSewa id" in by_method["esewa"]["instructions_html"]


def test_an_inactive_method_is_not_shown(api, instructions):
    from tenancy.models import PaymentInstruction

    PaymentInstruction.objects.filter(method="esewa").update(is_active=False)
    methods = {row["method"]
               for row in api.get("/api/v1/tenant/payment-instructions/")
               .json()}
    assert methods == {"bank_transfer"}


def test_a_platform_with_no_payment_methods_is_flagged_before_a_customer_asks(
        db, monthly_plan):
    """The gap the portal exposed: it told a customer exactly what they owed
    and showed them no way to pay it. Every figure correct, page useless."""
    from tenancy import launch

    answer = launch.check_payment_methods()
    assert answer["ready"] is False
    assert "no way to pay" in answer["detail"]


def test_a_method_configured_with_nothing_in_it_is_also_flagged(db,
                                                                monthly_plan):
    from tenancy import launch
    from tenancy.models import PaymentInstruction

    PaymentInstruction.objects.create(method="esewa", label="eSewa")
    answer = launch.check_payment_methods()
    assert answer["ready"] is False
    assert "empty" in answer["detail"]


# --- INERTNESS: Parts 2 and 4 -------------------------------------------
def test_requesting_a_plan_activates_nothing(api, org, annual_plan):
    """Part 2: "no plan activation occurs automatically"."""
    response = api.post("/api/v1/tenant/subscription/request/",
                        {"plan_code": "annual"}, format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "awaiting_proof"
    assert body["amount_minor"] == _price_of(annual_plan).amount_minor
    assert "Nothing is active yet" in body["detail"]

    org.refresh_from_db()
    assert org.subscription.plan.code == "monthly"
    assert org.subscription.status == "trial"


def test_the_amount_is_resolved_server_side_and_cannot_be_proposed(api,
                                                                    annual_plan):
    """There is no field through which a client could say what it owes."""
    response = api.post("/api/v1/tenant/subscription/request/",
                        {"plan_code": "annual", "amount_minor": 1,
                         "currency": "USD"}, format="json")
    assert response.status_code == 201
    assert response.json()["amount_minor"] == _price_of(annual_plan).amount_minor
    assert response.json()["currency"] == "NPR"


def test_a_retired_plan_cannot_be_requested_by_anybody_who_knows_its_code(
        api, annual_plan):
    """Including from an old quotation."""
    from tenancy.models import Plan

    Plan.objects.filter(code="annual").update(is_active=False)
    response = api.post("/api/v1/tenant/subscription/request/",
                        {"plan_code": "annual"}, format="json")
    assert response.status_code == 400
    assert "not available" in str(response.json())


def test_clicking_twice_does_not_leave_two_open_payments(api, annual_plan):
    first = api.post("/api/v1/tenant/subscription/request/",
                     {"plan_code": "annual"}, format="json").json()
    second = api.post("/api/v1/tenant/subscription/request/",
                      {"plan_code": "annual"}, format="json").json()

    assert first["reference"] == second["reference"]
    assert Payment.objects.filter(
        status=Payment.Status.AWAITING_PROOF).count() == 1


def test_changing_your_mind_supersedes_the_previous_request(api, annual_plan,
                                                             monthly_plan):
    """Two open payments for one tenant is the state in which somebody pays
    the wrong reference."""
    first = api.post("/api/v1/tenant/subscription/request/",
                     {"plan_code": "annual"}, format="json").json()
    second = api.post("/api/v1/tenant/subscription/request/",
                      {"plan_code": "quarterly"}, format="json").json()

    assert first["reference"] != second["reference"]
    assert Payment.objects.get(
        payment_reference=first["reference"]).status == \
        Payment.Status.CANCELLED
    assert Payment.objects.filter(
        status=Payment.Status.AWAITING_PROOF).count() == 1


# --- Part 4: the proof --------------------------------------------------
def test_submitting_proof_puts_it_in_the_review_queue_and_nothing_more(
        api, org, annual_plan):
    reference = api.post("/api/v1/tenant/subscription/request/",
                         {"plan_code": "annual"},
                         format="json").json()["reference"]

    response = api.post(
        f"/api/v1/tenant/payments/{reference}/proof/",
        {"method": "bank_transfer", "transaction_id": "TXN-123",
         "paid_at": "2026-06-10", "payer_note": "Paid from our NIC account"},
        format="multipart")
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"

    payment = Payment.objects.get(payment_reference=reference)
    assert payment.transaction_id == "TXN-123"
    assert payment.paid_at == datetime.date(2026, 6, 10)
    assert payment.payer_note.startswith("Paid from")
    # The customer's side ends here.
    org.refresh_from_db()
    assert org.subscription.status == "trial"
    assert payment in payments.pending_queue()


def test_a_payment_date_in_the_future_is_refused_at_the_form(api,
                                                              annual_plan):
    """Caught here rather than by the verifier: the customer can fix a typed
    date in a second, and an operator can only reject and ask."""
    from django.utils import timezone

    reference = api.post("/api/v1/tenant/subscription/request/",
                         {"plan_code": "annual"},
                         format="json").json()["reference"]
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)

    response = api.post(f"/api/v1/tenant/payments/{reference}/proof/",
                        {"method": "bank_transfer",
                         "paid_at": str(tomorrow)}, format="multipart")
    assert response.status_code == 400
    assert "future" in str(response.json())


def test_a_screenshot_is_stored_outside_the_tenant_media_tree(api,
                                                               annual_plan):
    """A receipt naming a bank account must not sit behind a guessable URL.

    `documents.protected_media` refuses the `platform/` prefix at both ends,
    so the only route to it is a platform-authenticated view.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from documents.protected_media import signed_media_url

    reference = api.post("/api/v1/tenant/subscription/request/",
                         {"plan_code": "annual"},
                         format="json").json()["reference"]
    api.post(f"/api/v1/tenant/payments/{reference}/proof/", {
        "method": "esewa",
        "proof": SimpleUploadedFile("receipt.png", b"\x89PNG\r\n\x1a\n",
                                     content_type="image/png"),
    }, format="multipart")

    payment = Payment.objects.get(payment_reference=reference)
    assert payment.proof
    assert payment.proof.name.startswith("platform/payments/")
    assert signed_media_url(payment.proof.name) is None


def test_a_rejected_payment_can_be_resubmitted_with_the_reason_shown(
        api, annual_plan, platform_user):
    """The reason is the whole point of a rejection: a customer cannot fix
    what they are not told."""
    reference = api.post("/api/v1/tenant/subscription/request/",
                         {"plan_code": "annual"},
                         format="json").json()["reference"]
    api.post(f"/api/v1/tenant/payments/{reference}/proof/",
             {"method": "bank_transfer", "transaction_id": "WRONG"},
             format="multipart")

    payment = Payment.objects.get(payment_reference=reference)
    # Claimed first: the status machine is submitted -> under_review ->
    # decided, which is what stops two operators reaching different
    # conclusions about one payment.
    payments.claim_for_review(payment, platform_user)
    payments.reject_payment(payment, platform_user,
                            reason="The transaction id does not exist.")

    history = {row["reference"]: row
               for row in api.get("/api/v1/tenant/payments/").json()}
    row = history[reference]
    assert row["status"] == "rejected"
    assert row["rejection_reason"] == "The transaction id does not exist."
    assert row["explanation"] == "The transaction id does not exist."
    assert row["can_submit_proof"] is True

    again = api.post(f"/api/v1/tenant/payments/{reference}/proof/",
                     {"method": "bank_transfer", "transaction_id": "RIGHT"},
                     format="multipart")
    assert again.status_code == 200
    assert again.json()["status"] == "submitted"


# --- Part 6 and 7: verification moves the subscription ------------------
def test_verification_renews_through_the_existing_service_layer(api, org,
                                                                 annual_plan,
                                                                 platform_user):
    """Part 6: "must use existing SubscriptionEvent, Payment, Subscription.
    No custom logic." Asserted by looking for the event the service writes.
    """
    from tenancy.models import SubscriptionEvent

    reference = api.post("/api/v1/tenant/subscription/request/",
                         {"plan_code": "annual"},
                         format="json").json()["reference"]
    api.post(f"/api/v1/tenant/payments/{reference}/proof/",
             {"method": "bank_transfer"}, format="multipart")

    payment = Payment.objects.get(payment_reference=reference)
    payments.claim_for_review(payment, platform_user)
    payment, subscription = payments.verify_payment(payment, platform_user)

    assert payment.status == Payment.Status.VERIFIED
    assert subscription.status == "active"
    assert subscription.plan.code == "annual"

    event = (SubscriptionEvent.objects
             .filter(subscription=subscription)
             .order_by("-effective_at", "-id").first())
    assert event.payment_id == payment.pk
    assert event.to_plan_id == annual_plan.pk


# --- Part 10: the audit trail -------------------------------------------
def test_the_whole_money_conversation_is_on_the_platform_trail(api, org,
                                                                annual_plan,
                                                                platform_user):
    """Request, submission, verification -- in one place.

    Recorded on the PLATFORM trail even though a customer performed the
    first two: an operator asking "why is this tenant active" needs all
    three together, and the customer-side events would otherwise be inside
    the workspace where the console cannot read them under RLS.
    """
    A = PlatformAuditLog.Action

    reference = api.post("/api/v1/tenant/subscription/request/",
                         {"plan_code": "annual"},
                         format="json").json()["reference"]
    api.post(f"/api/v1/tenant/payments/{reference}/proof/",
             {"method": "esewa", "transaction_id": "TXN-5"},
             format="multipart")
    payment = Payment.objects.get(payment_reference=reference)
    payments.claim_for_review(payment, platform_user)
    payments.verify_payment(payment, platform_user)

    actions = set(PlatformAuditLog.objects
                  .filter(organization=org)
                  .values_list("action", flat=True))
    assert {A.PAYMENT_REQUESTED, A.PAYMENT_SUBMITTED} <= actions

    requested = PlatformAuditLog.objects.filter(
        organization=org, action=A.PAYMENT_REQUESTED).first()
    assert requested.changes["plan"] == "annual"
    assert requested.changes["amount_minor"] == _price_of(
        annual_plan).amount_minor
    assert requested.changes["requested_by"] == "admin@abcschool.test"
    # No operator was involved in the request, and the entry says so.
    assert requested.actor is None

    submitted = PlatformAuditLog.objects.filter(
        organization=org, action=A.PAYMENT_SUBMITTED).first()
    assert submitted.changes["method"] == "esewa"
    assert submitted.changes["transaction_id"] == "TXN-5"


# --- Part 9: the revenue figures ----------------------------------------
def test_the_dashboard_reports_how_the_manual_workflow_is_clearing(
        platform_user, org, annual_plan):
    """A queue of 3 means nothing without knowing whether 40 or 0 were
    settled last month."""
    from tenancy import console

    # Measured BEFORE the renewal, because verifying an annual payment pushes
    # the period end a year out -- so the tenant that was expiring within
    # thirty days is exactly the one that stops being counted. That is the
    # figure behaving correctly, and a test that asserted otherwise would be
    # asserting that renewals do not work.
    before = console.dashboard(platform_user)
    assert before["subscriptions_expiring_30d"] >= 1, (
        "the fixture tenant is on a trial that ends within the month")
    assert before["payments_verified_30d"] == 0

    payment = payments.create_payment(org, annual_plan)
    payments.submit_proof(payment, method="bank_transfer")
    payments.claim_for_review(payment, platform_user)
    payments.verify_payment(payment, platform_user)

    after = console.dashboard(platform_user)
    assert after["payments_verified_30d"] == 1
    assert after["payments_pending_verification"] == 0
    assert after["subscriptions_expiring_30d"] == \
        before["subscriptions_expiring_30d"] - 1, (
        "a renewed tenant should drop out of the expiring window")
