"""JWT authentication that can resolve a user in a multi-tenant database.

THE DEFECT THIS FIXES. ``rest_framework_simplejwt``'s ``JWTAuthentication``
resolves the token's subject with::

    User.objects.get(pk=user_id)

``User.objects`` has been tenant-scoped since Phase S5, which makes that query
wrong in two different ways once ``TENANCY_ENABLED`` is on:

  * **A PLATFORM OPERATOR CAN NEVER BE FOUND.** Their row has
    ``organization IS NULL`` by database constraint, and a console request
    arrives on a platform host where no tenant is bound -- so the scoped
    manager has no tenant, refuses, and EVERY authenticated request to the
    platform console answered HTTP 500. The console was unusable.

  * A tenant user is scoped, which is right, but by accident rather than by
    decision: nothing stated the rule, so nothing could be relied on.

WHY ``all_tenants`` IS THE RIGHT MANAGER HERE, AND NOT A HOLE
-------------------------------------------------------------
Under row-level security the database already applies exactly the correct
narrowing, per request, without any application logic:

    on a TENANT host    app.current_org = that tenant  -> only its users
    on a PLATFORM host  app.current_org = '',
                        app.platform    = 'on'         -> only unowned rows,
                                                          i.e. the operators

So the unscoped manager asks "which user is this token for?" and the
connection decides which users are visible. That is the boundary doing its
job, and it is why this is not a widening.

With RLS OFF -- development, SQLite, the single-tenant deployment NIF runs
today -- the database cannot help, so the same rule is applied in Python by
``_refuse_wrong_tenant`` below. The two agree by construction; the test suite
runs both ways.

WHY THE CHECK LIVES HERE AND NOT IN THE MIDDLEWARE
--------------------------------------------------
``TenantResolutionMiddleware._enforce`` already refuses a cross-tenant
request -- but it reads ``request.user``, which for a JWT API call is
``AnonymousUser``: DRF authenticates inside the view, long after the
middleware has run. So for an API request the middleware's check never fires,
and this is the first place the token's subject is known.
"""
from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.settings import api_settings


class TenantJWTAuthentication(JWTAuthentication):
    """Resolve the token's user, then refuse it if it belongs to another tenant."""

    def get_user(self, validated_token):
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError:
            raise AuthenticationFailed(
                "Token contained no recognizable user identification",
                code="user_identification_failed")

        from django.contrib.auth import get_user_model

        User = get_user_model()
        # `all_tenants`: see the module docstring. The CONNECTION decides which
        # users are visible; this only decides which id is being asked for.
        user = User.all_tenants.filter(
            **{api_settings.USER_ID_FIELD: user_id}).first()

        if user is None:
            raise AuthenticationFailed("User not found", code="user_not_found")
        if not user.is_active:
            raise AuthenticationFailed("User is inactive", code="user_inactive")

        self._refuse_wrong_tenant(user)
        # Adoption: counted once per person per day. Never raises.
        from .adoption import record_active

        record_active(user)
        return user

    @staticmethod
    def _refuse_wrong_tenant(user):
        """The application-level half of the rule. Inert until enforcement is on.

        Gated on ``TENANCY_ENABLED`` so the single-tenant deployment running
        today is untouched: there, the compatibility shim binds NIF even on a
        platform host, and refusing a platform operator for that would lock
        the console out of the one deployment shape that has no separate
        console hostname.
        """
        if not settings.TENANCY_ENABLED:
            return

        from .context import current_org_id

        bound = current_org_id()

        if user.is_platform_staff:
            # A platform account has no workspace to be in. The middleware says
            # the same thing for session requests; this covers API ones.
            if bound is not None:
                raise AuthenticationFailed(
                    "Platform accounts cannot access a tenant workspace",
                    code="platform_account_on_tenant_host")
            return

        if bound is None:
            # A tenant user on a host that resolves to no tenant -- the console
            # host. Refused rather than served: the alternative is an
            # unscoped session.
            raise AuthenticationFailed(
                "This account does not belong here", code="no_workspace")

        if str(user.organization_id) != str(bound):
            # A valid token replayed against another tenant's subdomain.
            raise AuthenticationFailed(
                "This account does not belong to this workspace",
                code="wrong_workspace")
