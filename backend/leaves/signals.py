"""
Signal wiring for the enterprise leave records.

    * Creating a Leave generates its per-day LeaveDayRecords and computes the
      initial balances/summaries.
    * Changing a Leave's status re-syncs its day records and recomputes.
    * A single LeaveDayRecord save (ad-hoc admin edit) recomputes the affected
      balance/summaries. The services layer uses bulk_create/update, which do
      not emit this signal, so normal flows never trigger recompute storms.

Phase 5 will move the recompute work to an async task; the seam is here.
"""
from django.db.models import ProtectedError
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from . import services
from .models import Holiday, Leave, LeaveDayRecord

WEEKEND_WEEKDAYS = {5}  # Saturday only — Nepal's weekly holiday (Sunday is a working day).


@receiver(pre_save, sender=Leave)
def capture_old_leave_status(sender, instance, **kwargs):
    if instance.pk:
        previous = Leave.objects.filter(pk=instance.pk).only("status").first()
        instance._old_status = previous.status if previous else None
    else:
        instance._old_status = None


@receiver(post_save, sender=Leave)
def on_leave_saved(sender, instance, created, **kwargs):
    if created:
        services.generate_leave_day_records(instance, day_portion=instance.day_portion)
        services.sync_leave_day_records(instance)
        return
    old_status = getattr(instance, "_old_status", None)
    if old_status is not None and old_status != instance.status:
        services.sync_leave_day_records(instance)


@receiver(post_save, sender=LeaveDayRecord)
def on_day_record_saved(sender, instance, created, **kwargs):
    if getattr(instance, "_skip_recompute", False):
        return
    services.recompute_leave_balance(instance.user, instance.leave_type, instance.year)
    services.recompute_weekly_summary(instance.user, instance.year, instance.week_number)
    services.recompute_monthly_summary(instance.user, instance.year, instance.month)


# --- Phase 5 data-integrity guards -----------------------------------------
@receiver(pre_save, sender=LeaveDayRecord)
def enforce_day_flags(sender, instance, **kwargs):
    """
    Defense in depth: re-derive is_weekend / is_holiday from the source of truth
    on every single-record save so a stale/incorrect flag can never persist.
    (Bulk create/update used by the services layer bypasses this by design.)
    """
    instance.is_weekend = instance.date.weekday() in WEEKEND_WEEKDAYS
    instance.is_holiday = Holiday.objects.filter(is_active=True, date=instance.date).exists()


@receiver(pre_delete, sender=Leave)
def protect_approved_leave(sender, instance, **kwargs):
    """
    Prevent hard-deleting a leave that has approved day records. The API layer
    soft-deletes such leaves instead (services.soft_delete_leave); this guard
    stops accidental ORM/admin hard deletes from destroying approved history.
    """
    approved = instance.day_records.filter(status=LeaveDayRecord.Status.APPROVED)
    if approved.exists() and not getattr(instance, "_allow_hard_delete", False):
        raise ProtectedError(
            "Cannot hard-delete a leave with approved day records; soft-delete instead.",
            set(approved),
        )
