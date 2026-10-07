from rest_framework.routers import DefaultRouter

from .views import MemoViewSet, MemoTemplateViewSet

router = DefaultRouter()
router.register("memos", MemoViewSet, basename="memo")
router.register("memo-templates", MemoTemplateViewSet, basename="memo-template")

urlpatterns = router.urls
