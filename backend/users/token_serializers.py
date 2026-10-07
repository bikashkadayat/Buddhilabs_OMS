from rest_framework import serializers, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.views import TokenRefreshView
from django.contrib.auth import get_user_model

from documents.protected_media import signed_media_url
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action

from tenancy import tokens as tenant_tokens
from tenancy.tokens import TenantSafeRefreshToken

from . import login_security, tenant_login

User = get_user_model()


class TenantSafeTokenRefreshSerializer(TokenRefreshSerializer):
    """SimpleJWT's refresh, with the user read the way this project allows.

    THE BUG. `TokenRefreshSerializer.validate` looks the user up with
    `get_user_model().objects.get(...)` -- the tenant-scoped default manager.
    Refresh runs BEFORE authentication (the token is the credential), and on
    the platform console's own hostname no tenant is bound, by design. So the
    scoped read raised `TenantScopeMissing` and the endpoint answered HTTP
    500: a platform operator's session could never be renewed, and they were
    signed out whenever their access token expired, with a 500 in the log and
    nothing on screen to explain it.

    WHY NOT A SCOPE. `no_tenant()` does not help: `_apply_pending_tenant_scope`
    refuses whenever no organization is bound and does not consult
    `tenant_explicitly_unset()`. `all_tenants` is the only sanctioned
    cross-tenant read, and the right one here -- as
    `tenancy.authentication.TenantJWTAuthentication.get_user` already does for
    the same reason: the CONNECTION decides which users are visible, this only
    decides which id is being asked about.

    NOTHING IS WIDENED. The user is still checked against
    `USER_AUTHENTICATION_RULE`, the view above still pre-validates, and the
    tenant binding is still enforced at authentication, where
    `_refuse_wrong_tenant` rejects the resulting access token on any host but
    its own.
    """

    # Every token this view touches is minted, parsed and blacklisted
    # through the tenant-safe subclass -- see tenancy.tokens.
    token_class = TenantSafeRefreshToken

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])
        user_id = refresh.payload.get(jwt_settings.USER_ID_CLAIM, None)
        if user_id:
            user = User.all_tenants.filter(
                **{jwt_settings.USER_ID_FIELD: user_id}).first()
            if user is None or not jwt_settings.USER_AUTHENTICATION_RULE(user):
                raise AuthenticationFailed(
                    "No active account found for the given token.",
                    "no_active_account")

        data = {"access": str(refresh.access_token)}

        # Rotation, byte for byte as SimpleJWT does it. Reimplemented rather
        # than delegated because `super().validate()` is what performs the
        # scoped read this class exists to avoid.
        if jwt_settings.ROTATE_REFRESH_TOKENS:
            if jwt_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()
            data["refresh"] = str(refresh)

        return data


class SafeTokenRefreshView(TokenRefreshView):
    """
    Token refresh that returns 401 (not 500) when the refresh token references a
    user who no longer exists or has been deactivated.

    Discovered in Phase 1: SimpleJWT looks the user up with an unguarded
    User.objects.get(); a stale token (e.g. after a DB swap, or a deleted user)
    raised an uncaught DoesNotExist -> 500. We pre-validate the token's user so
    the frontend receives a clean 401 and can redirect to login.
    """

    permission_classes = [AllowAny]  # H6: explicitly public (token is the credential)
    serializer_class = TenantSafeTokenRefreshSerializer

    def post(self, request, *args, **kwargs):
        user = None
        refresh = request.data.get("refresh")
        if refresh:
            try:
                token = TenantSafeRefreshToken(refresh)  # verifies signature + expiry
            except TokenError as exc:
                raise InvalidToken(exc.args[0])
            user_id = token.get(jwt_settings.USER_ID_CLAIM)
            # `all_tenants`, NOT `objects`, and this is a bug fix rather than
            # a convenience.
            #
            # Refresh runs with NO TENANT IN CONTEXT: it is reached before
            # authentication (the token IS the credential), and on the
            # platform console's own hostname there is no tenant to bind at
            # all. `User.objects` is tenant-scoped and refuses a read with
            # nothing bound, so this line raised `TenantScopeMissing` and the
            # endpoint answered HTTP 500 -- meaning a platform operator's
            # session could never be refreshed, and they were signed out
            # whenever their access token expired.
            #
            # `tenancy.authentication.TenantJWTAuthentication` already reads
            # the user this way for exactly the same reason: the CONNECTION
            # decides which users are visible, and this only decides which id
            # is being asked about. The validity checks below are unchanged,
            # and the tenant binding is still enforced at authentication.
            user = User.all_tenants.filter(
                **{jwt_settings.USER_ID_FIELD: user_id}).first()
            if user is None or not user.is_active:
                raise AuthenticationFailed(
                    "Account for this session is no longer valid. Please sign in again.",
                    code="user_invalid",
                )
        try:
            return super().post(request, *args, **kwargs)
        except ObjectDoesNotExist:
            raise AuthenticationFailed("Token user no longer exists.", code="user_not_found")


class EmailLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')

        if not email or not password:
            raise serializers.ValidationError('Email and password are required.')

        # Phase S2: scoped to the organization resolved from the request's host.
        #
        # This WAS `User.objects.get(email=email)`, and `email` has no global
        # unique constraint -- so two users sharing an address raised
        # MultipleObjectsReturned, an uncaught 500 on the login endpoint. The
        # lookup now goes through users.tenant_login, which is scoped to one
        # tenant and cannot return more than one row. See that module for why
        # email-plus-host was chosen over the alternatives.
        user = tenant_login.find_by_email(
            email, request=self.context.get('request'))
        if user is None:
            raise serializers.ValidationError('Invalid email or password.')

        if not user.check_password(password):
            raise serializers.ValidationError('Invalid email or password.')

        # is_active is deliberately NOT checked here; the view returns a 403 with
        # a clear message (and audits it) for deactivated accounts.
        attrs['user'] = user
        return attrs


class EmailLoginView(APIView):
    """
    UNIFIED login for all roles (Phase 2.5). Returns JWT tokens plus a `user`
    block (including role + must_change_password) so the frontend can enforce a
    first-login password change and redirect by role. URL is unchanged:
    POST /api/v1/auth/login/

    Phase 11 (audit findings H1/H2) adds three things this endpoint lacked:
    a dedicated per-IP throttle, per-account lockout, and an audit record for
    every failed attempt. Previously a wrong password was recorded nowhere, so
    a brute-force attempt left no trace at all.
    """
    permission_classes = [AllowAny]
    # BOTH throttles, not just the new one. Setting `throttle_classes` REPLACES
    # the defaults, so listing only LoginRateThrottle silently removed the
    # global AnonRateThrottle from the single most attacked endpoint in the
    # system — caught by `test_anon_throttle_enforced`. DRF applies every
    # throttle listed and any one of them can reject, so keeping both means the
    # login-specific ceiling is additional to the global one rather than
    # instead of it.
    throttle_classes = [login_security.LoginRateThrottle, AnonRateThrottle]

    def post(self, request):
        email = (request.data.get("email") or "").strip()

        # The tenant being logged INTO, resolved from the host by
        # TenantResolutionMiddleware. Passed explicitly rather than read from
        # the contextvar inside login_security, so the lockout counter and the
        # credential lookup below can never disagree about which tenant this
        # attempt belongs to (Phase S3).
        organization = tenant_login.organization_for(request)

        # Checked BEFORE credentials are verified: a locked account must not
        # reveal, through a different error, whether the password was right.
        locked, remaining = login_security.is_locked(email, organization)
        if locked:
            log_action(None, AuditLog.Action.OTHER,
                       changes={**login_security.failure_changes(
                           email, request,
                           login_security.failure_count(email, organization),
                           reason="account_locked")},
                       request=request)
            minutes = max(1, remaining // 60)
            return Response(
                {'detail': f'Too many failed attempts. Try again in about '
                           f'{minutes} minute{"s" if minutes != 1 else ""}, or '
                           f'contact HR.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = EmailLoginSerializer(data=request.data,
                                          context={'request': request})
        if not serializer.is_valid():
            self._record_failure(email, request, organization)
            # Surface a single, human-readable message under `detail` so the
            # frontend shows "Invalid email or password." instead of a generic
            # "Request failed with status code 400". DRF nests validate() errors
            # under `non_field_errors`; pull the first available message out.
            errors = serializer.errors
            message = 'Invalid email or password.'
            non_field = errors.get('non_field_errors')
            if non_field:
                message = non_field[0]
            elif errors:
                first = next(iter(errors.values()))
                if isinstance(first, (list, tuple)) and first:
                    message = first[0]
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data['user']

        if not user.is_active:
            log_action(None, AuditLog.Action.OTHER, instance=user,
                       changes={'event': 'LOGIN_BLOCKED_INACTIVE'}, request=request)
            return Response(
                {'detail': 'Your account has been turned off. Please contact your HR team or administrator.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # A correct password clears the counter: an unlucky morning must not
        # accumulate towards a lockout later in the day.
        login_security.clear_failures(email, organization)
        refresh = TenantSafeRefreshToken.for_user(user)
        # Phase S1. Stamp the tenant claims (`org` / `plat`) into the token.
        # Additive and inert: nothing reads them until Phase S3 switches
        # enforcement on in tenancy.middleware. SimpleJWT copies custom claims
        # from the refresh token onto every access token it mints, so this one
        # call also covers /auth/refresh/.
        tenant_tokens.stamp(refresh, user)

        # A PLATFORM OPERATOR'S SESSION IS SET UP IN PLATFORM SCOPE (Phase S6).
        #
        # Their User row has `organization IS NULL`, so under row-level
        # security it is only writable on a connection that has declared
        # platform scope. The middleware declares it from the HOST -- which
        # works on a deployment with a console hostname, and cannot on a
        # SINGLE-HOST one, where a platform login is indistinguishable from a
        # tenant's until the credentials are resolved.
        #
        # By here they are resolved, so the request can say what it is. Without
        # this, `update_last_login` below matched zero rows and raised
        # "Save with update_fields did not affect any rows" -- a 500 on the
        # console's own sign-in, on exactly the deployment shape NIF runs
        # today.
        from tenancy.context import no_tenant

        import contextlib

        scope = (no_tenant() if user.is_platform_staff
                 else contextlib.nullcontext())
        with scope:
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])
            log_action(user, AuditLog.Action.LOGIN, instance=user,
                       changes={'event': 'LOGIN_SUCCESS'}, request=request)

        # Phase S6.75 Part 4. The audit entry above is TENANT data and
        # policed by row-level security, so the platform console counts zero
        # of them; this counter is the only way logins reach a platform
        # dashboard without giving the console a way to read customer rows.
        # It holds a count per tenant per day and nothing identifying.
        from tenancy import metrics
        from tenancy.models import PlatformMetric

        metrics.bump(PlatformMetric.Key.LOGIN_OK,
                     organization=user.organization_id)

        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': str(user.id),
                'employee_id': user.employee_id,
                'full_name': user.full_name,
                'username': user.username,
                'email': user.email,
                'role': user.role,
                'department': user.department_name,
                'designation': user.designation,
                'must_change_password': user.must_change_password,
                # WHICH APPLICATION TO SHOW (Phase S6).
                #
                # /auth/user/ carries these, but the frontend's login() reads
                # THIS block to decide where to send the operator -- so
                # without them a platform operator's first sign-in redirected
                # to the tenant workspace, and only a page reload (which calls
                # /auth/user/) put them in the console. Found by signing in
                # against a real server; no test client path reads this dict.
                'is_platform_staff': user.is_platform_staff,
                'organization_slug': (user.organization.slug
                                      if user.organization_id else None),
                # SIGNED, like every other place this field is serialised.
                #
                # This was `user.profile_photo.url` — the raw /media/ path,
                # which the project deliberately does not serve (config.urls
                # removed the public catch-all; files come only through
                # documents.protected_media). So every login handed the client
                # a URL guaranteed to 404, and the avatar fell back to
                # initials until something remounted it with the signed URL
                # that /auth/user/ returns a moment later.
                #
                # Bound to the same user, so the link is useless to anyone
                # else if it leaks (Phase 11, M7).
                'profile_photo': (
                    signed_media_url(user.profile_photo.name, user=user)
                    if user.profile_photo else None
                ),
            },
        })

    @staticmethod
    def _record_failure(email, request, organization=None):
        """Count and audit a failed attempt.

        Audited even when the email belongs to nobody: an attacker probing for
        valid addresses is exactly the pattern worth seeing, and it is invisible
        if only real accounts are recorded.
        """
        failures, locked_now = login_security.record_failure(email, organization)
        log_action(None, AuditLog.Action.OTHER,
                   changes=login_security.failure_changes(email, request, failures),
                   request=request)

        # Phase S6.75 Part 4: the platform-side counter. `organization` is
        # None when the attempted address belongs to nobody, which is the
        # pattern most worth watching -- so the counter keeps that row
        # unattributed rather than dropping it.
        from tenancy import metrics
        from tenancy.models import PlatformMetric

        metrics.bump(PlatformMetric.Key.LOGIN_FAILED,
                     organization=getattr(organization, "pk", organization))
        if locked_now:
            log_action(None, AuditLog.Action.OTHER,
                       changes=login_security.lockout_changes(email, request, failures),
                       request=request)


class LogoutView(APIView):
    """
    Blacklist the caller's refresh token so it can no longer be used to mint new
    access tokens (M3). Requires a valid access token plus the refresh token in
    the body.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get('refresh')
        if not refresh:
            return Response({'detail': 'A refresh token is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            TenantSafeRefreshToken(refresh).blacklist()
        except TokenError:
            return Response({'detail': 'Invalid or already-expired token.'},
                            status=status.HTTP_400_BAD_REQUEST)
        log_action(request.user, AuditLog.Action.OTHER, instance=request.user,
                   changes={'event': 'LOGOUT'}, request=request)
        return Response(status=status.HTTP_205_RESET_CONTENT)
