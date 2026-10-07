"""
Task serializers.

THE LIST SERIALIZER IS SLIM ON PURPOSE
--------------------------------------
`TaskListSerializer` omits the description, the checklist rows, the comments and
the timeline. A 50-row list page that carried them would be dominated by data no
list renders. Everything the list DOES compute per row — the assignee names, the
overdue flag, the checklist tally — is derived in PYTHON from prefetched rows,
never with `.filter()` on a related manager, which bypasses prefetch_related and
fires a query per row.

CAPABILITY FLAGS, NOT CLIENT-SIDE RULES
---------------------------------------
The detail serializer emits `can_*` booleans computed from tasks.permissions. The
UI renders its buttons purely from those, so authorization has exactly one home
on the server and the client cannot offer an action the API would refuse.

NOTHING HERE CHANGES STATUS
---------------------------
The write serializers accept the task's DEFINITION only — title, description,
priority, due date, department, reviewer. `status` is read-only everywhere,
because tasks.workflow is the only thing that may assign to it.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from . import permissions as perms
from .models import (
    DepartmentGoal, Task, TaskAssignee, TaskAttachment, TaskAttachmentDownload,
    TaskAuditLog, TaskChecklistGroup, TaskChecklistItem, TaskComment,
    TaskCommentMention, TaskDependency, TaskGroup, TaskSubtask, TaskTemplate,
    TaskTemplateGroup, TaskTemplateItem,
)
from .services import MIN_REMARK_LENGTH
from common.html_sanitizer import sanitize_html

User = get_user_model()


class UserMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "designation", "department"]
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


# ---------------------------------------------------------------------------
# Children
# ---------------------------------------------------------------------------
class TaskAssigneeSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)
    has_accepted = serializers.BooleanField(read_only=True)

    class Meta:
        model = TaskAssignee
        # `progress_percent` is null for somebody who has not reported yet, and
        # the page shows that differently from 0% — see
        # TaskAssignee.progress_percent (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).
        fields = ["id", "user", "user_name", "designation", "department_label",
                  "is_primary", "has_accepted", "accepted_at", "added_at",
                  "progress_percent", "progress_updated_at"]
        read_only_fields = fields


class TaskChecklistItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskChecklistItem
        fields = ["id", "group", "text", "position", "is_done", "done_by_name",
                  "done_at", "created_at"]
        read_only_fields = ["id", "is_done", "done_by_name", "done_at", "created_at"]


class TaskChecklistGroupSerializer(serializers.ModelSerializer):
    """
    A checklist section with its items nested.

    `done_count`/`total_count` read the PREFETCHED items rather than issuing a
    count per group — a task with eight sections would otherwise cost sixteen
    extra queries to render one page.
    """
    items = TaskChecklistItemSerializer(many=True, read_only=True)
    done_count = serializers.IntegerField(read_only=True)
    total_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TaskChecklistGroup
        fields = ["id", "title", "position", "items", "done_count", "total_count",
                  "created_at"]
        read_only_fields = fields


class TaskSubtaskSerializer(serializers.ModelSerializer):
    """
    One subtask (Phase TASK-AUTOSAVE-AND-SUBTASKS), with the two counts and the
    two per-row flags the panel needs so it never issues a request per row.

    `comment_count` / `evidence_count` read the PREFETCHED task comments and
    attachments through `self.context["task"]` when the detail view supplies
    it; a bare serialisation (the subtask endpoints) falls back to a query.
    """
    is_overdue = serializers.BooleanField(read_only=True)
    comment_count = serializers.SerializerMethodField()
    evidence_count = serializers.SerializerMethodField()
    can_complete = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = TaskSubtask
        fields = ["id", "title", "description", "position", "assignee",
                  "assignee_name", "due_date", "is_done", "done_by_name",
                  "done_at", "is_overdue", "comment_count", "evidence_count",
                  "can_complete", "can_edit", "created_by_name", "created_at",
                  "updated_at"]
        read_only_fields = fields

    def _task_rows(self, obj, relation):
        task = self.context.get("task")
        if task is not None and task.pk == obj.task_id:
            return [row for row in getattr(task, relation).all()
                    if row.subtask_id == obj.pk]
        return list(getattr(obj, relation).all())

    def get_comment_count(self, obj):
        return len(self._task_rows(obj, "comments"))

    def get_evidence_count(self, obj):
        return sum(1 for a in self._task_rows(obj, "attachments")
                   if a.is_evidence and not a.is_removed)

    def _user(self):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return None
        return request.user

    def get_can_complete(self, obj):
        user = self._user()
        return bool(user) and perms.can_complete_subtask(user, obj)

    def get_can_edit(self, obj):
        user = self._user()
        return bool(user) and perms.can_edit_subtask(user, obj)


class SubtaskWriteSerializer(serializers.Serializer):
    """
    Create or edit one subtask. `assignee` must be one of the PARENT's
    assignees - the invariant that keeps acceptance and visibility unchanged.
    The view passes the task in `context`.
    """
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True,
                                        max_length=4000)
    assignee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True), required=False,
        allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)

    def validate_title(self, value):
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("Give the subtask a title.")
        return text

    def validate_assignee(self, value):
        task = self.context.get("task")
        if value is None or task is None:
            return value
        if not any(row.user_id == value.id for row in task.assignees.all()):
            raise serializers.ValidationError(
                "A subtask can only be assigned to somebody who is on the task. "
                "Add them to the task's assignees first.")
        return value


class SubtaskCompleteSerializer(serializers.Serializer):
    is_done = serializers.BooleanField()


class SubtaskReorderSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class TaskAttachmentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    kind = serializers.CharField(read_only=True)
    download_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TaskAttachment
        # `uploaded_by` (the id, not just the name) is here because the client
        # has to decide whether to OFFER the remove and reclassify buttons, and
        # tasks.permissions.can_remove_attachment keys that on the uploader.
        # Without the id the uploader would never see their own controls and
        # would have to ask the task's owner to withdraw their own file.
        fields = ["id", "kind", "original_name", "link_url", "size", "content_type",
                  "caption", "is_evidence", "uploaded_by", "uploaded_by_name",
                  "uploaded_at", "download_url", "download_count", "is_removed",
                  "removed_at", "removed_by_name", "subtask"]
        read_only_fields = fields

    def get_download_url(self, obj):
        """
        The API-relative path the client fetches, or None for a link row — a link
        is opened at its own URL and has nothing to download from us.

        Uploaded files are never served from a public /media/ path: the project
        serves them only through an authenticated, task-scoped view.
        """
        if obj.kind == "link":
            return None
        return f"/api/v1/tasks/{obj.task_id}/attachments/{obj.id}/download/"


class TaskAttachmentDownloadSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskAttachmentDownload
        fields = ["id", "user_name", "ip_address", "downloaded_at"]
        read_only_fields = fields


class TaskCommentMentionSerializer(serializers.ModelSerializer):
    """Who was named. `user` is the id, so the client can highlight and link it."""

    class Meta:
        model = TaskCommentMention
        fields = ["id", "user", "user_name"]
        read_only_fields = fields


class TaskCommentSerializer(serializers.ModelSerializer):
    """
    One comment. Replies are nested one level deep — see TaskComment's docstring
    on why not further.

    `replies` reads the PREFETCHED reverse relation, so a thread of twenty
    comments costs one extra query for the whole page rather than twenty.
    """
    author = UserMiniSerializer(read_only=True)
    mentions = TaskCommentMentionSerializer(many=True, read_only=True)
    is_edited = serializers.BooleanField(read_only=True)
    replies = serializers.SerializerMethodField()

    class Meta:
        model = TaskComment
        fields = ["id", "parent", "subtask", "author", "author_name", "body",
                  "mentions", "created_at", "edited_at", "is_edited", "replies"]
        read_only_fields = ["id", "subtask", "author", "author_name", "created_at",
                            "edited_at", "is_edited", "mentions", "replies"]

    def get_replies(self, obj):
        # Only a top-level comment renders its replies; a reply has none, and
        # recursing here is what would turn one level into an accidental tree.
        if obj.parent_id is not None:
            return []
        return TaskCommentReplySerializer(
            obj.replies.all(), many=True, context=self.context).data

    def validate_body(self, value):
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("A comment cannot be empty.")
        return text


class TaskCommentReplySerializer(serializers.ModelSerializer):
    """A reply. Identical to its parent's shape minus `replies`, which it cannot have."""
    author = UserMiniSerializer(read_only=True)
    mentions = TaskCommentMentionSerializer(many=True, read_only=True)
    is_edited = serializers.BooleanField(read_only=True)

    class Meta:
        model = TaskComment
        fields = ["id", "parent", "subtask", "author", "author_name", "body",
                  "mentions", "created_at", "edited_at", "is_edited"]
        read_only_fields = fields


