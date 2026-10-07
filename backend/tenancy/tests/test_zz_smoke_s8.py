import datetime
import pytest
from rest_framework.test import APIClient
from tenancy.context import tenant_context
pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_admin_user(db, org):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    with tenant_context(org):
        u = User.objects.create_user(username="org-admin", email="admin@abc.test",
                                     password="x-Admin-1", role="admin",
                                     organization=org)
    return u


def test_smoke(org, tenant_admin_user, monthly_plan, annual_plan, platform_user):
    api = APIClient()
    api.force_authenticate(tenant_admin_user)

    r = api.get("/api/v1/tenant/subscription/")
    print("SUBSCRIPTION:", r.status_code)
    import json
    print(json.dumps(r.json(), indent=1, default=str)[:700])

    r = api.get("/api/v1/tenant/plans/")
    print("PLANS:", r.status_code, [(p["code"], p["amount_minor"], p["monthly_equivalent_minor"]) for p in r.json()])

    r = api.get("/api/v1/tenant/payment-instructions/")
    print("INSTRUCTIONS:", r.status_code, len(r.json()))

    r = api.post("/api/v1/tenant/subscription/request/", {"plan_code": "annual"}, format="json")
    print("REQUEST:", r.status_code, r.json())
    ref = r.json()["reference"]

    r = api.post(f"/api/v1/tenant/payments/{ref}/proof/",
                 {"method": "bank_transfer", "transaction_id": "TXN-1",
                  "paid_at": str(datetime.date(2026, 6, 1)), "payer_note": "paid"},
                 format="multipart")
    print("PROOF:", r.status_code, r.json())

    r = api.get("/api/v1/tenant/payments/")
    print("HISTORY:", r.status_code, [(p["reference"], p["status"], p["explanation"][:40]) for p in r.json()])

    # platform verifies
    from tenancy import payments as pay
    from tenancy.models import Payment
    p = Payment.objects.get(payment_reference=ref)
    pay.claim_for_review(p, platform_user)
    p, sub = pay.verify_payment(p, platform_user)
    print("VERIFIED:", p.status, sub.status, sub.plan.code, sub.current_period_end)

    r = api.get("/api/v1/tenant/subscription/")
    print("AFTER:", r.json()["plan"]["code"], r.json()["status"], r.json()["explanation"][:60])
