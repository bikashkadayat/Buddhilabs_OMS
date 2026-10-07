"""``Organization.seat_count`` and ``storage_bytes``: who maintains them, and why.

These two columns were modelled in Phase S1 and never written. Phase S6 needs
them for the platform dashboard, and the obvious implementation -- have the
dashboard count -- is the one thing that must not happen, for a reason that
only became true in Phase S5.

WHY THE DASHBOARD CANNOT COUNT
------------------------------
``Count("users")`` on the organization list is a join onto ``users_user``,
which carries a row-level-security policy. The platform console queries with
NO tenant bound (``no_tenant()``), so that join matches nothing and every
tenant's headcount renders as 0 -- a dashboard that is silently, uniformly
wrong rather than broken.

The fix people reach for is to give the console a ``BYPASSRLS`` database role.
That works and it is the wrong trade: it puts a credential in the web tier
that can read every tenant's leave records, to render a number. Keeping these
counters maintained means the platform console needs no privilege over tenant
data at all -- which is what closes risk R20 by removing the requirement
instead of satisfying it.

THE TWO ARE MAINTAINED DIFFERENTLY, ON PURPOSE
----------------------------------------------
``seat_count`` is cheap and billing-relevant, so it is updated on every user
insert, deactivation and delete. It is a plain ``UPDATE ... SET seat_count``
computed by the database, never a read-modify-write in Python, so two
concurrent user creations cannot lose one another's increment.

``storage_bytes`` is expensive -- it means summing eleven file columns across
the tenant -- and nothing bills on it minute to minute. It is recomputed by
``manage.py tenancy_refresh_counters``, which the existing cron container
runs, and the console displays it with the time it was computed rather than
pretending it is live. A number labelled with its age is honest; a stale
number labelled "now" is not.
"""
import logging

from django.db import transaction
from django.db.models import Count, Q

logger = logging.getLogger(__name__)

# The file columns that make up a tenant's footprint. Named explicitly rather
# than discovered by walking every FileField on every model: the point of the
# list is that a reviewer can see what is counted, and three of the columns
# that exist are deliberately NOT counted (see below).
STORAGE_SOURCES = [
    ("memos.MemoAttachment", "file"),
    ("minutes.MinuteAttachment", "file"),
    ("circulars.CircularAttachment", "file"),
    ("tasks.TaskAttachment", "file"),
    ("users.User", "profile_photo"),
    ("reports.ReportRun", "file"),
    ("inventory.InventoryItem", "photo"),
    ("inventory.InventoryItem", "document"),
    ("inventory.AssetTransferAttachment", "file"),
    ("inventory.AssetDisposalAttachment", "file"),
]

# NOT counted, and each for a reason:
#
#   tenancy.Payment.proof          platform-held, outside the tenant tree
#   tenancy.Organization.logo      branding, not tenant content
#   tenancy.OrganizationBranding.* same
#
# A tenant must not be charged for storage the platform keeps about them.


