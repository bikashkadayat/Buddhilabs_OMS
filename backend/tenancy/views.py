"""The Platform Admin Console, over HTTP. Phase S6 Parts 1, 2, 5, 7, 9.

Every view here is a thin wrapper. The authority check, the cross-tenant
query, the lifecycle move and the audit entry all live in
``tenancy.console``, and that split is deliberate rather than tidy: a
management command, a shell session and a future scheduled job must pass
through the same checks as a request does. A permission class in a DRF view
protects exactly one caller.

SO THERE ARE TWO GUARDS, NOT ONE
--------------------------------
``IsPlatformStaff`` refuses the request, and ``console.require_platform``
refuses the operation. The second is the one that matters; the first exists so
the refusal is a 403 with a readable message instead of an exception.

HOST SEPARATION (Part 1)
------------------------
The console is meant to live on its own hostname -- ``admin.platform.com`` --
away from every tenant workspace. ``PlatformConsoleMixin`` enforces that once
``TENANCY_ENABLED`` is on: a console request arriving on a TENANT's host is
refused, because a platform session established on a customer's subdomain is
one XSS away from being a platform session the customer can drive. While
tenancy is off there is no host separation to enforce and the check stands
down.

NOT HERE, ON PURPOSE: registration, signup, a subscription purchase flow, a
payment gateway. A tenant is still created by a platform operator, which is
what Part 3 asks for and all that it asks for.
"""
import logging

from django.conf import settings
from django.db.models import Count
from django.http import FileResponse, Http404
from rest_framework import status as http_status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import (FormParser, JSONParser,
                                    MultiPartParser)
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import (archive, console, counters, platform_audit, plans,
               resolver, services)
from .context import no_tenant
from .exceptions import TenancyError
from .lifecycle import IllegalOrganizationTransition
from .models import Organization, Plan
from .permissions import IsPlatformStaff
from .serializers import (ArchiveSerializer, BrandingAssetSerializer,
                          BrandingSerializer, ExportRequestSerializer,
                          ExtendSerializer, NoteSerializer,
                          OrganizationDetailSerializer,
                          OrganizationListSerializer,
                          OrganizationStatusSerializer,
                          OrganizationUpdateSerializer,
                          PaymentQueueSerializer, PlanChangeSerializer,
                          PlanReadSerializer, PlatformAuditSerializer,
                          ProvisionSerializer, ReasonSerializer,
                          RestoreSerializer, SettingsSerializer,
                          SubscriptionEventSerializer,
                          SubscriptionReadSerializer, TenantExportSerializer,
                          TrialSerializer)

from config.uploads import harden_file_response

logger = logging.getLogger(__name__)


