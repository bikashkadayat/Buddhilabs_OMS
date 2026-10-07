"""``Organization.seat_count`` and ``storage_bytes``: maintained, and reconcilable.

These two columns exist so the platform dashboard can show a headcount without
joining onto ``users_user`` -- a table that carries a row-level-security
policy, which the console queries with no tenant bound. A join there returns 0
for every tenant, uniformly and silently.

So the tests here are really about one claim: the platform console needs NO
privilege over tenant data. If the counters are wrong, the only remaining fix
is a BYPASSRLS credential in the web tier, which is the trade this avoids.
"""
import pytest

from tenancy import counters
from tenancy.context import tenant_context

pytestmark = pytest.mark.django_db


def _user(organization, username, **kwargs):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    with tenant_context(organization):
        user = User(username=username,
                    email=f"{username}@{organization.slug}.test",
                    organization=organization, **kwargs)
        user.set_password("x-Counter-1")
        user.save()
    return user


# --- incremental maintenance -------------------------------------------
def test_creating_a_user_takes_a_seat(django_capture_on_commit_callbacks, org):
    before = org.seat_count
    with django_capture_on_commit_callbacks(execute=True):
        _user(org, "seat-one")
    org.refresh_from_db()
    assert org.seat_count == before + 1


def test_deactivating_a_user_frees_a_seat(django_capture_on_commit_callbacks,
                                           org):
    with django_capture_on_commit_callbacks(execute=True):
        user = _user(org, "seat-two")
    org.refresh_from_db()
    taken = org.seat_count

    # Saved AS the tenant that owns the row. Under row-level security a
    # connection bound elsewhere cannot update another tenant's user, which is
    # the boundary working -- in production the save happens inside the
    # tenant's own request.
    with django_capture_on_commit_callbacks(execute=True), tenant_context(org):
        user.is_active = False
        user.save()
    org.refresh_from_db()
    assert org.seat_count == taken - 1

    with django_capture_on_commit_callbacks(execute=True), tenant_context(org):
        user.is_active = True
        user.save()
    org.refresh_from_db()
    assert org.seat_count == taken


def test_an_ordinary_profile_edit_does_not_move_the_counter(
        django_capture_on_commit_callbacks, org):
    """Without a pre-save diff, every phone-number change looked like a hire.

    The counter would climb by one each time anybody edited anything.
    """
    with django_capture_on_commit_callbacks(execute=True):
        user = _user(org, "seat-three")
    org.refresh_from_db()
    taken = org.seat_count

    for index in range(3):
        with django_capture_on_commit_callbacks(execute=True), tenant_context(org):
            user.phone = f"+977-1-000000{index}"
            user.save()
    org.refresh_from_db()
    assert org.seat_count == taken


def test_a_platform_account_takes_nobodys_seat(
        django_capture_on_commit_callbacks, org, django_user_model):
    from tenancy.context import no_tenant

    before = org.seat_count
    with django_capture_on_commit_callbacks(execute=True), no_tenant():
        django_user_model.objects.create_user(
            username="ops-two", email="ops2@platform.test",
            password="x-Platform-1", is_platform_staff=True, organization=None)
    org.refresh_from_db()
    assert org.seat_count == before


def test_a_rolled_back_user_creation_does_not_leave_the_counter_raised(org):
    """The adjustment runs on_commit, so an abandoned transaction costs nothing.

    Inside the transaction it would also hold a row lock on the Organization
    for the rest of the request, serialising two hires in the same tenant.
    """
    from django.db import transaction

    before = org.seat_count
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            _user(org, "seat-rollback")
            raise RuntimeError("abandon")
    org.refresh_from_db()
    assert org.seat_count == before


def test_one_tenants_hire_does_not_move_anothers_counter(
        django_capture_on_commit_callbacks, org, nif):
    before = nif.seat_count
    with django_capture_on_commit_callbacks(execute=True):
        _user(org, "seat-isolated")
    nif.refresh_from_db()
    assert nif.seat_count == before


