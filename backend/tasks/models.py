"""
The Task Management data model (Phase T1).

A SELF-CONTAINED MODULE, BY INSTRUCTION
---------------------------------------
Task Management does not read from, write to, or import Memo, Minute, Circular,
Leave, Attendance, Inventory, Reports or Analytics. It touches exactly three
things outside itself, all of them shared infrastructure rather than a peer
module:

  * settings.AUTH_USER_MODEL - there is one employee directory, and a task has
    to be assigned to somebody in it.
  * "leaves.Department" - a lazy STRING reference, so nothing in the leave module
    is imported. There is one department table in this system; a second one would
    have to be kept in step by hand and would disagree within a month.
  * config.uploads / audit / notifications - project-wide plumbing already shared
    by every module.

Nothing here is imported by any other module either, so the whole app can be
removed by deleting the directory and three registration lines.

WHY THE STATUSES ARE TextChoices AND THE PRIORITIES ARE TOO
-----------------------------------------------------------
Both are things this module's own code branches on. A status inserted as a
database row cannot be given behaviour by the workflow engine, so it would be a
state the system cannot reason about wearing the costume of configuration.

WHY ASSIGNEES ARE A TABLE AND NOT A ForeignKey
----------------------------------------------
The specification asks for employee selection with optional multi-assignee
("Bikash Kadayat / Sanjaya Poudel / Prashanta Acharya"), so the single-assignee
FK would have had to be migrated away within one phase. TaskAssignee also has to
carry per-person state - who accepted, and when - which a plain M2M cannot.

PEOPLE ARE ALWAYS SET_NULL PLUS A SNAPSHOT
------------------------------------------
Every reference to a user on anything historical is `on_delete=SET_NULL` beside a
text snapshot of the name. PROTECT would make an employee account undeletable the
moment they were assigned anything; CASCADE would silently delete the record of
work they did. The snapshot means a closed task from three years ago still names
who did it after the account is gone.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from tenancy.scoping import AllTenantsManager, TenantManager


def task_attachment_path(instance, filename):
    """
    Unguessable per-file directory. Even if MEDIA were ever served directly the
    path cannot be guessed; downloads still go through a signed, expiring URL.
    """
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="tasks", kind="attachments")


class TaskNumberSequence(models.Model):
    """
    Authoritative per-BS-year counter behind NIFN-TSK-2083-0001.

    A counter row locked with select_for_update(), not a "max existing number"
    read: concurrent creations serialise on the lock instead of racing, and an
    integer counter cannot develop the lexical-sort bug a zero-padded string does
    once it passes its padding width.

    The year is the BIKRAM SAMBAT year, because that is what the specified format
    shows (2083, not 2026). See tasks.services.current_bs_year.
    """
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()

    class Meta:
        verbose_name = "task number sequence"
        verbose_name_plural = "task number sequences"
        constraints = [
            # Was unique=True on `year`: one counter row for every tenant, so
            # Tenant A creating a task advanced Tenant B's task numbers. This
            # is the exact defect Phase S2 Part 2 names.
            models.UniqueConstraint(fields=["organization", "year"],
                                    name="uniq_task_sequence_org_year"),
        ]

    def __str__(self):
        return f"{self.year}: {self.last_value}"


class Task(models.Model):
    """
    One unit of assigned work, and where it is in its lifecycle.

        Draft -> Assigned -> Accepted -> In Progress -> Under Review
              -> Completed -> Closed

    with Blocked and Cancelled sitting outside the ladder as exceptions.

    `progress_percent` is stored rather than derived from the checklist, because
    a task may have no checklist at all and still be half done. Where a checklist
    DOES exist the API reports both numbers (`checklist_percent` beside
    `progress_percent`) rather than silently preferring one.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ASSIGNED = "assigned", "Assigned"
        ACCEPTED = "accepted", "Accepted"
        IN_PROGRESS = "in_progress", "In Progress"
        UNDER_REVIEW = "under_review", "Under Review"
        COMPLETED = "completed", "Completed"
        CLOSED = "closed", "Closed"
        BLOCKED = "blocked", "Blocked"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Kind(models.TextChoices):
        """
        LEGACY (Phase TASK-SIMPLIFICATION). There is one kind of task.

        Personal / Department / Assigned each carried rules about who could
        raise it and who signed it off. The phase removed all of them: anybody
        raises a task, for anybody, and the creator picks the reviewer. NOTHING
        branches on this any more.

        The choices and the column remain so that historical rows keep the value
        they were saved with and existing API consumers keep the field they
        read - the phase says to preserve both. New tasks take the default.
        """
        PERSONAL = "personal", "Personal Task"
        DEPARTMENT = "department", "Department Task"
        ASSIGNED = "assigned", "Assigned Task"

    # A closed or cancelled task is history: no edits, no transitions, no new
    # checklist rows. Enforced by the permission layer AND by the workflow
    # engine, never by hiding a button.
    TERMINAL_STATUSES = frozenset({Status.CLOSED, Status.CANCELLED})
    # Statuses whose content (title, description, due date, reviewer, ...) the
    # owner may still change. Once work has been submitted for review, changing
    # what was asked for would rewrite the thing being reviewed.
    EDITABLE_STATUSES = frozenset({
        Status.DRAFT, Status.ASSIGNED, Status.ACCEPTED, Status.IN_PROGRESS,
        Status.BLOCKED,
    })
    # Statuses that count as "open work" on every dashboard. Deliberately one
    # definition, because a tile that disagrees with the list it links to is
    # worse than no tile.
    OPEN_STATUSES = frozenset({
        Status.DRAFT, Status.ASSIGNED, Status.ACCEPTED, Status.IN_PROGRESS,
        Status.UNDER_REVIEW, Status.BLOCKED,
    })
    # Open work that has actually been given to somebody. `OPEN_STATUSES` minus
    # DRAFT: a draft is nobody's workload yet.
    ACTIVE_STATUSES = frozenset(OPEN_STATUSES - {Status.DRAFT})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task_number = models.CharField(max_length=32, db_index=True)

    title = models.CharField(max_length=255)
    # Plain text OR sanitized HTML, and `description_format` says which
    # (Phase TASK-AUTOSAVE-AND-SUBTASKS). Rows from before the phase are
    # `text` and the client renders them with their line breaks, as always.
    # Rows written by the rich editor are `html`, passed through
    # common.html_sanitizer on the way in - the same allowlist memos and
    # circulars use - and sanitized again by the browser on the way out.
    description = models.TextField(blank=True, default="")

    class DescriptionFormat(models.TextChoices):
        TEXT = "text", "Plain text"
        HTML = "html", "Rich text"

    description_format = models.CharField(
        max_length=4, choices=DescriptionFormat.choices,
        default=DescriptionFormat.TEXT)

    # LEGACY, kept for the data and the API. See Kind above: nothing reads this
    # to make a decision.
    task_type = models.CharField(
        max_length=12, choices=Kind.choices, default=Kind.ASSIGNED, db_index=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM,
        db_index=True)

    # Department the work belongs to. The FK is a lazy string reference so the
    # leave module is never imported; the snapshot keeps the label readable if
    # the department row is later removed.
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks")
    department_name = models.CharField(max_length=150, blank=True, default="")

    due_date = models.DateField(null=True, blank=True, db_index=True)

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks_to_review",
        help_text="Who decides Approved / Needs Rework once work is submitted. "
                  "Defaults to the creator when left empty.")
    reviewer_name = models.CharField(max_length=150, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="tasks_created", db_index=True)
    created_by_name = models.CharField(max_length=150, blank=True, default="")

    progress_percent = models.PositiveSmallIntegerField(default=0)
    # Phase T2.1/T2.2. Whether `progress_percent` is DERIVED from the checklist
    # or was typed by the assignee.
    #
    # A single derived number would be wrong for a task with no checklist (there
    # is nothing to derive it from) and a single manual number would be wrong
    # for one with a checklist (it would drift from the ticks the moment either
    # changed). So the flag records which of the two this task's number is, and
    # it flips to False the first time a human reports a figure themselves —
    # after which the checklist stops overwriting their judgement.
    progress_is_auto = models.BooleanField(default=True)

    # Phase T2.9. Which template this task was created from, if any. SET_NULL
    # rather than PROTECT: deleting a retired template must not be blocked by,
    # or delete, two years of tasks raised from it.
    template = models.ForeignKey(
        "tasks.TaskTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks_created")

    # Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP. The department goal this
    # work counts towards, if any. SET_NULL: retiring a goal must not delete the
    # work raised under it, and the task stands on its own afterwards.
    goal = models.ForeignKey(
        "tasks.DepartmentGoal", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks")

    # Phase T4.7. The batch this task was raised in, when a template produced
    # several at once. SET_NULL: dissolving a group must not delete the work.
    group = models.ForeignKey(
        "tasks.TaskGroup", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks")

    # Phase T4.8. Archiving is FILING, not deleting and not cancelling.
    #
    # A cancelled task was stopped; an archived one ran its course and is simply
    # out of the way. Keeping them distinct matters because every completion
    # metric counts closed work, and folding archiving into cancellation would
    # quietly delete finished work from the numbers. Archived tasks leave the
    # default lists and stay in search, reports and their own page.
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Why the task is stuck. Cleared on unblock rather than kept, so "blocked
    # because X" can never be shown against a task that is running again.
    blocked_reason = models.TextField(blank=True, default="")
    # The last unanswered "I need more information before I can accept this".
    # Cleared when the task is accepted.
    clarification_note = models.TextField(blank=True, default="")
    clarification_requested_at = models.DateTimeField(null=True, blank=True)

    # Lifecycle stamps. Each is set by exactly one transition in tasks.workflow
    # and never written anywhere else, so "when was this accepted" has one answer.
    assigned_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The three shapes every dashboard tile and list query takes:
            # "my department's open work", "what is due", "what is where".
            models.Index(fields=["status", "due_date"], name="task_status_due_idx"),
            models.Index(fields=["created_by", "status"], name="task_creator_idx"),
            models.Index(fields=["department", "status"], name="task_dept_idx"),
        ]
        constraints = [
            # Phase S4: was unique=True on this field alone -- a GLOBAL
            # namespace. Phase S3 already gives each tenant its own document
            # prefix, so a collision was impossible in practice; the composite
            # makes it impossible by construction, and removes the last way one
            # tenant's numbering could refuse another tenant's insert.
            models.UniqueConstraint(fields=["organization", "task_number"],
                                    name="uniq_task_number_org"),
        ]

    def __str__(self):
        return f"{self.task_number} — {self.title}"

    # --- derived, never stored ------------------------------------------
    @property
    def is_archived(self):
        return self.archived_at is not None

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self):
        """
        Past its due date and still open.

        A completed-but-not-yet-closed task is NOT overdue: the employee did
        their part, and chasing them for their reviewer's backlog is how a
        metric stops being believed.
        """
        if not self.due_date:
            return False
        if self.status not in self.OPEN_STATUSES:
            return False
        return self.due_date < timezone.localdate()

    @property
    def checklist_percent(self):
        """Completion of the checklist, or None when there is no checklist."""
        items = list(self.checklist.all())
        if not items:
            return None
        return round(100 * sum(1 for i in items if i.is_done) / len(items))

    # --- subtasks (Phase TASK-AUTOSAVE-AND-SUBTASKS) -----------------------
    #
    # All three read the PREFETCHED rows, like the acceptance properties below.
    @property
    def subtask_total(self):
        return len(self.subtasks.all())

    @property
    def subtask_done(self):
        return sum(1 for row in self.subtasks.all() if row.is_done)

    @property
    def subtask_percent(self):
        """Completion of the subtasks, or None when there are none."""
        total = self.subtask_total
        if not total:
            return None
        return round(100 * self.subtask_done / total)

    @property
    def open_subtask_count(self):
        return self.subtask_total - self.subtask_done

    # --- acceptance (Phase TASK-MULTI-ASSIGNEE-COLLABORATION) -------------
    #
    # Every one of these reads `self.assignees.all()`, which every list and
    # detail view has already prefetched. A `.filter()` here would be one extra
    # query per card on a board of fifty.
    @property
    def accepted_count(self):
        return sum(1 for row in self.assignees.all() if row.accepted_at)

    @property
    def assignee_count(self):
        return len(self.assignees.all())

    @property
    def pending_assignee_names(self):
        """Who has not accepted yet, in the order they are listed."""
        return [row.user_name or "Unknown"
                for row in self.assignees.all() if not row.accepted_at]

    @property
    def all_assignees_accepted(self):
        """
        True when everybody on the task has accepted it.

        A task with NOBODY on it answers False: there is no one to have agreed,
        and treating "nobody assigned" as "everybody accepted" would let the
        gate below pass on an empty task.
        """
        rows = self.assignees.all()
        return bool(rows) and all(row.accepted_at for row in rows)

    @property
    def needs_unanimous_acceptance(self):
        """
        Whether the all-must-accept gate applies to this task.

        SHARED WORK ONLY (Phase TASK-MULTI-ASSIGNEE-COLLABORATION). The phase
        asks that a task given to three people wait for all three, because one
        person accepting is not the team agreeing. A task given to ONE person is
        unchanged: Phase TASK-SIMPLIFICATION removed the acknowledgement step
        from the ladder, and re-imposing it on every solo task would make the
        common case slower to satisfy a rule about the uncommon one.
        """
        return self.assignee_count > 1

    @property
    def is_pending_acceptance(self):
        """Assigned, shared, and still waiting for somebody to say yes."""
        return (self.status == self.Status.ASSIGNED
                and self.needs_unanimous_acceptance
                and not self.all_assignees_accepted)

    @property
    def work_may_start(self):
        """
        Whether the acceptance gate is open. Everything that IS the work —
        progress, evidence, comments from the people doing it, submission —
        asks this one question rather than each re-deriving it.
        """
        return not self.needs_unanimous_acceptance or self.all_assignees_accepted

    @property
    def workflow_label(self):
        """
        The status as a person reading the task should see it.

        Two stored statuses get a clearer name and nothing else changes:
        ASSIGNED with acceptance outstanding reads "Pending Acceptance", and
        ACCEPTED reads "Ready to Start". `status_label` is untouched beside it,
        so every report, filter and export keeps the vocabulary it was built on.
        """
        if self.status == self.Status.ASSIGNED and not self.all_assignees_accepted:
            return "Pending Acceptance"
        if self.status == self.Status.ACCEPTED:
            return "Ready to Start"
        return self.get_status_display()

    @property
    def assignee_progress(self):
        """
        Per-person progress: [(name, percent-or-None)], in assignee order.

        None means "has not reported", which is not the same as 0% and is shown
        differently — a person who has not yet said anything has not claimed to
        have done nothing.
        """
        return [(row.user_name or "Unknown", row.progress_percent)
                for row in self.assignees.all()]


