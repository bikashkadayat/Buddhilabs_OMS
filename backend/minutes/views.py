"""
The minute API.

Every list menu is `?scope=<name>` resolved by `_apply_scope`. A menu is therefore a
route plus a scope name, not a new list implementation with its own filtering bugs, and
the definition of "assigned to me by involvement" lives in exactly one place.

The scopes mirror the manual's own menus (p.3, p.7): Draft Minute, Draft for Review
Minute, Assigned Minute (Initiated), Assigned Minute (Involvement), Underprocess
Minute, Archived Minutes - plus the two consolidated queues this HRMS's sidebar uses,
`mine` and `needs_me`.

OTP is deliberately absent from `acknowledge`. See minutes/workflow.py.
"""
import logging

from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import permissions as perms
from . import workflow
from .filters import MinuteFilterSet
from .models import Minute, MinuteInvolvement, MinuteParticipant, MinuteType
from .serializers import (
    AcknowledgementSerializer, InvolvementListWriteSerializer,
    MinuteAttachmentSerializer, MinuteAuditLogSerializer, MinuteDetailSerializer,
    MinuteInvolvementSerializer, MinuteListSerializer, MinuteParticipantSerializer,
    MinuteTypeSerializer, MinuteWriteSerializer, ParticipantListWriteSerializer,
    ReviewReturnSerializer,
)
from .services import generate_minute_number, record_audit, touch_department
from tenancy.stamping import stamp_all

logger = logging.getLogger("minutes")


def _flatten(detail):
    """
    A DRF error detail rendered as one readable sentence.

    `detail` may be a string, a list, or a dict of lists depending on where the
    ValidationError came from; str() on any of the latter two produces a Python repr.
    """
    if isinstance(detail, dict):
        return " ".join(_flatten(value) for value in detail.values())
    if isinstance(detail, (list, tuple)):
        return " ".join(_flatten(value) for value in detail)
    return str(detail)


class MinuteViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, perms.CanViewMinute]
    filterset_class = MinuteFilterSet
    search_fields = ["minute_number", "subject", "reference_number"]
    ordering_fields = ["created_at", "meeting_date", "minute_number", "status",
                       "archived_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = (
            Minute.objects
            .select_related("minute_type", "created_by", "fro", "department")
            .prefetch_related(
                Prefetch("participants",
                         queryset=MinuteParticipant.objects.select_related("user")),
                "involvements__user",
                "attachments",
                "audit_entries__actor",
            )
        )
        visible = perms.visible_minute_filter(self.request.user)
        if visible is not None:
            queryset = queryset.filter(visible)
        return self._apply_scope(queryset).distinct()

    def _apply_scope(self, queryset):
        user = self.request.user
        scope = self.request.query_params.get("scope", "all")
        Ack = MinuteParticipant.AckStatus
        present = MinuteParticipant.Attendance.PRESENT

        if scope == "drafts":
            return queryset.filter(created_by=user, status=Minute.Status.DRAFT)
        if scope == "draft_for_review":
            # The manual's "Draft for Review Minute" menu (p.7): what is with a
            # reviewer. Scoped to the FRO, because that is who it was sent to.
            return queryset.filter(status=Minute.Status.DRAFT_FOR_REVIEW).filter(
                Q(fro=user) | Q(created_by=user))
        if scope == "initiated":
            return queryset.filter(created_by=user).exclude(
                status=Minute.Status.ARCHIVED)
        if scope == "involvement":
            # Routed to me without my having raised it. Never overlaps with Initiated.
            return queryset.filter(
                Q(fro=user) | Q(involvements__user=user) | Q(participants__user=user)
            ).exclude(created_by=user)
        if scope == "under_process":
            return queryset.filter(status__in=[
                Minute.Status.DRAFT_FOR_REVIEW,
                Minute.Status.PENDING_ACKNOWLEDGEMENT])
        if scope == "my_acknowledgements":
            return queryset.filter(
                participants__user=user, participants__attendance=present,
                participants__ack_status=Ack.PENDING)
        if scope == "acknowledged":
            return queryset.filter(
                participants__user=user,
                participants__ack_status=Ack.ACKNOWLEDGED)
        if scope == "archived":
            return queryset.filter(status=Minute.Status.ARCHIVED)
        if scope == "mine":
            # "My Minutes" - everything I raised, at ANY status.
            return queryset.filter(created_by=user)
        if scope == "needs_me":
            # The single queue behind "Needs My Action". Two things can want something
            # from a person under this workflow, and these are the only two: a draft
            # sitting with me as FRO, and an acknowledgement I have not given.
            return queryset.filter(
                Q(fro=user, status=Minute.Status.DRAFT_FOR_REVIEW)
                | Q(participants__user=user,
                    participants__attendance=present,
                    participants__ack_status=Ack.PENDING)
            )
        return queryset

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return MinuteWriteSerializer
        if self.action == "list":
            return MinuteListSerializer
        return MinuteDetailSerializer

    def get_permissions(self):
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), perms.CanMutateMinute()]
        if self.action == "destroy":
            return [IsAuthenticated(), perms.CanDeleteMinute()]
        return super().get_permissions()

    # --- create / update / delete ---
    def perform_create(self, serializer):
        minute = serializer.save(
            created_by=self.request.user,
            minute_number=generate_minute_number(),
        )
        touch_department(minute)
        minute.save(update_fields=["department", "department_name"])
        workflow.record_creation(minute, self.request.user, request=self.request)

    def create(self, request, *args, **kwargs):
        write = self.get_serializer(data=request.data)
        write.is_valid(raise_exception=True)
        self.perform_create(write)
        detail = MinuteDetailSerializer(
            self.get_queryset().get(pk=write.instance.pk),
            context=self.get_serializer_context())
        return Response(detail.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        write = MinuteWriteSerializer(instance, data=request.data, partial=partial,
                                      context=self.get_serializer_context())
        write.is_valid(raise_exception=True)
        minute = write.save()
        record_audit(minute, request.user, "updated",
                     remarks="Minute content edited.", request=request)
        detail = MinuteDetailSerializer(
            self.get_queryset().get(pk=minute.pk),
            context=self.get_serializer_context())
        return Response(detail.data)

    def perform_destroy(self, instance):
        record_audit(instance, self.request.user, "deleted",
                     remarks=f"Minute {instance.minute_number} deleted by its author.",
                     request=self.request)
        instance.delete()

    # ------------------------------------------------------------------
    # Members Present / Absent / Invitee (manual p.4)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="participants")
    def participants(self, request, pk=None):
        minute = self.get_object()
        if request.method == "GET":
            return Response(MinuteParticipantSerializer(
                minute.participants.all(), many=True,
                context=self.get_serializer_context()).data)

        if not perms.can_manage_participants(request.user, minute):
            raise PermissionDenied(
                "The member list can only be changed while the minute is a draft.")
        write = ParticipantListWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        rows = workflow.set_participants(
            minute, write.validated_data["participants"], request.user,
            request=request)
        return Response(MinuteParticipantSerializer(
            rows, many=True, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get", "post"], url_path="involvements")
    def involvements(self, request, pk=None):
        minute = self.get_object()
        if request.method == "GET":
            return Response(MinuteInvolvementSerializer(
                minute.involvements.all(), many=True,
                context=self.get_serializer_context()).data)

        if not perms.can_edit(request.user, minute):
            raise PermissionDenied("This minute can no longer be changed.")
        write = InvolvementListWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        minute.involvements.all().delete()
        MinuteInvolvement.objects.bulk_create(stamp_all([
            MinuteInvolvement(minute=minute, user=row["user"],
                              assignment_type=row["assignment_type"],
                              note=row.get("note", ""))
            for row in write.validated_data["involvements"]
        ]))
        return Response(MinuteInvolvementSerializer(
            minute.involvements.all(), many=True,
            context=self.get_serializer_context()).data)

    # ------------------------------------------------------------------
    # The two submit buttons (manual p.7)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="send-for-review")
    def send_for_review(self, request, pk=None):
        """Submit for Draft Review - routes the draft to the FRO."""
        minute = self.get_object()
        if not perms.can_send_for_review(request.user, minute):
            raise PermissionDenied(
                "Only the initiator can submit this draft for review, and only once "
                "an FRO has been chosen.")
        workflow.send_for_draft_review(minute, request.user, request=request)
        return Response(self._detail(minute))

    @action(detail=True, methods=["post"], url_path="return-review")
    def return_review(self, request, pk=None):
        """The FRO hands the draft back to its initiator."""
        minute = self.get_object()
        if not perms.can_return_review(request.user, minute):
            raise PermissionDenied("This minute is not with you for review.")
        write = ReviewReturnSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.return_from_review(
            minute, request.user,
            remarks=write.validated_data.get("remarks", ""), request=request)
        return Response(self._detail(minute))

    @action(detail=True, methods=["post"], url_path="send-for-acknowledgement")
    def send_for_acknowledgement(self, request, pk=None):
        """Submit for Acknowledge - assigns the minute to the members present."""
        minute = self.get_object()
        if not perms.can_send_for_acknowledgement(request.user, minute):
            raise PermissionDenied(
                "Only the initiator can submit this minute for acknowledgement, and "
                "only with at least one member present.")
        workflow.send_for_acknowledgement(minute, request.user, request=request)
        return Response(self._detail(minute))

    # ------------------------------------------------------------------
    # Acknowledging (manual pp. 8-9, without the OTP)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="acknowledge")
    def acknowledge(self, request, pk=None):
        minute = self.get_object()
        if not perms.can_acknowledge(request.user, minute):
            raise PermissionDenied(
                "Only a member recorded present can acknowledge this minute.")
        write = AcknowledgementSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.acknowledge(minute, request.user,
                             remarks=write.validated_data.get("remarks", ""),
                             request=request)
        return Response(self._detail(minute))

    @action(detail=True, methods=["post"], url_path="remind-acknowledgements")
    def remind_acknowledgements(self, request, pk=None):
        minute = self.get_object()
        if not perms.can_remind_acknowledgements(request.user, minute):
            raise PermissionDenied("There is nobody left to remind.")
        reminded = workflow.remind_pending_acknowledgements(
            minute, request.user, request=request)
        return Response({"reminded": reminded})

    # ------------------------------------------------------------------
    # Reference No. search (manual p.5)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="reference-lookup")
    def reference_lookup(self, request):
        """
        "Enter the previous meeting minute reference number and click on the search
        button. Then the contents of the previous meeting minute will appear in the
        description field" (p.5).

        Returns the agenda body of the referenced minute so the client can seed the
        editor. Scoped through the caller's own visible queryset, so this cannot be
        used to read a minute the caller could not open directly.
        """
        reference = (request.query_params.get("reference") or "").strip()
        if not reference:
            return Response({"detail": "Give a reference to search for."},
                            status=status.HTTP_400_BAD_REQUEST)
        found = (self.get_queryset()
                 .filter(Q(minute_number__iexact=reference)
                         | Q(reference_number__iexact=reference))
                 .order_by("-created_at").first())
        if found is None:
            return Response({"detail": "No minute found with that reference."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id": str(found.id),
            "minute_number": found.minute_number,
            "meeting_date": found.meeting_date,
            "type_label": found.minute_type.name if found.minute_type_id else "",
            "agenda_body": found.agenda_body,
        })

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="attachments",
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def attachments(self, request, pk=None):
        """
        List, or upload to, a minute's attachments.

        Accepts MULTIPLE files in one request under `files`, plus a single `file` for
        convenience. Each is validated independently and a rejection names the file
        that failed, so uploading five documents and getting "unsupported file type"
        tells you which one.
        """
        minute = self.get_object()
        if request.method == "GET":
            return Response(MinuteAttachmentSerializer(
                minute.attachments.all(), many=True,
                context=self.get_serializer_context()).data)

        if not perms.can_edit(request.user, minute):
            raise PermissionDenied(
                "Attachments cannot be added once the minute is part of the record.")

        uploaded = request.FILES.getlist("files") or (
            [request.FILES["file"]] if "file" in request.FILES else [])
        if not uploaded:
            return Response({"detail": "No file was supplied."},
                            status=status.HTTP_400_BAD_REQUEST)

        replaces = None
        replaces_id = request.data.get("replaces")
        if replaces_id:
            replaces = minute.attachments.filter(pk=replaces_id).first()
            if replaces is None:
                return Response({"replaces": "That attachment does not exist."},
                                status=status.HTTP_400_BAD_REQUEST)
            if len(uploaded) > 1:
                return Response(
                    {"replaces": "Replacing a version takes exactly one file."},
                    status=status.HTTP_400_BAD_REQUEST)

        created = []
        for item in uploaded:
            try:
                created.append(workflow.add_attachment(
                    minute, request.user, item, replaces=replaces, request=request))
            except ValidationError as exc:
                # Name the offending file. The detail is FLATTENED to plain text
                # first: str() on a DRF detail structure renders an ErrorDetail repr
                # into the user-facing message.
                raise ValidationError({
                    "files": [f"{getattr(item, 'name', 'file')}: "
                              f"{_flatten(exc.detail)}"]
                }) from exc
        return Response(
            MinuteAttachmentSerializer(created, many=True,
                                       context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "delete"],
            url_path=r"attachments/(?P<attachment_id>[^/.]+)")
    def attachment_detail(self, request, pk=None, attachment_id=None):
        """
        One attachment: its metadata and version history, or its removal.

        The file itself is NOT streamed from here - the payload carries signed,
        expiring URLs the browser fetches directly, which keeps large downloads off
        this endpoint and lets an <img> or <embed> load a preview with no
        Authorization header.
        """
        minute = self.get_object()
        attachment = minute.attachments.filter(pk=attachment_id).first()
        if attachment is None:
            return Response({"detail": "Attachment not found."},
                            status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            if not perms.can_edit(request.user, minute):
                raise PermissionDenied(
                    "Attachments cannot be removed once the minute is part of the "
                    "record.")
            workflow.remove_attachment(minute, request.user, attachment,
                                       request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(MinuteAttachmentSerializer(
            attachment, context=self.get_serializer_context()).data)

    # ------------------------------------------------------------------
    # Audit, dashboard, taxonomy
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="audit-trail")
    def audit_trail(self, request, pk=None):
        minute = self.get_object()
        if not perms.can_view_audit(request.user, minute):
            raise PermissionDenied("You cannot view this minute's audit trail.")
        return Response(MinuteAuditLogSerializer(
            minute.audit_entries.all(), many=True).data)

    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        """
        Counts for the dashboard tiles, over the caller's OWN visible set - so a tile
        can never advertise a minute the user cannot open.
        """
        user = request.user
        base = Minute.objects.all()
        visible = perms.visible_minute_filter(user)
        if visible is not None:
            base = base.filter(visible)
        base = base.distinct()

        Ack = MinuteParticipant.AckStatus
        present = MinuteParticipant.Attendance.PRESENT
        pending_ack = base.filter(
            participants__user=user, participants__attendance=present,
            participants__ack_status=Ack.PENDING)
        to_review = base.filter(fro=user, status=Minute.Status.DRAFT_FOR_REVIEW)

        counts = {
            "my_minutes": base.filter(created_by=user).count(),
            "my_drafts": base.filter(created_by=user,
                                     status=Minute.Status.DRAFT).count(),
            "draft_for_review": to_review.distinct().count(),
            "my_pending_acknowledgements": pending_ack.distinct().count(),
            "my_acknowledged": base.filter(
                participants__user=user,
                participants__ack_status=Ack.ACKNOWLEDGED).distinct().count(),
            "under_process": base.filter(status__in=[
                Minute.Status.DRAFT_FOR_REVIEW,
                Minute.Status.PENDING_ACKNOWLEDGEMENT]).count(),
            "archived": base.filter(status=Minute.Status.ARCHIVED).count(),
            "assigned_to_me": base.filter(
                Q(fro=user) | Q(involvements__user=user)
                | Q(participants__user=user)
            ).exclude(created_by=user).distinct().count(),
        }
        # One queryset rather than the sum of the two components: a minute that is both
        # with me for review and awaiting my acknowledgement would otherwise be counted
        # twice and the badge would not match the rows the queue opens with.
        counts["needs_my_action"] = base.filter(
            Q(fro=user, status=Minute.Status.DRAFT_FOR_REVIEW)
            | Q(participants__user=user, participants__attendance=present,
                participants__ack_status=Ack.PENDING)
        ).distinct().count()
        return Response(counts)

    @action(detail=False, methods=["get"], url_path="dashboard/charts")
    def dashboard_charts(self, request):
        """By-status and by-type counts, aggregated in the database."""
        base = Minute.objects.all()
        visible = perms.visible_minute_filter(request.user)
        if visible is not None:
            base = base.filter(visible)
        ids = list(base.distinct().values_list("id", flat=True))
        scoped = Minute.objects.filter(id__in=ids)

        labels = dict(Minute.Status.choices)
        by_status = [
            {"label": labels.get(row["status"], row["status"]),
             "value": row["total"]}
            for row in scoped.values("status").annotate(total=Count("id"))
            .order_by("-total")
        ]
        by_type = [
            {"label": row["minute_type__name"] or "Unassigned",
             "value": row["total"]}
            for row in scoped.values("minute_type__name").annotate(total=Count("id"))
            .order_by("-total")[:12]
        ]
        return Response({"by_status": by_status, "by_type": by_type})

    @action(detail=False, methods=["get"], url_path="taxonomy")
    def taxonomy(self, request):
        """
        The Minute Type dropdown and the option lists the form needs. The client
        renders every dropdown from this, so adding a type needs no frontend change.
        """
        return Response({
            "types": MinuteTypeSerializer(
                MinuteType.objects.filter(is_active=True), many=True).data,
            "attendance": [{"code": code, "label": label}
                           for code, label in MinuteParticipant.Attendance.choices],
            "assignment_types": [
                {"code": code, "label": label}
                for code, label in MinuteInvolvement.AssignmentType.choices],
            "statuses": [{"code": code, "label": label}
                         for code, label in Minute.Status.choices],
        })

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        from documents.pdf import common_context, render_pdf
        from .exports import build_pdf_context

        minute = self.get_object()
        if not perms.can_view(request.user, minute):
            raise PermissionDenied("You cannot export this minute.")

        context = common_context(minute.minute_number)
        context.update(build_pdf_context(minute))
        pdf_bytes = render_pdf("pdf/minute.html", context)

        record_audit(minute, request.user, "exported",
                     remarks="PDF exported.", metadata={"format": "pdf"},
                     request=request)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{minute.minute_number}.pdf"')
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @action(detail=True, methods=["get"], url_path="acknowledgement-sheet")
    def acknowledgement_sheet(self, request, pk=None):
        """
        A one-page signature sheet listing every member with their department and
        acknowledgement date, plus a ruled signature column.

        Separate from the minute PDF on purpose: this is the sheet that gets printed,
        signed in the room and filed, so it must not carry the whole minute behind it.
        """
        from documents.pdf import common_context, render_pdf
        from .exports import build_acknowledgement_sheet_context

        minute = self.get_object()
        if not perms.can_view(request.user, minute):
            raise PermissionDenied("You cannot export this minute.")

        context = common_context(minute.minute_number)
        context.update(build_acknowledgement_sheet_context(minute))
        pdf_bytes = render_pdf("pdf/minute_acknowledgement.html", context)

        record_audit(minute, request.user, "exported",
                     remarks="Acknowledgement sheet exported.",
                     metadata={"format": "pdf", "kind": "acknowledgement"},
                     request=request)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{minute.minute_number}-acknowledgement.pdf"')
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @action(detail=True, methods=["get"], url_path="excel")
    def excel(self, request, pk=None):
        from .exports import minute_workbook

        minute = self.get_object()
        if not perms.can_view(request.user, minute):
            raise PermissionDenied("You cannot export this minute.")
        content = minute_workbook(minute)
        record_audit(minute, request.user, "exported",
                     remarks="Excel exported.", metadata={"format": "xlsx"},
                     request=request)
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument."
                         "spreadsheetml.sheet")
        response["Content-Disposition"] = (
            f'attachment; filename="{minute.minute_number}.xlsx"')
        response["X-Content-Type-Options"] = "nosniff"
        return response

    # ------------------------------------------------------------------
    def _detail(self, minute):
        """Re-read through the scoped queryset so the response is fully hydrated."""
        return MinuteDetailSerializer(
            self.get_queryset().get(pk=minute.pk),
            context=self.get_serializer_context()).data
