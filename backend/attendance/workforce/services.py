"""The correction workflow: submit, approve, reject, cancel, revert.

Every transition is audited through the central ``audit.log_action`` and
notified through ``notifications.dispatcher.notify`` — no parallel audit table,
no bespoke mailer.

The one genuinely delicate operation is ``apply_correction``. It writes
``source=HR, marked_by=HR``, which ``biometric.derivation._is_hr_locked``
already treats as untouchable: the corrected day then survives every future
punch import, mapping change and re-derivation without any change to the engine.
That protection is also why the snapshot exists — an applied correction cannot
be undone by re-deriving, so ``revert_correction`` is the only way back.
"""
import logging

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from notifications.models import Category

from .. import services as attendance_services
from ..models import Attendance
from ..policy.resolver import policy_cache
from . import routing
from .models import AttendanceCorrectionRequest

logger = logging.getLogger(__name__)

Status = AttendanceCorrectionRequest.Status


class WorkflowError(Exception):
    """An action that the request's current state does not allow."""


# ---------------------------------------------------------------------------
# notifications — best effort, never able to fail a transition
# ---------------------------------------------------------------------------
def _notify(user, category, title, body, correction):
    if user is None:
        return
    try:
        from notifications.dispatcher import notify

        notify(user, category, title, body,
               action_url=f"/workforce/corrections?id={correction.pk}",
               idempotency_key=f"correction:{correction.pk}:{category}",
               object_id=str(correction.pk))
    except Exception:
        logger.warning("Correction notification failed (%s)", category, exc_info=True)


def _audit(actor, action, correction, event, request=None, **extra):
    log_action(actor, action, instance=correction,
               changes={"event": event,
                        "correction": str(correction.pk),
                        "employee": str(correction.employee_id),
                        "date": str(correction.attendance_date),
                        "status": correction.status, **extra},
               request=request)


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------
@transaction.atomic
def submit(correction, *, actor, request=None):
    """Snapshot the current day, route to the department head, notify."""
    existing = (Attendance.objects
                .filter(employee=correction.employee, date=correction.attendance_date)
                .first())
    correction.had_attendance_row = existing is not None
    if existing is not None:
        correction.previous_check_in = existing.check_in
        correction.previous_check_out = existing.check_out
        correction.previous_status = existing.status
        correction.previous_source = existing.source

    manager = routing.department_head_for(correction.employee)
    correction.manager = manager
    # No department head (or the employee IS the head): go straight to HR rather
    # than parking the request in a queue nobody owns.
    correction.status = Status.PENDING if manager else Status.MANAGER_APPROVED
    correction.save()

    _audit(actor, AuditLog.Action.SUBMIT, correction, "ATTENDANCE_CORRECTION_SUBMIT",
           request=request, routed_to=str(manager.pk) if manager else None)

    label = f"{correction.employee.get_full_name()} — {correction.attendance_date}"
    if manager is not None:
        _notify(manager, Category.ATTENDANCE_CORRECTION_SUBMITTED,
                "Attendance correction to review", label, correction)
    else:
        _notify_hr(correction, Category.ATTENDANCE_CORRECTION_MANAGER_APPROVED,
                   "Attendance correction awaiting HR", label)
    return correction


def _notify_hr(correction, category, title, body):
    from users.models import User

    for hr in User.objects.filter(role=User.Roles.APPROVER, is_active=True):
        _notify(hr, category, title, body, correction)


# ---------------------------------------------------------------------------
# manager stage
# ---------------------------------------------------------------------------
@transaction.atomic
def manager_approve(correction, *, actor, remarks="", request=None):
    if correction.status != Status.PENDING:
        raise WorkflowError(
            f"This request is already {correction.get_status_display().lower()}.")

    correction.status = Status.MANAGER_APPROVED
    correction.manager = correction.manager or actor
    correction.manager_action_at = timezone.now()
    correction.manager_remarks = remarks[:255]
    correction.save(update_fields=["status", "manager", "manager_action_at",
                                   "manager_remarks", "updated_at"])

    _audit(actor, AuditLog.Action.APPROVE, correction,
           "ATTENDANCE_CORRECTION_MANAGER_APPROVE", request=request, remarks=remarks[:255])
    label = f"{correction.employee.get_full_name()} — {correction.attendance_date}"
    _notify_hr(correction, Category.ATTENDANCE_CORRECTION_MANAGER_APPROVED,
               "Attendance correction awaiting HR", label)
    _notify(correction.employee, Category.ATTENDANCE_CORRECTION_MANAGER_APPROVED,
            "Your correction was approved by your department head",
            "It is now with HR.", correction)
    return correction