class TaskAssignee(models.Model):
    """
    One employee this task is assigned to.

    Multi-assignee is optional (a task usually has one), but the row carries
    per-person acceptance state, so it has to be a table either way: "who has
    accepted" is not answerable from a single FK on Task.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="assignees")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_assignments", db_index=True)
    user_name = models.CharField(max_length=150, blank=True, default="")
    designation = models.CharField(max_length=120, blank=True, default="")
    department_label = models.CharField(max_length=150, blank=True, default="")

    # The person answerable for the task when several are assigned. Exactly one
    # per task, set to the first-listed assignee.
    is_primary = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(null=True, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    # Phase TASK-MULTI-ASSIGNEE-COLLABORATION. What THIS person reports, beside
    # the task's overall figure.
    #
    # NULL rather than 0 for "has not reported". The two are different answers —
    # 0% is a claim, silence is not — and averaging silence as a zero would drag
    # a shared task's overall figure down for every person who simply has not
    # got to it yet. `tasks.services.recalculate_overall_progress` averages the
    # rows that HAVE a figure and leaves the task's number alone when none do,
    # which is also what keeps tasks raised before this phase reading correctly.
    progress_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    progress_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_primary", "added_at"]
        constraints = [
            # A user assigned twice would be notified twice and could accept
            # twice, which would corrupt the "everyone has accepted" tally.
            models.UniqueConstraint(
                fields=["task", "user"], name="task_assignee_unique",
                condition=models.Q(user__isnull=False)),
        ]

    def __str__(self):
        return f"{self.user_name or 'Unknown'} on {self.task_id}"

    @property
    def has_accepted(self):
        return self.accepted_at is not None


class DepartmentGoal(models.Model):
    """
    What a department is trying to achieve this month, quarter or year
    (Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP).

    WHY IT LIVES IN THE TASK MODULE
    -------------------------------
    A goal's progress is the work beneath it, so whatever owns goals has to read
    tasks - and nothing outside this app may import it (tasks/tests/
    test_independence.py). A separate `planning` app would have had to break
    that boundary on its first line. The department is still reached the way
    every other cross-app reference here is: a lazy "leaves.Department" string.

    PROGRESS IS NEVER TYPED
    -----------------------
    It is the share of linked tasks that are finished, computed on read. A
    percentage somebody types is an opinion wearing a number's clothes, and it
    drifts from the work underneath it the moment either changes. A goal with no
    linked tasks reports "0 of 0" rather than "0%", because an empty goal and a
    failing one are different things and one figure cannot say both.

    THE PERIOD IS STORED AS DATES, NOT DERIVED
    ------------------------------------------
    `period` says which shape it is; `starts_on`/`ends_on` say exactly which
    month, quarter or year. Re-deriving the window from the period and a
    created-at stamp would silently move last quarter's goal into this one.
    """

    class Period(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        ANNUAL = "annual", "Annual"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ACHIEVED = "achieved", "Achieved"
        MISSED = "missed", "Missed"
        CANCELLED = "cancelled", "Cancelled"

    OPEN_STATUSES = frozenset({Status.ACTIVE})
    CLOSED_STATUSES = frozenset({Status.ACHIEVED, Status.MISSED, Status.CANCELLED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.CASCADE, related_name="task_goals")
    # The label, frozen, so a closed goal still names its department after a
    # reorganisation - the same reason every person reference here has one.
    department_name = models.CharField(max_length=150, blank=True, default="")

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    period = models.CharField(max_length=10, choices=Period.choices,
                              default=Period.QUARTERLY, db_index=True)
    starts_on = models.DateField(db_index=True)
    ends_on = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.ACTIVE, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="department_goals_created")
    created_by_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_on", "title"]
        verbose_name = "department goal"
        verbose_name_plural = "department goals"
        indexes = [
            models.Index(fields=["department", "status"], name="goal_dept_status_idx"),
            models.Index(fields=["starts_on", "ends_on"], name="goal_window_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_on__gte=models.F("starts_on")),
                                   name="goal_window_not_backwards"),
        ]

    def __str__(self):
        return f"{self.department_name or self.department_id}: {self.title}"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    def progress(self):
        """
        Counts, and the percentage derived from them. Returns the counts BESIDE
        the percentage so a reader can see 1-of-2 rather than a bare 50%, which
        on two tasks means far less than it looks.
        """
        rows = list(self.tasks.values_list("status", flat=True))
        done = sum(1 for status in rows
                   if status in (Task.Status.COMPLETED, Task.Status.CLOSED))
        live = [s for s in rows if s != Task.Status.CANCELLED]
        total = len(live)
        return {
            "linked_tasks": total,
            "completed_tasks": done,
            # None, not 0, when nothing is linked: "no work attached yet" and
            # "none of the work is done" are different answers.
            "percent": round(100 * done / total) if total else None,
        }


class TaskDependency(models.Model):
    """
    "This task cannot move until that one is finished"
    (Phase TASK-GOVERNANCE-HARDENING).

    ONE EDGE, TWO WORDINGS
    ----------------------
    The specification names two relationships, Blocked By and Depends On, and
    both mean the same thing to the engine: the prerequisite must be finished
    before this task may move forward. They are kept as separate LABELS rather
    than collapsed into one because they say different things to a reader -
    "blocked by" is usually somebody else's work standing in the way, "depends
    on" is usually an input this task needs - and a person deciding what to
    chase needs that difference. What they are NOT is two strengths of
    constraint: `blocks_progress` below is the rule, and it is the same rule for
    both, so nobody can be surprised by a "soft" dependency that turns out to be
    hard, or the reverse.

    WHAT "FINISHED" MEANS
    --------------------
    COMPLETED or CLOSED. A CANCELLED prerequisite does NOT block: it is never
    going to finish, and a dependency that outlives the work it pointed at would
    freeze a task permanently with no way out but an Admin. The API says the
    prerequisite was cancelled rather than quietly dropping it, so the reader can
    see why the block lifted.

    NO CYCLES
    ---------
    Refused when added (tasks.services.would_cycle), not detected later. A cycle
    is not a state this system can be in and recover from by itself: every task
    in it is waiting for another, forever, and the only fix is somebody deleting
    an edge they cannot see.
    """

    class Kind(models.TextChoices):
        BLOCKED_BY = "blocked_by", "Blocked by"
        DEPENDS_ON = "depends_on", "Depends on"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    # The task that must WAIT.
    task = models.ForeignKey(Task, on_delete=models.CASCADE,
                             related_name="dependencies")
    # The task that must FINISH first. CASCADE as well: an edge to a deleted
    # task is not information, and only an unassigned draft can ever be deleted.
    depends_on = models.ForeignKey(Task, on_delete=models.CASCADE,
                                   related_name="dependents")
    kind = models.CharField(max_length=12, choices=Kind.choices,
                            default=Kind.BLOCKED_BY)
    note = models.CharField(max_length=255, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_dependencies_added")
    created_by_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "task dependency"
        verbose_name_plural = "task dependencies"
        constraints = [
            models.UniqueConstraint(fields=["task", "depends_on"],
                                    name="task_dependency_unique"),
            # A task waiting for itself is the one-node cycle, and it is refused
            # by the database rather than only by the code that writes it.
            models.CheckConstraint(condition=~models.Q(task=models.F("depends_on")),
                                   name="task_dependency_not_self"),
        ]
        indexes = [models.Index(fields=["depends_on"], name="task_dep_target_idx")]

    def __str__(self):
        return f"{self.task_id} {self.kind} {self.depends_on_id}"

    @property
    def is_satisfied(self):
        """Finished, or cancelled and therefore never going to finish."""
        return self.depends_on.status in (Task.Status.COMPLETED, Task.Status.CLOSED,
                                          Task.Status.CANCELLED)

    @property
    def blocks_progress(self):
        return not self.is_satisfied


class TaskChecklistGroup(models.Model):
    """
    A named section of a task's checklist (Phase T2.1).

    Groups are OPTIONAL. An item with no group is a top-level item, which is
    what every checklist created in Phase T1 is and stays — making groups
    mandatory would have meant migrating every existing checklist into a
    synthetic "General" bucket that nobody asked for and that would show up in
    the UI forever.

    Groups carry no completion state of their own: a group is done when its
    items are, and storing that separately would create a second number that can
    disagree with the ticks.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="checklist_groups")
    title = models.CharField(max_length=150)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return self.title

    @property
    def done_count(self):
        return sum(1 for item in self.items.all() if item.is_done)

    @property
    def total_count(self):
        return len(self.items.all())