class CommentWriteSerializer(serializers.Serializer):
    """
    Posting or editing a comment.

    `mention_ids` are the ids the client resolved from its picker, not names
    scraped out of the body — see TaskCommentMention on why parsing "@Bikash"
    server-side cannot work. Ids that turn out not to be able to read the task
    are dropped rather than rejected, so a stale picker cannot fail the whole
    comment.
    """
    body = serializers.CharField(max_length=8000)
    parent = serializers.PrimaryKeyRelatedField(
        queryset=TaskComment.objects.all(), required=False, allow_null=True)
    # Phase TASK-AUTOSAVE-AND-SUBTASKS: thread under one subtask. The view
    # checks it belongs to this task.
    subtask = serializers.PrimaryKeyRelatedField(
        queryset=TaskSubtask.objects.all(), required=False, allow_null=True)
    mention_ids = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(
            queryset=User.objects.filter(is_active=True)),
        required=False, allow_empty=True)

    def validate_body(self, value):
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("A comment cannot be empty.")
        return text


class TaskAuditLogSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = TaskAuditLog
        fields = ["id", "action", "action_label", "actor_name", "from_status",
                  "to_status", "remarks", "metadata", "created_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------
class TaskDependencySerializer(serializers.ModelSerializer):
    """
    One "waits for" edge, as the task page and the API report it
    (Phase TASK-GOVERNANCE-HARDENING).

    The prerequisite's NUMBER, TITLE and STATUS travel with the row. A bare id
    would make the panel useless without a request per edge, and the status is
    the whole point: it is what says whether this row is still in the way.
    """
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    depends_on_number = serializers.CharField(source="depends_on.task_number",
                                              read_only=True)
    depends_on_title = serializers.CharField(source="depends_on.title", read_only=True)
    depends_on_status = serializers.CharField(source="depends_on.status", read_only=True)
    depends_on_status_label = serializers.CharField(
        source="depends_on.get_status_display", read_only=True)
    is_satisfied = serializers.BooleanField(read_only=True)
    # The WAITING side, for the mirror view: a task's `dependents` are rows where
    # it is the prerequisite, and there the interesting task is the other one.
    task_number = serializers.CharField(source="task.task_number", read_only=True)
    task_title = serializers.CharField(source="task.title", read_only=True)
    task_status_label = serializers.CharField(source="task.get_status_display",
                                              read_only=True)

    class Meta:
        model = TaskDependency
        fields = ["id", "kind", "kind_label", "note", "task", "task_number",
                  "task_title", "task_status_label", "depends_on",
                  "depends_on_number", "depends_on_title", "depends_on_status",
                  "depends_on_status_label", "is_satisfied", "created_by_name",
                  "created_at"]
        read_only_fields = fields


class DependencyWriteSerializer(serializers.Serializer):
    depends_on = serializers.PrimaryKeyRelatedField(queryset=Task.objects.all())
    kind = serializers.ChoiceField(choices=TaskDependency.Kind.choices,
                                   default=TaskDependency.Kind.BLOCKED_BY)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class DepartmentGoalSerializer(serializers.ModelSerializer):
    """
    A goal and how far its work has got. `percent` is None when nothing is
    linked - see DepartmentGoal.progress on why that is not 0.
    """
    period_label = serializers.CharField(source="get_period_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    linked_tasks = serializers.SerializerMethodField()
    completed_tasks = serializers.SerializerMethodField()
    percent = serializers.SerializerMethodField()

    class Meta:
        model = DepartmentGoal
        fields = ["id", "department", "department_name", "title", "description",
                  "period", "period_label", "starts_on", "ends_on", "status",
                  "status_label", "linked_tasks", "completed_tasks", "percent",
                  "created_by_name", "created_at", "updated_at"]
        read_only_fields = ["id", "department_name", "period_label", "status_label",
                            "linked_tasks", "completed_tasks", "percent",
                            "created_by_name", "created_at", "updated_at"]

    def _progress(self, obj):
        if not hasattr(obj, "_progress_cache"):
            obj._progress_cache = obj.progress()
        return obj._progress_cache

    def get_linked_tasks(self, obj):
        return self._progress(obj)["linked_tasks"]

    def get_completed_tasks(self, obj):
        return self._progress(obj)["completed_tasks"]

    def get_percent(self, obj):
        return self._progress(obj)["percent"]

    def validate(self, attrs):
        """The window must run forwards, and the goal must belong to somebody."""
        attrs = super().validate(attrs)
        instance = self.instance
        starts = attrs.get("starts_on", getattr(instance, "starts_on", None))
        ends = attrs.get("ends_on", getattr(instance, "ends_on", None))
        if starts and ends and ends < starts:
            raise serializers.ValidationError(
                {"ends_on": "A goal cannot end before it starts."})

        department = attrs.get("department", getattr(instance, "department", None))
        request = self.context.get("request")
        if request is not None and department is not None:
            if not perms.can_manage_goals(request.user, department.pk):
                raise serializers.ValidationError({"department": (
                    "You can only set goals for a department you are the head of. "
                    "HR and an Admin may set them for any department.")})
        return attrs


class TaskListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    priority_label = serializers.CharField(source="get_priority_display", read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    assignee_names = serializers.SerializerMethodField()
    checklist_done = serializers.SerializerMethodField()
    checklist_total = serializers.SerializerMethodField()
    # Phase TASK-AUTOSAVE-AND-SUBTASKS. Read off the prefetched rows (see
    # Task.subtask_done) so a card can say "3 / 5" without a query.
    subtask_done = serializers.IntegerField(read_only=True)
    subtask_total = serializers.IntegerField(read_only=True)
    # Phase T2.8 — what a board card shows beyond the title. Annotated on the
    # queryset (tasks.views.TaskViewSet.get_queryset) rather than counted per
    # row, so a 50-card board is one query rather than a hundred.
    comment_count = serializers.IntegerField(read_only=True)
    attachment_count = serializers.IntegerField(read_only=True)
    evidence_count = serializers.IntegerField(read_only=True)
    # Phase T3 Part 6. How late, in whole days — 0 when the task is not late.
    # Computed rather than stored: it changes every midnight, and a stored copy
    # would be wrong for most of the day it was written.
    overdue_days = serializers.SerializerMethodField()
    # Phase TASK-MANAGEMENT-ASANA-MODEL. The card shows the kind of work and who
    # holds it; both are on the LIST payload because a board card must not need a
    # detail request to render.
    task_type_label = serializers.CharField(source="get_task_type_display",
                                            read_only=True)
    # LEGACY (Phase TASK-SIMPLIFICATION): `task_type` is kept on the payload so
    # existing API consumers do not break, and on the row so historical tasks
    # keep the label they were raised under. Nothing branches on it any more -
    # there is one kind of task.
    # The goal this work counts towards, named on the card so a board says what
    # the work is FOR (Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP).
    goal_title = serializers.CharField(source="goal.title", read_only=True,
                                       default=None)
    owner_name = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    # Phase TASK-GOVERNANCE-HARDENING. Whether this card is waiting on other
    # work. Annotated on the queryset, so a board of fifty cards stays one query.
    blocked_by_count = serializers.IntegerField(read_only=True, default=0)
    # Phase TASK-MULTI-ASSIGNEE-COLLABORATION. Where a shared task's acceptance
    # has got to, on the LIST payload so a card can say "1 of 3 accepted"
    # without a detail request. Every field reads the prefetched assignee rows.
    #
    # `status_label` is untouched beside it: reports, filters and exports are
    # built on that vocabulary, and `workflow_label` is the reading of it a
    # person sees ("Pending Acceptance" / "Ready to Start").
    workflow_label = serializers.CharField(read_only=True)
    acceptance = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = ["id", "task_number", "title", "summary", "status", "status_label",
                  "workflow_label", "acceptance",
                  "task_type", "task_type_label", "priority", "priority_label",
                  "due_date", "is_overdue", "owner_name",
                  "progress_percent", "progress_is_auto", "department_name",
                  "created_by_name", "reviewer_name", "assignee_names",
                  "checklist_done", "checklist_total",
                  "subtask_done", "subtask_total", "comment_count",
                  "attachment_count", "evidence_count", "overdue_days",
                  "blocked_by_count", "goal", "goal_title",
                  "created_at", "updated_at"]
        read_only_fields = fields

    # These read the prefetched list, never a fresh .filter() — see the docstring.
    def get_assignee_names(self, obj):
        return [row.user_name for row in obj.assignees.all()]

    def get_acceptance(self, obj):
        """
        Who has accepted, who has not, and whether the work may start.

        One object rather than five loose fields, because they are only ever
        read together and a card that showed the count without the gate would
        invite the reader to work the rule out for themselves.
        """
        return {
            "accepted": obj.accepted_count,
            "total": obj.assignee_count,
            "all_accepted": obj.all_assignees_accepted,
            "pending_names": obj.pending_assignee_names,
            "is_pending": obj.is_pending_acceptance,
            "requires_all": obj.needs_unanimous_acceptance,
            "work_may_start": obj.work_may_start,
        }

    def get_checklist_done(self, obj):
        return sum(1 for row in obj.checklist.all() if row.is_done)

    def get_checklist_total(self, obj):
        return len(obj.checklist.all())

    def get_overdue_days(self, obj):
        from .analytics import overdue_days

        return overdue_days(obj)

    def get_owner_name(self, obj):
        """
        Whose task this is: the person doing it, else whoever raised it.

        Read off the prefetched assignee rows, never a fresh query - see the
        class docstring on why a board of 50 cards must stay one query.
        """
        rows = list(obj.assignees.all())
        if rows:
            primary = next((r for r in rows if r.is_primary), rows[0])
            return primary.user_name
        return obj.created_by_name

    def get_summary(self, obj):
        """
        The first line or so of the description, for the card. Trimmed on the
        SERVER so a board does not ship every task's full brief to draw a
        two-line card.
        """
        raw = obj.description or ""
        if obj.description_format == Task.DescriptionFormat.HTML:
            # Tags are not a summary. Block boundaries become spaces so the
            # headings of the starter template do not run into each other.
            import html as _html
            import re as _re
            raw = _html.unescape(_re.sub(r"<[^>]+>", " ", raw))
        text = " ".join(raw.split())
        return text[:157] + "..." if len(text) > 160 else text


class TaskDetailSerializer(TaskListSerializer):
    assignees = TaskAssigneeSerializer(many=True, read_only=True)
    checklist = serializers.SerializerMethodField()
    checklist_groups = TaskChecklistGroupSerializer(many=True, read_only=True)
    attachments = serializers.SerializerMethodField()
    evidence = serializers.SerializerMethodField()
    removed_attachments = serializers.SerializerMethodField()
    comments = serializers.SerializerMethodField()
    timeline = TaskAuditLogSerializer(source="audit_entries", many=True, read_only=True)
    created_by = UserMiniSerializer(read_only=True)
    reviewer = UserMiniSerializer(read_only=True)
    checklist_percent = serializers.IntegerField(read_only=True)
    subtasks = serializers.SerializerMethodField()
    subtask_percent = serializers.IntegerField(read_only=True)
    dependencies = TaskDependencySerializer(many=True, read_only=True)
    dependents = TaskDependencySerializer(many=True, read_only=True)
    milestones = serializers.SerializerMethodField()
    template_name = serializers.CharField(source="template.name", read_only=True,
                                          default=None)
    capabilities = serializers.SerializerMethodField()

    class Meta(TaskListSerializer.Meta):
        fields = TaskListSerializer.Meta.fields + [
            "description", "description_format", "department", "created_by",
            "reviewer", "subtasks", "subtask_percent",
            "blocked_reason", "clarification_note", "clarification_requested_at",
            "assigned_at", "accepted_at", "started_at", "submitted_at",
            "completed_at", "closed_at", "cancelled_at",
            "assignees", "checklist", "checklist_groups", "attachments",
            "evidence", "removed_attachments", "comments", "timeline",
            "checklist_percent", "template", "template_name", "capabilities",
            "dependencies", "dependents", "milestones",
        ]
        read_only_fields = fields

    # --- the four attachment/comment views of the same prefetched rows -----
    # All of these filter IN PYTHON over `obj.attachments.all()` /
    # `obj.comments.all()`, which the viewset has already prefetched. Calling
    # `.filter()` on the related manager here would bypass that cache and fire a
    # query per section, per task.
    def get_subtasks(self, obj):
        """The prefetched rows; counts and flags read the task's prefetches too."""
        context = dict(self.context, task=obj)
        return TaskSubtaskSerializer(obj.subtasks.all(), many=True,
                                     context=context).data

    def get_checklist(self, obj):
        """Top-level items only. Grouped items are rendered under their group."""
        rows = [item for item in obj.checklist.all() if item.group_id is None]
        return TaskChecklistItemSerializer(rows, many=True).data

    def get_attachments(self, obj):
        """General attachments: present, and not flagged as evidence."""
        rows = [a for a in obj.attachments.all()
                if not a.is_removed and not a.is_evidence]
        return TaskAttachmentSerializer(rows, many=True).data

    def get_evidence(self, obj):
        """
        Evidence — files AND links. A separate section, because Phase T2 asks for
        one: a reviewer opening the task wants what was PRODUCED, not the brief
        it was produced against.
        """
        rows = [a for a in obj.attachments.all()
                if not a.is_removed and a.is_evidence]
        return TaskAttachmentSerializer(rows, many=True).data

    def get_removed_attachments(self, obj):
        """The withdrawal history. Rendered behind a disclosure, never inline."""
        rows = [a for a in obj.attachments.all() if a.is_removed]
        return TaskAttachmentSerializer(rows, many=True).data

    def get_comments(self, obj):
        """Top-level comments; each carries its own replies."""
        rows = [c for c in obj.comments.all() if c.parent_id is None]
        return TaskCommentSerializer(rows, many=True, context=self.context).data

    def get_milestones(self, obj):
        """
        Created, Started, Reviewed, Completed - the four the specification names
        (Phase TASK-GOVERNANCE-HARDENING).

        The full audit trail is still there beside it; this is the SHAPE of the
        task's life, which forty timeline rows do not show at a glance. Each
        milestone carries its timestamp or None, and None renders as "not yet"
        rather than as a gap - a blank Started beside a filled Completed is
        itself worth seeing.

        Reviewed is the moment a reviewer DECIDED, which is `completed_at` on
        the approval path; submitted_at is carried beside it because the wait
        between the two is the review turnaround the reports measure.
        """
        return [
            {"key": "created", "label": "Created", "at": obj.created_at},
            {"key": "started", "label": "Started", "at": obj.started_at},
            {"key": "reviewed", "label": "Reviewed",
             "at": obj.completed_at if obj.submitted_at else None,
             "submitted_at": obj.submitted_at},
            {"key": "completed", "label": "Completed",
             "at": obj.closed_at or obj.completed_at},
        ]

    def get_capabilities(self, obj):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return {}
        return perms.capabilities(request.user, obj)


class TaskWriteSerializer(serializers.ModelSerializer):
    """
    Create / edit a task's DEFINITION.

    `assignee_ids` is accepted here as a convenience so the create form is one
    request, but it is applied through tasks.workflow.set_assignees rather than
    written directly — the same code path the dedicated endpoint uses, so there
    is one definition of what assigning means.

    `checklist` is accepted the same way on create.
    """
    assignee_ids = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(
            queryset=User.objects.filter(is_active=True)),
        required=False, allow_empty=True, write_only=True)
    checklist = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False,
        allow_empty=True, write_only=True)
    # Phase TASK-AUTOSAVE-AND-SUBTASKS. Subtasks on CREATE only, so the form's
    # draft is one request; afterwards they are managed on the task itself.
    subtasks = serializers.ListField(
        child=serializers.DictField(), required=False, allow_empty=True,
        write_only=True)
    # The task's `updated_at` as the form loaded it. On an edit, a mismatch is a
    # conflict with somebody else's save and the view answers 409 rather than
    # silently overwriting their change.
    expected_updated_at = serializers.DateTimeField(
        required=False, allow_null=True, write_only=True)
    # REQUIRED on create (Phase TASK-REVIEWER-SELECTION). The creator chooses
    # who approves the work; nothing routes it for them. Only `required` is
    # overridden - the queryset, and therefore who may be chosen, stays whatever
    # the model says: any active user.
    reviewer = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True), required=True,
        allow_null=False)

    class Meta:
        model = Task
        fields = ["title", "description", "description_format", "priority",
                  "department", "due_date", "reviewer", "goal", "assignee_ids",
                  "checklist", "subtasks", "expected_updated_at"]

    def _validate_people(self, attrs):
        """
        TWO RULES ABOUT PEOPLE:

          SOMEBODY MUST BE DOING IT, AND SOMEBODY MUST BE REVIEWING IT. Both
          fields are mandatory (Phase TASK-REVIEWER-SELECTION). A task with
          nobody on it is a note; a task with no reviewer is work that can be
          submitted to nobody, and the system used to paper over that by picking
          a reviewer itself. It no longer does: the creator chooses.

          THE REVIEWER CANNOT BE AN ASSIGNEE. A person signing off their own
          work is what the review step exists to prevent; `can_review` enforces
          it again at decision time, and this says it at creation, when it can
          still be fixed.

        Everything else that used to live here - which KIND of task each role
        could raise, whether their department had a head, whether they were
        allowed to name somebody else at all - went with the three task types.
        Anybody may raise a task, for anybody, reviewed by anybody.
        """
        attrs = super().validate(attrs)
        named = attrs.get("assignee_ids")
        reviewer = attrs.get("reviewer", getattr(self.instance, "reviewer", None))
        if named is None:
            named = ([row.user for row in self.instance.assignees.all()]
                     if self.instance is not None else [])
        elif not named:
            # An EXPLICIT empty list, on a create or an edit: refused either way,
            # because a task that ends up with nobody on it is the same problem
            # whenever it happens.
            raise serializers.ValidationError({"assignee_ids": (
                "Choose who is doing this task. A task with nobody on it is a "
                "note rather than a task - assign it to yourself if it is yours.")})
        if self.instance is None and not named:
            raise serializers.ValidationError({"assignee_ids": (
                "Choose who is doing this task. Assign it to yourself if it is "
                "yours.")})
        if reviewer is not None and any(u.id == reviewer.id for u in named):
            raise serializers.ValidationError({"reviewer":
                "Reviewer cannot be the same as the assignee."})
        return attrs

    def validate_title(self, value):
        text = (value or "").strip()
        if len(text) < 3:
            raise serializers.ValidationError(
                "Give the task a title of at least 3 characters.")
        return text

    def validate(self, attrs):  # noqa: F811 - wraps the people rules above
        attrs = self._validate_people(attrs)
        # The description is sanitized HERE, once the format is known: an
        # `html` description goes through the shared allowlist and a `text`
        # one is stored as typed and escaped on render, exactly as before.
        fmt = attrs.get("description_format",
                        getattr(self.instance, "description_format", None)
                        or Task.DescriptionFormat.TEXT)
        if "description" in attrs and fmt == Task.DescriptionFormat.HTML:
            attrs["description"] = sanitize_html(attrs["description"] or "")
        for row in attrs.get("subtasks") or []:
            title = str(row.get("title") or "").strip()
            if not title:
                raise serializers.ValidationError(
                    {"subtasks": "Every subtask needs a title."})
        return attrs

    def validate_due_date(self, value):
        """
        A due date in the past is refused on CREATE only. Editing a task that
        has already run past its date must stay possible — otherwise fixing a
        typo in the title of an overdue task would be blocked by its due date.
        """
        from django.utils import timezone

        if value and self.instance is None and value < timezone.localdate():
            raise serializers.ValidationError("The due date cannot be in the past.")
        return value


