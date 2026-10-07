"""The tenant-aware query foundation, and the one shim that makes Phase S2 safe.

TWO SEPARATE THINGS LIVE HERE
-----------------------------
1. ``active_organization()`` -- the compatibility shim. Phase A code (number
   generators, the login path, the User manager) needs to know "which tenant is
   this for?" even when there is no request: a management command, a data
   migration, a test, the nightly cron. This answers that question, and it is
   the ONLY place the single-tenant fallback is written down.

2. ``TenantQuerySet`` / ``TenantManager`` / ``TenantScopedModel`` -- the Phase
   S3 foundation. Introduced, tested, and ATTACHED TO NOTHING. Phase S2 adds
   ``organization`` columns to the Phase A models but deliberately does not
   switch their managers: a default manager that filters would change the
   behaviour of every existing query in the project, which is precisely the
   regression this phase must not cause.

WHY THE FALLBACK IS A SHIM AND NOT A DEFAULT
--------------------------------------------
While ``TENANCY_ENABLED`` is False there is exactly one organization, so
"the only one that exists" is not a guess -- it is the single correct answer,
and it is the behaviour the system already has. The moment the flag flips, or a
second organization appears, the fallback stops answering and
``TenantScopeMissing`` is raised instead. A shim that silently keeps guessing
after that point is how one tenant's rows get stamped with another's id.
"""
import logging

from django.conf import settings
from django.db import models

from . import resolver
from .context import current_org_id, migrations_running
from .exceptions import CrossTenantWrite, TenantScopeMissing

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The compatibility shim
# ---------------------------------------------------------------------------
def active_organization(*, required=True):
    """The Organization this work belongs to.

    Resolution order:

      1. the tenant in context (set by TenantResolutionMiddleware, by
         ``tenant_context()``, or by a management command),
      2. while ``TENANCY_ENABLED`` is False, the single existing organization,
      3. nothing -- raise, or return None when ``required=False``.

    Step 2 disappears when the flag flips. It also disappears the moment a
    second organization exists, because ``resolver.default_organization()``
    refuses to choose between two.
    """
    from .models import Organization

    org_id = current_org_id()
    if org_id is not None:
        organization = Organization.objects.filter(pk=org_id).first()
        if organization is not None:
            return organization
        logger.warning("tenant context names organization %s, which no longer "
                       "exists", org_id)

    # Inside `no_tenant()` the caller has said "platform-wide" explicitly, and
    # the single-tenant fallback must not undo that (Phase S5). Without this
    # check the platform console would be silently scoped to one customer.
    from .context import tenant_explicitly_unset

    if tenant_explicitly_unset():
        if required:
            raise TenantScopeMissing(
                "This block is inside tenancy.context.no_tenant(), which "
                "declares there is deliberately no tenant. Use the "
                "`all_tenants` manager for cross-tenant work.")
        return None

    if not settings.TENANCY_ENABLED:
        organization = resolver.default_organization(
            getattr(settings, "TENANCY_DEFAULT_SLUG", ""))
        if organization is not None:
            return organization

    if not required:
        return None
    raise TenantScopeMissing(
        "No organization in context, and no single default is available. "
        "Wrap this call in tenancy.context.tenant_context(org) -- a "
        "management command, cron job or data migration has no request to "
        "resolve one from.")


def active_organization_id(*, required=True):
    """``active_organization()``, but only the id. Saves a row fetch."""
    org_id = current_org_id()
    if org_id is not None:
        return org_id
    organization = active_organization(required=required)
    return organization.pk if organization is not None else None