class TaskChecklistItem(models.Model):
    """One tick-box on a task. Ordered by an explicit position, not by pk."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist")
    # Null = a top-level item. CASCADE, because an item cannot outlive the
    # section it belongs to without silently changing which list it is in.
    group = models.ForeignKey(
        TaskChecklistGroup, on_delete=models.CASCADE, null=True, blank=True,
        related_name="items")
    text = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)
    is_done = models.BooleanField(default=False)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="task_checklist_items_done")
    done_by_name = models.CharField(max_length=150, blank=True, default="")
    done_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return f"[{'x' if self.is_done else ' '}] {self.text}"


class TaskSubtask(models.Model):
    """
    One piece of a task (Phase TASK-AUTOSAVE-AND-SUBTASKS).

    NOT A TASK. A subtask has no reviewer, no acceptance, no status ladder and
    no task number: it is open or done. Making it a `Task` row with a parent
    would have put every subtask into every list, board, report, reminder and
    KPI that counts tasks, and would have required a reviewer for "Configure
    SMTP". NOT A CHECKLIST ITEM either: the checklist is replaced wholesale on
    every write and is matched by text, which would wipe an assignment the
    moment anybody edited the list.

    `assignee` must be one of the parent's assignees - validated in the
    serializer - so nobody gains visibility of a task through a subtask and
    shared-task acceptance is unchanged. Comments and evidence live in the
    task's own tables with a nullable pointer here, so task-level counts,
    mentions and notifications keep working.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="subtasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    position = models.PositiveIntegerField(default=0)

    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="subtasks")
    assignee_name = models.CharField(max_length=150, blank=True, default="")
    due_date = models.DateField(null=True, blank=True, db_index=True)

    is_done = models.BooleanField(default=False)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="+")
    done_by_name = models.CharField(max_length=150, blank=True, default="")
    done_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="+")
    created_by_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(fields=["task", "is_done"], name="subtask_task_done_idx"),
            # The overdue reminder's only query.
            models.Index(fields=["is_done", "due_date"], name="subtask_due_idx"),
        ]

    def __str__(self):
        return f"{self.task_id}: {self.title}"

    @property
    def is_overdue(self):
        if self.is_done or not self.due_date:
            return False
        return self.due_date < timezone.localdate()


