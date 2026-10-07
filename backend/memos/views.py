from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle


class MemoDirectoryThrottle(UserRateThrottle):
    """Per-user scoped rate limit for the assignee-directory search (H5)."""
    scope = "memo_directory"

from audit.models import AuditLog
from users.models import User
from . import governance, services, workflow
from .filters import MemoFilter
from .models import (
    Memo, MemoApprovalStep, MemoSection, MemoTemplate, MemoWorkflowStep,
)
from .sanitizers import sanitize_memo_html
from .permissions import (
    CanDeleteMemo,
    CanMutateMemo,
    CanViewMemo,
    IsWorkflowParticipant,
    has_forensic_read, has_org_wide_read,
    visible_memo_filter,
)
from .serializers import (
    MemoArchiveGrantSerializer,
    MemoAttachmentSerializer,
    MemoCreateSerializer,
    MemoDetailSerializer,
    MemoEmployeeSerializer,
    MemoListSerializer,
    MemoMatrixInputSerializer,
    MemoNoteRequestSerializer,
    MemoNoteSerializer,
    MemoReassignSerializer,
    MemoReplacementSerializer,
    MemoSectionListWriteSerializer,
    MemoSectionSerializer,
    MemoTemplateSerializer,
    MemoTimelineEntrySerializer,
    MemoUnavailabilitySerializer,
    MemoUpdateSerializer,
    MemoWorkflowActionSerializer,
    MemoWorkflowStepSerializer,
)
from tenancy.stamping import stamp_all

# Assignee directory: require a search term and cap results so the endpoint
# cannot be used to enumerate the whole staff roster (H5).
DIRECTORY_MIN_QUERY = 2
DIRECTORY_LIMIT = 20

# Statuses treated as "in flight" - the memo has left the author but has not
# reached a terminal state. Backs the Outbox menu.
IN_FLIGHT_STATUSES = [
    Memo.Status.DRAFT_FOR_REVIEW,
    Memo.Status.UNDER_REVIEW,
    Memo.Status.RECOMMENDED,
    Memo.Status.SUPPORTED,
]


