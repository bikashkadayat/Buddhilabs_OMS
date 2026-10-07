"""Forgot password: request a link, follow it, choose a new password.

WHY IT DID NOT EXIST. A tenant's users could only be reset by an
administrator, and a customer's ONLY administrator could only be reset by the
platform team -- every forgotten password was a support ticket, and the
sign-in page could say no more than "ask somebody".

THE FOUR RULES THIS FILE IS BUILT AROUND

1. The request never says whether an account exists. Same answer, same
   status, for a known address and an unknown one: otherwise the form is a
   directory of who works where.

2. The account is found the way sign-in finds it -- by email WITHIN the
   organization this hostname belongs to (`users.tenant_login`). A reset
   requested on abc.example.com can only ever reach an ABC account, and a
   platform operator's only on the console's own host.

3. The link is built from the organization's CANONICAL address, never from
   the request's Host header. Host is user-supplied; a reset email whose link
   points wherever the requester's Host said is the classic way to steal
   reset tokens.

4. The token is Django's: bound to the user's current password hash and last
   sign-in, so it dies the moment the password changes or the person signs
   in, and it expires after PASSWORD_RESET_TIMEOUT. One link, one use.

On success every existing session is ended -- somebody resetting a password
they did not forget is often somebody whose account is in use by someone
else -- and any sign-in lockout on the address is cleared.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

SENT = ("If an account exists for that email, we’ve sent a link to reset the "
        "password. It works for {minutes} minutes. Check your spam folder if "
        "it doesn’t arrive.")
BAD_LINK = ("This reset link has expired or has already been used. Ask for a "
            "new one and use it within {minutes} minutes.")


def _minutes():
    return max(1, int(getattr(settings, "PASSWORD_RESET_TIMEOUT", 3600)) // 60)


def _base_url(user):
    """Where this person signs in. Never the request's Host."""
    if getattr(user, "is_platform_staff", False):
        from tenancy import resolver

        hosts = resolver.platform_hosts()
        if hosts:
            return f"https://{hosts[0]}/"
        return getattr(settings, "FRONTEND_URL", "").rstrip("/") + "/"
    organization = getattr(user, "organization", None)
    if organization is None:
        return getattr(settings, "FRONTEND_URL", "").rstrip("/") + "/"
    from tenancy.handover import login_url

    return login_url(organization)


def reset_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{_base_url(user).rstrip('/')}/reset-password?uid={uid}&token={token}"


def _send(user, link):
    """The email, in the organization's own branding. Raises on failure."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from notifications.emails import _branding, powered_by

    if user.organization_id:
        from tenancy.context import tenant_context

        with tenant_context(user.organization_id):
            brand = _branding()
    else:
        brand = {"org_name": getattr(settings, "PLATFORM_NAME", "Buddhi Labs"),
                 "logo_url": None, "brand_color": ""}
    context = {**brand, "powered_by": powered_by(),
               "name": user.first_name or user.get_full_name() or "",
               "link": link, "minutes": _minutes(),
               "support_email": getattr(settings, "PLATFORM_SUPPORT_EMAIL", "")}
    message = EmailMultiAlternatives(
        f"Reset your {brand.get('org_name') or 'account'} password",
        render_to_string("emails/password_reset.txt", context),
        settings.DEFAULT_FROM_EMAIL, [user.email])
    message.attach_alternative(
        render_to_string("emails/password_reset.html", context), "text/html")
    message.send(fail_silently=False)


def request_reset(email, request):
    """Send a link if the address is an active account here. Says nothing."""
    from audit.models import AuditLog
    from audit.services import log_action

    from . import tenant_login

    user = tenant_login.find_by_email(email, request=request)
    if user is None or not user.is_active or not user.email:
        return
    if user.is_platform_staff and not tenant_login.platform_login_allowed(request):
        # An operator's address typed on a customer's sign-in page. Sign-in
        # refuses it there; so does this.
        return
    try:
        _send(user, reset_link(user))
    except Exception:                              # noqa: BLE001
        # Logged, not raised: the answer must not reveal that the address
        # exists by failing differently for it.
        logger.warning("password reset email to user %s failed", user.pk,
                       exc_info=True)
        return
    log_action(user, AuditLog.Action.OTHER, instance=user,
               changes={"event": "PASSWORD_RESET_REQUESTED"}, request=request)


def _user_from(uid):
    User = get_user_model()
    try:
        pk = force_str(urlsafe_base64_decode(uid or ""))
        # all_tenants: the link is followed before anybody is signed in. The
        # hostname's tenant binding (and row-level security under it) still
        # decides which accounts this request can see at all.
        return User.all_tenants.select_related("organization").get(pk=pk)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError,
            DjangoValidationError):
        return None


def check_link(uid, token):
    user = _user_from(uid)
    if user is None or not user.is_active or \
            not default_token_generator.check_token(user, token or ""):
        return None
    return user


def complete_reset(uid, token, new_password, request):
    """Set the new password. Returns the user, or raises ValidationError."""
    from audit.models import AuditLog
    from audit.services import log_action

    from . import login_security

    user = check_link(uid, token)
    if user is None:
        raise ValidationError({"detail": BAD_LINK.format(minutes=_minutes())})
    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as exc:
        raise ValidationError({"new_password": list(exc.messages)}) from exc

    user.set_password(new_password)
    user.must_change_password = False
    user.last_password_change = timezone.now()
    user.save(update_fields=["password", "must_change_password",
                             "last_password_change"])
    _end_sessions(user)
    # The same key sign-in counts against: the host's organization, which
    # is None on the console's host for an operator.
    from . import tenant_login
    login_security.clear_failures(user.email, tenant_login.organization_for(request))
    log_action(user, AuditLog.Action.UPDATE, instance=user,
               changes={"event": "PASSWORD_RESET_COMPLETED"}, request=request)
    return user


def _end_sessions(user):
    """Revoke every refresh token this account holds. Never raises."""
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken, OutstandingToken,
        )

        for row in (OutstandingToken.objects
                    .filter(user=user, blacklistedtoken__isnull=True)):
            BlacklistedToken.objects.get_or_create(token=row)
    except Exception:                              # noqa: BLE001
        logger.warning("could not end sessions after reset for %s", user.pk,
                       exc_info=True)


class PasswordResetRequestView(APIView):
    """POST {email}. Always the same answer."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        if not email or "@" not in email:
            raise ValidationError({"email": "Enter the email address you sign in with."})
        request_reset(email, request)
        return Response({"detail": SENT.format(minutes=_minutes())},
                        status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(APIView):
    """GET ?uid&token checks a link; POST {uid, token, new_password} uses it."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def get(self, request):
        user = check_link(request.query_params.get("uid"),
                          request.query_params.get("token"))
        if user is None:
            return Response({"valid": False,
                             "detail": BAD_LINK.format(minutes=_minutes())})
        return Response({"valid": True, "email": user.email})

    def post(self, request):
        complete_reset(request.data.get("uid"), request.data.get("token"),
                       request.data.get("new_password") or "", request)
        return Response({"detail": "Your password has been changed. Sign in with "
                                   "your new password."})
