"""Part 7: the platform console service layer.

No UI exists, by instruction. These tests pin the behaviour and, more
importantly, the authority boundary: every function must refuse a tenant user.
"""
import datetime
import uuid

import pytest

from tenancy import console
from tenancy.console import NotPlatformStaff
from tenancy.exceptions import TenancyError
from tenancy.models import Organization, Subscription

from .conftest import TODAY

pytestmark = pytest.mark.django_db

S = Subscription.Status


# --- the authority boundary --------------------------------------------
# Parameterised over every public function, so a function added later without
# a require_platform() call fails this test rather than shipping.
CONSOLE_CALLS = [
    ("dashboard", lambda actor, org, plan: console.dashboard(actor)),
    ("recent", lambda actor, org, plan: console.recent(actor)),
    ("revenue_summary", lambda actor, org, plan: console.revenue_summary(actor)),
    ("recent_payment_decisions",
     lambda actor, org, plan: console.recent_payment_decisions(actor)),
    ("approve_payment",
     lambda actor, org, plan: console.approve_payment(actor, "00000000-0000-0000-0000-000000000000")),
    ("reject_payment",
     lambda actor, org, plan: console.reject_payment(actor, "00000000-0000-0000-0000-000000000000", reason="x")),
    ("request_payment_information",
     lambda actor, org, plan: console.request_payment_information(actor, "00000000-0000-0000-0000-000000000000", message="x")),
    ("payment_proof",
     lambda actor, org, plan: console.payment_proof(actor, "00000000-0000-0000-0000-000000000000")),
    ("list_payment_methods", lambda actor, org, plan: console.list_payment_methods(actor)),
    ("save_payment_method",
     lambda actor, org, plan: console.save_payment_method(actor, {"method": "esewa", "label": "x", "esewa_id": "1"})),
    ("set_payment_method_state",
     lambda actor, org, plan: console.set_payment_method_state(actor, "00000000-0000-0000-0000-000000000000", "active")),
    ("plan_usage", lambda actor, org, plan: console.plan_usage(actor)),
    ("list_organizations",
     lambda actor, org, plan: console.list_organizations(actor)),
    ("organization_detail",
     lambda actor, org, plan: console.organization_detail(actor, org.slug)),
    ("suspend",
     lambda actor, org, plan: console.suspend(actor, org, reason="x")),
    ("activate", lambda actor, org, plan: console.activate(actor, org)),
    ("change_plan",
     lambda actor, org, plan: console.change_plan(actor, org, plan)),
    ("extend", lambda actor, org, plan: console.extend(actor, org, months=1)),
    ("verification_queue",
     lambda actor, org, plan: console.verification_queue(actor)),
    ("create_organization", lambda actor, org, plan: console.create_organization(
        actor, name="X", slug="xorg", document_prefix="X", email="x@y.test")),
    # --- Phase S6 ---
    ("revenue_forecast",
     lambda actor, org, plan: console.revenue_forecast(actor)),
    ("system_health", lambda actor, org, plan: console.system_health(actor)),
    ("update_organization",
     lambda actor, org, plan: console.update_organization(actor, org,
                                                          name="Renamed")),
    ("organization_usage",
     lambda actor, org, plan: console.organization_usage(actor, org)),
    ("tenant_health",
     lambda actor, org, plan: console.tenant_health(actor, org)),
    ("repair_configuration",
     lambda actor, org, plan: console.repair_configuration(actor, org)),
    ("cancel",
     lambda actor, org, plan: console.cancel(actor, org, reason="x")),
    ("set_organization_status",
     lambda actor, org, plan: console.set_organization_status(
         actor, org, Organization.Status.SUSPENDED)),
    ("assign_plan",
     lambda actor, org, plan: console.assign_plan(actor, org, plan)),
    ("start_trial", lambda actor, org, plan: console.start_trial(actor, org)),
    ("branding", lambda actor, org, plan: console.branding(actor, org)),
    ("set_branding",
     lambda actor, org, plan: console.set_branding(actor, org,
                                                   display_name="X")),
    ("set_branding_asset",
     lambda actor, org, plan: console.set_branding_asset(
         actor, org, "logo_login", None)),
    ("settings_for", lambda actor, org, plan: console.settings_for(actor, org)),
    ("set_settings",
     lambda actor, org, plan: console.set_settings(actor, org,
                                                   office_start=None)),
    ("audit_trail", lambda actor, org, plan: console.audit_trail(actor)),
    ("repair_mirrors", lambda actor, org, plan: console.repair_mirrors(actor)),
    # --- Phase S6.5 ---
    ("export_readiness",
     lambda actor, org, plan: console.export_readiness(actor, org)),
    ("create_export",
     lambda actor, org, plan: console.create_export(actor, org)),
    ("list_exports", lambda actor, org, plan: console.list_exports(actor, org)),
    ("get_export",
     lambda actor, org, plan: console.get_export(actor, org, uuid.uuid4())),
    ("record_export_download",
     lambda actor, org, plan: console.record_export_download(actor, None)),
    ("archive_organization",
     lambda actor, org, plan: console.archive_organization(actor, org,
                                                            reason="x")),
    ("restore_organization",
     lambda actor, org, plan: console.restore_organization(actor, org)),
    # --- Phase S6.75 / S7 ---
    ("launch_readiness",
     lambda actor, org, plan: console.launch_readiness(actor)),
    ("event_dashboard",
     lambda actor, org, plan: console.event_dashboard(actor)),
    ("registration_funnel",
     lambda actor, org, plan: console.registration_funnel(actor)),
]