# ---------------------------------------------------------------------------
# Action payloads. Each names its own fields so an unknown one is not silently
# accepted and ignored.
# ---------------------------------------------------------------------------
class AssigneeListSerializer(serializers.Serializer):
    assignee_ids = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(
            queryset=User.objects.filter(is_active=True)),
        allow_empty=False)


class RemarksSerializer(serializers.Serializer):
    """Optional remarks — used by approve and close."""
    remarks = serializers.CharField(required=False, allow_blank=True,
                                    max_length=2000)


class RequiredReasonSerializer(serializers.Serializer):
    """
    Mandatory reason — used by rework, block, cancel and clarification.

    The floor is MIN_REMARK_LENGTH rather than a literal here, because "no" and
    "wrong" are not reasons anybody can act on, and this is the field the
    assignee reads to find out what to do next. One definition, in
    tasks.services, so the API and any future reminder text cannot disagree
    about what counts as an explanation.
    """
    reason = serializers.CharField(min_length=MIN_REMARK_LENGTH, max_length=2000)


class ProgressSerializer(serializers.Serializer):
    progress_percent = serializers.IntegerField(min_value=0, max_value=100)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class SubmitReviewSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ChecklistGroupWriteSerializer(serializers.Serializer):
    """One section of the checklist, with its lines."""
    title = serializers.CharField(max_length=150)
    items = serializers.ListField(child=serializers.CharField(max_length=255),
                                  allow_empty=True)


