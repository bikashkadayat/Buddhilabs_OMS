"""Reconcile 'awaiting your review' notifications against the actionable resolver.

The bell counts unread Notification rows; the pending queue reads the shared
resolver. A submitted-notification can go stale (its request was deleted, already
decided, or the recipient is no longer the actionable approver — e.g. rows created
by an older recipient rule). This marks those as read so the bell can never claim
'awaiting action' for something absent from the recipient's pending queue.
"""
import re

from django.utils import timezone

from .models import Notification

_LEAVE = re.compile(r"^leave-([0-9a-fA-F-]{36})-submitted")
_TAKEOUT = re.compile(r"^takeout-([0-9a-fA-F-]{36})-submitted")


def reconcile_stale_approvals():
    """Return the number of stale submitted-notifications marked read."""
    from leaves.models import Leave
    from leaves.approvals import actionable_approver_ids
    from inventory.models import TakeOutRequest
    from inventory.notifications import _managers_for

    stale = []
    qs = Notification.objects.filter(is_read=False, idempotency_key__contains="-submitted") \
        .only("id", "recipient_id", "idempotency_key")
    for n in qs:
        key = n.idempotency_key or ""

        m = _LEAVE.match(key)
        if m:
            lv = Leave.objects.filter(id=m.group(1)).select_related("user").first()
            if lv is None or lv.is_deleted or lv.status != Leave.Status.PENDING:
                stale.append(n.id)                       # request gone / already decided
            elif "-submitted-head-" in key and n.recipient_id not in actionable_approver_ids(lv):
                stale.append(n.id)                       # recipient is not the actionable approver
            continue

        m = _TAKEOUT.match(key)
        if m:
            req = TakeOutRequest.objects.filter(id=m.group(1)).select_related("requested_by").first()
            if req is None or req.status != TakeOutRequest.Status.PENDING:
                stale.append(n.id)
            elif n.recipient_id not in {u.id for u in _managers_for(req.requested_by)}:
                stale.append(n.id)
            continue

    if stale:
        Notification.objects.filter(id__in=stale).update(is_read=True, read_at=timezone.now())
    return len(stale)
