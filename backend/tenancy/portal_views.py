"""Phase S8: the endpoints a customer's own administrator calls.

NOT `PlatformConsoleMixin`, and not the public registration mixin either.
This is the third audience in this package and it gets its own, narrower
gate: authenticated, belongs to an organization, and administers it. See
``tenancy.portal.require_tenant_admin`` for why all three conditions are
checked rather than just the role.

EVERY ENDPOINT HERE IS SCOPED BY THE CALLER, never by a parameter. There is
no ``?organization=`` on any of them, because the only workspace an
administrator may ask about is the one they are signed in to -- which makes
cross-tenant access not "refused" but unexpressible.
"""
import logging

from rest_framework import status as http_status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import portal
from .exceptions import TenancyError
from .portal_serializers import PaymentProofSerializer, PlanRequestSerializer

logger = logging.getLogger(__name__)


class TenantPortalMixin:
    """Tenant administrator only, with its refusals mapped to HTTP."""

    permission_classes = [IsAuthenticated]

    def run(self, operation, *args, **kwargs):
        """Call a portal service, turning its refusals into the right status.

        ``NotTenantAdmin`` is a 403 and every other ``TenancyError`` is a
        400: "you may not" and "that is not allowed" are different answers,
        and collapsing them would tell an employee that their input was
        wrong when the truth is that this page is not theirs.
        """
        try:
            return operation(*args, **kwargs)
        except portal.NotTenantAdmin as exc:
            raise PermissionDenied(str(exc)) from exc
        except TenancyError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

    def validated(self, serializer_class, request):
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class SubscriptionView(TenantPortalMixin, APIView):
    """GET /api/v1/tenant/subscription/ -- Part 1, and Part 7's explanation."""

    def get(self, request):
        return Response(self.run(portal.subscription_state, request.user))


class TenantPlansView(TenantPortalMixin, APIView):
    """GET /api/v1/tenant/plans/ -- Part 2, priced from the database.

    The four figures the brief lists are not in this codebase's front end
    anywhere. `PlanPrice` rows are immutable and superseded by insertion, so
    a price typed into a page is a price that goes stale on the first change
    -- shown to the one audience that must never see a stale one.
    """

    def get(self, request):
        return Response(self.run(portal.available_plans, request.user))


class PaymentInstructionsView(TenantPortalMixin, APIView):
    """GET /api/v1/tenant/payment-instructions/ -- Part 3, console-managed."""

    def get(self, request):
        return Response(self.run(portal.payment_instructions, request.user))


class PlanRequestView(TenantPortalMixin, APIView):
    """POST /api/v1/tenant/subscription/request/ -- Part 2.

    ACTIVATES NOTHING. It opens a payment with a reference and an amount the
    server resolved, and answers 201 with what the customer now owes. The
    subscription does not move until a human verifies the money arrived.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "subscription_request"

    def post(self, request):
        data = self.validated(PlanRequestSerializer, request)
        payment = self.run(portal.request_plan, request.user,
                           data["plan_code"], request=request)
        return Response(_payment_body(payment),
                        status=http_status.HTTP_201_CREATED)


class PaymentProofView(TenantPortalMixin, APIView):
    """POST /api/v1/tenant/payments/<reference>/proof/ -- Part 4.

    Multipart, because it carries a screenshot. The file goes to the
    platform media tree (outside `org/`), so it is readable only through a
    platform-authenticated view -- a receipt naming a bank account is not
    something to leave behind a guessable URL.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "subscription_request"

    def post(self, request, reference):
        data = self.validated(PaymentProofSerializer, request)
        payment = self.run(
            portal.submit_payment_proof, request.user, reference,
            method=data["method"],
            transaction_id=data.get("transaction_id", ""),
            paid_at=data.get("paid_at"),
            proof=request.FILES.get("proof"),
            payer_note=data.get("payer_note", ""),
            request=request)
        return Response(_payment_body(payment))


class PaymentHistoryView(TenantPortalMixin, APIView):
    """GET /api/v1/tenant/payments/ -- Part 7's trail, theirs only."""

    def get(self, request):
        return Response(self.run(portal.payment_history, request.user))


