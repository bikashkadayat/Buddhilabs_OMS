"""Cache invalidation on history-altering writes only.

The distinction that makes analytics caching viable: a punch arriving at 10:03
this morning changes TODAY, which every live-window entry re-reads within five
minutes anyway. A correction applied to last March changes a CLOSED month, whose
entry is cached for a day and would otherwise stay wrong until tomorrow.

So: bump the generation for the past, ignore the present. Bumping on every punch
would mean no cache at all during office hours, which is precisely when the
dashboards are read.
"""
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from . import cache

logger = logging.getLogger(__name__)


def _is_historical(day):
    return bool(day) and day < timezone.localdate()


def _organization_of(instance):
    """Which tenant's analytics this write invalidates.

    DERIVED FROM THE ROW, NOT FROM REQUEST CONTEXT (Phase S3). Several of these
    signals fire from places that have no request at all -- `device_sync`
    pulling punches off a terminal, `rederive_attendance` over an old range,
    the nightly jobs. Falling back to context there would either invalidate the
    wrong tenant or, with no context, flush every tenant's cache because one
    tenant's correction was applied.

    Attendance and LeaveDayRecord are Phase B models with no organization
    column of their own yet, so the owner comes through the employee -- which
    IS tenant-scoped (users.User is Phase A). WFHRequest is Phase A and carries
    it directly.
    """
    org_id = getattr(instance, "organization_id", None)
    if org_id is not None:
        return org_id

    for attribute in ("employee", "user"):
        person = getattr(instance, attribute, None)
        if person is not None and getattr(person, "organization_id", None):
            return person.organization_id

    # Nothing derivable. Returning None lets bump_generation fall back to
    # context, and failing that to a platform-wide flush -- correct but blunt,
    # and logged there.
    return None


@receiver(post_save, sender="attendance.Attendance", dispatch_uid="analytics_attendance_saved")
def attendance_saved(sender, instance, created, **kwargs):
    """A backdated row (an HR entry, a correction being applied, a re-derivation
    over an old range) changes a closed period. Today's punches do not."""
    if _is_historical(instance.date):
        cache.invalidate_all(f"attendance {instance.date}",
                             organization=_organization_of(instance))


@receiver(post_delete, sender="attendance.Attendance", dispatch_uid="analytics_attendance_deleted")
def attendance_deleted(sender, instance, **kwargs):
    if _is_historical(instance.date):
        cache.invalidate_all(f"attendance deleted {instance.date}",
                             organization=_organization_of(instance))


@receiver(post_save, sender="leaves.LeaveDayRecord", dispatch_uid="analytics_leave_day_saved")
def leave_day_saved(sender, instance, created, **kwargs):
    """Leave is the other half of every attendance denominator, and it is
    routinely approved for dates that have already passed."""
    if _is_historical(instance.date):
        cache.invalidate_all(f"leave day {instance.date}",
                             organization=_organization_of(instance))


@receiver(post_delete, sender="leaves.LeaveDayRecord", dispatch_uid="analytics_leave_day_deleted")
def leave_day_deleted(sender, instance, **kwargs):
    if _is_historical(instance.date):
        cache.invalidate_all(f"leave day deleted {instance.date}",
                             organization=_organization_of(instance))


@receiver(post_save, sender="leaves.CompensatoryLedger", dispatch_uid="analytics_comp_saved")
def comp_saved(sender, instance, created, **kwargs):
    """Comp balances are cumulative: confirming a day earned last month changes
    every window that contains it, not just the current one."""
    cache.invalidate_all("comp ledger",
                         organization=_organization_of(instance))


@receiver(post_save, sender="attendance.WFHRequest", dispatch_uid="analytics_wfh_saved")
def wfh_saved(sender, instance, created, **kwargs):
    if _is_historical(instance.start_date):
        cache.invalidate_all(f"wfh {instance.start_date}",
                             organization=_organization_of(instance))
