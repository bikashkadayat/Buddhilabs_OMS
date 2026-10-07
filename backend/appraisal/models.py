"""
The Appraisal module's data model.

WHAT THIS MODULE IS, AND THE ONE LINE IT DOES NOT CROSS
-------------------------------------------------------
This is a HUMAN performance-management process with a machine-assisted evidence
pack. People set goals, people write assessments, people rate competencies, and
people decide outcomes. The system's job is to put the right evidence in front
of them, route the conversation between the right people, and record what was
decided.

What it never does is DECIDE. There is:

  * no computed score of any kind;
  * no composite or overall rating — not from goals, not from competencies, not
    from task evidence;
  * no ranking, ordering by performance, or leaderboard;
  * no algorithm that converts task activity into a judgement.

The specification asks for `CompetencyRating` and for "promotion readiness", and
both live here — because both are things a PERSON records, with their reasons,
and can be held to. The prohibition is on the machine forming an opinion, not on
a manager forming one. `appraisal/tests/test_fairness.py` asserts the difference
rather than trusting it to survive.

WHY COMPETENCY LEVELS ARE WORDS AND NOT NUMBERS
-----------------------------------------------
`CompetencyRating.level` is an ordinal TextChoice — "Needs Development" through
"Outstanding" — not an integer 1-5. Stored as integers, somebody averages them
within a month, and an "overall competency score of 3.4" is exactly the
composite this module is forbidden to produce. Words are still ordered and still
comparable by a human, and they make the average nobody should be computing
awkward enough that writing one is a deliberate act rather than a convenience.

Every rating also REQUIRES a comment. A level with no reasoning is a number in
disguise.

EVIDENCE IS CITED, NEVER RECOMPUTED
-----------------------------------
`EvidenceReference` records what the task module already said, with its
provenance and the moment it was read. This module runs no metric of its own —
see appraisal/evidence_link.py. A second evidence system would be a second set
of numbers that disagrees with the first, and the disagreement would surface in
the appraisal conversation itself.
"""
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from tenancy.scoping import AllTenantsManager, TenantManager