@pytest.mark.parametrize("name,call", CONSOLE_CALLS, ids=[c[0] for c in CONSOLE_CALLS])
def test_a_tenant_user_is_refused_by_every_console_function(
        name, call, tenant_admin, org, monthly_plan):
    with pytest.raises(NotPlatformStaff):
        call(tenant_admin, org, monthly_plan)


@pytest.mark.parametrize("name,call", CONSOLE_CALLS, ids=[c[0] for c in CONSOLE_CALLS])
def test_a_django_superuser_is_refused_too(name, call, django_user_model, nif,
                                            org, monthly_plan):
    """Every admin guard here is an allow-list, including this one.

    The superuser is created inside `tenant_context(nif)` because TWO
    organizations exist in this test, and Phase S2's compatibility shim
    deliberately stops guessing at that point: `active_organization()` will not
    choose between two tenants, so a user created with no tenant in context
    fails `user_tenant_has_organization`. That is the designed behaviour, not a
    limitation -- it is what makes the shim safe to leave in place.
    """
    from tenancy.context import tenant_context

    with tenant_context(nif):
        root = django_user_model.objects.create_superuser(
            username="root", email="root@x.test", password="x-Root-1")
    with pytest.raises(NotPlatformStaff):
        call(root, org, monthly_plan)


def test_every_public_console_function_is_covered_by_the_boundary_test():
    """Keeps the parametrised list above honest as the module grows."""
    import inspect

    public = {
        name for name, obj in vars(console).items()
        if inspect.isfunction(obj)
        and not name.startswith("_")
        and obj.__module__ == "tenancy.console"
        and name != "require_platform"
    }
    covered = {name for name, _ in CONSOLE_CALLS}
    assert public == covered, (
        f"console functions with no authority test: {sorted(public - covered)}")


# --- Platform Dashboard ------------------------------------------------
def test_dashboard_counts_organizations_by_status(platform_user, nif, org):
    data = console.dashboard(platform_user)
    assert data["organizations_total"] == 2
    assert data["organizations_active"] == 1      # NIF
    assert data["organizations_trial"] == 1       # the provisioned one
    assert data["plan_usage"]["annual"] == 1
    assert data["plan_usage"]["monthly"] == 1


def test_dashboard_counts_pending_payment_verifications(platform_user, org,
                                                         annual_plan):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from tenancy import payments
    from tenancy.models import Payment

    assert console.dashboard(platform_user)["payments_pending_verification"] == 0

    payment = payments.create_payment(org, annual_plan, today=TODAY)
    payments.submit_proof(payment, method=Payment.Method.ESEWA,
                           transaction_id="T1", paid_at=TODAY,
                           proof=SimpleUploadedFile("r.pdf", b"x"))
    assert console.dashboard(platform_user)["payments_pending_verification"] == 1


# --- Organization Management -------------------------------------------
def test_create_organization_records_who_created_it(platform_user):
    org = console.create_organization(
        platform_user, name="XYZ Hospital", slug="xyzhospital",
        document_prefix="XYZH", email="admin@xyz.test")
    assert org.created_by == platform_user
    assert org.subscription.status == S.TRIAL
    assert org.settings and org.branding


def test_list_organizations_can_filter_and_search(platform_user, nif, org):
    assert console.list_organizations(platform_user).count() == 2
    assert console.list_organizations(platform_user, status=S.ACTIVE).count() == 1
    assert console.list_organizations(platform_user, search="ABC").count() == 1


def test_organization_detail_reports_no_mirror_drift(platform_user, nif):
    detail = console.organization_detail(platform_user, "nif")
    assert detail["organization"] == nif
    assert detail["mirror_drift"] is None


# --- mirror drift: detected AND corrected ------------------------------
def test_drift_is_detected_without_being_written_to(platform_user, org):
    """The detector is read-only, because the health check is a GET.

    A detector that quietly repairs cannot be run twice to confirm a finding,
    and a GET that writes is a GET somebody will put behind a refresh timer.
    """
    from tenancy import services

    Organization.objects.filter(pk=org.pk).update(
        subscription_status=S.SUSPENDED)
    org.refresh_from_db()

    assert services.reconcile_mirrors()[org.slug]
    org.refresh_from_db()
    assert org.subscription_status == S.SUSPENDED, (
        "reconcile_mirrors repaired the row it was only asked about")