class PlatformConsoleMixin:
    """Platform-staff only, and (once tenancy is on) platform-host only."""

    permission_classes = [IsPlatformStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._require_platform_host(request)

    @staticmethod
    def _require_platform_host(request):
        """Refuse a console request that arrived on a TENANT's hostname.

        NO CONSOLE HOST CONFIGURED MEANS THE CONSOLE SHARES THE HOST, and this
        stands down -- exactly as ``users.tenant_login.platform_login_allowed``
        already does for sign-in. The two have to agree: refusing here while
        permitting there would let an operator log in and then meet 403 on
        every console page, which is the single-host deployment NIF runs
        today. ``tenancy.checks`` warns about that posture rather than this
        silently making it unreachable.
        """
        if not settings.TENANCY_ENABLED:
            return
        if not resolver.platform_hosts():
            return
        host = resolver.normalise_host(request.get_host())
        if resolver.is_platform_host(host):
            return
        raise PermissionDenied(
            "The platform console is served only on a platform host. "
            "Reaching it through a tenant workspace is refused.")

    # -- helpers ---------------------------------------------------------
    def organization(self, slug):
        from .context import no_tenant

        with no_tenant():
            try:
                return (Organization.objects
                        .select_related("subscription", "subscription__plan",
                                        "subscription__plan_price",
                                        "settings", "branding")
                        .get(slug=slug))
            except Organization.DoesNotExist:
                raise Http404(f"No workspace with the address '{slug}'.")

    def validated(self, serializer_class, request):
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def plan(self, code):
        from .context import no_tenant

        with no_tenant():
            try:
                return Plan.objects.get(code=code)
            except Plan.DoesNotExist:
                raise ValidationError({"plan_code": f"No plan '{code}'."})

    def run(self, operation, *args, **kwargs):
        """Call a console service, turning its refusals into 400s.

        ``TenancyError`` and ``IllegalOrganizationTransition`` are both
        "you asked for something the rules do not allow", which is a 400 and
        not a 500. Catching them here rather than in each view keeps the
        mapping in one place, and keeps an unexpected exception a 500 -- a
        console that answers 400 to a genuine bug is a console that hides it.
        """
        try:
            return operation(*args, **kwargs)
        except (TenancyError, IllegalOrganizationTransition) as exc:
            raise ValidationError({"detail": str(exc)})


# ---------------------------------------------------------------------------
# Part 1: the dashboard
# ---------------------------------------------------------------------------
class PlatformDashboardView(PlatformConsoleMixin, APIView):
    """GET /api/v1/platform/dashboard/ -- every widget Part 1 names."""

    def get(self, request):
        return Response(console.dashboard(request.user))


class PlatformHealthView(PlatformConsoleMixin, APIView):
    """GET /api/v1/platform/health/ -- the operations panel on its own.

    Separate from the dashboard because it is the one part worth polling, and
    polling the whole dashboard to refresh it would re-run the revenue
    forecast every few seconds.
    """

    def get(self, request):
        return Response(console.system_health(request.user))


class PlanListView(PlatformConsoleMixin, APIView):
    """GET /api/v1/platform/plans/ -- the plan picker, with live prices."""

    def get(self, request):
        from .context import no_tenant

        with no_tenant():
            queryset = (Plan.objects
                        .annotate(subscriptions_count=Count("subscriptions"))
                        .order_by("sort_order", "interval_months"))
            if request.query_params.get("purchasable") == "true":
                queryset = plans.purchasable_plans()
            return Response(PlanReadSerializer(queryset, many=True).data)


# ---------------------------------------------------------------------------
# Part 2: organization management
# ---------------------------------------------------------------------------
class OrganizationListCreateView(PlatformConsoleMixin, APIView):
    """GET lists tenants. POST provisions one -- the "one-click" endpoint.

    MULTIPART AS WELL AS JSON. The creation form now carries a logo and a
    favicon, so the request has files in it; the JSON parser is kept because
    every existing caller -- the management command, the tests, any script --
    sends JSON and must keep working unchanged.
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        queryset = console.list_organizations(
            request.user,
            status=request.query_params.get("subscription_status") or None,
            organization_status=request.query_params.get("status") or None,
            search=request.query_params.get("search") or None)
        return Response(OrganizationListSerializer(queryset, many=True).data)

    def post(self, request):
        data = self.validated(ProvisionSerializer, request)
        plan_code = data.pop("plan_code", "")
        admin_email = data.pop("admin_email", "")
        admin_name = data.pop("admin_name", "")
        organization = self.run(
            console.create_organization, request.user, request=request,
            plan=self.plan(plan_code) if plan_code else None,
            admin_email=admin_email or None, admin_name=admin_name, **data)

        body = OrganizationDetailSerializer(organization).data
        # The provisioning receipt: what the bootstrap created, and whether
        # anything is still missing. An operator who cannot see this has to
        # trust that provisioning worked, which is the thing Phase S5 showed
        # could silently not be true.
        report = getattr(organization, "provisioning_report", {})
        body["provisioning"] = report
        if admin_email:
            # Shown ONCE, never stored, never logged. This is the only moment
            # the temporary password can be handed to the customer.
            body["admin_initial_password"] = _initial_password(organization,
                                                               admin_email)
        # The handover package: where to sign in, as whom, on what plan. The
        # same facts the organization page shows every day after this, so
        # what the operator copies now and what they look up next week agree.
        from . import handover

        body["access"] = handover.package(organization)
        return Response(body, status=http_status.HTTP_201_CREATED)


def _initial_password(organization, email):
    """Recover the generated password from the just-created user instance.

    ``create_tenant_admin`` returns it as an attribute on the User it built,
    and ``provision_organization`` records the email it used. The password is
    never persisted, so if it is not on the instance in this process it is
    gone -- the operator resets it instead, which is the correct outcome.
    """
    report = getattr(organization, "provisioning_report", {}) or {}
    if report.get("admin_user") != email:
        return None
    return getattr(organization, "_admin_initial_password", None)


class OrganizationDetailView(PlatformConsoleMixin, APIView):
    """GET one tenant in full. PATCH edits the fields an operator may edit."""

    def get(self, request, slug):
        organization = self.organization(slug)
        detail = console.organization_detail(request.user, slug)
        return Response({
            **OrganizationDetailSerializer(organization).data,
            "subscription": (
                SubscriptionReadSerializer(detail["subscription"]).data
                if detail["subscription"] else None),
            "events": SubscriptionEventSerializer(detail["events"],
                                                  many=True).data,
            "payments": PaymentQueueSerializer(detail["payments"],
                                               many=True).data,
            "audit": PlatformAuditSerializer(detail["audit"], many=True).data,
            "mirror_drift": detail["mirror_drift"],
            "health": console.tenant_health(request.user, organization),
            "usage": console.organization_usage(request.user, organization),
        })

    def patch(self, request, slug):
        organization = self.organization(slug)
        data = self.validated(OrganizationUpdateSerializer, request)
        organization = self.run(console.update_organization, request.user,
                                organization, request=request, **data)
        return Response(OrganizationDetailSerializer(organization).data)


class OrganizationUsageView(PlatformConsoleMixin, APIView):
    def get(self, request, slug):
        return Response(console.organization_usage(
            request.user, self.organization(slug)))


class OrganizationHealthView(PlatformConsoleMixin, APIView):
    """GET the tenant-health check -- "is this workspace actually usable?"."""

    def get(self, request, slug):
        return Response(console.tenant_health(
            request.user, self.organization(slug)))


class OrganizationRepairView(PlatformConsoleMixin, APIView):
    """POST re-runs the bootstrap. Fills gaps; overwrites nothing."""

    def post(self, request, slug):
        return Response(self.run(
            console.repair_configuration, request.user,
            self.organization(slug), request=request))


# ---------------------------------------------------------------------------
# Part 6: lifecycle actions
# ---------------------------------------------------------------------------
class _SubscriptionActionView(PlatformConsoleMixin, APIView):
    """Shared shape: validate, act, return the fresh subscription."""

    serializer_class = NoteSerializer

    def act(self, request, organization, data):   # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, slug):
        organization = self.organization(slug)
        data = self.validated(self.serializer_class, request)
        subscription = self.run(self.act, request, organization, data)
        organization.refresh_from_db()
        return Response({
            "subscription": SubscriptionReadSerializer(subscription).data,
            "organization": OrganizationDetailSerializer(organization).data,
        })


class OrganizationSuspendView(_SubscriptionActionView):
    serializer_class = ReasonSerializer

    def act(self, request, organization, data):
        return console.suspend(request.user, organization,
                               reason=data["reason"], request=request)


class OrganizationActivateView(_SubscriptionActionView):
    def act(self, request, organization, data):
        return console.activate(request.user, organization,
                                note=data.get("note", ""), request=request)


class OrganizationCancelView(_SubscriptionActionView):
    serializer_class = ReasonSerializer

    def act(self, request, organization, data):
        return console.cancel(request.user, organization,
                              reason=data["reason"], request=request)


class OrganizationStatusView(PlatformConsoleMixin, APIView):
    """POST the operator's direct lifecycle override (Part 6)."""

    def post(self, request, slug):
        organization = self.organization(slug)
        data = self.validated(OrganizationStatusSerializer, request)
        organization = self.run(
            console.set_organization_status, request.user, organization,
            data["status"], reason=data.get("note", ""), request=request)
        return Response(OrganizationDetailSerializer(organization).data)


# ---------------------------------------------------------------------------
# Part 5: subscription management
# ---------------------------------------------------------------------------
class OrganizationAssignPlanView(_SubscriptionActionView):
    """Put a tenant on a plan AND sell it a term -- the activation path."""

    serializer_class = PlanChangeSerializer

    def act(self, request, organization, data):
        return console.assign_plan(
            request.user, organization, self.plan(data["plan_code"]),
            months=data.get("months"), note=data.get("note", ""),
            request=request)


class OrganizationChangePlanView(_SubscriptionActionView):
    """Move plan WITHOUT re-dating the period -- the correction path."""

    serializer_class = PlanChangeSerializer

    def act(self, request, organization, data):
        return console.change_plan(
            request.user, organization, self.plan(data["plan_code"]),
            note=data.get("note", ""), request=request)


class OrganizationTrialView(_SubscriptionActionView):
    serializer_class = TrialSerializer

    def act(self, request, organization, data):
        code = data.get("plan_code") or ""
        return console.start_trial(
            request.user, organization,
            plan=self.plan(code) if code else None, days=data.get("days"),
            note=data.get("note", ""), request=request)


class OrganizationExtendView(_SubscriptionActionView):
    serializer_class = ExtendSerializer

    def act(self, request, organization, data):
        return console.extend(request.user, organization,
                              months=data["months"],
                              note=data.get("note", ""), request=request)


# ---------------------------------------------------------------------------
# Part 7: branding and settings
# ---------------------------------------------------------------------------
class OrganizationBrandingView(PlatformConsoleMixin, APIView):
    """GET / PATCH a tenant's branding.

    VERIFY: A TENANT SEES ONLY ITS OWN. Nothing here can read or write
    another tenant's row -- the organization is resolved from the URL slug and
    the branding is reached through its own one-to-one. The tenant-facing side
    of the same guarantee is the pre-login endpoint, which serves only
    ``OrganizationBranding.public_payload()`` for the host it was asked on.
    """

    def get(self, request, slug):
        row = console.branding(request.user, self.organization(slug))
        return Response(BrandingSerializer(row).data)

    def patch(self, request, slug):
        organization = self.organization(slug)
        serializer = BrandingSerializer(organization.branding,
                                        data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        row = self.run(console.set_branding, request.user, organization,
                       request=request, **serializer.validated_data)
        return Response(BrandingSerializer(row).data)


class OrganizationBrandingAssetView(PlatformConsoleMixin, APIView):
    """POST one branding image (multipart)."""

    def post(self, request, slug):
        organization = self.organization(slug)
        serializer = BrandingAssetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = self.run(console.set_branding_asset, request.user, organization,
                       serializer.validated_data["field"],
                       serializer.validated_data["file"], request=request)
        return Response(BrandingSerializer(row).data)


class OrganizationSettingsView(PlatformConsoleMixin, APIView):
    def get(self, request, slug):
        row = console.settings_for(request.user, self.organization(slug))
        return Response(SettingsSerializer(row).data)

    def patch(self, request, slug):
        organization = self.organization(slug)
        serializer = SettingsSerializer(organization.settings,
                                        data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        row = self.run(console.set_settings, request.user, organization,
                       request=request, **serializer.validated_data)
        return Response(SettingsSerializer(row).data)


# ---------------------------------------------------------------------------
# Part 9: the platform audit trail
# ---------------------------------------------------------------------------
class PlatformAuditView(PlatformConsoleMixin, APIView):
    """GET /api/v1/platform/audit/ -- read-only, by construction.

    ``PlatformAuditLog`` refuses an update and refuses a delete at the model,
    so there is no write endpoint to leave off: there is nothing a write
    endpoint could do.
    """

    def get(self, request):
        slug = request.query_params.get("organization") or None
        organization = self.organization(slug) if slug else None
        try:
            limit = min(int(request.query_params.get("limit", 200)), 1000)
        except (TypeError, ValueError):
            limit = 200
        entries = console.audit_trail(
            request.user, organization=organization,
            action=request.query_params.get("action") or None, limit=limit)
        return Response(PlatformAuditSerializer(entries, many=True).data)


class PaymentQueueView(PlatformConsoleMixin, APIView):
    """GET everything awaiting a verification decision."""

    def get(self, request):
        # `?view=decided`: the recent decisions, so an operator can see what
        # was approved or rejected (and by whom) without the audit trail.
        if request.query_params.get("view") == "decided":
            rows = console.recent_payment_decisions(request.user)
        else:
            rows = console.verification_queue(request.user)
        return Response(PaymentQueueSerializer(rows, many=True).data)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
class CounterRefreshView(PlatformConsoleMixin, APIView):
    """POST recompute seat counters. Seats only -- storage is a cron job.

    Exposed because the incremental maintenance can drift (``bulk_create``
    emits no signal) and an operator looking at a headcount they believe is
    wrong needs a way to settle it without shell access. Storage is excluded
    deliberately: it stats every file a tenant owns, which is not a thing to
    put behind a button somebody might hold down.
    """

    def post(self, request):
        console.require_platform(request.user)
        return Response(counters.refresh_all(storage=False))


# ---------------------------------------------------------------------------
# Phase S6.5: export, archive, restore
# ---------------------------------------------------------------------------
class OrganizationExportView(PlatformConsoleMixin, APIView):
    """GET lists a tenant's exports. POST produces one.

    POST IS SYNCHRONOUS and answers 201 with a finished receipt -- see
    ``tenancy.export`` for why a thread would be the wrong shape here. For a
    very large tenant that is a long request, which is a real limit rather
    than a hidden one: the receipt says exactly what was written.
    """

    def get(self, request, slug):
        organization = self.organization(slug)
        rows = console.list_exports(request.user, organization)
        return Response(TenantExportSerializer(rows, many=True).data)

    def post(self, request, slug):
        organization = self.organization(slug)
        data = self.validated(ExportRequestSerializer, request)
        export_row = self.run(console.create_export, request.user,
                              organization, contents=data.get("contents"),
                              request=request)
        body = TenantExportSerializer(export_row).data
        # A FAILED export is a 201 with a receipt that says so, not a 500:
        # the request was handled, and the outcome is the answer. The
        # integrity refusal is a result, not a server fault.
        return Response(body, status=http_status.HTTP_201_CREATED)


class OrganizationExportManifestView(PlatformConsoleMixin, APIView):
    """GET the full manifest for one export: every table, file and checksum."""

    def get(self, request, slug, export_id):
        organization = self.organization(slug)
        export_row = self.run(console.get_export, request.user, organization,
                              export_id)
        return Response(export_row.manifest or {})


class OrganizationExportDownloadView(PlatformConsoleMixin, APIView):
    """GET streams the bundle. Platform-authenticated, and audited every time.

    NOT A SIGNED MEDIA LINK, deliberately. The media view is ``AllowAny`` and
    treats the signature as the credential, which is right for an avatar in an
    <img> tag and wrong for a file containing a customer's entire workspace --
    a link in a chat log would be enough. ``documents.protected_media`` now
    refuses the ``platform/`` prefix outright at both ends, so this view is
    the only way to that file, and it requires a platform session.
    """

    def get(self, request, slug, export_id):
        organization = self.organization(slug)
        export_row = self.run(console.get_export, request.user, organization,
                              export_id)
        if not export_row.is_downloadable:
            raise ValidationError({"detail": (
                f"This export is '{export_row.status}' and has no file to "
                f"download." + (f" {export_row.error}" if export_row.error
                                else ""))})

        console.record_export_download(request.user, export_row,
                                       request=request)
        response = FileResponse(
            export_row.file.open("rb"), as_attachment=True,
            filename=export_row.file.name.rsplit("/", 1)[-1])
        # The checksum travels with the file, so a customer can verify the
        # copy they received against the receipt without a second request.
        response["X-Export-SHA256"] = export_row.sha256
        return harden_file_response(response)


class OrganizationArchiveView(PlatformConsoleMixin, APIView):
    """POST closes a workspace and preserves it. Nothing is deleted."""

    def post(self, request, slug):
        organization = self.organization(slug)
        data = self.validated(ArchiveSerializer, request)
        organization = self.run(console.archive_organization, request.user,
                                organization, reason=data["reason"],
                                request=request)
        return Response({
            "organization": OrganizationDetailSerializer(organization).data,
            "archive": archive.archive_state(organization),
        })


class OrganizationRestoreView(PlatformConsoleMixin, APIView):
    """POST reopens an archived workspace in the state it was closed in."""

    def post(self, request, slug):
        organization = self.organization(slug)
        data = self.validated(RestoreSerializer, request)
        organization = self.run(
            console.restore_organization, request.user, organization,
            note=data.get("note", ""), to_status=data.get("to_status") or None,
            request=request)
        return Response({
            "organization": OrganizationDetailSerializer(organization).data,
            "archive": archive.archive_state(organization),
        })


class LaunchReadinessView(PlatformConsoleMixin, APIView):
    """GET /api/v1/platform/launch-readiness/ -- Parts 1 and 5.

    The same audit the deploy gate enforces and `manage.py
    launch_readiness` prints, so an operator reading the console and an
    engineer reading a deployment log are looking at one answer.
    """

    def get(self, request):
        return Response(console.launch_readiness(request.user))


class PlatformEventsView(PlatformConsoleMixin, APIView):
    """GET /api/v1/platform/events/?days=30 -- Part 4's dashboards."""

    def get(self, request):
        return Response(console.event_dashboard(
            request.user, days=request.query_params.get("days", 30)))


class RegistrationFunnelView(PlatformConsoleMixin, APIView):
    """GET /api/v1/platform/registration-funnel/?days=30 -- Part 11.

    Separate from the event dashboards because it answers a different
    question: those count what happened, this counts how far people got.
    """

    def get(self, request):
        return Response(console.registration_funnel(
            request.user, days=request.query_params.get("days", 30)))


class MirrorReconcileView(PlatformConsoleMixin, APIView):
    """POST corrects every organization whose mirror has drifted.

    IT USED TO ONLY REPORT, which made it the second place drift was detected
    and the second place nothing was done about it -- the platform health
    check being the first. The mirror is what every request reads to decide
    whether a tenant is admitted, so "detected and logged" is not a resting
    place for it.
    """

    def post(self, request):
        return Response(self.run(console.repair_mirrors, request.user,
                                 request=request))


# ---------------------------------------------------------------------------
# Tenant-facing (NOT platform): the pre-login branding lookup
# ---------------------------------------------------------------------------
class PublicBrandingView(APIView):
    """GET /api/v1/tenant/public/branding/ -- unauthenticated, by necessity.

    A login page has to know whose login page it is before anybody has logged
    in. So this is the one tenancy endpoint with no authentication, and it is
    therefore the one that needs the narrowest possible answer: exactly
    ``OrganizationBranding.public_payload()`` -- a name, a logo, two colours
    and a tagline.

    It must NOT reveal headcount, plan, subscription status, or whether a
    given workspace address exists at all beyond what a login page needs. An
    unknown host gets the platform default rather than a 404, so this cannot
    be used to enumerate customers.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        host = resolver.normalise_host(request.get_host())
        organization = None
        try:
            organization = resolver.resolve(host)
            if organization is None and not settings.TENANCY_ENABLED:
                organization = resolver.default_organization(
                    getattr(settings, "TENANCY_DEFAULT_SLUG", ""))
        except Exception:  # noqa: BLE001 - never 500 the login page
            logger.warning("branding lookup failed for host %r", host,
                           exc_info=True)

        # Phase S7: whether this deployment offers self-service signup, so
        # the sign-in page knows whether to show a "create a workspace" link.
        # A capability of the DEPLOYMENT, not a fact about any customer --
        # the one thing this endpoint may safely volunteer.
        from . import registration as registration_module

        open_for_signup = registration_module.is_open()
        # PHASE S11 PART 7. The signup page said "No payment details are
        # needed" and never said what the customer GETS -- the trial was
        # stated in the verification email and on the first-login page, i.e.
        # everywhere except the one screen where somebody decides whether to
        # sign up. Served from the setting rather than written into the page,
        # so changing the offer is a configuration change and the signup
        # page cannot go stale against it.
        trial_days = int(getattr(settings,
                                 "TENANCY_SELF_SERVICE_TRIAL_DAYS", 14))

        if organization is None:
            return Response({"name": "", "logo_login": None, "favicon": None,
                             "color_primary": "", "color_secondary": "",
                             "login_tagline": "", "known": False,
                             "registration_open": open_for_signup,
                             "trial_days": trial_days})

        branding = getattr(organization, "branding", None)
        payload = (branding.public_payload() if branding
                   else {"name": organization.name, "logo_login": None,
                         "favicon": (organization.favicon.name
                                     if organization.favicon else None),
                         "color_primary": "", "color_secondary": "",
                         "login_tagline": ""})
        # Both images are tenant-owned files under a protected prefix, so
        # neither is reachable without a signature -- including the favicon,
        # which the browser fetches with no session of any kind.
        for asset in ("logo_login", "favicon"):
            if payload.get(asset):
                from documents.protected_media import signed_media_url

                # `organization=` explicitly: this endpoint is unauthenticated
                # and has resolved the tenant from the host already, so the
                # link must not depend on whether something happened to leave
                # a tenant in context.
                payload[asset] = signed_media_url(payload[asset],
                                                  organization=organization)
        payload["known"] = True
        payload["registration_open"] = open_for_signup
        payload["trial_days"] = trial_days
        return Response(payload)


# ---------------------------------------------------------------------------
# Phase S9 Parts 3-5: custom domains, from the operator's side
# ---------------------------------------------------------------------------
class OrganizationDomainsView(PlatformConsoleMixin, APIView):
    """GET a tenant's claimed domains; POST claims one on their behalf.

    An operator claiming for a customer is a real support case -- somebody
    reads the hostname off a ticket -- but it issues the same token and
    resolves to nothing until DNS agrees, so an operator cannot grant a
    hostname either. That is the point of Part 4.
    """

    def get(self, request, slug):
        from . import domains

        organization = self.organization(slug)
        return Response([domains.instructions(domain)
                         for domain in domains.for_organization(organization)])

    def post(self, request, slug):
        from . import domains
        from .portal_serializers import DomainClaimSerializer

        organization = self.organization(slug)
        serializer = DomainClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        domain = self.run(domains.claim, organization,
                          serializer.validated_data["hostname"],
                          method=serializer.validated_data.get("method"),
                          actor=request.user, request=request)
        return Response(domains.instructions(domain),
                        status=http_status.HTTP_201_CREATED)


class OrganizationDomainView(PlatformConsoleMixin, APIView):
    """POST re-checks DNS; DELETE withdraws the claim.

    DELETE IS THE FRAUD REMEDY. If a customer claims a hostname they do not
    own, verification will not pass -- but if a hostname changes hands, or a
    claim was made in error, an operator needs to be able to take it back,
    and the act is audited against the tenant that held it.
    """

    def post(self, request, slug, hostname):
        from . import domains

        domain = self._own(self.organization(slug), hostname)
        try:
            domain = domains.verify(domain, actor=request.user,
                                    request=request)
        except domains.VerificationUnavailable as exc:
            return Response({"detail": str(exc), "hostname": domain.hostname,
                             "status": domain.status},
                            status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(domains.instructions(domain))

    def delete(self, request, slug, hostname):
        from . import domains

        domain = self._own(self.organization(slug), hostname)
        domains.remove(domain, actor=request.user, request=request)
        return Response(status=http_status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _own(organization, hostname):
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


class PlatformDomainsView(PlatformConsoleMixin, APIView):
    """GET every custom domain on the platform -- Part 3's operations view.

    An operator needs one place that answers "which hostnames are we
    serving, and which claims are stuck": a claim that has been checked
    fifteen times and still fails is a customer who needs help, and nobody
    finds that by visiting tenants one at a time.
    """

    def get(self, request):
        from . import domains
        from .models import TenantDomain

        rows = (TenantDomain.objects
                .select_related("organization")
                .order_by("-updated_at")[:500])
        return Response({
            "domains": [{
                "hostname": row.hostname,
                "organization": row.organization.name,
                "slug": row.organization.slug,
                "status": row.status,
                "status_display": row.get_status_display(),
                "method": row.method,
                "is_primary": row.is_primary,
                "check_count": row.check_count,
                "last_checked_at": row.last_checked_at,
                "verified_at": row.verified_at,
                "last_error": row.last_error,
            } for row in rows],
            # Shown because it changes what the page can promise: with no
            # resolver installed, "Check now" cannot answer at all, and the
            # operator should know that before telling a customer to wait.
            "dns_available": domains.dns_available(),
            "counts": {
                status: TenantDomain.objects.filter(status=status).count()
                for status, _ in TenantDomain.Status.choices
            },
        })


# ---------------------------------------------------------------------------
# Client handover
# ---------------------------------------------------------------------------
class OrganizationAccessView(PlatformConsoleMixin, APIView):
    """GET the customer's way in: login URL, administrator, plan, trial.

    Never the password -- see ``tenancy.handover``.
    """

    def get(self, request, slug):
        from . import handover

        return Response(handover.package(self.organization(slug)))


class OrganizationAccessSendView(PlatformConsoleMixin, APIView):
    """POST emails the administrator their sign-in details.

    With ``password``: the temporary password the creation dialog showed,
    checked against the account before it is sent. Without: a new temporary
    password is issued -- only while the administrator has not yet chosen
    their own.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "access_send"

    def post(self, request, slug):
        from rest_framework.exceptions import APIException

        from . import handover

        organization = self.organization(slug)
        password = (request.data.get("password") or "").strip() or None
        try:
            receipt = self.run(handover.send_access, organization,
                               actor=request.user, password=password,
                               request=request)
        except handover.AccessDeliveryFailed as exc:
            # 502, not 400: the request was right and the customer's details
            # are fine. The mail server is what failed.
            class _MailFailed(APIException):
                status_code = http_status.HTTP_502_BAD_GATEWAY

            raise _MailFailed(str(exc)) from exc
        return Response(receipt)


class PlatformRecentView(PlatformConsoleMixin, APIView):
    """The dashboard's "what just happened" lists. Platform tables only."""

    def get(self, request):
        return Response(console.recent(request.user))


# ---------------------------------------------------------------------------
# Payment Center: decide payments, manage payment methods
# ---------------------------------------------------------------------------
class PaymentDecisionView(PlatformConsoleMixin, APIView):
    """POST approve / reject / request-info on one payment.

    The decision is the console's; the rules (who may decide, from which
    state, and what activating a subscription means) are tenancy.payments'.
    """

    action = None

    def post(self, request, payment_id):
        if self.action == "approve":
            payment, subscription = self.run(
                console.approve_payment, request.user, payment_id,
                note=(request.data.get("note") or "").strip(), request=request)
            body = PaymentQueueSerializer(payment).data
            body["subscription"] = {
                "status": subscription.status,
                "current_period_end": subscription.current_period_end,
            }
            return Response(body)
        if self.action == "reject":
            payment = self.run(console.reject_payment, request.user, payment_id,
                               reason=request.data.get("reason") or "",
                               request=request)
        else:
            payment = self.run(console.request_payment_information,
                               request.user, payment_id,
                               message=request.data.get("message") or "",
                               request=request)
        return Response(PaymentQueueSerializer(payment).data)



class PlatformPaymentProofView(PlatformConsoleMixin, APIView):
    """GET the customer's receipt. Platform session only, never a link.

    The file is attacker-supplied content opened by the most privileged user
    on the platform, so it is served hardened (no sniffing, no scripts) and
    only to an authenticated operator -- the same rule as export bundles.
    """

    def get(self, request, payment_id):
        payment = self.run(console.payment_proof, request.user, payment_id)
        response = FileResponse(
            payment.proof.open("rb"), as_attachment=False,
            filename=payment.proof.name.rsplit("/", 1)[-1])
        return harden_file_response(response)


class PaymentMethodsView(PlatformConsoleMixin, APIView):
    """GET the payment methods customers are shown; POST adds one."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        return Response(console.list_payment_methods(
            request.user,
            include_archived=request.query_params.get("archived") == "1"))

    def post(self, request):
        row = self.run(console.save_payment_method, request.user, request.data,
                       qr_image=request.FILES.get("qr_image"), request=request)
        return Response(row, status=http_status.HTTP_201_CREATED)


class PaymentMethodView(PlatformConsoleMixin, APIView):
    """PATCH edits one payment method; POST {state} activates, disables or archives."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def patch(self, request, method_id):
        return Response(self.run(
            console.save_payment_method, request.user, request.data,
            method_id=method_id, qr_image=request.FILES.get("qr_image"),
            request=request))

    def post(self, request, method_id):
        return Response(self.run(
            console.set_payment_method_state, request.user, method_id,
            request.data.get("state"), request=request))

    def run(self, operation, *args, **kwargs):
        from .models import PaymentInstruction

        try:
            return super().run(operation, *args, **kwargs)
        except (PaymentInstruction.DoesNotExist, ValueError, TypeError) as exc:
            raise Http404("No such payment method.") from exc


# ---------------------------------------------------------------------------
# Customer success: health, adoption, support inbox
# ---------------------------------------------------------------------------
class CustomerHealthView(PlatformConsoleMixin, APIView):
    """GET every organization's health and the platform's adoption trend.

    From the usage counters only (tenancy.adoption): the console still reads
    no customer record to answer "is this customer actually using it?".
    """

    def get(self, request):
        from . import adoption

        try:
            days = max(7, min(int(request.query_params.get("days") or 30), 90))
        except ValueError:
            days = 30
        return Response(adoption.customer_health(days=days))


class SupportInboxView(PlatformConsoleMixin, APIView):
    """GET the support desk: filtered tickets plus the header numbers."""

    def get(self, request):
        from . import support

        p = request.query_params
        return Response(support.platform_list(
            request.user, kind=p.get("kind") or None, state=p.get("status") or None,
            filters={k: p.get(k) for k in ("view", "organization", "priority",
                                           "category", "assigned", "q") if p.get(k)}))


class SupportRequestView(PlatformConsoleMixin, APIView):
    """GET one ticket with the full thread (internal notes included); PATCH it."""

    def get(self, request, request_id):
        from . import support

        return Response(self.run(support.platform_detail, request.user, request_id))

    def patch(self, request, request_id):
        from . import support

        d = request.data
        if "known_issue" in d:
            from . import desk
            self.run(desk.set_ticket_issue, request.user, request_id, d.get("known_issue"))
        return Response(self.run(
            support.platform_update, request.user, request_id,
            state=d.get("status"), response=d.get("response"), request=request,
            priority=d.get("priority"), category=d.get("category"),
            assigned_to=d.get("assigned_to"), assign="assigned_to" in d,
            escalate=bool(d.get("escalate")), escalate_reason=d.get("escalate_reason") or "",
            roadmap_status=d.get("roadmap_status")))


class SupportMessageView(PlatformConsoleMixin, APIView):
    """POST a reply (emailed to the customer) or an internal note."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, request_id):
        from . import support

        upload = support._validate_file(request.FILES.get("attachment"), "attachment")
        internal = str(request.data.get("internal", "")).lower() in ("1", "true", "yes")
        return Response(self.run(support.platform_message, request.user, request_id,
                                 body=request.data.get("body", ""), internal=internal,
                                 attachment=upload), status=http_status.HTTP_201_CREATED)


class SupportFileConsoleView(PlatformConsoleMixin, APIView):
    def get(self, request, request_id, which, message_id=None):
        from . import support

        return support._serve(self.run(support.platform_file, request.user,
                                       request_id, which, message_id))


class SupportOverviewView(PlatformConsoleMixin, APIView):
    """GET the Support Overview card: open, assigned, critical, overdue, times."""

    def get(self, request):
        from . import support

        console.require_platform(request.user)
        with no_tenant():
            return Response(support.overview())


class SupportAgentsView(PlatformConsoleMixin, APIView):
    def get(self, request):
        from . import support

        return Response(self.run(support.agents, request.user))


class ProductUpdatesConsoleView(PlatformConsoleMixin, APIView):
    """GET every update (drafts too); POST a new one."""

    def get(self, request):
        from . import support
        from .models import ProductUpdate

        console.require_platform(request.user)
        with no_tenant():
            return Response([support.update_row(u) for u in ProductUpdate.objects.all()[:200]])

    def post(self, request):
        from . import support

        serializer = support.ProductUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(support.save_update(request.user, serializer.validated_data),
                        status=http_status.HTTP_201_CREATED)


class ProductUpdateConsoleView(PlatformConsoleMixin, APIView):
    def patch(self, request, update_id):
        from . import support

        serializer = support.ProductUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(support.save_update(request.user, serializer.validated_data, update_id))

    def delete(self, request, update_id):
        from .models import ProductUpdate

        console.require_platform(request.user)
        with no_tenant():
            ProductUpdate.objects.filter(pk=update_id).delete()
        return Response(status=http_status.HTTP_204_NO_CONTENT)


class StatusNoticesConsoleView(PlatformConsoleMixin, APIView):
    """GET recent notices; POST a new incident or maintenance notice."""

    def get(self, request):
        from . import support
        from .models import StatusNotice

        console.require_platform(request.user)
        with no_tenant():
            return Response([support.notice_row(n) for n in StatusNotice.objects.all()[:100]])

    def post(self, request):
        from . import support
        from .models import StatusNotice

        console.require_platform(request.user)
        serializer = support.StatusNoticeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with no_tenant():
            notice = StatusNotice.objects.create(created_by=request.user,
                                                 **serializer.validated_data)
        support._forget_status()
        return Response(support.notice_row(notice), status=http_status.HTTP_201_CREATED)


class StatusNoticeConsoleView(PlatformConsoleMixin, APIView):
    """POST resolves a notice."""

    def post(self, request, notice_id):
        from django.utils import timezone as tz

        from . import support
        from .models import StatusNotice

        console.require_platform(request.user)
        with no_tenant():
            notice = StatusNotice.objects.filter(pk=notice_id).first()
            if notice is None:
                raise Http404
            notice.resolved_at = notice.resolved_at or tz.now()
            notice.save(update_fields=["resolved_at"])
        support._forget_status()
        return Response(support.notice_row(notice))


# ---------------------------------------------------------------------------
# Customer Success 2.0 (console)
# ---------------------------------------------------------------------------
class _SuccessView(PlatformConsoleMixin, APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        console.require_platform(request.user)


class SuccessCommandCenterView(_SuccessView):
    """GET segments + every organization's health score with its components."""

    def get(self, request):
        from . import success
        return Response(success.command_center())


class SuccessAdoptionView(_SuccessView):
    def get(self, request):
        from . import success
        try:
            weeks = max(4, min(int(request.query_params.get("weeks") or 12), 26))
        except ValueError:
            weeks = 12
        return Response(success.adoption(weeks))


class SuccessOnboardingView(_SuccessView):
    def get(self, request):
        from . import success
        return Response(success.milestones())


class SuccessExecutiveView(_SuccessView):
    def get(self, request):
        from . import success
        return Response(success.executive(request.user))


class SuccessAlertsView(_SuccessView):
    """GET what needs the team now: ticket SLA alerts and customer signals."""

    def get(self, request):
        from . import success
        from . import desk
        return Response({"tickets": success.ticket_alerts(),
                         "customers": success.customer_alerts(),
                         "mentions": desk.mentions_for(request.user, unread_only=True)})


class OrganizationTimelineView(_SuccessView):
    def get(self, request, slug):
        from . import success
        return Response(success.timeline(self.organization(slug)))


class SuccessTasksView(_SuccessView):
    """GET tasks (?organization=&status=&mine=1); POST one."""

    def get(self, request):
        from . import success
        from .models import SuccessTask

        p = request.query_params
        with no_tenant():
            qs = SuccessTask.objects.select_related("organization", "assigned_to", "ticket", "campaign")
            if p.get("organization"):
                qs = qs.filter(organization__slug=p["organization"])
            if p.get("campaign"):
                qs = qs.filter(campaign_id=p["campaign"])
            if p.get("ticket"):
                qs = qs.filter(ticket_id=p["ticket"])
            if p.get("status"):
                qs = qs.filter(status=p["status"])
            if p.get("mine") == "1":
                qs = qs.filter(assigned_to=request.user)
            return Response([success.task_row(t) for t in qs[:300]])

    def post(self, request):
        from . import success
        from .models import SuccessTask

        d = request.data
        kind = d.get("kind") or SuccessTask.Kind.FOLLOW_UP
        if kind not in SuccessTask.Kind.values:
            raise ValidationError({"kind": "Unknown task type."})
        title = (d.get("title") or "").strip() or dict(SuccessTask.Kind.choices)[kind]
        organization = self.organization(d.get("organization") or "")
        with no_tenant():
            task = SuccessTask.objects.create(
                organization=organization, kind=kind, title=title[:200],
                notes=(d.get("notes") or "").strip(), due_date=d.get("due_date") or None,
                assigned_to=self._agent(d.get("assigned_to")), created_by=request.user)
            task = SuccessTask.objects.select_related("organization", "assigned_to").get(pk=task.pk)
            return Response(success.task_row(task), status=http_status.HTTP_201_CREATED)

    @staticmethod
    def _agent(agent_id):
        from django.contrib.auth import get_user_model

        if not agent_id:
            return None
        agent = get_user_model().all_tenants.filter(
            pk=agent_id, is_platform_staff=True, organization__isnull=True).first()
        if agent is None:
            raise ValidationError({"assigned_to": "Tasks are assigned to platform staff."})
        return agent


class SuccessTaskView(_SuccessView):
    def patch(self, request, task_id):
        from django.utils import timezone as tz

        from . import success
        from .models import SuccessTask

        d = request.data
        with no_tenant():
            task = SuccessTask.objects.select_related("organization", "assigned_to").filter(pk=task_id).first()
            if task is None:
                raise Http404
            for field in ("title", "notes"):
                if field in d:
                    setattr(task, field, (d[field] or "").strip())
            if "due_date" in d:
                task.due_date = d["due_date"] or None
            if "kind" in d and d["kind"] in SuccessTask.Kind.values:
                task.kind = d["kind"]
            if "assigned_to" in d:
                task.assigned_to = SuccessTasksView._agent(d["assigned_to"])
            if "status" in d:
                if d["status"] not in SuccessTask.Status.values:
                    raise ValidationError({"status": "Unknown status."})
                task.status = d["status"]
                task.completed_at = tz.now() if d["status"] == SuccessTask.Status.DONE else None
            task.save()
            task = SuccessTask.objects.select_related("organization", "assigned_to").get(pk=task.pk)
            return Response(success.task_row(task))


# ---------------------------------------------------------------------------
# Support Desk 3.0 (console)
# ---------------------------------------------------------------------------
class SupportTeamsView(PlatformConsoleMixin, APIView):
    """GET every team with members and queue numbers; POST a new team."""

    def get(self, request):
        from . import desk
        return Response(self.run(desk.teams, request.user))

    def post(self, request):
        from . import desk
        return Response(self.run(desk.save_team, request.user, request.data),
                        status=http_status.HTTP_201_CREATED)


class SupportTeamView(PlatformConsoleMixin, APIView):
    def patch(self, request, team_id):
        from . import desk
        return Response(self.run(desk.save_team, request.user, request.data, team_id))


class SupportTeamMembersView(PlatformConsoleMixin, APIView):
    """POST {user, role?, is_available?, remove?}: add, change or remove."""

    def post(self, request, team_id):
        from . import desk

        d = request.data
        return Response(self.run(
            desk.set_member, request.user, team_id, d.get("user"), role=d.get("role"),
            is_available=d.get("is_available") if "is_available" in d else None,
            remove=bool(d.get("remove"))))


class SupportTransferView(PlatformConsoleMixin, APIView):
    def post(self, request, request_id):
        from . import desk

        d = request.data
        return Response(self.run(desk.transfer, request.user, request_id, d.get("team"),
                                 agent_id=d.get("assigned_to"), note=d.get("note") or ""))


class SupportLinksView(PlatformConsoleMixin, APIView):
    """POST {ticket: "SUP-000042" | uuid, remove?} -> the linked tickets."""

    def post(self, request, request_id):
        from . import desk
        return Response(self.run(desk.link, request.user, request_id, request.data.get("ticket"),
                                 remove=bool(request.data.get("remove"))))


class SupportTicketTasksView(PlatformConsoleMixin, APIView):
    def post(self, request, request_id):
        from . import desk
        return Response(self.run(desk.create_ticket_task, request.user, request_id, request.data),
                        status=http_status.HTTP_201_CREATED)


class SupportSlaBoardView(PlatformConsoleMixin, APIView):
    def get(self, request):
        from . import desk
        return Response(self.run(desk.sla_board, request.user,
                                 team=request.query_params.get("team") or None))


class SupportMentionsView(PlatformConsoleMixin, APIView):
    """GET my mentions; POST marks them read (all, or ?ticket=)."""

    def get(self, request):
        from . import desk
        return Response(self.run(desk.mentions_for, request.user,
                                 unread_only=request.query_params.get("unread") == "1"))

    def post(self, request):
        from . import desk
        return Response({"marked": self.run(desk.mark_mentions_read, request.user,
                                            request.data.get("ticket") or None)})


class KnownIssuesView(PlatformConsoleMixin, APIView):
    """GET known issues; POST one (``from_ticket`` links that ticket)."""

    def get(self, request):
        from . import desk
        return Response(self.run(desk.known_issues, request.user))

    def post(self, request):
        from . import desk
        return Response(self.run(desk.save_issue, request.user, request.data,
                                 from_ticket=request.data.get("from_ticket") or None),
                        status=http_status.HTTP_201_CREATED)


class KnownIssueView(PlatformConsoleMixin, APIView):
    """PATCH; {state: "fixed", resolve_linked: true, fix_note} closes the loop."""

    def patch(self, request, issue_id):
        from . import desk
        return Response(self.run(desk.save_issue, request.user, request.data, issue_id))


class SuccessCampaignsView(_SuccessView):
    """GET campaigns and segment sizes; POST {segment, ...} launches one."""

    def get(self, request):
        from . import desk

        segment = request.query_params.get("segment")
        if segment:
            return Response({"segment": segment,
                             "organizations": self.run(desk.segment_members, segment)})
        return Response({"campaigns": self.run(desk.campaigns, request.user),
                         "segments": self.run(desk.segments_summary, request.user)})

    def post(self, request):
        from . import desk
        return Response(self.run(desk.create_campaign, request.user, request.data),
                        status=http_status.HTTP_201_CREATED)


class SuccessOperationsView(_SuccessView):
    """GET the founder view: revenue, customers, risk, support, renewals."""

    def get(self, request):
        from . import desk
        return Response(desk.operations(request.user))
