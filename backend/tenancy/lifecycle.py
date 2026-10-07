"""The ORGANIZATION lifecycle, and the only sanctioned way to move it.

Phase S6 Part 6. There are two status machines on a tenant and they are not
the same machine:

    Subscription.status   what the customer has BOUGHT   (tenancy/services.py)
    Organization.status   whether the workspace is OPEN  (this module)

They agree almost always, and the places they do not are the whole reason both
exist:

  * ``PROVISIONING`` has no subscription meaning at all. The tenant has a
    subscription from the moment it is created, but the workspace is not
    finished being built, and nobody may be let in until the bootstrap has
    run. There is no subscription status that expresses "wait".

  * A MANUAL suspension must survive a renewal. If the operator suspended a
    customer for abuse, a payment clearing the next morning must not quietly
    re-open the workspace. ``Organization.status`` is the operator's switch;
    ``Subscription.status`` is the billing fact.

So ``Organization.status`` is DERIVED from the subscription by default and
OVERRIDABLE by an operator, and this module is the only thing permitted to
change it. ``Organization.save()`` refuses a status that changed any other
way -- the same enforcement, and for the same reason, as
``Subscription.save()``.

THE LIFECYCLE
-------------
    PROVISIONING ──▶ TRIAL ──▶ ACTIVE ──▶ GRACE ──▶ SUSPENDED ──▶ CANCELLED
                       │         ▲   ▲       │           │            │
                       │         └───┴───────┴───────────┘            │
                       └──────────────────────────────────────────────┘

                 any of TRIAL/ACTIVE/GRACE/SUSPENDED/CANCELLED
                                    ▼
                                 ARCHIVED ──▶ back to whichever it came from

Read the back edges as: a tenant comes back. Grace returns to active when the
payment lands, suspension lifts when the dispute is settled, and a cancelled
customer who returns is REACTIVATED rather than recreated -- recreating would
mean a second Organization row and the loss of their history.

``CANCELLED`` is not terminal and ``PROVISIONING`` is not re-enterable: a
workspace is built once.

``ARCHIVED`` (Phase S6.5) is reachable from every live state and returns to
exactly the one it came from -- see ``tenancy.archive``, which stores that
status rather than guessing it later. It is NOT a stronger suspension: a
suspension is a dispute an operator expects to resolve, an archive is a
closed workspace whose data is being kept. The practical difference is that
nothing may be derived over an archive and nothing inside one may change,
because a restore has to be faithful.
"""
import logging

from django.db import transaction

from .exceptions import TenancyError

logger = logging.getLogger(__name__)


class IllegalOrganizationTransition(TenancyError):
    """An organization status move the lifecycle does not permit."""


def _status():
    from .models import Organization

    return Organization.Status


def allowed_transitions():
    """``{from: {to, ...}}``. Data, so the rule is readable and testable.

    Built lazily rather than at import time because it names the enum on
    ``Organization``, and this module is imported from ``models.py``'s own
    save path.
    """
    S = _status()
    return {
        # A workspace is built once. PROVISIONING is never re-entered.
        S.PROVISIONING: {S.TRIAL, S.ACTIVE, S.CANCELLED},
        S.TRIAL: {S.ACTIVE, S.GRACE, S.SUSPENDED, S.CANCELLED, S.ARCHIVED},
        S.ACTIVE: {S.GRACE, S.SUSPENDED, S.CANCELLED, S.ARCHIVED},
        S.GRACE: {S.ACTIVE, S.SUSPENDED, S.CANCELLED, S.ARCHIVED},
        # SUSPENDED -> GRACE exists because the subscription machine permits
        # it: a suspended tenant whose payment is accepted lands back inside
        # a grace window rather than on a fresh paid period.
        S.SUSPENDED: {S.ACTIVE, S.TRIAL, S.GRACE, S.CANCELLED, S.ARCHIVED},
        # Win-back.
        S.CANCELLED: {S.ACTIVE, S.TRIAL, S.ARCHIVED},
        # Phase S6.5. ARCHIVED accepts the five live states and returns to
        # them; PROVISIONING is excluded deliberately, because a workspace
        # that was never finished has nothing worth preserving and the
        # operator wants `cancel`, not `archive`.
        S.ARCHIVED: {S.TRIAL, S.ACTIVE, S.GRACE, S.SUSPENDED, S.CANCELLED},
    }


def is_legal(from_status, to_status):
    if from_status == to_status:
        return True
    return to_status in allowed_transitions().get(from_status, set())


