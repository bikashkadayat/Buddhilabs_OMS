"""Platform console routes, mounted at ``/api/v1/platform/``.

Slug-addressed rather than UUID-addressed: an operator reading a URL, a log
line or an audit entry can tell which customer it is about. The slug is
already the tenant's unique public identifier (it is their subdomain), so
this leaks nothing a hostname does not.

ACTIONS, NOT A STATUS FIELD. There is no ``PATCH {"status": "active"}`` route
for a subscription. Every lifecycle move is its own verb -- suspend, activate,
cancel, assign-plan, start-trial, extend -- because each one carries different
required input (a suspension needs a reason, an extension needs months) and
because that is what makes the audit trail readable afterwards.
"""
from django.urls import path

from .support import SupportRequestsView

from .views import (CounterRefreshView, MirrorReconcileView,
                    OrganizationActivateView, OrganizationArchiveView,
                    OrganizationAssignPlanView,
                    OrganizationBrandingAssetView, OrganizationBrandingView,
                    OrganizationDomainsView, OrganizationDomainView,
                    PlatformDomainsView,
                    OrganizationCancelView, OrganizationChangePlanView,
                    OrganizationDetailView, OrganizationExportDownloadView,
                    OrganizationExportManifestView, OrganizationExportView,
                    OrganizationExtendView,
                    OrganizationHealthView, OrganizationListCreateView,
                    OrganizationRepairView, OrganizationRestoreView,
                    OrganizationSettingsView,
                    OrganizationStatusView, OrganizationSuspendView,
                    OrganizationTrialView, OrganizationUsageView,
                    LaunchReadinessView, PlatformEventsView,
                    RegistrationFunnelView,
                    PaymentQueueView, PlanListView, PlatformAuditView,
                    PlatformDashboardView, PlatformHealthView,
                    PublicBrandingView,
                    OrganizationAccessView, OrganizationAccessSendView,
                    PlatformRecentView, PaymentDecisionView, PlatformPaymentProofView,
                    PaymentMethodsView, PaymentMethodView, CustomerHealthView,
                    SupportInboxView, SupportRequestView)
from .portal_views import (BrandingAssetView, BrandingView,
                           PaymentHistoryView, PaymentInstructionsView,
                           PaymentProofView, PlanRequestView,
                           SubscriptionView, TenantDomainsView,
                           TenantDomainVerifyView, TenantPlansView)
from .registration_views import (OnboardingView, RegistrationView,
                                 SlugAvailabilityView, VerificationView)

ORG = "platform/organizations/<slug:slug>"