def seats_for(organization):
    """Active, non-platform user count for one tenant. Reads ``users_user``.

    Called from the tenant's own context (a signal fires inside the request
    that created the user), so RLS is satisfied.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return (User.all_tenants
            .filter(organization=organization, is_active=True,
                    is_platform_staff=False)
            .count())


def refresh_seats(organization):
    """Recount and store. Returns the new count."""
    from .models import Organization

    count = seats_for(organization)
    Organization.objects.filter(pk=organization.pk).update(seat_count=count)
    return count


def bump_seats(organization_id, delta):
    """Adjust by ``delta`` in the database, never in Python.

    ``F("seat_count") + delta`` is one statement, so two users created at the
    same instant cannot each read 7 and each write 8.

    CLAMPED IN SQL, not afterwards. ``seat_count`` is a
    ``PositiveIntegerField``, which means a database CHECK constraint -- so a
    decrement that would go below zero does not produce a negative number to
    tidy up later, it raises ``IntegrityError`` and takes the surrounding
    delete with it. Deactivating a user would fail on a counter. GREATEST
    inside the UPDATE keeps the clamp and the increment in one atomic
    statement.

    The clamp is a floor, not a repair: reaching it means the counter had
    drifted, and ``manage.py tenancy_refresh_counters --check`` is what
    reports that.
    """
    from django.db.models import F, Value
    from django.db.models.functions import Greatest

    from .models import Organization

    if not organization_id or not delta:
        return
    Organization.objects.filter(pk=organization_id).update(
        seat_count=Greatest(F("seat_count") + delta, Value(0)))


def storage_for(organization):
    """Total bytes of tenant-owned uploads. EXPENSIVE -- stats every file.

    File sizes are not stored in the database by any of these models, so this
    has to ask the storage backend for each one. That is why it is a cron job
    and not a page load. Missing files are counted as zero and logged: a row
    pointing at a file that is gone is a data-integrity problem, not a reason
    to fail the whole recount.
    """
    from django.apps import apps

    total = 0
    missing = 0
    for label, field_name in STORAGE_SOURCES:
        app_label, model_name = label.split(".")
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:                      # pragma: no cover
            continue
        manager = getattr(model, "all_tenants", model._default_manager)
        rows = (manager.filter(organization=organization)
                .exclude(**{field_name: ""})
                .exclude(**{f"{field_name}__isnull": True})
                .values_list(field_name, flat=True)
                .iterator())
        for name in rows:
            try:
                total += model._meta.get_field(field_name).storage.size(name)
            except (OSError, NotImplementedError, ValueError):
                missing += 1
    if missing:
        logger.warning("storage recount for %s: %d file(s) unreadable",
                       organization.slug, missing)
    return total


def refresh_storage(organization):
    from .models import Organization

    total = storage_for(organization)
    Organization.objects.filter(pk=organization.pk).update(storage_bytes=total)
    return total


@transaction.atomic
def refresh_all(*, storage=True):
    """Recompute both counters for every tenant. ``{slug: (seats, bytes)}``.

    Runs with no tenant bound, so it must reach tenant tables -- which means
    it needs a role that can cross tenants, exactly like
    ``monitoring.backup_verify``. It is a management command for that reason
    and is never reachable from a request.
    """
    from .context import no_tenant, tenant_context
    from .models import Organization

    report = {}
    with no_tenant():
        organizations = list(Organization.objects.all())

    for organization in organizations:
        # Per-tenant context, so this works under RLS as an ordinary role too:
        # each recount only ever reads one tenant's rows.
        with tenant_context(organization):
            seats = refresh_seats(organization)
            total = refresh_storage(organization) if storage else (
                organization.storage_bytes)
        report[organization.slug] = (seats, total)
    return report


def seat_summary():
    """``{"total": n, "by_status": {...}}`` from the counters alone.

    No join onto a tenant table, which is the whole point -- see the module
    docstring.
    """
    from django.db.models import Sum

    from .context import no_tenant
    from .models import Organization

    with no_tenant():
        rows = (Organization.objects
                .values("subscription_status")
                .annotate(organizations=Count("id"), seats=Sum("seat_count"),
                          storage=Sum("storage_bytes"))
                .order_by())
        totals = Organization.objects.aggregate(
            organizations=Count("id"), seats=Sum("seat_count"),
            storage=Sum("storage_bytes"),
            provisioning=Count("id", filter=Q(status="provisioning")))
    return {
        "organizations": totals["organizations"] or 0,
        "seats": totals["seats"] or 0,
        "storage_bytes": totals["storage"] or 0,
        "provisioning": totals["provisioning"] or 0,
        "by_status": {
            row["subscription_status"]: {
                "organizations": row["organizations"],
                "seats": row["seats"] or 0,
                "storage_bytes": row["storage"] or 0,
            }
            for row in rows
        },
    }
