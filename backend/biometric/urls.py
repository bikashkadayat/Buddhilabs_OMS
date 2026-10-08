from django.urls import include, path, re_path
from rest_framework.routers import SimpleRouter

from .console_views import PlatformDeviceDetailView, PlatformDeviceListView
from .device_views import AttendanceModeView, AutoMatchView, BiometricDeviceViewSet
from .views import (
    BiometricEmployeeViewSet,
    BulkSyncView,
    MappingSuggestionsView,
    PunchIngestView,
    RosterSyncView,
    UnmappedPunchesView,
)

# SimpleRouter (no API-root view) — several routers are mounted under
# /api/v1/ and a DefaultRouter's root view would clash with them.
router = SimpleRouter()
router.register(r"biometric/mappings", BiometricEmployeeViewSet, basename="biometric-mapping")
# Organization Settings -> Attendance -> Biometric Devices.
router.register(r"biometric/devices", BiometricDeviceViewSet, basename="biometric-device")

urlpatterns = [
    # Device ingest — API key + HMAC, not JWT.
    path("biometric/punch/", PunchIngestView.as_view(), name="biometric-punch"),
    path("biometric/bulk-sync/", BulkSyncView.as_view(), name="biometric-bulk-sync"),
    path("biometric/roster-sync/", RosterSyncView.as_view(), name="biometric-roster-sync"),
    # HR/Admin — JWT.
    path("biometric/unmapped/", UnmappedPunchesView.as_view(), name="biometric-unmapped"),
    path("biometric/suggestions/", MappingSuggestionsView.as_view(), name="biometric-suggestions"),
    # Before the router: "auto-match" would otherwise be read as a mapping pk.
    path("biometric/mappings/auto-match/", AutoMatchView.as_view(), name="biometric-auto-match"),
    path("attendance/settings/mode/", AttendanceModeView.as_view(), name="attendance-mode"),
    # Platform console: a platform operator managing one tenant's devices.
    path("platform/organizations/<slug:slug>/biometric-devices/",
         PlatformDeviceListView.as_view(), name="platform-biometric-devices"),
    path("platform/organizations/<slug:slug>/biometric-devices/<uuid:device_id>/",
         PlatformDeviceDetailView.as_view(), name="platform-biometric-device"),
    re_path(r"^platform/organizations/(?P<slug>[-\w]+)/biometric-devices/"
            r"(?P<device_id>[0-9a-f-]{36})/(?P<operation>test-connection|sync)/$",
            PlatformDeviceDetailView.as_view(), name="platform-biometric-device-op"),
    path("", include(router.urls)),
]