urlpatterns = [
    # Part 1
    path("platform/dashboard/", PlatformDashboardView.as_view(),
         name="platform-dashboard"),
    path("platform/health/", PlatformHealthView.as_view(),
         name="platform-health"),
    path("platform/plans/", PlanListView.as_view(), name="platform-plans"),

    # Part 2
    path("platform/organizations/", OrganizationListCreateView.as_view(),
         name="platform-organizations"),
    path(f"{ORG}/", OrganizationDetailView.as_view(),
         name="platform-organization-detail"),
    path(f"{ORG}/access/", OrganizationAccessView.as_view(),
         name="platform-organization-access"),
    path(f"{ORG}/access/send/", OrganizationAccessSendView.as_view(),
         name="platform-organization-access-send"),
    path("platform/recent/", PlatformRecentView.as_view(),
         name="platform-recent"),
    path("platform/payments/<uuid:payment_id>/approve/",
         PaymentDecisionView.as_view(action="approve"),
         name="platform-payment-approve"),
    path("platform/payments/<uuid:payment_id>/reject/",
         PaymentDecisionView.as_view(action="reject"),
         name="platform-payment-reject"),
    path("platform/payments/<uuid:payment_id>/request-info/",
         PaymentDecisionView.as_view(action="request_info"),
         name="platform-payment-request-info"),
    path("platform/payments/<uuid:payment_id>/proof/",
         PlatformPaymentProofView.as_view(), name="platform-payment-proof"),
    path("platform/customer-health/", CustomerHealthView.as_view(),
         name="platform-customer-health"),
    path("platform/support/", SupportInboxView.as_view(), name="platform-support"),
    path("platform/support/<uuid:request_id>/", SupportRequestView.as_view(),
         name="platform-support-request"),
    path("support/requests/", SupportRequestsView.as_view(),
         name="support-requests"),
    path("platform/payment-methods/", PaymentMethodsView.as_view(),
         name="platform-payment-methods"),
    path("platform/payment-methods/<uuid:method_id>/",
         PaymentMethodView.as_view(), name="platform-payment-method"),
    path(f"{ORG}/usage/", OrganizationUsageView.as_view(),
         name="platform-organization-usage"),
    path(f"{ORG}/health/", OrganizationHealthView.as_view(),
         name="platform-organization-health"),
    path(f"{ORG}/repair/", OrganizationRepairView.as_view(),
         name="platform-organization-repair"),

    # Part 6
    path(f"{ORG}/suspend/", OrganizationSuspendView.as_view(),
         name="platform-organization-suspend"),
    path(f"{ORG}/activate/", OrganizationActivateView.as_view(),
         name="platform-organization-activate"),
    path(f"{ORG}/cancel/", OrganizationCancelView.as_view(),
         name="platform-organization-cancel"),
    path(f"{ORG}/status/", OrganizationStatusView.as_view(),
         name="platform-organization-status"),

    # Part 5
    path(f"{ORG}/assign-plan/", OrganizationAssignPlanView.as_view(),
         name="platform-organization-assign-plan"),
    path(f"{ORG}/change-plan/", OrganizationChangePlanView.as_view(),
         name="platform-organization-change-plan"),
    path(f"{ORG}/start-trial/", OrganizationTrialView.as_view(),
         name="platform-organization-start-trial"),
    path(f"{ORG}/extend/", OrganizationExtendView.as_view(),
         name="platform-organization-extend"),

    # Part 7
    path(f"{ORG}/branding/", OrganizationBrandingView.as_view(),
         name="platform-organization-branding"),
    path(f"{ORG}/branding/asset/", OrganizationBrandingAssetView.as_view(),
         name="platform-organization-branding-asset"),
    path(f"{ORG}/settings/", OrganizationSettingsView.as_view(),
         name="platform-organization-settings"),

    # Part 9
    # Phase S6.5: portability and the archive. Nested under the organization
    # for the same reason everything else here is -- an operator reading a URL
    # or an audit entry can tell which customer it concerns.
    path(f"{ORG}/exports/", OrganizationExportView.as_view(),
         name="organization-exports"),
    path(f"{ORG}/exports/<uuid:export_id>/manifest/",
         OrganizationExportManifestView.as_view(),
         name="organization-export-manifest"),
    path(f"{ORG}/exports/<uuid:export_id>/download/",
         OrganizationExportDownloadView.as_view(),
         name="organization-export-download"),
    path(f"{ORG}/archive/", OrganizationArchiveView.as_view(),
         name="organization-archive"),
    path(f"{ORG}/restore/", OrganizationRestoreView.as_view(),
         name="organization-restore"),

    # Phase S6.75: launch readiness and the event dashboards.
    path("platform/launch-readiness/", LaunchReadinessView.as_view(),
         name="platform-launch-readiness"),
    path("platform/events/", PlatformEventsView.as_view(),
         name="platform-events"),
    path("platform/registration-funnel/", RegistrationFunnelView.as_view(),
         name="platform-registration-funnel"),

    path("platform/audit/", PlatformAuditView.as_view(), name="platform-audit"),
    path("platform/payments/", PaymentQueueView.as_view(),
         name="platform-payments"),

    # Operations
    path("platform/counters/refresh/", CounterRefreshView.as_view(),
         name="platform-counters-refresh"),
    path("platform/mirrors/reconcile/", MirrorReconcileView.as_view(),
         name="platform-mirrors-reconcile"),

    # Tenant-facing, unauthenticated. Mounted under the exempt
    # /api/v1/tenant/public/ prefix so a suspended or unknown workspace can
    # still render a login page.
    # --- Phase S7: public self-service registration ---
    #
    # NOT under `platform/`: these are the only endpoints on this platform
    # that an anonymous stranger may write to, and grouping them with the
    # operator console would put them behind the same mental boundary as the
    # pages that suspend customers.
    path("register/", RegistrationView.as_view(), name="public-register"),
    path("register/slug/", SlugAvailabilityView.as_view(),
         name="public-register-slug"),
    path("register/verify/", VerificationView.as_view(),
         name="public-register-verify"),

    # The tenant's own first-login payload (Parts 6, 7, 8).
    path("tenant/onboarding/", OnboardingView.as_view(),
         name="tenant-onboarding"),

    # --- Phase S8: the customer's own subscription portal ---
    #
    # Under `tenant/`, like the onboarding payload: these are the endpoints a
    # CUSTOMER's administrator calls about their own organization, which is a
    # different audience from the console and from the public signup pages.
    path("tenant/subscription/", SubscriptionView.as_view(),
         name="tenant-subscription"),
    path("tenant/subscription/request/", PlanRequestView.as_view(),
         name="tenant-subscription-request"),
    path("tenant/plans/", TenantPlansView.as_view(), name="tenant-plans"),
    path("tenant/payment-instructions/", PaymentInstructionsView.as_view(),
         name="tenant-payment-instructions"),
    path("tenant/payments/", PaymentHistoryView.as_view(),
         name="tenant-payments"),
    path("tenant/payments/<str:reference>/proof/", PaymentProofView.as_view(),
         name="tenant-payment-proof"),

    # --- Phase S9: custom domains, operator side ---
    path("platform/domains/", PlatformDomainsView.as_view(),
         name="platform-domains"),
    path(f"{ORG}/domains/", OrganizationDomainsView.as_view(),
         name="platform-organization-domains"),
    path(f"{ORG}/domains/<str:hostname>/", OrganizationDomainView.as_view(),
         name="platform-organization-domain"),

    # --- Phase S9: the tenant's own branding and domains ---
    path("tenant/branding/", BrandingView.as_view(), name="tenant-branding"),
    path("tenant/branding/asset/", BrandingAssetView.as_view(),
         name="tenant-branding-asset"),
    path("tenant/domains/", TenantDomainsView.as_view(),
         name="tenant-domains"),
    path("tenant/domains/<str:hostname>/", TenantDomainVerifyView.as_view(),
         name="tenant-domain-verify"),

    path("tenant/public/branding/", PublicBrandingView.as_view(),
         name="tenant-public-branding"),
]
