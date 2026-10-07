"""
API for the Phase 70 workflows, dashboard, reports and QR labels.

Kept beside `views.py` rather than inside it for the same reason the service and
serializer layers are split: the pre-existing viewsets are load-bearing for three
test files and a working UI, and burying six hundred new lines among them would
make the regression risk invisible.

Every state transition is delegated to `inventory.lifecycle`. Nothing here mutates
an asset directly, which is what keeps "the engine is the only thing that moves an
asset" true rather than aspirational.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status as http, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User

from . import lifecycle, reports as reporting, roles
from .lifecycle_serializers import (
    AssetRequestCreateSerializer, AssetRequestSerializer, AssetReturnCreateSerializer, AssetReturnSerializer,
    DecisionSerializer, DisposeSerializer, HandOverSerializer, InspectSerializer,
    MaintenanceAssignSerializer, MaintenanceCompleteSerializer,
    MaintenanceCreateSerializer, MaintenanceTicketSerializer, ReasonSerializer,
    RemarksSerializer, StockSerializer,
)
from .models import (
    AssetRequest, AssetReturn, InventoryCategory, InventoryItem,
    MaintenanceTicket,
)


class CanManageAssets(BasePermission):
    """Inventory officers and above. The store's own work."""
    message = "This action is restricted to inventory officers."

    def has_permission(self, request, view):
        return roles.can_manage_assets(request.user)


def _visible_requests(user):
    """
    Which asset requests a user may see.

    An employee sees their own. A supervisor additionally sees what is waiting on
    them. An inventory officer sees everything, because triaging the queue is the
    job. Deliberately NOT department-scoped for supervisors: the org chart here is
    `employee_type`, which does not say who reports to whom, so scoping by it would
    be a guess dressed as a rule.
    """
    queryset = AssetRequest.objects.select_related(
        "item", "requested_by", "department", "requested_category")
    if roles.can_manage_assets(user):
        return queryset
    if roles.is_supervisor(user):
        from django.db.models import Q
        return queryset.filter(
            Q(requested_by=user) | Q(status=AssetRequest.Status.PENDING)
            | Q(supervisor=user))
    return queryset.filter(requested_by=user)


