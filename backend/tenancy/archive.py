"""Phase S6.5 Parts 2 and 3: closing a workspace without losing it.

ARCHIVE IS NOT DELETE, AND IT IS NOT SUSPEND
--------------------------------------------
Three operations end up looking similar from the outside -- nobody can sign
in -- and they are different promises:

  suspend   a dispute an operator expects to resolve. The subscription keeps
            running underneath; the tenant is locked out until somebody
            decides otherwise.
  cancel    a commercial end. The subscription stops. Data is kept, because
            ``console.cancel`` deletes nothing, but nothing is promised about
            how long.
  archive   the workspace is closed and its contents are being PRESERVED for
            a faithful restore. That promise is the whole feature.

WHAT PRESERVATION ACTUALLY REQUIRES
-----------------------------------
Nothing is copied, moved or rewritten. That is not laziness -- it is the only
implementation that can keep the guarantee Part 3 asks for. "Preserve IDs,
references, numbering, documents, relationships" is satisfied trivially by not
touching the rows, and is nearly impossible to satisfy by copying them into an
archive table and copying them back: every uuid primary key, every foreign
key, every per-tenant number sequence and every stored file path would have to
survive two translations, and the first one that did not would be discovered
by a customer whose documents no longer verify.

So an archive is a GATE, not a migration. The data stays exactly where it is,
and four things change:

  1. ``Organization.status`` becomes ARCHIVED, which makes ``is_admitted``
     False -- so the middleware refuses every request, which is what "cannot
     sign in" and "cannot generate activity" mean in this codebase.
  2. The status is pinned (``status_override``), so a renewal cannot reopen it.
     ``lifecycle.apply_derived`` refuses an archived tenant outright as well,
     because the pin can be cleared and the data is mid-preservation.
  3. The status it had is recorded, so a restore is exact rather than a guess.
  4. The resolver cache is evicted, so the lockout takes effect now rather
     than when a cache entry happens to expire.

WHAT IS DELIBERATELY NOT DONE
-----------------------------
No row is deleted, no file is removed, no audit entry is trimmed, no
subscription is cancelled. An archived tenant's subscription keeps whatever
status it had: ending the subscription would be a commercial decision the
operator did not ask for, and it would also destroy the thing a restore needs
in order to put the customer back.
"""
import logging

from django.db import transaction
from django.utils import timezone

from . import lifecycle, platform_audit, resolver
from .exceptions import TenancyError

logger = logging.getLogger(__name__)

# The states an archive may be entered from. PROVISIONING is excluded: a
# workspace that was never finished has nothing to preserve, and the operator
# wants `cancel`.
ARCHIVABLE_FROM = ("trial", "active", "grace", "suspended", "cancelled")


class NotArchived(TenancyError):
    """A restore was asked for on a tenant that is not archived."""


class AlreadyArchived(TenancyError):
    """An archive was asked for on a tenant that is already archived."""


@transaction.atomic
def archive_organization(organization, *, actor=None, reason="", request=None):
    """Close the workspace, preserve everything, record who decided it.

    A reason is required, for the same purpose as a suspension reason: this is
    the action a customer will ring up about, possibly months later, and
    "archived by somebody in March" is not an answer.
    """
    from .models import Organization

    if organization.status == Organization.Status.ARCHIVED:
        raise AlreadyArchived(
            f"{organization.slug} is already archived "
            f"(since {organization.archived_at:%Y-%m-%d}).")
    if not (reason or "").strip():
        raise TenancyError("An archive reason is required.")
    if organization.status == Organization.Status.PROVISIONING:
        raise TenancyError(
            "A tenant that is still provisioning cannot be archived: there is "
            "no finished workspace to preserve. Cancel it instead.")

    previous = organization.status
    organization.archived_at = timezone.now()
    organization.archived_reason = reason.strip()[:300]
    organization.status_before_archive = previous
    organization.save(update_fields=["archived_at", "archived_reason",
                                     "status_before_archive", "updated_at"])

    # override=True: a payment landing tomorrow must not reopen an archive.
    lifecycle.transition_organization(
        organization, Organization.Status.ARCHIVED, actor=actor,
        reason=reason.strip(), request=request, override=True, audit=False)

    platform_audit.record(
        actor, _action().TENANT_ARCHIVED, organization=organization,
        changes={"status": {"from": previous, "to": Organization.Status.ARCHIVED},
                 "preserved": _preserved_counts(organization)},
        note=(f"Archived from '{previous}'. Reason: {reason.strip()} "
              f"Nothing was deleted."),
        request=request)

    resolver.forget(organization)
    logger.info("organization %s archived from %s", organization.slug, previous)
    return organization


