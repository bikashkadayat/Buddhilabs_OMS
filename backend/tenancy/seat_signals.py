"""Keep ``Organization.seat_count`` true, one user at a time.

Separate module from ``stamping.py`` because the two do opposite things:
stamping writes the tenant ONTO a row on the way in, this writes a COUNT back
onto the tenant afterwards. Mixing them would mean the 105 stamping receivers
and this one share a connect/disconnect switch, and a test that wants to
silence one would silence the other.

WHAT COUNTS AS A SEAT: an active, non-platform user. So deactivating somebody
frees a seat and reactivating them takes one back, which is what an
organization paying per seat would expect and what ``is_active`` already means
everywhere else in this codebase.

DELIBERATELY NOT A FULL RECOUNT PER SAVE. A ``+1``/``-1`` applied by the
database survives concurrency (see ``counters.bump_seats``) and costs one
cheap UPDATE, where a recount costs a COUNT over every user in the tenant on
every profile edit. Drift is possible in principle -- a bulk_create or a raw
UPDATE bypasses signals -- so ``manage.py tenancy_refresh_counters`` exists to
reconcile, and a test asserts the two agree.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save

from . import counters

_UID = "tenancy.seat_count"


def _is_seat(user):
    return bool(user.is_active) and not user.is_platform_staff


def remember_previous(sender, instance, **kwargs):
    """Record the pre-save seat state, so post_save knows what CHANGED.

    Without this, a profile edit on an active user looks exactly like an
    activation and the count climbs by one every time somebody changes their
    phone number.
    """
    if instance.pk is None or instance._state.adding:
        instance._seat_was = None
        return
    previous = (sender.all_tenants
                .filter(pk=instance.pk)
                .values("is_active", "is_platform_staff", "organization_id")
                .first())
    instance._seat_was = previous


def apply_change(sender, instance, created, **kwargs):
    now_seat = _is_seat(instance)
    previous = getattr(instance, "_seat_was", None)

    if created:
        if now_seat:
            _after_commit(instance.organization_id, +1)
        return

    if previous is None:
        # Saved an instance this process never loaded (refresh_from_db-less
        # update_fields path). Nothing reliable to diff against, so leave the
        # counter to the reconciler rather than guess.
        return

    was_seat = (previous["is_active"] and not previous["is_platform_staff"])
    was_org = previous["organization_id"]

    if was_org != instance.organization_id:
        # A user moved between tenants. Both counters change.
        if was_seat:
            _after_commit(was_org, -1)
        if now_seat:
            _after_commit(instance.organization_id, +1)
        return

    if was_seat != now_seat:
        _after_commit(instance.organization_id, +1 if now_seat else -1)


def apply_delete(sender, instance, **kwargs):
    if _is_seat(instance):
        _after_commit(instance.organization_id, -1)


def _after_commit(organization_id, delta):
    """Adjust once the surrounding transaction commits.

    Inside the transaction would be wrong twice over: a rolled-back user
    creation would leave the count incremented, and the UPDATE would hold a
    row lock on the Organization for the rest of the request -- so two
    employees being created in two tenants would be fine, but two in the SAME
    tenant would serialise on a counter.
    """
    if not organization_id:
        return
    transaction.on_commit(
        lambda: counters.bump_seats(organization_id, delta))


def connect():
    User = get_user_model()
    pre_save.connect(remember_previous, sender=User,
                     dispatch_uid=f"{_UID}.pre")
    post_save.connect(apply_change, sender=User,
                      dispatch_uid=f"{_UID}.post")
    post_delete.connect(apply_delete, sender=User,
                        dispatch_uid=f"{_UID}.delete")


def disconnect():
    User = get_user_model()
    pre_save.disconnect(sender=User, dispatch_uid=f"{_UID}.pre")
    post_save.disconnect(sender=User, dispatch_uid=f"{_UID}.post")
    post_delete.disconnect(sender=User, dispatch_uid=f"{_UID}.delete")