# --- reconciliation ----------------------------------------------------
def test_the_reconciler_corrects_drift_from_a_bulk_create(org):
    """``bulk_create`` emits no post_save, so the counter cannot see it.

    Drift is therefore possible by design; this is why the command exists.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    with tenant_context(org):
        User.objects.bulk_create([
            User(username=f"bulk-{index}",
                 email=f"bulk-{index}@{org.slug}.test", organization=org)
            for index in range(4)
        ])

    org.refresh_from_db()
    stale = org.seat_count
    # Counted AS the tenant: `seats_for` reads users_user, so a connection
    # bound elsewhere sees none of them and the drift would look like -stale
    # rather than +4. `refresh_all` binds each tenant in turn for exactly
    # this reason.
    with tenant_context(org):
        assert counters.seats_for(org) == stale + 4, (
            "expected the counter to be behind after a bulk_create")

    counters.refresh_all(storage=False)
    org.refresh_from_db()
    with tenant_context(org):
        assert org.seat_count == counters.seats_for(org)


def test_the_management_command_reports_drift_without_writing(org, capsys):
    from django.core.management import call_command

    from tenancy.models import Organization

    Organization.objects.filter(pk=org.pk).update(seat_count=999)
    call_command("tenancy_refresh_counters", "--slug", org.slug, "--check",
                  "--no-storage")
    output = capsys.readouterr().out
    assert "999" in output

    org.refresh_from_db()
    assert org.seat_count == 999, "--check must not write"

    call_command("tenancy_refresh_counters", "--slug", org.slug, "--no-storage")
    org.refresh_from_db()
    with tenant_context(org):
        assert org.seat_count == counters.seats_for(org)


def test_the_counter_never_goes_negative(org):
    """seat_count is a PositiveIntegerField, so this is a CHECK constraint.

    Without the SQL-level clamp, a decrement past zero raises IntegrityError
    and takes the user deletion that triggered it down too -- so a drifted
    counter would make it impossible to remove an employee.
    """
    counters.bump_seats(org.pk, -50)
    org.refresh_from_db()
    assert org.seat_count == 0


def test_deleting_a_user_succeeds_even_if_the_counter_has_drifted_low(
        django_capture_on_commit_callbacks, org):
    from tenancy.models import Organization

    with django_capture_on_commit_callbacks(execute=True):
        user = _user(org, "drifted")
    Organization.objects.filter(pk=org.pk).update(seat_count=0)

    with django_capture_on_commit_callbacks(execute=True):
        user.delete()
    org.refresh_from_db()
    assert org.seat_count == 0


# --- what the dashboard reads ------------------------------------------
def test_the_seat_summary_touches_no_tenant_table(org, nif):
    """The whole point. Three aggregates on `tenancy_organization`, no joins.

    A join onto a policed table would be the bug this module exists to
    prevent, and it would be invisible -- the numbers would simply all be 0
    once RLS was enforced.
    """
    from django.db import connection

    counters.refresh_all(storage=False)
    recorder = _RecordTables()
    with connection.execute_wrapper(recorder):
        counters.seat_summary()
    joined = {table for sql in recorder.statements
              for table in ("users_user", "leaves_leave", "tasks_task")
              if table in sql}
    assert not joined, f"seat_summary read tenant table(s): {joined}"


class _RecordTables:
    def __init__(self):
        self.statements = []

    def __call__(self, execute, sql, params, many, context):
        self.statements.append(sql)
        return execute(sql, params, many, context)


def test_the_seat_summary_totals_match_the_rows(org, nif):
    counters.refresh_all(storage=False)
    summary = counters.seat_summary()
    from tenancy.models import Organization

    assert summary["organizations"] == Organization.objects.count()
    assert summary["seats"] == sum(
        Organization.objects.values_list("seat_count", flat=True))


def test_platform_storage_is_not_charged_to_a_tenant():
    """A tenant must not be charged for storage the platform keeps about them.

    Payment proofs and branding assets are platform-held, which is why they
    are absent from STORAGE_SOURCES -- asserted here so a later addition has
    to be deliberate.
    """
    counted = {f"{label}.{field}" for label, field in counters.STORAGE_SOURCES}
    for excluded in ("tenancy.Payment.proof",
                     "tenancy.Organization.logo",
                     "tenancy.OrganizationBranding.logo_primary"):
        assert excluded not in counted


def test_every_storage_source_names_a_real_field():
    """A typo here would silently under-count forever."""
    from django.apps import apps

    for label, field_name in counters.STORAGE_SOURCES:
        app_label, model_name = label.split(".")
        model = apps.get_model(app_label, model_name)
        field = model._meta.get_field(field_name)
        assert hasattr(field, "storage"), (
            f"{label}.{field_name} is not a file field")
        assert any(f.name == "organization" for f in model._meta.get_fields()), (
            f"{label} has no organization column, so its files cannot be "
            f"attributed to a tenant")
