"""The two command-line bootstraps, and why each one has to exist.

``create_platform_admin`` closes the only gap the product cannot close
itself: every account on the platform is created by somebody, and the FIRST
platform operator has nobody above them.

``provision_tenant`` is the console's own entry point with a CLI in front of
it -- for a scripted migration of several tenants, and for a deployment where
the console is not reachable yet. The test that matters is that it is NOT a
second implementation.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from tenancy import bootstrap
from tenancy.context import no_tenant, tenant_context
from tenancy.models import Organization, PlatformAuditLog

pytestmark = pytest.mark.django_db

User = get_user_model()


# --- create_platform_admin --------------------------------------------
#
# READING a platform account is platform-scope work too, not just writing one.
# The row has `organization IS NULL`, and under row-level security that is
# invisible to a connection which has not declared platform scope -- so these
# assertions go inside `no_tenant()`, exactly as the command and the
# middleware do on the real paths.
def test_it_creates_a_structurally_correct_platform_account():
    call_command("create_platform_admin", email="ops@platform.test",
                 password="x-Platform-99", name="Ops Person")

    with no_tenant():
        user = User.all_tenants.get(email="ops@platform.test")
    assert user.is_platform_staff is True
    assert user.organization_id is None
    assert user.must_change_password is True
    assert user.check_password("x-Platform-99")


def test_a_platform_operator_is_not_a_django_superuser():
    """Every admin guard here is an allow-list, including this one.

    Widening it to Django's staff flag would hand the platform console to
    anybody given admin access for an unrelated reason -- so the command must
    not quietly set those flags either.
    """
    call_command("create_platform_admin", email="ops2@platform.test",
                 password="x-Platform-99")
    with no_tenant():
        user = User.all_tenants.get(email="ops2@platform.test")
    assert user.is_staff is False
    assert user.is_superuser is False


def test_it_refuses_a_duplicate_email(platform_user):
    with pytest.raises(CommandError):
        call_command("create_platform_admin", email=platform_user.email,
                     password="x-Platform-99")


def test_it_still_works_once_tenancy_is_enforced(settings):
    """The command must not depend on the tenant-scoped manager degrading.

    It runs with no tenant in context, and once TENANCY_ENABLED is on the
    scoped default manager REFUSES to answer rather than returning every row
    -- which is the behaviour that makes the scoping safe. So the command has
    to use `all_tenants` explicitly. While the flag is off, `User.objects`
    degrades to unfiltered and would happen to work, which is exactly the kind
    of accident that breaks on the day the flag flips.
    """
    from tenancy.exceptions import TenantScopeMissing

    settings.TENANCY_ENABLED = True

    # The proof that the escape hatch is load-bearing here, not decorative.
    with pytest.raises(TenantScopeMissing):
        User.objects.filter(email="dup@platform.test").exists()

    call_command("create_platform_admin", email="dup@platform.test",
                 password="x-Platform-99")
    with no_tenant():
        assert User.all_tenants.filter(email="dup@platform.test").count() == 1

    # And the duplicate check still works, so a second attempt is a readable
    # error rather than an integrity error from the driver.
    with pytest.raises(CommandError):
        call_command("create_platform_admin", email="dup@platform.test",
                     password="x-Platform-99")


def test_the_creation_is_recorded_in_the_platform_trail():
    call_command("create_platform_admin", email="ops3@platform.test",
                 password="x-Platform-99")
    assert PlatformAuditLog.objects.filter(
        action=PlatformAuditLog.Action.ADMIN_USER_CREATED,
        actor_email="").exists()


# --- provision_tenant --------------------------------------------------
def test_it_provisions_a_usable_tenant(platform_user, monthly_plan):
    call_command("provision_tenant", name="CLI Co", slug="clico",
                 prefix="CLI", email="billing@cli.test", plan="monthly")

    organization = Organization.objects.get(slug="clico")
    assert organization.status == Organization.Status.TRIAL
    assert organization.is_admitted is True
    assert bootstrap.verify_organization(organization) == {}
    # Accountability: a tenant whose creator is NULL is one nobody owns.
    assert organization.created_by_id == platform_user.pk


def test_it_can_create_the_first_administrator(platform_user, monthly_plan):
    call_command("provision_tenant", name="CLI Two", slug="clitwo",
                 prefix="CLI2", email="billing@cli2.test",
                 admin_email="head@cli2.test", admin_name="Head Teacher")

    organization = Organization.objects.get(slug="clitwo")
    # A TENANT user this time, so it is read as that tenant.
    with tenant_context(organization):
        admin = User.objects.get(role="admin")
    assert admin.email == "head@cli2.test"
    assert admin.must_change_password is True


def test_it_refuses_to_run_with_no_platform_operator(db, monthly_plan):
    """Provisioning records who did it, and that is not inventable here."""
    with pytest.raises(CommandError) as exc:
        call_command("provision_tenant", name="Nobody", slug="nobodyco",
                     prefix="NOB", email="a@nobody.test")
    assert "create_platform_admin" in str(exc.value)
    assert not Organization.objects.filter(slug="nobodyco").exists()


def test_it_refuses_to_guess_between_two_operators(platform_user, monthly_plan):
    call_command("create_platform_admin", email="ops9@platform.test",
                 password="x-Platform-99")
    with pytest.raises(CommandError) as exc:
        call_command("provision_tenant", name="Ambiguous", slug="ambig",
                     prefix="AMB", email="a@ambig.test")
    assert "--as" in str(exc.value)

    # Named explicitly, it goes through and records the right operator.
    # `--as` has dest="operator"; call_command keys off the dest, not the flag.
    call_command("provision_tenant", name="Ambiguous", slug="ambig",
                 prefix="AMB", email="a@ambig.test",
                 operator="ops9@platform.test")
    with no_tenant():
        assert Organization.objects.get(slug="ambig").created_by.email \
            == "ops9@platform.test"


def test_it_refuses_an_unknown_plan(platform_user):
    with pytest.raises(CommandError):
        call_command("provision_tenant", name="Bad Plan", slug="badplan",
                     prefix="BAD", email="a@bad.test", plan="platinum")


def test_no_bootstrap_says_so_rather_than_pretending(platform_user,
                                                      monthly_plan, capsys):
    call_command("provision_tenant", name="Bare CLI", slug="barecli",
                 prefix="BARE", email="a@bare.test", no_bootstrap=True)
    output = capsys.readouterr().out
    assert "INCOMPLETE" in output
    organization = Organization.objects.get(slug="barecli")
    assert bootstrap.verify_organization(organization) != {}


def test_the_command_goes_through_the_consoles_authority_check(platform_user,
                                                                monthly_plan):
    """Not a second implementation. It calls console.create_organization.

    So the authority check, the audit entry and the bootstrap all happen for a
    CLI caller exactly as they do for a request -- which is the reason the
    check lives in the service layer and not in a DRF permission class.
    """
    call_command("provision_tenant", name="Audited CLI", slug="auditcli",
                 prefix="ACLI", email="a@acli.test")
    organization = Organization.objects.get(slug="auditcli")
    actions = set(PlatformAuditLog.objects
                  .filter(organization=organization)
                  .values_list("action", flat=True))
    assert PlatformAuditLog.Action.TENANT_CREATED in actions
    assert PlatformAuditLog.Action.TENANT_BOOTSTRAPPED in actions