# ---------------------------------------------------------------------------
# The Phase S3 foundation -- deliberately attached to nothing yet
# ---------------------------------------------------------------------------
class TenantQuerySet(models.QuerySet):
    """A queryset that knows how to narrow itself to one tenant.

    THE SCOPE IS APPLIED WHEN THE QUERYSET IS EVALUATED, NOT WHEN IT IS BUILT
    -- but only for a queryset built with no tenant in context. Phase S6 added
    that, and it is the fix for a defect that made the platform unbootable.

    ``TenantManager.get_queryset`` used to RAISE ``TenantScopeMissing`` the
    moment it was called with no tenant in context and TENANCY_ENABLED on.
    Building a queryset is not reading data, and declarative import-time code
    builds them constantly: django-filter calls
    ``Model._default_manager.all()`` while ``leaves/filters.py`` is being
    imported, in order to resolve a lookup expression. So with
    TENANCY_ENABLED=1 the URLconf could not be imported at all -- not a
    request, not a management command, not ``migrate``. The master switch the
    whole SaaS launch depends on made the application fail to start.

    Phase S4's note on the same method was about the related trap one level
    down: resolving the tenant must not QUERY at import time either. Both
    hazards have the same root -- a default manager is used by code that is
    only describing a query, long before anybody intends to run one.

    So: a tenant present at construction is still bound there (unchanged, and
    predictable). A queryset built with NO tenant carries a pending marker and
    resolves it at evaluation, which is the moment "which tenant am I" is
    actually knowable.
    """

    # Class-level default, so a queryset that never went through
    # TenantManager (AllTenantsManager, a plain .filter chain) is unaffected.
    _tenant_scope_pending = False

    def _clone(self):
        """Carry the pending marker across the many clones Django makes.

        Every ``filter``/``exclude``/``values``/``order_by`` returns a clone,
        and Django does not copy custom attributes -- so without this the
        marker is lost on the first chained call and the scope silently never
        applies.
        """
        clone = super()._clone()
        clone._tenant_scope_pending = self._tenant_scope_pending
        return clone

    def _apply_pending_tenant_scope(self):
        """Resolve the deferred scope. Called from every evaluation path.

        Idempotent and one-shot: the marker is cleared first, so a second
        evaluation of the same (now cached) queryset does not add the filter
        twice.
        """
        if not self._tenant_scope_pending:
            return
        self._tenant_scope_pending = False

        org_id = current_org_id()
        if org_id is not None:
            # add_q rather than .filter(): .filter() refuses once a slice has
            # been taken, and `Model.objects.all()[:10]` evaluated inside a
            # tenant context is a perfectly ordinary thing to do. SQL applies
            # WHERE before LIMIT, so scoping a sliced query is exactly right.
            self.query.add_q(models.Q(organization_id=org_id))
            return

        if settings.TENANCY_ENABLED and not migrations_running():
            raise TenantScopeMissing(
                f"{self.model.__name__} was READ with no organization in "
                f"context. Wrap the call in tenancy.context.tenant_context(), "
                f"or use {self.model.__name__}.all_tenants for a deliberate "
                f"cross-tenant read.")
        # Compatibility shim: one organization exists, so there is nothing to
        # scope. Same expiry as every other Phase S2-S4 shim.

    # -- every evaluation path -------------------------------------------
    #
    # Most reads funnel through _fetch_all (iteration, list, len, bool, repr,
    # get, first, last, latest, in_bulk). The rest are listed explicitly
    # because they build and execute their own SQL without filling the result
    # cache. Under RLS a missed path returns zero rows rather than another
    # tenant's, so the failure mode of an oversight here is loud-and-empty,
    # not a leak.
    def _fetch_all(self):
        self._apply_pending_tenant_scope()
        return super()._fetch_all()

    def iterator(self, *args, **kwargs):
        self._apply_pending_tenant_scope()
        return super().iterator(*args, **kwargs)

    def count(self):
        self._apply_pending_tenant_scope()
        return super().count()

    def exists(self):
        self._apply_pending_tenant_scope()
        return super().exists()

    def contains(self, obj):
        self._apply_pending_tenant_scope()
        return super().contains(obj)

    def aggregate(self, *args, **kwargs):
        self._apply_pending_tenant_scope()
        return super().aggregate(*args, **kwargs)

    def update(self, **kwargs):
        self._apply_pending_tenant_scope()
        return super().update(**kwargs)

    def delete(self):
        self._apply_pending_tenant_scope()
        return super().delete()

    def explain(self, *args, **kwargs):
        self._apply_pending_tenant_scope()
        return super().explain(*args, **kwargs)

    def for_organization(self, org_or_id):
        """Narrow to one organization. Accepts an instance or an id."""
        return self.filter(organization_id=getattr(org_or_id, "pk", org_or_id))

    def for_current_tenant(self):
        """Narrow to the tenant in context. Raises if there is none.

        Raises IMMEDIATELY, unlike the deferred scope above, and that is the
        difference between the two: this is an explicit request to scope, so
        there is no later moment at which the answer might arrive. Raising
        rather than returning ``none()`` because an empty result reads as
        "this tenant has no rows", which is a different and much more
        confusing answer than "nobody told me which tenant you meant".
        """
        return self.for_organization(active_organization_id())


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager that scopes every read to the tenant in context.

    IN USE ON ALL 55 PHASE B MODELS since Phase S4.

    ``get_queryset`` MUST NOT TOUCH THE DATABASE, AND THAT IS NOT AN
    OPTIMISATION
    ---------------------------------------------------------------------
    A default manager is used by declarative, import-time code. django-filter
    builds a ``ModelChoiceFilter`` from ``Model._default_manager`` while
    ``leaves/filters.py`` is being imported; DRF does the same for
    ``PrimaryKeyRelatedField``. The first version of this method resolved the
    tenant through ``active_organization_id()``, which falls back to a
    *database query* -- so merely importing the URLconf tried to read
    ``tenancy_organization`` before migrations had created it, and
    ``manage.py makemigrations`` died with "no such table".

    So the tenant comes from the CONTEXTVAR only. That is free, and it is
    always set on a real request (TenantResolutionMiddleware) or inside an
    explicit ``tenant_context`` block.

    WHAT HAPPENS WITH NO TENANT IN CONTEXT
    --------------------------------------
    The queryset is marked PENDING and the decision is taken when it is
    evaluated -- see ``TenantQuerySet``. At that point:

    * ``TENANCY_ENABLED = False`` (now): it is read UNFILTERED. There is
      exactly one organization, so there is nothing to scope, and import-time
      and management-command paths keep working unchanged. This is the same
      compatibility shim every other part of Phase S2-S4 uses, with the same
      expiry.
    * ``TENANCY_ENABLED = True``: ``TenantScopeMissing`` is raised. A read with
      no tenant is then a bug, and returning every tenant's rows would be the
      worst possible way to report it -- so it fails closed and loudly.

    DEFERRED RATHER THAN IMMEDIATE (Phase S6). Raising here broke importing
    the project with TENANCY_ENABLED=1; raising on evaluation says the same
    thing at the only moment it is true. See TenantQuerySet for the full
    story.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        org_id = current_org_id()
        if org_id is not None:
            # Bound here when the answer is already known: unchanged
            # behaviour, and predictable -- a queryset built inside
            # `tenant_context(A)` stays A's however it is passed around.
            return queryset.filter(organization_id=org_id)

        queryset._tenant_scope_pending = True
        return queryset


class AllTenantsManager(models.Manager.from_queryset(TenantQuerySet)):
    """Unscoped access. Deliberately a DIFFERENT, greppable name.

    Every intentional cross-tenant query reads ``Model.all_tenants`` at the
    call site, so "this crosses tenants" is one grep away and reviewable --
    rather than being the accidental default it is today.
    """


class TenantScopedModel(models.Model):
    """Abstract base for a tenant-owned table. NOT YET USED BY ANY MODEL.

    Phase S2 adds ``organization`` to the Phase A models as a plain field
    instead of inheriting from this, because inheriting would also install
    ``TenantManager`` as the default manager. Phase S3 does the swap, one app at
    a time, behind the feature flag.
    """

    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT,
        related_name="+", db_index=True,
    )

    objects = TenantManager()
    all_tenants = AllTenantsManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """Stamp the tenant on insert; refuse to write across tenants."""
        current = current_org_id()
        if self.organization_id is None:
            self.organization_id = active_organization_id()
        elif current is not None and self.organization_id != current:
            raise CrossTenantWrite(
                f"Refusing to save {type(self).__name__} owned by "
                f"organization {self.organization_id} while organization "
                f"{current} is in context.")
        super().save(*args, **kwargs)
