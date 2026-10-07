"""
HTTP surface for custody transfers, exit clearance and the asset custody view
(Phase ASSET-CUSTODY-TRANSFER).

Every rule lives in `transfers.py`, `clearance.py` and `roles.py`. The views only
translate: they parse input, call one function, and render what it returns - so
the same authority is enforced whether a transfer is decided through the API, a
management command or a test.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.nepali_dates import to_bs
from config.uploads import DOCUMENT_ATTACHMENT_EXTENSIONS, validate_attachment
from documents.protected_media import signed_media_url

from . import clearance, lifecycle, roles, transfers
from .ownership import ownership_timeline
from .models import (
    AssetRequest, AssetReturn, AssetTransfer, InventoryItem, ItemAssignment,
    MaintenanceTicket,
)

User = get_user_model()

MAX_TRANSFER_ATTACHMENT_SIZE = 10 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def _person(user, name, department=""):
    if user is None and not name:
        return None
    return {"id": str(user.id) if user else None, "name": name,
            "department": department}


class TransferWriteSerializer(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=InventoryItem.objects.all())
    to_employee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True)
    transfer_date = serializers.DateField()
    reason = serializers.ChoiceField(choices=AssetTransfer.Reason.choices)
    condition = serializers.ChoiceField(choices=AssetTransfer.Condition.choices)
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class TransferUpdateSerializer(serializers.Serializer):
    to_employee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True)
    transfer_date = serializers.DateField(required=False)
    reason = serializers.ChoiceField(choices=AssetTransfer.Reason.choices, required=False)
    condition = serializers.ChoiceField(
        choices=AssetTransfer.Condition.choices, required=False)
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class DecisionSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=4000)


def serialize_transfer(transfer, user, *, detail=False):
    stage = transfer.status
    data = {
        "id": str(transfer.id),
        "transfer_number": transfer.transfer_number,
        "item": {"id": str(transfer.item_id) if transfer.item_id else None,
                 "asset_code": transfer.item_code, "name": transfer.item_name},
        "from_employee": _person(transfer.from_employee, transfer.from_employee_name,
                                 transfer.from_department_name),
        "to_employee": _person(transfer.to_employee, transfer.to_employee_name,
                               transfer.to_department_name),
        "transfer_date": transfer.transfer_date,
        "transfer_date_bs": to_bs(transfer.transfer_date),
        "reason": transfer.reason, "reason_label": transfer.get_reason_display(),
        "condition": transfer.condition,
        "condition_label": transfer.get_condition_display(),
        "remarks": transfer.remarks,
        "status": stage, "status_label": transfer.get_status_display(),
        "requested_by": _person(transfer.requested_by, transfer.requested_by_name),
        "submitted_at": transfer.submitted_at,
        "approved_by": _person(transfer.approved_by, transfer.approved_by_name),
        "approved_at": transfer.approved_at,
        "completed_at": transfer.completed_at,
        "rejection": ({
            "by": transfer.rejected_by_name, "at": transfer.rejected_at,
            "stage": transfer.rejected_stage,
            "stage_label": transfer.get_rejected_stage_display_safe(),
            "remarks": transfer.rejection_remarks,
        } if transfer.status == AssetTransfer.Status.REJECTED else None),
        "created_at": transfer.created_at,
        "stages": transfers.stage_tracker(transfer),
        # What THIS user may do now. The client renders buttons from this and never
        # re-derives authority, so the server stays the only place a rule lives.
        "permissions": {
            "can_edit": stage == AssetTransfer.Status.DRAFT and (
                user.id == transfer.requested_by_id or roles.is_admin(user)),
            "can_submit": stage == AssetTransfer.Status.DRAFT and (
                user.id == transfer.requested_by_id or roles.is_admin(user)),
            "can_approve": (stage in AssetTransfer.REVIEW_STAGES
                            and transfers.can_act_at_stage(user, transfer)),
            "can_reject": (stage in AssetTransfer.REVIEW_STAGES
                           and transfers.can_act_at_stage(user, transfer)),
            "can_cancel": transfer.is_open and (
                user.id == transfer.requested_by_id or roles.is_admin(user)),
            "can_attach": transfer.is_open,
        },
    }
    if detail:
        # HR is the only gate now, so it is the only row a new transfer has. The
        # retired gates are listed ONLY when this record actually passed them -
        # an older transfer whose department head and administrator signed it must
        # still show both, by name. Listing them unconditionally would have drawn
        # two permanently empty rows on every transfer raised from here on.
        data["stage_approvals"] = [
            row for row in (
                ({"stage": "dept_head_review", "label": "Department Head",
                  "by": transfer.dept_head_name, "at": transfer.dept_head_at,
                  "remarks": transfer.dept_head_remarks}
                 if transfer.dept_head_at else None),
                {"stage": "hr_review", "label": "HR (final authority)",
                 "by": transfer.hr_name, "at": transfer.hr_at,
                 "remarks": transfer.hr_remarks},
                ({"stage": "admin_approval", "label": "Admin",
                  "by": transfer.approved_by_name, "at": transfer.approved_at,
                  "remarks": transfer.admin_remarks}
                 if transfer.admin_remarks or transfer.events.filter(
                     action="admin_approved").exists() else None),
            ) if row is not None
        ]
        data["attachments"] = [{
            "id": str(a.id), "name": a.original_name, "size": a.size,
            "uploaded_by": a.uploaded_by_name, "uploaded_at": a.uploaded_at,
            "url": signed_media_url(a.file.name, user=user),
        } for a in transfer.attachments.all()]
        data["timeline"] = transfers.timeline(transfer)
    return data


# --------------------------------------------------------------------------- #
# Transfers
# --------------------------------------------------------------------------- #
class AssetTransferViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           mixins.CreateModelMixin, mixins.UpdateModelMixin,
                           viewsets.GenericViewSet):
    """
    No destroy. A transfer is a governance record; one that should not proceed is
    cancelled or rejected, and stays on file saying so.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = (transfers.visible_to(self.request.user)
              .select_related("item", "from_employee", "to_employee", "requested_by",
                              "approved_by"))
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("item"):
            qs = qs.filter(item_id=params["item"])
        if params.get("employee"):
            qs = qs.filter(Q(from_employee_id=params["employee"])
                           | Q(to_employee_id=params["employee"]))
        if params.get("q"):
            term = params["q"].strip()
            qs = qs.filter(Q(transfer_number__icontains=term)
                           | Q(item_code__icontains=term) | Q(item_name__icontains=term)
                           | Q(from_employee_name__icontains=term)
                           | Q(to_employee_name__icontains=term))
        return qs.order_by("-created_at")

    def list(self, request, *args, **kwargs):
        rows = list(self.get_queryset())
        user = request.user
        if request.query_params.get("scope") == "awaiting_me":
            rows = [t for t in rows if t.status in AssetTransfer.REVIEW_STAGES
                    and transfers.can_act_at_stage(user, t)]
        return Response([serialize_transfer(t, user) for t in rows])

    def retrieve(self, request, *args, **kwargs):
        return Response(serialize_transfer(self.get_object(), request.user, detail=True))

    def create(self, request, *args, **kwargs):
        payload = TransferWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        transfer = transfers.create_transfer(
            item=data["item"], to_employee=data.get("to_employee"),
            requested_by=request.user, transfer_date=data["transfer_date"],
            reason=data["reason"], condition=data["condition"],
            remarks=data.get("remarks", ""))
        return Response(serialize_transfer(transfer, request.user, detail=True),
                        status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if not kwargs.get("partial"):
            raise MethodNotAllowed("PUT", detail="Use PATCH to edit a draft transfer.")
        transfer = self.get_object()
        payload = TransferUpdateSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        transfer = transfers.update_draft(transfer.id, request.user,
                                          **payload.validated_data)
        return Response(serialize_transfer(transfer, request.user, detail=True))

    def _remarks(self, request):
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return payload.validated_data.get("remarks", "")

    def _respond(self, transfer):
        return Response(serialize_transfer(transfer, self.request.user, detail=True))

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return self._respond(transfers.submit(self.get_object().id, request.user))

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._respond(transfers.approve(
            self.get_object().id, request.user, remarks=self._remarks(request)))

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._respond(transfers.reject(
            self.get_object().id, request.user, remarks=self._remarks(request)))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._respond(transfers.cancel(
            self.get_object().id, request.user, remarks=self._remarks(request)))

    @action(detail=True, methods=["post"], url_path="attachments")
    def attachments(self, request, pk=None):
        transfer = self.get_object()
        files = request.FILES.getlist("files") or request.FILES.getlist("file")
        if not files:
            return Response({"files": ["Choose at least one file to attach."]},
                            status=status.HTTP_400_BAD_REQUEST)
        for upload in files:
            validate_attachment(upload, max_size=MAX_TRANSFER_ATTACHMENT_SIZE,
                                extensions=DOCUMENT_ATTACHMENT_EXTENSIONS)
        for upload in files:
            transfers.add_attachment(transfer.id, request.user, upload)
        transfer.refresh_from_db()
        return Response(serialize_transfer(transfer, request.user, detail=True),
                        status=status.HTTP_201_CREATED)


class TransferOptionsView(APIView):
    """The vocabularies the transfer form needs, so the client never hardcodes them."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "reasons": [{"value": v, "label": l} for v, l in AssetTransfer.Reason.choices],
            "conditions": [{"value": v, "label": l}
                           for v, l in AssetTransfer.Condition.choices],
            "can_create": transfers.can_create(request.user),
        })


# --------------------------------------------------------------------------- #
# Exit clearance
# --------------------------------------------------------------------------- #
def _can_view_clearance(user, employee):
    if user.id == employee.id:
        return True
    if roles.is_admin(user) or roles.is_hr(user) or roles.is_inventory_officer(user):
        return True
    # The Board too, read-only (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).
    if getattr(user, "role", None) == "bod":
        return True
    # A Department Head sees every employee's clearance (Phase
    # ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD). The last phase gave heads
    # organisation-wide sight of assets, owners and transfers and missed this
    # endpoint, so a head could see that someone held a laptop and not whether
    # that person was cleared to leave. It is read-only either way: clearance is
    # a status derived from custody, and nothing here changes custody.
    if roles.is_department_head(user):
        return True
    return False


class ExitClearanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        employee = request.user if pk is None else get_object_or_404(User, pk=pk)
        if not _can_view_clearance(request.user, employee):
            raise PermissionDenied("You can see your own exit clearance only.")
        return Response(clearance.exit_clearance(employee))


# --------------------------------------------------------------------------- #
# The asset custody view (asset detail page)
# --------------------------------------------------------------------------- #
class AssetCustodyView(APIView):
    """
    Everything about who has held an asset and what has happened to it, in one
    response: current owner, department, custody status, and the transfer,
    ownership, maintenance and request histories, plus its documents.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        item = get_object_or_404(
            InventoryItem.objects.select_related("category", "department"), pk=pk)
        user = request.user
        holder = transfers.current_holder(item)
        if not (roles.can_view_all_assets(user)
                or (holder and holder.assigned_to_id == user.id)):
            raise PermissionDenied("You can see the custody record of your own assets only.")

        ownership = [{
            "employee": a.assigned_to_name, "assigned_by": a.assigned_by_name,
            "from_date": a.assigned_date, "assigned_at": a.assigned_at,
            "returned_at": a.returned_at, "is_active": a.is_active,
            "handover_condition": a.handover_condition,
            "return_condition": a.return_condition, "is_handover": a.is_handover,
            "note": a.note,
        } for a in ItemAssignment.objects.filter(item=item).order_by("assigned_at")]

        open_transfer = item.transfers.filter(
            status__in=AssetTransfer.OPEN_STATUSES).first()
        open_return = AssetReturn.objects.filter(
            item=item, status__in=AssetReturn.OPEN_STATUSES).first()
        if open_transfer:
            custody_status = "transfer_in_progress"
        elif open_return:
            custody_status = "return_in_progress"
        elif holder:
            custody_status = "held"
        else:
            custody_status = "unassigned"

        documents = []
        if item.document:
            documents.append({"kind": "asset_document",
                              "name": item.document_name or item.document.name,
                              "url": signed_media_url(item.document.name, user=user)})
        for transfer in item.transfers.all():
            for attachment in transfer.attachments.all():
                documents.append({
                    "kind": "transfer_attachment", "name": attachment.original_name,
                    "reference": transfer.transfer_number,
                    "url": signed_media_url(attachment.file.name, user=user)})

        return Response({
            "item": {"id": str(item.id), "asset_code": item.asset_code,
                     "name": item.name, "status": item.status,
                     "status_label": item.get_status_display(),
                     "condition": item.condition,
                     "category": getattr(item.category, "name", "") or ""},
            "current_owner": ({"id": str(holder.assigned_to_id)
                               if holder.assigned_to_id else None,
                               "name": holder.assigned_to_name,
                               "since": holder.assigned_date,
                               "is_active": getattr(holder.assigned_to, "is_active", None)}
                              if holder else None),
            "current_department": getattr(item.department, "name", "") or "",
            "custody_status": custody_status,
            "open_transfer": (serialize_transfer(open_transfer, user)
                              if open_transfer else None),
            "transfers": [serialize_transfer(t, user)
                          for t in item.transfers.order_by("-created_at")],
            "ownership_history": ownership,
            # The same rows, told as sentences - "Transferred to Raj Kumar".
            "owner_timeline": ownership_timeline(item),
            "movement_history": lifecycle.history(item),
            "maintenance_history": [{
                "reference": t.reference, "status": t.get_status_display(),
                "issue": t.issue, "reported_at": t.created_at,
            } for t in MaintenanceTicket.objects.filter(item=item).order_by("-created_at")],
            "request_history": [{
                "reference": r.reference, "status": r.get_status_display(),
                "requested_by": r.requested_by_name, "requested_at": r.created_at,
            } for r in AssetRequest.objects.filter(item=item).order_by("-created_at")],
            "documents": documents,
        })


# --------------------------------------------------------------------------- #
# Asset Visibility (Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD)
# --------------------------------------------------------------------------- #
class AssetVisibilityOptionsView(APIView):
    """
    The vocabularies the read-only Asset Visibility page filters by.

    Served rather than hard-coded because the register page already carries its
    own copy of the status list, and a third copy on a new page is the one that
    drifts: a status added to the model would be filterable on one screen and
    silently missing from the other. Departments come from the Department table,
    not from the assets, so a department that owns nothing yet is still a filter
    a head can choose - and see is empty.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from leaves.models import Department

        if not roles.can_browse_register(request.user):
            raise PermissionDenied(
                "Asset Visibility is for Department Heads, HR, administrators and the "
                "store.")
        item = InventoryItem
        return Response({
            "statuses": [{"value": v, "label": l} for v, l in item.Status.choices],
            "conditions": [{"value": v, "label": l} for v, l in item.Condition.choices],
            "asset_types": [{"value": v, "label": l} for v, l in item.AssetType.choices],
            "departments": [{"value": str(d.id), "label": d.name}
                            for d in Department.objects.order_by("name")],
            "owners": [{"value": str(u.id), "label": u.get_full_name() or u.username}
                       for u in User.objects.filter(is_active=True)
                       .order_by("first_name", "last_name")],
            # Read-only by construction: the page offers no write controls at all,
            # and says so, whatever the viewer's own rights.
            "read_only": True,
        })
