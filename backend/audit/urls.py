from rest_framework.routers import DefaultRouter
from .admin_views import AuditLogViewSet

router = DefaultRouter()
router.register(r"", AuditLogViewSet, basename="audit-log")

urlpatterns = router.urls
