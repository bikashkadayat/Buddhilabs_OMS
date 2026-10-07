from rest_framework.routers import DefaultRouter

from .views import MinuteViewSet

router = DefaultRouter()
router.register("minutes", MinuteViewSet, basename="minute")

urlpatterns = router.urls