class ChecklistWriteSerializer(serializers.Serializer):
    """
    Replace the whole checklist. Ticks on surviving rows are preserved.

    Both shapes are accepted, and both may be sent together:

        {"items": ["Collect data", "Draft report"]}          -> top-level lines
        {"groups": [{"title": "Drafting", "items": [...]}]}   -> sections

    Accepting the flat form unchanged is what lets every checklist written in
    Phase T1 keep working, and lets a simple task stay simple — a four-line
    checklist does not need a section header above it.
    """
    items = serializers.ListField(child=serializers.CharField(max_length=255),
                                  required=False, allow_empty=True)
    groups = ChecklistGroupWriteSerializer(many=True, required=False)

    def validate(self, attrs):
        if "items" not in attrs and "groups" not in attrs:
            raise serializers.ValidationError(
                "Send `items`, `groups`, or both. To clear the checklist send an "
                "empty `items` list — omitting both is more likely a mistake "
                "than an instruction.")
        return attrs


class AttachmentLinkSerializer(serializers.Serializer):
    """Evidence that lives somewhere else: a dashboard, a published page, a doc."""
    link_url = serializers.URLField(max_length=500)
    caption = serializers.CharField(max_length=255, required=False, allow_blank=True)
    is_evidence = serializers.BooleanField(default=True)