class TaskAttachment(models.Model):
    """
    Something attached to a task: the brief on the way in, the evidence on the
    way out — a FILE or a LINK.

    `is_evidence` distinguishes the two purposes, because "Upload Evidence" is a
    named step of the workflow and a reviewer needs to see what was produced
    without hunting through the specification documents attached at creation.

    LINKS ARE THE SAME ROW, NOT A SECOND TABLE (Phase T2.5)
    -------------------------------------------------------
    Evidence is often a link — a dashboard, a published page, a document in
    somebody else's system. A separate table would have meant two of everything
    downstream: two lists to merge, two evidence counts, two flag endpoints, two
    timeline vocabularies. So a row carries EITHER `file` OR `link_url`, and the
    `kind` property says which. The one thing a link cannot have is a download,
    which is exactly right.

    REMOVAL IS SOFT (Phase T2.4)
    ----------------------------
    "Attachment history" means the record survives the removal. A hard delete
    would erase the fact that a file was ever attached, which is the fact a
    reviewer most needs when a task's evidence changes between submissions.
    Removed rows keep their audit trail and drop out of every default list.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    # Evidence produced for ONE subtask (Phase TASK-AUTOSAVE-AND-SUBTASKS).
    # Still a task attachment - the review and the evidence snapshot see it -
    # this only says which piece of the work it belongs to.
    subtask = models.ForeignKey(
        TaskSubtask, on_delete=models.CASCADE, null=True, blank=True,
        related_name="attachments")
    # Blank for a link row. See the class docstring.
    file = models.FileField(upload_to=task_attachment_path, max_length=255,
                            blank=True, null=True)
    link_url = models.URLField(max_length=500, blank=True, default="")
    original_name = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True, default="")
    caption = models.CharField(max_length=255, blank=True, default="")
    is_evidence = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_attachments_uploaded")
    uploaded_by_name = models.CharField(max_length=150, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Soft removal. The row and its download history stay; it leaves the lists.
    is_removed = models.BooleanField(default=False, db_index=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="task_attachments_removed")
    removed_by_name = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.original_name or self.link_url or str(self.file)

    @property
    def kind(self):
        return "link" if self.link_url else "file"

    @property
    def download_count(self):
        return len(self.downloads.all())


class TaskAttachmentDownload(models.Model):
    """
    One record of somebody opening an attachment (Phase T2.4).

    Kept BESIDE the task's activity timeline rather than in it. A download is
    not an event in the life of the task — nothing about the work changed — and
    writing one timeline row per download would bury the eleven events that do
    matter under a hundred that do not. It is a separate log, read on demand by
    whoever owns the task.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    attachment = models.ForeignKey(
        TaskAttachment, on_delete=models.CASCADE, related_name="downloads")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_attachment_downloads")
    user_name = models.CharField(max_length=150, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    downloaded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-downloaded_at"]
        verbose_name = "task attachment download"

    def __str__(self):
        return f"{self.user_name} -> {self.attachment_id}"


class TaskComment(models.Model):
    """
    A comment on a task, optionally a reply to another comment (Phase T2.3).

    EDITING IS ALLOWED, AND VISIBLE
    -------------------------------
    Phase T1 refused edits outright, on the grounds that an editable comment is
    not evidence. Phase T2 asks for edited markers, which is the better answer
    to the same concern: the author may fix their own comment, `edited_at` is
    set, every reader sees "edited", and the edit writes its own timeline row.
    Silent editing would destroy the record; forbidding it entirely just moves
    the correction into a second comment nobody reads. Deletion is still not
    offered, and only the AUTHOR may edit — not the owner, not an admin.

    REPLIES ARE ONE LEVEL DEEP
    --------------------------
    `parent` may only point at a top-level comment; the API refuses a reply to a
    reply. Arbitrary nesting reads as a tree nobody can follow in a work log,
    and it makes "show the last three comments" — which is what a card and a
    notification need — ambiguous. One level gives threading where it helps and
    a flat chronology everywhere else.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    # Threaded under ONE subtask (Phase TASK-AUTOSAVE-AND-SUBTASKS). Still a
    # task comment: counts, mentions and notifications are unchanged.
    subtask = models.ForeignKey(
        TaskSubtask, on_delete=models.CASCADE, null=True, blank=True,
        related_name="comments")
    # CASCADE: a reply to a deleted parent has no meaning. Nothing deletes
    # comments through the API today, so this only fires when the task goes.
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="replies")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_comments")
    author_name = models.CharField(max_length=150, blank=True, default="")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author_name}: {self.body[:40]}"

    @property
    def is_edited(self):
        return self.edited_at is not None

    @property
    def is_reply(self):
        return self.parent_id is not None


class TaskCommentMention(models.Model):
    """
    Somebody named in a comment (Phase T2.3).

    A row rather than parsing the body on read: the body is plain text, so
    "@Bikash" in it is a string and two people called Bikash are
    indistinguishable. The client sends the ids it resolved from the picker, the
    server checks each one may actually READ the task, and this row is what the
    notification and the highlight are driven from. A mention that could not be
    resolved is simply not stored, so the body may still read "@Someone" without
    that ever having notified anybody.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    comment = models.ForeignKey(
        TaskComment, on_delete=models.CASCADE, related_name="mentions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_mentions", db_index=True)
    user_name = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["user_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["comment", "user"], name="task_comment_mention_unique",
                condition=models.Q(user__isnull=False)),
        ]

    def __str__(self):
        return f"@{self.user_name}"


