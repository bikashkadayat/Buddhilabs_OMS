"""The Payment Center: deciding payments and managing payment methods.

THE PROPERTY THAT MATTERS: no engineer. Approving a payment from the console
must do everything the shell procedure did -- activate or extend the
subscription, record the subscription event -- plus what the shell never
did: the platform audit entry and telling the customer.
"""
import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from tenancy import payments
from tenancy.context import no_tenant
from tenancy.models import (Payment, PaymentInstruction, PlatformAuditLog,
                            Subscription, SubscriptionEvent)

from .conftest import TODAY

pytestmark = pytest.mark.django_db
P = Payment.Status


@pytest.fixture
def ops(platform_user):
    client = APIClient()
    client.force_authenticate(user=platform_user)
    return client


@pytest.fixture
def tenant_admin_of_org(org, django_user_model):
    from tenancy.context import tenant_context

    with tenant_context(org):
        return django_user_model.objects.create_user(
            username="abc-admin", email="admin@abc.test", password="x-Pass-12345",
            role="admin", organization=org)


@pytest.fixture
def submitted(org, annual_plan):
    payment = payments.create_payment(org, annual_plan, today=TODAY)
    return payments.submit_proof(
        payment, method=Payment.Method.BANK_TRANSFER, transaction_id="TXN-1",
        paid_at=TODAY,
        proof=SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 receipt",
                                 content_type="application/pdf"))


def _url(payment, verb):
    return f"/api/v1/platform/payments/{payment.pk}/{verb}/"


# --- Part 12: approval -----------------------------------------------------
def test_approving_activates_the_subscription_in_one_step(ops, submitted, org):
    response = ops.post(_url(submitted, "approve"), {"note": "Matched NIC Asia"},
                        format="json")
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == P.VERIFIED
    sub = Subscription.objects.get(organization=org)
    assert sub.status == Subscription.Status.ACTIVE
    assert SubscriptionEvent.objects.filter(
        subscription=sub, payment=submitted).exists()
    assert PlatformAuditLog.objects.filter(
        organization=org, action=PlatformAuditLog.Action.PAYMENT_VERIFIED).exists()
    # The customer is told, in an email that names the reference.
    assert any(submitted.payment_reference in m.subject or
               submitted.payment_reference in m.body for m in mail.outbox)


def test_approving_twice_does_not_extend_twice(ops, submitted, org):
    ops.post(_url(submitted, "approve"), {}, format="json")
    end = Subscription.objects.get(organization=org).current_period_end
    again = ops.post(_url(submitted, "approve"), {}, format="json")
    assert again.status_code == 400
    assert Subscription.objects.get(organization=org).current_period_end == end


def test_a_payment_another_operator_is_reviewing_is_not_taken(
        ops, submitted, django_user_model):
    with no_tenant():
        other = django_user_model.objects.create_user(
            username="ops2", email="ops2@platform.test", password="x-Pass-12345",
            is_platform_staff=True, organization=None)
    payments.claim_for_review(submitted, other)
    response = ops.post(_url(submitted, "approve"), {}, format="json")
    assert response.status_code == 400
    assert "ops2@platform.test" in response.json()["detail"]


# --- Part 13: rejection ----------------------------------------------------
def test_rejecting_needs_a_reason_and_the_customer_sees_it(ops, submitted, org,
                                                           tenant_admin_of_org):
    assert ops.post(_url(submitted, "reject"), {"reason": " "},
                    format="json").status_code == 400
    response = ops.post(_url(submitted, "reject"),
                        {"reason": "The amount on the receipt is NPR 999, not 9,990."},
                        format="json")
    assert response.status_code == 200
    customer = APIClient()
    customer.force_authenticate(user=tenant_admin_of_org)
    from tenancy import portal
    history = portal.payment_history(tenant_admin_of_org)
    row = next(r for r in history if r["reference"] == submitted.payment_reference)
    assert "NPR 999" in row["explanation"]
    assert row["next_step"] and row["can_submit_proof"] is True


