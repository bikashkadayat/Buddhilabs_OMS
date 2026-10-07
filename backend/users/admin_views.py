from rest_framework import generics, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from datetime import datetime
from audit.models import AuditLog
from audit.services import log_action
from . import login_security
from .models import User
from leaves.models import Leave, LeaveBalance
from leaves.serializers import LeaveSerializer, LeaveBalanceSerializer
from leaves.filters import LeaveFilter
from .serializers import UserSerializer, AdminUserCreateSerializer


class IsAdminOrSuperuser(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and (request.user.is_staff or request.user.is_superuser or request.user.role == 'admin')


class AdminUserViewSet(viewsets.ModelViewSet):
    """
    Admin-only user management (Phase 2.5). Mounted at both
    /api/v1/admin/users/ (legacy) and /api/v1/users/admin/users/ (spec).
    Every mutating action is transactional and audit-logged.
    """
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrSuperuser]
    search_fields = ['username', 'email', 'first_name', 'last_name', 'employee_id']
    filterset_fields = ['role', 'is_active']

    def get_queryset(self):
        return User.objects.select_related('department_ref').all().order_by('username')

    def get_serializer_class(self):
        # Create + edit go through the category-aware serializer so employment
        # type / joining date / gender changes re-resolve the category and rebuild
        # balances. List/retrieve use the read serializer.
        if self.action in ('create', 'update', 'partial_update'):
            return AdminUserCreateSerializer
        return UserSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_action(request.user, AuditLog.Action.CREATE, instance=serializer.instance,
                   changes={'event': 'USER_CREATED', 'role': serializer.instance.role}, request=request)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # Fields whose values must never be written into the audit log, even redacted
    # into a before/after pair.
    _AUDIT_SECRET_FIELDS = frozenset({"password", "generated_password"})

    @staticmethod
    def _audit_value(value):
        """Render a field value as something JSON-safe and human-readable."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    @transaction.atomic
    def perform_update(self, serializer):
        """Record WHAT changed, not merely that something did.

        This previously called log_action with no `changes`, and audit.services
        stores `changes or {}` — so the row named the actor, the timestamp and the
        target but was silent on the field. That is the weakest possible record on
        the most privileged surface in the system: this one endpoint sets
        `is_active`, `department_ref` and `employee_type`, and `employee_type` is
        read directly by memos.governance.can_manage_assignments and
        inventory.roles.is_supervisor, so editing it grants authority.

        Shaped to match the `change-role` action, which already records
        from/to — the two now read the same way in the log.
        """
        instance = serializer.instance
        fields = [f for f in sorted(serializer.validated_data)
                  if f not in self._AUDIT_SECRET_FIELDS]
        before = {f: self._audit_value(getattr(instance, f, None)) for f in fields}

        updated = serializer.save()

        after = {f: self._audit_value(getattr(updated, f, None)) for f in fields}
        changed = sorted(f for f in fields if before[f] != after[f])
        secrets_touched = sorted(set(serializer.validated_data) & self._AUDIT_SECRET_FIELDS)

        log_action(self.request.user, AuditLog.Action.UPDATE, instance=updated,
                   changes={
                       "event": "USER_UPDATED",
                       "fields": changed,
                       "from": {f: before[f] for f in changed},
                       "to": {f: after[f] for f in changed},
                       # Named but never valued, so a password reset is still visible
                       # in the trail without the log becoming a place secrets live.
                       "secrets_set": secrets_touched,
                   },
                   request=self.request)

    def destroy(self, request, *args, **kwargs):
        """
        Permanently delete a user — Admin only, and only when the account is
        INACTIVE. Guards protect against locking the org out or stranding work:
          * cannot delete your own account
          * cannot delete an ACTIVE user (deactivate first) -> 409
          * cannot delete the last remaining active Admin -> 409
          * cannot delete the sole approver of pending memos -> 409
        """
        instance = self.get_object()

        if instance.id == request.user.id:
            return Response({'detail': 'You cannot delete your own account.'},
                            status=status.HTTP_409_CONFLICT)

        if instance.is_active:
            return Response({'detail': 'Deactivate the user before deleting.'},
                            status=status.HTTP_409_CONFLICT)

        is_admin = instance.is_staff or instance.is_superuser or instance.role == User.Roles.ADMIN
        if is_admin:
            other_admins = User.objects.exclude(id=instance.id).filter(
                Q(is_active=True) & (Q(is_staff=True) | Q(is_superuser=True) | Q(role=User.Roles.ADMIN))
            )
            if not other_admins.exists():
                return Response({'detail': 'Cannot delete the last remaining active Admin.'},
                                status=status.HTTP_409_CONFLICT)

        # Routing lives in the approval matrix now, not in a current_approver
        # column: a user is "the approver" of a memo when they hold an Approver
        # step on it that has not yet been actioned. Filtered in a single
        # filter() call so both conditions apply to the SAME step - split across
        # two calls, Django joins the step table twice and would match a memo
        # where this user holds any step and some other step is outstanding.
        from memos.models import Memo, MemoWorkflowStep
        pending_memos = Memo.objects.filter(
            workflow_steps__assignee=instance,
            workflow_steps__role_type=MemoWorkflowStep.RoleType.APPROVER,
            workflow_steps__status__in=[
                MemoWorkflowStep.StepStatus.ACTIVE,
                MemoWorkflowStep.StepStatus.PENDING,
            ],
        ).exclude(status__in=Memo.TERMINAL_STATUSES).distinct()
        if pending_memos.exists():
            has_other_approver = User.objects.filter(
                is_active=True, role__in=[User.Roles.APPROVER, User.Roles.ADMIN],
            ).exclude(id=instance.id).exists()
            if not has_other_approver:
                return Response(
                    {'detail': f'This user is the sole approver of {pending_memos.count()} pending '
                               f'memo(s). Reassign or resolve them before deleting.'},
                    status=status.HTTP_409_CONFLICT,
                )

        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    def perform_destroy(self, instance):
        # A permanent user removal intentionally clears their own leave history.
        # The cascade collector re-instantiates each Leave, so the pre_delete
        # guard (protect_approved_leave) would raise ProtectedError on approved
        # leaves. Delete the user's leaves explicitly first with the guard flag
        # set so the cascade that follows has nothing left to protect.
        for leave in Leave.objects.filter(user=instance):
            leave._allow_hard_delete = True
            leave.delete()

        # Snapshot the profile photo file so we can clean it up after the row is
        # gone (FileField.delete() removes the file from storage).
        photo = instance.profile_photo if instance.profile_photo else None

        log_action(self.request.user, AuditLog.Action.DELETE, instance=instance,
                   changes={'event': 'USER_DELETED', 'email': instance.email, 'role': instance.role},
                   request=self.request)
        instance.delete()

        if photo:
            photo.delete(save=False)

    @action(detail=True, methods=['post'], url_path='reset-password')
    @transaction.atomic
    def reset_password(self, request, pk=None):
        import secrets
        user = self.get_object()
        new_password = request.data.get('password') or secrets.token_urlsafe(9)
        user.set_password(new_password)
        user.must_change_password = True  # force change on next login
        user.save(update_fields=['password', 'must_change_password'])
        log_action(request.user, AuditLog.Action.UPDATE, instance=user,
                   changes={'event': 'PASSWORD_RESET'}, request=request)
        return Response({'detail': 'Password reset.', 'generated_password': new_password})

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def deactivate(self, request, pk=None):
        user = self.get_object()
        if user.id == request.user.id:
            return Response({'detail': 'You cannot deactivate your own account.'}, status=status.HTTP_400_BAD_REQUEST)
        user.is_active = False
        user.save(update_fields=['is_active'])
        log_action(request.user, AuditLog.Action.UPDATE, instance=user,
                   changes={'event': 'USER_DEACTIVATED'}, request=request)
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def activate(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=['is_active'])
        log_action(request.user, AuditLog.Action.UPDATE, instance=user,
                   changes={'event': 'USER_ACTIVATED'}, request=request)
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=['post'], url_path='unlock')
    def unlock(self, request, pk=None):
        """Clear a login lockout (Phase 11, audit finding H2).

        The escape hatch that makes lockout acceptable to enable at all: without
        an unlock path, ten fat-fingered attempts before a board meeting mean a
        fifteen-minute wait and a support call. Audited, because clearing
        somebody's failed-attempt history is itself a security-relevant act.
        """
        user = self.get_object()
        # The TARGET USER's organization, not the caller's context (Phase S3):
        # a lockout counter is namespaced per tenant, so unlocking has to name
        # the same tenant the failures were recorded under.
        org = user.organization_id
        failures = login_security.failure_count(user.email, org)
        was_locked, _remaining = login_security.is_locked(user.email, org)
        login_security.unlock(user.email, org)
        log_action(request.user, AuditLog.Action.UPDATE, instance=user,
                   changes={'event': 'LOGIN_UNLOCKED', 'was_locked': was_locked,
                            'cleared_failures': failures},
                   request=request)
        return Response({'detail': 'Login lockout cleared.',
                         'was_locked': was_locked,
                         'cleared_failures': failures})

    @action(detail=True, methods=['post'], url_path='change-role')
    @transaction.atomic
    def change_role(self, request, pk=None):
        user = self.get_object()
        new_role = request.data.get('role')
        if new_role not in User.Roles.values:
            return Response({'detail': 'Invalid role.'}, status=status.HTTP_400_BAD_REQUEST)
        old_role = user.role
        user.role = new_role
        user.save(update_fields=['role'])
        log_action(request.user, AuditLog.Action.UPDATE, instance=user,
                   changes={'event': 'ROLE_CHANGED', 'from': old_role, 'to': new_role}, request=request)
        return Response(UserSerializer(user).data)


class AdminStatsView(generics.GenericAPIView):
    permission_classes = [IsAdminOrSuperuser]

    def get(self, request):
        now = timezone.now()
        current_year = now.year

        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        total_leaves = Leave.objects.count()
        pending_leaves = Leave.objects.filter(status__in=['pending', 'pending_hr']).count()
        approved_leaves = Leave.objects.filter(status='approved').count()
        rejected_leaves = Leave.objects.filter(status='rejected').count()

        # Per-department breakdown (Phase 2.6 dashboard). Only a handful of
        # departments, so a few small counts each is fine.
        from leaves.models import Department
        by_department = []
        for d in Department.objects.filter(is_active=True).order_by('name'):
            members = User.objects.filter(department_ref=d)
            dept_leaves = Leave.objects.filter(user__department_ref=d, is_deleted=False)
            by_department.append({
                'id': str(d.id),
                'department': d.name,
                'code': d.code,
                'employee_count': members.count(),
                'active_employees': members.filter(is_active=True).count(),
                'leave_requests': dept_leaves.count(),
                'pending_reviews': dept_leaves.filter(status__in=['pending', 'pending_hr']).count(),
            })

        recent_leaves = Leave.objects.order_by('-created_at')[:10]
        recent_leaves_data = []
        for leave in recent_leaves:
            recent_leaves_data.append({
                'id': str(leave.id),
                'user_name': leave.user.get_full_name() or leave.user.username,
                'leave_type': leave.get_leave_type_display(),
                'start_date': str(leave.start_date),
                'end_date': str(leave.end_date),
                'status': leave.status,
                'created_at': leave.created_at.isoformat(),
            })

        return Response({
            'total_users': total_users,
            'active_users': active_users,
            'total_leaves': total_leaves,
            'pending_leaves': pending_leaves,
            'approved_leaves': approved_leaves,
            'rejected_leaves': rejected_leaves,
            'by_department': by_department,
            'recent_leaves': recent_leaves_data,
        })


class AdminLeaveViewSet(viewsets.ModelViewSet):
    serializer_class = LeaveSerializer
    permission_classes = [IsAdminOrSuperuser]
    filterset_class = LeaveFilter
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'reason']
    ordering_fields = ['created_at', 'start_date', 'end_date', 'status']

    def get_queryset(self):
        return Leave.objects.select_related('user', 'approver').order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save()
        log_action(self.request.user, AuditLog.Action.CREATE, instance=serializer.instance, request=self.request)

    def perform_update(self, serializer):
        serializer.save()
        log_action(self.request.user, AuditLog.Action.UPDATE, instance=serializer.instance, request=self.request)

    def perform_destroy(self, instance):
        log_action(self.request.user, AuditLog.Action.DELETE, instance=instance, request=self.request)
        instance.delete()


class AdminBalanceViewSet(viewsets.ModelViewSet):
    serializer_class = LeaveBalanceSerializer
    permission_classes = [IsAdminOrSuperuser]

    def get_queryset(self):
        return LeaveBalance.objects.select_related('user').order_by('user__username')

    def perform_create(self, serializer):
        serializer.save()
        log_action(self.request.user, AuditLog.Action.CREATE, instance=serializer.instance, request=self.request)

    def perform_update(self, serializer):
        serializer.save()
        log_action(self.request.user, AuditLog.Action.UPDATE, instance=serializer.instance, request=self.request)

    def perform_destroy(self, instance):
        log_action(self.request.user, AuditLog.Action.DELETE, instance=instance, request=self.request)
        instance.delete()