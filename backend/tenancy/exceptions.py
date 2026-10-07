"""Tenancy errors, separated from the services that raise them.

Each one exists so a caller can distinguish a *refusal* (a rule said no) from a
*bug* (something was None that should not have been). Views map them to status
codes; nothing should be catching bare Exception around this layer.
"""


class TenancyError(Exception):
    """Base class for every refusal in this app."""


class TenantScopeMissing(TenancyError):
    """Tenant-scoped work was attempted with no organization in context.

    Raised rather than silently defaulting. A default here is how one tenant's
    rows end up stamped with another tenant's id.
    """


class CrossTenantWrite(TenancyError):
    """A write was attempted against an organization other than the current one."""


class IllegalTransition(TenancyError):
    """A subscription status change that the lifecycle does not permit."""


class DirectStatusChangeForbidden(TenancyError):
    """Subscription.status was assigned outside tenancy.services.transition().

    The lifecycle is the only thing allowed to move a subscription, because it
    is also what writes the SubscriptionEvent audit row and refreshes the
    Organization mirror. A bare `sub.status = "active"; sub.save()` would skip
    both and leave the platform lying about what a customer has paid for.
    """


class IllegalPaymentTransition(TenancyError):
    """A payment verification step out of order (or replayed)."""


class NoActivePrice(TenancyError):
    """A plan has no PlanPrice effective on the requested date."""
