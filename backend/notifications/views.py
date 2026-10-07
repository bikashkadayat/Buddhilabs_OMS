from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .dispatcher import get_preference
from .models import Category, Notification, NotificationPreference
from .serializers import NotificationPreferenceSerializer, NotificationSerializer


GROUPS = {
    "attendance": (("ATTENDANCE_", "WFH_", "COMP_"), (), ()),
    "leave": (("LEAVE_",), (), ()),
    "tasks": (("TASK_",), (), ()),
    "documents": (("MEMO_", "MINUTE_", "CIRCULAR_"), (), ()),
    "payments": (("PAYMENT_",), (), ()),
    "approvals": ((), ("_REQUIRED", "_SUBMITTED"),
                  ("MEMO_ASSIGNED_TO_REVIEW", "TASK_REVIEW_REMINDER")),
}


class NotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        qs = Notification.objects.filter(recipient=self.request.user)
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            qs = qs.filter(is_read=is_read.lower() in ("1", "true", "yes"))
        # Filter by module rather than by exact category. Categories are named
        # MODULE_EVENT, so the prefix IS the module — the same rule
        # notifications.emails.module_for uses, which keeps the filter and the
        # audit log agreeing about what "Leave" means. Server-side on purpose:
        # filtering the fetched page instead would silently hide rows on page 2.
        module = self.request.query_params.get("module")
        if module:
            qs = qs.filter(category__startswith=f"{module.upper()}_")
        # The notification centre's tabs. "Approvals" cuts across modules:
        # anything asking this person to decide or act.
        group = (self.request.query_params.get("group") or "").lower()
        if group in GROUPS:
            prefixes, suffixes, exact = GROUPS[group]
            match = Q(pk__in=[])
            for prefix in prefixes:
                match |= Q(category__startswith=prefix)
            for suffix in suffixes:
                match |= Q(category__endswith=suffix)
            if exact:
                match |= Q(category__in=exact)
            qs = qs.filter(match)
        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({"unread": count})

    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        notif = self.get_object()
        if not notif.is_read:
            notif.is_read = True
            notif.read_at = timezone.now()
            notif.save(update_fields=["is_read", "read_at"])
        return Response(NotificationSerializer(notif).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated = Notification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True, read_at=timezone.now(),
        )
        return Response({"marked_read": updated})

    @action(detail=False, methods=["get", "post"], url_path="preferences")
    def preferences(self, request):
        if request.method == "GET":
            data = []
            for category in Category.values:
                pref = get_preference(request.user, category)
                data.append(NotificationPreferenceSerializer(pref).data)
            return Response(data)

        serializer = NotificationPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        pref, _ = NotificationPreference.objects.update_or_create(
            user=request.user, category=v["category"],
            defaults={"in_app_enabled": v.get("in_app_enabled", True), "email_enabled": v.get("email_enabled", True)},
        )
        return Response(NotificationPreferenceSerializer(pref).data, status=status.HTTP_200_OK)
