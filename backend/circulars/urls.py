from rest_framework.routers import DefaultRouter

from .views import CircularViewSet

router = DefaultRouter()
router.register("circulars", CircularViewSet, basename="circular")

urlpatterns = router.urls
