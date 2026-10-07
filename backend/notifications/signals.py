"""
Leave notification triggers. Memo notifications are emitted directly from
memos.services (which owns the memo state transitions).

This receiver runs after leaves' own post_save receiver (leaves is loaded before
notifications in INSTALLED_APPS), so balances are already recomputed when the
low-balance check runs.
"""
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from leaves.models import EnterpriseLeaveBalance, Leave, LeaveType
from .dispatcher import notify
from .models import Category

User = get_user_model()
LOW_BALANCE_THRESHOLD = 3


def _employee(user):
    return user.get_full_name() or user.username


def _check_low_balance(leave):
    leave_type = LeaveType.objects.filter(code__iexact=leave.leave_type).first()
    if leave_type is None:
        return
    year = leave.start_date.year
    balance = EnterpriseLeaveBalance.objects.filter(user=leave.user, leave_type=leave_type, year=year).first()
    if balance is None or balance.available_days >= LOW_BALANCE_THRESHOLD:
        return

    key = f"balance_low:{leave.user_id}:{leave_type.code}:{year}"
    notify(
        leave.user, Category.LEAVE_BALANCE_LOW,
        f"Low {leave_type.name} balance",
        f"You have {balance.available_days} day(s) of {leave_type.name} remaining for {year}.",
        action_url="/leaves/my-history", idempotency_key=key,
    )
    for hr in User.objects.filter(role="admin", is_active=True):
        notify(
            hr, Category.LEAVE_BALANCE_LOW,
            f"Low balance: {_employee(leave.user)}",
            f"{leave_type.name} balance is {balance.available_days} day(s) for {year}.",
            action_url=f"/admin/leaves/employees/{leave.user_id}",
            idempotency_key=f"{key}:hr:{hr.id}",
        )


@receiver(post_save, sender=Leave)
def leave_notifications(sender, instance, created, **kwargs):
    """
    Single source of truth for leave-workflow notifications. Fires on every
    Leave.save() (API endpoints, bulk actions, admin, shell), and delegates to
    leaves.notifications for the rich, dynamically-addressed, BS+AD-dated emails.
    Each trigger is idempotency-keyed, so a transition can't notify twice.
    """
    from leaves import notifications as leave_notify
    from .dispatcher import resolve_source_notifications

    if getattr(instance, "is_deleted", False):
        # Soft-deleted -> nobody needs to review it any more; clear the queue notices.
        resolve_source_notifications(f"leave-{instance.id}-submitted")
        return

    if created:
        leave_notify.leave_submitted(instance)          # Trigger 1 -> Dept Head(s) + HR
        _check_low_balance(instance)
        return

    old = getattr(instance, "_old_status", None)
    if old is not None and old != instance.status:
        # Left the PENDING stage (approved/rejected) -> clear "awaiting review" notices
        # so the approver's bell reconciles with their (now empty) pending queue.
        if instance.status != Leave.Status.PENDING:
            resolve_source_notifications(f"leave-{instance.id}-submitted")
        if instance.status == Leave.Status.PENDING_HR:
            leave_notify.leave_l1_approved(instance)      # Trigger 2 -> HR (+ employee)
        elif instance.status == Leave.Status.APPROVED:
            leave_notify.leave_finalized(instance)        # Trigger 3 -> employee (approved)
        elif instance.status == Leave.Status.REJECTED:
            if old == Leave.Status.PENDING:
                leave_notify.leave_rejected_l1(instance)  # Trigger 4 -> employee (L1 reject)
            else:
                leave_notify.leave_finalized(instance)    # Trigger 3 -> employee (L2 reject)
        _check_low_balance(instance)
