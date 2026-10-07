"""The only writer of ``CompensatoryLedger.Source.ATTENDANCE``.

Closes G10 — the model has documented this behaviour since it was written, but
nothing ever created the rows.

Idempotency is the whole design. ``rederive_attendance`` re-runs derivation for
arbitrary date ranges after every mapping change, so a naive ``create()`` would
mint a fresh comp day on every run. The partial unique constraint added in
``leaves/0016`` plus ``update_or_create`` keyed on ``(user, source, entry_type,
source_date)`` makes re-deriving the same Saturday a hundred times converge on
exactly one ledger row.

Earns are created PENDING and are excluded from ``comp_available()`` until HR
confirms them — see ``leaves/comp_views.py``. Auto-confirming would silently
hand out leave, and a first enablement can produce months of retroactive
candidates.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def sync_entry(record):
    """Reconcile the ledger with one derived attendance day.

    Earned days go in as PENDING; a day that stops qualifying withdraws only a
    PENDING entry. A CONFIRMED entry is never revoked automatically — once HR
    has granted the day it is the employee's, and taking it back is an explicit
    HR action.
    """
    from leaves.models import CompensatoryLedger

    days = record.comp_off_days or 0
    base = {
        "user": record.employee,
        "entry_type": CompensatoryLedger.EntryType.EARN,
        "source": CompensatoryLedger.Source.ATTENDANCE,
        "source_date": record.date,
    }

    if days > 0:
        entry, created = CompensatoryLedger.objects.update_or_create(
            **base,
            # `status` is deliberately absent: re-deriving must never reopen an
            # entry HR has already confirmed.
            defaults={"days": days,
                      "note": f"Auto: {record.working_hours}h worked on {record.date}"},
        )
        if created:
            logger.info("Comp-off earned: user=%s date=%s days=%s",
                        record.employee_id, record.date, days)
        return entry

    removed, _ = CompensatoryLedger.objects.filter(
        **base, status=CompensatoryLedger.Status.PENDING).delete()
    if removed:
        logger.info("Comp-off withdrawn (no longer qualifies): user=%s date=%s",
                    record.employee_id, record.date)
    return None


@receiver(post_save, sender="attendance.Attendance", dispatch_uid="attendance_comp_off_sync")
def _sync_on_attendance_save(sender, instance, **kwargs):
    """Keep the ledger in step with every attendance write.

    A signal rather than four explicit calls: browser check-in, browser
    check-out, biometric derivation and HR correction all end in
    ``Attendance.save()``, and routing through one receiver means no write path
    can be forgotten. Queued to ``on_commit`` so a rolled-back derivation never
    leaves a ledger row behind.
    """
    transaction.on_commit(lambda: _safe_sync(instance))


def _safe_sync(record):
    try:
        sync_entry(record)
    except Exception:
        # Comp-off is a downstream benefit. A failure here must never be able to
        # undo an attendance record that is already committed.
        logger.exception("Comp-off sync failed for user=%s date=%s",
                         record.employee_id, record.date)