# ---------------------------------------------------------------------------
# Phase S9: branding and custom domains, from the tenant side
# ---------------------------------------------------------------------------
class BrandingView(APIView):
    """GET for any member, PATCH for an administrator.

    THE READ IS DELIBERATELY NOT ADMIN-ONLY. R28 is "tenant branding visible
    everywhere, not login page only", and that means every employee's browser
    needs the colours and the logo in order to theme the application they are
    using. Only an administrator chooses them.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(portal.branding_for_member(request.user))

    def patch(self, request):
        from .portal_serializers import BrandingSerializer

        serializer = BrandingSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(portal.update_branding(
                request.user, request=request, **serializer.validated_data))
        except portal.NotTenantAdmin as exc:
            raise PermissionDenied(str(exc)) from exc
        except TenancyError as exc:
            raise ValidationError({"detail": str(exc)}) from exc


class BrandingAssetView(TenantPortalMixin, APIView):
    """POST one branding image. Multipart. Administrator only."""

    def post(self, request):
        field = (request.data.get("field") or "").strip()
        uploaded = request.FILES.get("image")
        if uploaded is None:
            raise ValidationError({"image": "Choose an image to upload."})
        return Response(self.run(portal.set_branding_asset, request.user,
                                 field, uploaded, request=request))


class TenantDomainsView(TenantPortalMixin, APIView):
    """GET their domains and what to publish; POST claims a new one."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "subscription_request"

    def get(self, request):
        from . import domains

        organization = self.run(portal.require_tenant_admin, request.user)
        return Response([domains.instructions(domain)
                         for domain in domains.for_organization(organization)])

    def post(self, request):
        from . import domains
        from .portal_serializers import DomainClaimSerializer

        organization = self.run(portal.require_tenant_admin, request.user)
        data = self.validated(DomainClaimSerializer, request)
        domain = self.run(domains.claim, organization, data["hostname"],
                          method=data.get("method"), actor=request.user,
                          request=request)
        return Response(domains.instructions(domain),
                        status=http_status.HTTP_201_CREATED)


class TenantDomainVerifyView(TenantPortalMixin, APIView):
    """POST asks DNS whether the proof is published.

    Throttled on its own, tighter scope: each call is a DNS query the
    platform makes on the customer's behalf, and a customer waiting for
    propagation will press it repeatedly.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "domain_verify"

    def post(self, request, hostname):
        from . import domains

        organization = self.run(portal.require_tenant_admin, request.user)
        domain = self._own(organization, hostname)
        try:
            domain = domains.verify(domain, actor=request.user,
                                    request=request)
        except domains.VerificationUnavailable as exc:
            # 503, not 400: nothing is wrong with the customer's request or
            # their DNS -- this deployment cannot look.
            from rest_framework.exceptions import APIException

            class _Unavailable(APIException):
                status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE

            raise _Unavailable(str(exc)) from exc
        return Response(domains.instructions(domain))

    def delete(self, request, hostname):
        from . import domains

        organization = self.run(portal.require_tenant_admin, request.user)
        domain = self._own(organization, hostname)
        domains.remove(domain, actor=request.user, request=request)
        return Response(status=http_status.HTTP_204_NO_CONTENT)

    def _own(self, organization, hostname):
        """Their domain, or a refusal that reveals nothing about others."""
        from . import domains
        from .models import TenantDomain

        domain = (TenantDomain.objects
                  .filter(organization=organization,
                          hostname=domains.normalise(hostname))
                  .first())
        if domain is None:
            raise ValidationError(
                {"detail": "No such domain for this organization."})
        return domain


def _payment_body(payment):
    """What the customer needs in order to pay: the reference and the amount."""
    return {
        "reference": payment.payment_reference,
        "plan": getattr(payment.plan, "code", None),
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "status": payment.status,
        "status_display": payment.get_status_display(),
        "created_at": payment.created_at,
        "submitted_at": payment.submitted_at,
        "detail": ("Nothing is active yet. Send the amount using one of the "
                   "methods shown, quoting this reference, then upload your "
                   "receipt."
                   if payment.status == payment.Status.AWAITING_PROOF else
                   "We have your receipt and will review it shortly."),
    }