class MemoViewSet(viewsets.ModelViewSet):
    """
    Memo CRUD plus workflow action endpoints.

    Every state transition is delegated to memos.workflow - the single workflow
    engine - so this class stays thin. The old two-slot endpoints (submit /
    review / approve / reject / return, and the available-checkers and
    available-approvers pickers that fed them) were removed once migration 0010
    converted their memos onto the matrix.
    """
    permission_classes = [IsAuthenticated, CanViewMemo]

    # Server-side filtering. The global DEFAULT_FILTER_BACKENDS (DjangoFilter,
    # Search, Ordering) were enabled in settings but this viewset declared no
    # fields for them, so every documented query parameter was silently ignored
    # and each client re-filtered the first page of results in the browser -
    # which made list counts and queue badges wrong as soon as a user could see
    # more than one page of memos.
    filterset_class = MemoFilter
    search_fields = ["memo_number", "reference_number", "subject",
                     "created_by__first_name", "created_by__last_name",
                     "created_by__username"]
    ordering_fields = ["created_at", "submitted_at", "approved_at", "archived_at",
                       "memo_number", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        qs = Memo.objects.select_related("created_by", "department").prefetch_related(
            "approval_steps__actor", "workflow_steps__assignee", "attachments",
        )

        visible = visible_memo_filter(user)
        if visible is not None:
            qs = qs.filter(visible)
        return self._apply_scope(qs).distinct()

    def _apply_scope(self, qs):
        """
        Apply the ?scope= menu filter (Phase 3). Each sidebar entry maps to one
        scope so the server owns the definition of every menu and the client
        cannot drift from it.
        """
        scope = (self.request.query_params.get("scope") or "").strip()
        user = self.request.user
        if not scope or scope == "all":
            return qs

        if scope == "drafts":
            return qs.filter(created_by=user, status=Memo.Status.DRAFT)

        if scope == "draft_for_review":
            return qs.filter(created_by=user, status=Memo.Status.DRAFT_FOR_REVIEW)

        if scope == "mine":
            return qs.filter(created_by=user)

        if scope == "pending":
            # My Pending Actions: the memo is waiting on ME right now.
            return qs.filter(
                workflow_steps__assignee=user,
                workflow_steps__status=MemoWorkflowStep.StepStatus.ACTIVE,
            )

        if scope == "inbox":
            # Everything routed to me that is STILL LIVE, whether or not my turn
            # has arrived - so I can read the memo and the earlier remarks before
            # my turn comes round, and can still follow one I have actioned while
            # it moves on through the chain.
            #
            # Terminal memos drop out: a memo I reviewed months ago and which is
            # now archived is not inbox work, and leaving it there would grow the
            # queue without bound. It stays findable under All Memos.
            return qs.filter(
                workflow_steps__assignee=user,
                workflow_steps__status__in=[
                    MemoWorkflowStep.StepStatus.ACTIVE,
                    MemoWorkflowStep.StepStatus.PENDING,
                    MemoWorkflowStep.StepStatus.COMPLETED,
                ],
            ).exclude(created_by=user).exclude(status__in=Memo.TERMINAL_STATUSES)

        if scope == "outbox":
            return qs.filter(created_by=user, status__in=IN_FLIGHT_STATUSES)

        if scope == "department":
            return self._department_scope(qs, user)

        if scope == "approved":
            # Approval is the milestone; archiving is automatic and immediate, so
            # "Approved Memo" spans both or it would almost always read empty.
            return qs.filter(status__in=[Memo.Status.APPROVED, Memo.Status.ARCHIVED])

        if scope == "archived":
            return qs.filter(status=Memo.Status.ARCHIVED)

        if scope == "rejected":
            return qs.filter(status=Memo.Status.REJECTED)

        return qs

    def _department_scope(self, qs, user):
        """Memos raised by the caller's department (FK, or legacy text label)."""
        dept_ids = set(user.departments_headed.values_list("id", flat=True))
        if getattr(user, "department_ref_id", None):
            dept_ids.add(user.department_ref_id)
        scope = Q()
        if dept_ids:
            scope |= Q(department_id__in=dept_ids)
        if user.department:
            scope |= Q(department_name__iexact=user.department)
        if not scope:
            return qs.none()
        return qs.filter(scope)

    def get_serializer_class(self):
        if self.action == "list":
            return MemoListSerializer
        if self.action == "create":
            return MemoCreateSerializer
        if self.action in ("update", "partial_update"):
            return MemoUpdateSerializer
        return MemoDetailSerializer

    def get_permissions(self):
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), CanMutateMemo()]
        if self.action == "destroy":
            return [IsAuthenticated(), CanDeleteMemo()]
        return super().get_permissions()

    def perform_create(self, serializer):
        # Employee / Department Head / HR may create memos. Admin is an
        # oversight/approval role and does not author personal memo requests.
        user = self.request.user
        if user.role == User.Roles.ADMIN:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Admins oversee and approve memos; they do not create memo requests.")
        # H2: memo_number is assigned by Memo.save() via the single canonical
        # generator; do not mint it here (that was a second, divergent path).
        memo = serializer.save(
            created_by=user,
            status=Memo.Status.DRAFT,
        )
        # The form opens with Background and Recommendation (p.3). Seeded as
        # ordinary rows, so the author can retitle or delete either one - the
        # manual's red X on a block allows exactly that.
        MemoSection.objects.bulk_create(stamp_all([
            MemoSection(memo=memo, position=index, title=title, body="")
            for index, title in enumerate(MemoSection.DEFAULT_TITLES)
        ]))
        # Snapshot the unit labels beside their foreign keys, so a renamed or
        # deleted unit does not rewrite the header of an archived memo.
        if memo.department_unit_id or memo.department_sub_unit_id:
            memo.department_unit_name = (
                memo.department_unit.name if memo.department_unit_id else "")
            memo.department_sub_unit_name = (
                memo.department_sub_unit.name if memo.department_sub_unit_id else "")
            memo.save(update_fields=["department_unit_name",
                                     "department_sub_unit_name"])
        services.create_audit_log(
            user, AuditLog.Action.CREATE, instance=memo, request=self.request
        )
        # Opens the timeline at "Created by …", which Phase 7's own example does
        # and the previous implementation omitted entirely.
        workflow.record_creation(memo, user, request=self.request)

    def update(self, request, *args, **kwargs):
        """
        Apply the edit, then return the FULL detail payload. The write serializer
        only carries the editable fields, but every client re-renders the memo
        from this response, so returning the narrow shape would blank the page.
        """
        partial = kwargs.pop("partial", False)
        memo = self.get_object()
        serializer = MemoUpdateSerializer(memo, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        changed = {
            field: str(value) for field, value in serializer.validated_data.items()
        }
        memo = serializer.save()
        services.create_audit_log(
            request.user, AuditLog.Action.UPDATE, instance=memo,
            metadata={"transition": "edited", "fields": sorted(changed)},
            request=request,
        )
        # Phase 49.5: also write a history row, so the edit reaches the TIMELINE.
        # The audit trail already carried `transition: edited` with the changed
        # field names - what the Phase 49 audit actually found was that none of it
        # was visible to anyone but HR/Admin, because the timeline is built from
        # MemoApprovalStep and nothing wrote one for an edit.
        #
        # Only when something really changed: a PATCH that sets a field to the
        # value it already has is not an edit, and recording it would let a client
        # pad the timeline. The field names go in the comment rather than the
        # values, because a body diff does not belong on a timeline and some of
        # these memos are confidential.
        if changed:
            services._record_step(
                memo, request.user, MemoApprovalStep.Action.EDITED,
                comment="Edited: " + ", ".join(sorted(changed)))
        return self._detail_response(memo)

    def perform_destroy(self, instance):
        # Deletion is now guarded by CanDeleteMemo (author's own draft, or an
        # admin on anything not yet archived) and is recorded before the row
        # goes, so a removal is never invisible in the audit trail.
        services.create_audit_log(
            self.request.user, AuditLog.Action.DELETE, instance=instance,
            metadata={
                "memo_number": instance.memo_number,
                "status": instance.status,
                "subject": instance.subject,
            },
            request=self.request,
        )
        instance.delete()

    @action(detail=False, methods=["post"], url_path="create-and-submit",
            permission_classes=[IsAuthenticated])
    def create_and_submit(self, request):
        """
        Create a memo and send it for review in one atomic step (M2).

        If anything in the matrix is invalid the whole thing rolls back, so a
        rejected workflow never leaves an orphaned draft behind. A `workflow`
        matrix is required: there is no auto-routing fallback any more, because
        the person who knows who should approve a memo is the person writing it.
        """
        from django.db import transaction

        if request.user.role == User.Roles.ADMIN:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Admins oversee and approve memos; they do not create memo requests.")

        serializer = MemoCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        rows = self._matrix_rows(request, required=True)

        with transaction.atomic():
            memo = serializer.save(created_by=request.user, status=Memo.Status.DRAFT)
            services.create_audit_log(
                request.user, AuditLog.Action.CREATE, instance=memo, request=request)
            workflow.record_creation(memo, request.user, request=request)
            memo = workflow.send_for_review(
                memo, request.user, rows=rows,
                remarks=request.data.get("remarks", ""), request=request,
            )
        return Response(
            MemoDetailSerializer(memo, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def _detail_response(self, memo):
        # Re-read so the response reflects rows written in the transition (new
        # step statuses, new history entries) rather than a stale prefetch.
        memo = self.get_queryset().filter(pk=memo.pk).first() or memo
        serializer = MemoDetailSerializer(memo, context=self.get_serializer_context())
        return Response(serializer.data)

    def _matrix_rows(self, request, required=True):
        """
        Parse and validate the `workflow` payload into rows for the engine.
        Returns None when absent and not required.
        """
        # A body that is not an object at all — a bare JSON list, say — used to
        # reach `.get()` and raise AttributeError, which DRF turns into a 500.
        # Malformed input is the client's mistake and deserves a 400 telling
        # them so; an unhandled exception on request data is ours.
        if not hasattr(request.data, "get"):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({
                "workflow": "Send an object like {\"workflow\": [...]}, not a bare list."})
        raw = request.data.get("workflow")
        if raw in (None, "", []):
            if required:
                serializer = MemoMatrixInputSerializer(data={"steps": []})
                serializer.is_valid(raise_exception=True)
            return None
        if isinstance(raw, str):
            # multipart/form-data cannot carry nested JSON, so the create form
            # sends the matrix as a JSON string alongside the file parts.
            import json
            from rest_framework.exceptions import ValidationError
            try:
                raw = json.loads(raw)
            except ValueError:
                raise ValidationError({"workflow": "Must be valid JSON."})
        serializer = MemoMatrixInputSerializer(data={"steps": raw})
        serializer.is_valid(raise_exception=True)
        return [
            {"assignee_id": row["assignee_id"], "role_type": row["role_type"]}
            for row in serializer.validated_data["steps"]
        ]

    # -- Phase 4: approval matrix -------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="matrix",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def matrix(self, request, pk=None):
        """Read or replace a memo's approval matrix (replace = draft only)."""
        memo = self.get_object()
        if request.method == "GET":
            steps = memo.workflow_steps.order_by("sequence").select_related("assignee")
            return Response(MemoWorkflowStepSerializer(steps, many=True).data)

        rows = self._matrix_rows(request, required=True)
        workflow.set_matrix(memo, request.user, rows, request=request)
        return self._detail_response(memo)

    @action(detail=False, methods=["get"], url_path="departments",
            permission_classes=[IsAuthenticated])
    def departments(self, request):
        """
        The department tree, for the From unit / sub-unit pickers and the CC list
        (E-memo-manual p.4).

        Not search-gated the way the employee directory is: a department list is
        organisational structure, not personal data, and the form needs the whole
        list in a dropdown. `parent` is included because leaves.Department is
        self-nesting - a unit IS a department row further down the tree - so the
        client can narrow sub-units to the chosen unit rather than showing all of
        them at once.
        """
        from leaves.models import Department

        rows = (Department.objects.all()
                .values("id", "name", "parent_id")
                .order_by("name"))
        return Response([
            {"id": str(row["id"]), "name": row["name"],
             "parent": str(row["parent_id"]) if row["parent_id"] else None}
            for row in rows
        ])

    @action(detail=False, methods=["get"], url_path="employees",
            permission_classes=[IsAuthenticated], throttle_classes=[MemoDirectoryThrottle])
    def employees(self, request):
        """
        Employee directory for the approval-matrix picker.

        Phase 4 requires that ANY employee can be selected for ANY role type, so
        this is deliberately unfiltered by role - unlike the legacy
        available-checkers/available-approvers endpoints. It stays search-gated
        (>= 2 chars), capped and throttled so it cannot be used to walk the
        roster, and it never returns email addresses.
        """
        query = (request.query_params.get("search") or "").strip()
        if len(query) < DIRECTORY_MIN_QUERY:
            return Response([])
        qs = (
            User.objects.filter(is_active=True)
            .exclude(pk=request.user.pk)
            .filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(username__icontains=query)
                | Q(employee_id__icontains=query)
                | Q(designation__icontains=query)
            )
            .select_related("department_ref")
            .order_by("first_name", "username")[:DIRECTORY_LIMIT]
        )
        return Response(MemoEmployeeSerializer(qs, many=True).data)

    # -- Phase 2/5: matrix workflow transitions -----------------------------
    @action(detail=True, methods=["post"], url_path="send-for-review",
            permission_classes=[IsAuthenticated])
    def send_for_review(self, request, pk=None):
        """Draft -> Draft For Review. Optionally sets the matrix in the same call."""
        memo = self.get_object()
        rows = self._matrix_rows(request, required=False)
        result = workflow.send_for_review(
            memo, request.user, rows=rows,
            remarks=request.data.get("remarks", ""), request=request,
        )
        return self._detail_response(result)

    @action(detail=True, methods=["post"], url_path="act",
            permission_classes=[IsAuthenticated, IsWorkflowParticipant])
    def act(self, request, pk=None):
        """
        Complete or reject the caller's step. One endpoint rather than four
        because the active step's role type already determines the verb - which
        removes any chance of a client calling `approve` on a supporter's step.
        """
        serializer = MemoWorkflowActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        memo = self.get_object()
        result = workflow.act_on_step(
            memo, request.user,
            decision=serializer.validated_data["decision"],
            remarks=serializer.validated_data.get("remarks", ""),
            request=request,
        )
        return self._detail_response(result)

    # -- Phase 49.5 blocker 1: the note round -------------------------------
    #
    # Separate endpoints from `act`, deliberately. `act` completes a workflow step
    # and advances the chain; noting does neither, and routing both through one
    # endpoint would make "does not block the workflow" a property of a branch
    # rather than of the design.
    @action(detail=True, methods=["get", "post"], url_path="notes",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def notes(self, request, pk=None):
        """The note register, or a request for one or more people to note."""
        memo = self.get_object()
        if request.method == "GET":
            return Response(governance.note_summary(memo))
        payload = MemoNoteRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        governance.request_notes(
            memo, request.user, payload.validated_data["user_ids"],
            remarks=payload.validated_data.get("remarks", ""), request=request)
        return Response(governance.note_summary(memo),
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="note",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def note(self, request, pk=None):
        """Record that the caller has noted this memo."""
        memo = self.get_object()
        payload = MemoNoteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        governance.record_note(memo, request.user,
                              remarks=payload.validated_data.get("remarks", ""),
                              request=request)
        return Response(governance.note_summary(memo))

    @action(detail=True, methods=["delete"],
            url_path=r"notes/(?P<recipient_id>[^/.]+)",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def note_withdraw(self, request, pk=None, recipient_id=None):
        """Withdraw an outstanding request. A note already given is permanent."""
        memo = self.get_object()
        governance.withdraw_note_request(memo, request.user, recipient_id,
                                         request=request)
        return Response(governance.note_summary(memo))

    @action(detail=True, methods=["post"], url_path="notes/remind",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def note_remind(self, request, pk=None):
        memo = self.get_object()
        reminded = governance.remind_notes(memo, request.user, request=request)
        return Response({"reminded": reminded, **governance.note_summary(memo)})

    # -- Phase 49.5 blockers 2 and 3: unavailability and reassignment -------
    @action(detail=True, methods=["post"], url_path="unavailable",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def unavailable(self, request, pk=None):
        """Declare that a live step's holder cannot act."""
        memo = self.get_object()
        payload = MemoUnavailabilitySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        governance.mark_unavailable(
            memo, request.user, reason=payload.validated_data["reason"],
            reason_note=payload.validated_data.get("reason_note", ""),
            step_id=payload.validated_data.get("step_id"), request=request)
        return self._detail_response(Memo.objects.get(pk=memo.pk))

    @action(detail=True, methods=["post"], url_path="replacement",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def replacement(self, request, pk=None):
        """Put an alternate or acting approver on an unavailable step."""
        memo = self.get_object()
        payload = MemoReplacementSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        governance.assign_replacement(
            memo, request.user, user_id=payload.validated_data["user_id"],
            reason=payload.validated_data["reason"],
            kind=payload.validated_data.get("kind"),
            step_id=payload.validated_data.get("step_id"), request=request)
        return self._detail_response(Memo.objects.get(pk=memo.pk))

    @action(detail=True, methods=["post"], url_path="reassign",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def reassign(self, request, pk=None):
        """Self-assign, reassign or transfer a live step without an absence."""
        memo = self.get_object()
        payload = MemoReassignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        governance.reassign_step(
            memo, request.user, user_id=payload.validated_data.get("user_id"),
            reason=payload.validated_data["reason"],
            kind=payload.validated_data["kind"],
            step_id=payload.validated_data.get("step_id"), request=request)
        return self._detail_response(Memo.objects.get(pk=memo.pk))

    # -- Phase 49.5 blocker 4: archived access sharing ----------------------
    @action(detail=True, methods=["get", "post"], url_path="archive-access",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def archive_access(self, request, pk=None):
        """The sharing history, or a new grant."""
        memo = self.get_object()
        if request.method == "GET":
            return Response({
                "can_share": governance.can_share_archive(request.user, memo),
                "grants": governance.archive_sharing_history(memo),
            })
        payload = MemoArchiveGrantSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        governance.grant_archive_access(
            memo, request.user, request=request, **payload.validated_data)
        return Response({
            "can_share": governance.can_share_archive(request.user, memo),
            "grants": governance.archive_sharing_history(memo),
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"],
            url_path=r"archive-access/(?P<grant_id>[^/.]+)",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def archive_access_revoke(self, request, pk=None, grant_id=None):
        memo = self.get_object()
        governance.revoke_archive_access(
            memo, request.user, grant_id,
            reason=request.data.get("reason", "") if request.data else "",
            request=request)
        return Response({
            "can_share": governance.can_share_archive(request.user, memo),
            "grants": governance.archive_sharing_history(memo),
        })

    @action(detail=True, methods=["post"], url_path="archive",
            permission_classes=[IsAuthenticated])
    def archive(self, request, pk=None):
        """Manual archive fallback; approval normally archives automatically."""
        memo = self.get_object()
        result = workflow.archive_memo(memo, request.user, request=request)
        return self._detail_response(result)

    @action(detail=True, methods=["get"], url_path="timeline",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def timeline(self, request, pk=None):
        """Phase 7 activity timeline: actions taken plus steps still outstanding."""
        memo = self.get_object()
        entries = workflow.build_timeline(memo)
        return Response(MemoTimelineEntrySerializer(entries, many=True).data)

    # -- Phase 9: dashboard counts / badges ---------------------------------
    @action(detail=False, methods=["get"], url_path="dashboard",
            permission_classes=[IsAuthenticated])
    def dashboard(self, request):
        """
        Counts for the memo dashboard and the sidebar badges.

        Computed with COUNT queries over the caller's visible set rather than by
        counting rows in a paginated list response, which is what the previous
        dashboard card did - and which under-reported every total the moment a
        user could see more than one page.
        """
        user = request.user
        base = Memo.objects.all()
        visible = visible_memo_filter(user)
        if visible is not None:
            base = base.filter(visible)
        base = base.distinct()

        def count(**filters):
            return base.filter(**filters).count()

        def pending_as(*role_types):
            """
            Memos sitting on this user's desk in the given capacity.

            Filtered on assignee AND status AND role_type inside a single
            `filter()` call on purpose: split across two calls, Django would join
            the step table twice and match a memo where the user holds one step
            and some *other* step happens to be active.
            """
            return base.filter(
                workflow_steps__assignee=user,
                workflow_steps__status=MemoWorkflowStep.StepStatus.ACTIVE,
                workflow_steps__role_type__in=role_types,
            ).distinct().count()

        RoleType = MemoWorkflowStep.RoleType
        pending_review = pending_as(RoleType.REVIEWER)
        pending_recommend = pending_as(RoleType.RECOMMENDER)
        pending_support = pending_as(RoleType.SUPPORTER)
        pending_approval = pending_as(RoleType.APPROVER)

        # Mirrors the `inbox` scope exactly - the badge and the page must not
        # disagree about what an inbox contains.
        inbox = base.filter(
            workflow_steps__assignee=user,
            workflow_steps__status__in=[
                MemoWorkflowStep.StepStatus.ACTIVE,
                MemoWorkflowStep.StepStatus.PENDING,
                MemoWorkflowStep.StepStatus.COMPLETED,
            ],
        ).exclude(created_by=user).exclude(
            status__in=Memo.TERMINAL_STATUSES).distinct().count()

        department = self._department_scope(base, user).distinct().count()

        # Phase 49.5: the note round. Counted over the caller's OWN note requests
        # rather than over the memos' visible set, because "Pending Notes" answers
        # "what is waiting on me" - the same question the four pending_* counts
        # above answer for the approval chain. Not filtered through `base`: a note
        # request is itself a grant of the right to read the memo it is about, so
        # filtering it by visibility could hide a task from the person who has to
        # do it.
        from .models import MemoNoteRecipient
        NoteStatus = MemoNoteRecipient.NoteStatus
        my_notes = MemoNoteRecipient.objects.filter(user=user)
        pending_notes = my_notes.filter(status=NoteStatus.PENDING).count()
        completed_notes = my_notes.filter(status=NoteStatus.NOTED).count()

        # The manual's two "Under Process" tiles (E-memo-manual p.2).
        #
        #   Initiated  - "created by the logged-in user and is in the process of
        #                 approval"
        #   Involvement- "memo in which the logged-in user has been INVOLVED (i.e.
        #                 Supported, Reviewed, Approved, and Noted)"
        #
        # Involvement is about what the caller has already DONE, not what is waiting
        # on them: a completed step or a note they have given. That is why it is not
        # `inbox`, which counts what is still on their desk - the two answer
        # different questions and the manual names both.
        under_process_initiated = base.filter(
            created_by=user, status__in=IN_FLIGHT_STATUSES).distinct().count()
        under_process_involvement = base.filter(
            Q(workflow_steps__assignee=user,
              workflow_steps__status=MemoWorkflowStep.StepStatus.COMPLETED)
            | Q(note_round__recipients__user=user,
                note_round__recipients__status=NoteStatus.NOTED),
            status__in=IN_FLIGHT_STATUSES,
        ).exclude(created_by=user).distinct().count()

        return Response({
            # Phase 12 item 5 tiles.
            "drafts": count(created_by=user, status=Memo.Status.DRAFT),
            "under_process_initiated": under_process_initiated,
            "under_process_involvement": under_process_involvement,
            "pending_review": pending_review,
            "pending_recommendation": pending_recommend,
            "pending_support": pending_support,
            "pending_approval": pending_approval,
            "approved": base.filter(
                status__in=[Memo.Status.APPROVED, Memo.Status.ARCHIVED]).count(),
            "archived": count(status=Memo.Status.ARCHIVED),
            # Supporting counts for the sidebar badges and the other menus.
            "pending_actions": (pending_review + pending_recommend
                                + pending_support + pending_approval),
            # Phase 49.5 item: Pending Note / Completed Note. Deliberately NOT
            # added into pending_actions - a note is not an action the workflow is
            # waiting on, and folding it into the badge that means "the memo is
            # blocked on you" would make a non-blocking feature look blocking.
            "pending_notes": pending_notes,
            "completed_notes": completed_notes,
            # "Assigned Memo": memos where a step is genuinely on this desk now.
            # Named separately from pending_actions (which is the same number) so
            # the dashboard tile and the sidebar badge cannot drift apart.
            "assigned": (pending_review + pending_recommend
                         + pending_support + pending_approval),
            "draft_for_review": count(
                created_by=user, status=Memo.Status.DRAFT_FOR_REVIEW),
            "my_memos": count(created_by=user),
            "inbox": inbox,
            "outbox": base.filter(
                created_by=user, status__in=IN_FLIGHT_STATUSES).count(),
            "department": department,
            "rejected": count(status=Memo.Status.REJECTED),
            "total_visible": base.count(),
        })

    @action(detail=False, methods=["get"], url_path="dashboard/charts",
            permission_classes=[IsAuthenticated])
    def dashboard_charts(self, request):
        """
        The three dashboard chart datasets, aggregated over the caller's visible
        memos: by department, by status, and a monthly trend.

        Kept separate from /dashboard/ because the tiles are what the sidebar
        badge polls every minute and these three GROUP BY queries have no business
        running that often.
        """
        from django.db.models import Count
        from django.db.models.functions import TruncMonth

        user = request.user
        base = Memo.objects.all()
        visible = visible_memo_filter(user)
        if visible is not None:
            base = base.filter(visible)
        base = base.distinct()

        # --- by department -----------------------------------------------------
        # Grouped on the snapshot label, not the FK, so memos whose author only
        # ever had the legacy free-text department still land in a named bucket
        # instead of an unexplained blank one.
        dept_rows = (
            base.values("department_name")
            .annotate(total=Count("id")).order_by("-total")
        )
        by_department = [{
            "label": row["department_name"] or "Unassigned",
            "total": row["total"],
        } for row in dept_rows]

        # --- by status ---------------------------------------------------------
        # Emitted in workflow order with explicit zeros. A chart that silently
        # omits the empty stages reads as a shorter pipeline than the real one.
        counted = {
            row["status"]: row["total"]
            for row in base.values("status").annotate(total=Count("id"))
        }
        status_order = [
            Memo.Status.DRAFT, Memo.Status.DRAFT_FOR_REVIEW, Memo.Status.UNDER_REVIEW,
            Memo.Status.RECOMMENDED, Memo.Status.SUPPORTED, Memo.Status.APPROVED,
            Memo.Status.ARCHIVED, Memo.Status.REJECTED, Memo.Status.CANCELLED,
        ]
        by_status = [{
            "status": value,
            "label": Memo.Status(value).label,
            "total": counted.get(value, 0),
        } for value in status_order]

        # --- monthly trend -----------------------------------------------------
        months = int(request.query_params.get("months") or 12)
        months = max(3, min(months, 24))
        start = (timezone.now() - timedelta(days=31 * months)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)

        raw = (
            base.filter(created_at__gte=start)
            .annotate(month=TruncMonth("created_at"))
            .values("month").annotate(total=Count("id")).order_by("month")
        )
        seen = {row["month"].date().replace(day=1): row["total"] for row in raw if row["month"]}

        # Fill every month in the window, including the empty ones - a line chart
        # that skips a month with no memos draws a straight line across the gap
        # and claims activity that did not happen.
        trend = []
        cursor = start.date().replace(day=1)
        today = timezone.localdate().replace(day=1)
        while cursor <= today:
            trend.append({
                "month": cursor.isoformat(),
                "label": cursor.strftime("%b %Y"),
                "total": seen.get(cursor, 0),
            })
            cursor = (cursor.replace(day=28) + timedelta(days=7)).replace(day=1)

        return Response({
            "by_department": by_department,
            "by_status": by_status,
            "monthly_trend": trend,
        })

    # -- Phase 6: archive export --------------------------------------------
    @action(detail=False, methods=["get"], url_path="export",
            permission_classes=[IsAuthenticated])
    def export(self, request):
        """
        Excel export of the caller's current filtered/scoped view. Runs through
        the same queryset as the list endpoint, so a user can never export a
        memo they could not read.
        """
        from .exports import memos_to_xlsx

        qs = self.filter_queryset(self.get_queryset())[:5000]
        content = memos_to_xlsx(qs)
        from django.http import HttpResponse
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        scope = (request.query_params.get("scope") or "memos").replace("_", "-")
        response["Content-Disposition"] = f'attachment; filename="{scope}-export.xlsx"'
        return response

    @action(detail=True, methods=["post"], url_path="cancel",
            permission_classes=[IsAuthenticated])
    def cancel(self, request, pk=None):
        """
        The author withdraws their own memo.

        Kept when the two-slot endpoints were removed because it is not part of
        approval routing: it is the only way an author can stop a memo they have
        already sent without asking an approver to reject it. The URL keeps its
        original name so existing links continue to work.
        """
        memo = self.get_object()
        result = workflow.withdraw_memo(
            memo, request.user,
            remarks=request.data.get("remarks") or request.data.get("comment", ""),
            request=request,
        )
        return self._detail_response(result)

    # -- Phase 12 item 10: the full audit trail -----------------------------
    @action(detail=True, methods=["get"], url_path="audit-trail",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def audit_trail(self, request, pk=None):
        """
        Every recorded event for this memo, with actor, action, timestamp, IP and
        remarks.

        Restricted to HR/Admin: the timeline already gives everyone the workflow
        history, and this adds the client IP and user agent, which is forensic
        data rather than something a colleague needs to see.
        """
        from django.contrib.contenttypes.models import ContentType
        from rest_framework.exceptions import PermissionDenied

        memo = self.get_object()
        if not has_forensic_read(request.user):
            raise PermissionDenied(
                "The full audit trail with client addresses is restricted to HR and administrators."
            )

        entries = (
            AuditLog.objects
            .filter(content_type=ContentType.objects.get_for_model(Memo),
                    object_id=str(memo.id))
            .select_related("actor").order_by("created_at")
        )
        return Response([{
            "id": str(entry.id),
            "action": entry.get_action_display(),
            "transition": entry.changes.get("transition", ""),
            "actor": (entry.actor.get_full_name() or entry.actor.username) if entry.actor else "System",
            "actor_id": str(entry.actor_id) if entry.actor_id else None,
            "at": entry.created_at,
            "ip_address": entry.ip_address,
            "user_agent": entry.user_agent,
            "remarks": entry.changes.get("remarks", ""),
            "metadata": entry.changes,
        } for entry in entries])

    # -- content sections ("+ Add more", p.3) --------------------------------
    @action(detail=True, methods=["get", "post"], url_path="sections",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def sections(self, request, pk=None):
        """
        Read, or replace, the memo's ordered content blocks.

        POST takes the WHOLE list rather than one block at a time. The manual's
        "+ Add more" adds a (title, description) pair and the red X removes one,
        and their order is the document's order - sending the list is the only
        shape in which a client cannot leave the positions inconsistent.

        Editing follows the memo's own edit rules, so an archived memo refuses
        section changes exactly as it refuses every other edit.
        """
        from rest_framework.exceptions import PermissionDenied

        memo = self.get_object()
        if request.method == "GET":
            return Response(MemoSectionSerializer(
                memo.sections.all(), many=True,
                context=self.get_serializer_context()).data)

        guard = CanMutateMemo()
        if not guard.has_object_permission(request, self, memo):
            raise PermissionDenied(guard.message)

        write = MemoSectionListWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        rows = write.validated_data["sections"]

        memo.sections.all().delete()
        MemoSection.objects.bulk_create(stamp_all([
            MemoSection(memo=memo, position=index,
                        title=row["title"], body=row.get("body", ""))
            for index, row in enumerate(rows)
        ]))
        services.create_audit_log(
            request.user, AuditLog.Action.UPDATE, instance=memo,
            metadata={"transition": "sections_updated", "count": len(rows)},
            request=request,
        )
        return Response(MemoSectionSerializer(
            memo.sections.all(), many=True,
            context=self.get_serializer_context()).data)

    # -- attachments --------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="attachments",
            permission_classes=[IsAuthenticated, CanViewMemo])
    def attachments(self, request, pk=None):
        """
        List, or add to, a memo's attachments. Uploads follow the edit rules.

        The manual gives each file its own **File Name** box above "Choose file"
        (p.6), so `name` is accepted alongside the file and is what the reader is
        shown; the original filename is kept underneath it either way.
        """
        from rest_framework.exceptions import PermissionDenied

        memo = self.get_object()
        if request.method == "GET":
            return Response(MemoAttachmentSerializer(
                memo.attachments.all(), many=True,
                context=self.get_serializer_context()).data)

        guard = CanMutateMemo()
        if not guard.has_object_permission(request, self, memo):
            raise PermissionDenied(guard.message)

        uploaded = request.FILES.getlist("files") or (
            [request.FILES["file"]] if "file" in request.FILES else []
        )
        if not uploaded:
            return Response({"detail": "No file was supplied."},
                            status=status.HTTP_400_BAD_REQUEST)

        # THE WHOLE BATCH FIRST, then each file.
        #
        # Rejecting on count and total size before validating (and storing) any
        # single file means an oversized request costs one cheap check instead
        # of a partial write that leaves some files attached and some not.
        services.validate_memo_attachment_batch(
            uploaded, existing_count=memo.attachments.count())

        from .models import MemoAttachment
        # One name for a single upload; `names` repeated for a batch. Absent, the
        # file's own name is used, which is what the box is pre-filled with.
        names = request.data.getlist("names") if hasattr(request.data, "getlist") else []
        single = (request.data.get("name") or "").strip()
        created = []
        for index, item in enumerate(uploaded):
            services.validate_memo_attachment(item)
            given = (names[index].strip() if index < len(names) else "") or single
            created.append(MemoAttachment.objects.create(
                memo=memo, file=item, original_name=item.name,
                display_name=given[:255],
                size=getattr(item, "size", 0) or 0, uploaded_by=request.user,
            ))
        services.create_audit_log(
            request.user, AuditLog.Action.UPDATE, instance=memo,
            metadata={"transition": "attachments_added", "count": len(created)},
            request=request,
        )
        return Response(
            MemoAttachmentSerializer(created, many=True,
                                     context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="attachment")
    def attachment(self, request, pk=None):
        """
        Stream the memo's attachment behind authorization.

        get_object() runs CanViewMemo, so only users entitled to the memo can
        download its file (closes the unauthenticated-media hole). The file is
        always served as an attachment (never inline) so an uploaded
        HTML/SVG cannot execute in the app origin.
        """
        from django.http import FileResponse, Http404

        memo = self.get_object()
        if not memo.attachment:
            raise Http404("This memo has no attachment.")
        response = FileResponse(
            memo.attachment.open("rb"),
            as_attachment=True,
            filename=memo.attachment.name.rsplit("/", 1)[-1],
            # Force a generic type so the browser never renders an uploaded
            # HTML/SVG inline in the app origin; nosniff blocks type-guessing.
            content_type="application/octet-stream",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        """Approved-memo PDF with letterhead, approval matrix and verification QR."""
        from django.http import HttpResponse
        from documents.models import IssuedDocument
        from documents.pdf import common_context, render_pdf
        from documents.services import issue_document

        memo = self.get_object()
        # Archived is included: approval auto-archives, so gating on `approved`
        # alone would make the PDF unreachable for every completed memo.
        if memo.status not in (Memo.Status.APPROVED, Memo.Status.ARCHIVED):
            return Response({"detail": "PDF is available only for approved memos."},
                            status=status.HTTP_409_CONFLICT)

        steps = list(memo.approval_steps.select_related("actor").all())
        matrix = workflow.pdf_matrix(memo)
        # Built once and both grouped and passed through, so the compact rows and the
        # flat list can never describe different signatures.
        signatures = workflow.signature_blocks(memo)
        author = memo.created_by
        # "To" is the final approver on the chain - the person the memo is
        # addressed to for decision - read off the matrix now that the legacy
        # current_approver column is gone.
        final_approver = next(
            (row for row in reversed(matrix) if row["role_type"] == "approver"), None)

        def _name(u):
            return (u.get_full_name() or u.username) if u else "—"

        doc = issue_document(
            IssuedDocument.DocType.MEMO, "memo", memo, subject=memo.subject,
            issued_by=request.user, actors=[_name(s.actor) for s in steps if s.actor],
        )
        ctx = common_context(doc.document_number)
        ctx.update({
            "memo": memo,
            "to_name": final_approver["name"] if final_approver else "—",
            "from_name": _name(author),
            # The departments copied in. On a GENERAL memo these may also read the
            # archived memo (p.4), so the document names them.
            "cc_names": [d.name for d in memo.cc_departments.all()],
            "author_designation": getattr(author, "designation", "") or "",
            "department_label": memo.resolved_department_name() or "—",
            # Each titled block in order. Sections are sanitized on write and
            # re-sanitized here as defence in depth: this is the moment untrusted
            # HTML is rendered into a document. `linebreaks` is NOT applied - it
            # wrapped already-structured HTML in extra <p> tags, which pushed
            # authored tables out of their containing block.
            "sections": [
                {"title": section.title,
                 "body_html": sanitize_memo_html(section.body)}
                for section in memo.sections.all()
            ],
            "approver_name": final_approver["name"] if final_approver else "—",
            # The memo type is printed on the face of the document, because
            # CONFIDENTIAL is a handling instruction and not merely metadata.
            "memo_type": memo.memo_type,
            "memo_type_label": memo.get_memo_type_display(),
            "fully_approved": memo.status in (Memo.Status.APPROVED, Memo.Status.ARCHIVED),
            "matrix": matrix,
            # Phase 16/20 signature cards, from the same source the detail page
            # renders, so screen and paper cannot disagree.
            "signatures": signatures,
            # Phase 30: the same blocks grouped into compact rows - three steps
            # across in one row, five or more wrapped in balanced rows. The template
            # renders whatever grouping it is handed and knows no block count.
            "signature_rows": workflow.signature_rows(signatures),
            # Phase 26: drives the certifying seal at the foot of the document.
            # None unless the memo really is approved, so the template needs no
            # status test of its own.
            "certificate": workflow.approval_certificate(memo),
            "attachments": [{
                "name": f.original_name or f.file.name.rsplit("/", 1)[-1],
                "label": f.label,
                "uploaded_by": _name(f.uploaded_by),
                "uploaded_at": f.uploaded_at,
            } for f in memo.attachments.all()],
            "steps": [{
                "step_order": s.step_order,
                "get_action_display": s.get_action_display(),
                "actor_name": _name(s.actor),
                "acted_at": s.acted_at,
                "comment": s.comment,
            } for s in steps],
            # Phase 49.5: three registers the Phase 49 audit found missing from the
            # document. Each renders only when it has rows, so an ordinary memo's
            # PDF is unchanged - a governance document should not grow three empty
            # tables to prove a feature exists.
            "notes": governance.note_summary(memo),
            # The reviewed register: the reviewer steps alone, because Reviewer is
            # the one role the module requires remarks from and those remarks are
            # the substance of the review.
            "reviewed_rows": [row for row in matrix
                              if row["role_type"] == MemoWorkflowStep.RoleType.REVIEWER],
            "archive_sharing": governance.archive_sharing_history(memo),
            "assignment_history": governance.assignment_history(memo),
        })
        pdf_bytes = render_pdf("pdf/memo.html", ctx)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{doc.document_number}.pdf"'
        return response


class MemoTemplateViewSet(viewsets.ModelViewSet):
    """
    Memo templates. Any authenticated user may read active templates to prefill
    a new memo; create/update/delete are admin-only (L1).
    """
    serializer_class = MemoTemplateSerializer

    def get_permissions(self):
        from users.admin_views import IsAdminOrSuperuser
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdminOrSuperuser()]

    def get_queryset(self):
        # Admins manage all templates (incl. inactive); everyone else sees only
        # active ones for the prefill dropdown.
        user = self.request.user
        if getattr(user, "role", None) == User.Roles.ADMIN or user.is_staff or user.is_superuser:
            return MemoTemplate.objects.all()
        return MemoTemplate.objects.filter(is_active=True)
