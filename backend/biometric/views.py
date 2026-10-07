"""Biometric mapping API — HR/Admin only.

Read-only listing plus four write paths, all audited: map, remap, unmap and
backfill. Backfill is split into preview + execute so re-attributing history is
always a deliberate second action, never a side effect.
"""
from django.db.models import Count, Max, Min
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from users.permissions import IsApproverOrAdmin

from . import services
from .authentication import DeviceAPIKeyAuthentication, IsRegisteredDevice
from .ingest import ingest_punches, ingest_roster, record_failed_batch
from .matching import suggest_for_mapping
from .models import AttendancePunch, BiometricEmployee, DeviceSyncLog
from .serializers import (
    AttendancePunchSerializer,
    BackfillRequestSerializer,
    BiometricEmployeeSerializer,
    BulkSyncSerializer,
    MappingWriteSerializer,
    RosterSyncSerializer,
    SinglePunchSerializer,
    SuggestionSerializer,
    UnmapRequestSerializer,
    UnmappedGroupSerializer,
)
from .throttling import DeviceRateThrottle


def _bool_param(value):
    return str(value).lower() in ("1", "true", "yes")


class BiometricEmployeeViewSet(viewsets.ModelViewSet):
    """CRUD for device-ID -> employee mappings.

    DELETE is a soft unmap: the row is retired with an end date, never removed,
    because a retired mapping is what bounds a future occupant's backfill.
    """
    serializer_class = BiometricEmployeeSerializer
    permission_classes = [IsAuthenticated, IsApproverOrAdmin]
    search_fields = ["device_user_id", "device_name", "user__first_name",
                     "user__last_name", "user__email", "user__employee_id"]
    ordering_fields = ["device_user_id", "created_at", "mapped_at"]

    def get_queryset(self):
        qs = BiometricEmployee.objects.select_related(
            "device", "user", "user__department_ref", "mapped_by")
        params = self.request.query_params
        if params.get("device"):
            qs = qs.filter(device_id=params["device"])
        if params.get("is_active") is not None and params.get("is_active") != "":
            qs = qs.filter(is_active=_bool_param(params["is_active"]))
        if params.get("mapped") is not None and params.get("mapped") != "":
            qs = qs.filter(user__isnull=not _bool_param(params["mapped"]))
        return qs.order_by("device__name", "device_user_id")

    def perform_create(self, serializer):
        mapping = serializer.save()
        if mapping.user_id:
            # Route through the service so the uniqueness check and the audit
            # entry are identical to the map action's.
            services.map_employee(
                mapping, mapping.user, actor=self.request.user,
                effective_from=mapping.effective_from, request=self.request,
            )

    def perform_update(self, serializer):
        previous_user_id = serializer.instance.user_id
        mapping = serializer.save()
        if mapping.user_id != previous_user_id and mapping.user_id:
            services.audit_mapping_change(
                self.request.user, AuditLog.Action.UPDATE, mapping,
                "BIOMETRIC_MAPPING_UPDATED", self.request,
                previous_user=str(previous_user_id) if previous_user_id else None,
            )

    def destroy(self, request, *args, **kwargs):
        """Soft unmap — user=NULL, is_active=False, effective_until=today."""
        mapping = self.get_object()
        ser = UnmapRequestSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)
        mapping, detached = services.unmap_employee(
            mapping, actor=request.user,
            detach_punches=ser.validated_data["detach_punches"], request=request,
        )
        return Response(
            {"detail": "Mapping unmapped.", "punches_detached": detached,
             "mapping": BiometricEmployeeSerializer(mapping).data},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def map(self, request, pk=None):
        """Attach an employee to an unmapped device ID."""
        mapping = self.get_object()
        ser = MappingWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        mapping = services.map_employee(
            mapping, ser.validated_data["user"], actor=request.user,
            effective_from=ser.validated_data.get("effective_from"), request=request,
        )
        return Response(BiometricEmployeeSerializer(mapping).data)

    @action(detail=True, methods=["post"])
    def remap(self, request, pk=None):
        """Reassign a device ID, retiring the current mapping with its history."""
        mapping = self.get_object()
        ser = MappingWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        replacement = services.remap_employee(
            mapping, ser.validated_data["user"], actor=request.user,
            effective_from=ser.validated_data.get("effective_from"), request=request,
        )
        return Response(BiometricEmployeeSerializer(replacement).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def suggestions(self, request, pk=None):
        mapping = self.get_object()
        results = suggest_for_mapping(mapping, limit=int(request.query_params.get("limit", 5)))
        return Response(SuggestionSerializer(results, many=True).data)

    @action(detail=True, methods=["get"], url_path="backfill-preview")
    def backfill_preview(self, request, pk=None):
        """What a backfill would claim — count, date range, device, employee."""
        mapping = self.get_object()
        ser = BackfillRequestSerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        return Response(services.preview_backfill(
            mapping,
            date_from=ser.validated_data.get("date_from"),
            date_to=ser.validated_data.get("date_to"),
        ))

    @action(detail=True, methods=["post"])
    def backfill(self, request, pk=None):
        """Execute the backfill. Separate call = the HR confirmation step."""
        mapping = self.get_object()
        ser = BackfillRequestSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)
        updated = services.backfill_punches(
            mapping, actor=request.user,
            date_from=ser.validated_data.get("date_from"),
            date_to=ser.validated_data.get("date_to"),
            allow_unbounded=ser.validated_data["allow_unbounded"],
            request=request,
        )
        return Response({"detail": "Backfill complete.", "records_updated": updated,
                         "mapping": BiometricEmployeeSerializer(mapping).data})


class UnmappedPunchesView(APIView):
    """The unmapped queue, grouped by device ID — the unit HR actually resolves.

    Pass ?flat=1 for the raw punch rows behind a group.
    """
    permission_classes = [IsAuthenticated, IsApproverOrAdmin]

    def get(self, request):
        qs = AttendancePunch.objects.filter(user__isnull=True)
        if request.query_params.get("device"):
            qs = qs.filter(device_id=request.query_params["device"])

        if _bool_param(request.query_params.get("flat")):
            qs = qs.select_related("device", "user").order_by("-timestamp")[:500]
            return Response(AttendancePunchSerializer(qs, many=True).data)

        groups = (qs.values("device_id", "device__label", "employee_device_id")
                    .annotate(punch_count=Count("id"),
                              first_punch_date=Min("local_date"),
                              last_punch_date=Max("local_date"))
                    .order_by("device__label", "employee_device_id"))

        mappings = {
            (m.device_id, m.device_user_id): m
            for m in BiometricEmployee.objects.filter(is_active=True).select_related("device")
        }
        include_suggestions = _bool_param(request.query_params.get("suggestions"))

        rows = []
        for g in groups:
            mapping = mappings.get((g["device_id"], g["employee_device_id"]))
            row = {
                "device": g["device_id"],
                "device_label": g["device__label"],
                "device_user_id": g["employee_device_id"],
                "device_name": mapping.device_name if mapping else "",
                "mapping_id": mapping.pk if mapping else None,
                "punch_count": g["punch_count"],
                "first_punch_date": g["first_punch_date"],
                "last_punch_date": g["last_punch_date"],
                "suggestions": suggest_for_mapping(mapping) if (include_suggestions and mapping) else [],
            }
            rows.append(row)
        return Response(UnmappedGroupSerializer(rows, many=True).data)


class MappingSuggestionsView(APIView):
    """Suggestions for every unmapped device ID, or one specific device.

    Never applies anything — the response is a proposal that HR must confirm.
    """
    permission_classes = [IsAuthenticated, IsApproverOrAdmin]

    def get(self, request):
        qs = BiometricEmployee.objects.filter(
            is_active=True, user__isnull=True).select_related("device")
        if request.query_params.get("device"):
            qs = qs.filter(device_id=request.query_params["device"])
        if request.query_params.get("mapping"):
            qs = qs.filter(pk=request.query_params["mapping"])

        limit = int(request.query_params.get("limit", 5))
        payload = []
        for mapping in qs.order_by("device__name", "device_user_id"):
            results = suggest_for_mapping(mapping, limit=limit)
            payload.append({
                "mapping_id": str(mapping.pk),
                "device": mapping.device.label,
                "device_user_id": mapping.device_user_id,
                "device_name": mapping.device_name,
                "suggestions": SuggestionSerializer(results, many=True).data,
                "requires_confirmation": True,
            })
        return Response(payload)


# ===========================================================================
# Device ingest (Phase 6) — authenticated by API key + HMAC, not JWT
# ===========================================================================

class DeviceIngestView(APIView):
    """Shared plumbing for the three collector endpoints."""
    authentication_classes = [DeviceAPIKeyAuthentication]
    permission_classes = [IsRegisteredDevice]
    throttle_classes = [DeviceRateThrottle]

    @property
    def device(self):
        return self.request.auth

    def client_ip(self):
        forwarded = self.request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip()
        return self.request.META.get("REMOTE_ADDR")


class PunchIngestView(DeviceIngestView):
    """POST /api/v1/biometric/punch/ — a single live punch."""

    def post(self, request):
        ser = SinglePunchSerializer(data=request.data)
        if not ser.is_valid():
            record_failed_batch(
                self.device, DeviceSyncLog.SyncType.LIVE, error=str(ser.errors),
                client_ip=self.client_ip(), received=1)
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        data = ser.validated_data
        summary = ingest_punches(
            self.device, [data], source=data["source"],
            sync_type=DeviceSyncLog.SyncType.LIVE, client_ip=self.client_ip(),
            queue_depth=data["queue_depth"],
        )
        return Response(summary, status=status.HTTP_201_CREATED
                        if summary["created"] else status.HTTP_200_OK)


class BulkSyncView(DeviceIngestView):
    """POST /api/v1/biometric/bulk-sync/ — a backlog chunk (<= BIOMETRIC_MAX_BATCH).

    Idempotent: the collector re-sends a device's whole backlog on every
    reconnect by design, so replaying an identical batch creates nothing.
    """

    def post(self, request):
        ser = BulkSyncSerializer(data=request.data)
        if not ser.is_valid():
            record_failed_batch(
                self.device, DeviceSyncLog.SyncType.HISTORY, error=str(ser.errors),
                client_ip=self.client_ip(),
                received=len(request.data.get("punches", []) or []))
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        data = ser.validated_data
        summary = ingest_punches(
            self.device, data["punches"], source=data["source"],
            sync_type=DeviceSyncLog.SyncType.HISTORY, client_ip=self.client_ip(),
            queue_depth=data["queue_depth"],
        )
        return Response(summary, status=status.HTTP_200_OK)


class RosterSyncView(DeviceIngestView):
    """POST /api/v1/biometric/roster-sync/ — the device's enrolled users.

    Creates unmapped BiometricEmployee rows. It never links an employee: that
    is an HR decision made through the Phase 4 mapping API.
    """

    def post(self, request):
        ser = RosterSyncSerializer(data=request.data)
        if not ser.is_valid():
            record_failed_batch(
                self.device, DeviceSyncLog.SyncType.ROSTER, error=str(ser.errors),
                client_ip=self.client_ip())
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        summary = ingest_roster(
            self.device, ser.validated_data["employees"], client_ip=self.client_ip())
        return Response(summary, status=status.HTTP_200_OK)