@transaction.atomic
def restore_organization(organization, *, actor=None, note="", request=None,
                         to_status=None):
    """Reopen an archived workspace, in the state it was closed in.

    ``to_status`` overrides where it lands, for the case an operator needs:
    a tenant archived while SUSPENDED whose dispute has since been settled
    should come back ACTIVE rather than straight back into a lockout. It is
    still validated against the lifecycle, so it cannot be used to invent a
    transition.
    """
    from .models import Organization

    if organization.status != Organization.Status.ARCHIVED:
        raise NotArchived(
            f"{organization.slug} is not archived (it is "
            f"'{organization.status}'), so there is nothing to restore.")

    target = to_status or organization.status_before_archive
    if not target:
        # Only reachable for a row archived before this column existed.
        target = Organization.Status.SUSPENDED
        logger.warning(
            "organization %s has no recorded pre-archive status; restoring to "
            "SUSPENDED so an operator decides rather than this function",
            organization.slug)

    archived_at = organization.archived_at
    lifecycle.transition_organization(
        organization, target, actor=actor, reason=note or "Restored from archive",
        request=request, override=True, audit=False)

    organization.archived_at = None
    organization.archived_reason = ""
    organization.status_before_archive = ""
    organization.save(update_fields=["archived_at", "archived_reason",
                                     "status_before_archive", "updated_at"])

    platform_audit.record(
        actor, _action().TENANT_RESTORED, organization=organization,
        changes={"status": {"from": Organization.Status.ARCHIVED, "to": target},
                 "archived_at": archived_at.isoformat() if archived_at else None,
                 "restored": _preserved_counts(organization)},
        note=(note or f"Restored from archive to '{target}'."),
        request=request)

    resolver.forget(organization)
    logger.info("organization %s restored to %s", organization.slug, target)
    return organization


def _action():
    from .models import PlatformAuditLog

    return PlatformAuditLog.Action


def _preserved_counts(organization):
    """What the archive is holding, for the audit entry.

    Counted from the MAINTAINED counters and the platform-side tables, not by
    sweeping the tenant's rows: an archive must not need a 106-table scan to
    be recorded, and the counters are the same figures the console reports.
    """
    from .models import PlatformAuditLog, Subscription

    subscription = getattr(organization, "subscription", None)
    with_platform = {
        "seats": organization.seat_count,
        "storage_bytes": organization.storage_bytes,
        "subscription_status": getattr(subscription, "status", None),
        "subscription_period_end": (
            subscription.current_period_end.isoformat()
            if subscription and subscription.current_period_end else None),
        "platform_audit_entries": PlatformAuditLog.objects.filter(
            organization=organization).count(),
    }
    # Named so a reader of the audit entry can see that the subscription was
    # preserved rather than ended -- Part 2's "Preserve Subscriptions".
    if subscription is not None:
        with_platform["subscription_preserved"] = (
            subscription.status != Subscription.Status.CANCELLED
            or organization.status_before_archive == "cancelled")
    return with_platform


def archive_state(organization):
    """The archive facts for the tenant-health payload (Part 7)."""
    return {
        "is_archived": organization.status == organization.Status.ARCHIVED,
        "archived_at": organization.archived_at,
        "archived_reason": organization.archived_reason,
        "status_before_archive": organization.status_before_archive or None,
        "can_archive": organization.status in ARCHIVABLE_FROM,
        "can_restore": organization.status == organization.Status.ARCHIVED,
    }
