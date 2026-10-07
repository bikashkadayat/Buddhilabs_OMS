"""Create (idempotently) an initial admin account for bootstrapping."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from users.models import User
from users.services import generate_employee_id


class Command(BaseCommand):
    help = "Create an initial admin user (must change password on first login)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="admin@nif.org.np")
        parser.add_argument("--password", default="ChangeMe123!")
        parser.add_argument("--name", default="System Administrator")

    def handle(self, *args, **options):
        email = options["email"]
        existing = User.objects.filter(email=email).first()
        if existing:
            self.stdout.write(self.style.WARNING(f"Admin {email} already exists ({existing.employee_id})."))
            return

        first, _, last = options["name"].partition(" ")
        user = User(
            username=email.split("@")[0],
            email=email,
            first_name=first,
            last_name=last,
            role=User.Roles.ADMIN,
            is_staff=True,
            is_superuser=True,
            employee_id=generate_employee_id(),
            must_change_password=True,
            date_of_joining=timezone.now().date(),
        )
        user.set_password(options["password"])
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"Admin created: {email} / {options['password']} ({user.employee_id}). "
            f"You will be prompted to change the password on first login."
        ))
