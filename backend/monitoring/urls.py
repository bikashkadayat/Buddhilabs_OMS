"""Monitoring routes, under the project's existing /api/v1/ prefix."""
from django.urls import path

from .views import AlertStatusView, CronStatusView, SystemHealthView

urlpatterns = [
    path("monitoring/health/", SystemHealthView.as_view(), name="monitoring-health"),
    path("monitoring/cron/", CronStatusView.as_view(), name="monitoring-cron"),
    path("monitoring/alerts/", AlertStatusView.as_view(), name="monitoring-alerts"),
]