# --- Request clarification -------------------------------------------------
def test_asking_for_more_information_lets_the_customer_resubmit(ops, submitted,
                                                                tenant_admin_of_org):
    response = ops.post(_url(submitted, "request-info"),
                        {"message": "The screenshot is cut off; please send the full receipt."},
                        format="json")
    assert response.status_code == 200
    assert response.json()["status"] == P.NEEDS_INFO
    from tenancy import portal
    row = next(r for r in portal.payment_history(tenant_admin_of_org)
               if r["reference"] == submitted.payment_reference)
    assert "cut off" in row["explanation"]
    assert row["can_submit_proof"] is True
    # The customer answers; it is back in the queue, the question cleared.
    payment = Payment.objects.get(pk=submitted.pk)
    payments.submit_proof(payment, method=Payment.Method.BANK_TRANSFER,
                          transaction_id="TXN-1b", paid_at=TODAY)
    payment.refresh_from_db()
    assert payment.status == P.SUBMITTED and payment.review_message == ""


def test_the_operator_can_open_the_receipt(ops, submitted):
    response = ops.get(_url(submitted, "proof"))
    assert response.status_code == 200
    assert b"".join(response.streaming_content).startswith(b"%PDF")
    assert response["X-Content-Type-Options"] == "nosniff"


def test_the_queue_says_who_sent_the_receipt(ops, org, annual_plan,
                                             tenant_admin_of_org):
    """Copied at submission: under row-level security the console cannot read
    the tenant's user row, so the requester's email came back empty live."""
    payment = payments.create_payment(org, annual_plan, today=TODAY)
    payments.submit_proof(payment, method=Payment.Method.ESEWA,
                          transaction_id="ES-1", actor=tenant_admin_of_org)
    row = next(r for r in ops.get("/api/v1/platform/payments/").json()
               if r["id"] == str(payment.pk))
    assert row["submitted_by"] == "admin@abc.test"


def test_the_queue_and_the_decisions_list(ops, submitted):
    queue = ops.get("/api/v1/platform/payments/").json()
    row = next(r for r in queue if r["id"] == str(submitted.pk))
    assert row["has_proof"] is True and row["plan_name"]
    ops.post(_url(submitted, "approve"), {}, format="json")
    decided = ops.get("/api/v1/platform/payments/?view=decided").json()
    assert decided[0]["id"] == str(submitted.pk)


# --- Part 8: payment methods -----------------------------------------------
def test_payment_methods_are_managed_in_the_console(ops, tenant_admin_of_org):
    created = ops.post("/api/v1/platform/payment-methods/", {
        "method": "khalti", "label": "Khalti", "khalti_id": "9800000000",
        "instructions": "Use your workspace address as the remark.\n<script>x</script>",
    }, format="json")
    assert created.status_code == 201, created.json()
    method_id = created.json()["id"]
    row = PaymentInstruction.objects.get(pk=method_id)
    assert "<script>" not in row.instructions_html

    from tenancy import portal
    shown = [m["label"] for m in portal.payment_instructions(tenant_admin_of_org)]
    assert "Khalti" in shown

    ops.post(f"/api/v1/platform/payment-methods/{method_id}/", {"state": "disabled"},
             format="json")
    shown = [m["label"] for m in portal.payment_instructions(tenant_admin_of_org)]
    assert "Khalti" not in shown

    ops.post(f"/api/v1/platform/payment-methods/{method_id}/", {"state": "archived"},
             format="json")
    listed = [m["id"] for m in ops.get("/api/v1/platform/payment-methods/").json()]
    assert method_id not in listed
    every = ops.get("/api/v1/platform/payment-methods/?archived=1").json()
    assert any(m["id"] == method_id and m["state"] == "archived" for m in every)

    ops.post(f"/api/v1/platform/payment-methods/{method_id}/", {"state": "active"},
             format="json")
    assert "Khalti" in [m["label"] for m in portal.payment_instructions(tenant_admin_of_org)]


@pytest.mark.parametrize("payload,expected", [
    ({"method": "bank_transfer", "label": "NIC"}, "bank name and account number"),
    ({"method": "esewa", "label": "eSewa"}, "eSewa ID"),
    ({"method": "qr", "label": "Scan"}, "QR code"),
    ({"method": "bitcoin", "label": "BTC"}, "payment type"),
])
def test_a_payment_method_must_be_usable(ops, payload, expected):
    response = ops.post("/api/v1/platform/payment-methods/", payload, format="json")
    assert response.status_code == 400
    assert expected in response.json()["detail"]


# --- Part 14: revenue ------------------------------------------------------
def test_the_dashboard_reports_cash_collected(ops, submitted):
    ops.post(_url(submitted, "approve"), {}, format="json")
    revenue = ops.get("/api/v1/platform/dashboard/").json()["revenue"]
    assert revenue["collected_this_month_minor"] == submitted.amount_minor
    assert revenue["arr_minor"] == revenue["mrr_minor"] * 12
