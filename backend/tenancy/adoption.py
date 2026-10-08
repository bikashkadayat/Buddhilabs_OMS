"""Adoption: is a customer actually using the product?

COUNTS OF USE, NOT RECORDS OF WHO. Every figure here is a daily count per
organization in ``PlatformMetric`` -- "12 people were active", "3 leave
requests were made" -- never a name, a date range or a reason. That is what
lets the platform console show customer health and adoption trends while
holding no privilege over a single customer record (the same principle as
``tenancy.counters`` and the seat count).

Two kinds of signal:

* ``ACTIVE_USER``: counted once per person per day, the first time they use
  the API that day. Deduplicated in the cache, so a person who makes a
  thousand requests counts once; if the cache is down, at worst a person
  counts twice that day, which an adoption trend survives.

* Module use (attendance, leave, tasks, documents): counted when a record of
  that kind is CREATED -- the moment of use, not of editing.

NEVER IN THE WAY. Each count runs in its own savepoint and swallows its own
failure: a dashboard figure is not worth a user's request.
"""
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)


def _count(key, organization_id):
    if not organization_id:
        return
    from .models import PlatformMetric

    try:
        with transaction.atomic():
            day = timezone.localdate()
            updated = (PlatformMetric.objects
                       .filter(day=day, key=key, organization_id=organization_id)
                       .update(count=F("count") + 1))
            if not updated:
                PlatformMetric.objects.create(
                    day=day, key=key, organization_id=organization_id, count=1)
    except Exception:                              # noqa: BLE001
        logger.debug("adoption count %s failed", key, exc_info=True)


def record_active(user):
    """Count a tenant user as active today, once."""
    from django.core.cache import cache

    from .models import PlatformMetric

    if user is None or getattr(user, "is_platform_staff", False):
        return
    organization_id = getattr(user, "organization_id", None)
    if not organization_id:
        return
    key = f"adoption:active:{user.pk}:{timezone.localdate().isoformat()}"
    try:
        first = cache.add(key, 1, timeout=60 * 60 * 26)
    except Exception:                              # noqa: BLE001
        first = False   # no cache, no count: better than counting every request
    if first:
        _count(PlatformMetric.Key.ACTIVE_USER, organization_id)


# Which model's creation counts as use of which module.
_MODULES = {
    "attendance.Attendance": "ATTENDANCE_USED",
    "leaves.Leave": "LEAVE_USED",
    "tasks.Task": "TASK_USED",
    "memos.Memo": "DOCUMENT_USED",
    "minutes.Minute": "DOCUMENT_USED",
    "circulars.Circular": "DOCUMENT_USED",
}