class EvidenceFlagSerializer(serializers.Serializer):
    is_evidence = serializers.BooleanField()


class ChecklistTickSerializer(serializers.Serializer):
    is_done = serializers.BooleanField()


# ---------------------------------------------------------------------------
# Templates (Phase T2.9)
# ---------------------------------------------------------------------------
class TaskTemplateItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskTemplateItem
        fields = ["id", "group", "text", "position"]
        read_only_fields = fields


class TaskTemplateGroupSerializer(serializers.ModelSerializer):
    items = TaskTemplateItemSerializer(many=True, read_only=True)

    class Meta:
        model = TaskTemplateGroup
        fields = ["id", "title", "position", "items"]
        read_only_fields = fields


class TaskTemplateListSerializer(serializers.ModelSerializer):
    """Slim: what the picker and the template list need, without the lines."""
    priority_label = serializers.CharField(source="get_priority_display",
                                           read_only=True)
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TaskTemplate
        fields = ["id", "name", "description", "priority", "priority_label",
                  "default_due_in_days", "department_name", "created_by_name",
                  "usage_count", "item_count", "is_active", "created_at"]
        read_only_fields = fields


class TaskTemplateDetailSerializer(TaskTemplateListSerializer):
    groups = TaskTemplateGroupSerializer(many=True, read_only=True)
    items = serializers.SerializerMethodField()

    class Meta(TaskTemplateListSerializer.Meta):
        fields = TaskTemplateListSerializer.Meta.fields + [
            "title_template", "groups", "items",
        ]
        read_only_fields = fields

    def get_items(self, obj):
        """Top-level lines only; grouped ones render under their group."""
        rows = [item for item in obj.items.all() if item.group_id is None]
        return TaskTemplateItemSerializer(rows, many=True).data


