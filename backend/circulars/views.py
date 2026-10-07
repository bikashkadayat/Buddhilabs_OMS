"""
The circular API.

Every sidebar menu is `?scope=<name>` resolved by `_apply_scope`, so a menu is a
route plus a scope name rather than another list implementation with its own
filtering bugs - the pattern both other document modules settled on.

Every state transition is delegated to circulars.workflow or circulars.broadcast.
Nothing here mutates a circular directly, which is what keeps "the engine is the
only thing that moves a circular" true rather than aspirational.
"""
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import broadcast as broadcast_engine
from . import permissions as perms
from . import services, workflow
from .filters import CircularFilter
from .models import (
    Circular, CircularAcknowledgement, CircularAttachment, CircularAuditLog,
    CircularWorkflowStep,
)
from .permissions import (
    CanDeleteCircular, CanMutateCircular, CanViewCircular, has_org_wide_read,
    visible_circular_filter,
)
from .serializers import (
    AcknowledgeSerializer, BroadcastSerializer, ChainInputSerializer,
    CircularActionSerializer, CircularAttachmentSerializer,
    CircularDetailSerializer, CircularListSerializer, CircularWriteSerializer,
    ImportSerializer, RemarksSerializer,
)

# Statuses where the circular has left the author but is not finished.
IN_FLIGHT_STATUSES = [
    Circular.Status.UNDER_REVIEW, Circular.Status.READY_FOR_ISSUE,
    Circular.Status.ISSUED, Circular.Status.READY_FOR_BROADCAST,
]


class CircularViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanViewCircular]
    filterset_class = CircularFilter
    search_fields = ["circular_number", "subject", "external_reference"]

    def get_queryset(self):
        user = self.request.user
        queryset = (
            Circular.objects
            .select_related("created_by", "department", "issued_by",
                            "memo_reference", "minute_reference")
            .prefetch_related(
                Prefetch("workflow_steps",
                         queryset=CircularWorkflowStep.objects.select_related(
                             "assignee").order_by("sequence")),
                "attachments", "broadcasts",
            )
            # Annotated, not counted per row: the list serializer's `reach` block
            # reads these, and without them a fifty-row page would issue a hundred
            # extra queries.
            .annotate(
                recipient_total=Count("recipients", distinct=True),
                read_total=Count("recipients__read_log", distinct=True),
            )
        )
        visible = visible_circular_filter(user)
        if visible is not None:
            queryset = queryset.filter(visible)

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(circular_number__icontains=search)
                | Q(subject__icontains=search)
                | Q(external_reference__icontains=search))
        # The explicit order_by is NOT redundant with Meta.ordering: the annotate()
        # above introduces a GROUP BY, and DRF warned that the resulting queryset
        # was unordered. An unordered paginated list is a real defect on an archive
        # people page through - a row can appear on page one and again on page two,
        # or never - so the ordering is stated here where the annotations are.
        return self._apply_scope(queryset).distinct().order_by("-created_at", "id")

    def _apply_scope(self, queryset):
        """
        The nine menus the brief names, each one Q object rather than a page.

        `assigned` and `my_acknowledgements` are the two that are about the caller
        rather than about a status, which is why they filter on a relation.
        """
        user = self.request.user
        scope = self.request.query_params.get("scope", "all")
        Status = Circular.Status
        active = CircularWorkflowStep.StepStatus.ACTIVE

        if scope == "drafts":
            return queryset.filter(created_by=user,
                                   status__in=[Status.DRAFT, Status.REJECTED])
        if scope == "assigned":
            # On my desk NOW, in either capacity.
            return queryset.filter(workflow_steps__assignee=user,
                                   workflow_steps__status=active)
        if scope == "under_review":
            return queryset.filter(status=Status.UNDER_REVIEW)
        if scope == "ready_for_issue":
            return queryset.filter(status=Status.READY_FOR_ISSUE)
        if scope == "ready_for_broadcast":
            # ISSUED and READY_FOR_BROADCAST together: both mean "official, not yet
            # sent", and splitting the menu would leave a circular briefly invisible
            # between two queues.
            return queryset.filter(status__in=[Status.ISSUED,
                                               Status.READY_FOR_BROADCAST])
        if scope == "broadcasted":
            return queryset.filter(status=Status.BROADCASTED)
        if scope == "my_acknowledgements":
            return queryset.filter(
                recipients__user=user,
                recipients__acknowledgement__state=(
                    CircularAcknowledgement.State.PENDING))
        if scope == "unread":
            return queryset.filter(
                recipients__user=user,
                recipients__read_log__isnull=True,
                status__in=[Status.BROADCASTED, Status.ARCHIVED])
        if scope == "archived":
            return queryset.filter(status=Status.ARCHIVED)
        if scope == "in_flight":
            return queryset.filter(created_by=user, status__in=IN_FLIGHT_STATUSES)
        return queryset

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return CircularWriteSerializer
        if self.action == "list":
            return CircularListSerializer
        return CircularDetailSerializer

    def get_permissions(self):
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), CanMutateCircular()]
        if self.action == "destroy":
            return [IsAuthenticated(), CanDeleteCircular()]
        return super().get_permissions()

    def _detail(self, circular, code=status.HTTP_200_OK):
        circular = self.get_queryset().get(pk=circular.pk)
        return Response(
            CircularDetailSerializer(
                circular, context=self.get_serializer_context()).data, status=code)

    # --- create / update / delete -----------------------------------------
    def create(self, request, *args, **kwargs):
        write = CircularWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        circular = write.save(created_by=request.user, status=Circular.Status.DRAFT)
        services.touch_department(circular)
        circular.save(update_fields=["department", "department_name"])
        workflow.record_creation(circular, request.user, request=request)
        return self._detail(circular, code=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        """
        Read the circular, and note that the caller opened it.

        Read tracking happens HERE rather than in a separate endpoint the client
        must remember to call, because a tracker the client can forget is a tracker
        that under-reports. It is a no-op for anyone who is not a recipient.
        """
        circular = self.get_object()
        broadcast_engine.record_read(circular, request.user)
        return Response(CircularDetailSerializer(
            circular, context=self.get_serializer_context()).data)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        circular = self.get_object()
        write = CircularWriteSerializer(circular, data=request.data, partial=partial)
        write.is_valid(raise_exception=True)
        changed = sorted(write.validated_data)
        circular = write.save()
        if changed:
            services.record_audit(
                circular, request.user, CircularAuditLog.Action.EDITED,
                remarks="Edited: " + ", ".join(changed),
                metadata={"fields": changed}, request=request)
        return self._detail(circular)

    def perform_destroy(self, instance):
        services.record_audit(
            instance, self.request.user, CircularAuditLog.Action.CANCELLED,
            remarks=f"Draft {instance.circular_number} deleted.",
            metadata={"circular_number": instance.circular_number},
            request=self.request)
        instance.delete()

    # --- the chain ---------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="chain")
    def chain(self, request, pk=None):
        circular = self.get_object()
        payload = ChainInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        rows = [{"assignee_id": row["assignee_id"], "role_type": row["role_type"]}
                for row in payload.validated_data["workflow"]]
        workflow.set_chain(circular, request.user, rows, request=request)
        return self._detail(circular)

    @action(detail=True, methods=["post"], url_path="send-for-review")
    def send_for_review(self, request, pk=None):
        circular = self.get_object()
        rows = None
        if request.data.get("workflow"):
            payload = ChainInputSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            rows = [{"assignee_id": r["assignee_id"], "role_type": r["role_type"]}
                    for r in payload.validated_data["workflow"]]
        workflow.send_for_review(circular, request.user, rows=rows, request=request)
        return self._detail(circular)

    @action(detail=True, methods=["post"], url_path="act")
    def act(self, request, pk=None):
        """
        Complete or return at the caller's step. One endpoint, because the active
        step's role already determines whether "proceed" means review or issue.
        """
        circular = self.get_object()
        payload = CircularActionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow.act_on_step(
            circular, request.user, decision=payload.validated_data["decision"],
            remarks=payload.validated_data.get("remarks", ""), request=request)
        return self._detail(circular)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        circular = self.get_object()
        payload = RemarksSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow.cancel(circular, request.user,
                        remarks=payload.validated_data.get("remarks", ""),
                        request=request)
        return self._detail(circular)

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        circular = self.get_object()
        workflow.archive(circular, request.user, request=request)
        return self._detail(circular)

    # --- content import ----------------------------------------------------
    @action(detail=True, methods=["post"], url_path="import")
    def import_content(self, request, pk=None):
        """Pull content from a memo or a minute. Editable afterwards; not a live link."""
        from memos.models import Memo
        from memos.permissions import CanViewMemo
        from minutes.models import Minute
        from minutes.permissions import can_read as can_read_minute

        circular = self.get_object()
        if not perms.can_edit(request.user, circular):
            raise PermissionDenied("This circular's content can no longer be replaced.")
        payload = ImportSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        memo = minute = None
        memo_id = payload.validated_data.get("memo_id")
        minute_id = payload.validated_data.get("minute_id")
        if memo_id:
            memo = Memo.objects.filter(pk=memo_id).first()
            # Importing is a READ of the source. Anyone who could not open the memo
            # must not be able to copy its body out through this endpoint.
            if memo is None or not CanViewMemo().has_object_permission(
                    request, self, memo):
                raise PermissionDenied("You cannot read that memo.")
        if minute_id:
            minute = Minute.objects.filter(pk=minute_id).first()
            if minute is None or not can_read_minute(request.user, minute):
                raise PermissionDenied("You cannot read that minute.")

        services.import_source(circular, request.user, memo=memo, minute=minute,
                               request=request)
        return self._detail(circular)

    # --- broadcast ---------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="broadcast")
    def broadcast(self, request, pk=None):
        circular = self.get_object()
        payload = BroadcastSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        broadcast_engine.broadcast(
            circular, request.user,
            audience=payload.validated_data["audience"],
            department_ids=payload.validated_data.get("department_ids"),
            group_ids=payload.validated_data.get("group_ids"),
            employee_ids=payload.validated_data.get("employee_ids"),
            include_children=payload.validated_data.get("include_children", True),
            remarks=payload.validated_data.get("remarks", ""), request=request)
        return self._detail(circular)

    @action(detail=True, methods=["post"], url_path="audience-preview")
    def audience_preview(self, request, pk=None):
        """
        How many people an audience would reach, WITHOUT sending.

        Broadcasting is irreversible - people are told - so the count is offered
        before the button rather than reported after it.
        """
        circular = self.get_object()
        if not broadcast_engine.can_broadcast(request.user, circular):
            raise PermissionDenied("You cannot broadcast this circular.")
        payload = BroadcastSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        people = broadcast_engine.resolve_audience(
            audience=payload.validated_data["audience"],
            department_ids=payload.validated_data.get("department_ids"),
            group_ids=payload.validated_data.get("group_ids"),
            employee_ids=payload.validated_data.get("employee_ids"),
            include_children=payload.validated_data.get("include_children", True),
            exclude_user=circular.issued_by, category=circular.category)
        already = set(circular.recipients.values_list("user_id", flat=True))
        total = people.count()
        overlap = people.filter(pk__in=already).count() if already else 0
        return Response({"total": total, "new": total - overlap,
                         "already_present": overlap})

    # --- registers ---------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="recipients")
    def recipients(self, request, pk=None):
        """
        Who received it and who has read it.

        Guarded by can_view_registers, which is NARROWER than reading the circular:
        a recipient is entitled to the announcement, not to a list of which
        colleagues have not opened it yet.
        """
        circular = self.get_object()
        if not perms.can_view_registers(request.user, circular):
            raise PermissionDenied(
                "The distribution list is available to the author, the issuer, the "
                "chain and HR/Admin.")
        return Response({
            "summary": broadcast_engine.read_summary(circular),
            "recipients": broadcast_engine.read_register(circular),
        })

    @action(detail=True, methods=["get"], url_path="acknowledgements")
    def acknowledgements(self, request, pk=None):
        circular = self.get_object()
        if not perms.can_view_registers(request.user, circular):
            raise PermissionDenied(
                "The acknowledgement register is available to the author, the "
                "issuer, the chain and HR/Admin.")
        return Response({
            "summary": broadcast_engine.acknowledgement_summary(circular),
            "recipients": broadcast_engine.acknowledgement_register(circular),
        })

    @action(detail=True, methods=["post"], url_path="acknowledge")
    def acknowledge(self, request, pk=None):
        circular = self.get_object()
        payload = AcknowledgeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        broadcast_engine.acknowledge(
            circular, request.user, accept=payload.validated_data["accept"],
            remarks=payload.validated_data.get("remarks", ""), request=request)
        return self._detail(circular)

    @action(detail=True, methods=["post"], url_path="remind")
    def remind(self, request, pk=None):
        circular = self.get_object()
        count = broadcast_engine.remind_acknowledgements(
            circular, request.user, request=request)
        return Response({"reminded": count,
                         "acknowledgement": broadcast_engine.acknowledgement_summary(
                             circular)})

    # --- attachments -------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="attachments",
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def attachments(self, request, pk=None):
        from config.uploads import validate_attachment
        from circulars.services import CIRCULAR_ATTACHMENT_EXTENSIONS

        circular = self.get_object()
        if request.method == "GET":
            return Response(CircularAttachmentSerializer(
                circular.attachments.all(), many=True,
                context=self.get_serializer_context()).data)

        if not perms.can_edit(request.user, circular):
            raise PermissionDenied(
                "Attachments are part of the circular and can only be added while "
                "it is a draft.")
        uploaded = request.FILES.getlist("files") or (
            [request.FILES["file"]] if "file" in request.FILES else [])
        if not uploaded:
            return Response({"detail": "No file was supplied."},
                            status=status.HTTP_400_BAD_REQUEST)

        created = []
        for item in uploaded:
            validate_attachment(item, extensions=CIRCULAR_ATTACHMENT_EXTENSIONS)
            created.append(CircularAttachment.objects.create(
                circular=circular, file=item, original_name=item.name,
                size=getattr(item, "size", 0) or 0, uploaded_by=request.user))
        services.record_audit(
            circular, request.user, CircularAuditLog.Action.ATTACHED,
            remarks=f"{len(created)} file(s) attached.",
            metadata={"count": len(created)}, request=request)
        return Response(
            CircularAttachmentSerializer(
                created, many=True, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"],
            url_path=r"attachments/(?P<attachment_id>[^/.]+)")
    def attachment_detail(self, request, pk=None, attachment_id=None):
        circular = self.get_object()
        if not perms.can_edit(request.user, circular):
            raise PermissionDenied(
                "This circular is issued; its attachments are part of the record.")
        attachment = circular.attachments.filter(pk=attachment_id).first()
        if attachment is None:
            return Response({"detail": "Attachment not found."},
                            status=status.HTTP_404_NOT_FOUND)
        name = attachment.original_name
        attachment.delete()
        services.record_audit(
            circular, request.user, CircularAuditLog.Action.ATTACHMENT_REMOVED,
            remarks=f"Attachment removed: {name}", request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # --- audit -------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="audit-trail")
    def audit_trail(self, request, pk=None):
        """
        The full trail with client addresses. HR, Admin and the author.

        Narrower than the timeline everyone can see, because this adds the IP and
        user agent, which is forensic data rather than something a colleague needs.
        """
        from django.contrib.contenttypes.models import ContentType

        from audit.models import AuditLog

        circular = self.get_object()
        if not perms.can_view_audit(request.user, circular):
            raise PermissionDenied(
                "The full audit trail is restricted to HR, administrators and the "
                "circular's author.")
        entries = (AuditLog.objects
                   .filter(content_type=ContentType.objects.get_for_model(Circular),
                           object_id=str(circular.id))
                   .select_related("actor").order_by("created_at"))
        return Response([{
            "id": str(entry.id),
            "action": entry.get_action_display(),
            "transition": entry.changes.get("transition", ""),
            "actor": (entry.actor.get_full_name() or entry.actor.username)
                     if entry.actor else "System",
            "at": entry.created_at,
            "ip_address": entry.ip_address,
            "user_agent": entry.user_agent,
            "remarks": entry.changes.get("remarks", ""),
            "metadata": entry.changes,
        } for entry in entries])

    # --- dashboard ---------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        """
        The nine counts the brief names.

        COUNT queries over the caller's visible set, never row counts from a
        paginated response - the mistake that made an earlier module's dashboard
        under-report every total that spanned more than one page.
        """
        user = request.user
        base = Circular.objects.all()
        visible = visible_circular_filter(user)
        if visible is not None:
            base = base.filter(visible)
        base = base.distinct()
        Status = Circular.Status

        def count(**filters):
            return base.filter(**filters).count()

        # The three that are about ME rather than about a status. Not filtered
        # through `base`: being a recipient IS the grant of visibility, so filtering
        # these by visibility could hide a task from the person who has to do it.
        mine = CircularAcknowledgement.objects.filter(recipient__user=user)
        unread = Circular.objects.filter(
            recipients__user=user, recipients__read_log__isnull=True,
            status__in=[Status.BROADCASTED, Status.ARCHIVED]).distinct().count()

        assigned = base.filter(
            workflow_steps__assignee=user,
            workflow_steps__status=CircularWorkflowStep.StepStatus.ACTIVE
        ).distinct().count()

        return Response({
            "drafts": count(created_by=user,
                            status__in=[Status.DRAFT, Status.REJECTED]),
            "under_review": count(status=Status.UNDER_REVIEW),
            "ready_for_issue": count(status=Status.READY_FOR_ISSUE),
            "issued": count(status=Status.ISSUED),
            "ready_for_broadcast": base.filter(
                status__in=[Status.ISSUED, Status.READY_FOR_BROADCAST]).count(),
            "broadcasted": count(status=Status.BROADCASTED),
            "archived": count(status=Status.ARCHIVED),
            "assigned": assigned,
            "unread": unread,
            "pending_acknowledgement": mine.filter(
                state=CircularAcknowledgement.State.PENDING).count(),
            "acknowledged": mine.filter(
                state=CircularAcknowledgement.State.ACKNOWLEDGED).count(),
            "total_visible": base.count(),
        })

    @action(detail=False, methods=["get"], url_path="taxonomy")
    def taxonomy(self, request):
        """
        Every dropdown the client renders, from the server's own choice lists.

        Including `statuses`, which the minute module's audit found missing - its
        filter fell back to a hardcoded list that omitted one status entirely, so
        those records could not be filtered for at all.
        """
        return Response({
            "categories": [{"code": c, "label": lbl}
                           for c, lbl in Circular.Category.choices],
            "classifications": [{"code": c, "label": lbl}
                                for c, lbl in Circular.Classification.choices],
            "priorities": [{"code": c, "label": lbl}
                           for c, lbl in Circular.Priority.choices],
            "statuses": [{"code": c, "label": lbl}
                         for c, lbl in Circular.Status.choices],
            "role_types": [{"code": c, "label": lbl}
                           for c, lbl in CircularWorkflowStep.RoleType.choices],
            "audiences": [{"code": c, "label": lbl}
                          for c, lbl in
                          broadcast_engine.CircularBroadcast.Audience.choices],
        })

    @action(detail=False, methods=["get"], url_path="audiences")
    def audiences(self, request):
        """Departments and groups a broadcaster can choose from."""
        from django.contrib.auth.models import Group

        from leaves.models import Department

        return Response({
            "departments": [
                {"id": str(d.id), "name": d.name, "code": d.code,
                 "parent": str(d.parent_id) if d.parent_id else None}
                for d in Department.objects.filter(is_active=True).order_by("name")],
            "groups": [{"id": g.id, "name": g.name}
                       for g in Group.objects.order_by("name")],
        })

    # --- export ------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        from documents.pdf import common_context, render_pdf

        circular = self.get_object()
        if not perms.can_export(request.user, circular):
            raise PermissionDenied("You cannot export this circular.")

        context = common_context(circular.circular_number)
        context.update(self._pdf_context(request, circular))
        pdf_bytes = render_pdf("pdf/circular.html", context)
        services.record_audit(circular, request.user,
                              CircularAuditLog.Action.EXPORTED,
                              remarks="PDF exported.", request=request)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{circular.circular_number}.pdf"')
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def _pdf_context(self, request, circular):
        """
        What the circular PDF prints.

        The registers are included only for a reader entitled to them. A recipient
        exporting their own copy gets the circular and its summary figures, not a
        list naming which colleagues have not read it - the same boundary the
        `recipients` endpoint draws, applied to paper.
        """
        from .sanitizers import sanitize_circular_html

        privileged = perms.can_view_registers(request.user, circular)
        return {
            "circular": circular,
            "content_html": sanitize_circular_html(circular.content),
            "department_label": circular.resolved_department_name() or "—",
            "category_label": circular.get_category_display(),
            "classification": circular.classification,
            "classification_label": circular.get_classification_display(),
            "priority_label": circular.get_priority_display(),
            "status_label": circular.get_status_display(),
            "issued_by_name": circular.issued_by_name or "—",
            "author_name": services.describe(circular.created_by),
            "author_designation": getattr(circular.created_by, "designation", "") or "",
            "chain": [{
                "sequence": step.sequence,
                "role_label": step.get_role_type_display(),
                "name": step.display_name,
                "designation": step.designation,
                "department": step.department_label,
                "acted_at": step.acted_at,
                "status_label": step.get_status_display(),
                "completed": step.status == CircularWorkflowStep.StepStatus.COMPLETED,
                "remarks": step.remarks,
            } for step in circular.workflow_steps.order_by("sequence")],
            "attachments": [{
                "name": row.original_name or row.file.name.rsplit("/", 1)[-1],
                "uploaded_by": services.describe(row.uploaded_by),
                "uploaded_at": row.uploaded_at,
            } for row in circular.attachments.all()],
            "broadcasts": broadcast_engine.broadcast_history(circular),
            "read_summary": broadcast_engine.read_summary(circular),
            "acknowledgement": broadcast_engine.acknowledgement_summary(circular),
            # Named registers only for a privileged reader; the summaries above are
            # printed either way.
            "acknowledgement_register": (
                broadcast_engine.acknowledgement_register(circular)
                if privileged and circular.acknowledgement_required else []),
            "show_registers": privileged,
            "generated_for": services.describe(request.user),
            "printed_at": timezone.now(),
        }