class TaskAuditLog(models.Model):
    """
    The per-task activity timeline.

    Written inside the same transaction as the change it records, so a rolled
    back transition cannot leave a trail claiming it happened. Rows are never
    updated or deleted.

    This is deliberately SEPARATE from the project-wide `audit.AuditLog`, which
    is also written: the global log answers "what did this user do across the
    system", this one answers "what happened to this task" and is what the
    detail page's Activity Timeline renders.
    """
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        ASSIGNED = "assigned", "Assigned"
        ASSIGNEES_CHANGED = "assignees_changed", "Assignees changed"
        ACCEPTED = "accepted", "Accepted"
        CLARIFICATION_REQUESTED = "clarification_requested", "Clarification requested"
        STARTED = "started", "Work started"
        PROGRESS_UPDATED = "progress_updated", "Progress updated"
        CHECKLIST_UPDATED = "checklist_updated", "Checklist updated"
        EVIDENCE_UPLOADED = "evidence_uploaded", "Evidence uploaded"
        COMMENTED = "commented", "Comment added"
        # Phase T2. Every one of these is a change somebody may later have to
        # account for — an edited comment, a withdrawn file, a task built from a
        # template. A download is deliberately NOT here; see
        # TaskAttachmentDownload.
        COMMENT_EDITED = "comment_edited", "Comment edited"
        ATTACHMENT_ADDED = "attachment_added", "Attachment added"
        ATTACHMENT_REMOVED = "attachment_removed", "Attachment removed"
        LINK_ADDED = "link_added", "Link added"
        EVIDENCE_FLAGGED = "evidence_flagged", "Evidence flag changed"
        TEMPLATE_SAVED = "template_saved", "Saved as template"
        TEMPLATE_APPLIED = "template_applied", "Created from template"
        DEPENDENCY_ADDED = "dependency_added", "Dependency added"
        DEPENDENCY_REMOVED = "dependency_removed", "Dependency removed"
        # Phase TASK-AUTOSAVE-AND-SUBTASKS.
        SUBTASK_ADDED = "subtask_added", "Subtask added"
        SUBTASK_ASSIGNED = "subtask_assigned", "Subtask assigned"
        SUBTASK_COMPLETED = "subtask_completed", "Subtask completed"
        SUBTASK_REMOVED = "subtask_removed", "Subtask removed"
        SUBMITTED_FOR_REVIEW = "submitted_for_review", "Submitted for review"
        REVIEW_APPROVED = "review_approved", "Review approved"
        REWORK_REQUESTED = "rework_requested", "Rework requested"
        CLOSED = "closed", "Closed"
        BLOCKED = "blocked", "Blocked"
        UNBLOCKED = "unblocked", "Unblocked"
        CANCELLED = "cancelled", "Cancelled"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="audit_entries")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_actions")
    actor_name = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=32, choices=Action.choices)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "task audit entry"
        verbose_name_plural = "task audit entries"

    def __str__(self):
        return f"{self.task_id}: {self.action} by {self.actor_name}"