class AppraisalCycle(models.Model):
    """
    One appraisal round — "FY 2083/84 Annual", "Mid-Year 2083".

    A cycle owns the calendar, not the outcome: it says when goal setting opens
    and when the round closes, and every appraisal inside it moves through its
    own workflow independently. Cycles are never deleted once they hold
    appraisals; they are closed, because a closed appraisal is a record somebody
    may have to produce years later.
    """
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
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

    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.DRAFT, db_index=True)

    period_start = models.DateField()
    period_end = models.DateField()
    # The windows the process is meant to happen in. Advisory: the workflow does
    # not refuse a late self-assessment, because a system that locks somebody out
    # of their own appraisal for being three days late creates an HR problem
    # rather than solving one. They drive reminders and the "overdue" columns.
    goal_setting_deadline = models.DateField(null=True, blank=True)
    self_assessment_deadline = models.DateField(null=True, blank=True)
    review_deadline = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="appraisal_cycles_created")
    created_by_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_start", "name"]
        constraints = [
            # Was unique=True on `name`: two tenants could not both run an
            # "FY 2082-83" cycle.
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_appraisal_cycle_org_name"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        return self.status == self.Status.ACTIVE


class Competency(models.Model):
    """
    The competency catalogue — the ten the specification names, seeded by
    migration.

    A table rather than an enum because an organisation adds or retires one
    without a deployment, and because a retired competency must keep rendering
    on the appraisals that already used it. Retired, never deleted.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=40)
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

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    ordering = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ordering", "name"]
        verbose_name_plural = "competencies"
        constraints = [
            # Was unique=True on `code`: two tenants could not both have a
            # "communication" competency.
            models.UniqueConstraint(fields=["organization", "code"],
                                    name="uniq_competency_org_code"),
        ]

    def __str__(self):
        return self.name


class Appraisal(models.Model):
    """
    One person's appraisal within one cycle.

    THE WORKFLOW IS THE SPECIFICATION'S, EXACTLY
    --------------------------------------------
        Goal Setting -> Mid-Year Review -> Self Assessment -> Supervisor Review
        -> Review Committee -> Final Review -> Development Plan -> Training Plan
        -> Closed

    Nine states, each a real conversation with a real owner. `appraisal/workflow.py`
    is the only thing that may assign to `status`, and every transition records
    who moved it and why.

    WHAT IS DELIBERATELY ABSENT FROM THIS MODEL
    -------------------------------------------
    There is no `score`, no `overall_rating`, no `band`, no `percentile` and no
    `rank`. The outcome of an appraisal here is a written summary, a set of
    rated competencies each with its reasoning, a development plan and a
    training plan. If an organisation later wants a grade, that is a decision to
    be taken openly with the people it affects — not one this module makes for
    them by having a column ready.
    """
    class Status(models.TextChoices):
        GOAL_SETTING = "goal_setting", "Goal Setting"
        # Phase APM-FINAL. Separated from GOAL_SETTING because "the employee has
        # drafted objectives" and "the supervisor has accepted them as the basis
        # for the year" are different facts, and only the second should freeze
        # them. Collapsed into one transition, an employee could not tell
        # agreed-and-locked objectives from ones still under discussion — and
        # the moment of acceptance, which is the one somebody is held to, left
        # no trace of its own in the record.
        GOAL_APPROVAL = "goal_approval", "Goal Approval"
        MID_YEAR = "mid_year_review", "Mid-Year Review"
        SELF_ASSESSMENT = "self_assessment", "Self Assessment"
        SUPERVISOR_REVIEW = "supervisor_review", "Supervisor Review"
        COMMITTEE = "review_committee", "Review Committee"
        FINAL_REVIEW = "final_review", "Final Review"
        DEVELOPMENT_PLAN = "development_plan", "Development Plan"
        TRAINING_PLAN = "training_plan", "Training Plan"
        CLOSED = "closed", "Closed"

    # The ladder in order. Used by the engine to know what "the next step" is
    # without ten separate transition functions that could disagree. Every
    # "stage N of M" on screen derives M from this list rather than hard-coding
    # it, so inserting a stage is one edit here.
    LADDER = [
        Status.GOAL_SETTING, Status.GOAL_APPROVAL,
        Status.MID_YEAR, Status.SELF_ASSESSMENT,
        Status.SUPERVISOR_REVIEW, Status.COMMITTEE, Status.FINAL_REVIEW,
        Status.DEVELOPMENT_PLAN, Status.TRAINING_PLAN, Status.CLOSED,
    ]
    # A closed appraisal is a permanent record: no edits, no transitions, no
    # new goals. Enforced by the permission layer AND the engine.
    TERMINAL_STATUSES = frozenset({Status.CLOSED})
    # Goals may be shaped while they are being drafted and again at the mid-year
    # checkpoint. After that they are what the person was measured against, and
    # changing them retrospectively rewrites the question.
    #
    # GOAL_APPROVAL is deliberately NOT editable: the objectives are in front of
    # the supervisor for a decision, and a set that can be edited while it is
    # being approved is a set nobody can be held to. Rework goes back through
    # `return_stage` to GOAL_SETTING, which is visible in the audit trail —
    # unlike a quiet edit underneath the person reading them.
    GOAL_EDITABLE_STATUSES = frozenset({Status.GOAL_SETTING, Status.MID_YEAR})

    class PromotionReadiness(models.TextChoices):
        """
        A supervisor's answer to "is this person ready for more", in words.

        Three recorded values and a fourth that is the ABSENCE of a value:
        `promotion_readiness == ""` means the question was never reached, and it
        is not the same answer as "development required". A boolean would have
        collapsed those two, which puts a negative on every record that simply
        did not get there — so the empty string is load-bearing, and the reports
        omit those people rather than listing them as a no.

        READY_WITH_DEVELOPMENT is the value that makes the field honest. Forced
        to choose yes or no, a supervisor whose real answer is "yes, once they
        have run a project of their own" has to pick a wrong one, and whichever
        they pick is the one that gets quoted.

        These are not ordered, scored or summed anywhere. They are three labels
        a person selects and must justify — see `promotion_rationale`, which the
        final-review transition requires whenever any of them is set.
        """
        READY = "ready", "Ready"
        READY_WITH_DEVELOPMENT = "ready_with_development", \
            "Ready With Development"
        DEVELOPMENT_REQUIRED = "development_required", "Development Required"


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
    cycle = models.ForeignKey(AppraisalCycle, on_delete=models.PROTECT,
                              related_name="appraisals")
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="appraisals", db_index=True)
    employee_name = models.CharField(max_length=150, blank=True, default="")
    designation = models.CharField(max_length=120, blank=True, default="")

    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appraisals")
    department_name = models.CharField(max_length=150, blank=True, default="")

    # Who reviews. SET_NULL plus a snapshot, like everything historical in this
    # codebase: a supervisor who leaves must not make an appraisal undeletable
    # or nameless.
    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="appraisals_supervising", db_index=True)
    supervisor_name = models.CharField(max_length=150, blank=True, default="")
    committee = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name="appraisals_on_committee")

    status = models.CharField(max_length=24, choices=Status.choices,
                              default=Status.GOAL_SETTING, db_index=True)

    # --- the written record ---
    # Every one of these is somebody's words. None is computed.
    self_assessment = models.TextField(blank=True, default="")
    supervisor_comments = models.TextField(blank=True, default="")
    committee_comments = models.TextField(blank=True, default="")
    final_summary = models.TextField(blank=True, default="")

    # --- outcomes a HUMAN records (specification: promotion readiness,
    #     succession planning). Both are recommendations with reasons, never a
    #     computed readiness score. Both are absent rather than negative when
    #     the question was never reached — see PromotionReadiness above.
    promotion_readiness = models.CharField(
        max_length=24, choices=PromotionReadiness.choices, blank=True,
        default="", db_index=True)
    promotion_rationale = models.TextField(blank=True, default="")
    successor_for = models.CharField(
        max_length=150, blank=True, default="",
        help_text="A role this person is being developed towards, if any. Free "
                  "text and human-entered; nothing computes it.")

    # Lifecycle stamps, each written by exactly one transition.
    goals_agreed_at = models.DateTimeField(null=True, blank=True)
    mid_year_at = models.DateTimeField(null=True, blank=True)
    self_assessed_at = models.DateTimeField(null=True, blank=True)
    supervisor_reviewed_at = models.DateTimeField(null=True, blank=True)
    committee_reviewed_at = models.DateTimeField(null=True, blank=True)
    finalised_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "employee_name"]
        constraints = [
            # One appraisal per person per cycle. A second would mean two
            # records of the same conversation, and nobody could say which was
            # the real one.
            models.UniqueConstraint(fields=["cycle", "employee"],
                                    name="appraisal_one_per_cycle"),
        ]
        indexes = [
            models.Index(fields=["cycle", "status"], name="appraisal_cycle_idx"),
            models.Index(fields=["supervisor", "status"],
                         name="appraisal_supervisor_idx"),
        ]

    def __str__(self):
        return f"{self.employee_name} — {self.cycle}"

    @property
    def is_closed(self):
        return self.status in self.TERMINAL_STATUSES

    @property
    def goal_weight_total(self):
        """Sum of goal weights. Must reach 100 before objectives are agreed."""
        return sum(goal.weight for goal in self.goals.all())

    @property
    def stage_index(self):
        """
        This appraisal's position in the ladder, ONE-BASED: 1..10.

        One-based because every consumer renders it as "stage N of 10" to a
        person, and a human counting stages starts at one. It was zero-based
        until APM-FINAL.1, which meant an appraisal at Goal Setting reported
        "Stage 0 of 10" and the tracker highlighted nothing at all — while an
        appraisal at Final Review highlighted Review Committee, the stage
        before it.

        Anything comparing LADDER POSITIONS must use `LADDER.index()` directly
        rather than this property, or it is off by one — see
        `workflow.return_to`.
        """
        try:
            return self.LADDER.index(self.status) + 1
        except ValueError:  # pragma: no cover - defensive
            return 0


class Goal(models.Model):
    """
    One objective, its weight, and what actually happened against it.

    `achievement` and `progress` are the EMPLOYEE'S account, reviewed by their
    supervisor — not a figure derived from task data. Task evidence is attached
    alongside (see EvidenceReference) so the conversation can be had against
    real activity, but nothing here reads a task and decides a goal was met. A
    goal is a sentence about intent; a task count is not the same thing, and
    treating one as the other is how people start optimising for task volume.
    """
    class Status(models.TextChoices):
        """
        Where one objective is in its own life, which is not the same question
        as where the APPRAISAL is.

        DRAFT     — being written. Editable by the employee and the supervisor.
        APPROVED  — the supervisor has accepted it as the basis for the year.
                    Still adjustable at the mid-year checkpoint, because the
                    year moves and an objective nobody can revise is one people
                    quietly stop referring to.
        LOCKED    — the appraisal has passed mid-year. This is now what the
                    person was measured against, and editing it would rewrite
                    the question after the answer.

        Derived from the appraisal's stage by the workflow, never set by hand:
        two places deciding whether an objective is locked is two places that
        can disagree about what somebody agreed to.
        """
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        LOCKED = "locked", "Locked"

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
    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE,
                                  related_name="goals")
    objective = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    # Percentage points. The set must total exactly 100 before goals are agreed
    # — see appraisal/workflow.agree_goals.
    weight = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Percentage weight. All goals on an appraisal must total 100.")

    target = models.TextField(
        blank=True, default="",
        help_text="What success looks like, in the employee's and supervisor's "
                  "own words.")
    achievement = models.TextField(
        blank=True, default="",
        help_text="What actually happened. Written by the employee, discussed "
                  "with the supervisor.")
    progress_percent = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)],
        help_text="The employee's own assessment of progress. Not computed.")

    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.DRAFT, db_index=True)
    # Usually the employee, but a shared objective may sit with somebody else.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="appraisal_goals_owned")
    owner_name = models.CharField(max_length=150, blank=True, default="")

    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return f"{self.objective} ({self.weight}%)"


class CompetencyRating(models.Model):
    """
    One competency, as assessed by one person, with their reasoning.

    LEVELS ARE WORDS. See the module docstring: stored as 1-5 integers, somebody
    averages them and produces the composite this module may not have.

    `comment` is REQUIRED by the serializer. A level with no reasoning is a
    number in disguise, and it is the reasoning that the person being appraised
    can actually respond to.

    Both the employee (self-rating) and the supervisor rate the same
    competencies, and BOTH are kept. A self-assessment overwritten by a
    supervisor's view is not an appraisal, it is a verdict — and the gap between
    the two is usually the most useful thing in the conversation.
    """
    class Level(models.TextChoices):
        NEEDS_DEVELOPMENT = "needs_development", "Needs Development"
        DEVELOPING = "developing", "Developing"
        MEETS = "meets", "Meets Expectations"
        EXCEEDS = "exceeds", "Exceeds Expectations"
        OUTSTANDING = "outstanding", "Outstanding"

    class RatedBy(models.TextChoices):
        EMPLOYEE = "employee", "Self assessment"
        SUPERVISOR = "supervisor", "Supervisor"
        COMMITTEE = "committee", "Review committee"

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
    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE,
                                  related_name="competency_ratings")
    competency = models.ForeignKey(Competency, on_delete=models.PROTECT,
                                   related_name="ratings")
    competency_name = models.CharField(max_length=100, blank=True, default="")

    level = models.CharField(max_length=24, choices=Level.choices)
    comment = models.TextField(
        help_text="Why. A level without reasoning is a number in disguise.")

    rated_by_role = models.CharField(max_length=12, choices=RatedBy.choices)
    rated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="competency_ratings_given")
    rated_by_name = models.CharField(max_length=150, blank=True, default="")
    rated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["competency__ordering", "rated_by_role"]
        constraints = [
            # One rating per competency per rater ROLE. The employee's and the
            # supervisor's views coexist; a second supervisor rating would be an
            # edit, and edits go through the same row.
            models.UniqueConstraint(
                fields=["appraisal", "competency", "rated_by_role"],
                name="appraisal_one_rating_per_role"),
        ]

    def __str__(self):
        return f"{self.competency_name}: {self.get_level_display()}"


class DevelopmentPlan(models.Model):
    """
    What this person will work on, agreed between them and their supervisor.

    An ACTION, not a diagnosis: every row has something somebody will do, a
    person accountable and a date. A development plan that lists weaknesses
    without actions is an assessment wearing a plan's clothes.
    """
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        DEFERRED = "deferred", "Deferred"

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
    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE,
                                  related_name="development_plans")
    area = models.CharField(max_length=150,
                            help_text="The capability being developed.")
    action = models.TextField(help_text="What will actually be done.")
    support_required = models.TextField(blank=True, default="")
    competency = models.ForeignKey(
        Competency, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="development_plans")

    target_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PLANNED)
    accountable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="development_plans_accountable")
    accountable_name = models.CharField(max_length=150, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["target_date", "created_at"]

    def __str__(self):
        return self.area


class TrainingPlan(models.Model):
    """
    Training identified during the appraisal.

    Kept separate from the development plan because they are answered by
    different people: a development action is something the employee and their
    manager do, and a training need is a request to the organisation that HR has
    to fund and schedule. Folding them together makes the second invisible.
    """
    class Status(models.TextChoices):
        IDENTIFIED = "identified", "Identified"
        APPROVED = "approved", "Approved"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        DECLINED = "declined", "Declined"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Kind(models.TextChoices):
        """
        WHAT is being asked for, which decides who can supply it.

        A course, a certification, a mentor and a stretch assignment are four
        different requests with four different costs and four different owners,
        and only the first two need a budget line. Recorded as one field on one
        model rather than four models: the shape is identical — a need, a
        reason, a priority, a decision — and splitting it would triple the
        queries behind HR's training screen for no analytical gain.

        Before this field existed everything was "training", so a mentoring
        pairing could only be expressed by writing the word in the title, and
        HR's list of training needs silently included things nobody had to buy.
        """
        TRAINING = "training", "Training"
        CERTIFICATION = "certification", "Certification"
        MENTORSHIP = "mentorship", "Mentorship"
        ON_THE_JOB = "on_the_job", "On-the-job"

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
    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE,
                                  related_name="training_plans")
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=Kind.choices,
                            default=Kind.TRAINING, db_index=True)
    justification = models.TextField(blank=True, default="")
    # The skill gap this answers. Optional because a real need is sometimes
    # named before anybody has decided which competency it sits under, and
    # refusing the request until the taxonomy is right loses the request.
    competency = models.ForeignKey(
        Competency, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="training_plans")
    # Only meaningful for MENTORSHIP. Nullable rather than a separate model:
    # a pairing with no named mentor is the state a mentoring scheme is
    # actually in for most of its life, and a NOT NULL here would mean the
    # need could not be recorded until somebody had already volunteered.
    mentor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="mentorship_plans")
    mentor_name = models.CharField(max_length=150, blank=True, default="")

    priority = models.CharField(max_length=8, choices=Priority.choices,
                                default=Priority.MEDIUM)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.IDENTIFIED)
    target_period = models.CharField(
        max_length=100, blank=True, default="",
        help_text="When it is wanted — a quarter or a month, not a fixed date.")
    # HR's decision, recorded with a name. A declined request that nobody owns
    # is how training needs quietly disappear.
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="training_plans_decided")
    decided_by_name = models.CharField(max_length=150, blank=True, default="")
    decision_note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "created_at"]

    def __str__(self):
        return self.title


class EvidenceReference(models.Model):
    """
    A CITATION of evidence the task module already produced (Phase T6 contract).

    NOT A COPY OF THE EVIDENCE SYSTEM
    ---------------------------------
    This module computes no metric. It asks the evidence registry what the task
    module said about a person over a period, and records the answer here with
    its provenance — source, contract version, window, and the moment it was
    read. That is a citation, not a second system: there is exactly one place
    these numbers are calculated, and it is not this one.

    WHY THE VALUES ARE STORED RATHER THAN FETCHED LIVE
    --------------------------------------------------
    An appraisal is a conversation about a fixed period, held on a particular
    day, and sometimes revisited months later. Evidence that moved underneath it
    would mean the figure in the discussion and the figure in the record no
    longer match — and the person being appraised would have no way to show what
    they were actually shown. So the numbers are frozen at attach time, with
    `captured_at` saying exactly when, and the live API stays available beside
    them for anybody who wants today's picture.
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
    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE,
                                  related_name="evidence_references")
    # Optional: evidence attached to a specific objective rather than the whole
    # appraisal.
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, null=True,
                             blank=True, related_name="evidence_references")

    # Provenance. `source` is an evidence.schema.Source value; stored as text so
    # this table survives the contract gaining an eighth source.
    source = models.CharField(max_length=32, default="task", db_index=True)
    contract_version = models.CharField(max_length=12, blank=True, default="")
    period_start = models.DateField()
    period_end = models.DateField()
    period_type = models.CharField(max_length=12, default="annual")

    # The metrics exactly as the source reported them: a list of
    # {key,label,unit,value,definition,basis_of}. Stored whole rather than
    # flattened into columns, so a new metric needs no migration here and an old
    # one keeps rendering on the appraisals that cited it.
    metrics = models.JSONField(default=list, blank=True)
    # The disclaimer travels with the citation for the same reason it travels
    # with the evidence: so it cannot be left behind.
    disclaimer = models.TextField(blank=True, default="")
    note = models.TextField(
        blank=True, default="",
        help_text="Why this evidence was attached, in the attacher's words.")

    captured_at = models.DateTimeField(default=timezone.now)
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="appraisal_evidence_attached")
    attached_by_name = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["-captured_at"]
        verbose_name = "evidence reference"

    def __str__(self):
        return f"{self.source} evidence for {self.appraisal_id}"