def _on_created(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    from .models import PlatformMetric

    key = _MODULES.get(sender._meta.label)
    if key:
        _count(getattr(PlatformMetric.Key, key),
               getattr(instance, "organization_id", None))


def _on_user_created(sender, instance, created, raw=False, **kwargs):
    """Count an employee added -- beyond the organization's first user.

    The first user is the founding administrator, created by provisioning or
    registration; counting them would tick "first employee" before anybody
    had added one. Platform staff belong to no organization and never count.
    """
    if not created or raw or getattr(instance, "is_platform_staff", False):
        return
    organization_id = getattr(instance, "organization_id", None)
    if not organization_id:
        return
    from .models import PlatformMetric

    try:
        others = (sender.all_tenants.filter(organization_id=organization_id)
                  .exclude(pk=instance.pk).exists())
    except Exception:                              # noqa: BLE001
        return
    if others:
        _count(PlatformMetric.Key.EMPLOYEE_ADDED, organization_id)


def connect():
    from django.apps import apps
    from django.conf import settings
    from django.db.models.signals import post_save

    post_save.connect(_on_user_created, sender=apps.get_model(settings.AUTH_USER_MODEL),
                      dispatch_uid="adoption:employee_added")
    for label in _MODULES:
        try:
            model = apps.get_model(label)
        except LookupError:                        # an app not installed here
            continue
        post_save.connect(_on_created, sender=model,
                          dispatch_uid=f"adoption:{label}")


# ---------------------------------------------------------------------------
# Reading it back, for the console
# ---------------------------------------------------------------------------
USAGE_KEYS = ("ATTENDANCE_USED", "LEAVE_USED", "TASK_USED", "DOCUMENT_USED")


def adoption_summary(*, days=30):
    """Platform-wide adoption, and per organization, from the counters only."""
    from datetime import timedelta

    from django.db.models import Sum

    from .context import no_tenant
    from .models import Organization, PlatformMetric

    K = PlatformMetric.Key
    today = timezone.localdate()
    since = today - timedelta(days=days - 1)
    keys = [K.ACTIVE_USER] + [getattr(K, k) for k in USAGE_KEYS]
    with no_tenant():
        rows = list(PlatformMetric.objects
                    .filter(day__gte=since, key__in=keys)
                    .values("organization_id", "key", "day")
                    .annotate(n=Sum("count")))
        orgs = list(Organization.objects.exclude(status=Organization.Status.ARCHIVED)
                    .select_related("subscription")
                    .order_by("name"))

    by_day = {}                 # key -> {day: n}, platform-wide
    per_org = {}                # org_id -> {key: total, "last_active": day, "wau": n}
    week_start = today - timedelta(days=6)
    for row in rows:
        by_day.setdefault(row["key"], {}).setdefault(row["day"], 0)
        by_day[row["key"]][row["day"]] += row["n"]
        org = per_org.setdefault(row["organization_id"], {})
        org[row["key"]] = org.get(row["key"], 0) + row["n"]
        if row["key"] == K.ACTIVE_USER:
            if row["day"] >= week_start:
                # Daily distinct actives summed over the week: "person-days",
                # an upper bound on weekly actives. Labelled as such.
                org["active_days_7d"] = org.get("active_days_7d", 0) + row["n"]
            if row["day"] == today:
                org["active_today"] = row["n"]
            last = org.get("last_active")
            if last is None or row["day"] > last:
                org["last_active"] = row["day"]

    def series(key):
        data = by_day.get(key, {})
        return [{"day": (since + timedelta(days=i)).isoformat(),
                 "count": data.get(since + timedelta(days=i), 0)}
                for i in range(days)]

    platform = {
        "dau_today": sum(n for (d, n) in by_day.get(K.ACTIVE_USER, {}).items() if d == today),
        "active_person_days_7d": sum(n for (d, n) in by_day.get(K.ACTIVE_USER, {}).items()
                                     if d >= week_start),
        "series": {"active_users": series(K.ACTIVE_USER),
                   **{k.lower().replace("_used", ""): series(getattr(K, k)) for k in USAGE_KEYS}},
        "totals_30d": {k.lower().replace("_used", ""): sum(by_day.get(getattr(K, k), {}).values())
                       for k in USAGE_KEYS},
    }
    return platform, orgs, per_org


def customer_health(*, days=30):
    """Every organization, sorted worst first, with the reasons in words.

    The score is Customer Success 2.0's weighted health score
    (``tenancy.success``): login activity, user adoption, attendance, tasks,
    documents and support volume. The response keeps its original shape so
    every existing reader of this endpoint is unaffected.
    """
    from . import success

    platform, _orgs, per_org = adoption_summary(days=days)
    rows = success.health(days)
    by_slug = {o.slug: o.pk for o in _orgs}
    for row in rows:
        usage = per_org.get(by_slug.get(row["slug"]), {})
        row["active_person_days_7d"] = usage.get("active_days_7d", 0)
        row["active_today"] = usage.get("active_today", row.get("active_today", 0))
    return {"platform": platform, "organizations": rows,
            "window_days": days, "generated_at": timezone.now()}