# ---------------------------------------------------------------------------
# Templates (Phase T2.9)
# ---------------------------------------------------------------------------
class TaskTemplate(models.Model):
    """
    A reusable task shape: title, description, priority, a due-date offset, and
    a checklist — "Website Launch", "Audit Review", "Monthly Report", "HR
    Onboarding".

    A TEMPLATE IS A COPY SOURCE, NOT A LIVING PARENT
    ------------------------------------------------
    Applying one COPIES its checklist onto a new task. It does not link them.
    That is the whole design decision here, and it is deliberate: if a template
    stayed authoritative, editing "HR Onboarding" in March would silently
    rewrite the checklist of every onboarding in flight, changing what people
    had already been asked to do and invalidating ticks they had already made.
    Tasks keep a `template` foreign key purely so "where did this come from"
    stays answerable.

    A template has no assignees and no due DATE — only `default_due_in_days`.
    Templates outlive the people and the calendar dates they were written
    against, and a template that assigned work to somebody who left last year
    would be worse than no template.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    title_template = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Default title for tasks raised from this template. Falls back "
                  "to the template name.")
    priority = models.CharField(
        max_length=10, choices=Task.Priority.choices, default=Task.Priority.MEDIUM)
    # Days from the day the task is raised. Null = no due date, which is a real
    # answer for a template whose deadline depends on the occasion.
    default_due_in_days = models.PositiveSmallIntegerField(null=True, blank=True)

    # Scope. A template with no department is available to everybody; one with a
    # department belongs to that department. There is no per-user private
    # template: a template nobody else can see is a note, and this is the wrong
    # place to keep notes.
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="task_templates")
    department_name = models.CharField(max_length=150, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_templates_created")
    created_by_name = models.CharField(max_length=150, blank=True, default="")
    # Retired rather than deleted, so the tasks that point at it keep a name.
    is_active = models.BooleanField(default=True, db_index=True)
    usage_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-usage_count", "name"]
        constraints = [
            # Was (name) alone: two tenants could not both have an
            # "Onboarding Checklist" template.
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="task_template_org_name_unique"),
        ]

    def __str__(self):
        return self.name

    @property
    def item_count(self):
        return len(self.items.all())

    @property
    def section_count(self):
        """How many tasks a group raised from this template would contain."""
        return len(self.groups.all())


class TaskTemplateGroup(models.Model):
    """A checklist section on a template — the shape TaskChecklistGroup copies."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    template = models.ForeignKey(
        TaskTemplate, on_delete=models.CASCADE, related_name="groups")
    title = models.CharField(max_length=150)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return self.title


