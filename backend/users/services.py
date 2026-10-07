"""User-related helpers (employee id generation)."""
from django.db import transaction
from django.utils import timezone

from .models import User


@transaction.atomic
def generate_employee_id(organization=None):
    """The next employee id for one tenant: NIFN-EMP-YYYY-XXXX for NIF.

    Phase S2 scoped the scan to the organization. It was already a "highest
    existing value" read rather than a counter row, so it never shared a
    counter -- but it did scan EVERY tenant's employee ids, which would have
    made one company's headcount visible in another company's next id.
    """
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = organization or active_organization()
    year = timezone.now().year
    prefix = numbering.format_number(
        "EMP", year=year, value=0, organization=organization)[:-4]
    last = (
        User.objects.select_for_update()
        .filter(organization=organization, employee_id__startswith=prefix)
        .order_by("-employee_id").first()
    )
    seq = int(last.employee_id.rsplit("-", 1)[-1]) + 1 if last and last.employee_id else 1
    return f"{prefix}{seq:04d}"