class AssetRequestViewSet(viewsets.ModelViewSet):
    """Phase 70.5 - request, two approvals, handover, acceptance."""
    serializer_class = AssetRequestSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = _visible_requests(self.request.user)
        state = self.request.query_params.get("status")
        if state == "open":
            queryset = queryset.filter(status__in=AssetRequest.OPEN_STATUSES)
        elif state:
            queryset = queryset.filter(status=state)
        if self.request.query_params.get("mine") == "1":
            queryset = queryset.filter(requested_by=self.request.user)
        if self.request.query_params.get("awaiting") == "1":
            # What is waiting on ME, whichever gate I hold.
            if roles.can_manage_assets(self.request.user):
                queryset = queryset.filter(status__in=[
                    AssetRequest.Status.SUPERVISOR_APPROVED,
                    AssetRequest.Status.INVENTORY_APPROVED])
            elif roles.is_supervisor(self.request.user):
                queryset = queryset.filter(status=AssetRequest.Status.PENDING)
            else:
                queryset = queryset.filter(
                    requested_by=self.request.user,
                    status=AssetRequest.Status.HANDED_OVER)
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        payload = AssetRequestCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = None
        category = None
        if payload.validated_data.get("item"):
            item = get_object_or_404(
                InventoryItem, pk=payload.validated_data["item"])
        if payload.validated_data.get("requested_category"):
            category = get_object_or_404(
                InventoryCategory, pk=payload.validated_data["requested_category"])
        created = lifecycle.create_request(
            requester=request.user, purpose=payload.validated_data["purpose"],
            item=item, category=category,
            needed_by=payload.validated_data.get("needed_by"))
        return Response(self.get_serializer(created).data,
                        status=http.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="supervisor")
    def supervisor(self, request, pk=None):
        record = self.get_object()
        if not roles.can_approve_as_supervisor(request.user, record):
            raise PermissionDenied(
                "The first approval is a supervisor's, and never the requester's.")
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.supervisor_decision(
            record.id, request.user,
            approve=payload.validated_data["approve"],
            remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="inventory")
    def inventory(self, request, pk=None):
        record = self.get_object()
        if not roles.can_approve_as_inventory(request.user, record):
            raise PermissionDenied(
                "The second approval is an inventory officer's.")
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = None
        if payload.validated_data.get("item"):
            item = get_object_or_404(
                InventoryItem, pk=payload.validated_data["item"])
        lifecycle.inventory_decision(
            record.id, request.user,
            approve=payload.validated_data["approve"],
            remarks=payload.validated_data.get("remarks", ""),
            item=item,
            accessories=payload.validated_data.get("accessories", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="handover",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def handover(self, request, pk=None):
        record = self.get_object()
        payload = HandOverSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.hand_over(
            record.id, request.user,
            condition=payload.validated_data.get("condition", ""),
            accessories=payload.validated_data.get("accessories", ""),
            remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        """Only the requester. That restriction is the point of the step."""
        record = self.get_object()
        payload = RemarksSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.accept_asset(record.id, request.user,
                               remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        record = self.get_object()
        if not (record.requested_by_id == request.user.id
                or roles.can_manage_assets(request.user)):
            raise PermissionDenied("You cannot cancel this request.")
        payload = RemarksSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.cancel_request(record.id, request.user,
                                 remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)


class AssetReturnViewSet(viewsets.ModelViewSet):
    """Phase 70.6 - employee raises, officer verifies, inspects, accepts."""
    serializer_class = AssetReturnSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = AssetReturn.objects.select_related("item", "returned_by")
        if not roles.can_manage_assets(self.request.user):
            queryset = queryset.filter(returned_by=self.request.user)
        state = self.request.query_params.get("status")
        if state == "open":
            queryset = queryset.filter(status__in=AssetReturn.OPEN_STATUSES)
        elif state:
            queryset = queryset.filter(status=state)
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        payload = AssetReturnCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = get_object_or_404(InventoryItem, pk=payload.validated_data["item"])
        created = lifecycle.create_return(
            item=item, actor=request.user,
            reason=payload.validated_data.get("reason", ""),
            declared_condition=payload.validated_data.get("declared_condition", ""),
            declared_remarks=payload.validated_data.get("declared_remarks", ""))
        return Response(self.get_serializer(created).data,
                        status=http.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="verify",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def verify(self, request, pk=None):
        payload = RemarksSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.verify_return(self.get_object().id, request.user,
                                remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="inspect",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def inspect(self, request, pk=None):
        payload = InspectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.inspect_return(
            self.get_object().id, request.user,
            condition=payload.validated_data["condition"],
            remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="accept",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def accept(self, request, pk=None):
        payload = RemarksSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.accept_return(self.get_object().id, request.user,
                                remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="reject",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def reject(self, request, pk=None):
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.reject_return(self.get_object().id, request.user,
                                reason=payload.validated_data["reason"])
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["get"], url_path="form")
    def form(self, request, pk=None):
        """The Asset Return Form (Phase 70.16)."""
        from django.http import HttpResponse

        from .pdf import render_return_form

        record = self.get_object()
        response = HttpResponse(render_return_form(record),
                                content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{record.reference}-return.pdf"')
        response["X-Content-Type-Options"] = "nosniff"
        return response


class MaintenanceTicketViewSet(viewsets.ModelViewSet):
    """Phase 70.8 - reported, assigned, in maintenance, completed, returned."""
    serializer_class = MaintenanceTicketSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = MaintenanceTicket.objects.select_related("item", "reported_by")
        if not roles.can_manage_assets(self.request.user):
            # Anybody may REPORT a fault - the holder is the most likely to notice
            # one - so everybody can see their own tickets.
            queryset = queryset.filter(reported_by=self.request.user)
        state = self.request.query_params.get("status")
        if state == "open":
            queryset = queryset.filter(status__in=MaintenanceTicket.OPEN_STATUSES)
        elif state:
            queryset = queryset.filter(status=state)
        if self.request.query_params.get("item"):
            queryset = queryset.filter(item_id=self.request.query_params["item"])
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        payload = MaintenanceCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = get_object_or_404(InventoryItem, pk=payload.validated_data["item"])
        ticket = lifecycle.report_maintenance(
            item=item, actor=request.user,
            issue=payload.validated_data["issue"],
            priority=payload.validated_data.get("priority", "normal"))
        return Response(self.get_serializer(ticket).data,
                        status=http.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="assign",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def assign(self, request, pk=None):
        payload = MaintenanceAssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        technician = None
        if payload.validated_data.get("technician"):
            technician = get_object_or_404(
                User, pk=payload.validated_data["technician"])
        lifecycle.assign_maintenance(
            self.get_object().id, request.user, technician=technician,
            vendor=payload.validated_data.get("vendor", ""),
            remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="start",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def start(self, request, pk=None):
        payload = RemarksSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.start_maintenance(
            self.get_object().id, request.user,
            remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="complete",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def complete(self, request, pk=None):
        payload = MaintenanceCompleteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.complete_maintenance(
            self.get_object().id, request.user,
            resolution=payload.validated_data["resolution"],
            condition=payload.validated_data.get("condition", ""),
            cost=payload.validated_data.get("cost"))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="return-to-service",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def return_to_service(self, request, pk=None):
        payload = RemarksSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.return_to_service(
            self.get_object().id, request.user,
            remarks=payload.validated_data.get("remarks", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="cancel",
            permission_classes=[IsAuthenticated, CanManageAssets])
    def cancel(self, request, pk=None):
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lifecycle.cancel_maintenance(
            self.get_object().id, request.user,
            reason=payload.validated_data["reason"])
        return Response(self.get_serializer(self.get_object()).data)


class AssetLifecycleView(APIView):
    """
    Phase 70.2 front-of-lifecycle transitions, plus disposal, on one asset.

    Grouped rather than spread across the item viewset because they share a guard
    and a shape, and adding six more `@action`s to a viewset that already has seven
    would make the pre-existing ones harder to find.
    """
    permission_classes = [IsAuthenticated, CanManageAssets]

    def post(self, request, pk, verb):
        item = get_object_or_404(InventoryItem, pk=pk)
        if verb == "receive":
            payload = StockSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            lifecycle.mark_received(
                item.id, request.user,
                remarks=payload.validated_data.get("remarks", ""),
                condition=payload.validated_data.get("condition", ""))
        elif verb == "stock":
            payload = StockSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            lifecycle.mark_stocked(
                item.id, request.user,
                remarks=payload.validated_data.get("remarks", ""),
                location=payload.validated_data.get("location", ""))
        elif verb == "dispose":
            # Phase ASSET-LIFECYCLE-DISPOSAL. Disposal now needs a department head
            # AND an administrator. Leaving this one-click path open beside that
            # workflow would make the approvals decorative: anybody who could click
            # it could skip both. `lifecycle.dispose` still exists and is what the
            # approved request calls - it just is not reachable on its own any more.
            return Response(
                {"detail": "Disposal needs approval. Raise a disposal request for this "
                           "asset; it is disposed once a department head and an "
                           "administrator approve it."},
                status=http.HTTP_409_CONFLICT)
        elif verb == "archive":
            payload = RemarksSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            lifecycle.archive_asset(
                item.id, request.user,
                remarks=payload.validated_data.get("remarks", ""))
        else:
            return Response({"detail": "Unknown action."},
                            status=http.HTTP_400_BAD_REQUEST)

        item.refresh_from_db()
        from .serializers import InventoryItemSerializer
        return Response(InventoryItemSerializer(
            item, context={"request": request}).data)


class AssetHistoryView(APIView):
    """
    Phase 70.11 - the complete audit timeline for one asset.

    Readable by anyone who can see the asset, because the history is what makes a
    custody record trustworthy to the person holding it. The forensic audit log
    with IP addresses stays where it is, behind its own permission.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        item = get_object_or_404(InventoryItem, pk=pk)
        if not roles.can_view_all_assets(request.user):
            holds_it = item.assignments.filter(
                assigned_to=request.user, is_active=True).exists()
            held_it = item.assignments.filter(assigned_to=request.user).exists()
            if not (holds_it or held_it):
                raise PermissionDenied(
                    "You can see the history of assets you hold or have held.")
        # lifecycle.history() already returns the display shape - it is the same
        # dict the PDF and the timeline component read, built in one place so the
        # three cannot describe an asset differently.
        return Response(lifecycle.history(item))


class InventoryDashboardView(APIView):
    """Phase 70.9 and 70.14 - the stock picture, in aggregate queries."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not roles.can_view_all_assets(request.user):
            # An employee gets their own picture rather than a 403: the dashboard
            # is the module's front door, and a locked front door for most of the
            # organisation is a worse answer than a smaller room.
            return Response({
                "scope": "self",
                "roles": roles.role_summary(request.user),
                **reporting.employee_profile(request.user),
            })
        return Response({
            "scope": "organisation",
            "roles": roles.role_summary(request.user),
            "counts": reporting.dashboard(request.user),
            "low_stock": reporting.low_stock(),
        })


class InventoryReportView(APIView):
    """
    Phase 70.13 - the seven reports, one endpoint.

    One route with a name rather than seven routes, so adding an eighth report is a
    dict entry. Restricted to people who can see the whole register: every one of
    them is organisation-wide by definition.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        if not roles.can_view_all_assets(request.user):
            raise PermissionDenied(
                "Inventory reports cover the whole organisation and are "
                "restricted to inventory officers, supervisors and management.")
        entry = reporting.REPORTS.get(name)
        if entry is None:
            return Response(
                {"detail": "Unknown report.",
                 "available": sorted(reporting.REPORTS)},
                status=http.HTTP_404_NOT_FOUND)
        label, builder = entry
        rows = builder()
        # `download`, NOT `format`: DRF reserves `format` for content negotiation,
        # so `?format=pdf` makes it look for a renderer called "pdf" and 404 before
        # this view ever runs. Found by generating the report rather than by
        # reading the router.
        if request.query_params.get("download") == "pdf":
            from django.http import HttpResponse

            from .pdf import render_report

            pdf = render_report(
                name, label, rows,
                generated_for=request.user.get_full_name() or request.user.username)
            response = HttpResponse(pdf, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'attachment; filename="inventory-{name}.pdf"')
            response["X-Content-Type-Options"] = "nosniff"
            return response
        return Response({"report": name, "label": label, "rows": rows})


class EmployeeAssetProfileView(APIView):
    """Phase 70.12 - one person's assets, history, take-outs and returns."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        target = request.user
        if pk and str(pk) != str(request.user.id):
            if not roles.can_view_all_assets(request.user):
                raise PermissionDenied(
                    "You can only see your own asset profile.")
            target = get_object_or_404(User, pk=pk)
        return Response(reporting.employee_profile(target))


class AssetQRView(APIView):
    """
    Phase 70.10 - the QR label for an asset.

    The code encodes the asset's URL in this application, not a bare asset code:
    scanning it in any phone camera then OPENS the asset, which is the whole point.
    A bare code would need a separate scanner app to be useful, and the brief's
    "scan to view / assign / return" all start from the asset page.

    Reuses documents.pdf's QR helper - and its SITE_URL guard, so a label can never
    be printed pointing at localhost. That defect was found in Phase 49 and fixed
    once, in the shared place.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from documents.pdf import qr_data_uri, site_url_is_publishable
        from django.conf import settings

        item = get_object_or_404(InventoryItem, pk=pk)
        if not roles.can_view_all_assets(request.user):
            if not item.assignments.filter(
                    assigned_to=request.user, is_active=True).exists():
                raise PermissionDenied("You can only label assets you hold.")

        if not site_url_is_publishable():
            return Response({
                "asset_code": item.asset_code,
                "url": None, "qr": None,
                "warning": ("This deployment has no public site address "
                            "configured (SITE_URL), so a scannable label cannot "
                            "be produced. A QR pointing at localhost would be "
                            "printed onto an asset and be useless."),
            }, status=http.HTTP_409_CONFLICT)

        url = f"{settings.SITE_URL.rstrip('/')}/inventory/items/{item.id}"
        return Response({
            "asset_code": item.asset_code,
            "name": item.name,
            "url": url,
            "qr": qr_data_uri(url),
            "warning": None,
        })


class AssetScanView(APIView):
    """
    Phase 70.10 - resolve a scanned code to an asset.

    Accepts an asset code, a serial number or the asset's UUID, because what a
    scanner hands over depends on what was printed on the label - and a lookup that
    only accepted one of the three would fail in the field with no explanation.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        code = (request.query_params.get("code") or "").strip()
        if not code:
            return Response({"detail": "Pass ?code=..."},
                            status=http.HTTP_400_BAD_REQUEST)

        item = InventoryItem.objects.filter(asset_code__iexact=code).first()
        if item is None and code:
            # `uniq_inventory_serial_number` keeps new duplicates out, but rows
            # predating it can still collide. `.first()` silently resolved those to
            # an ARBITRARY asset — and the holder check below then ran against the
            # wrong one, which is worse than not resolving at all. Refuse instead.
            matches = list(InventoryItem.objects.filter(serial_number__iexact=code)[:2])
            if len(matches) > 1:
                return Response(
                    {"detail": f"More than one asset carries the serial '{code}'. "
                               f"Scan the asset code instead, and ask an "
                               f"administrator to correct the duplicate serials."},
                    status=http.HTTP_409_CONFLICT)
            item = matches[0] if matches else None
        if item is None:
            try:
                item = InventoryItem.objects.filter(pk=code).first()
            except (ValueError, TypeError, DjangoValidationError):
                item = None
        if item is None:
            return Response({"detail": f"No asset matches '{code}'."},
                            status=http.HTTP_404_NOT_FOUND)

        holder = item.assignments.filter(is_active=True).first()
        if not roles.can_view_all_assets(request.user):
            if holder is None or holder.assigned_to_id != request.user.id:
                raise PermissionDenied(
                    "You can only scan assets you hold.")

        from .serializers import InventoryItemSerializer
        return Response({
            "item": InventoryItemSerializer(
                item, context={"request": request}).data,
            "holder": holder.assigned_to_name if holder else None,
            # What the scanner may do next, from the server - so the phone screen
            # cannot offer an action the API would refuse.
            "can_assign": (roles.can_manage_assets(request.user)
                           and holder is None and item.is_in_service),
            "can_return": bool(holder and (
                roles.can_manage_assets(request.user)
                or holder.assigned_to_id == request.user.id)),
            "can_maintain": (roles.can_manage_assets(request.user)
                             and item.open_maintenance_ticket is None
                             and item.is_in_service),
        })
