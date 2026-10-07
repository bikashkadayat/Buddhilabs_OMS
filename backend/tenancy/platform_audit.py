"""The platform audit trail. ONE writer, and it is this module.

Phase S6 Part 9 requires every tenancy action to be recorded: tenant created,
tenant suspended, subscription changed, plan changed, branding changed. The
record has to answer three questions months later, in a billing dispute or a
security review:

    who did it, to which tenant, and what exactly changed.

WHY A SEPARATE TRAIL FROM ``audit.AuditLog``
--------------------------------------------
See ``tenancy.models.PlatformAuditLog``. In one line: a suspended tenant's own
audit table is unreadable by the tenant and must not be the only copy of the
reason it was suspended.

NEVER RAISES
------------
``record()`` swallows its own failures and logs them. That is a deliberate and
slightly uncomfortable choice. The alternative -- letting an audit write abort
the transaction -- means a malformed ``changes`` payload can stop an operator
suspending a non-paying customer. Losing one audit row is bad; being unable to
act on the platform is worse. The failure is logged at ERROR so it is visible
rather than silent, and every call site is inside the service it describes, so
the action itself is still atomic with its SubscriptionEvent.
"""
import logging

logger = logging.getLogger(__name__)


def record(actor, action, *, organization=None, changes=None, note="",
           request=None):
    """Append one entry. Returns it, or ``None`` if it could not be written."""
    from .models import PlatformAuditLog

    try:
        org = organization
        meta = getattr(request, "META", {}) or {}
        return PlatformAuditLog.objects.create(
            organization=org,
            organization_slug=getattr(org, "slug", "") or "",
            organization_name=getattr(org, "name", "") or "",
            action=action,
            actor=actor if getattr(actor, "pk", None) else None,
            actor_email=(getattr(actor, "email", "") or "")[:254],
            changes=changes,
            note=note or "",
            ip_address=_client_ip(meta),
            user_agent=(meta.get("HTTP_USER_AGENT") or "")[:300],
        )
    except Exception:  # noqa: BLE001 - see the module docstring
        logger.exception(
            "platform audit write FAILED for action=%s organization=%s; the "
            "action itself was not rolled back", action,
            getattr(organization, "slug", organization))
        return None


def _client_ip(meta):
    """The caller's address, preferring the proxy header the deployment sets.

    Takes the FIRST entry of X-Forwarded-For, which is the client as the edge
    saw it; the entries after it are the proxies in between. A trusting read of
    the LAST entry records the load balancer for every single request, which is
    an audit column that never varies and therefore never helps.
    """
    forwarded = (meta.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    candidate = forwarded or meta.get("REMOTE_ADDR") or ""
    # The column is GenericIPAddressField; a spoofed header must not be able to
    # fail the write (and with it the audit entry) with a ValidationError.
    try:
        import ipaddress

        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def entries(*, organization=None, action=None, limit=200):
    """The trail, newest first. Platform-only -- callers check authority."""
    from .models import PlatformAuditLog

    queryset = (PlatformAuditLog.objects
                .select_related("organization", "actor")
                .order_by("-created_at"))
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    if action:
        queryset = queryset.filter(action=action)
    return queryset[:limit]
