"""
The task API.

Every list menu is `?scope=<name>` resolved by `_apply_scope`. A menu is
therefore a route plus a scope name, not a new list implementation with its own
filtering bugs, and the definition of "assigned to me" lives in exactly one
place. The module's menus — My Tasks, Assigned By Me, Team Tasks, Due Today,
Overdue, Completed — are those scopes.

The dashboard endpoint counts over the SAME visible queryset the lists use, so a
tile can never advertise a task the caller cannot open.
"""
import csv
import datetime
import logging
import uuid

from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status as http_status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    NotFound, PermissionDenied, ValidationError,
)
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.client_ip import client_ip
from config.uploads import harden_file_response
from notifications.models import Category

from . import analytics
from . import board as board_config
from . import permissions as perms
from . import planner
from . import workflow
from .filters import TaskFilterSet
from .models import (
    DepartmentGoal,
    Task, TaskAssignee, TaskAttachment, TaskAttachmentDownload,
    TaskChecklistGroup, TaskChecklistItem, TaskComment, TaskCommentMention,
    TaskGroup, TaskSubtask, TaskTemplate,
)
from .serializers import (
    DepartmentGoalSerializer, DependencyWriteSerializer, TaskDependencySerializer,
    ApplyTemplateSerializer, AssigneeListSerializer, AttachmentLinkSerializer,
    BulkActionSerializer, CreateTaskGroupSerializer, TaskGroupSerializer,
    ChecklistTickSerializer, ChecklistWriteSerializer, CommentWriteSerializer,
    EvidenceFlagSerializer, ProgressSerializer, RemarksSerializer,
    RequiredReasonSerializer, SaveAsTemplateSerializer, SubmitReviewSerializer,
    SubtaskCompleteSerializer, SubtaskReorderSerializer, SubtaskWriteSerializer,
    TaskSubtaskSerializer,
    TaskAssigneeSerializer, TaskAttachmentDownloadSerializer,
    TaskAttachmentSerializer, TaskAuditLogSerializer,
    TaskChecklistGroupSerializer, TaskChecklistItemSerializer,
    TaskCommentSerializer, TaskDetailSerializer, TaskListSerializer,
    TaskTemplateDetailSerializer, TaskTemplateListSerializer,
    TaskTemplateWriteSerializer, TaskWriteSerializer,
)
from .services import (
    generate_task_number, notify_user, recalculate_progress, record_audit,
    resolve_department, subtask_tally as recalc_tally, user_snapshot,
    validate_task_attachment,
)
from tenancy.stamping import stamp_all

logger = logging.getLogger("tasks")


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _person(user):
    """A user as the create form's pickers carry them."""
    if user is None:
        return None
    return {"id": str(user.id), "full_name": user.get_full_name() or user.username}


def _default_reviewer(user):
    """
    The caller's department head, or None.

    None when there is no head, and none when the caller IS the head: a reviewer
    who is also the assignee is refused, and the assignee defaults to the caller.
    """
    from .services import department_head

    head = department_head(getattr(user, "department_ref_id", None))
    if head is None or head.id == user.id:
        return None
    return _person(head)



def _first_message(exc):
    """
    One readable sentence out of a DRF exception.

    `detail` may be a string, a list, or a dict of lists depending on where the
    error came from, and str() on the latter two produces a Python repr — which
    is what a user would otherwise read in a bulk-operation failure list.
    """
    detail = getattr(exc, "detail", None) or str(exc)
    while isinstance(detail, dict):
        detail = next(iter(detail.values()), "")
    if isinstance(detail, (list, tuple)):
        detail = detail[0] if detail else ""
    return str(detail)


class TaskViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, perms.CanCreateTask, perms.CanViewTask]
    filterset_class = TaskFilterSet
    # Phase T4.10 adds the reviewer and the template name. Both are things
    # people search by out loud — "the onboarding one", "whatever Sanjaya is
    # reviewing" — and neither was findable before.
    search_fields = ["task_number", "title", "description", "department_name",
                     "reviewer_name", "created_by_name", "template__name"]
    ordering_fields = ["created_at", "due_date", "priority", "status", "task_number",
                       "progress_percent"]
    ordering = ["-created_at"]

    # ------------------------------------------------------------------
    # Queryset, visibility and scopes
    # ------------------------------------------------------------------
    def get_queryset(self):
        queryset = (
            Task.objects
            .select_related("created_by", "reviewer", "department", "template")
            .prefetch_related(
                Prefetch("assignees",
                         queryset=TaskAssignee.objects.select_related("user")),
                "checklist",
                # Groups with their items, so the detail page renders sections
                # without a query per section.
                Prefetch("checklist_groups",
                         queryset=TaskChecklistGroup.objects.prefetch_related(
                             "items")),
                Prefetch("attachments",
                         queryset=TaskAttachment.objects.prefetch_related(
                             "downloads")),
                # Comments with their replies and both sides' mentions. One
                # prefetch for the whole thread rather than one per comment.
                Prefetch("comments",
                         queryset=TaskComment.objects.select_related("author")
                         .prefetch_related("mentions",
                                           "replies__author",
                                           "replies__mentions")),
                "audit_entries__actor",
                "dependencies__depends_on", "dependents__task",
                # Phase TASK-AUTOSAVE-AND-SUBTASKS. Counts, progress precedence
                # and the open-subtask gates all read these rows.
                Prefetch("subtasks",
                         queryset=TaskSubtask.objects.select_related("assignee")),
            )
            # Phase T2.8 card counts. Annotated with distinct=True for the same
            # reason the dashboard aggregates are: `visible` LEFT JOINs the
            # assignee table, and without it a task with three assignees would
            # report three times its real comment count.
            .annotate(
                comment_count=Count("comments", distinct=True),
                attachment_count=Count(
                    "attachments", distinct=True,
                    filter=Q(attachments__is_removed=False,
                             attachments__is_evidence=False)),
                evidence_count=Count(
                    "attachments", distinct=True,
                    filter=Q(attachments__is_removed=False,
                             attachments__is_evidence=True)),
                # Phase TASK-GOVERNANCE-HARDENING. How many prerequisites are
                # still in the way, so a board card can say "waiting on 2"
                # without a query per card. Finished AND cancelled prerequisites
                # are excluded here for the same reason
                # TaskDependency.is_satisfied excludes them.
                blocked_by_count=Count(
                    "dependencies", distinct=True,
                    filter=~Q(dependencies__depends_on__status__in=(
                        Task.Status.COMPLETED, Task.Status.CLOSED,
                        Task.Status.CANCELLED))),
            )
        )
        visible = perms.visible_task_filter(self.request.user)
        if visible is not None:
            queryset = queryset.filter(visible)
        # Phase T4.8: archived work is FILED, not deleted. It leaves the default
        # LIST and stays reachable at ?archived=1, in search, in reports and on
        # its own page.
        #
        # Applied to the list action ONLY. `get_queryset()` is also what
        # `get_object()` looks in, so filtering here unconditionally would 404
        # an archived task's own detail page — and a record you cannot open is
        # deleted, whatever the field is called.
        if (self.action == "list"
                and self.request.query_params.get("archived") not in ("1", "true")):
            queryset = queryset.filter(archived_at__isnull=True)
        return self._apply_scope(queryset).distinct()

    def _apply_scope(self, queryset):
        user = self.request.user
        scope = self.request.query_params.get("scope", "all")
        today = timezone.localdate()

        if scope == "mine":
            # "My Tasks" — everything assigned to me that is still live.
            return queryset.filter(assignees__user=user,
                                   status__in=Task.ACTIVE_STATUSES)
        if scope == "assigned_by_me":
            return queryset.filter(Q(created_by=user) | Q(reviewer=user))
        if scope == "team":
            return self._team_queryset(queryset)
        if scope == "due_today":
            return queryset.filter(assignees__user=user, due_date=today,
                                   status__in=Task.OPEN_STATUSES)
        if scope == "overdue":
            # Everything overdue that I can see, not only my own: a department
            # head opening Overdue wants their team's, and the queryset is
            # already scoped to what they may read.
            return queryset.filter(due_date__lt=today, status__in=Task.OPEN_STATUSES)
        if scope == "completed":
            return queryset.filter(status__in=[Task.Status.COMPLETED,
                                               Task.Status.CLOSED])
        if scope == "pending_review":
            return queryset.filter(status=Task.Status.UNDER_REVIEW)
        if scope == "drafts":
            return queryset.filter(created_by=user, status=Task.Status.DRAFT)
        if scope == "needs_me":
            # The single queue behind "Waiting for me". Three things can want
            # something from a person under this workflow, and these are all of
            # them: a task to accept, work in flight that is mine, and a review
            # or closure sitting with me.
            queue = Q(assignees__user=user,
                      status__in=[Task.Status.ASSIGNED, Task.Status.ACCEPTED,
                                  Task.Status.IN_PROGRESS])
            queue |= Q(status__in=[Task.Status.UNDER_REVIEW, Task.Status.COMPLETED]) & (
                Q(reviewer=user) | Q(created_by=user))
            return queryset.filter(queue)
        return queryset

    def _team_queryset(self, queryset):
        """
        "Team Tasks": the department head's own scope; everything for HR/Admin.

        An Employee has no team, so this returns nothing for them rather than
        quietly falling back to their own tasks — a menu that shows your own work
        under somebody else's label is a lie about what you are looking at.
        """
        user = self.request.user
        if perms.has_org_wide_read(user):
            return queryset.exclude(status=Task.Status.DRAFT)
        if not perms.is_department_head(user):
            return queryset.none()
        dept_ids = perms.department_ids_in_scope(user)
        scope = Q()
        if dept_ids:
            scope |= Q(department_id__in=dept_ids)
        if getattr(user, "department", ""):
            scope |= Q(department_name__iexact=user.department)
        if not scope:
            return queryset.none()
        return queryset.filter(scope).exclude(status=Task.Status.DRAFT)

    # ------------------------------------------------------------------
    # Serializers and per-action permissions
    # ------------------------------------------------------------------
    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return TaskWriteSerializer
        if self.action == "list":
            return TaskListSerializer
        return TaskDetailSerializer

    def get_permissions(self):
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), perms.CanMutateTask()]
        if self.action == "destroy":
            return [IsAuthenticated(), perms.CanDeleteTask()]
        return super().get_permissions()

    def _work_refusal(self, task, default):
        """
        The reason THIS person is being refused, not the generic one
        (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).

        Without this, an assignee blocked by the acceptance gate is told "only
        an assignee can do this" — which they are, so the message sends them
        looking for a permissions problem that does not exist. When the gate is
        what closed the door, the gate says so, in the same words the workflow
        engine uses when it refuses the same thing.
        """
        if not task.work_may_start and perms.is_assignee(self.request.user, task):
            return ("This task cannot be worked on until every assignee has "
                    "accepted it. Still waiting for: "
                    + ", ".join(task.pending_assignee_names) + ".")
        return default

    def get_object(self):
        """
        Resolve a task by its UUID **or by its task number**
        (Phase TASK-DEEP-LINK-SHARING).

        WHY THE NUMBER IS A LOOKUP KEY AND NOT JUST A LABEL
        ---------------------------------------------------
        A link is only shareable if a person can read it, recognise it and type
        it. `/tasks/019f…-8d1c` is none of those; `/tasks/NIFN-TSK-2083-0002` is
        the reference that already appears on the card, in the email and in the
        notification, so the URL and the thing people call the task are finally
        the same string.

        The number is unique and immutable — `TaskNumberSequence` issues each
        one once and nothing rewrites it — so it is a stable key, which is what
        a canonical URL has to be.

        NOTHING ABOUT ACCESS CHANGES HERE
        ---------------------------------
        The lookup runs against `self.get_queryset()`, which is already scoped
        to what this user may see (tasks.permissions.visible_task_filter), and
        `check_object_permissions` runs after it exactly as DRF's own
        implementation does. A task number somebody is not allowed to see
        resolves to nothing and 404s — the same answer, from the same code, as
        guessing its UUID. A shared link is a shortcut past SEARCHING, never
        past permission.

        UUIDs keep working. Every link already sent, every integration and
        every `reverse()` in the codebase still resolves.
        """
        raw = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        try:
            uuid.UUID(str(raw))
        except (AttributeError, TypeError, ValueError):
            pass
        else:
            return super().get_object()

        # `filter_queryset` as well as `get_queryset`, mirroring DRF's own
        # get_object: a detail route that ignored the filter backends would
        # behave differently from the one it replaces.
        queryset = self.filter_queryset(self.get_queryset())
        task = get_object_or_404(queryset, task_number__iexact=str(raw).strip())
        self.check_object_permissions(self.request, task)
        return task

    def _detail(self, task):
        fresh = self.get_queryset().filter(pk=task.pk).first() or task
        return TaskDetailSerializer(fresh, context=self.get_serializer_context()).data

    # ------------------------------------------------------------------
    # Create / update / delete
    # ------------------------------------------------------------------
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Create a task, and — if people were named on the form — assign it in the
        same request.

        Atomic, because this is four writes that only make sense together. A
        failure partway (a duplicate assignee, say) would otherwise leave a task
        row that nobody asked for, holding a number the sequence has already
        handed out and will never issue again.
        """
        # Anybody may create a task, for anybody, reviewed by anybody
        # (Phase TASK-SIMPLIFICATION). The one rule about people is enforced in
        # the write serializer: the reviewer cannot be an assignee, because a
        # person signing off their own work is what the review step exists to
        # prevent.
        write = self.get_serializer(data=request.data)
        write.is_valid(raise_exception=True)
        assignees = write.validated_data.pop("assignee_ids", [])
        checklist = write.validated_data.pop("checklist", [])
        subtasks = write.validated_data.pop("subtasks", [])
        write.validated_data.pop("expected_updated_at", None)

        snapshot = user_snapshot(request.user)
        task = write.save(
            task_number=generate_task_number(),
            created_by=request.user,
            created_by_name=snapshot["name"],
            reviewer_name=user_snapshot(write.validated_data.get("reviewer"))["name"],
        )
        workflow.record_creation(task, request.user, request=request)

        # Assignees FIRST, then the department. `resolve_department` prefers the
        # primary assignee's department over the creator's — a task raised by HR
        # for somebody in Finance belongs to Finance, not to HR — and it can only
        # do that once the assignees exist.
        if assignees:
            workflow.set_assignees(task, assignees, request.user, request=request)
        if task.department_id:
            task.department_name = task.department.name
        resolve_department(task)
        task.save(update_fields=["department", "department_name"])

        if assignees:
            # A task created with people on it is created ASSIGNED: the form has
            # an assignee picker, and leaving it in Draft would mean every
            # creation needed a second click to do the thing that was asked for.
            workflow.assign(task, request.user, request=request)
        workflow.announce_creation(task, request.user)
        if checklist:
            self._replace_checklist(task, checklist, request.user, request=request)
        if subtasks:
            # After the assignees, because a subtask may only be assigned to
            # one of them. Refreshed so the validator sees the rows just added.
            task = self.get_queryset().get(pk=task.pk)
            for position, row in enumerate(subtasks):
                self._create_subtask(task, row, request.user, position,
                                     request=request)
            recalculate_progress(task)

        return Response(self._detail(task), status=http_status.HTTP_201_CREATED)

    def _screen_owner_only_fields(self, task, data):
        """
        An assignee editing a task they did not raise may change its DETAILS but
        not its TERMS (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).

        Only actual CHANGES are refused. A field that arrived UNCHANGED is
        dropped from the payload instead — quietly, because there is nothing to
        tell anybody: the caller asked for the value that is already stored, and
        got it.

        DROPPING, not merely allowing, is the point
        (Phase TASK-COLLABORATION-HARDENING). `assignee_ids` and `checklist` are
        re-applied further down by code that demands owner authority, so an
        unchanged list that survived this method reached that code and was
        refused there — a 403 for sending the task back exactly as it was. Any
        API client that PATCHes the whole object hit it; only the edit form
        escaped, and only because it had been taught not to send those fields.
        """
        if perms.can_edit_all_fields(self.request.user, task):
            return
        current = {
            "reviewer": task.reviewer_id,
            "department": task.department_id,
            "goal": task.goal_id,
        }
        refused = [name for name, existing in current.items()
                   if name in data
                   and getattr(data[name], "pk", data[name]) != existing]
        # Compared as SETS: the order of the list decides who is primary, which
        # is the owner's call, but a client that sends the same three people in
        # a different order has not asked to change anything about them.
        if data.get("assignee_ids") is not None:
            if ({u.pk for u in data["assignee_ids"]}
                    != {row.user_id for row in task.assignees.all()}):
                refused.append("assignee_ids")
            else:
                data.pop("assignee_ids")
        if data.get("checklist") is not None:
            if ([text.strip() for text in data["checklist"]]
                    != [row.text for row in task.checklist.all()
                        if row.group_id is None]):
                refused.append("checklist")
            else:
                data.pop("checklist")
        if refused:
            raise PermissionDenied(
                "You can change this task's title, description, priority and due "
                "date. Its reviewer, department, goal, assignees and checklist "
                "are the creator's to change: " + ", ".join(sorted(refused)) + ".")

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        write = TaskWriteSerializer(instance, data=request.data, partial=partial,
                                    context=self.get_serializer_context())
        write.is_valid(raise_exception=True)
        # Phase TASK-AUTOSAVE-AND-SUBTASKS. The form says which version it
        # edited; if the row moved since, somebody else saved in between and
        # the client is handed the current task to review rather than having
        # its own save silently win. Subtasks are managed on the task itself
        # once it exists, so a stale draft cannot rewrite them from here.
        expected = write.validated_data.pop("expected_updated_at", None)
        write.validated_data.pop("subtasks", None)
        if expected is not None and abs(
                (instance.updated_at - expected).total_seconds()) > 0.001:
            return Response(
                {"detail": "This task was updated by another user while you "
                           "were editing it.",
                 "task": self._detail(instance)},
                status=http_status.HTTP_409_CONFLICT)
        self._screen_owner_only_fields(instance, write.validated_data)
        # What the edit actually changed, read BEFORE the save. The timeline row
        # below names the fields: "Task details edited" against a task three
        # people are now allowed to edit does not tell the fourth what moved
        # (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).
        tracked = ("title", "description", "priority", "due_date")
        before = {name: getattr(instance, name) for name in tracked}
        assignees = write.validated_data.pop("assignee_ids", None)
        checklist = write.validated_data.pop("checklist", None)
        task = write.save()
        if "reviewer" in write.validated_data:
            task.reviewer_name = user_snapshot(task.reviewer)["name"]
        if task.department_id:
            task.department_name = task.department.name
        task.save(update_fields=["reviewer_name", "department_name"])

        if assignees is not None:
            if not perms.can_manage_assignees(request.user, task):
                raise PermissionDenied("The assignees can no longer be changed.")
            workflow.set_assignees(task, assignees, request.user, request=request)
        if checklist is not None:
            if not perms.can_manage_checklist(request.user, task):
                raise PermissionDenied("The checklist can no longer be changed.")
            self._replace_checklist(task, checklist, request.user, request=request)

        changed = [name for name in tracked if getattr(task, name) != before[name]]
        # BEFORE AND AFTER, not just the field name
        # (Phase TASK-COLLABORATION-HARDENING). Priority and due date are the
        # two an assignee can now move that somebody is later measured against —
        # a due date pushed out is an overdue task that never became overdue —
        # so the row records what it was as well as what it is. Title and
        # description are named but not quoted: the current text is on the page
        # and the values would make the timeline unreadable.
        moved = {name: [str(before[name]), str(getattr(task, name))]
                 for name in changed if name in ("priority", "due_date")}
        detail = ", ".join(
            f"{name.replace('_', ' ')} {moved[name][0]} → {moved[name][1]}"
            if name in moved else name.replace("_", " ")
            for name in changed)
        record_audit(task, request.user, "updated",
                     remarks=f"Task details edited: {detail}." if changed
                             else "Task details edited.",
                     metadata={"fields": changed, "moved": moved},
                     request=request)
        return Response(self._detail(task))

    def perform_destroy(self, instance):
        record_audit(instance, self.request.user, "deleted",
                     remarks=f"Draft {instance.task_number} deleted.",
                     request=self.request)
        instance.delete()

    # ------------------------------------------------------------------
    # Assignees
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="assignees")
    def assignees(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            return Response(TaskAssigneeSerializer(
                task.assignees.all(), many=True,
                context=self.get_serializer_context()).data)

        if not perms.can_manage_assignees(request.user, task):
            raise PermissionDenied("The assignees can no longer be changed.")
        write = AssigneeListSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        rows = workflow.set_assignees(task, write.validated_data["assignee_ids"],
                                      request.user, request=request)
        return Response(TaskAssigneeSerializer(
            rows, many=True, context=self.get_serializer_context()).data)

    # ------------------------------------------------------------------
    # Transitions. One endpoint per named step of the workflow diagram.
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        task = self.get_object()
        if not perms.can_assign_task(request.user, task):
            raise PermissionDenied(
                "Only the task's owner can assign it, and only with at least one "
                "employee selected.")
        workflow.assign(task, request.user, request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        task = self.get_object()
        if not perms.can_accept(request.user, task):
            raise PermissionDenied("This task is not waiting for you to accept it.")
        workflow.accept(task, request.user, request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="request-clarification")
    def request_clarification(self, request, pk=None):
        task = self.get_object()
        if not perms.can_request_clarification(request.user, task):
            raise PermissionDenied(
                "Only an assignee can request clarification, and only before "
                "accepting the task.")
        write = RequiredReasonSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.request_clarification(task, request.user,
                                       write.validated_data["reason"],
                                       request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        task = self.get_object()
        allowed = (perms.can_update_progress(request.user, task)
                   or perms.can_unblock(request.user, task))
        if not allowed:
            raise PermissionDenied(self._work_refusal(
                task, "Only an assignee can start work on this task."))
        workflow.start(task, request.user, request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="progress")
    def progress(self, request, pk=None):
        task = self.get_object()
        if not perms.can_update_progress(request.user, task):
            raise PermissionDenied(self._work_refusal(
                task, "Only an assignee can report progress on this task."))
        write = ProgressSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        try:
            workflow.update_progress(task, request.user,
                                     write.validated_data["progress_percent"],
                                     note=write.validated_data.get("note", ""),
                                     request=request)
        except workflow.ProgressDerived as exc:
            return Response({"detail": str(exc)},
                            status=http_status.HTTP_409_CONFLICT)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="submit-for-review")
    def submit_for_review(self, request, pk=None):
        task = self.get_object()
        if not perms.can_submit_for_review(request.user, task):
            self._refuse_open_subtasks(task, "submitted for review")
            raise PermissionDenied(self._work_refusal(
                task, "Only an assignee can submit this task for review."))
        write = SubmitReviewSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.submit_for_review(task, request.user,
                                   note=write.validated_data.get("note", ""),
                                   request=request)
        return Response(self._detail(task))

    # ------------------------------------------------------------------
    # Dependencies (Phase TASK-GOVERNANCE-HARDENING)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="dependencies")
    def dependencies(self, request, pk=None):
        """What this task waits for, and (POST) a new thing to wait for."""
        task = self.get_object()
        if request.method == "GET":
            rows = task.dependencies.select_related("depends_on").all()
            return Response(TaskDependencySerializer(rows, many=True).data)

        if not perms.can_manage_dependencies(request.user, task):
            raise PermissionDenied(
                "Only the people who own or are doing this task can change what "
                "it waits for.")
        write = DependencyWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        prerequisite = write.validated_data["depends_on"]
        # A prerequisite you cannot read is a prerequisite you cannot see the
        # status of: the panel would show a blocker with no name behind it, and
        # the existence of the task would leak through the block itself.
        if not perms.can_read(request.user, prerequisite):
            raise PermissionDenied(
                "You can only make this task wait for work you can see.")
        workflow.add_dependency(
            task, prerequisite, request.user,
            kind=write.validated_data.get("kind"),
            note=write.validated_data.get("note", ""), request=request)
        return Response(self._detail(task), status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"],
            url_path=r"dependencies/(?P<dependency_id>[0-9a-f-]+)")
    def remove_dependency(self, request, pk=None, dependency_id=None):
        task = self.get_object()
        if not perms.can_manage_dependencies(request.user, task):
            raise PermissionDenied(
                "Only the people who own or are doing this task can change what "
                "it waits for.")
        row = task.dependencies.filter(pk=dependency_id).first()
        if row is None:
            raise NotFound("This task does not wait for that one.")
        workflow.remove_dependency(row, request.user, request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        task = self.get_object()
        if not perms.can_review(request.user, task):
            raise PermissionDenied("This task is not with you for review.")
        write = RemarksSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.approve_review(task, request.user,
                                remarks=write.validated_data.get("remarks", ""),
                                request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="request-rework")
    def request_rework(self, request, pk=None):
        task = self.get_object()
        if not perms.can_review(request.user, task):
            raise PermissionDenied("This task is not with you for review.")
        write = RequiredReasonSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.request_rework(task, request.user, write.validated_data["reason"],
                                request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        task = self.get_object()
        if not perms.can_close(request.user, task):
            self._refuse_open_subtasks(task, "closed")
            raise PermissionDenied(
                "Only HR, an Admin or the department head can close this task, and "
                "never the person who did it.")
        write = RemarksSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.close(task, request.user,
                       remarks=write.validated_data.get("remarks", ""),
                       request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="block")
    def block(self, request, pk=None):
        task = self.get_object()
        if not perms.can_block(request.user, task):
            raise PermissionDenied("This task cannot be blocked from its current state.")
        write = RequiredReasonSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.block(task, request.user, write.validated_data["reason"],
                       request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="unblock")
    def unblock(self, request, pk=None):
        task = self.get_object()
        if not perms.can_unblock(request.user, task):
            raise PermissionDenied("This task is not blocked.")
        workflow.start(task, request.user, request=request)
        return Response(self._detail(task))

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        task = self.get_object()
        if not perms.can_cancel(request.user, task):
            raise PermissionDenied("Only the task's owner can cancel it.")
        write = RequiredReasonSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.cancel(task, request.user, write.validated_data["reason"],
                        request=request)
        return Response(self._detail(task))

    # ------------------------------------------------------------------
    # Checklist
    # ------------------------------------------------------------------
    @transaction.atomic
    def _replace_checklist(self, task, items, actor, groups=None, request=None):
        """
        Replace the checklist, preserving the tick on any row whose text is
        unchanged — including across a regroup.

        The tick is carried on the TEXT, not on the row id, because the client
        sends text. Rebuilding from scratch would silently un-tick completed work
        every time somebody fixed a typo in another row, or moved one line into a
        section.

        `groups` is the Phase T2.1 shape; `items` is the Phase T1 flat one. Both
        may be present, and top-level items keep their position ahead of the
        sections, which is the order the page renders.
        """
        items = list(items or [])
        groups = list(groups or [])

        done = {row.text: row for row in task.checklist.all() if row.is_done}
        # Groups first: deleting a group CASCADEs to its items, so clearing the
        # items separately afterwards would be operating on rows already gone.
        task.checklist_groups.all().delete()
        task.checklist.all().delete()

        def build(text, position, group=None):
            previous = done.get(text)
            return TaskChecklistItem(
                task=task, group=group, text=text, position=position,
                is_done=previous is not None,
                done_by=previous.done_by if previous else None,
                done_by_name=previous.done_by_name if previous else "",
                done_at=previous.done_at if previous else None)

        rows = [build(text, position) for position, text in enumerate(items)]

        for group_position, group_spec in enumerate(groups):
            group = TaskChecklistGroup.objects.create(
                task=task, title=group_spec["title"], position=group_position)
            rows.extend(
                build(text, position, group)
                for position, text in enumerate(group_spec.get("items", [])))

        TaskChecklistItem.objects.bulk_create(stamp_all(rows))

        total = len(rows)
        record_audit(task, actor, "checklist_updated",
                     remarks=f"{total} checklist item(s) in "
                             f"{len(groups)} group(s).",
                     metadata={"count": total, "groups": len(groups)},
                     request=request)
        # Phase T2.1: "progress auto-calculated". Rebuilding the list changes the
        # denominator, so the derived figure has to move with it.
        recalculate_progress(task)
        return task

    @action(detail=True, methods=["get", "post"], url_path="checklist")
    def checklist(self, request, pk=None):
        """
        Read or replace the checklist.

        The response stays a FLAT LIST, as it was in Phase T1 — every item, in
        order, each now carrying the `group` it belongs to (null for a top-level
        one). Adding groups did not have to change this shape, and changing it
        would have broken every caller for the sake of a convenience the detail
        payload already provides: `checklist_groups` there carries the nested
        form for rendering sections.
        """
        task = self.get_object()
        if request.method == "GET":
            return Response(TaskChecklistItemSerializer(
                task.checklist.all(), many=True).data)

        if not perms.can_manage_checklist(request.user, task):
            raise PermissionDenied("The checklist can no longer be changed.")
        write = ChecklistWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        self._replace_checklist(
            task,
            write.validated_data.get("items", []),
            request.user,
            groups=write.validated_data.get("groups", []),
            request=request)
        return Response(TaskChecklistItemSerializer(
            task.checklist.all(), many=True).data)

    @action(detail=True, methods=["get"], url_path="checklist/grouped")
    def checklist_grouped(self, request, pk=None):
        """
        The same checklist in its nested form: loose items, then sections.

        A separate route rather than a query parameter on the one above, so each
        endpoint has ONE response shape. `?grouped=1` returning either a list or
        an object is the kind of API that forces every caller to branch.
        """
        task = self.get_object()
        loose = [item for item in task.checklist.all() if item.group_id is None]
        return Response({
            "items": TaskChecklistItemSerializer(loose, many=True).data,
            "groups": TaskChecklistGroupSerializer(
                task.checklist_groups.all(), many=True).data,
            "done": sum(1 for item in task.checklist.all() if item.is_done),
            "total": len(task.checklist.all()),
            "percent": task.checklist_percent,
            "progress_percent": task.progress_percent,
            "progress_is_auto": task.progress_is_auto,
        })

    @action(detail=True, methods=["post"], url_path=r"checklist/(?P<item_id>[^/.]+)/tick")
    def tick_checklist(self, request, pk=None, item_id=None):
        task = self.get_object()
        if not perms.can_tick_checklist(request.user, task):
            raise PermissionDenied("You cannot change this task's checklist.")
        row = task.checklist.filter(pk=item_id).first()
        if row is None:
            raise NotFound("That checklist item does not exist on this task.")

        write = ChecklistTickSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        row.is_done = write.validated_data["is_done"]
        if row.is_done:
            row.done_by = request.user
            row.done_by_name = user_snapshot(request.user)["name"]
            row.done_at = timezone.now()
        else:
            row.done_by = None
            row.done_by_name = ""
            row.done_at = None
        row.save(update_fields=["is_done", "done_by", "done_by_name", "done_at"])

        record_audit(task, request.user, "checklist_updated",
                     remarks=f"{'Completed' if row.is_done else 'Reopened'}: {row.text}",
                     request=request)
        # Phase T2.1/T2.2 — the tick IS the progress update, while the task is on
        # automatic. Making the employee tick a box and then separately drag a
        # slider to the number the boxes already imply is the busywork this
        # removes.
        recalculate_progress(task)
        # The item's own fields at the top level, exactly as in Phase T1, with
        # the recalculated progress ALONGSIDE them. Nesting the item under a key
        # would have been tidier and would have broken every existing caller for
        # no gain; the client needs both, and a superset costs nobody anything.
        payload = dict(TaskChecklistItemSerializer(row).data)
        payload.update({
            "progress_percent": task.progress_percent,
            "progress_is_auto": task.progress_is_auto,
            "checklist_percent": task.checklist_percent,
        })
        return Response(payload)

    # ------------------------------------------------------------------
    # Subtasks (Phase TASK-AUTOSAVE-AND-SUBTASKS)
    # ------------------------------------------------------------------
    def _refuse_open_subtasks(self, task, verb):
        """The reason named first: an open subtask is the thing to act on."""
        open_count = sum(1 for row in task.subtasks.all() if not row.is_done)
        if open_count:
            raise PermissionDenied(
                f"This task cannot be {verb} while {open_count} subtask"
                f"{'' if open_count == 1 else 's'} "
                f"{'is' if open_count == 1 else 'are'} still open.")

    def _subtask_from_request(self, task, raw):
        """Resolve an optional subtask id sent with a comment or an upload."""
        if not raw:
            return None
        row = task.subtasks.filter(pk=raw).first() if _is_uuid(raw) else None
        if row is None:
            raise ValidationError({"subtask": "That subtask is not on this task."})
        return row

    def _subtask_payload(self, task, subtask):
        """One row, with the task's progress alongside - the panel needs both."""
        fresh = self.get_queryset().filter(pk=task.pk).first() or task
        row = fresh.subtasks.filter(pk=subtask.pk).first() or subtask
        context = dict(self.get_serializer_context(), task=fresh)
        return {
            "subtask": TaskSubtaskSerializer(row, context=context).data,
            "progress_percent": fresh.progress_percent,
            "progress_is_auto": fresh.progress_is_auto,
            "subtask_done": fresh.subtask_done,
            "subtask_total": fresh.subtask_total,
            "subtask_percent": fresh.subtask_percent,
            "open_subtask_count": fresh.open_subtask_count,
        }

    def _subtask_list(self, task):
        fresh = self.get_queryset().filter(pk=task.pk).first() or task
        context = dict(self.get_serializer_context(), task=fresh)
        return TaskSubtaskSerializer(fresh.subtasks.all(), many=True,
                                     context=context).data

    def _create_subtask(self, task, data, actor, position, request=None):
        write = SubtaskWriteSerializer(data=data, context={"task": task})
        write.is_valid(raise_exception=True)
        assignee = write.validated_data.get("assignee")
        row = TaskSubtask.objects.create(
            task=task, position=position,
            title=write.validated_data["title"],
            description=write.validated_data.get("description", ""),
            assignee=assignee,
            assignee_name=user_snapshot(assignee)["name"] if assignee else "",
            due_date=write.validated_data.get("due_date"),
            created_by=actor, created_by_name=user_snapshot(actor)["name"])
        record_audit(task, actor, "subtask_added", remarks=row.title,
                     metadata={"subtask": str(row.pk),
                               "assignee": row.assignee_name},
                     request=request)
        if assignee is not None and assignee.pk != actor.pk:
            self._notify_subtask_assigned(task, row, actor)
        return row

    def _notify_subtask_assigned(self, task, row, actor):
        notify_user(
            row.assignee, Category.TASK_SUBTASK_ASSIGNED,
            "Subtask assigned to you",
            f"{user_snapshot(actor)['name']} assigned you \"{row.title}\" on "
            f"{task.task_number} — {task.title}.",
            task, idempotency_key=f"subtask-assigned-{row.pk}-{row.assignee_id}")

    @action(detail=True, methods=["get", "post"], url_path="subtasks")
    @transaction.atomic
    def subtasks(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            return Response(self._subtask_list(task))
        if not perms.can_manage_subtasks(request.user, task):
            raise PermissionDenied("Only the task's owner can add subtasks.")
        position = task.subtask_total
        row = self._create_subtask(task, request.data, request.user, position,
                                   request=request)
        recalculate_progress(task)
        return Response(self._subtask_payload(task, row),
                        status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch", "delete"],
            url_path=r"subtasks/(?P<subtask_id>[^/.]+)")
    @transaction.atomic
    def subtask_detail(self, request, pk=None, subtask_id=None):
        task = self.get_object()
        row = task.subtasks.filter(pk=subtask_id).first() if _is_uuid(subtask_id) else None
        if row is None:
            raise NotFound("That subtask does not exist on this task.")

        if request.method == "DELETE":
            if not perms.can_manage_subtasks(request.user, task):
                raise PermissionDenied("Only the task's owner can remove subtasks.")
            title = row.title
            row.delete()
            record_audit(task, request.user, "subtask_removed", remarks=title,
                         metadata={"subtask": str(subtask_id)}, request=request)
            recalculate_progress(task)
            return Response(status=http_status.HTTP_204_NO_CONTENT)

        if not perms.can_edit_subtask(request.user, row):
            raise PermissionDenied("You cannot change this subtask.")
        if not perms.can_manage_subtasks(request.user, task):
            # The assignee may edit the notes on their piece, nothing else.
            extra = set(request.data) - {"description"}
            if extra:
                raise PermissionDenied(
                    "Only the task's owner can change a subtask's title, "
                    "assignee or due date.")
        write = SubtaskWriteSerializer(data=request.data, partial=True,
                                       context={"task": task})
        write.is_valid(raise_exception=True)
        data = write.validated_data
        previous_assignee = row.assignee_id
        changed = []
        for name in ("title", "description", "due_date"):
            if name in data and getattr(row, name) != data[name]:
                setattr(row, name, data[name])
                changed.append(name)
        if "assignee" in data and data["assignee"] != row.assignee:
            row.assignee = data["assignee"]
            row.assignee_name = (user_snapshot(row.assignee)["name"]
                                 if row.assignee else "")
            changed.append("assignee")
        if changed:
            row.save()
            record_audit(task, request.user,
                         "subtask_assigned" if "assignee" in changed else "updated",
                         remarks=f"{row.title}: {', '.join(changed)} changed."
                                 if "assignee" not in changed else
                                 (f"{row.title} assigned to {row.assignee_name}."
                                  if row.assignee_id else f"{row.title} unassigned."),
                         metadata={"subtask": str(row.pk), "fields": changed},
                         request=request)
            if ("assignee" in changed and row.assignee_id
                    and row.assignee_id != previous_assignee
                    and row.assignee_id != request.user.pk):
                self._notify_subtask_assigned(task, row, request.user)
        return Response(self._subtask_payload(task, row))

    @action(detail=True, methods=["post"],
            url_path=r"subtasks/(?P<subtask_id>[^/.]+)/complete")
    @transaction.atomic
    def complete_subtask(self, request, pk=None, subtask_id=None):
        task = self.get_object()
        row = task.subtasks.filter(pk=subtask_id).first() if _is_uuid(subtask_id) else None
        if row is None:
            raise NotFound("That subtask does not exist on this task.")
        if not perms.can_complete_subtask(request.user, row):
            raise PermissionDenied(self._work_refusal(
                task, "You cannot complete this subtask."))
        write = SubtaskCompleteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        is_done = write.validated_data["is_done"]
        if is_done != row.is_done:
            row.is_done = is_done
            if is_done:
                row.done_by = request.user
                row.done_by_name = user_snapshot(request.user)["name"]
                row.done_at = timezone.now()
            else:
                row.done_by = None
                row.done_by_name = ""
                row.done_at = None
            row.save(update_fields=["is_done", "done_by", "done_by_name",
                                    "done_at", "updated_at"])
            record_audit(task, request.user, "subtask_completed",
                         remarks=f"{'Completed' if is_done else 'Reopened'}: {row.title}",
                         metadata={"subtask": str(row.pk), "is_done": is_done},
                         request=request)
            previous = task.progress_percent
            moved = recalculate_progress(task)
            if moved:
                record_audit(task, request.user, "progress_updated",
                             remarks=f"Progress {previous}% → {task.progress_percent}% "
                                     "from subtasks.",
                             metadata={"from": previous, "to": task.progress_percent,
                                       "source": "subtasks"},
                             request=request)
            if is_done:
                self._notify_subtask_completed(task, row, request.user)
        return Response(self._subtask_payload(task, row))

    def _notify_subtask_completed(self, task, row, actor):
        """
        The creator hears that a piece is done; on a SHARED task the other
        assignees hear only when the LAST piece closes - that is the moment the
        submission is in reach and whoever is left becomes the visible one.
        """
        done, total = recalc_tally(task)
        label = f"{task.task_number} — {task.title}"
        body = (f"{user_snapshot(actor)['name']} completed \"{row.title}\" on "
                f"{label} · {done} / {total} · {round(100 * done / total) if total else 0}%.")
        recipients = {task.created_by} if task.created_by else set()
        if done == total and task.assignee_count > 1:
            recipients.update(r.user for r in task.assignees.all() if r.user)
        for user in recipients:
            if user is None or user.pk == actor.pk:
                continue
            notify_user(user, Category.TASK_SUBTASK_COMPLETED, "Subtask completed",
                        body, task,
                        idempotency_key=f"subtask-done-{row.pk}-{user.pk}")

    @action(detail=True, methods=["post"], url_path="subtasks/reorder")
    @transaction.atomic
    def reorder_subtasks(self, request, pk=None):
        task = self.get_object()
        if not perms.can_manage_subtasks(request.user, task):
            raise PermissionDenied("Only the task's owner can reorder subtasks.")
        write = SubtaskReorderSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        ids = [str(i) for i in write.validated_data["ids"]]
        own = {str(row.pk): row for row in task.subtasks.all()}
        if set(ids) != set(own) or len(ids) != len(own):
            raise ValidationError(
                {"ids": "Send every subtask of this task exactly once."})
        for position, sid in enumerate(ids):
            if own[sid].position != position:
                own[sid].position = position
                own[sid].save(update_fields=["position"])
        return Response(self._subtask_list(task))

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="comments")
    @transaction.atomic
    def comments(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            rows = [c for c in task.comments.all() if c.parent_id is None]
            return Response(TaskCommentSerializer(
                rows, many=True, context=self.get_serializer_context()).data)

        if not perms.can_comment(request.user, task):
            raise PermissionDenied("You cannot comment on this task.")
        write = CommentWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)

        parent = write.validated_data.get("parent")
        subtask = write.validated_data.get("subtask")
        if parent is not None:
            if parent.task_id != task.pk:
                raise ValidationError(
                    {"parent": "That comment is not on this task."})
            if parent.parent_id is not None:
                # One level deep. See TaskComment's docstring.
                raise ValidationError(
                    {"parent": "Reply to the original comment, not to a reply."})
            # A reply stays in its parent's thread.
            subtask = parent.subtask
        if subtask is not None and subtask.task_id != task.pk:
            raise ValidationError({"subtask": "That subtask is not on this task."})

        comment = TaskComment.objects.create(
            task=task, parent=parent, subtask=subtask, author=request.user,
            author_name=user_snapshot(request.user)["name"],
            body=write.validated_data["body"])
        mentioned = self._record_mentions(
            task, comment, write.validated_data.get("mention_ids", []))

        record_audit(task, request.user, "commented",
                     remarks=comment.body[:200],
                     metadata={"is_reply": parent is not None,
                               "mentions": len(mentioned)},
                     request=request)
        self._notify_comment(task, comment, mentioned, request.user)
        return Response(TaskCommentSerializer(
            comment, context=self.get_serializer_context()).data,
            status=http_status.HTTP_201_CREATED)

    def _record_mentions(self, task, comment, users):
        """
        Store the mentions the client resolved, minus anybody who cannot read
        the task.

        Dropped rather than rejected: a stale picker, or a colleague who lost
        access between typing and posting, must not fail the whole comment. The
        body may then still read "@Someone" without that having notified them,
        which is the honest outcome — the alternative is notifying somebody about
        a task they cannot open.
        """
        allowed = []
        for user in users:
            if user.pk == comment.author_id:
                continue    # naming yourself is not a notification
            if not perms.can_read(user, task):
                logger.info("Dropped mention of %s on task %s: no read access.",
                            user.pk, task.pk)
                continue
            allowed.append(user)

        TaskCommentMention.objects.bulk_create(stamp_all([
            TaskCommentMention(comment=comment, user=user,
                               user_name=user_snapshot(user)["name"])
            for user in allowed
        ]), ignore_conflicts=True)
        return allowed

    def _notify_comment(self, task, comment, mentioned, actor):
        """
        Two audiences, one notification each, and never both to the same person.

        Somebody NAMED in a comment gets the mention category — it is addressed
        to them, and they should get it even if they have muted general task
        chatter. Everybody else involved gets the ordinary comment category. A
        person who is both would otherwise get two emails about one sentence.
        """
        mentioned_ids = {u.pk for u in mentioned}
        for user in mentioned:
            notify_user(user, Category.TASK_MENTIONED, "You were mentioned",
                        f"{comment.author_name} mentioned you on "
                        f"{task.task_number}: {comment.body[:120]}", task)

        recipients = {row.user for row in task.assignees.all() if row.user}
        recipients.update(u for u in (task.created_by, task.reviewer) if u)
        # A reply also reaches the person being replied to, who may be neither.
        if comment.parent_id and comment.parent.author:
            recipients.add(comment.parent.author)
        for user in recipients:
            if user.pk == actor.pk or user.pk in mentioned_ids:
                continue
            notify_user(user, Category.TASK_COMMENTED, "New comment on a task",
                        f"{comment.author_name} commented on {task.task_number}: "
                        f"{comment.body[:120]}", task)

    @action(detail=True, methods=["patch"],
            url_path=r"comments/(?P<comment_id>[^/.]+)")
    def edit_comment(self, request, pk=None, comment_id=None):
        """
        Edit your own comment (Phase T2.3).

        Author only — not the owner, not an Admin. `edited_at` is stamped and
        every reader sees the marker, so the correction is visible rather than
        silent, and the timeline records that it happened.
        """
        task = self.get_object()
        comment = task.comments.filter(pk=comment_id).first()
        if comment is None:
            raise NotFound("That comment does not exist on this task.")
        if not perms.can_edit_comment(request.user, comment):
            raise PermissionDenied("You can only edit your own comments.")

        write = CommentWriteSerializer(data=request.data, partial=True)
        write.is_valid(raise_exception=True)
        body = write.validated_data.get("body")
        if not body:
            raise ValidationError({"body": "A comment cannot be empty."})

        previous = comment.body
        comment.body = body
        comment.edited_at = timezone.now()
        comment.save(update_fields=["body", "edited_at"])
        record_audit(task, request.user, "comment_edited",
                     remarks=comment.body[:200],
                     metadata={"comment_id": str(comment.pk),
                               "previous": previous[:500]},
                     request=request)
        return Response(TaskCommentSerializer(
            comment, context=self.get_serializer_context()).data)

    # ------------------------------------------------------------------
    # Attachments and evidence
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="attachments",
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def attachments(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            return Response(TaskAttachmentSerializer(
                task.attachments.all(), many=True).data)

        if not perms.can_upload_attachment(request.user, task):
            raise PermissionDenied(self._work_refusal(
                task, "You cannot add files to this task."))

        files = request.FILES.getlist("files") or request.FILES.getlist("file")
        if not files:
            return Response({"files": ["Attach at least one file."]},
                            status=http_status.HTTP_400_BAD_REQUEST)

        is_evidence = str(request.data.get("is_evidence", "true")).lower() not in (
            "false", "0", "no")
        caption = (request.data.get("caption") or "")[:255]
        subtask = self._subtask_from_request(task, request.data.get("subtask"))

        created = []
        for uploaded in files:
            validate_task_attachment(uploaded)
            created.append(TaskAttachment.objects.create(
                task=task, subtask=subtask, file=uploaded,
                original_name=uploaded.name[:255],
                size=uploaded.size, content_type=(uploaded.content_type or "")[:120],
                caption=caption, is_evidence=is_evidence,
                uploaded_by=request.user,
                uploaded_by_name=user_snapshot(request.user)["name"]))

        record_audit(
            task, request.user,
            # The timeline distinguishes the two, because a reviewer scanning it
            # for "what was produced" should not have to read past every
            # specification document attached at creation.
            "evidence_uploaded" if is_evidence else "attachment_added",
            remarks=", ".join(a.original_name for a in created),
            metadata={"count": len(created), "is_evidence": is_evidence},
            request=request)
        return Response(TaskAttachmentSerializer(created, many=True).data,
                        status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="attachments/links")
    def add_link(self, request, pk=None):
        """
        Attach a LINK rather than a file (Phase T2.5).

        Evidence is often somewhere else — a dashboard, a published page, a
        document in another system. Same row, same evidence flag, same timeline;
        the only thing a link has not got is a download.
        """
        task = self.get_object()
        if not perms.can_upload_attachment(request.user, task):
            raise PermissionDenied(self._work_refusal(
                task, "You cannot add links to this task."))
        write = AttachmentLinkSerializer(data=request.data)
        write.is_valid(raise_exception=True)

        row = TaskAttachment.objects.create(
            task=task,
            link_url=write.validated_data["link_url"],
            original_name=write.validated_data.get("caption")
            or write.validated_data["link_url"][:255],
            caption=write.validated_data.get("caption", "")[:255],
            is_evidence=write.validated_data["is_evidence"],
            uploaded_by=request.user,
            uploaded_by_name=user_snapshot(request.user)["name"])

        record_audit(task, request.user, "link_added",
                     remarks=row.link_url[:200],
                     metadata={"is_evidence": row.is_evidence}, request=request)
        return Response(TaskAttachmentSerializer(row).data,
                        status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"],
            url_path=r"attachments/(?P<attachment_id>[^/.]+)")
    def remove_attachment(self, request, pk=None, attachment_id=None):
        """
        Withdraw an attachment (Phase T2.4).

        SOFT. The row and its download history survive, drop out of every default
        list, and appear under the task's withdrawal history. A hard delete would
        erase the fact that a file was ever attached — which is the fact a
        reviewer most needs when a task's evidence changes between submissions.
        """
        task = self.get_object()
        row = task.attachments.filter(pk=attachment_id).first()
        if row is None:
            raise NotFound("That attachment does not exist on this task.")
        if not perms.can_remove_attachment(request.user, row):
            raise PermissionDenied(
                "Only the person who attached this, or the task's owner, can "
                "remove it.")

        row.is_removed = True
        row.removed_at = timezone.now()
        row.removed_by = request.user
        row.removed_by_name = user_snapshot(request.user)["name"]
        row.save(update_fields=["is_removed", "removed_at", "removed_by",
                                "removed_by_name"])
        record_audit(task, request.user, "attachment_removed",
                     remarks=row.original_name or row.link_url,
                     metadata={"attachment_id": str(row.pk),
                               "was_evidence": row.is_evidence},
                     request=request)
        return Response(TaskAttachmentSerializer(row).data)

    @action(detail=True, methods=["post"],
            url_path=r"attachments/(?P<attachment_id>[^/.]+)/flag")
    def flag_attachment(self, request, pk=None, attachment_id=None):
        """Move a row between Evidence and General Attachment (Phase T2.5)."""
        task = self.get_object()
        row = task.attachments.filter(pk=attachment_id).first()
        if row is None:
            raise NotFound("That attachment does not exist on this task.")
        if not perms.can_flag_evidence(request.user, row):
            raise PermissionDenied(
                "Only the person who attached this, or the task's owner, can "
                "reclassify it.")

        write = EvidenceFlagSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        row.is_evidence = write.validated_data["is_evidence"]
        row.save(update_fields=["is_evidence"])
        record_audit(task, request.user, "evidence_flagged",
                     remarks=f"{row.original_name or row.link_url} marked as "
                             f"{'evidence' if row.is_evidence else 'a general attachment'}.",
                     metadata={"attachment_id": str(row.pk),
                               "is_evidence": row.is_evidence},
                     request=request)
        return Response(TaskAttachmentSerializer(row).data)

    @action(detail=True, methods=["get"],
            url_path=r"attachments/(?P<attachment_id>[^/.]+)/downloads")
    def attachment_downloads(self, request, pk=None, attachment_id=None):
        """
        Who has opened this file (Phase T2.4).

        Owner-only. An assignee seeing the full read history of a task they are
        on would learn who has been checking up on them, which is not theirs to
        know and would change how the log gets used.
        """
        task = self.get_object()
        if not perms.can_view_download_log(request.user, task):
            raise PermissionDenied(
                "The download log is visible to the task's owner only.")
        row = task.attachments.filter(pk=attachment_id).first()
        if row is None:
            raise NotFound("That attachment does not exist on this task.")
        return Response(TaskAttachmentDownloadSerializer(
            row.downloads.all()[:200], many=True).data)

    @action(detail=True, methods=["get"],
            url_path=r"attachments/(?P<attachment_id>[^/.]+)/download")
    def download_attachment(self, request, pk=None, attachment_id=None):
        """
        Serve one attachment.

        Reached through get_object(), so the caller's read permission on the TASK
        is what gates the file — there is no unauthenticated media path to it.
        The response is hardened with the project's own headers so a stored file
        cannot execute in this origin.
        """
        task = self.get_object()
        row = task.attachments.filter(pk=attachment_id).first()
        if row is None or not row.file or row.is_removed:
            raise NotFound("That attachment does not exist on this task.")

        # Phase T2.4 — the download audit. Written BEFORE the file is streamed:
        # if the write fails the download does not happen, which is the right way
        # round for an access log. Kept out of the activity timeline on purpose —
        # see TaskAttachmentDownload.
        TaskAttachmentDownload.objects.create(
            attachment=row, user=request.user,
            user_name=user_snapshot(request.user)["name"],
            ip_address=client_ip(request))

        response = FileResponse(row.file.open("rb"), as_attachment=True,
                                filename=row.original_name or "attachment")
        return harden_file_response(response)

    # ------------------------------------------------------------------
    # Activity timeline
    # ------------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        task = self.get_object()
        return Response(TaskAuditLogSerializer(
            task.audit_entries.all(), many=True).data)

    # ------------------------------------------------------------------
    # Bulk operations (Phase T4.8)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["post"], url_path="bulk")
    @transaction.atomic
    def bulk(self, request):
        """
        Apply one action to many tasks.

        PARTIAL SUCCESS IS REPORTED, NOT HIDDEN
        ---------------------------------------
        Every task is checked individually against the same permission functions
        a single-task request uses, and the response names what was applied and
        what was refused, with the reason. The alternative — refusing the whole
        batch because one row was ineligible — turns a fifty-task update into a
        guessing game, and applying silently to whatever happened to be allowed
        is worse still.

        The whole thing runs in one transaction, so a crash halfway leaves
        nothing half-applied.
        """
        write = BulkActionSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        action_name = write.validated_data["action"]
        wanted = write.validated_data["task_ids"]

        # Scoped: ids the caller cannot see simply are not found, which is the
        # same answer they would get asking for one of them directly.
        found = {str(task.id): task for task in
                 self._visible().filter(id__in=wanted)}

        applied, refused = [], []
        for task_id in wanted:
            key = str(task_id)
            task = found.get(key)
            if task is None:
                refused.append({"id": key, "reason": "Not found."})
                continue
            try:
                self._apply_bulk(task, action_name, write.validated_data, request)
                applied.append(key)
            except (ValidationError, PermissionDenied) as exc:
                refused.append({"id": key, "task_number": task.task_number,
                                "reason": _first_message(exc)})

        return Response({
            "action": action_name,
            "requested": len(wanted),
            "applied": applied,
            "refused": refused,
        })

    def _apply_bulk(self, task, action_name, data, request):
        """One task, one action. Raises rather than returning a flag, so the
        caller's error handling is the same as a single-task request's."""
        user = request.user
        if action_name == "assign":
            if not perms.can_manage_assignees(user, task):
                raise PermissionDenied("The assignees can no longer be changed.")
            workflow.set_assignees(task, data["assignee_ids"], user, request=request)
            if task.status in (Task.Status.DRAFT, Task.Status.ASSIGNED):
                workflow.assign(task, user, request=request)
            return
        if action_name == "close":
            if not perms.can_close(user, task):
                raise PermissionDenied(
                    "Only HR, an Admin or the department head can close this "
                    "task, and never the person who did it.")
            workflow.close(task, user, remarks=data.get("remarks", ""),
                           request=request)
            return
        if action_name in ("priority", "due_date"):
            if not perms.can_edit(user, task):
                raise PermissionDenied("This task can no longer be edited.")
            field = "priority" if action_name == "priority" else "due_date"
            setattr(task, field, data[field])
            task.save(update_fields=[field, "updated_at"])
            record_audit(task, user, "updated",
                         remarks=f"{field.replace('_', ' ').title()} set to "
                                 f"{data[field]} in a bulk update.",
                         request=request)
            return
        if action_name == "archive":
            if not perms.is_owner(user, task):
                raise PermissionDenied("Only the task's owner can archive it.")
            workflow.archive(task, user, request=request)
            return
        if action_name == "unarchive":
            if not perms.is_owner(user, task):
                raise PermissionDenied("Only the task's owner can restore it.")
            workflow.unarchive(task, user, request=request)

    # ------------------------------------------------------------------
    # Task groups (Phase T4.7)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get", "post"], url_path="groups")
    def groups(self, request):
        """List the batches raised, or raise one from a template."""
        if request.method == "GET":
            rows = (TaskGroup.objects
                    .annotate(task_count=Count("tasks", distinct=True))
                    .select_related("template")
                    .order_by("-created_at")[:100])
            return Response(TaskGroupSerializer(rows, many=True).data)

        if not perms.can_assign(request.user):
            raise PermissionDenied(
                "Only a Department Head, HR or an Admin may raise a task group.")
        write = CreateTaskGroupSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        group, tasks = workflow.create_task_group(
            write.validated_data["template"], request.user,
            name=write.validated_data.get("name", ""),
            due_date=write.validated_data.get("due_date"),
            assignees=write.validated_data.get("assignee_ids") or [],
            request=request)

        context = self.get_serializer_context()
        return Response({
            "group": TaskGroupSerializer(
                TaskGroup.objects.annotate(
                    task_count=Count("tasks", distinct=True)).get(pk=group.pk)).data,
            "tasks": TaskListSerializer(
                self._visible().filter(group=group), many=True,
                context=context).data,
        }, status=http_status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Review management (Phase T4.6)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="review-queue")
    def review_queue(self, request):
        """
        What is waiting for a review decision, oldest first, with its age.

        AGE IS MEASURED FROM SUBMISSION
        -------------------------------
        Not from the due date and not from creation. The question this page
        answers is "how long has this been sitting with a reviewer", and a task
        submitted yesterday against a deadline three months out is not a
        bottleneck — while one submitted three weeks ago is, whatever its due
        date says. A review backlog is invisible on a due-date report, which is
        exactly why it gets its own queue.
        """
        today = timezone.localdate()
        rows = (self._filtered()
                .filter(status=Task.Status.UNDER_REVIEW)
                .select_related("reviewer", "created_by", "department")
                .prefetch_related("assignees")
                .order_by("submitted_at"))

        now = timezone.now()
        items = []
        for task in rows:
            waiting = (now - task.submitted_at).days if task.submitted_at else 0
            items.append({
                "task_id": str(task.id),
                "task_number": task.task_number,
                "title": task.title,
                "assignee": ", ".join(r.user_name for r in task.assignees.all())
                            or "Unassigned",
                "reviewer": task.reviewer_name or task.created_by_name or "—",
                "department": task.department_name or "—",
                "priority": task.get_priority_display(),
                "submitted_at": task.submitted_at,
                "waiting_days": waiting,
                "due_date": task.due_date,
                "is_overdue": bool(task.due_date and task.due_date < today
                                   and task.status in Task.OPEN_STATUSES),
            })

        # Backlog per reviewer, so a bottleneck has a name rather than being a
        # number somebody has to eyeball a list for.
        backlog = {}
        for item in items:
            entry = backlog.setdefault(item["reviewer"],
                                       {"reviewer": item["reviewer"],
                                        "waiting": 0, "oldest_days": 0})
            entry["waiting"] += 1
            entry["oldest_days"] = max(entry["oldest_days"], item["waiting_days"])

        ages = [item["waiting_days"] for item in items]
        return Response({
            "rows": items,
            "by_reviewer": sorted(backlog.values(),
                                  key=lambda r: -r["oldest_days"]),
            "summary": {
                "waiting": len(items),
                "oldest_days": max(ages, default=0),
                "average_days": round(sum(ages) / len(ages), 1) if ages else 0,
                # The reviewer report already answers "how long do decisions
                # take"; this is the live queue, so it reports what is WAITING.
                "reviewers": len(backlog),
            },
        })

    # ------------------------------------------------------------------
    # Board, calendar, workload, overdue (Phase T3, Parts 1, 3, 4, 6)
    # ------------------------------------------------------------------
    def _visible(self):
        """
        The caller's visible set, WITHOUT any menu scope applied.

        `get_queryset()` folds `?scope=` in, which is right for the list but
        wrong for a board or a report — those apply their own filters and would
        otherwise be silently narrowed by a scope parameter left on the URL.
        """
        queryset = (Task.objects
                    .select_related("created_by", "reviewer", "department")
                    .prefetch_related(
                        Prefetch("assignees",
                                 queryset=TaskAssignee.objects.select_related("user")),
                        "checklist", "subtasks")
                    .annotate(
                        comment_count=Count("comments", distinct=True),
                        attachment_count=Count(
                            "attachments", distinct=True,
                            filter=Q(attachments__is_removed=False,
                                     attachments__is_evidence=False)),
                        evidence_count=Count(
                            "attachments", distinct=True,
                            filter=Q(attachments__is_removed=False,
                                     attachments__is_evidence=True)),
                    ))
        visible = perms.visible_task_filter(self.request.user)
        if visible is not None:
            queryset = queryset.filter(visible)
        return queryset.distinct()

    def _filtered(self):
        """The visible set with the URL's filters applied, but not its scope."""
        return TaskFilterSet(self.request.query_params,
                             queryset=self._visible(),
                             request=self.request).qs

    @action(detail=False, methods=["get"], url_path="board")
    def board(self, request):
        """
        The Kanban board (Part 1): five columns, cards, read-only.

        The status-to-column mapping is served with the payload rather than
        duplicated in the client — see tasks/board.py. Cancelled tasks have no
        column and are excluded; they remain in the List view and in search.

        `?variant=department` (Phase TASK-GOVERNANCE-HARDENING) drops the
        Backlog column, for the department board - see board.DEPARTMENT_COLUMNS
        on why that column would be empty for every viewer of it.
        """
        variant = request.query_params.get("variant")
        columns_spec = (board_config.DEPARTMENT_COLUMNS if variant == "department"
                        else board_config.COLUMNS)
        statuses = [status for column in columns_spec for status in column["statuses"]]
        rows = (self._filtered()
                .filter(status__in=statuses)
                .order_by("due_date", "-priority", "-created_at"))
        columns = board_config.bucket(rows, columns_spec)
        context = self.get_serializer_context()
        return Response({
            "columns": [
                {
                    "key": column["key"],
                    "label": column["label"],
                    "statuses": column["statuses"],
                    "count": column["count"],
                    "tasks": TaskListSerializer(column["tasks"], many=True,
                                                context=context).data,
                }
                for column in columns
            ],
            "excluded_statuses": list(board_config.EXCLUDED_STATUSES),
            "variant": variant or "all",
            "total": sum(column["count"] for column in columns),
        })

    @action(detail=False, methods=["get"], url_path="calendar")
    def calendar(self, request):
        """
        Tasks as dated events (Part 3), for a Day, Week or Month window.

        The window is computed SERVER-side from `date` and `view` so the client
        cannot ask for an unbounded range — and so "which day does the week start
        on" has one answer (Sunday; see analytics.calendar_range).
        """
        view = (request.query_params.get("view") or "month").lower()
        if view not in ("day", "week", "month"):
            raise ValidationError({"view": "Choose day, week or month."})

        raw = request.query_params.get("date")
        try:
            anchor = datetime.date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            raise ValidationError({"date": "Give a date as YYYY-MM-DD."})

        start, end = analytics.calendar_range(anchor, view)

        # Phase T4.9 — whose calendar. Four lenses over the SAME scoped
        # queryset, so none of them can widen what the caller may see: `team`
        # and `department` narrow an already-narrowed set, they never reach past
        # it. An employee asking for `department` gets their own department's
        # tasks that they can already see, which is a smaller set, not a bigger
        # one.
        owner = (request.query_params.get("owner") or "all").lower()
        if owner not in ("all", "me", "team", "department", "review"):
            raise ValidationError(
                {"owner": "Choose all, me, team, department or review."})

        queryset = self._filtered()
        if owner == "me":
            queryset = queryset.filter(assignees__user=request.user)
        elif owner == "review":
            # What is on MY plate as a reviewer, dated by when it is due —
            # a review calendar is about the deadline the decision is holding up.
            queryset = queryset.filter(
                Q(reviewer=request.user) | Q(created_by=request.user),
                status__in=[Task.Status.UNDER_REVIEW, Task.Status.COMPLETED])
        elif owner in ("team", "department"):
            queryset = self._team_queryset(queryset)

        events = analytics.calendar_events(queryset.distinct(), start, end)
        context = self.get_serializer_context()
        return Response({
            "view": view,
            "owner": owner,
            "anchor": anchor,
            "start": start,
            "end": end,
            "events": [
                {
                    "date": event["date"],
                    "kind": event["kind"],
                    "task": TaskListSerializer(event["task"], context=context).data,
                }
                for event in events
            ],
            "counts": {
                kind: sum(1 for e in events if e["kind"] == kind)
                for kind in ("due", "overdue", "completed", "upcoming")
            },
        })

    @action(detail=False, methods=["get"], url_path="workload")
    def workload(self, request):
        """
        Who is carrying what (Part 4) — THREE perspectives from one endpoint,
        chosen by the server from the caller's role.

        An employee gets their own four numbers and nothing else; a department
        head additionally gets their team, broken down by person; HR and Admin
        additionally get the departmental view and the utilisation summary. The
        `perspective` key names which one came back, so the page renders what it
        was given rather than guessing from the caller's role — which is how a
        client ends up asking for a block the server did not send.

        Each block is ADDED rather than substituted: a department head is also
        somebody with tasks of their own, and a workload page that hides their
        own load is one they will keep a second list beside.
        """
        user = request.user
        queryset = self._filtered()
        today = timezone.localdate()

        payload = {
            "perspective": "employee",
            "personal": analytics.personal_workload(queryset, user, today=today),
            "generated_at": timezone.now(),
        }

        if perms.is_department_head(user) or perms.has_org_wide_read(user):
            payload["perspective"] = "department"
            payload["by_employee"] = analytics.workload_by_employee(
                queryset, today=today)
            payload["pending_review"] = queryset.filter(
                status=Task.Status.UNDER_REVIEW).count()
            payload["overdue"] = queryset.filter(
                status__in=Task.OPEN_STATUSES, due_date__lt=today).count()

        if perms.has_org_wide_read(user):
            payload["perspective"] = "organisation"
            payload["by_department"] = analytics.workload_by_department(
                queryset, today=today)
            payload["utilisation"] = analytics.resource_utilisation(
                queryset, today=today)

        return Response(payload)

    @action(detail=False, methods=["get"], url_path="overdue")
    def overdue(self, request):
        """
        The overdue screen (Part 6): days late, assignee, department, priority,
        reviewer. Scoped like everything else, so a department head sees their
        department's and an employee sees their own.
        """
        return Response(analytics.report_overdue(self._filtered()))

    @action(detail=False, methods=["get"], url_path="planner")
    def planner_view(self, request):
        """
        A week, a month or a quarter of the caller's visible work, in lanes.

        Scoped exactly like every other list here: a department head plans their
        department, HR and the Board see the organisation. `?department=` narrows
        further for somebody who can see more than one.
        """
        period = request.query_params.get("view", "monthly")
        if period not in planner.PERIODS:
            raise ValidationError({"view": f"Choose one of {', '.join(planner.PERIODS)}."})
        anchor = request.query_params.get("date")
        try:
            anchor = (datetime.date.fromisoformat(anchor) if anchor
                      else timezone.localdate())
        except ValueError:
            raise ValidationError({"date": "Use YYYY-MM-DD."})

        queryset = self._filtered().exclude(status=Task.Status.CANCELLED)
        payload = planner.plan(queryset, period, anchor=anchor,
                               today=timezone.localdate())
        context = self.get_serializer_context()
        payload["lanes"] = [{**lane,
                             "tasks": TaskListSerializer(lane["tasks"], many=True,
                                                         context=context).data}
                            for lane in payload["lanes"]]
        goals = planner.visible_goals(
            request.user,
            DepartmentGoal.objects.select_related("department").prefetch_related("tasks"))
        # The goals whose window OVERLAPS the plan's, not only those inside it:
        # an annual goal is exactly what a weekly plan is serving.
        goals = goals.filter(starts_on__lte=payload["end"],
                             ends_on__gte=payload["start"])
        department = request.query_params.get("department")
        if department:
            goals = goals.filter(department_id=department)
        payload["goals"] = planner.goal_rows(goals)
        return Response(payload)

    @action(detail=False, methods=["get"], url_path="department-kpis")
    def department_kpis(self, request):
        """
        The Department Head's five figures for a window, over their own
        department's work. HR, Admin and the Board get the same shape over
        whatever they can see.
        """
        if not (perms.is_department_head(request.user)
                or perms.has_org_wide_read(request.user)):
            raise PermissionDenied(
                "Department KPIs are for the people answerable for a department.")
        period = request.query_params.get("view", "monthly")
        if period not in planner.PERIODS:
            raise ValidationError({"view": f"Choose one of {', '.join(planner.PERIODS)}."})
        start, end, _ = planner.window(period, timezone.localdate())
        return Response(planner.kpis(self._filtered(), start, end,
                                     today=timezone.localdate()))

    @action(detail=False, methods=["get"], url_path="filter-options")
    def filter_options(self, request):
        """
        The values the advanced filter can offer (Part 2).

        Derived from the caller's OWN visible set rather than from the
        directory: a filter listing departments somebody cannot see any tasks in
        is a list of dead ends, and it leaks the shape of the organisation to an
        employee who has no business with it.
        """
        queryset = self._visible()
        departments = sorted({
            name for name in queryset.values_list("department_name", flat=True)
            if name
        })
        assignees = {}
        for task in queryset.prefetch_related("assignees"):
            for row in task.assignees.all():
                if row.user_id:
                    assignees[str(row.user_id)] = row.user_name or "Unknown"
        # Reviewers are a separate list: the two sets overlap but are not the
        # same people, and offering an assignee list under a Reviewer label
        # would produce filters that match nothing.
        reviewers = {
            str(task.reviewer_id): task.reviewer_name or "Unknown"
            for task in queryset if task.reviewer_id
        }
        return Response({
            "departments": departments,
            "assignees": [{"id": key, "name": value}
                          for key, value in sorted(assignees.items(),
                                                   key=lambda kv: kv[1])],
            "reviewers": [{"id": key, "name": value}
                          for key, value in sorted(reviewers.items(),
                                                   key=lambda kv: kv[1])],
            # What the create form pre-selects. BOTH are defaults, never rules:
            # the creator may assign the task to any active user and choose any
            # reviewer. The server decides them so the form does not have to
            # know what a department head is, or who is logged in.
            #
            # Assigned To defaults to the CREATOR, because most tasks somebody
            # writes are their own - and the field is mandatory, so a default
            # that is right most of the time is the difference between one click
            # and three.
            "default_assignee": _person(request.user),
            "default_reviewer": _default_reviewer(request.user),
            "statuses": [{"value": v, "label": l} for v, l in Task.Status.choices],
            "priorities": [{"value": v, "label": l} for v, l in Task.Priority.choices],
        })

    # ------------------------------------------------------------------
    # Reports (Phase T3, Part 8)
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="reports")
    def reports(self, request):
        """The catalogue. One list, so a new report appears without a UI change."""
        return Response([
            {"slug": slug, "label": spec["label"],
             "description": spec["description"]}
            for slug, spec in analytics.REPORTS.items()
        ])

    @action(detail=False, methods=["get"], url_path=r"reports/(?P<slug>[a-z-]+)")
    def report_detail(self, request, slug=None):
        """
        One report, over the caller's visible set with the URL's filters applied.

        `?export=csv` returns the same rows as a file. Reports are read-only and
        derived, so there is no permission of their own: a report can never
        contain a task the caller could not open individually.
        """
        spec = analytics.REPORTS.get(slug)
        if spec is None:
            raise NotFound(f"No report called '{slug}'.")

        payload = spec["build"](self._filtered(), today=timezone.localdate())
        export = request.query_params.get("export")
        if export == "csv":
            return self._csv(slug, spec["label"], payload)
        if export == "pdf":
            return self._pdf(slug, spec["label"], payload)
        return Response({"slug": slug, "label": spec["label"], **payload})

    def _pdf(self, slug, label, payload):
        """
        Render a report as PDF.

        WeasyPrint is used directly — it is the project's PDF library, already a
        dependency, and reaching for it is not the same as importing the memo or
        minute modules that also use it. A shared library is infrastructure; a
        peer module is not, and this app imports none.

        If WeasyPrint is unavailable the caller gets a 503 naming the reason
        rather than a 500: a missing rendering library is a deployment problem,
        and saying so is more useful than a stack trace.
        """
        try:
            from weasyprint import HTML
        except Exception:  # pragma: no cover - present in the project image
            logger.error("WeasyPrint unavailable; task report PDF export refused.")
            return Response(
                {"detail": "PDF export is unavailable on this server. "
                           "Export as CSV instead."},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE)

        html = render_to_string("tasks/report.html", {
            "label": label,
            "columns": payload["columns"],
            # Flattened to cells aligned with the columns, rather than dicts the
            # template would need a custom lookup filter to read. One fewer
            # moving part, and the CSV writer below does exactly the same thing —
            # so the two exports cannot drift on column order.
            "rows": [
                [row.get(column["key"]) for column in payload["columns"]]
                for row in payload["rows"]
            ],
            "summary": payload["summary"],
            "generated_at": timezone.now(),
            "generated_by": user_snapshot(self.request.user)["name"],
        })
        pdf = HTML(string=html).write_pdf()
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{slug}-report-{timezone.localdate()}.pdf"')
        return harden_file_response(response)

    def _csv(self, slug, label, payload):
        """
        Render a report as CSV.

        Streamed through the same hardening as an uploaded file: a spreadsheet
        is opened by a desktop application, and `nosniff` plus the sandbox CSP
        stop a browser rendering it in this origin.
        """
        # UTF-8 WITH A BOM, and a charset on the content type.
        #
        # Excel on Windows does not sniff encodings: it reads a CSV as the system
        # codepage unless the file opens with a byte-order mark. Without one, a
        # Nepali name — or any Devanagari, or a smart quote — arrives as mojibake in
        # the one application most of these exports are opened in, and the person
        # looking at it has no way to tell whether the data or the export is wrong.
        #
        # `analytics/exports.py` and `reports/workforce_reports.py` have done this
        # since they were written; these two writers were the ones that diverged.
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="{slug}-report-{timezone.localdate()}.csv"')
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow([column["label"] for column in payload["columns"]])
        for row in payload["rows"]:
            writer.writerow([row.get(column["key"], "")
                             for column in payload["columns"]])
        return harden_file_response(response)

    # ------------------------------------------------------------------
    # Templates (Phase T2.9)
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="save-as-template")
    def save_as_template(self, request, pk=None):
        """Freeze this task's shape for reuse. See tasks.workflow.save_as_template."""
        task = self.get_object()
        if not perms.can_manage_templates(request.user):
            raise PermissionDenied(
                "Only a Department Head, HR or an Admin may create templates.")
        write = SaveAsTemplateSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        template = workflow.save_as_template(
            task, request.user,
            name=write.validated_data["name"],
            description=write.validated_data.get("description", ""),
            due_in_days=write.validated_data.get("default_due_in_days"),
            request=request)
        return Response(TaskTemplateDetailSerializer(template).data,
                        status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="apply-template")
    def apply_template(self, request, pk=None):
        """Copy a template's checklist onto this task."""
        task = self.get_object()
        if not perms.can_apply_template(request.user, task):
            raise PermissionDenied("The checklist can no longer be changed.")
        write = ApplyTemplateSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        workflow.apply_template(task, write.validated_data["template"],
                                request.user, request=request)
        return Response(self._detail(task))

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        """
        The counts behind every tile, for whichever role is asking.

        Computed over `base` — the caller's own visible set, unscoped by any
        menu — so a tile can never advertise a task the caller cannot open. The
        role-specific blocks are ADDED to the personal ones rather than replacing
        them: an HR officer is also an employee with tasks of their own, and a
        dashboard that hides those is why people keep a second list on paper.
        """
        user = request.user
        today = timezone.localdate()

        base = Task.objects.all()
        visible = perms.visible_task_filter(user)
        if visible is not None:
            base = base.filter(visible)
        base = base.distinct()

        mine = base.filter(assignees__user=user)
        payload = {
            # Employee tiles — the specification's four.
            "my_tasks": mine.filter(status__in=Task.ACTIVE_STATUSES).count(),
            "due_today": mine.filter(due_date=today,
                                     status__in=Task.OPEN_STATUSES).count(),
            "overdue": mine.filter(due_date__lt=today,
                                   status__in=Task.OPEN_STATUSES).count(),
            "completed": mine.filter(status__in=[Task.Status.COMPLETED,
                                                 Task.Status.CLOSED]).count(),
            # The single "waiting on me" number the sidebar badge reads.
            "needs_my_action": mine.filter(
                status__in=[Task.Status.ASSIGNED, Task.Status.ACCEPTED,
                            Task.Status.IN_PROGRESS]).count(),
            "to_accept": mine.filter(status=Task.Status.ASSIGNED).count(),
            "assigned_by_me": base.filter(
                Q(created_by=user) | Q(reviewer=user)).exclude(
                status__in=Task.TERMINAL_STATUSES).count(),
            "my_drafts": base.filter(created_by=user,
                                     status=Task.Status.DRAFT).count(),
            "blocked": mine.filter(status=Task.Status.BLOCKED).count(),
            # Phase T3 Part 5. None, not 0.0, when nothing has completed yet —
            # "no data" and "same day" are different answers, and a dashboard
            # reading 0.0 days would be understood as the second.
            "average_completion_days": analytics.average_completion_days(mine),
        }

        if perms.is_department_head(user) or perms.has_org_wide_read(user):
            team = self._team_scope(base, user)
            team_open = team.filter(status__in=Task.OPEN_STATUSES).count()
            team_done = team.filter(status__in=[Task.Status.COMPLETED,
                                                Task.Status.CLOSED]).count()
            total = team_open + team_done
            payload.update({
                "team_tasks": team_open,
                "team_completed": team_done,
                "team_overdue": team.filter(
                    due_date__lt=today, status__in=Task.OPEN_STATUSES).count(),
                # Integer percent, and 0 rather than a division by zero when a
                # department has no tasks at all.
                "team_completion_percent": round(100 * team_done / total) if total else 0,
                "pending_review": team.filter(
                    status=Task.Status.UNDER_REVIEW).count(),
                "pending_closure": team.filter(
                    status=Task.Status.COMPLETED).count(),
                "team_overdue_percent": (
                    round(100 * team.filter(
                        due_date__lt=today,
                        status__in=Task.OPEN_STATUSES).count() / total)
                    if total else 0),
                "team_average_completion_days":
                    analytics.average_completion_days(team),
            })

        if perms.has_org_wide_read(user):
            org = base.exclude(status=Task.Status.DRAFT)
            org_done = org.filter(status__in=[Task.Status.COMPLETED,
                                              Task.Status.CLOSED]).count()
            org_total = org.count()
            payload.update({
                "org_open": org.filter(status__in=Task.OPEN_STATUSES).count(),
                "org_completed": org_done,
                "org_overdue": org.filter(
                    due_date__lt=today, status__in=Task.OPEN_STATUSES).count(),
                "org_total": org_total,
                "org_completion_percent":
                    round(100 * org_done / org_total) if org_total else 0,
                "org_overdue_percent": (
                    round(100 * org.filter(
                        due_date__lt=today,
                        status__in=Task.OPEN_STATUSES).count() / org_total)
                    if org_total else 0),
                "org_average_completion_days":
                    analytics.average_completion_days(org),
                # Phase T3 Part 5 — "Task Distribution". The status report,
                # reused rather than recomputed, so the dashboard tile and the
                # report can never disagree about the same number.
                "distribution": analytics.report_status(org)["rows"],
                # Phase T4.11 — the completion trend, twelve weeks back.
                "completion_trend": analytics.completion_trend(org),
                "by_department": [
                    {"label": row["department_name"] or "Unassigned",
                     "open": row["open_count"], "total": row["total"]}
                    for row in org.values("department_name").annotate(
                        total=Count("id", distinct=True),
                        open_count=Count("id", distinct=True, filter=Q(
                            status__in=Task.OPEN_STATUSES)),
                    ).order_by("-total")[:20]
                ],
            })

        # `distinct=True` on every aggregate, not decoration: `visible` LEFT
        # JOINs the assignee table, so a task with three assignees yields three
        # joined rows for anybody matched by one of the other OR branches (its
        # creator, say). A plain COUNT over that reports three tasks where there
        # is one. `.distinct()` on the queryset does NOT fix this — it dedupes
        # the outer rows of a GROUP BY, not the rows being counted inside it.
        payload["by_status"] = [
            {"value": row["status"], "label": dict(Task.Status.choices)[row["status"]],
             "count": row["count"]}
            for row in base.values("status").annotate(
                count=Count("id", distinct=True)).order_by()
        ]
        payload["by_priority"] = [
            {"value": row["priority"],
             "label": dict(Task.Priority.choices)[row["priority"]],
             "count": row["count"]}
            for row in base.values("priority").annotate(
                count=Count("id", distinct=True)).order_by()
        ]
        return Response(payload)

    def _team_scope(self, base, user):
        if perms.has_org_wide_read(user):
            return base.exclude(status=Task.Status.DRAFT)
        dept_ids = perms.department_ids_in_scope(user)
        scope = Q()
        if dept_ids:
            scope |= Q(department_id__in=dept_ids)
        if getattr(user, "department", ""):
            scope |= Q(department_name__iexact=user.department)
        if not scope:
            return base.none()
        return base.filter(scope).exclude(status=Task.Status.DRAFT)

    # ------------------------------------------------------------------
    # Employee picker
    # ------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="employees")
    def employees(self, request):
        """
        The employee search behind the Assigned To field.

        The specification is explicit that a task is NOT assigned by department
        alone — a person is searched for and selected.

        Open to every authenticated user since Phase TASK-SIMPLIFICATION: the
        creator of a task chooses its assignee AND its reviewer, so gating this
        would leave most people unable to fill in either field. It answers the
        same question the directory already answers for a memo's recipients, and
        it returns names and designations only — never contact details, never
        anything about employment.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()
        query = (request.query_params.get("search") or "").strip()
        rows = User.objects.filter(is_active=True)
        if query:
            rows = rows.filter(
                Q(first_name__icontains=query) | Q(last_name__icontains=query)
                | Q(username__icontains=query) | Q(email__icontains=query)
                | Q(employee_id__icontains=query))
        rows = rows.select_related("department_ref").order_by(
            "first_name", "last_name")[:25]
        return Response([
            {"id": str(u.id),
             "full_name": u.get_full_name() or u.username,
             "email": u.email,
             "designation": u.designation or "",
             "department": u.department_name or ""}
            for u in rows
        ])


class DepartmentGoalViewSet(viewsets.ModelViewSet):
    """
    /api/v1/department-goals/ — what each department is trying to achieve
    (Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP).

    READ is scoped like tasks are: your own department, or every department for
    the roles that read the whole organisation (planner.visible_goals). WRITE is
    the department head's, HR's and an Admin's - and the Board's never, which is
    the same line every other operational write in this module draws.

    Progress is not writable at all. It is the share of linked tasks that are
    finished, computed on read, and there is no field to type over it.
    """
    serializer_class = DepartmentGoalSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["department", "period", "status"]
    search_fields = ["title", "description", "department_name"]

    def get_queryset(self):
        queryset = (DepartmentGoal.objects
                    .select_related("department")
                    .prefetch_related("tasks"))
        return planner.visible_goals(self.request.user, queryset)

    def _require_owner(self, department_id=None):
        if not perms.can_manage_goals(self.request.user, department_id):
            raise PermissionDenied(
                "Only the head of that department, HR or an Admin may set its goals.")

    def perform_create(self, serializer):
        department = serializer.validated_data["department"]
        self._require_owner(department.pk)
        snapshot = user_snapshot(self.request.user)
        serializer.save(created_by=self.request.user,
                        created_by_name=snapshot["name"],
                        department_name=department.name)

    def perform_update(self, serializer):
        self._require_owner(serializer.instance.department_id)
        department = serializer.validated_data.get("department")
        if department is not None:
            self._require_owner(department.pk)
            serializer.save(department_name=department.name)
        else:
            serializer.save()

    def perform_destroy(self, instance):
        """
        A goal with work under it is CLOSED, not deleted: the tasks raised
        against it are part of a department's record, and deleting the heading
        would leave them explaining nothing. An empty goal is genuinely just a
        mistake and can go.
        """
        self._require_owner(instance.department_id)
        if instance.tasks.exists():
            instance.status = DepartmentGoal.Status.CANCELLED
            instance.save(update_fields=["status", "updated_at"])
            return
        instance.delete()


class TaskTemplateViewSet(viewsets.ModelViewSet):
    """
    Reusable task shapes (Phase T2.9).

    READ IS OPEN, WRITE IS NOT
    --------------------------
    Every authenticated user may LIST templates: an employee cannot create a
    task, but they can be shown what a "Monthly Report" involves, and hiding the
    catalogue from them buys nothing. Creating, editing and retiring one is the
    same authority as creating a task — a template is a shape somebody else will
    be held to.

    RETIRED, NOT DELETED
    --------------------
    `destroy` sets `is_active = False`. A hard delete would strip the name off
    every task raised from the template, and "where did this checklist come
    from" is exactly what somebody asks two years later.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TaskTemplateDetailSerializer
    search_fields = ["name", "description", "department_name"]
    ordering_fields = ["name", "usage_count", "created_at"]
    ordering = ["-usage_count", "name"]

    def get_queryset(self):
        queryset = (TaskTemplate.objects
                    .select_related("department", "created_by")
                    .prefetch_related("items", "groups__items"))
        # Retired templates are hidden unless explicitly asked for, so the
        # picker never offers one that should not be used again.
        if self.request.query_params.get("include_inactive") not in ("1", "true"):
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return TaskTemplateWriteSerializer
        if self.action == "list":
            return TaskTemplateListSerializer
        return TaskTemplateDetailSerializer

    def _require_manager(self):
        if not perms.can_manage_templates(self.request.user):
            raise PermissionDenied(
                "Only a Department Head, HR or an Admin may manage templates.")

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self._require_manager()
        write = self.get_serializer(data=request.data)
        write.is_valid(raise_exception=True)
        items = write.validated_data.pop("items", [])
        groups = write.validated_data.pop("groups", [])
        snapshot = user_snapshot(request.user)
        template = write.save(created_by=request.user,
                              created_by_name=snapshot["name"])
        if template.department_id:
            template.department_name = template.department.name
            template.save(update_fields=["department_name"])
        self._replace_template_lines(template, items, groups)
        return Response(TaskTemplateDetailSerializer(
            self.get_queryset().get(pk=template.pk)).data,
            status=http_status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self._require_manager()
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        write = TaskTemplateWriteSerializer(instance, data=request.data,
                                            partial=partial)
        write.is_valid(raise_exception=True)
        items = write.validated_data.pop("items", None)
        groups = write.validated_data.pop("groups", None)
        template = write.save()
        if template.department_id:
            template.department_name = template.department.name
            template.save(update_fields=["department_name"])
        if items is not None or groups is not None:
            self._replace_template_lines(template, items or [], groups or [])
        return Response(TaskTemplateDetailSerializer(
            self.get_queryset().get(pk=template.pk)).data)

    def destroy(self, request, *args, **kwargs):
        """Retire rather than delete. See the class docstring."""
        self._require_manager()
        template = self.get_object()
        template.is_active = False
        template.save(update_fields=["is_active"])
        return Response(TaskTemplateDetailSerializer(template).data)

    def _replace_template_lines(self, template, items, groups):
        """
        Rebuild a template's checklist.

        No tick preservation here, unlike the task checklist: a template has no
        completion state to preserve. Groups are deleted first because deleting
        one CASCADEs to its items.
        """
        from .models import TaskTemplateGroup, TaskTemplateItem

        template.groups.all().delete()
        template.items.all().delete()

        # `organization_id` is copied from the template on every row: these
        # children belong to whichever tenant owns the template, and
        # bulk_create() does not emit pre_save, so tenancy.stamping's receiver
        # never runs for them (Phase S2).
        org_id = template.organization_id
        rows = [TaskTemplateItem(template=template, organization_id=org_id,
                                 text=text, position=position)
                for position, text in enumerate(items)]
        for group_position, spec in enumerate(groups):
            group = TaskTemplateGroup.objects.create(
                template=template, organization_id=org_id,
                title=spec["title"], position=group_position)
            rows.extend(
                TaskTemplateItem(template=template, organization_id=org_id,
                                 group=group, text=text, position=position)
                for position, text in enumerate(spec.get("items", [])))
        TaskTemplateItem.objects.bulk_create(rows)
        return template