class AppraisalAuditLog(models.Model):
    """
    The appraisal's activity timeline.

    Written inside the same transaction as the change it records. An appraisal
    is the most contestable record this system holds — somebody may need to show
    what was said, by whom, and when, years later — so every transition, every
    rating and every decision leaves a row, and rows are never updated or
    deleted.
    """
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        GOALS_SUBMITTED = "goals_submitted", "Goals submitted for approval"
        GOALS_AGREED = "goals_agreed", "Goals approved"
        GOALS_LOCKED = "goals_locked", "Goals locked"
        GOAL_CHANGED = "goal_changed", "Goal changed"
        PROMOTION_RECORDED = "promotion_recorded", "Promotion status changed"
        MID_YEAR_RECORDED = "mid_year_recorded", "Mid-year review recorded"
        SELF_ASSESSED = "self_assessed", "Self assessment submitted"
        SUPERVISOR_REVIEWED = "supervisor_reviewed", "Supervisor review recorded"
        COMMITTEE_REVIEWED = "committee_reviewed", "Committee review recorded"
        FINALISED = "finalised", "Final review recorded"
        DEVELOPMENT_PLANNED = "development_planned", "Development plan agreed"
        TRAINING_PLANNED = "training_planned", "Training plan agreed"
        RETURNED = "returned", "Returned to an earlier stage"
        CLOSED = "closed", "Closed"
        RATED = "rated", "Competency rated"
        EVIDENCE_ATTACHED = "evidence_attached", "Evidence attached"

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
    appraisal = models.ForeignKey(Appraisal, on_delete=models.CASCADE,
                                  related_name="audit_entries")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="appraisal_actions")
    actor_name = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=32, choices=Action.choices)
    from_status = models.CharField(max_length=24, blank=True, default="")
    to_status = models.CharField(max_length=24, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "appraisal audit entry"
        verbose_name_plural = "appraisal audit entries"

    def __str__(self):
        return f"{self.appraisal_id}: {self.action}"
