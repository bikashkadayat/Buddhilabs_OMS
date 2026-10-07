from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import log_action
from users.models import User

from . import roles, services, notifications as inv_notify, takeout_eligibility
from .lifecycle import record_event
from .models import AssetLifecycleEvent, InventoryCategory, InventoryItem, ItemAssignment, TakeOutRequest

Event = AssetLifecycleEvent.Event


def _timeline(item, event, actor, *, subject="", from_status="", remarks="", metadata=None):
    """
    Write the lifecycle event a direct store action used to skip (Phase
    ASSET-LIFECYCLE-DISPOSAL).

    These endpoints changed custody or status and wrote only a generic audit row,
    so an asset assigned, handed over or returned from the Assignment page never
    appeared in its own timeline or in the Asset Movement report - the chain of
    custody had holes exactly where the fastest path ran. The event is written
    HERE rather than inside services.assign_item/return_item because those are
    shared with the request, return and transfer workflows, which already record
    their own event and would otherwise record it twice.
    """
    item.refresh_from_db()
    record_event(item, event, actor, subject=subject, remarks=remarks,
                 from_status=from_status, to_status=item.status, metadata=metadata or {})
from .permissions import (
    CanChangeCustody, InventoryItemPermission, IsManager, _may_run_the_store,
    is_manager)
from users.roles import has_org_wide_read as _org_wide_read
from .serializers import (
    EligibleAssetSerializer, InventoryCategorySerializer, InventoryItemSerializer,
    TakeOutRequestSerializer)


def _clear_takeout_notices(req_id):
    """Mark managers' 'awaiting your review' notices for a take-out read once it is
    decided, so their bell reconciles with the (now-empty) pending queue."""
    from notifications.dispatcher import resolve_source_notifications
    resolve_source_notifications(f"takeout-{req_id}-submitted")


class ManagerEmployeeListView(APIView):
    """Active users an asset can be assigned to — with the name + department the
    assignment UI needs. Managers only (Admin / HR / Dept Head); the shared
    /users/ endpoint is intentionally left minimal, so this is purpose-built.

    Also anyone who reads the whole register - the Board included (Phase BOD) -
    because the visibility and exit-clearance pickers name people from it. A
    name and department grants nothing; assigning is CanChangeCustody's."""
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        return [IsAuthenticated()]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not (_may_run_the_store(request.user) or roles.can_browse_register(request.user)):
            raise PermissionDenied("You cannot list employees.")

    def get(self, request):
        out = []
        qs = User.objects.filter(is_active=True).select_related("department_ref").order_by(
            "first_name", "last_name", "username")
        for u in qs:
            dep = getattr(u, "department_ref", None)
            out.append({
                "id": str(u.id),
                "full_name": u.get_full_name() or u.username,
                "employee_id": u.employee_id or "",
                "role": u.role,
                "department_ref": str(dep.id) if dep else None,
                "department_name": dep.name if dep else (getattr(u, "department", None) or None),
            })
        return Response(out)


class InventoryCategoryViewSet(viewsets.ModelViewSet):
    queryset = InventoryCategory.objects.all()
    serializer_class = InventoryCategorySerializer
    permission_classes = [InventoryItemPermission]


