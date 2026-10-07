"""Provision a tenant from the command line.

    python manage.py provision_tenant \
        --name "ABC School" --slug abcschool --prefix ABCS \
        --email admin@abcschool.edu.np --plan monthly \
        --admin-email head@abcschool.edu.np

THE SAME ENTRY POINT THE CONSOLE USES -- ``tenancy.console.create_organization``
-- so this is not a second implementation that can drift. The authority check
is included: the command requires an existing platform operator to act as, and
records them as the creator, because "who provisioned this customer" is a
question somebody will ask.

It exists for the two cases a web form is wrong for: a scripted migration of
several tenants at once, and a deployment where the console is not reachable
yet. It is NOT a way around provisioning -- it does exactly what the button
does, including the bootstrap and the verification.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tenancy import bootstrap, console
from tenancy.exceptions import TenancyError


class Command(BaseCommand):
    help = "Provision a complete, usable tenant."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--slug", required=True,
                            help="The workspace address. Permanent.")
        parser.add_argument("--prefix", required=True,
                            help="Document number prefix. Permanent.")
        parser.add_argument("--email", required=True,
                            help="Billing and platform-notice address.")
        parser.add_argument("--plan", help="Plan code. Defaults to the cheapest.")
        parser.add_argument("--trial-days", type=int)
        parser.add_argument("--as", dest="operator",
                            help="Email of the platform operator to act as. "
                                 "Defaults to the only one, if there is one.")
        parser.add_argument("--admin-email",
                            help="Create the tenant's first administrator.")
        parser.add_argument("--admin-name", default="")
        parser.add_argument(
            "--no-bootstrap", action="store_true",
            help="Skip the configuration seed. The tenant will NOT be usable "
                 "until something else supplies it.")

    def handle(self, *args, **options):
        operator = self._operator(options.get("operator"))

        from tenancy.models import Plan

        plan = None
        if options.get("plan"):
            try:
                plan = Plan.objects.get(code=options["plan"])
            except Plan.DoesNotExist:
                raise CommandError(f"No plan with code '{options['plan']}'.")

        try:
            organization = console.create_organization(
                operator,
                name=options["name"], slug=options["slug"],
                document_prefix=options["prefix"], email=options["email"],
                plan=plan, trial_days=options.get("trial_days"),
                bootstrap=not options["no_bootstrap"],
                admin_email=options.get("admin_email"),
                admin_name=options["admin_name"],
            )
        except TenancyError as exc:
            raise CommandError(str(exc))

        report = getattr(organization, "provisioning_report", {})
        created = report.get("bootstrap") or {}
        self.stdout.write(self.style.SUCCESS(
            f"{organization.name} ({organization.slug}) provisioned, "
            f"status {organization.status}."))
        for key, count in sorted(created.items()):
            self.stdout.write(f"  {key.replace('_', ' ')}: {count}")

        if report.get("admin_user"):
            # Printed once, never stored. The operator hands it over now.
            self.stdout.write(self.style.WARNING(
                f"  administrator {report['admin_user']} — temporary password: "
                f"{getattr(organization, '_admin_initial_password', '?')}"))

        gaps = bootstrap.verify_organization(organization)
        if gaps:
            self.stdout.write(self.style.ERROR(
                f"  INCOMPLETE — still missing: {gaps}"))
        else:
            self.stdout.write("  configuration complete; the tenant is usable.")

    @staticmethod
    def _operator(email):
        """The platform operator to act as. Required, and not inventable here.

        Provisioning records `created_by`, and a tenant whose creator is NULL
        is a tenant nobody is accountable for. If there is exactly one
        operator the choice is unambiguous; otherwise it has to be named.
        """
        from tenancy.context import no_tenant

        User = get_user_model()
        # Platform scope: these rows have organization IS NULL and are
        # invisible under RLS to a connection that has not declared it.
        with no_tenant():
            operators = list(User.all_tenants.filter(
                is_platform_staff=True, organization__isnull=True))

        if email:
            operator = next(
                (o for o in operators if o.email.lower() == email.lower()),
                None)
            if operator is None:
                raise CommandError(f"No platform operator with email {email}.")
            return operator

        count = len(operators)
        if count == 0:
            raise CommandError(
                "No platform operator exists. Create one first:\n"
                "    python manage.py create_platform_admin --email ...")
        if count > 1:
            raise CommandError(
                f"{count} platform operators exist; pass --as <email> to say "
                f"which one is provisioning this tenant.")
        return operators[0]
