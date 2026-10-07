"""Per-tenant attendance configuration, with the deployment as fallback.

THE DEFECT THIS FIXES. `tenancy.OrganizationSettings` has carried
`office_name`, `office_lat`, `office_lng`, `office_radius_m`,
`max_accuracy_m`, `require_location`, `office_start`, `full_day_hours`,
`half_day_hours`, `absent_cutoff` and `tracking_start` since Phase S6 --
editable from the platform console, exported in the tenant's bundle, and
**read by nothing**. Every one of the seventeen attendance settings was
resolved from `django.conf.settings`, which is one value for the whole
deployment.

For the geofence that is not merely unset, it is WRONG. A hospital in
Lalitpur and a school in Kathmandu were both judged "at office" against a
single configured point, so one of them was permanently away from an office
it has never been near -- and app-based attendance is sold on that judgement.
Same shape as the Phase S9 letterhead defect: a per-tenant column collected
and never consulted.

RESOLUTION ORDER, and the middle step is the important one:

  1. this organization's `OrganizationSettings` column, when it is not null
  2. the deployment-wide `ATTENDANCE_*` Django setting
  3. the literal default passed by the caller

So a tenant that has configured nothing behaves exactly as it did before
this module existed, which is what makes the change safe for the
single-tenant deployment NIF runs today.

CACHED, BRIEFLY, AND EVICTED ON WRITE. `recompute_status` runs in a loop
during biometric derivation, so one query per setting per record would be a
real cost. The row is fetched once per organization and held for
`CACHE_TTL`; `OrganizationSettings.save()` evicts it through a signal, so a
console change takes effect on the next request rather than within a minute.
"""
import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_TTL = 300
_MISS = "\x00none"
# The single-tenant fallback's resolved id, cached so resolving it is not a
# query per setting read.
_FALLBACK_KEY = "attendance:orgsettings:fallback-org"


def _cache_key(org_id):
    return f"attendance:orgsettings:{org_id}"


def forget(org_id=None):
    """Drop the cached row. Called from a post_save signal."""
    cache.delete(_FALLBACK_KEY)
    if org_id is None:
        return
    cache.delete(_cache_key(org_id))


def _org_id():
    """The organization id for this request, WITHOUT fetching the row.

    This is why the function exists. The first version asked
    `tenancy.scoping.active_organization()`, which materialises the
    `Organization` -- and `value()` is called once per setting, so a
    dashboard that reads eleven of them paid eleven Organization queries
    even though the settings row itself was cached.

    A query-budget test caught it immediately: one dashboard went from 15
    queries to 29. `current_org_id` reads the context variable the
    middleware already bound and touches no table at all.
    """
    try:
        from tenancy.context import current_org_id, tenant_explicitly_unset

        bound = current_org_id()
        if bound is not None:
            return bound

        # NOTHING BOUND. Two different situations, and they must not be
        # treated alike:
        #
        #   * inside `no_tenant()` -- a deliberately platform-wide block, so
        #     there is no tenant whose settings apply and the answer is the
        #     deployment's;
        #   * the single-tenant deployment with TENANCY_ENABLED off, where
        #     `active_organization` resolves to the one organization that
        #     exists. That is the deployment NIF runs today, and its console
        #     settings have to work.
        if tenant_explicitly_unset():
            return None

        # Resolved through the SETTINGS CACHE, not per call. Asking
        # `active_organization()` each time is what the first version did,
        # and a query-budget test caught it: one dashboard reads eleven of
        # these settings and went from 15 queries to 29.
        cached = cache.get(_FALLBACK_KEY)
        if cached is not None:
            return None if cached == _MISS else cached

        from tenancy.scoping import active_organization

        organization = active_organization(required=False)
        org_id = organization.pk if organization is not None else None
        cache.set(_FALLBACK_KEY, org_id if org_id is not None else _MISS,
                  CACHE_TTL)
        return org_id
    except Exception:                                   # noqa: BLE001
        return None


def _row():
    """This request's `OrganizationSettings`, or None.

    Never raises. Attendance must keep working if the settings row is
    missing, if the table has not been migrated yet, or if there is no
    tenant in context at all -- in every one of those cases the answer is
    "fall back to the deployment", which is the behaviour that predates this
    module.
    """
    org_id = _org_id()
    if org_id is None:
        return None

    key = _cache_key(org_id)
    cached = cache.get(key)
    if cached == _MISS:
        return None
    if cached is not None:
        return cached

    try:
        from tenancy.models import OrganizationSettings

        row = (OrganizationSettings.objects
               .filter(organization_id=org_id)
               .first())
    except Exception:                                   # noqa: BLE001
        logger.debug("organization attendance settings unreadable",
                     exc_info=True)
        return None

    cache.set(key, row if row is not None else _MISS, CACHE_TTL)
    return row


def value(field, setting_name, default=None):
    """The tenant's own value, else the deployment's, else ``default``.

    A BLANK STRING AND A NULL BOTH MEAN "NOT SET" on these columns, which
    matters for `office_name` and `attendance_mode`: an operator clearing a
    text field in the console produces `""`, and treating that as a
    deliberate empty name rather than "inherit" would blank the office
    label for every tenant that had never set one.
    """
    row = _row()
    if row is not None:
        own = getattr(row, field, None)
        if own is not None and own != "":
            return own
    if setting_name:
        from_settings = getattr(settings, setting_name, None)
        if from_settings is not None and from_settings != "":
            return from_settings
    return default


def flag(field, setting_name, default=True):
    """As ``value``, for a nullable boolean.

    Separate because ``False`` is a real answer here and ``value`` would
    treat it as set -- which it is. What ``value`` would get wrong is
    nothing; what this adds is coercing the deployment setting, which may be
    the string "0" from an environment variable.
    """
    row = _row()
    if row is not None:
        own = getattr(row, field, None)
        if own is not None:
            return bool(own)
    raw = getattr(settings, setting_name, None) if setting_name else None
    if raw is None or raw == "":
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


# ---------------------------------------------------------------------------
# Attendance mode (the brief's "Organization Settings -> Attendance Mode")
# ---------------------------------------------------------------------------
MODE_BIOMETRIC_ONLY = "biometric_only"
MODE_APP_ONLY = "app_only"
MODE_BOTH = "both"
DEFAULT_MODE = MODE_BOTH


def attendance_mode():
    """How this tenant takes attendance.

    DEFAULTS TO ``both``, which is the only safe default for an existing
    deployment: anything narrower would silently switch off a method that
    customers are already using the morning this ships.
    """
    mode = value("attendance_mode", "ATTENDANCE_MODE", DEFAULT_MODE)
    if mode not in (MODE_BIOMETRIC_ONLY, MODE_APP_ONLY, MODE_BOTH):
        logger.warning("unknown attendance mode %r; using %r", mode,
                       DEFAULT_MODE)
        return DEFAULT_MODE
    return mode


def app_check_in_allowed():
    """Whether an employee may check in from the application."""
    return attendance_mode() in (MODE_APP_ONLY, MODE_BOTH)


def biometric_allowed():
    """Whether punches from a device should be derived into attendance."""
    return attendance_mode() in (MODE_BIOMETRIC_ONLY, MODE_BOTH)
