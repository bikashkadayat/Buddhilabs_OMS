"""Seed the four subscription plans and their opening prices.

PRICES ARE DATA, NOT CODE. Nothing in the application names an amount; a price
is a ``PlanPrice`` row selected by date (``tenancy.plans.current_price``). This
migration establishes the opening set and nothing else -- platform staff change
a price afterwards by INSERTING a new PlanPrice with a later ``effective_from``,
never by editing one of these rows. See the PlanPrice docstring for why.

Amounts are integer minor units (paisa):

    monthly      NPR   999  =  99900   999.00/month
    quarterly    NPR 2,599  = 259900   866.33/month   -13.3%
    halfyearly   NPR 4,999  = 499900   833.17/month   -16.6%
    annual       NPR 9,990  = 999000   832.50/month   -16.7%

NOTE FOR WHOEVER OWNS PRICING: the 6- and 12-month plans are within 0.08% of
each other per month, so the annual commitment buys a customer essentially
nothing. That is a commercial decision, not a technical one, and changing it is
a new PlanPrice row rather than a code change -- which is the point of this
design. Flagged in the S0 report; seeded exactly as specified.

Idempotent (``update_or_create`` on the natural key) and reversible.
"""
import datetime
import uuid

from django.db import migrations

# code, name, interval_months, trial_days, grace_days, amount_minor, sort_order
PLANS = [
    ("monthly",    "Monthly",   1,  14, 7,  99900, 10),
    ("quarterly",  "3 Months",  3,  14, 7, 259900, 20),
    ("halfyearly", "6 Months",  6,  14, 7, 499900, 30),
    ("annual",     "12 Months", 12, 14, 7, 999000, 40),
]

# The date the opening price set takes effect. Deliberately early and fixed, so
# every environment resolves the same price for the same plan regardless of when
# the migration happens to run.
PRICE_EFFECTIVE_FROM = datetime.date(2020, 1, 1)
CURRENCY = "NPR"

# Payment methods the platform accepts in V1. Seeded INACTIVE on purpose: the
# account numbers and eSewa id are real-world facts this migration must not
# invent, and showing a customer blank transfer details would send real money
# nowhere. Platform staff fill them in and activate them.
INSTRUCTIONS = [
    ("esewa", "eSewa"),
    ("bank_transfer", "Bank Transfer"),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("tenancy", "Plan")
    PlanPrice = apps.get_model("tenancy", "PlanPrice")
    PaymentInstruction = apps.get_model("tenancy", "PaymentInstruction")

    for code, name, months, trial, grace, amount, order in PLANS:
        plan, _ = Plan.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "interval_months": months,
                "trial_days": trial,
                "grace_days": grace,
                "billing_mode": "flat",
                "is_public": True,
                "is_active": True,
                "sort_order": order,
            },
        )
        # get_or_create, not update_or_create: a PlanPrice is immutable. If this
        # row already exists -- a re-run, or a replayed migration -- it is left
        # exactly as it was rather than being rewritten under the
        # subscriptions that already cite it.
        PlanPrice.objects.get_or_create(
            plan=plan, currency=CURRENCY, effective_from=PRICE_EFFECTIVE_FROM,
            defaults={"id": uuid.uuid4(), "amount_minor": amount},
        )

    for method, label in INSTRUCTIONS:
        PaymentInstruction.objects.get_or_create(
            method=method,
            defaults={
                "id": uuid.uuid4(),
                "label": label,
                "is_active": False,
                "instructions_html": "",
            },
        )


def unseed(apps, schema_editor):
    """Remove only what this migration created, and only if unused.

    A plan with subscriptions or payments against it is left alone: PROTECT on
    those foreign keys would refuse the delete anyway, and failing a reverse
    migration on live data is worse than leaving four rows behind.
    """
    Plan = apps.get_model("tenancy", "Plan")
    PlanPrice = apps.get_model("tenancy", "PlanPrice")
    PaymentInstruction = apps.get_model("tenancy", "PaymentInstruction")

    codes = [code for code, *_ in PLANS]
    PlanPrice.objects.filter(
        plan__code__in=codes, effective_from=PRICE_EFFECTIVE_FROM,
        subscriptions__isnull=True, payments__isnull=True).delete()
    Plan.objects.filter(code__in=codes, subscriptions__isnull=True,
                        payments__isnull=True).delete()
    PaymentInstruction.objects.filter(
        method__in=[m for m, _ in INSTRUCTIONS], is_active=False).delete()


class Migration(migrations.Migration):

    dependencies = [("tenancy", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