class TaskTemplateItem(models.Model):
    """One checklist line on a template. No completion state — nothing to tick."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    template = models.ForeignKey(
        TaskTemplate, on_delete=models.CASCADE, related_name="items")
    group = models.ForeignKey(
        TaskTemplateGroup, on_delete=models.CASCADE, null=True, blank=True,
        related_name="items")
    text = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return self.text


# ---------------------------------------------------------------------------
# Task groups (Phase T4.7)
# ---------------------------------------------------------------------------
class TaskGroup(models.Model):
    """
    A batch of tasks raised together — normally from a template whose checklist
    sections each become a task of their own.

    WHY A GROUP IS NOT A PARENT TASK
    --------------------------------
    A parent task would need a status, and its status would have to mean
    something: is "Website Launch" in progress when three of its five tasks are?
    Every answer to that is a rule somebody has to learn, and the workflow engine
    would need a second, different set of transitions for a task nobody actually
    does. A group is a label and a creation event — it has no status, no
    assignee and no workflow — so the five real tasks keep the one lifecycle this
    system has, and the group answers only "what was raised together".
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    template = models.ForeignKey(
        "tasks.TaskTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="groups_created")
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="task_groups")
    department_name = models.CharField(max_length=150, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="task_groups_created")
    created_by_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Reminders and escalation (Phase T4.4, T4.5)
# ---------------------------------------------------------------------------
class TaskReminderLog(models.Model):
    """
    One row per (task, kind, day) — the idempotency record behind the reminder
    and escalation engines.

    WHY THE DATE IS PART OF THE KEY
    -------------------------------
    Reminders repeat: a task overdue for a week should be chased more than once,
    and "three days overdue" is a different message from "one day overdue". What
    must NOT happen is the same reminder going twice because the scheduler ran
    twice, or because somebody re-ran the command by hand after a failure. So the
    unique key is (task, kind, sent_on): the same reminder is sent at most once
    per calendar day, and re-running the command the same afternoon is free.

    This is the state the T3 event seam deliberately did not hold — see
    tasks/events.py, which said the receiver would own collapsing duplicates.
    This is that receiver's state, and it lives here rather than in the
    notifications app because the collapsing rule is a task rule.
    """
    class Kind(models.TextChoices):
        # Approach reminders, named for the distance rather than a number, so a
        # reading of the log says what was sent rather than needing the schedule
        # to decode it.
        DUE_7 = "due_7", "Due in 7 days"
        DUE_3 = "due_3", "Due in 3 days"
        DUE_1 = "due_1", "Due tomorrow"
        DUE_TODAY = "due_today", "Due today"
        OVERDUE = "overdue", "Overdue"
        REVIEW_PENDING = "review_pending", "Review pending"
        # Phase TASK-AUTOSAVE-AND-SUBTASKS. One row per task per day; the
        # subtask ids go in `recipients` beside the user ids.
        SUBTASK_OVERDUE = "subtask_overdue", "Subtask overdue"
        # Escalation rungs. Each is sent once per day at most, like any other.
        ESCALATED_SUPERVISOR = "escalated_supervisor", "Escalated to supervisor"
        ESCALATED_HR = "escalated_hr", "Escalated to HR"
        ESCALATED_MANAGEMENT = "escalated_management", "Escalated to management"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="reminders")
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    sent_on = models.DateField(db_index=True)
    # Who it actually reached, as a snapshot. The recipients of an escalation are
    # the point of it, and resolving them again later would answer with today's
    # org chart rather than the one that applied.
    recipients = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_on", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "kind", "sent_on"],
                name="task_reminder_once_per_day"),
        ]
        indexes = [
            models.Index(fields=["kind", "sent_on"], name="task_reminder_kind_idx"),
        ]

    def __str__(self):
        return f"{self.task_id}: {self.kind} on {self.sent_on}"


