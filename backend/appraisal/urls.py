from rest_framework.routers import DefaultRouter

from .views import AppraisalCycleViewSet, AppraisalViewSet, CompetencyViewSet

router = DefaultRouter()
router.register("appraisals", AppraisalViewSet, basename="appraisal")
router.register("appraisal-cycles", AppraisalCycleViewSet,
                basename="appraisal-cycle")
router.register("competencies", CompetencyViewSet, basename="competency")

urlpatterns = router.urls
