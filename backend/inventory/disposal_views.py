"""
HTTP surface for disposals and the asset lifecycle view (Phase ASSET-LIFECYCLE-DISPOSAL).

Rules live in disposals.py, depreciation.py and lifecycle.py; these views parse,
call one function and render - exactly as transfer_views.py does.
"""
from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.uploads import DOCUMENT_ATTACHMENT_EXTENSIONS, validate_attachment
from documents.protected_media import signed_media_url

from . import disposals, lifecycle, roles
from .depreciation import depreciation
from .models import AssetDisposal, AssetLifecycleEvent, InventoryItem

MAX_DISPOSAL_ATTACHMENT_SIZE = 10 * 1024 * 1024
E = AssetLifecycleEvent.Event

# The six lifecycle phases, and which recorded events belong to each.
# Everything an asset can have happen to it lands in exactly one.
#
# ORDER: Purchased, Assigned, Transferred, Returned, Maintained, Disposed - the
# custody-timeline brief (Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD) puts
# Returned before Maintained, which is also the usual order of events: an asset
# comes back to the store and is then sent for repair. The previous phase had the
# two the other way round.
PHASES = [
    ("purchased", "Purchased", {E.CREATED, E.PROCUREMENT, E.RECEIVED, E.STOCKED}),
    ("assigned", "Assigned", {E.REQUESTED, E.REQUEST_APPROVED, E.ASSIGNED, E.HANDED_OVER,
                              E.ACCEPTED, E.REASSIGNED, E.TAKEN_OUT}),
    ("transferred", "Transferred", {E.TRANSFERRED, E.DEPARTMENT_CHANGED}),
    ("returned", "Returned", {E.RETURN_REQUESTED, E.RETURNED, E.BROUGHT_BACK}),
    ("maintained", "Maintained", {E.MAINTENANCE_REPORTED, E.MAINTENANCE_STARTED,
                                  E.MAINTENANCE_DONE, E.CONDITION_CHANGED}),
    ("disposed", "Disposed", {E.LOST, E.DISPOSED, E.ARCHIVED}),
]
PHASE_OF = {event: key for key, _, events in PHASES for event in events}


class DisposalWriteSerializer(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=InventoryItem.objects.all())
    disposal_type = serializers.ChoiceField(choices=AssetDisposal.DisposalType.choices)
    reason = serializers.CharField(max_length=4000)
    expected_proceeds = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0)
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class DecisionSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=4000)


def _money(value):
    return str(value) if value is not None else None


def serialize_disposal(d, user, *, detail=False):
    item_position = (depreciation(d.item) if d.item_id and d.status != AssetDisposal.Status.DISPOSED
                     else None)
    data = {
        "id": str(d.id), "disposal_number": d.disposal_number,
        "item": {"id": str(d.item_id) if d.item_id else None, "asset_code": d.item_code,
                 "name": d.item_name, "department": d.department_name},
        "disposal_type": d.disposal_type, "disposal_type_label": d.get_disposal_type_display(),
        "reason": d.reason, "remarks": d.remarks,
        "expected_proceeds": _money(d.expected_proceeds),
        "last_holder": d.last_holder_name,
        "status": d.status, "status_label": d.get_status_display(),
        "requested_by": d.requested_by_name, "submitted_at": d.submitted_at,
        "approved_by": d.approved_by_name, "approved_at": d.approved_at,
        "created_at": d.created_at, "disposed_at": d.disposed_at,
        # While in review: today's book value, so approvers see what is being
        # written off. Once disposed: the figures frozen at that moment.
        "book_value_now": _money(item_position["book_value"]) if item_position else None,
        "book_value_note": item_position["reason"] if item_position else "",
        "figures": {
            "purchase_cost": _money(d.purchase_cost_at_disposal),
            "accumulated": _money(d.accumulated_at_disposal),
            "book_value": _money(d.book_value_at_disposal),
            "proceeds": _money(d.proceeds),
            "written_off": _money(d.written_off),
            "gain": _money(d.gain_on_disposal),
        } if d.status == AssetDisposal.Status.DISPOSED else None,
        "rejection": ({"by": d.rejected_by_name, "at": d.rejected_at,
                       "stage_label": d.get_rejected_stage_display_safe(),
                       "remarks": d.rejection_remarks}
                      if d.status == AssetDisposal.Status.REJECTED else None),
        "stages": disposals.stage_tracker(d),
        "permissions": {
            "can_submit": d.status == AssetDisposal.Status.DRAFT and (
                user.id == d.requested_by_id or roles.is_admin(user)),
            "can_approve": (d.status in AssetDisposal.REVIEW_STAGES
                            and disposals.can_act_at_stage(user, d)),
            "can_reject": (d.status in AssetDisposal.REVIEW_STAGES
                           and disposals.can_act_at_stage(user, d)),
            "can_cancel": d.is_open and (user.id == d.requested_by_id or roles.is_admin(user)),
            "can_attach": True,
        },
    }
    if detail:
        data["stage_approvals"] = [
            {"label": "Department Head", "by": d.dept_head_name, "at": d.dept_head_at,
             "remarks": d.dept_head_remarks},
            {"label": "Admin", "by": d.approved_by_name, "at": d.approved_at,
             "remarks": d.admin_remarks},
        ]
        data["attachments"] = [{
            "id": str(a.id), "name": a.original_name, "size": a.size,
            "uploaded_by": a.uploaded_by_name, "uploaded_at": a.uploaded_at,
            "url": signed_media_url(a.file.name, user=user),
        } for a in d.attachments.all()]
        data["timeline"] = disposals.timeline(d)
    return data


class AssetDisposalViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           mixins.CreateModelMixin, viewsets.GenericViewSet):
    """No update, no destroy: a disposal request is a record, cancelled or rejected in place."""
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = disposals.visible_to(self.request.user).select_related(
            "item", "item__department", "item__category", "requested_by", "last_holder")
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("item"):
            qs = qs.filter(item_id=params["item"])
        if params.get("type"):
            qs = qs.filter(disposal_type=params["type"])
        return qs.order_by("-created_at")

    def list(self, request, *args, **kwargs):
        rows = list(self.get_queryset())
        if request.query_params.get("scope") == "awaiting_me":
            rows = [d for d in rows if d.status in AssetDisposal.REVIEW_STAGES
                    and disposals.can_act_at_stage(request.user, d)]
        return Response([serialize_disposal(d, request.user) for d in rows])

    def retrieve(self, request, *args, **kwargs):
        return Response(serialize_disposal(self.get_object(), request.user, detail=True))

    def create(self, request, *args, **kwargs):
        payload = DisposalWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        v = payload.validated_data
        disposal = disposals.create_disposal(
            item=v["item"], requested_by=request.user, disposal_type=v["disposal_type"],
            reason=v["reason"], expected_proceeds=v.get("expected_proceeds"),
            remarks=v.get("remarks", ""))
        return Response(serialize_disposal(disposal, request.user, detail=True),
                        status=status.HTTP_201_CREATED)

    def _remarks(self, request):
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return payload.validated_data.get("remarks", "")

    def _respond(self, disposal):
        return Response(serialize_disposal(disposal, self.request.user, detail=True))

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return self._respond(disposals.submit(self.get_object().id, request.user))

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._respond(disposals.approve(
            self.get_object().id, request.user, remarks=self._remarks(request)))

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._respond(disposals.reject(
            self.get_object().id, request.user, remarks=self._remarks(request)))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._respond(disposals.cancel(
            self.get_object().id, request.user, remarks=self._remarks(request)))

    @action(detail=True, methods=["post"], url_path="attachments")
    def attachments(self, request, pk=None):
        disposal = self.get_object()
        files = request.FILES.getlist("files") or request.FILES.getlist("file")
        if not files:
            return Response({"files": ["Choose at least one file to attach."]},
                            status=status.HTTP_400_BAD_REQUEST)
        for upload in files:
            validate_attachment(upload, max_size=MAX_DISPOSAL_ATTACHMENT_SIZE,
                                extensions=DOCUMENT_ATTACHMENT_EXTENSIONS)
        for upload in files:
            disposals.add_attachment(disposal.id, request.user, upload)
        return Response(serialize_disposal(disposal, request.user, detail=True),
                        status=status.HTTP_201_CREATED)


class DisposalOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "types": [{"value": v, "label": l,
                       "has_proceeds": v not in AssetDisposal.NO_PROCEEDS_TYPES}
                      for v, l in AssetDisposal.DisposalType.choices],
            "can_create": disposals.can_create(request.user),
        })


class AssetLifecycleSummaryView(APIView):
    """
    One asset's whole life: depreciation, warranty, AMC, end of life, and its
    history grouped into the six phases, with every underlying event kept.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        item = get_object_or_404(InventoryItem.objects.select_related("category"), pk=pk)
        user = request.user
        holds_it = item.assignments.filter(is_active=True, assigned_to=user).exists()
        if not (roles.can_view_all_assets(user) or holds_it):
            raise PermissionDenied("You can see the lifecycle of your own assets only.")

        events = lifecycle.history(item)
        phases = []
        for key, label, _ in PHASES:
            mine = [e for e in events if PHASE_OF.get(e["event"]) == key]
            phases.append({
                "key": key, "label": label, "count": len(mine),
                "first_at": mine[0]["at"] if mine else None,
                "last_at": mine[-1]["at"] if mine else None,
                "derived": False,
            })
        # Assets registered before creation was recorded have no purchase event.
        # Their purchase date is still a fact on the asset, so the milestone is
        # shown from it - and marked as derived, never passed off as an event.
        purchased = phases[0]
        if purchased["count"] == 0 and item.purchase_date:
            purchased.update(first_at=item.purchase_date, last_at=item.purchase_date,
                             derived=True)
        for event in events:
            event["phase"] = PHASE_OF.get(event["event"], "other")

        # The request in flight if there is one; otherwise the approved disposal
        # that took this asset off the books. Showing only the open one left a
        # disposed asset's page silent about how it left.
        open_disposal = item.disposals.filter(status__in=AssetDisposal.OPEN_STATUSES).first()
        disposal = open_disposal or item.disposals.filter(
            status=AssetDisposal.Status.DISPOSED).order_by("-disposed_at").first()
        return Response({
            "item": {"id": str(item.id), "asset_code": item.asset_code, "name": item.name,
                     "status": item.status, "status_label": item.get_status_display(),
                     "is_terminal": item.is_terminal},
            "depreciation": {k: (str(v) if hasattr(v, "quantize") else v)
                             for k, v in depreciation(item).items()},
            "warranty": {"start": item.warranty_start, "end": item.warranty_expiry,
                         "state": item.warranty_state},
            "amc": {"provider": item.amc_provider, "contract": item.amc_contract_number,
                    "start": item.amc_start, "end": item.amc_end,
                    "cost": _money(item.amc_cost), "state": item.amc_state},
            "end_of_life": {"date": item.end_of_life_date, "state": item.end_of_life_state},
            "phases": phases,
            "events": events,
            "disposal": (serialize_disposal(disposal, user) if disposal else None),
            "can_request_disposal": (disposals.can_create(user) and not item.is_terminal
                                     and open_disposal is None),
        })
