from rest_framework import viewsets
from users.admin_views import IsAdminOrSuperuser
from .models import AuditLog
from .serializers import AuditLogSerializer

class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdminOrSuperuser]

    def get_queryset(self):
        return AuditLog.objects.select_related("actor", "content_type").all()
