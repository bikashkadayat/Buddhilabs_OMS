from django.urls import include, path
from rest_framework.routers import SimpleRouter

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

urlpatterns = [
    # Device ingest — API key + HMAC, not JWT.
    path("biometric/punch/", PunchIngestView.as_view(), name="biometric-punch"),
    path("biometric/bulk-sync/", BulkSyncView.as_view(), name="biometric-bulk-sync"),
    path("biometric/roster-sync/", RosterSyncView.as_view(), name="biometric-roster-sync"),
    # HR/Admin — JWT.
    path("biometric/unmapped/", UnmappedPunchesView.as_view(), name="biometric-unmapped"),
    path("biometric/suggestions/", MappingSuggestionsView.as_view(), name="biometric-suggestions"),
    path("", include(router.urls)),
]