class TaskTemplateWriteSerializer(serializers.ModelSerializer):
    """
    Create or edit a template directly (as opposed to saving one off a task).

    `groups` and `items` use the same shape the checklist endpoint takes, so
    there is one way to describe a checklist in this API rather than two.
    """
    items = serializers.ListField(child=serializers.CharField(max_length=255),
                                  required=False, allow_empty=True)
    groups = ChecklistGroupWriteSerializer(many=True, required=False)

    class Meta:
        model = TaskTemplate
        fields = ["name", "description", "title_template", "priority",
                  "default_due_in_days", "department", "is_active", "items",
                  "groups"]

    def validate_name(self, value):
        name = (value or "").strip()
        if len(name) < 3:
            raise serializers.ValidationError(
                "Give the template a name of at least 3 characters.")
        clash = TaskTemplate.objects.filter(name__iexact=name)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                f'A template called "{name}" already exists.')
        return name


class SaveAsTemplateSerializer(serializers.Serializer):
    """Freeze a task's shape. See tasks.workflow.save_as_template on what is copied."""
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(required=False, allow_blank=True,
                                        max_length=4000)
    default_due_in_days = serializers.IntegerField(required=False, allow_null=True,
                                                   min_value=0, max_value=3650)


class ApplyTemplateSerializer(serializers.Serializer):
    template = serializers.PrimaryKeyRelatedField(
        queryset=TaskTemplate.objects.filter(is_active=True))


