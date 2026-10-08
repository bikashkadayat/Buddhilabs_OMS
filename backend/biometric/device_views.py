"""Organization Settings -> Attendance -> Biometric Devices.

Device setup, connection test, manual sync, the device dashboard and the
sync history, for an organization's own administrator. HR (approver role) may
read the dashboard and logs; only an administrator may add, change, test or
sync a device.

Every queryset here goes through ``BiometricDevice.objects``, the tenant-scoped
manager, so a device id from another organization is a 404 -- and under
PostgreSQL row-level security the database refuses the row as well.
"""
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import log_action
from users import roles
from users.permissions import IsApproverOrAdmin

from . import devices, services
from .models import AttendancePunch, BiometricDevice, BiometricEmployee, DeviceSyncLog
from .serializers import (
    AutoMatchRequestSerializer,
    BiometricDeviceSerializer,
    BiometricEmployeeSerializer,
    DeviceConnectionTestSerializer,
    DeviceSyncLogSerializer,
)


class IsOrganizationAdmin(BasePermission):
    """An administrator OF A TENANT. Platform staff use the console instead."""

    message = "Only your organization's administrator can manage biometric devices."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated
                    and not getattr(user, "is_platform_staff", False)
                    and roles.is_admin(user))


def _count(model, **filters):
    """A correlated COUNT subquery -- two Count() joins would multiply."""
    return Coalesce(Subquery(
        model.objects.filter(device=OuterRef("pk"), **filters)
        .order_by().values("device").annotate(n=Count("pk")).values("n")[:1],
        output_field=IntegerField()), 0)


def annotated_devices():
    return (BiometricDevice.objects
            .annotate(attendance_imported=_count(AttendancePunch),
                      mapped_users=_count(BiometricEmployee, is_active=True,
                                          user__isnull=False),
                      unmapped_users=_count(BiometricEmployee, is_active=True,
                                            user__isnull=True))
            .order_by("name"))


def _audit(request, device, event, **extra):
    log_action(request.user, AuditLog.Action.UPDATE, instance=device,
               changes={"event": event, "device": device.name, **extra},
               request=request)