def test_the_console_corrects_drift_and_says_whose(platform_user, org):
    """The gap: drift was detected, logged at ERROR, shown on the console --
    and nothing anywhere corrected it.

    That matters because the mirror is not cosmetic. Every request reads
    `Organization.subscription_status` to decide whether a tenant is admitted,
    the columns are `editable=False` so not even the admin could fix one by
    hand, and the remedy was therefore a shell.
    """
    truth = org.subscription.status
    Organization.objects.filter(pk=org.pk).update(
        subscription_status=S.SUSPENDED,
        subscription_expiry=datetime.date(2020, 1, 1))

    result = console.repair_mirrors(platform_user)

    assert org.slug in result["repaired"], result
    org.refresh_from_db()
    assert org.subscription_status == truth
    assert org.subscription_expiry == org.subscription.current_period_end
    # And it is now clean, which is the half a detector alone never reached.
    from tenancy import services
    assert services.reconcile_mirrors() == {}


def test_a_mirror_repair_is_audited_against_the_customer_it_changed(
        platform_user, org):
    """Findable on the tenant's own trail, not in a list of maintenance jobs.

    The question this answers is "why did this customer's access change on
    Tuesday", and that is read one organization at a time.
    """
    from tenancy.models import PlatformAuditLog

    Organization.objects.filter(pk=org.pk).update(
        subscription_status=S.SUSPENDED)
    console.repair_mirrors(platform_user)

    entry = PlatformAuditLog.objects.filter(
        organization=org,
        action=PlatformAuditLog.Action.SUBSCRIPTION_CHANGED).first()
    assert entry is not None, "a mirror repair left no trace"
    assert entry.actor_email == platform_user.email
    assert "mirror" in entry.note.lower()


def test_repairing_a_clean_platform_changes_and_records_nothing(
        platform_user, org):
    """An operator pressing the button twice must not fill the trail."""
    from tenancy.models import PlatformAuditLog

    before = PlatformAuditLog.objects.count()
    assert console.repair_mirrors(platform_user) == {"repaired": {}}
    assert PlatformAuditLog.objects.count() == before


# --- Tenant Management -------------------------------------------------
def test_suspend_locks_the_tenant_out_and_requires_a_reason(platform_user, org):
    with pytest.raises(TenancyError):
        console.suspend(platform_user, org, reason="  ")

    console.suspend(platform_user, org, reason="Non-payment, invoice 7")
    org.refresh_from_db()
    assert org.subscription_status == S.SUSPENDED
    assert org.is_admitted is False
    event = org.subscription_events.first()
    # `actor_id`: the actor is a PLATFORM operator, whose row has
    # `organization IS NULL` and cannot be dereferenced from a connection that
    # has not declared platform scope. The foreign key value is the fact.
    assert event.actor_id == platform_user.pk
    assert "invoice 7" in event.note


def test_suspension_deletes_nothing(platform_user, org, tenant_admin):
    """Suspension is a gate. Counted as the tenant whose data it is."""
    from tenancy.context import tenant_context

    with tenant_context(org):
        before = type(tenant_admin).objects.count()
    console.suspend(platform_user, org, reason="x")
    with tenant_context(org):
        assert type(tenant_admin).objects.count() == before
    assert Organization.objects.filter(pk=org.pk).exists()
    assert org.subscription_events.exists()


def test_activate_lets_a_suspended_tenant_back_in(platform_user, org):
    console.suspend(platform_user, org, reason="x")
    org.refresh_from_db()
    console.activate(platform_user, Organization.objects.get(pk=org.pk))
    org.refresh_from_db()
    assert org.subscription_status == S.ACTIVE
    assert org.is_admitted is True


# --- Subscription Management -------------------------------------------
def test_change_plan_pins_the_new_price_and_records_the_event(
        platform_user, org, annual_plan):
    console.change_plan(platform_user, org, annual_plan)
    org.refresh_from_db()
    sub = org.subscription
    assert sub.plan == annual_plan
    assert sub.plan_price == annual_plan.prices.first()
    event = org.subscription_events.first()
    assert event.event == "plan_changed"
    assert event.to_plan == annual_plan


def test_extend_adds_paid_time_without_a_payment(platform_user, org):
    from tenancy.periods import add_months

    before = org.subscription.current_period_end
    console.extend(platform_user, org, months=3, note="goodwill")
    org.refresh_from_db()
    assert org.subscription.current_period_end == add_months(before, 3)
    assert org.subscription_events.first().event == "extended"


def test_extend_refuses_a_nonsense_duration(platform_user, org):
    with pytest.raises(TenancyError):
        console.extend(platform_user, org, months=0)


# --- Payment Verification ----------------------------------------------
def test_the_verification_queue_is_the_payments_service(platform_user):
    assert list(console.verification_queue(platform_user)) == []


# --- the console is the only cross-tenant reader -----------------------
def test_console_functions_run_with_no_tenant_in_context(platform_user, nif, org):
    """Crossing tenants is explicit here and nowhere else.

    After the call, the context must be clean -- a console query must not leave
    a tenant id (or the absence of one) set for whatever runs next.
    """
    from tenancy.context import current_org_id, tenant_context

    with tenant_context(nif):
        console.dashboard(platform_user)
        assert current_org_id() == nif.pk
    assert current_org_id() is None
