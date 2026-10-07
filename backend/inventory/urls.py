from django.urls import path
from rest_framework.routers import DefaultRouter

from .lifecycle_views import (
    AssetHistoryView, AssetLifecycleView, AssetQRView, AssetRequestViewSet,
    AssetReturnViewSet, AssetScanView, EmployeeAssetProfileView,
    InventoryDashboardView, InventoryReportView, MaintenanceTicketViewSet,
)
from .disposal_views import (
    AssetDisposalViewSet, AssetLifecycleSummaryView, DisposalOptionsView,
)
from .transfer_views import (
    AssetCustodyView, AssetTransferViewSet, AssetVisibilityOptionsView,
    ExitClearanceView, TransferOptionsView,
)
from .views import (
    AssignmentViewSet, InventoryCategoryViewSet, InventoryItemViewSet,
    ManagerEmployeeListView, TakeOutEligibleAssetsView, TakeOutRequestViewSet)

router = DefaultRouter()
router.register(r"inventory/categories", InventoryCategoryViewSet, basename="inventory-category")
router.register(r"inventory/items", InventoryItemViewSet, basename="inventory-item")
router.register(r"inventory/assignments", AssignmentViewSet, basename="inventory-assignment")
router.register(r"inventory/takeouts", TakeOutRequestViewSet, basename="inventory-takeout")
# Phase 70 workflows.
router.register(r"inventory/requests", AssetRequestViewSet, basename="inventory-request")
router.register(r"inventory/returns", AssetReturnViewSet, basename="inventory-return")
router.register(r"inventory/maintenance", MaintenanceTicketViewSet, basename="inventory-maintenance")
# Phase ASSET-CUSTODY-TRANSFER.
router.register(r"inventory/transfers", AssetTransferViewSet, basename="inventory-transfer")
# Phase ASSET-LIFECYCLE-DISPOSAL.
router.register(r"inventory/disposals", AssetDisposalViewSet, basename="inventory-disposal")

urlpatterns = router.urls + [
    path("inventory/employees/", ManagerEmployeeListView.as_view(), name="inventory-employees"),

    # Phase 70.19-A. Singular "takeout/" so it cannot collide with the router's
    # "takeouts/<pk>/" detail route, which would otherwise try to read
    # "eligible-assets" as a request id.
    path("inventory/takeout/eligible-assets/", TakeOutEligibleAssetsView.as_view(),
         name="inventory-takeout-eligible-assets"),

    # Phase 70. `scan` and `dashboard` are registered BEFORE the <pk> routes: the
    # router's item detail route would otherwise swallow them as asset ids.
    path("inventory/dashboard/", InventoryDashboardView.as_view(),
         name="inventory-dashboard"),
    path("inventory/scan/", AssetScanView.as_view(), name="inventory-scan"),
    path("inventory/reports/<str:name>/", InventoryReportView.as_view(),
         name="inventory-report"),
    path("inventory/profile/", EmployeeAssetProfileView.as_view(),
         name="inventory-my-profile"),
    path("inventory/profile/<uuid:pk>/", EmployeeAssetProfileView.as_view(),
         name="inventory-profile"),
    path("inventory/items/<uuid:pk>/history/", AssetHistoryView.as_view(),
         name="inventory-item-history"),
    # Phase ASSET-CUSTODY-TRANSFER. "transfer-options", hyphenated and outside the
    # router's prefix, so it cannot be read as a transfer id by transfers/<pk>/.
    path("inventory/transfer-options/", TransferOptionsView.as_view(),
         name="inventory-transfer-options"),
    # Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD.
    path("inventory/visibility-options/", AssetVisibilityOptionsView.as_view(),
         name="inventory-visibility-options"),
    path("inventory/exit-clearance/", ExitClearanceView.as_view(),
         name="inventory-exit-clearance-mine"),
    path("inventory/exit-clearance/<uuid:pk>/", ExitClearanceView.as_view(),
         name="inventory-exit-clearance"),
    path("inventory/items/<uuid:pk>/custody/", AssetCustodyView.as_view(),
         name="inventory-item-custody"),
    # Phase ASSET-LIFECYCLE-DISPOSAL. Hyphenated, outside the router prefix, for
    # the same reason as transfer-options.
    path("inventory/disposal-options/", DisposalOptionsView.as_view(),
         name="inventory-disposal-options"),
    path("inventory/items/<uuid:pk>/lifecycle-summary/", AssetLifecycleSummaryView.as_view(),
         name="inventory-item-lifecycle-summary"),
    path("inventory/items/<uuid:pk>/qr/", AssetQRView.as_view(),
         name="inventory-item-qr"),
    # One route for the four lifecycle verbs rather than four, so adding a fifth is
    # a branch rather than a URL.
    path("inventory/items/<uuid:pk>/lifecycle/<str:verb>/",
         AssetLifecycleView.as_view(), name="inventory-item-lifecycle"),
]
