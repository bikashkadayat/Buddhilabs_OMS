"""Phase S7: the three public endpoints, and the onboarding one behind them.

THESE ARE THE ONLY UNAUTHENTICATED WRITE ENDPOINTS ON THE PLATFORM.
Everything else either sits behind a tenant session, a platform session, or an
HMAC signature. So each one here carries its own throttle scope, and the
reasons for the ceilings are in ``config/settings.py`` next to the numbers.

WHY THE RESPONSES ARE DELIBERATELY UNINFORMATIVE ABOUT PEOPLE.
``POST /register/`` answers the same way whether the address was new, already
had a pending registration, or belongs to an existing tenant's administrator.
A signup form that says "this email is already registered" is a membership
oracle: it tells a stranger which addresses have workspaces here, one request
at a time, and the rate limit does not help because they only need one request
per address.

The subdomain checker is the deliberate exception -- see its docstring.
"""
import logging

from django.conf import settings
from rest_framework import status as http_status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import registration, resolver
from .exceptions import TenancyError
from .registration_serializers import (RegistrationSerializer,
                                       VerificationSerializer)

logger = logging.getLogger(__name__)


class PublicRegistrationMixin:
    """Open to anybody, refused on a tenant's own hostname.

    YOU DO NOT SIGN UP INSIDE SOMEBODY ELSE'S WORKSPACE. A registration form
    served on ``abcschool.platform.com`` would read as ABC School inviting
    you to create an account with them, and the workspace it created would
    have nothing to do with theirs. Registration belongs to the platform
    host, exactly as the console does.

    It stands down when no platform host is configured, for the same reason
    ``PlatformConsoleMixin`` does: in the single-host deployment NIF runs
    today every host resolves to the one tenant, and refusing there would
    make the feature unreachable rather than correctly placed.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not registration.is_open():
            # 404, not 403: a deployment that does not sell seats should not
            # advertise that the endpoint exists.
            from django.http import Http404

            raise Http404("Public registration is not available here.")
        self._require_public_host(request)

    @staticmethod
    def _require_public_host(request):
        if not settings.TENANCY_ENABLED:
            return
        if not resolver.platform_hosts():
            return
        host = resolver.normalise_host(request.get_host())
        if resolver.is_platform_host(host):
            return
        raise PermissionDenied(
            "Registration is served on the platform's own address, not "
            "inside a customer's workspace.")


class SlugAvailabilityView(PublicRegistrationMixin, APIView):
    """GET /api/v1/register/slug/?slug=abcschool -- Part 2's live check.

    IT NECESSARILY REVEALS THAT A SUBDOMAIN IS TAKEN, and that is a real
    disclosure rather than one to wave away: Phase S6 went to some trouble to
    stop the pre-login branding endpoint becoming a customer directory, and
    this endpoint hands back one bit of the same information.

    It is accepted because a signup form cannot work without it -- being told
    on submit that the name you have typed is gone is the worst version of
    this -- and it is narrowed as far as it can be:

      * one bit out, never a name: "taken" does not say by whom, and a
        subdomain held by a registration in progress is reported identically
        to one held by a live customer;
      * a tight throttle (30/min) which is generous for a person typing and
        useless for walking a dictionary;
      * nothing is created, so the endpoint cannot be used to hold names.

    The remaining exposure is in the Security Findings, where it belongs,
    rather than being quietly left out of the report.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "registration_slug"

    def get(self, request):
        return Response(registration.slug_status(
            request.query_params.get("slug", "")))