# ---------------------------------------------------------------------------
# Bulk operations (Phase T4.8)
# ---------------------------------------------------------------------------
class BulkActionSerializer(serializers.Serializer):
    """
    One endpoint, one action, many tasks.

    `task_ids` is required and capped. Uncapped bulk endpoints are how a
    mis-typed filter becomes a thousand-row write under one request timeout, and
    a cap that refuses is better than a partial apply nobody can see.
    """
    ACTIONS = ["assign", "close", "priority", "due_date", "archive", "unarchive"]

    action = serializers.ChoiceField(choices=ACTIONS)
    task_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=200)

    # Per-action payload. Validated in `validate` rather than by five separate
    # serializers, so the API has one bulk shape rather than five near-identical
    # ones.
    assignee_ids = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(
            queryset=User.objects.filter(is_active=True)),
        required=False, allow_empty=False)
    priority = serializers.ChoiceField(choices=Task.Priority.choices,
                                       required=False)
    due_date = serializers.DateField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True,
                                    max_length=2000)

    REQUIRES = {
        "assign": "assignee_ids",
        "priority": "priority",
        "due_date": "due_date",
    }

    def validate(self, attrs):
        needed = self.REQUIRES.get(attrs["action"])
        # `due_date` may legitimately be null — that is how a due date is
        # cleared — so presence is checked, not truthiness.
        if needed and needed not in attrs:
            raise serializers.ValidationError(
                {needed: f"`{needed}` is required for the "
                         f"'{attrs['action']}' action."})
        return attrs


class TaskGroupSerializer(serializers.ModelSerializer):
    task_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TaskGroup
        fields = ["id", "name", "description", "template", "department_name",
                  "created_by_name", "created_at", "task_count"]
        read_only_fields = fields


class CreateTaskGroupSerializer(serializers.Serializer):
    """
    Raise a whole batch from a template: one task per checklist SECTION.

    The sections are the natural seams of a multi-step piece of work — "Website
    Launch" has a Content section and an Infrastructure section, and those are
    different people's jobs. Turning each into its own task is what makes a
    template more than a checklist; keeping them as one task with eight sections
    would leave a single assignee holding all of it.
    """
    template = serializers.PrimaryKeyRelatedField(
        queryset=TaskTemplate.objects.filter(is_active=True))
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    # Optional: one assignee list applied to every task in the batch. Per-task
    # assignment is done afterwards on each task, because a batch raised for
    # eight different people is eight decisions, not one field.
    assignee_ids = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(
            queryset=User.objects.filter(is_active=True)),
        required=False, allow_empty=True)