# ---------------------------------------------------------------------------
# Appraisal evidence (Phase T5.8)
# ---------------------------------------------------------------------------
class EmployeeTaskEvidenceSnapshot(models.Model):
    """
    A daily, immutable record of what one employee's task data SAID on one day.

    WHY A SNAPSHOT AND NOT A QUERY
    ------------------------------
    Everything in here can be computed live from the task tables, and Phase T5's
    evidence API does exactly that for the current period. The snapshot exists
    for one reason: appraisal happens months later, and by then the underlying
    tasks have moved. Somebody archived, somebody reassigned, a department was
    renamed, a task was cancelled. A figure recomputed in December against
    March's work is not March's figure, and an appraisal conversation that turns
    on a number nobody can reproduce is worse than no number at all.

    So this is written once per employee per day and never updated. It is what
    the data said that day.

    THIS IS EVIDENCE, NOT AN ASSESSMENT
    -----------------------------------
    There is no score field, no rating, no band, no weighting and no comparison
    to anybody else. Every column is a count or a duration that can be traced
    back to specific tasks. Phase T5's instruction is explicit — build the
    evidence service, not appraisal — and the absence of a judgement column is
    the difference between the two. A future appraisal module may weigh these
    numbers; deciding how is that module's job and its accountability, and it
    must not find the decision already made for it here.

    NOTHING HERE IS DERIVED THAT CANNOT BE RE-DERIVED
    --------------------------------------------------
    Percentages are stored alongside their numerator and denominator, so a
    reader can always see what a figure was over. A stored percentage with no
    denominator is the kind of number that gets quoted for years.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="task_evidence_snapshots", db_index=True)
    # Snapshots survive the account being renamed or the person moving, so the
    # name and department are frozen with the numbers.
    employee_name = models.CharField(max_length=150, blank=True, default="")
    department_name = models.CharField(max_length=150, blank=True, default="")

    snapshot_date = models.DateField(db_index=True)
    # Phase T6.3. Which cadence this row belongs to. A daily row and the monthly
    # row that contains it are BOTH kept: the daily series is what shows a
    # trend, and the monthly one is what an appraisal quotes. Deriving either
    # from the other later would mean recomputing against tasks that have since
    # moved, which is the whole reason snapshots exist.
    period_type = models.CharField(
        max_length=12, default="daily", db_index=True,
        choices=[("daily", "Daily"), ("monthly", "Monthly"),
                 ("quarterly", "Quarterly"), ("annual", "Annual")])
    # The window the figures cover. Stored rather than implied, because "as at
    # today" means something different every day it is read.
    period_start = models.DateField()
    period_end = models.DateField()

    # --- counts (Part 7) ---
    tasks_assigned = models.PositiveIntegerField(default=0)
    tasks_completed = models.PositiveIntegerField(default=0)
    tasks_open = models.PositiveIntegerField(default=0)
    tasks_overdue = models.PositiveIntegerField(default=0)
    # Numerator and denominator kept beside every percentage — see the docstring.
    completed_with_due_date = models.PositiveIntegerField(default=0)
    completed_on_time = models.PositiveIntegerField(default=0)
    completion_percent = models.PositiveSmallIntegerField(default=0)
    on_time_percent = models.PositiveSmallIntegerField(default=0)

    average_completion_days = models.DecimalField(
        max_digits=7, decimal_places=1, null=True, blank=True,
        help_text="Null means nothing completed in the window — not zero days.")

    # --- participation (Part 7) ---
    reviews_performed = models.PositiveIntegerField(default=0)
    checklist_items_total = models.PositiveIntegerField(default=0)
    checklist_items_completed = models.PositiveIntegerField(default=0)
    evidence_files_submitted = models.PositiveIntegerField(default=0)
    comments_posted = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-snapshot_date", "employee_name"]
        verbose_name = "employee task evidence snapshot"
        constraints = [
            # One per employee per cadence per day. A second run must not create
            # a second, slightly different version of the same period's truth —
            # and the monthly row must not collide with the daily one written
            # the same afternoon.
            models.UniqueConstraint(
                fields=["employee", "period_type", "snapshot_date"],
                name="task_evidence_one_per_period"),
        ]
        indexes = [
            models.Index(fields=["snapshot_date", "employee"],
                         name="task_evidence_date_idx"),
            models.Index(fields=["period_type", "snapshot_date"],
                         name="task_evidence_period_idx"),
        ]

    def __str__(self):
        return f"{self.employee_name} — {self.period_type} {self.snapshot_date}"
