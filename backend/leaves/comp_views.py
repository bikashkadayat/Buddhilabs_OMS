"""HR review of attendance-earned compensatory days (Phase 8).

Closes G18. Before this, ``CompensatoryLedger.Status.PENDING`` had no writer
*and* no reader: the only endpoint that created earns (the HR grant) created
them CONFIRMED. Phase 8's derivation engine creates PENDING earns from
Saturday/holiday work, so without a confirm action they could never become
spendable and ``comp_summary()['pending']`` would grow forever.

A separate module from ``views.py`` on purpose: the existing leave workflow is
not modified by this phase, and keeping the diff at zero there makes that
verifiable.
"""
from django.utils import timezone
from rest_framework import status, views
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.models import AuditLog
from audit.services import log_action
from users.models import User

from .models import CompensatoryLedger


def _require_hr(actor):
    if actor.role not in (User.Roles.APPROVER, User.Roles.ADMIN):
        raise PermissionDenied("Only HR or Admin can review compensatory days.")


def _serialize(entry):
    return {
        "id": str(entry.id),
        "user": str(entry.user_id),
        "user_name": entry.user.get_full_name(),
        "days": float(entry.days),
        "source": entry.source,
        "status": entry.status,
        "source_date": entry.source_date,
        "note": entry.note,
        "created_at": entry.created_at,
    }


class CompensatoryPendingView(views.APIView):
    """GET /api/v1/leaves/compensatory/pending/ — the HR review queue.

    Attendance-sourced earns only. HR grants are already confirmed and comp
    *usage* is not something to approve here.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_hr(request.user)
        entries = (CompensatoryLedger.objects
                   .filter(entry_type=CompensatoryLedger.EntryType.EARN,
                           source=CompensatoryLedger.Source.ATTENDANCE,
                           status=CompensatoryLedger.Status.PENDING)
                   .select_related("user")
                   .order_by("source_date"))
        user_id = request.query_params.get("user")
        if user_id:
            entries = entries.filter(user_id=user_id)
        return Response({"count": entries.count(),
                         "entries": [_serialize(e) for e in entries]})


class CompensatoryConfirmView(views.APIView):
    """POST /api/v1/leaves/compensatory/<uuid:pk>/confirm/

    Only after this does the day count toward ``comp_available()`` and unlock
    Compensatory leave in ``applicable_type_codes()``.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        _require_hr(request.user)
        entry = _get_pending_earn(pk)
        if entry is None:
            return Response({"detail": "Pending compensatory entry not found."},
                            status=status.HTTP_404_NOT_FOUND)

        entry.status = CompensatoryLedger.Status.CONFIRMED
        entry.approved_by = request.user
        note = (request.data.get("note") or "").strip()
        if note:
            entry.note = f"{entry.note} | {note}"[:255]
        entry.save(update_fields=["status", "approved_by", "note"])
        log_action(request.user, AuditLog.Action.UPDATE, instance=entry,
                   changes={"event": "COMP_CONFIRM", "user": str(entry.user_id),
                            "days": float(entry.days),
                            "source_date": str(entry.source_date)},
                   request=request)
        return Response(_serialize(entry))


class CompensatoryRejectView(views.APIView):
    """POST /api/v1/leaves/compensatory/<uuid:pk>/reject/

    Deletes the pending earn rather than storing a rejected state: the ledger is
    an append-only record of days that COUNT, and a rejected candidate is not
    one. Re-deriving the day would recreate it, which is why the reason is
    written to the audit log — that is the durable record of the decision.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        _require_hr(request.user)
        entry = _get_pending_earn(pk)
        if entry is None:
            return Response({"detail": "Pending compensatory entry not found."},
                            status=status.HTTP_404_NOT_FOUND)

        snapshot = {"event": "COMP_REJECT", "user": str(entry.user_id),
                    "days": float(entry.days), "source_date": str(entry.source_date),
                    "reason": (request.data.get("reason") or "")[:255],
                    "rejected_at": timezone.now().isoformat()}
        log_action(request.user, AuditLog.Action.DELETE, instance=entry,
                   changes=snapshot, request=request)
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _get_pending_earn(pk):
    return (CompensatoryLedger.objects
            .select_related("user")
            .filter(pk=pk, entry_type=CompensatoryLedger.EntryType.EARN,
                    status=CompensatoryLedger.Status.PENDING)
            .first())