class InventoryItemViewSet(viewsets.ModelViewSet):
    serializer_class = InventoryItemSerializer
    permission_classes = [InventoryItemPermission]

    def get_queryset(self):
        qs = InventoryItem.objects.select_related("category", "department").all()
        user = self.request.user
        # Phase 70: `_may_run_the_store`, not `is_manager` - an inventory officer
        # runs the register without being senior, and scoping them to their own
        # assets made the receipt for a handover they had just performed 404. The
        # permission class let them through and the queryset then hid the asset,
        # which is the harder half of that bug to find.
        # Phase ASSET-TRANSFER-GOVERNANCE: a Department Head sees EVERY asset in
        # every department. The scoping that used to sit here is gone, not
        # widened - a head asked to account for the organisation's assets cannot
        # do it from a view that hides most of them. They still cannot change any
        # of it; InventoryItemPermission answers that separately.
        if not roles.can_browse_register(user):
            # Everybody else sees ONLY items actively assigned to them.
            qs = qs.filter(assignments__assigned_to=user, assignments__is_active=True).distinct()
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("category"):
            qs = qs.filter(category_id=p["category"])
        if p.get("department"):
            qs = qs.filter(department_id=p["department"])
        # Phase ASSET-TRANSFER-GOVERNANCE: the brief's five filters. Department and
        # status already existed; these three are new.
        if p.get("asset_type"):
            qs = qs.filter(asset_type=p["asset_type"])
        if p.get("condition"):
            qs = qs.filter(condition=p["condition"])
        if p.get("owner"):
            # Against the ACTIVE custody row. "unassigned" is a first-class answer
            # rather than the absence of a filter: "nobody is accountable for this"
            # is precisely the question a head is asking, and it cannot be
            # expressed by picking a person.
            if p["owner"] == "unassigned":
                qs = qs.exclude(assignments__is_active=True)
            else:
                qs = qs.filter(assignments__assigned_to_id=p["owner"],
                               assignments__is_active=True)
        if p.get("search"):
            term = p["search"]
            from django.db.models import Q
            # Phase ASSET-TRANSFER-GOVERNANCE: the brief's selector searches by
            # owner and department as well as name and code. Matched against the
            # ACTIVE custody row - `assignments__assigned_to_name` without the
            # is_active filter would find an asset by whoever used to hold it,
            # which is exactly the wrong answer when you are choosing what to move.
            qs = qs.filter(
                Q(name__icontains=term)
                | Q(asset_code__icontains=term)
                | Q(serial_number__icontains=term)
                | Q(department__name__icontains=term)
                | Q(category__name__icontains=term)
                | Q(assignments__assigned_to_name__icontains=term,
                    assignments__is_active=True)
            ).distinct()
        return qs

    def perform_create(self, serializer):
        item = serializer.save(asset_code=services.generate_asset_code())
        log_action(self.request.user, AuditLog.Action.CREATE, instance=item,
                   changes={"event": "INVENTORY_ITEM_CREATED", "asset_code": item.asset_code},
                   request=self.request)
        _timeline(item, Event.CREATED, self.request.user, metadata={
            "purchase_date": str(item.purchase_date) if item.purchase_date else None,
            "purchase_cost": str(item.purchase_cost) if item.purchase_cost is not None else None,
            "vendor": item.vendor})

    def destroy(self, request, *args, **kwargs):
        """
        Assets are never physically deleted (Phase ASSET-LIFECYCLE-DISPOSAL).

        Deleting one kept its history rows (every FK is SET_NULL with a snapshot)
        but cut them loose from the asset, so the chain of custody could no longer
        be read by asset - which is the question an auditor asks. An asset leaves
        the books through an approved disposal and stays on file, marked disposed.
        """
        return Response(
            {"detail": "Assets are never deleted. To take one off the books, raise a "
                       "disposal request; it stays on file with its full history."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def perform_update(self, serializer):
        item = serializer.save()
        log_action(self.request.user, AuditLog.Action.UPDATE, instance=item, request=self.request)

    def _employee(self, request):
        emp_id = request.data.get("assigned_to")
        if not emp_id:
            return None, Response({"detail": "assigned_to (employee id) is required."},
                                  status=status.HTTP_400_BAD_REQUEST)
        try:
            return User.objects.get(id=emp_id), None
        except User.DoesNotExist:
            return None, Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

    def _parse_date(self, request, field):
        """Parse an optional YYYY-MM-DD field.

        Absent/blank -> None (the caller's documented default applies). Malformed ->
        ValidationError (400): silently substituting today would record a wrong
        business date that nobody can tell apart from a real one.
        """
        raw = request.data.get(field)
        if raw in (None, ""):
            return None
        from datetime import date
        try:
            y, m, d = (int(x) for x in str(raw).split("-"))
            return date(y, m, d)
        except (ValueError, TypeError):
            raise DRFValidationError({field: [f"Invalid date '{raw}'. Expected format YYYY-MM-DD."]})

    @action(detail=True, methods=["post"], permission_classes=[CanChangeCustody])
    def assign(self, request, pk=None):
        item = self.get_object()
        employee, err = self._employee(request)
        if err:
            return err
        before = item.status
        services.assign_item(
            item.id, employee, request.user,
            note=request.data.get("note", ""),
            assigned_date=self._parse_date(request, "assigned_date"),
            handover_condition=request.data.get("handover_condition", ""),
            accessories=request.data.get("accessories", ""))
        item.refresh_from_db()
        log_action(request.user, AuditLog.Action.UPDATE, instance=item,
                   changes={"event": "INVENTORY_ASSIGNED", "to": str(employee.id)}, request=request)
        _timeline(item, Event.ASSIGNED, request.user, subject=services._name(employee),
                  from_status=before, remarks=request.data.get("note", ""),
                  metadata={"path": "direct"})
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["post"], permission_classes=[CanChangeCustody])
    def handover(self, request, pk=None):
        item = self.get_object()
        employee, err = self._employee(request)
        if err:
            return err
        previous = ItemAssignment.objects.filter(item=item, is_active=True).first()
        before = item.status
        services.handover_item(
            item.id, employee, request.user,
            note=request.data.get("note", ""),
            assigned_date=self._parse_date(request, "assigned_date"),
            handover_condition=request.data.get("handover_condition", ""),
            accessories=request.data.get("accessories", ""))
        item.refresh_from_db()
        log_action(request.user, AuditLog.Action.UPDATE, instance=item,
                   changes={"event": "INVENTORY_HANDOVER", "to": str(employee.id)}, request=request)
        _timeline(item, Event.REASSIGNED, request.user, subject=services._name(employee),
                  from_status=before, remarks=request.data.get("note", ""),
                  metadata={"path": "direct", "approval": "none",
                            "from": previous.assigned_to_name if previous else ""})
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["post"], url_path="return", permission_classes=[CanChangeCustody])
    def return_item(self, request, pk=None):
        item = self.get_object()
        previous = ItemAssignment.objects.filter(item=item, is_active=True).first()
        before = item.status
        services.return_item(
            item.id, request.user,
            return_condition=request.data.get("return_condition", ""),
            return_remarks=request.data.get("return_remarks", ""))
        item.refresh_from_db()
        log_action(request.user, AuditLog.Action.UPDATE, instance=item,
                   changes={"event": "INVENTORY_RETURNED"}, request=request)
        _timeline(item, Event.RETURNED, request.user,
                  subject=previous.assigned_to_name if previous else "", from_status=before,
                  remarks=request.data.get("return_remarks", ""),
                  metadata={"path": "direct",
                            "condition": request.data.get("return_condition", "")})
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["get"], permission_classes=[IsManager])
    def assignments(self, request, pk=None):
        """Assignment history for an item (managers only; Dept Head dept-scoped via
        get_object)."""
        from .serializers import ItemAssignmentSerializer
        item = self.get_object()  # enforces manager scope (404 outside Dept Head's dept)
        qs = item.assignments.order_by("-assigned_at")
        return Response(ItemAssignmentSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="assignment-receipt")
    def assignment_receipt(self, request, pk=None):
        item = self.get_object()
        active = item.active_assignment
        if not active:
            return Response({"detail": "No active assignment to generate a receipt for."},
                            status=status.HTTP_409_CONFLICT)
        from .pdf import render_assignment_receipt
        pdf = render_assignment_receipt(item, active)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="handover-{item.asset_code}.pdf"'
        return resp


class AssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Cross-item assignment board ('who has what') + employee 'My Assigned Assets'.

    - list: managers only (Dept Head scoped to their own department); active
      assignments by default, filterable by employee / department / category / status.
    - mine: any authenticated user's own active assignments (read-only).
    """
    from .serializers import AssignmentBoardSerializer
    serializer_class = AssignmentBoardSerializer
    permission_classes = [IsManager]

    def get_permissions(self):
        if self.action == "mine":
            return [IsAuthenticated()]
        return [IsManager()]

    def _base_qs(self):
        from .models import ItemAssignment
        return ItemAssignment.objects.select_related(
            "item", "item__category", "assigned_to", "assigned_to__department_ref")

    def get_queryset(self):
        qs = self._base_qs()
        # Active-only unless explicitly asked for history.
        if self.request.query_params.get("all") != "1":
            qs = qs.filter(is_active=True)
        # Phase ASSET-TRANSFER-GOVERNANCE: no department scoping. A Department Head
        # sees who holds what across the whole organisation - the board is the
        # answer to "all owners", and a head who only saw their own department
        # could not give it. The filters below are how anyone narrows it back down,
        # by choice rather than by their own department.
        p = self.request.query_params
        if p.get("employee"):
            qs = qs.filter(assigned_to_id=p["employee"])
        if p.get("department"):
            qs = qs.filter(assigned_to__department_ref_id=p["department"])
        if p.get("category"):
            qs = qs.filter(item__category_id=p["category"])
        if p.get("status"):
            qs = qs.filter(item__status=p["status"])
        if p.get("search"):
            from django.db.models import Q
            s = p["search"]
            qs = qs.filter(Q(item__name__icontains=s) | Q(item__asset_code__icontains=s) | Q(assigned_to_name__icontains=s))
        return qs.order_by("assigned_to_name", "item__asset_code")

    @action(detail=False, methods=["get"])
    def mine(self, request):
        qs = self._base_qs().filter(is_active=True, assigned_to=request.user).order_by("item__asset_code")
        return Response(self.get_serializer(qs, many=True).data)


class TakeOutRequestViewSet(viewsets.ModelViewSet):
    serializer_class = TakeOutRequestSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]  # no PUT/PATCH/DELETE

    def get_queryset(self):
        user = self.request.user
        qs = TakeOutRequest.objects.select_related("item", "requested_by", "department").all()
        if _org_wide_read(user):
            pass  # HR / Admin, and the Board read-only: org-wide
        elif user.role == User.Roles.CHECKER:
            # Dept Head: own department's requests + own requests. With no department
            # this must fall back to own requests only — a None department_id would
            # otherwise match every unscoped request (same flaw as the item list).
            from django.db.models import Q
            own = Q(requested_by=user)
            qs = qs.filter(own if user.department_ref_id is None
                           else Q(department_id=user.department_ref_id) | own)
        else:
            qs = qs.filter(requested_by=user)  # Employee: own only
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("mine") == "1":
            qs = qs.filter(requested_by=user)
        return qs

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        item = ser.validated_data.get("item")
        # Phase 70.19-D. Before this, `create_takeout` checked only the item's own
        # state, so any authenticated user could raise a take-out for ANY asset by
        # posting its id -- including one assigned to a colleague, and including
        # stock they were never shown. The list endpoint hid those assets; nothing
        # stopped the POST. Enforced here against the SAME predicate the selector is
        # built from, so the two cannot disagree.
        try:
            takeout_eligibility.assert_may_request_takeout(request.user, item)
        except DjangoPermissionDenied as exc:
            # Phase 70.19-H: a refused attempt is the event worth keeping. Records
            # which control fired, so an ownership bypass attempt reads differently
            # in the trail from someone picking a retired asset.
            log_action(request.user, AuditLog.Action.OTHER,
                       instance=item,
                       changes={"event": "TAKEOUT_PERMISSION_REJECTED",
                                "denial": takeout_eligibility.denial_kind(request.user, item),
                                "item_code": getattr(item, "asset_code", None),
                                "detail": str(exc)},
                       request=request)
            raise PermissionDenied(str(exc))
        req = services.create_takeout(
            item=item, requester=request.user,
            purpose=ser.validated_data.get("purpose"),
            reason=ser.validated_data.get("reason"),
            expected_out_date=ser.validated_data.get("expected_out_date"),
            expected_return_date=ser.validated_data.get("expected_return_date"),
        )
        log_action(request.user, AuditLog.Action.SUBMIT, instance=req,
                   changes={"event": "TAKEOUT_REQUESTED", "reference": req.reference}, request=request)
        inv_notify.takeout_submitted(req)
        return Response(self.get_serializer(req).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsManager])
    def approve(self, request, pk=None):
        req = self.get_object()
        # Segregation of duties: a manager may not approve their own take-out — it
        # must go to another manager / HR / Admin. Mirrors the leaves module
        # ("You cannot approve your own leave", leaves/views.py).
        if req.requested_by_id == request.user.id:
            raise PermissionDenied("You cannot approve your own take-out request.")
        before = req.item.status if req.item_id else ""
        req = services.approve_takeout(req.id, request.user, remarks=request.data.get("remarks", ""))
        log_action(request.user, AuditLog.Action.APPROVE, instance=req,
                   changes={"event": "TAKEOUT_APPROVED", "reference": req.reference}, request=request)
        if req.item_id:
            _timeline(req.item, Event.TAKEN_OUT, request.user, subject=req.requested_by_name,
                      from_status=before, remarks=req.reason,
                      metadata={"reference": req.reference,
                                "expected_return": str(req.expected_return_date)})
        _clear_takeout_notices(req.id)  # reconcile approvers' bells with their queue
        inv_notify.takeout_finalized(req)
        return Response(self.get_serializer(req).data)

    @action(detail=True, methods=["post"], permission_classes=[IsManager])
    def reject(self, request, pk=None):
        req = self.get_object()
        req = services.reject_takeout(req.id, request.user, remarks=request.data.get("remarks", ""))
        log_action(request.user, AuditLog.Action.REJECT, instance=req,
                   changes={"event": "TAKEOUT_REJECTED", "reference": req.reference}, request=request)
        _clear_takeout_notices(req.id)
        inv_notify.takeout_finalized(req)
        return Response(self.get_serializer(req).data)

    @action(detail=True, methods=["post"], url_path="mark_returned", permission_classes=[IsManager])
    def mark_returned(self, request, pk=None):
        req = self.get_object()
        before = req.item.status if req.item_id else ""
        req = services.mark_returned(req.id, request.user)
        log_action(request.user, AuditLog.Action.UPDATE, instance=req,
                   changes={"event": "TAKEOUT_RETURNED", "reference": req.reference}, request=request)
        if req.item_id:
            _timeline(req.item, Event.BROUGHT_BACK, request.user, subject=req.requested_by_name,
                      from_status=before, metadata={"reference": req.reference})
        _clear_takeout_notices(req.id)
        return Response(self.get_serializer(req).data)

    @action(detail=True, methods=["get"], url_path="gate-pass")
    def gate_pass(self, request, pk=None):
        req = self.get_object()
        if req.status not in (TakeOutRequest.Status.APPROVED, TakeOutRequest.Status.RETURNED):
            return Response({"detail": "Gate pass is available only for approved requests."},
                            status=status.HTTP_409_CONFLICT)
        # Requester or any manager may download.
        if not (_may_run_the_store(request.user)
                or req.requested_by_id == request.user.id):
            return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
        from .pdf import render_gate_pass
        pdf = render_gate_pass(req)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="gate-pass-{req.reference}.pdf"'
        return resp


class TakeOutEligibleAssetsView(APIView):
    """
    Phase 70.19-A — GET /api/v1/inventory/takeout/eligible-assets/

    The assets the CALLER may raise a take-out for. A dedicated endpoint rather than
    a filter on the asset register, for two reasons that both matter:

      * `/inventory/items/` is gated by `InventoryItemPermission`, which denies the
        list action to everyone who does not run the store. The take-out form is for
        exactly the people it denies, so building the form on it produced a 403 that
        the client turned into an empty dropdown (Phase 70.18, cause A). Widening
        that permission to fix a dropdown would have exposed the whole register.

      * "What is in the register" and "what may I take home" are different
        questions. Answering the second with a filtered version of the first is what
        let the list and the create path disagree.

    `IsAuthenticated` is the whole permission check on purpose: every authenticated
    user may ASK, and `eligible_assets_for` decides what they get back — which for a
    plain employee holding nothing is an empty list plus a reason, not a 403. A
    permission error is the wrong answer to "show me my own assets".
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = takeout_eligibility.eligible_assets_for(request.user)

        search = (request.query_params.get("search") or "").strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=search)
                           | Q(asset_code__icontains=search)
                           | Q(serial_number__icontains=search)
                           | Q(category__name__icontains=search))

        assets = EligibleAssetSerializer(qs, many=True, context={"request": request}).data
        scope = takeout_eligibility.scope_for(request.user)

        # Phase 70.19-H: the form being opened is the first event in the take-out
        # trail, and the only record that somebody looked at what they could take.
        # Logged once per open rather than per keystroke -- a search re-query is the
        # same act of looking, and logging each one would bury the real events.
        if not search:
            log_action(request.user, AuditLog.Action.OTHER,
                       changes={"event": "TAKEOUT_FORM_OPENED",
                                "scope": scope,
                                "eligible_count": len(assets)},
                       request=request)

        return Response({
            "count": len(assets),
            "scope": scope,
            # The client renders this verbatim when the list is empty, so the reason
            # a dropdown is empty is decided on the server, where the rule lives.
            "empty_reason": self._empty_reason(scope) if not assets and not search else None,
            "results": assets,
        })

    @staticmethod
    def _empty_reason(scope):
        if scope == takeout_eligibility.SCOPE_ALL:
            return ("There are no take-out eligible assets in the register. Assets must be "
                    "in stock or assigned before they can be taken out.")
        if scope == takeout_eligibility.SCOPE_DEPARTMENT:
            return ("No take-out eligible assets in your department, and none assigned to "
                    "you. Contact your Inventory Officer.")
        if scope == takeout_eligibility.SCOPE_OWN_PLUS_STOCK:
            return ("No assets are in stock and none are assigned to you. Check assets into "
                    "stock before requesting a take-out.")
        return ("You currently have no take-out eligible assets. Assets must be assigned to "
                "you before you can request to take one out — contact your Inventory Officer.")
