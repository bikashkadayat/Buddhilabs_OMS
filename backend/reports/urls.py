from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AnalyticsView, ReportsViewSet, ScheduledReportViewSet

router = SimpleRouter()
router.register("reports", ReportsViewSet, basename="report")
router.register("scheduled-reports", ScheduledReportViewSet, basename="scheduled-report")

urlpatterns = [
    path("reports/analytics/", AnalyticsView.as_view(), name="reports-analytics"),
] + router.urls