# ---------------------------------------------------------------------------
# HR stage — this is the one that changes attendance
# ---------------------------------------------------------------------------
@transaction.atomic
def hr_approve(correction, *, actor, remarks="", request=None):
    if correction.status != Status.MANAGER_APPROVED:
        raise WorkflowError(
            "Only a request approved by the department head can be finalised by HR.")

    record = apply_correction(correction)

    correction.status = Status.HR_APPROVED
    correction.hr_actor = actor
    correction.hr_action_at = timezone.now()
    correction.hr_remarks = remarks[:255]
    correction.applied_at = timezone.now()
    correction.attendance = record
    correction.save(update_fields=["status", "hr_actor", "hr_action_at", "hr_remarks",
                                   "applied_at", "attendance", "updated_at"])

    _audit(actor, AuditLog.Action.APPROVE, correction,
           "ATTENDANCE_CORRECTION_HR_APPROVE", request=request,
           remarks=remarks[:255],
           applied={"check_in": str(record.check_in) if record.check_in else None,
                    "check_out": str(record.check_out) if record.check_out else None,
                    "status": record.status},
           previous={"check_in": str(correction.previous_check_in or ""),
                     "check_out": str(correction.previous_check_out or ""),
                     "status": correction.previous_status})
    _notify(correction.employee, Category.ATTENDANCE_CORRECTION_APPROVED,
            "Your attendance correction was applied",
            f"{correction.attendance_date}: now {record.get_status_display()}.",
            correction)
    return correction


def apply_correction(correction):
    """Write the requested values onto the attendance row.

    ``source=HR`` / ``marked_by=HR`` is what makes the change permanent: the
    derivation engine skips HR-authored rows entirely, so a later punch import
    or ``rederive_attendance`` run cannot silently undo an HR decision.

    Idempotent by construction — it assigns absolute values rather than
    applying a delta, so running it twice produces the same row.
    """
    with policy_cache():
        record, _created = Attendance.objects.get_or_create(
            employee=correction.employee, date=correction.attendance_date)

        if correction.requested_check_in is not None:
            record.check_in = correction.requested_check_in
        if correction.requested_check_out is not None:
            record.check_out = correction.requested_check_out

        record.source = Attendance.Source.HR
        record.marked_by = Attendance.MarkedBy.HR
        record.remarks = (f"Correction: {correction.reason}")[:1000]

        # Recompute through the single seam so a corrected day obeys exactly the
        # same NIF arrival rules as a browser check-in or a device punch.
        attendance_services.recompute_status(record)

        # An explicit status request outranks the derived one — that is the
        # point of letting HR state it.
        if correction.requested_status:
            record.status = correction.requested_status

        record.save()
    return record


# ---------------------------------------------------------------------------
# reject / cancel / revert
# ---------------------------------------------------------------------------
@transaction.atomic
def reject(correction, *, actor, reason="", request=None):
    if not correction.is_open:
        raise WorkflowError(
            f"This request is already {correction.get_status_display().lower()}.")

    correction.status = Status.REJECTED
    correction.rejected_by = actor
    correction.rejection_reason = reason[:255]
    correction.save(update_fields=["status", "rejected_by", "rejection_reason",
                                   "updated_at"])

    _audit(actor, AuditLog.Action.REJECT, correction, "ATTENDANCE_CORRECTION_REJECT",
           request=request, reason=reason[:255])
    _notify(correction.employee, Category.ATTENDANCE_CORRECTION_REJECTED,
            "Your attendance correction was rejected",
            reason or "No reason given.", correction)
    return correction


@transaction.atomic
def cancel(correction, *, actor, request=None):
    if correction.employee_id != actor.pk:
        raise WorkflowError("You can only cancel your own request.")
    if not correction.is_open:
        raise WorkflowError("Only a request still in review can be cancelled.")

    correction.status = Status.CANCELLED
    correction.save(update_fields=["status", "updated_at"])
    _audit(actor, AuditLog.Action.UPDATE, correction, "ATTENDANCE_CORRECTION_CANCEL",
           request=request)
    return correction


@transaction.atomic
def revert(correction, *, actor, reason="", request=None):
    """Undo an applied correction from its snapshot.

    The documented rollback path (L2). An applied correction is HR-locked, so
    re-derivation will never restore the original values — this is the only way
    back, which is why the snapshot columns exist.
    """
    if correction.status != Status.HR_APPROVED or correction.applied_at is None:
        raise WorkflowError("Only an applied correction can be reverted.")

    record = (Attendance.objects
              .filter(employee=correction.employee, date=correction.attendance_date)
              .first())
    if record is not None:
        if not correction.had_attendance_row:
            # The correction created the row; undoing means removing it, which
            # lets the derivation engine own the day again.
            record.delete()
            record = None
        else:
            record.check_in = correction.previous_check_in
            record.check_out = correction.previous_check_out
            record.source = correction.previous_source or Attendance.Source.BROWSER
            record.marked_by = Attendance.MARKED_BY_FOR_SOURCE.get(
                record.source, Attendance.MarkedBy.SELF)
            with policy_cache():
                attendance_services.recompute_status(record)
            if correction.previous_status:
                record.status = correction.previous_status
            record.save()

    correction.status = Status.REJECTED
    correction.reverted_at = timezone.now()
    correction.rejected_by = actor
    correction.rejection_reason = (reason or "Reverted after approval.")[:255]
    correction.attendance = record
    correction.save(update_fields=["status", "reverted_at", "rejected_by",
                                   "rejection_reason", "attendance", "updated_at"])

    _audit(actor, AuditLog.Action.UPDATE, correction, "ATTENDANCE_CORRECTION_REVERT",
           request=request, reason=reason[:255],
           restored={"check_in": str(correction.previous_check_in or ""),
                     "status": correction.previous_status})
    _notify(correction.employee, Category.ATTENDANCE_CORRECTION_REJECTED,
            "An applied attendance correction was reverted",
            reason or "Contact HR for details.", correction)
    return correction