class RegistrationView(PublicRegistrationMixin, APIView):
    """POST /api/v1/register/ -- Part 1. Creates NO tenant.

    Answers 202, not 201: nothing has been created yet, which is exactly
    what Part 3 requires and what the response body says.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "registration"

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The honeypot. A field no human sees and no real form fills in; a
        # bot that posts every input it finds fills it. Answered with the
        # ordinary success body on purpose -- telling a script it was detected
        # is telling it what to change.
        if (data.get("website") or "").strip():
            logger.info("registration honeypot tripped from %s",
                        request.META.get("REMOTE_ADDR"))
            return Response(self._accepted_body(data["slug"]),
                            status=http_status.HTTP_202_ACCEPTED)

        try:
            record, token = registration.register(
                organization_name=data["organization_name"],
                slug=data["slug"],
                organization_email=data["organization_email"],
                admin_email=data["admin_email"],
                admin_name=data.get("admin_name", ""),
                password=data["password"],
                industry=data.get("industry", ""),
                country=data.get("country", ""),
                request=request)
        except registration.SlugUnavailable as exc:
            # The ONE refusal that is reported plainly. A subdomain clash is
            # about a name the registrant typed, not about a person, and they
            # cannot proceed without being told.
            raise ValidationError({"slug": str(exc)}) from exc
        except TenancyError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        registration.send_verification_email(record, token, request=request)
        return Response(self._accepted_body(record.slug),
                        status=http_status.HTTP_202_ACCEPTED)

    @staticmethod
    def _accepted_body(slug):
        return {
            "status": "verification_sent",
            "slug": slug,
            "detail": ("Check your email. Nothing has been created yet -- "
                       "your workspace is built when you confirm the "
                       "address."),
            "expires_in_hours": int(getattr(
                settings, "TENANCY_VERIFICATION_TTL_HOURS", 24)),
        }


class VerificationView(PublicRegistrationMixin, APIView):
    """POST /api/v1/register/verify/ -- Parts 3, 4 and 5 in one request.

    On success the workspace exists, is bootstrapped, is on a 14-day trial
    and has an administrator who can sign in with the password they chose at
    registration. No platform operator was involved at any point.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "registration_verify"

    def post(self, request):
        serializer = VerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record, organization, created = registration.verify(
                serializer.validated_data["token"], request=request)
        except registration.VerificationFailed as exc:
            raise ValidationError({"token": str(exc)}) from exc
        except TenancyError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        from . import bootstrap

        # Part 8: the health check runs immediately, and its verdict is in
        # the response -- so the first thing the new administrator sees is
        # either "ready" or exactly what is missing, rather than a welcome
        # screen over a half-built workspace.
        gaps = bootstrap.verify_organization(organization)
        subscription = getattr(organization, "subscription", None)
        return Response({
            "status": "provisioned" if created else "already_provisioned",
            "organization": {
                "name": organization.name,
                "slug": organization.slug,
                "status": organization.status,
            },
            "workspace_url": f"https://{registration._workspace_host(organization.slug)}/",
            "admin_email": record.admin_email,
            "trial": {
                "days": int(getattr(settings,
                                    "TENANCY_SELF_SERVICE_TRIAL_DAYS", 14)),
                "ends_on": getattr(subscription, "trial_end", None),
                "status": organization.subscription_status,
            },
            "health": {
                "verdict": ("Tenant Ready" if not gaps
                            else "Missing Configuration"),
                "configuration_gaps": gaps,
            },
        })


class OnboardingView(APIView):
    """GET /api/v1/tenant/onboarding/ -- Parts 6, 7 and 8, for the tenant.

    TENANT-AUTHENTICATED, NOT PLATFORM. This is the only S7 endpoint a
    customer's own administrator calls, and it reads only their own
    organization: the checklist is computed inside their tenant context, so
    an administrator of one workspace cannot see another's progress even by
    id, because there is no id to pass.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from . import onboarding

        return Response(onboarding.state(request.user))

    def post(self, request):
        """Mark the wizard as seen, or a step as done by hand.

        Two steps cannot be measured from data -- "invite your team" has no
        row that proves it happened, and a customer who has decided the
        seeded departments are fine has nothing to create. So those can be
        ticked, and the tick is stored per organization.
        """
        from . import onboarding

        try:
            return Response(onboarding.update(
                request.user,
                dismissed=request.data.get("dismissed"),
                step_done=request.data.get("step_done")))
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
