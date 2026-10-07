from datetime import date
from rest_framework import viewsets, views, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from users.permissions import IsApproverOrAdmin
from audit.models import AuditLog
from audit.services import log_action
from .models import Leave, LeaveBalance
from .serializers import LeaveSerializer, LeaveBalanceSerializer
from users.roles import has_org_wide_read as _org_wide_read


def get_leave_days(start_date, end_date):
    return (end_date - start_date).days + 1


class LeaveViewSet(viewsets.ModelViewSet):
    serializer_class = LeaveSerializer
    permission_classes = [IsAuthenticated]
    # Global search (Ctrl+K). Applied to get_queryset()'s role-scoped rows, so
    # it can only narrow what the caller may already see.
    search_fields = ["user__first_name", "user__last_name", "user__employee_id",
                     "leave_type", "reason"]

    def get_queryset(self):
        user = self.request.user
        from users.models import User

        # Actionable pending queue — the SINGLE SOURCE shared with notifications
        # (leaves.approvals). Drives the Pending Approvals list + count so they can
        # never disagree with who was told "awaiting your review".
        if self.request.query_params.get("queue") == "actionable":
            from .approvals import pending_actionable_leaves
            return pending_actionable_leaves(user).order_by("-created_at")

        # Soft-deleted leaves are hidden from all listings/detail (Phase 5).
        base = Leave.objects.filter(is_deleted=False).order_by("-created_at")

        if user.role == User.Roles.MAKER:
            return base.filter(user=user)

        if user.role == User.Roles.CHECKER:
            # A Department Head's list serves TWO purposes from one endpoint:
            #   1. their OWN applications (all statuses) — for My Applications,
            #      dashboard counts, and recent/history; and
            #   2. their Level-1 review queue = pending leaves in their department.
            # Without clause (1), a Dept Head who applied for leave saw an empty
            # My Applications and all-zero dashboard counts even though their leave
            # existed (it wasn't 'pending', so the review-only filter dropped it).
            from django.db.models import Q
            own = Q(user=user)
            # 3. leaves where THIS head is the reporting manager the employee
            #    selected — they may sit in another department, so the department
            #    filter below would otherwise hide a request routed to them.
            chosen = Q(status=Leave.Status.PENDING, approver=user)

            # The departmental review claim. Resolved exactly as
            # leaves.approvals.pending_actionable_leaves does it, so the list and
            # the actionable queue cannot disagree about who a head reviews:
            # structured ref when set, else the legacy free-text department.
            #
            # A head with NEITHER has no departmental claim at all, and must fall
            # through to `own | chosen`. This clause used to be *dropped* in that
            # case rather than emptied, which left `review` as a bare
            # Q(status=PENDING) — every pending leave in the organisation. That is
            # reachable without any data-entry mistake: `User.department_ref` is
            # on_delete=SET_NULL, so deleting or merging a Department nulls it for
            # every member at once and silently widened each head's queue company-wide.
            if user.department_ref_id:
                review = Q(status=Leave.Status.PENDING,
                           user__department_ref_id=user.department_ref_id)
            elif user.department:
                review = Q(status=Leave.Status.PENDING,
                           user__department__iexact=user.department)
            else:
                return base.filter(own | chosen).distinct()

            return base.filter(own | review | chosen).distinct()

        if _org_wide_read(user):
            # HR / Admin: all departments (Level-2 queue is status=PENDING_HR).
            # The Board reads every department's leave too (Phase BOD); what it
            # may DECIDE is limited to Department Heads' leave - leaves.approvals.
            return base

        return Leave.objects.none()

    def perform_create(self, serializer):
        from users.models import User
        # Admin is an oversight/approval role and cannot apply for personal leave.
        if self.request.user.role == User.Roles.ADMIN:
            raise PermissionDenied("Admins manage and approve leave; they do not apply for personal leave.")
        if self.request.user.role not in [User.Roles.MAKER, User.Roles.CHECKER,
                                          User.Roles.APPROVER, User.Roles.BOD]:
            raise PermissionDenied("You are not permitted to apply for leave.")
        approver = serializer.validated_data.get('approver') if hasattr(serializer, 'validated_data') else None
        if approver and approver.role not in [User.Roles.CHECKER, User.Roles.APPROVER,
                                              User.Roles.BOD, User.Roles.ADMIN]:
            raise PermissionDenied("Invalid reporting manager selected.")
        # The Board decides Department Heads' and Board members' leave only
        # (Phase BOD). An employee's leave stays in the department.
        from .approvals import routes_to_board
        if approver and approver.role == User.Roles.BOD and not routes_to_board(self.request.user):
            raise PermissionDenied(
                "Board members decide Department Heads' leave. Choose your Department Head.")

        from django.db import transaction
        from rest_framework.exceptions import ValidationError
        from . import category_engine
        vd = serializer.validated_data
        start, end, ltype = vd.get('start_date'), vd.get('end_date'), vd.get('leave_type')

        # Category-aware balance guard + create in ONE transaction (M4/H4):
        # ensure_category_balances refreshes this user's allocations from their
        # resolved category and locks/recomputes committed days (approved + both
        # pending stages). check_apply then validates the request against the
        # category entitlement (working vs calendar days) or the comp ledger. The
        # row lock is held through serializer.save(), so concurrent applications
        # for the same type/year cannot slip past the guard.
        with transaction.atomic():
            if start and end:
                category_engine.ensure_category_balances(self.request.user, start.year)
                error = category_engine.check_apply(self.request.user, ltype, start, end)
                if error:
                    raise ValidationError(error)
            serializer.save(user=self.request.user, status=Leave.Status.PENDING)
        log_action(self.request.user, AuditLog.Action.SUBMIT, instance=serializer.instance, request=self.request)
        # Notifications (Trigger 1 -> Dept Head + HR) fire from the Leave post_save
        # signal (notifications.signals), which covers every save path.

    def perform_destroy(self, instance):
        # Leaves with approved day records are soft-deleted to preserve history;
        # everything else is hard-deleted as before.
        from .models import LeaveDayRecord
        from . import services

        has_approved = instance.day_records.filter(
            status=LeaveDayRecord.Status.APPROVED
        ).exists()
        log_action(self.request.user, AuditLog.Action.DELETE, instance=instance, request=self.request)
        if has_approved:
            services.soft_delete_leave(instance)
        else:
            instance.delete()

    @action(detail=True, methods=['post'], permission_classes=[IsApproverOrAdmin])
    def set_status(self, request, pk=None):
        leave = self.get_object()
        status_value = request.data.get('status')
        valid_statuses = [choice[0] for choice in Leave.Status.choices]
        if status_value not in valid_statuses:
            return Response({"error": "Invalid leave status."}, status=status.HTTP_400_BAD_REQUEST)

        remarks = (request.data.get('remarks') or '').strip()
        # A rejection must always carry a reason (spec: mandatory remark on reject).
        if status_value == Leave.Status.REJECTED and not remarks:
            return Response({"error": "A remark is required when rejecting a request."},
                            status=status.HTTP_400_BAD_REQUEST)

        old_status = leave.status
        # Enforce the two-stage workflow (M3): status cannot jump PENDING ->
        # APPROVED skipping the Department Head (Level 1). Balance is derived from
        # day records by the signal layer, so a status change here recomputes it
        # exactly once and can never double-deduct.
        ALLOWED_TRANSITIONS = {
            Leave.Status.PENDING_HR: {Leave.Status.PENDING},
            Leave.Status.APPROVED: {Leave.Status.PENDING_HR},
            Leave.Status.REJECTED: {Leave.Status.PENDING, Leave.Status.PENDING_HR},
        }
        if old_status != status_value and old_status not in ALLOWED_TRANSITIONS.get(status_value, set()):
            return Response(
                {"error": "This request has already moved on, or needs the Department "
                          "Head's approval before HR. Refresh to see where it stands."},
                status=status.HTTP_409_CONFLICT,
            )

        from django.db import transaction
        from django.utils import timezone
        with transaction.atomic():
            # Re-read under a ROW LOCK and re-check the guard inside the
            # transaction. The check above is a cheap pre-filter only: it runs on
            # an unlocked read, so two reviewers acting at once (or one
            # double-submitted form) could both pass it and both write, and the
            # second write would silently overwrite the first reviewer's identity
            # and timestamp. inventory/lifecycle.py has always done it this way
            # -- lock, then guard -- and says so in its module docstring.
            leave = Leave.objects.select_for_update().get(pk=leave.pk)
            old_status = leave.status
            if old_status != status_value and old_status not in ALLOWED_TRANSITIONS.get(status_value, set()):
                return Response(
                    {"error": "This request has already moved on, or needs the Department "
                              "Head's approval before HR. Refresh to see where it stands."},
                    status=status.HTTP_409_CONFLICT,
                )
            leave.status = status_value
            leave.approver = request.user
            # Record the acting reviewer + timestamp + remark for the audit timeline.
            leave.hr_reviewer = request.user
            leave.hr_action_date = timezone.now()
            if remarks:
                leave.remarks = remarks
            leave.save()  # signal syncs day records + recomputes both balances

        audit_action = AuditLog.Action.APPROVE if status_value == Leave.Status.APPROVED else AuditLog.Action.REJECT if status_value == Leave.Status.REJECTED else AuditLog.Action.UPDATE
        log_action(request.user, audit_action, instance=leave, changes={'from': old_status, 'to': status_value}, request=request)

        # Notifications fire from the Leave post_save signal on the status change.
        return Response(self.get_serializer(leave).data)

    @action(detail=True, methods=['post'], url_path='dept-head-review', permission_classes=[IsAuthenticated])
    def dept_head_review(self, request, pk=None):
        """Department Head review — FINAL and EFFECTIVE. approve -> APPROVED
        (leave granted immediately + balance deducted atomically); reject ->
        REJECTED (reason mandatory). HR/Admin approval is NOT a second stage; HR
        (approver) may act only as a fallback grantor when the applicant's
        department has no active Department Head, so leave can never get stuck."""
        from django.db import transaction
        from django.utils import timezone
        from users.models import User
        from . import services
        leave = self.get_object()
        actor = request.user

        if actor.id == leave.user_id:
            raise PermissionDenied("You cannot review your own leave.")

        is_admin = actor.role == User.Roles.ADMIN
        from .approvals import board_member_ids, routes_to_board
        board_routed = routes_to_board(leave.user)
        if board_routed:
            # A Department Head's or Board member's leave: the Board decides
            # (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE). HR only when no other Board
            # member is active, and no Department Head at all.
            board = board_member_ids(leave.user_id)
            is_board = actor.role == User.Roles.BOD
            if is_board and board and leave.approver_id in board and leave.approver_id != actor.id:
                raise PermissionDenied(
                    "This leave was addressed to another Board member, who decides it.")
            is_hr_fallback = actor.role == User.Roles.APPROVER and not board
            is_dept_head = False
            if not (is_admin or is_board or is_hr_fallback):
                raise PermissionDenied(
                    "A Department Head's leave is decided by the Board of Directors. "
                    "HR may act only when no Board member is available.")
        else:
            is_board = False
            is_dept_head = actor.role == User.Roles.CHECKER
            # Fallback: HR grants ONLY when the applicant's department has no active
            # Department Head. Otherwise HR is not part of the grant path.
            is_hr_fallback = (
                actor.role == User.Roles.APPROVER
                and not services.department_has_dept_head(leave.user)
            )
            if not (is_admin or is_dept_head or is_hr_fallback):
                raise PermissionDenied(
                    "Only the Department Head grants leave. HR/Admin may act only as a "
                    "fallback when the department has no active Department Head."
                )
        # A Department Head is scoped to leave from their OWN department — UNLESS
        # the employee explicitly selected them as their reporting manager, which
        # is exactly what routed the request to them (leaves.approvals). Without
        # this, a head chosen from another department would see the request in
        # their queue and then be refused when they tried to act on it.
        from .approvals import selection_is_actionable
        is_selected_manager = leave.approver_id == actor.id and selection_is_actionable(leave)
        if (is_dept_head and not is_selected_manager and actor.department_ref_id
                and leave.user.department_ref_id != actor.department_ref_id):
            raise PermissionDenied("You can only review leave from your own department.")
        if leave.status != Leave.Status.PENDING:
            return Response({"error": "Leave is not awaiting Department Head review."}, status=status.HTTP_400_BAD_REQUEST)

        decision = (request.data.get('decision') or '').lower()
        remarks = (request.data.get('remarks') or '').strip()
        if decision not in ('approve', 'reject'):
            return Response({"error": "Please choose Approve or Reject."}, status=status.HTTP_400_BAD_REQUEST)
        # A rejection must always carry a reason (spec: mandatory remark on reject).
        if decision == 'reject' and not remarks:
            return Response({"error": "A remark is required when rejecting a request."},
                            status=status.HTTP_400_BAD_REQUEST)

        old_status = leave.status
        with transaction.atomic():
            # Re-read under a ROW LOCK and re-check the guard inside the
            # transaction. The check above is a cheap pre-filter only: it runs on
            # an unlocked read, so two reviewers acting at once (or one
            # double-submitted form) could both pass it and both write, and the
            # second write would silently overwrite the first reviewer's identity
            # and timestamp. inventory/lifecycle.py has always done it this way
            # -- lock, then guard -- and says so in its module docstring.
            leave = Leave.objects.select_for_update().get(pk=leave.pk)
            if leave.status != Leave.Status.PENDING:
                return Response(
                    {"error": "Leave is not awaiting Department Head review."},
                    status=status.HTTP_400_BAD_REQUEST)
            old_status = leave.status
            if board_routed:
                # Recorded as the Board's decision, never as a Department Head's.
                leave.board_reviewer = actor
                leave.board_action_date = timezone.now()
            else:
                leave.department_head_reviewer = actor
                leave.department_head_action_date = timezone.now()
            # Single effective approval: record the actor as the final approver too
            # (drives the granted-leave PDF/certificate + the employee notification).
            leave.approver = actor
            if remarks:
                leave.remarks = remarks
            if decision == 'approve':
                # Re-validate the balance at grant time (H4): entitlement or other
                # approvals may have changed since the employee applied. The leave
                # is already counted as pending, so this row-locked recompute is the
                # true post-grant figure and must not exceed the allocation.
                if leave.leave_type in services.ENTITLEMENT_CODES:
                    bal = services.sync_simple_balance(leave.user, leave.leave_type, leave.start_date.year)
                    if bal and bal.used_so_far > bal.total_allocated:
                        return Response(
                            {"error": f"Approving would exceed the {leave.leave_type.capitalize()} "
                                      f"Leave allocation ({bal.total_allocated} day(s)); the balance "
                                      f"changed after this leave was applied."},
                            status=status.HTTP_409_CONFLICT,
                        )
                leave.status = Leave.Status.APPROVED
                audit_action = (AuditLog.Action.BOD_APPROVED if is_board
                                else AuditLog.Action.APPROVE)
            else:
                leave.status = Leave.Status.REJECTED
                audit_action = (AuditLog.Action.BOD_REJECTED if is_board
                                else AuditLog.Action.REJECT)
            leave.save()  # signal syncs day records + recomputes balances (derived, no double-deduct)

        # The stage says WHO decided this, in the routing's own terms. Phase
        # DEPARTMENT-GOVERNANCE-HARDENING added `hr_fallback_no_department_head`:
        # HR deciding an employee's leave because their department has nobody at
        # its head used to be logged as 'department_head', which made an
        # unowned department invisible in the audit trail - the exact ambiguity
        # this phase exists to remove. The fallback still happens (nobody is
        # blocked from taking leave over a configuration gap); it is no longer
        # silent.
        stage = ('board' if is_board
                 else 'board_fallback' if board_routed
                 else 'hr_fallback_no_department_head' if is_hr_fallback
                 else 'department_head')
        changes = {'stage': stage, 'from': old_status, 'to': leave.status,
                   'remarks': remarks, 'actor_role': actor.role}
        if stage == 'hr_fallback_no_department_head':
            changes['department'] = (
                getattr(leave.user.department_ref, 'name', '')
                or getattr(leave.user, 'department', '') or 'No department')
        log_action(actor, audit_action, instance=leave, changes=changes,
                   request=request)
        if board_routed and is_admin and board:
            # BOARD-LEVEL NOTIFICATION (Phase BOD): an administrator decided a
            # Department Head's leave while Board members were available to. That
            # is allowed - Admin keeps oversight - but it bypassed the Board, and
            # the Board is told. Best-effort: a failed notice never undoes a
            # decision already recorded.
            from notifications.dispatcher import notify
            from notifications.models import Category
            verb = "approved" if leave.status == Leave.Status.APPROVED else "rejected"
            for member in User.objects.filter(pk__in=board):
                try:
                    notify(member, Category.BOARD_NOTICE,
                           f"Department Head leave decided by an administrator — {leave.user.get_full_name()}",
                           f"{actor.get_full_name()} {verb} {leave.user.get_full_name()}'s "
                           f"{leave.get_leave_type_display()} without a Board decision.",
                           action_url="/leave/records",
                           idempotency_key=f"leave-{leave.id}-board-override-{member.id}",
                           object_id=leave.id)
                except Exception:  # noqa: BLE001 - notification must not undo the decision
                    pass
        # Notifications fire from the Leave post_save signal: APPROVED -> employee
        # "granted" (+ HR/Admin record copy); REJECTED -> employee with reason.
        return Response(self.get_serializer(leave).data)

    @action(detail=True, methods=['post'], url_path='hr-review', permission_classes=[IsApproverOrAdmin])
    def hr_review(self, request, pk=None):
        """Level-2 final review by HR (role=approver). approve -> APPROVED (+balance), reject -> REJECTED."""
        from django.utils import timezone
        leave = self.get_object()
        actor = request.user
        if actor.id == leave.user_id:
            raise PermissionDenied("You cannot approve your own leave.")
        if leave.status != Leave.Status.PENDING_HR:
            return Response({"error": "Leave is not awaiting HR review (Department Head must approve first)."},
                            status=status.HTTP_400_BAD_REQUEST)

        decision = (request.data.get('decision') or '').lower()
        remarks = request.data.get('remarks', '') or ''
        if decision not in ('approve', 'reject'):
            return Response({"error": "Please choose Approve or Reject."}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction
        from . import services
        old_status = leave.status
        with transaction.atomic():
            # Re-read under a ROW LOCK and re-check the guard inside the
            # transaction. The check above is a cheap pre-filter only: it runs on
            # an unlocked read, so two reviewers acting at once (or one
            # double-submitted form) could both pass it and both write, and the
            # second write would silently overwrite the first reviewer's identity
            # and timestamp. inventory/lifecycle.py has always done it this way
            # -- lock, then guard -- and says so in its module docstring.
            leave = Leave.objects.select_for_update().get(pk=leave.pk)
            if leave.status != Leave.Status.PENDING_HR:
                return Response(
                    {"error": "Leave is not awaiting HR review (Department Head must approve first)."},
                    status=status.HTTP_400_BAD_REQUEST)
            old_status = leave.status
            leave.hr_reviewer = actor
            leave.hr_action_date = timezone.now()
            leave.approver = actor  # backward-compat: `approver` = final approver
            if remarks:
                leave.remarks = remarks
            if decision == 'approve':
                # Re-validate the balance at the final stage (H4): entitlement or
                # other approvals may have changed since the employee applied.
                # sync_simple_balance locks + recomputes committed days; the leave
                # is already counted (it is pending_hr), so this is the true
                # post-approval figure and must not exceed the allocation.
                if leave.leave_type in services.ENTITLEMENT_CODES:
                    bal = services.sync_simple_balance(leave.user, leave.leave_type, leave.start_date.year)
                    if bal and bal.used_so_far > bal.total_allocated:
                        return Response(
                            {"error": f"Approving would exceed the {leave.leave_type.capitalize()} "
                                      f"Leave allocation ({bal.total_allocated} day(s)); the balance "
                                      f"changed after this leave was applied."},
                            status=status.HTTP_409_CONFLICT,
                        )
                leave.status = Leave.Status.APPROVED
                leave.save()  # signal recomputes both balances (derived, no double-deduct)
                audit_action = AuditLog.Action.APPROVE
            else:
                leave.status = Leave.Status.REJECTED
                leave.save()
                audit_action = AuditLog.Action.REJECT
        log_action(actor, audit_action, instance=leave,
                   changes={'stage': 'hr', 'from': old_status, 'to': leave.status, 'remarks': remarks},
                   request=request)
        # Notifications (Trigger 3 -> employee) fire from the Leave post_save signal.
        return Response(self.get_serializer(leave).data)

    def _leave_pdf_context(self, leave, document_number):
        from documents.pdf import common_context
        from . import services as leave_services
        from .models import EnterpriseLeaveBalance, LeaveType

        user = leave.user
        employee_name = user.get_full_name() or user.username
        approver_name = (leave.approver.get_full_name() or leave.approver.username) if leave.approver else "—"
        working_days = leave_services.calculate_working_days(leave.start_date, leave.end_date)

        leave_type = LeaveType.objects.filter(code__iexact=leave.leave_type).first()
        year = leave.start_date.year
        statement = "Balance details are available in your leave history."
        if leave_type:
            bal = EnterpriseLeaveBalance.objects.filter(user=user, leave_type=leave_type, year=year).first()
            if bal:
                statement = (
                    f"This {working_days}-working-day {leave_type.name} leave is reflected in your {year} balance. "
                    f"Remaining available: {bal.available_days} of {bal.entitled_days} day(s)."
                )

        ctx = common_context(document_number)
        ctx.update({
            "leave": leave,
            "employee_name": employee_name,
            "department": getattr(user, "department", "") or "",
            "approver_name": approver_name,
            "working_days": working_days,
            "balance_statement": statement,
        })
        return ctx

    def _render_leave_pdf(self, request, template_name, doc_type):
        from django.http import HttpResponse
        from documents.models import IssuedDocument
        from documents.pdf import render_pdf
        from documents.services import issue_document

        leave = self.get_object()
        if leave.status != Leave.Status.APPROVED:
            return Response({"detail": "Available only for approved leave."}, status=status.HTTP_409_CONFLICT)

        actors = [leave.user.get_full_name() or leave.user.username]
        if leave.approver:
            actors.append(leave.approver.get_full_name() or leave.approver.username)
        doc = issue_document(
            doc_type, "leave", leave,
            subject=f"{leave.get_leave_type_display()} {leave.start_date}..{leave.end_date}",
            issued_by=request.user, actors=actors,
        )
        ctx = self._leave_pdf_context(leave, doc.document_number)
        pdf_bytes = render_pdf(template_name, ctx)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{doc.document_number}.pdf"'
        return response

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        from documents.models import IssuedDocument
        return self._render_leave_pdf(request, "pdf/leave_application.html", IssuedDocument.DocType.LEAVE_APPLICATION)

    @action(detail=True, methods=["get"], url_path="certificate")
    def certificate(self, request, pk=None):
        from documents.models import IssuedDocument
        return self._render_leave_pdf(request, "pdf/leave_certificate.html", IssuedDocument.DocType.LEAVE_CERTIFICATE)


class DepartmentGovernanceView(views.APIView):
    """
    GET /api/v1/leaves/departments/governance/

    Who answers for each department, as one set of figures
    (Phase DEPARTMENT-GOVERNANCE-HARDENING). The dashboard banner, the
    department analytics page and the admin Departments page all read THIS,
    rather than each counting for itself - a green health check beside a red
    banner teaches people to believe neither.

    Not open to every employee: it is a list of management gaps, and the people
    who need it are the ones who can act on it or who read department analytics.
    An employee sees the consequence instead, in the refusal message when they
    try to raise departmental work.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from users.roles import has_org_wide_read, is_department_head

        user = request.user
        if not (has_org_wide_read(user) or is_department_head(user)):
            raise PermissionDenied(
                "Department governance is for the people who can act on it.")
        from .governance import governance_summary

        return Response(governance_summary())


class LeaveBalanceView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.utils import timezone
        from . import category_engine
        # Self-healing + category-aware: re-resolve the user's category (auto-
        # promotes as service crosses thresholds) and generate this year's
        # allocation rows from the DB entitlement matrix, so balances are never
        # empty and always reflect the user's current category.
        year = timezone.localdate().year
        category_engine.ensure_category_balances(request.user, year)
        balances = LeaveBalance.objects.filter(user=request.user, year=year)
        serializer = LeaveBalanceSerializer(balances, many=True)
        return Response(serializer.data)


class LeaveCalendarView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        leaves = Leave.objects.filter(status=Leave.Status.APPROVED)
        serializer = LeaveSerializer(leaves, many=True)
        return Response(serializer.data)


class CalendarEventsView(views.APIView):
    """
    GET /api/v1/leaves/calendar-events/?start=YYYY-MM-DD&end=YYYY-MM-DD

    Festivals and observances for a date range, for the Nepali calendar grid.

    Range-based rather than year-based on purpose: a Bikram Sambat month can
    straddle two Gregorian years (Poush runs across December into January), so
    a year parameter would silently drop everything on the far side of the
    boundary — the bug the holiday endpoint already has to be worked around for.
    """
    permission_classes = [IsAuthenticated]

    # A month's grid is at most 42 days; the cap stops a caller pulling the
    # whole table through a calendar endpoint.
    MAX_DAYS = 400

    def get(self, request):
        from datetime import date as _date, timedelta
        from django.utils import timezone as _tz
        from .models import CalendarEvent
        from .serializers import CalendarEventSerializer

        def parse(value, fallback):
            try:
                return _date.fromisoformat(value)
            except (TypeError, ValueError):
                return fallback

        today = _tz.localdate()
        start = parse(request.query_params.get("start"), today - timedelta(days=45))
        end = parse(request.query_params.get("end"), today + timedelta(days=45))
        if end < start:
            start, end = end, start
        if (end - start).days > self.MAX_DAYS:
            end = start + timedelta(days=self.MAX_DAYS)

        rows = (CalendarEvent.objects
                .filter(is_active=True, date__gte=start, date__lte=end)
                .order_by("date", "-is_public_holiday", "name"))
        return Response(CalendarEventSerializer(rows, many=True).data)


class LeavePolicyView(views.APIView):
    """GET /api/v1/leaves/leave-policy/ - the official NIF category-based leave
    entitlements, driven LIVE from the EntitlementRule matrix (the same source the
    balance cards derive from), so the policy text can never drift from the actual
    balances. Also returns the caller's resolved category to highlight/expand it."""
    permission_classes = [IsAuthenticated]

    # Probation first: the list reads as a career, not an alphabet.
    CATEGORY_ORDER = ["PROBATION", "C", "B", "A", "D", "E"]
    CATEGORY_LABEL = {
        "PROBATION": "Probation — first 3 months",
        "C": "Post-Probation — 3 months to 1 year of service",
        "B": "Permanent Staff — 1 to 3 years of service",
        "A": "Permanent Staff — more than 3 years of service",
        # Split: these two were one line reading "Interns and Volunteers",
        # which described an entitlement neither of them actually has.
        "D": "Interns",
        "E": "Volunteers",
    }
    TYPE_ORDER = ["annual", "sick", "maternity", "paternity", "compensatory"]

    def _value(self, code, days, applicable, by_arrangement=False):
        if not applicable:
            return "Not applicable"
        # Offered, but with no fixed yearly figure — the policy's wording for an
        # intern's maternity and paternity leave. Printing "0 days" here would
        # state the opposite of what it means.
        if by_arrangement:
            return "As per organisation policy"
        d = f"{days:g}"
        if code == "compensatory":
            return "Granted for approved weekend/holiday work"
        if code in ("maternity", "paternity"):
            return f"{d} days"
        if code == "annual":
            return f"{d} working days/year"
        return f"{d} days/year"

    def get(self, request):
        from . import category_engine
        from .models import EntitlementRule

        your_category, _flag = category_engine.resolve_and_cache(request.user)

        by_cat = {}
        for r in EntitlementRule.objects.select_related("leave_type").all():
            by_cat.setdefault(r.category, {})[r.leave_type.code.lower()] = r

        categories = []
        for key in self.CATEGORY_ORDER:
            rows = by_cat.get(key)
            if not rows:
                continue
            items = []
            for code in self.TYPE_ORDER:
                r = rows.get(code)
                if r is None:
                    continue
                items.append({
                    "code": code,
                    "leave_type": r.leave_type.name,
                    "days": float(r.entitlement_days),
                    "applicable": r.applicable,
                    "by_arrangement": r.by_arrangement,
                    "value": self._value(code, float(r.entitlement_days),
                                         r.applicable, r.by_arrangement),
                })
            categories.append({"key": key, "label": self.CATEGORY_LABEL.get(key, key), "items": items})

        return Response({"your_category": your_category, "categories": categories})


class MyEntitlementsView(views.APIView):
    """
    GET /api/v1/leaves/my-entitlements/ - category-aware balance view for the
    dashboard: resolved category + service, one card per applicable leave type
    (with total/used/remaining), and the compensatory ledger summary.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.utils import timezone
        from . import category_engine
        user = request.user
        year = timezone.localdate().year

        category, flag = category_engine.resolve_and_cache(user)
        category_engine.ensure_category_balances(user, year)

        rules = {r.leave_type.code.upper(): r for r in category_engine.entitlements_for_user(user)}
        balances = {b.leave_type: b for b in LeaveBalance.objects.filter(user=user, year=year)}

        cards = []
        for code in category_engine.BALANCE_LEAVE_CODES:
            rule = rules.get(code)
            if rule is None:
                continue  # not applicable to this user (e.g. maternity for interns)
            bal = balances.get(code.lower())
            total = float(bal.total_allocated) if bal else float(rule.entitlement_days)
            used = float(bal.used_so_far) if bal else 0.0
            cards.append({
                "code": code.lower(),
                "name": rule.leave_type.name,
                "color": rule.leave_type.display_color,
                # A by-arrangement entitlement has no allocation to divide, so
                # the card carries the flag and the client renders a note in
                # place of the numbers rather than "0 of 0 remaining".
                "by_arrangement": rule.by_arrangement,
                "total": total,
                "used": used,
                "remaining": max(0.0, total - used),
                "is_working_day_based": rule.is_working_day_based,
            })

        comp = category_engine.comp_summary(user)
        comp_applicable = "COMPENSATORY" in rules
        return Response({
            "category": category,
            "category_label": user.get_leave_category_display() if category else None,
            "category_flag": flag,
            "employment_type": user.employment_type,
            "employment_type_label": user.get_employment_type_display(),
            "service_label": category_engine.service_label(user.date_of_joining),
            "service_months": category_engine.service_months(user.date_of_joining),
            "balances": cards,
            "compensatory": {
                "applicable": comp_applicable,
                "earned": float(comp["earned"]),
                "used": float(comp["used"]),
                "available": float(comp["available"]),
                "pending": float(comp["pending"]),
            },
            "applicable_types": category_engine.applicable_type_codes(user),
        })


class HRCategoryReviewView(views.APIView):
    """GET /api/v1/leaves/hr/category-review/ - users whose category resolution
    raised a flag (fallback / auto-transition / missing data), for HR to action."""
    permission_classes = [IsApproverOrAdmin]

    def get(self, request):
        from . import category_engine
        rows = []
        for user in User.objects.filter(is_active=True).exclude(category_flag__isnull=True).exclude(category_flag=""):
            category_engine.resolve_and_cache(user)
            if not user.category_flag:
                continue
            rows.append({
                "id": str(user.id),
                "name": user.get_full_name() or user.username,
                "employee_id": user.employee_id,
                "employment_type": user.employment_type,
                "employment_type_label": user.get_employment_type_display(),
                "leave_category": user.leave_category,
                "category_label": user.get_leave_category_display() if user.leave_category else None,
                "service_label": category_engine.service_label(user.date_of_joining),
                "flag": user.category_flag,
            })
        return Response({"count": len(rows), "results": rows})


class CompensatoryView(views.APIView):
    """
    GET  /api/v1/leaves/compensatory/            - my ledger + summary
    POST /api/v1/leaves/compensatory/            - HR manual grant (approver/admin)
         body: {user_id, days, source_date, note}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from . import category_engine
        from .models import CompensatoryLedger
        entries = CompensatoryLedger.objects.filter(user=request.user)[:100]
        return Response({
            "summary": {k: float(v) for k, v in category_engine.comp_summary(request.user).items()},
            "entries": [{
                "id": str(e.id), "entry_type": e.entry_type, "days": float(e.days),
                "source": e.source, "status": e.status,
                "source_date": e.source_date, "note": e.note,
                "created_at": e.created_at,
            } for e in entries],
        })

    def post(self, request):
        from django.utils import timezone
        from .models import CompensatoryLedger
        actor = request.user
        if actor.role not in [User.Roles.APPROVER, User.Roles.ADMIN]:
            raise PermissionDenied("Only HR or Admin can grant compensatory days.")
        user_id = request.data.get("user_id")
        try:
            target = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            days = float(request.data.get("days", 1))
        except (TypeError, ValueError):
            return Response({"detail": "days must be a number."}, status=status.HTTP_400_BAD_REQUEST)
        if days <= 0:
            return Response({"detail": "days must be positive."}, status=status.HTTP_400_BAD_REQUEST)

        entry = CompensatoryLedger.objects.create(
            user=target, entry_type=CompensatoryLedger.EntryType.EARN,
            days=days, source=CompensatoryLedger.Source.HR_GRANT,
            status=CompensatoryLedger.Status.CONFIRMED,  # HR grants are authoritative
            source_date=request.data.get("source_date") or timezone.localdate(),
            approved_by=actor, note=(request.data.get("note") or "")[:255],
        )
        log_action(actor, AuditLog.Action.CREATE, instance=entry,
                   changes={"event": "COMP_GRANT", "user": str(target.id), "days": days}, request=request)
        return Response({"id": str(entry.id), "detail": "Compensatory days granted."}, status=status.HTTP_201_CREATED)


# ===========================================================================
# Phase 4 - Enterprise Leave Records views
# ===========================================================================
from django.db.models import Q
from rest_framework import viewsets as drf_viewsets
from rest_framework.views import APIView
from users.models import User
from users.admin_views import IsAdminOrSuperuser
from . import services
from .filters import LeaveDayRecordFilter
from .models import (
    EnterpriseLeaveBalance,
    Holiday,
    LeaveDayRecord,
    LeaveType,
    MonthlyLeaveSummary,
    WeeklyLeaveSummary,
)
from .serializers import (
    EnterpriseLeaveBalanceSerializer,
    HolidaySerializer,
    LeaveDayRecordSerializer,
    LeaveHistorySerializer,
    LeaveTypeSerializer,
    MonthlyLeaveSummarySerializer,
    UserMiniSerializer,
    WeeklyLeaveSummarySerializer,
)


def _is_privileged(user):
    """Admin/checker/approver may view records beyond their own."""
    return user.role in [User.Roles.ADMIN, User.Roles.CHECKER, User.Roles.APPROVER]


def _dept_scope_q(user, prefix="user__"):
    """Q limiting a queryset to `user`'s own department (via department_ref, then
    the legacy department string; falls back to self if no department is set)."""
    if user.department_ref_id:
        return Q(**{f"{prefix}department_ref_id": user.department_ref_id})
    if user.department:
        return Q(**{f"{prefix}department__iexact": user.department})
    return Q(**{f"{prefix}id": user.id})


def scope_records(user, qs, prefix="user__"):
    """
    Scope a per-user queryset to what `user` is allowed to see (M7):
      * Admin / HR (approver) -> org-wide (unchanged),
      * Dept Head (checker)   -> OWN department only,
      * everyone else         -> self only.
    `prefix` is the relation path to the owning user ("user__" for record tables,
    "" when the queryset is the User model itself). Consistent with
    LeaveViewSet.get_queryset and AttendanceListView.
    """
    if _org_wide_read(user):  # Board sees this read-only (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).
        return qs
    if user.role == User.Roles.CHECKER:
        return qs.filter(_dept_scope_q(user, prefix))
    field = prefix[:-2] if prefix.endswith("__") else prefix
    return qs.filter(**{(field or "id"): (user if field else user.id)})


def can_view_user(requester, target_id):
    """Whether `requester` may target another user's records via ?user_id. Admin/HR:
    anyone; Dept Head: same department; others: only themselves. No data leak on a
    cross-department id (caller should 403)."""
    if str(requester.id) == str(target_id):
        return True
    if _org_wide_read(requester):  # Board sees this read-only (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).
        return True
    if requester.role == User.Roles.CHECKER:
        target = User.objects.filter(pk=target_id).first()
        if target is None:
            return False
        if requester.department_ref_id and target.department_ref_id == requester.department_ref_id:
            return True
        if requester.department and (target.department or "").lower() == requester.department.lower():
            return True
    return False


class LeaveTypeViewSet(drf_viewsets.ReadOnlyModelViewSet):
    """Active leave types for all authenticated users (admin CRUD lives in admin_views)."""
    permission_classes = [IsAuthenticated]
    serializer_class = LeaveTypeSerializer
    queryset = LeaveType.objects.filter(is_active=True)


class HolidayViewSet(drf_viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = HolidaySerializer

    def get_queryset(self):
        qs = Holiday.objects.filter(is_active=True)
        year = self.request.query_params.get("year")
        if year:
            qs = qs.filter(date__year=year)
        return qs


class LeaveDayRecordViewSet(drf_viewsets.ReadOnlyModelViewSet):
    """Per-day records, filterable by user_id / start / end."""
    permission_classes = [IsAuthenticated]
    serializer_class = LeaveDayRecordSerializer
    filterset_class = LeaveDayRecordFilter
    ordering_fields = ['date', 'status', 'year', 'month', 'week_number']

    def get_queryset(self):
        user = self.request.user
        qs = LeaveDayRecord.objects.select_related("leave_type", "user")
        qs = scope_records(user, qs)  # M7: checker limited to own department
        target = self.request.query_params.get("user_id")
        if target:
            if not can_view_user(user, target):
                raise PermissionDenied("You may only view records for your own department.")
            qs = qs.filter(user_id=target)
        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return qs


class EnterpriseLeaveBalanceViewSet(drf_viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EnterpriseLeaveBalanceSerializer

    def get_queryset(self):
        user = self.request.user
        qs = EnterpriseLeaveBalance.objects.select_related("leave_type", "user")
        if user.role != User.Roles.ADMIN:
            qs = qs.filter(user=user)
        elif self.request.query_params.get("user_id"):
            qs = qs.filter(user_id=self.request.query_params["user_id"])
        year = self.request.query_params.get("year")
        if year:
            qs = qs.filter(year=year)
        return qs


class WeeklyLeaveSummaryViewSet(drf_viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = WeeklyLeaveSummarySerializer

    def get_queryset(self):
        user = self.request.user
        qs = scope_records(user, WeeklyLeaveSummary.objects.all())  # M7
        target = self.request.query_params.get("user_id")
        if target:
            if not can_view_user(user, target):
                raise PermissionDenied("You may only view records for your own department.")
            qs = qs.filter(user_id=target)
        for field in ("year", "week_number"):
            value = self.request.query_params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        return qs


class MonthlyLeaveSummaryViewSet(drf_viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MonthlyLeaveSummarySerializer

    def get_queryset(self):
        user = self.request.user
        qs = scope_records(user, MonthlyLeaveSummary.objects.all())  # M7
        target = self.request.query_params.get("user_id")
        if target:
            if not can_view_user(user, target):
                raise PermissionDenied("You may only view records for your own department.")
            qs = qs.filter(user_id=target)
        for field in ("year", "month"):
            value = self.request.query_params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        return qs


class MyLeaveHistoryView(APIView):
    """GET /api/v1/leaves/my-history/?year=2026 - rich per-year history."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = int(request.query_params.get("year", date.today().year))
        user = request.user
        payload = {
            "user": user,
            "year": year,
            "balances": EnterpriseLeaveBalance.objects.filter(
                user=user, year=year
            ).select_related("leave_type"),
            "recent_leaves": Leave.objects.filter(
                user=user, start_date__year=year
            ).order_by("-start_date")[:20],
            "monthly_summaries": MonthlyLeaveSummary.objects.filter(
                user=user, year=year
            ).order_by("month"),
        }
        return Response(LeaveHistorySerializer(payload, context={"request": request}).data)


class LeaveCalendarRecordsView(APIView):
    """
    GET /api/v1/leaves/calendar/?start=YYYY-MM-DD&end=YYYY-MM-DD&user_id=optional
    Returns LeaveDayRecords for calendar rendering (color-coded by leave type).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        qs = scope_records(user, LeaveDayRecord.objects.select_related("leave_type", "user"))  # M7
        target = request.query_params.get("user_id")
        if target:
            if not can_view_user(user, target):
                raise PermissionDenied("You may only view records for your own department.")
            qs = qs.filter(user_id=target)
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return Response(LeaveDayRecordSerializer(qs, many=True).data)


class TeamAttendanceView(APIView):
    """
    GET /api/v1/leaves/team-attendance/?department=CODE&month=YYYY-MM
    Team attendance heatmap for managers / HR (privileged roles only).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_privileged(request.user):
            return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)

        dept_code = request.query_params.get("department")
        month_param = request.query_params.get("month")
        if not month_param:
            return Response({"detail": "month=YYYY-MM is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            year, month = (int(x) for x in month_param.split("-"))
        except ValueError:
            return Response({"detail": "month must be YYYY-MM."}, status=status.HTTP_400_BAD_REQUEST)

        members = User.objects.filter(is_active=True)
        # M7: a Dept Head only ever sees their own department, regardless of the
        # ?department param. HR/Admin may filter across all departments.
        if request.user.role == User.Roles.CHECKER:
            members = members.filter(_dept_scope_q(request.user, prefix=""))
        if dept_code:
            members = members.filter(
                Q(department_ref__code__iexact=dept_code) | Q(department__iexact=dept_code)
            )

        # Biometric presence for the same month, folded in per member. Fetched
        # once (the whole dataset is one JSON) and matched by each user's
        # biometric_id. Best-effort: if the device service is unreachable the
        # leave columns still render and the biometric ones show "unavailable".
        from calendar import monthrange
        from attendance.biometric import _fetch_dataset, month_summary_for
        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        try:
            bio_dataset = _fetch_dataset()
        except Exception:  # noqa: BLE001 — any upstream/network issue is "unavailable"
            bio_dataset = None

        rows = []
        for member in members:
            summary = services.recompute_monthly_summary(member, year, month)
            bid = (member.biometric_id or "").strip()
            if not bid:
                biometric = {"linked": False}
            elif bio_dataset is None:
                biometric = {"linked": True, "available": False}
            else:
                bsum = month_summary_for(bio_dataset, bid, month_start, month_end)
                if bsum is None:
                    biometric = {"linked": True, "available": True, "found": False}
                else:
                    biometric = {"linked": True, "available": True, "found": True, **bsum}
            rows.append({
                "user": UserMiniSerializer(member).data,
                "working_days": summary.working_days,
                "approved_days": summary.approved_days,
                "pending_days": summary.pending_days,
                "attendance_percentage": summary.attendance_percentage,
                "by_type": summary.by_type,
                "biometric": biometric,
            })
        return Response({
            "department": dept_code, "year": year, "month": month,
            "biometric_available": bio_dataset is not None, "team": rows,
        })


class RecomputeBalanceView(APIView):
    """POST /api/v1/leaves/recompute-balance/  body: {user_id, year}  (admin only)."""
    permission_classes = [IsAdminOrSuperuser]

    def post(self, request):
        user_id = request.data.get("user_id")
        year = int(request.data.get("year", date.today().year))
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        balances = [
            services.recompute_leave_balance(target, lt, year)
            for lt in LeaveType.objects.filter(is_active=True)
        ]
        services.audit_log(
            request.user, "LEAVE_BALANCE_ADJUSTED", target=target,
            metadata={"year": year, "trigger": "manual_recompute"}, request=request,
        )
        return Response(EnterpriseLeaveBalanceSerializer(balances, many=True).data)


class YearEndCarryForwardView(APIView):
    """POST /api/v1/leaves/year-end-carry-forward/  body: {year}  (admin only)."""
    permission_classes = [IsAdminOrSuperuser]

    def post(self, request):
        year = int(request.data.get("year", date.today().year))
        processed = 0
        for member in User.objects.filter(is_active=True):
            services.process_year_end_carry_forward(member, year, actor=request.user)
            processed += 1
        return Response({"year": year, "users_processed": processed})


class MyLeaveReportView(APIView):
    """
    GET /api/v1/leaves/my-report/<period>/?year=YYYY
    Self-scoped PDF of the caller's weekly or monthly leave summaries, rendered
    with the NIF letterhead. period in {'weekly','monthly'}.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, period):
        from django.conf import settings
        from django.http import HttpResponse
        from django.utils import timezone
        from calendar import month_name
        from documents.pdf import logo_data_uri, render_pdf
        from config.nepali_dates import to_bs

        if period not in ("weekly", "monthly"):
            return Response({"detail": "period must be 'weekly' or 'monthly'."},
                            status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        year = int(request.query_params.get("year", timezone.localdate().year))
        rows, totals = [], {"working": 0, "approved": 0.0, "pending": 0.0}

        if period == "weekly":
            qs = WeeklyLeaveSummary.objects.filter(user=user, year=year).order_by("week_number")
            for s in qs:
                rows.append({
                    "label": f"Week {s.week_number}",
                    "sub": f"{s.week_start_date} – {s.week_end_date}",
                    "working": s.working_days, "approved": float(s.approved_days),
                    "pending": float(s.pending_days), "attendance": float(s.attendance_percentage),
                })
                totals["working"] += s.working_days
                totals["approved"] += float(s.approved_days)
                totals["pending"] += float(s.pending_days)
        else:
            qs = MonthlyLeaveSummary.objects.filter(user=user, year=year).order_by("month")
            for s in qs:
                rows.append({
                    "label": month_name[s.month], "sub": str(year),
                    "working": s.working_days, "approved": float(s.approved_days),
                    "pending": float(s.pending_days), "attendance": float(s.attendance_percentage),
                })
                totals["working"] += s.working_days
                totals["approved"] += float(s.approved_days)
                totals["pending"] += float(s.pending_days)

        now = timezone.localtime(timezone.now())
        ctx = {
            "logo": logo_data_uri(),
            "org": getattr(settings, "ORG_INFO", {}),
            "report_title": f"{period.capitalize()} Leave Report",
            "period_label": str(year),
            "employee_name": user.get_full_name() or user.username,
            "employee_id": user.employee_id or "—",
            "department": user.department_name or "—",
            "generated_ad": now.strftime("%Y-%m-%d %H:%M"),
            "generated_bs": to_bs(now.date()) or "—",
            "is_weekly": period == "weekly",
            "rows": rows,
            "totals": totals,
        }
        pdf = render_pdf("pdf/leave_summary_report.html", ctx)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{period}-leave-report-{year}.pdf"'
        return resp
