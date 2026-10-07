from rest_framework.routers import DefaultRouter

from .analytics_views import TaskAnalyticsViewSet
from .views import DepartmentGoalViewSet, TaskTemplateViewSet, TaskViewSet

router = DefaultRouter()
router.register("tasks", TaskViewSet, basename="task")
# Phase T2.9. A sibling collection rather than a nested route: a template is not
# owned by any one task, and /tasks/<id>/templates/ would imply it was.
router.register("task-templates", TaskTemplateViewSet, basename="task-template")
router.register("department-goals", DepartmentGoalViewSet, basename="department-goal")
# Phase T5. Analytics is its own collection: every action is a computation over
# the caller's visible tasks, sharing none of TaskViewSet's machinery — no
# serializer, no object permissions, no scopes.
router.register("task-analytics", TaskAnalyticsViewSet, basename="task-analytics")

urlpatterns = router.urls