class BiometricDeviceViewSet(viewsets.ModelViewSet):
    serializer_class = BiometricDeviceSerializer
    READ_ACTIONS = {"list", "retrieve", "dashboard", "sync_logs", "device_users"}

    def get_permissions(self):
        if self.action in self.READ_ACTIONS:
            return [IsAuthenticated(), IsApproverOrAdmin()]
        return [IsAuthenticated(), IsOrganizationAdmin()]

    def get_queryset(self):
        return annotated_devices()

    def _fresh(self, device):
        return annotated_devices().get(pk=device.pk)

    def perform_create(self, serializer):
        device = serializer.save(created_by=self.request.user)
        _audit(self.request, device, "BIOMETRIC_DEVICE_CREATED",
               host=device.host, port=device.port, device_type=device.device_type)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(self.get_serializer(self._fresh(serializer.instance)).data,
                        status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        before = {f: getattr(serializer.instance, f) for f in
                  ("host", "port", "device_type", "sync_interval_minutes", "is_active")}
        device = serializer.save()
        changed = {f: {"from": str(v), "to": str(getattr(device, f))}
                   for f, v in before.items() if getattr(device, f) != v}
        if "comm_key" in serializer.validated_data:
            changed["comm_key"] = "changed"
        _audit(self.request, device, "BIOMETRIC_DEVICE_UPDATED", changes=changed)

    def destroy(self, request, *args, **kwargs):
        """Delete a device that never imported anything; deactivate one that did.

        Punches are audit data and PROTECT their device, so a device with
        history is switched off rather than removed -- its attendance stays
        traceable to the terminal that recorded it.
        """
        device = self.get_object()
        if AttendancePunch.objects.filter(device=device).exists():
            device.is_active = False
            device.save(update_fields=["is_active", "updated_at"])
            _audit(request, device, "BIOMETRIC_DEVICE_DEACTIVATED")
            return Response({"detail": "This device has imported attendance, so it "
                                       "was deactivated rather than deleted.",
                             "deactivated": True}, status=status.HTTP_200_OK)
        _audit(request, device, "BIOMETRIC_DEVICE_DELETED")
        device.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # -- connection test --------------------------------------------------
    @action(detail=False, methods=["post"], url_path="test-connection")
    def test_unsaved(self, request):
        """Test the Add Device form's address before anything is saved."""
        ser = DeviceConnectionTestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        result = devices.test_connection(
            host=data["host"], port=data["port"], comm_key=data["comm_key"],
            device_type=data["device_type"], actor=request.user)
        return Response(result)

    @action(detail=True, methods=["post"], url_path="test-connection")
    def test_connection(self, request, pk=None):
        device = self.get_object()
        if not device.host:
            raise ValidationError({"host": "This device has no IP address to test."})
        result = devices.test_connection(
            host=device.host, port=device.port, comm_key=device.comm_key,
            device_type=device.device_type, device=device, actor=request.user)
        result["device"] = self.get_serializer(self._fresh(device)).data
        return Response(result)

    # -- sync -------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        """Sync now. Pulls users and attendance, maps, derives. Logged."""
        device = self.get_object()
        if not device.host:
            raise ValidationError({"host": "This device has no IP address; it pushes "
                                           "its attendance rather than being pulled."})
        if not device.is_active:
            raise ValidationError({"is_active": "This device is inactive. Activate it to sync."})
        result = devices.run_pull_sync(
            device, trigger=DeviceSyncLog.Trigger.MANUAL, actor=request.user)
        summary = result.pop("summary", None) or {}
        result["punches"] = summary.get("punches")
        result["roster"] = ({k: v for k, v in summary["roster"].items()
                             if k != "device_user_ids"} if summary.get("roster") else None)
        if result["punches"]:
            result["punches"].pop("device_user_ids", None)
        result["warnings"] = summary.get("warnings", [])
        result["device"] = self.get_serializer(self._fresh(device)).data
        code = status.HTTP_200_OK if result["status"] != "failed" else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=code)

    @action(detail=True, methods=["post"], url_path="reset-identity")
    def reset_identity(self, request, pk=None):
        """Forget the recorded serial -- for a terminal that was replaced."""
        device = self.get_object()
        previous = device.hardware_serial
        device.hardware_serial = ""
        device.save(update_fields=["hardware_serial", "updated_at"])
        _audit(request, device, "BIOMETRIC_DEVICE_IDENTITY_RESET", previous_serial=previous)
        return Response(self.get_serializer(self._fresh(device)).data)

    # -- read -------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="sync-logs")
    def sync_logs(self, request, pk=None):
        device = self.get_object()
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 500))
        except (TypeError, ValueError):
            limit = 50
        logs = (DeviceSyncLog.objects.filter(device=device)
                .select_related("triggered_by").order_by("-started_at")[:limit])
        return Response(DeviceSyncLogSerializer(logs, many=True).data)

    @action(detail=True, methods=["get"], url_path="users")
    def device_users(self, request, pk=None):
        """The device's enrolled users and who each is mapped to."""
        device = self.get_object()
        qs = (BiometricEmployee.objects.filter(device=device, is_active=True)
              .select_related("device", "user", "user__department_ref", "mapped_by")
              .order_by("device_user_id"))
        mapped = request.query_params.get("mapped")
        if mapped in ("true", "1"):
            qs = qs.filter(user__isnull=False)
        elif mapped in ("false", "0"):
            qs = qs.filter(user__isnull=True)
        return Response(BiometricEmployeeSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        """One row per device plus organization-wide totals."""
        rows = self.get_serializer(self.get_queryset(), many=True).data
        active = [r for r in rows if r["is_active"]]
        return Response({
            "devices": rows,
            "totals": {
                "devices": len(rows),
                "active": len(active),
                "online": sum(1 for r in active if r["connection_status"] == "online"),
                "offline": sum(1 for r in active if r["connection_status"] == "offline"),
                "with_errors": sum(1 for r in active if r["last_sync_status"] == "failed"),
                "attendance_imported": sum(r["attendance_imported"] or 0 for r in rows),
                "unmapped_users": sum(r["unmapped_users"] or 0 for r in active),
            },
        })


class AutoMatchView(APIView):
    """POST /biometric/mappings/auto-match/ -- preview, or apply with apply=true."""
    permission_classes = [IsAuthenticated, IsApproverOrAdmin]

    def post(self, request):
        ser = AutoMatchRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        device = None
        if ser.validated_data.get("device"):
            device = BiometricDevice.objects.filter(pk=ser.validated_data["device"]).first()
            if device is None:
                return Response({"detail": "Device not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(services.auto_match(
            device=device, apply=ser.validated_data["apply"],
            actor=request.user, request=request))


class AttendanceModeView(APIView):
    """GET/PATCH the organization's attendance mode -- Organization Settings.

    The same column the platform console has always been able to set; this is
    the organization administrator's own way in. Blank means "inherit the
    platform default", which is how every OrganizationSettings column behaves.
    """
    permission_classes = [IsAuthenticated]
    CHOICES = ("", "app_only", "biometric_only", "both")

    def _payload(self, row):
        from attendance import config
        return {
            "attendance_mode": row.attendance_mode if row else "",
            "effective_mode": config.attendance_mode(),
            "choices": [{"value": "app_only", "label": "App only"},
                        {"value": "biometric_only", "label": "Biometric only"},
                        {"value": "both", "label": "App + Biometric"}],
            "can_edit": roles.is_admin(self.request.user),
        }

    def _row(self):
        from tenancy.models import OrganizationSettings
        from tenancy.scoping import active_organization

        organization = active_organization(required=False)
        if organization is None:
            return None, None
        row, _ = OrganizationSettings.objects.get_or_create(organization=organization)
        return organization, row

    def get(self, request):
        _org, row = self._row()
        return Response(self._payload(row))

    def patch(self, request):
        if not IsOrganizationAdmin().has_permission(request, self):
            return Response({"detail": IsOrganizationAdmin.message},
                            status=status.HTTP_403_FORBIDDEN)
        mode = request.data.get("attendance_mode")
        if mode is None or mode not in self.CHOICES:
            raise ValidationError({"attendance_mode": "Choose app_only, biometric_only or both."})
        organization, row = self._row()
        if row is None:
            raise ValidationError({"detail": "No organization is in context."})
        before = row.attendance_mode
        if before != mode:
            row.attendance_mode = mode
            row.save(update_fields=["attendance_mode", "updated_at"])
            log_action(request.user, AuditLog.Action.UPDATE, instance=row, changes={
                "event": "ATTENDANCE_MODE_CHANGED", "from": before or "(platform default)",
                "to": mode or "(platform default)"}, request=request)
        return Response(self._payload(row))
