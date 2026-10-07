"""Create the first platform operator.

    python manage.py create_platform_admin --email ops@platform.test

THE ONE BOOTSTRAP THAT CANNOT BE DONE IN THE PRODUCT, and that is why it is a
command. Every other account on the platform is created by somebody: a
tenant's employees by their administrator, a tenant's first administrator by a
platform operator. The FIRST platform operator has nobody above them, so it
has to come from the deployment.

NOT `createsuperuser`, and the difference is the point. A Django superuser is
not a platform operator -- every admin guard in this project is an allow-list,
and `IsPlatformStaff` deliberately refuses `is_staff` and `is_superuser` so
that someone given Django admin access for an unrelated reason does not also
get the platform console. A platform account is a structural thing:
``is_platform_staff = True`` AND ``organization IS NULL``, which the database
enforces as a constraint.

The password is read from a prompt or --password, never from an argument
default, and the account is marked must_change_password unless told otherwise.
"""
import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tenancy import platform_audit
from tenancy.models import PlatformAuditLog


class Command(BaseCommand):
    help = "Create a platform operator (is_platform_staff, no organization)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--username", help="Defaults to the email local part.")
        parser.add_argument("--name", default="", help="Full name.")
        parser.add_argument(
            "--password",
            help="Read from a prompt if omitted. Avoid on a shared shell: an "
                 "argument is visible in the process list and the history.")
        parser.add_argument(
            "--no-password-change", action="store_true",
            help="Do not force a password change at first sign-in.")

    @transaction.atomic
    def handle(self, *args, **options):
        # PLATFORM SCOPE for the whole command (Phase S6). A platform account
        # has organization IS NULL, and under row-level security that row is
        # invisible to -- and unwritable by -- a connection that has not said
        # it is serving the platform. Without this the command reads nothing
        # and its INSERT is refused by the policy's WITH CHECK.
        from tenancy.context import no_tenant

        with no_tenant():
            self._create(options)

    def _create(self, options):
        User = get_user_model()
        email = options["email"].strip().lower()
        username = (options["username"]
                    or email.split("@")[0])[:150]

        # all_tenants, not objects. The default manager is tenant-scoped, and
        # this command runs with no tenant in context at all -- so once
        # TENANCY_ENABLED is on, `User.objects` raises TenantScopeMissing
        # rather than answering, and the command could not check for a
        # duplicate or create anything. (While tenancy is off the scoped
        # manager degrades to unfiltered and would happen to work, which is
        # exactly the kind of accident that breaks on the day the flag flips.)
        if User.all_tenants.filter(email__iexact=email).exists():
            raise CommandError(f"An account already uses {email}.")
        if User.all_tenants.filter(username=username,
                                   organization__isnull=True).exists():
            raise CommandError(
                f"A platform account is already called '{username}'. "
                f"Pass --username.")

        password = options["password"]
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("The two passwords did not match.")
        if not password:
            raise CommandError("A password is required.")

        first, _, last = (options["name"] or "").partition(" ")
        user = User(
            username=username, email=email,
            first_name=first or "Platform", last_name=last or "Operator",
            # A platform account belongs to NO organization. The database
            # refuses any other combination.
            organization=None, is_platform_staff=True,
            # Deliberately NOT a Django superuser or staff member: see the
            # module docstring.
            is_staff=False, is_superuser=False,
            must_change_password=not options["no_password_change"],
        )
        user.set_password(password)
        user.save()

        platform_audit.record(
            None, PlatformAuditLog.Action.ADMIN_USER_CREATED,
            organization=None, changes={"email": email, "platform": True},
            note="Platform operator created from the command line.")

        self.stdout.write(self.style.SUCCESS(
            f"Platform operator {email} created."))
        if user.must_change_password:
            self.stdout.write(
                "They must change this password at first sign-in.")