def assert_legal(from_status, to_status):
    if is_legal(from_status, to_status):
        return
    allowed = sorted(allowed_transitions().get(from_status, set()))
    raise IllegalOrganizationTransition(
        f"An organization cannot move from '{from_status}' to "
        f"'{to_status}'. Allowed from '{from_status}': "
        f"{allowed or 'nothing'}.")


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------
@transaction.atomic
def transition_organization(organization, to_status, *, actor=None, reason="",
                            audit=True, request=None, override=False):
    """Move an organization's status, legally, and record who did it.

    Returns the organization. A no-op move (same status) still returns, and
    writes nothing.

    ``override`` marks the status as OPERATOR-SET, so the subscription mirror
    stops deriving over it -- see ``Organization.status_override``.

    IT DEFAULTS TO FALSE, and the reason is a bug this defaulted the other way
    and caused: provisioning finishes with ``PROVISIONING -> TRIAL`` through
    this function, so every newly provisioned tenant was pinned, and its
    subscription could never move its status again. A trial expiring left the
    organization showing TRIAL forever.

    So a pin is opt-in, for the two cases that genuinely need one:
    ``console.suspend`` and an operator choosing SUSPENDED or CANCELLED
    directly. Those are decisions about the CUSTOMER, which must outlive
    their balance changing. Everything else is a decision about the
    SUBSCRIPTION, and the subscription should keep owning it.
    """
    from django.utils import timezone

    from . import platform_audit

    from_status = organization.status
    assert_legal(from_status, to_status)

    fields = ["status", "updated_at"]
    if override:
        # Applied even when the status does not CHANGE. "Suspend this tenant"
        # arriving at an already-suspended tenant is still the operator
        # deciding to pin it, and that is the whole value of the call -- the
        # pin is what a later renewal cannot undo.
        organization.status_override = True
        organization.status_overridden_at = timezone.now()
        organization.status_override_reason = (reason or "")[:300]
        fields += ["status_override", "status_overridden_at",
                   "status_override_reason"]
    elif from_status == to_status:
        return organization

    organization.status = to_status
    # The guard in Organization.save() looks for exactly this flag, which is
    # what makes it mean "came through this function".
    organization._status_change_authorised = True
    organization.save(update_fields=fields)

    if from_status == to_status:
        # Pinned, not moved. No audit entry for a status that did not change;
        # the caller that asked for the pin records its own reason.
        return organization

    if audit:
        platform_audit.record(
            actor, _AUDIT_ACTION_FOR.get(to_status,
                                         _default_audit_action()),
            organization=organization,
            changes={"status": {"from": from_status, "to": to_status}},
            note=reason, request=request)

    logger.info("organization %s: %s -> %s", organization.slug,
                from_status, to_status)
    return organization


def _default_audit_action():
    from .models import PlatformAuditLog

    return PlatformAuditLog.Action.TENANT_UPDATED


def is_archived(organization):
    """Convenience, so callers do not each import the enum to ask."""
    return organization.status == _status().ARCHIVED


class _LazyActionMap(dict):
    """``{organization status: platform audit action}``, resolved on first use.

    A module-level literal cannot be built here: it would import the models at
    the time ``models.py`` is still being defined.
    """

    def get(self, key, default=None):
        if not self:
            from .models import Organization, PlatformAuditLog

            A = PlatformAuditLog.Action
            S = Organization.Status
            self.update({
                S.TRIAL: A.TRIAL_STARTED,
                S.ACTIVE: A.TENANT_ACTIVATED,
                S.GRACE: A.SUBSCRIPTION_CHANGED,
                S.SUSPENDED: A.TENANT_SUSPENDED,
                S.CANCELLED: A.TENANT_CANCELLED,
                S.ARCHIVED: A.TENANT_ARCHIVED,
            })
        return dict.get(self, key, default)


_AUDIT_ACTION_FOR = _LazyActionMap()


def apply_derived(organization, to_status):
    """Set the status the SUBSCRIPTION implies, without an audit entry.

    Used by ``services.sync_subscription_mirror``, where the audit record is
    the ``SubscriptionEvent`` that the same transaction already wrote -- a
    second entry describing the same change would make the trail look like two
    events.

    REFUSES TO MOVE AN OPERATOR-SET STATUS. If a platform operator suspended
    this tenant deliberately, a payment clearing tomorrow must not re-open the
    workspace -- the suspension was a decision about the customer, not about
    their balance. The mirror's OTHER columns (the dates, the subscription
    status) are still refreshed, so the console shows an operator exactly what
    it is looking at: a paid-up tenant that is suspended anyway, and the
    reason somebody recorded.

    Still validated when it does move. An illegal derived move means the
    subscription status map and this lifecycle disagree, which is a bug in the
    platform rather than anything an operator did, and it should surface as
    one.
    """
    # AN ARCHIVE IS NEVER DERIVED AWAY FROM, override or no override
    # (Phase S6.5). Archiving sets the override, so the next clause already
    # covers the normal case -- but `clear_override` exists, and a tenant
    # whose override was cleared while archived would be re-opened by the
    # next renewal, silently, with its data mid-preservation. The subscription
    # has no opinion about archiving and must not be able to end one.
    if organization.status == _status().ARCHIVED:
        logger.info(
            "organization %s: derived status %r not applied; the workspace is "
            "ARCHIVED and only a restore may reopen it", organization.slug,
            to_status)
        return organization

    if organization.status_override:
        logger.info(
            "organization %s: derived status %r not applied; status is "
            "operator-set to %r (%s)", organization.slug, to_status,
            organization.status, organization.status_override_reason)
        return organization
    assert_legal(organization.status, to_status)
    organization.status = to_status
    organization._status_change_authorised = True
    return organization


@transaction.atomic
def clear_override(organization, *, actor=None, reason="", request=None):
    """Hand the status back to the subscription.

    The counterpart of an operator suspension: once the dispute is settled,
    the tenant should follow its billing again rather than stay pinned to
    whatever the operator last chose. Called by ``console.activate``, because
    an operator letting a customer back in IS the decision to stop overriding.
    """
    if not organization.status_override:
        return organization
    organization.status_override = False
    organization.status_overridden_at = None
    organization.status_override_reason = ""
    organization.save(update_fields=["status_override", "status_overridden_at",
                                     "status_override_reason", "updated_at"])
    logger.info("organization %s: status override cleared", organization.slug)
    return organization
