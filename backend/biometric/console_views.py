"""Platform console: a platform operator sets up a tenant's biometric devices.

The brief has "Platform Admin or Organization Admin" entering the device. The
organization administrator uses Settings (``device_views``); a platform
operator onboarding a customer uses these, from the console, against a named
organization.

Every operation runs inside ``tenant_context(organization)``. That is not a
convenience: the device is written with that organization as its owner, the
connection test and the sync ingest into that organization only, and under
row-level security the database itself refuses anything else. The operator
never sees two tenants' devices in one response.

Each write is recorded twice: in the tenant's own audit log (the event is part
of the customer's history) and in the platform audit trail (the operator did
it).
"""
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from tenancy import platform_audit
from tenancy.context import tenant_context
from tenancy.models import PlatformAuditLog
from tenancy.views import PlatformConsoleMixin

from . import devices
from .device_views import annotated_devices
from .models import BiometricDevice, DeviceSyncLog
from .serializers import BiometricDeviceSerializer


def _record(request, organization, event, device, **extra):
    platform_audit.record(
        request.user, PlatformAuditLog.Action.SETTINGS_CHANGED,
        organization=organization,
        changes={"biometric_device": {"event": event, "device": device.name,
                                      "id": str(device.pk), **extra}},
        request=request)


class PlatformDeviceListView(PlatformConsoleMixin, APIView):
    """GET a tenant's devices; POST adds one to that tenant."""

    def get(self, request, slug):
        organization = self.organization(slug)
        with tenant_context(organization):
            rows = BiometricDeviceSerializer(annotated_devices(), many=True).data
        return Response(rows)

    def post(self, request, slug):
        organization = self.organization(slug)
        with tenant_context(organization):
            serializer = BiometricDeviceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            device = serializer.save(created_by=request.user)
            data = BiometricDeviceSerializer(annotated_devices().get(pk=device.pk)).data
        _record(request, organization, "created", device,
                host=device.host, port=device.port)
        return Response(data, status=status.HTTP_201_CREATED)


class PlatformDeviceDetailView(PlatformConsoleMixin, APIView):
    """PATCH a tenant's device; POST .../test-connection/ or .../sync/."""

    def _device(self, organization, device_id):
        device = BiometricDevice.objects.filter(pk=device_id).first()
        if device is None:
            raise Http404("No such device in this organization.")
        return device

    def patch(self, request, slug, device_id):
        organization = self.organization(slug)
        with tenant_context(organization):
            device = self._device(organization, device_id)
            serializer = BiometricDeviceSerializer(device, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            device = serializer.save()
            data = BiometricDeviceSerializer(annotated_devices().get(pk=device.pk)).data
        _record(request, organization, "updated", device,
                fields=sorted(k for k in request.data.keys() if k != "comm_key"))
        return Response(data)

    def post(self, request, slug, device_id, operation=None):
        organization = self.organization(slug)
        with tenant_context(organization):
            device = self._device(organization, device_id)
            if not device.host:
                raise ValidationError({"host": "This device has no IP address."})
            if operation == "test-connection":
                result = devices.test_connection(
                    host=device.host, port=device.port, comm_key=device.comm_key,
                    device_type=device.device_type, device=device, actor=request.user)
            elif operation == "sync":
                result = devices.run_pull_sync(
                    device, trigger=DeviceSyncLog.Trigger.MANUAL, actor=request.user)
                summary = result.pop("summary", None) or {}
                punches = dict(summary.get("punches") or {})
                punches.pop("device_user_ids", None)
                result["punches"] = punches or None
            else:                                   # pragma: no cover - url-constrained
                raise Http404()
            result["device"] = BiometricDeviceSerializer(
                annotated_devices().get(pk=device.pk)).data
        _record(request, organization, operation, device,
                outcome=result.get("status"))
        return Response(result)
