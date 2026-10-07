"""The tenant claims carried inside a JWT.

WHY THE TENANT LIVES IN THE TOKEN AND NOT IN THE URL
----------------------------------------------------
The hostname a request arrives on is user-controlled. The organization a user
belongs to is not. So the authoritative tenant for an authenticated request is
the one stamped into the token at login, and the middleware's job (Phase S3) is
to check that it AGREES with the host -- never to trust the host on its own.

Two claims, both small and both stable for the life of the token:

    org   the user's organization id, or absent for a platform account
    plat  true for a platform account, absent otherwise

They are separate because "no org" and "is platform staff" are different facts,
and a token that merely lacked ``org`` would be indistinguishable from one
issued before this phase existed.

SimpleJWT copies custom claims from a refresh token onto the access tokens it
mints (everything outside ``RefreshToken.no_copy_claims``), so stamping the
refresh token once at login is enough -- /auth/refresh/ keeps the claims
without any change to SafeTokenRefreshView.
"""

from rest_framework_simplejwt.settings import api_settings as _jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken as _RefreshToken


ORG_CLAIM = "org"
PLATFORM_CLAIM = "plat"


def stamp(token, user):
    """Write the tenant claims onto a freshly minted token. Returns the token."""
    if getattr(user, "is_platform_staff", False):
        token[PLATFORM_CLAIM] = True
        return token

    org_id = getattr(user, "organization_id", None)
    if org_id is not None:
        token[ORG_CLAIM] = str(org_id)
    return token


def org_id_from(token):
    """The organization id a token claims, or None.

    None means one of three things, and the caller must not conflate them with
    "any organization": a platform token, a token issued before this phase, or
    a user not yet attached to an organization.
    """
    try:
        value = token.get(ORG_CLAIM)
    except (AttributeError, TypeError):        # pragma: no cover - defensive
        return None
    return str(value) if value else None


def is_platform_token(token):
    try:
        return bool(token.get(PLATFORM_CLAIM))
    except (AttributeError, TypeError):        # pragma: no cover - defensive
        return False


# ---------------------------------------------------------------------------
# A refresh token whose own bookkeeping can read the user
# ---------------------------------------------------------------------------
class TenantSafeRefreshToken(_RefreshToken):
    """SimpleJWT's refresh token, with the user read the way this project allows.

    THE BUG THIS FIXES, found by reading the live console's server log rather
    than by a test. ``BlacklistMixin.blacklist`` and ``.outstand`` both need
    the token's user, to fill in ``OutstandingToken.user``, and both look it
    up with::

        get_user_model().objects.get(...)

    ``User.objects`` is the TENANT-SCOPED manager, and it refuses a read with
    no organization in context. Both of those methods run on paths where
    there is deliberately no organization to bind:

      * ``/api/v1/auth/refresh/`` is reached BEFORE authentication -- the
        token is the credential -- and on the platform console's own hostname
        resolves to no tenant at all.
      * ``/api/v1/auth/logout/`` blacklists the caller's refresh token, and a
        platform operator's request carries platform scope, not a tenant.

    So each answered HTTP 500 for a platform operator: their session could
    never be renewed and could never be ended. They were signed out whenever
    their access token expired, with a 500 in the log and nothing on screen
    to explain it, and signing out properly was impossible -- which also left
    the token un-blacklisted, so "log out" did not actually revoke anything.

    WHY ``all_tenants`` AND NOT A SCOPE. There is no scope that would work:
    a platform operator's ``User`` row has ``organization IS NULL``, so there
    is no organization to bind, and ``no_tenant()`` does not help --
    ``scoping._apply_pending_tenant_scope`` refuses whenever nothing is bound
    and does not consult ``tenant_explicitly_unset()``. ``all_tenants`` is
    this project's one sanctioned cross-tenant read, and
    ``tenancy.authentication.TenantJWTAuthentication.get_user`` already reads
    the user that way for exactly the same reason.

    NOTHING IS WIDENED BY IT. The id comes from a SIGNED token, so this
    decides only which id is being asked about, never which rows the caller
    may see. Under row-level security the CONNECTION still governs
    visibility, the tenant binding is still checked at authentication (where
    ``middleware._refuse_wrong_tenant`` rejects a token presented on another
    tenant's hostname), and ``OutstandingToken`` is platform-global by
    classification already -- see ``tenancy.inventory``.

    The two methods below are the library's, with that one manager swapped
    and the duplicated lookup factored out. They exist only while the
    blacklist app is installed, mirroring the library's own conditional, so
    that removing it from INSTALLED_APPS still raises the ``AttributeError``
    SimpleJWT catches rather than finding a method that cannot work.
    """

    def _user_across_tenants(self):
        from django.contrib.auth import get_user_model

        user_id = self.payload.get(_jwt_settings.USER_ID_CLAIM)
        if not user_id:
            return None
        return get_user_model().all_tenants.filter(
            **{_jwt_settings.USER_ID_FIELD: user_id}).first()

    if hasattr(_RefreshToken, "blacklist"):

        def _ensure_outstanding(self):
            from rest_framework_simplejwt.token_blacklist.models import (
                OutstandingToken,
            )
            from rest_framework_simplejwt.utils import datetime_from_epoch

            return OutstandingToken.objects.get_or_create(
                jti=self.payload[_jwt_settings.JTI_CLAIM],
                defaults={
                    "user": self._user_across_tenants(),
                    "created_at": self.current_time,
                    "token": str(self),
                    "expires_at": datetime_from_epoch(self.payload["exp"]),
                },
            )

        def blacklist(self):
            from rest_framework_simplejwt.token_blacklist.models import (
                BlacklistedToken,
            )

            token, _created = self._ensure_outstanding()
            return BlacklistedToken.objects.get_or_create(token=token)

        def outstand(self):
            return self._ensure_outstanding()
