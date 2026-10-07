import uuid

from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models.functions import Lower


def profile_photo_path(instance, filename):
    """``org/<id>/profiles/<YYYY>/<MM>/<uuid>/<file>`` (Phase S3).

    Was the literal string "profiles/%Y/%m/", which had two problems: no tenant
    in the path, and the ORIGINAL FILENAME preserved verbatim -- so a photo was
    at a guessable location, unlike every other upload in this project. Both
    are fixed by routing through the shared standard.

    A module-level named function, not a lambda: Django serialises `upload_to`
    into the migration by import path.
    """
    from tenancy.storage import upload_path

    # A PLATFORM OPERATOR'S PHOTO IS NOT TENANT DATA. They belong to no
    # organization, so the shared standard had nothing to put in the org/
    # segment and wrote ``org/_unscoped/...`` -- and the media server, reading
    # that segment as an owner, then refused every link to it as a
    # cross-tenant request. An operator who uploaded a photo saw a broken
    # image, everywhere, forever. Found by driving the live console.
    #
    # ``staff/`` declares no organization, so the serving view protects it
    # the way it protects every ownerless path: the signature, the short TTL
    # and the authorisation of the view that minted the link.
    if (getattr(instance, "organization_id", None) is None
            and getattr(instance, "is_platform_staff", False)):
        import os
        import uuid

        from django.utils import timezone

        name = os.path.basename(str(filename).replace("\\", "/")) or "photo"
        return (f"staff/profiles/{timezone.now():%Y/%m}/"
                f"{uuid.uuid4().hex}/{name}")

    return upload_path(instance, filename, module="profiles")


class UnscopedUserManager(UserManager):
    """Every tenant's users. The deliberate, greppable escape hatch.

    `User.all_tenants` -- read by the platform console and by the Phase S5
    conformance tests, and by nothing else. Keeping it a full UserManager
    means `createsuperuser` and the shell still have an unfiltered handle when
    they genuinely need one.
    """


class TenantUserManager(UserManager):
    """Stamps the organization on every user this manager creates, and SCOPES
    every read to the tenant in context (Phase S5 Part 3).

    THE SCOPING IS WHAT CLOSES R4. `users/views.py:141` (UserListView) and
    `users/admin_views.py:36` (AdminUserViewSet.get_queryset) have returned
    `User.objects.filter(is_active=True)` and `User.objects.all()` since long
    before tenancy existed -- a full directory and full CRUD over every
    tenant's accounts. Neither call site changes: the manager under them does.

    It cannot subclass TenantManager as well, because UserManager carries the
    create_user/create_superuser contract Django's auth machinery depends on.
    So the one method that matters is reproduced here, with the same rules and
    the same reasons -- see tenancy.scoping.TenantManager for the long form:
    no database work in get_queryset (import-time callers), the refusal
    DEFERRED to evaluation (import-time callers again, Phase S6),
    pass-through while TENANCY_ENABLED is False, and fail closed once it is
    True.

    WHY THIS EXISTS. Phase S2 enforces a database-level rule that a user is
    either a tenant user (organization set) or a platform user (organization
    null, is_platform_staff true) and never neither. There are ~107 places in
    this project that create a user -- tests, fixtures, management commands,
    `createsuperuser` -- and none of them knows about organizations. Making all
    of them pass one would be a large mechanical diff that adds no safety;
    worse, it would mean the next caller to forget is a crash rather than a
    sensible default.

    So the organization is resolved the same way everything else in Phase S2
    resolves it (tenancy.scoping.active_organization), which while
    TENANCY_ENABLED is False is "the single organization that exists". The
    caller may always pass one explicitly, and a platform account is never
    stamped.
    """

    def get_queryset(self):
        """Scope to the tenant in context, DEFERRING when there is none.

        Phase S6 moved the refusal from here to evaluation, for the same
        reason as tenancy.scoping.TenantManager -- which is where the full
        story is. In short: raising at queryset CONSTRUCTION made the project
        unimportable with TENANCY_ENABLED=1, because declarative code
        (django-filter, DRF) builds querysets off the default manager at
        import time without any intention of running them.

        `TenantQuerySet` carries the deferral, so this returns one. Django's
        UserManager has no opinion about the queryset class, and
        `_create_user` / `get_by_natural_key` below are untouched by it.
        """
        from tenancy.context import current_org_id
        from tenancy.scoping import TenantQuerySet

        queryset = TenantQuerySet(self.model, using=self._db)
        org_id = current_org_id()
        if org_id is not None:
            return queryset.filter(organization_id=org_id)

        queryset._tenant_scope_pending = True
        return queryset

    def _create_user(self, username, email=None, password=None, **extra):
        if not extra.get("is_platform_staff") and not extra.get("organization"):
            from tenancy.scoping import active_organization

            extra["organization"] = active_organization(required=False)
        return super()._create_user(username, email, password, **extra)

    def get_by_natural_key(self, username):
        """Look a user up by username, disambiguating by tenant if needed.

        ``username`` is no longer globally unique (Phase S2), so this can match
        more than one row once a second tenant exists -- and ModelBackend,
        `createsuperuser` and the Django admin all go through here.

        Behaviour is UNCHANGED while one tenant exists: the fast path is the
        same single ``get()`` it always was, and the tenant narrowing only runs
        if that query finds more than one row. So this adds no query and no
        risk today, and is correct the day it stops being true.
        """
        try:
            return super().get_by_natural_key(username)
        except self.model.MultipleObjectsReturned:
            from tenancy.scoping import active_organization

            organization = active_organization(required=False)
            if organization is None:
                raise
            return self.get(**{self.model.USERNAME_FIELD: username,
                               "organization": organization})

    def create_superuser(self, username, email=None, password=None, **extra):
        """A Django superuser is a TENANT admin, not a platform operator.

        Deliberate: `createsuperuser` has always produced NIF's administrator,
        and platform staff are created explicitly with is_platform_staff=True.
        tenancy.permissions.IsPlatformStaff refuses superusers for exactly this
        reason -- every admin guard in this project is an allow-list.
        """
        return super().create_superuser(username, email, password, **extra)


class User(AbstractUser):
    """
    Custom user model for the platform.
    Uses UUID as the primary key.
    """
    class Roles(models.TextChoices):
        # Business-facing labels (Phase 2.6). The stored VALUES are unchanged
        # (maker/checker/approver/admin) so the leave/memo workflow engine,
        # permissions, policies and existing data keep working untouched; only
        # the display names become the clear business roles.
        MAKER = "maker", "Employee"
        CHECKER = "checker", "Department Head"
        APPROVER = "approver", "HR"
        # Phase BOD-ROLE-EXECUTIVE-GOVERNANCE. Sits between Admin and HR in the
        # hierarchy: sees the whole organisation, approves Department Heads'
        # leave, and configures nothing. Deliberately NOT an admin - every admin
        # guard in the codebase is an allow-list on "admin", so this role is
        # refused system configuration, user and role management by default.
        # See users/roles.py for what it IS granted.
        BOD = "bod", "Board of Directors"
        ADMIN = "admin", "Admin"

    class EmployeeType(models.TextChoices):
        # Org-hierarchy / seniority label - independent of `role` (permissions).
        EMPLOYEE = "employee", "Employee"
        SUPERVISOR = "supervisor", "Supervisor"
        MANAGER = "manager", "Manager"
        DEPARTMENT_HEAD = "department_head", "Department Head"
        HR_OFFICER = "hr_officer", "HR Officer"
        SYSTEM_ADMIN = "system_admin", "System Admin"

    class EmploymentType(models.TextChoices):
        # Contract/engagement basis. Drives the leave *category* engine together
        # with continuous service (date_of_joining). Distinct from both `role`
        # (permissions) and `employee_type` (org rank).
        PERMANENT = "permanent", "Permanent"
        POST_PROBATION = "post_probation", "Post-Probation"
        PROBATION = "probation", "Probation"
        INTERN = "intern", "Intern"
        VOLUNTEER = "volunteer", "Volunteer"

    class LeaveCategory(models.TextChoices):
        # Cached result of the category engine (leaves.category_engine). Never
        # NULL after resolution; PROBATION is the sub-1-quarter floor tier.
        A = "A", "Category A — Permanent (>3 yrs)"
        B = "B", "Category B — Permanent (1–3 yrs)"
        C = "C", "Category C — Post-Probation / Permanent (<1 yr)"
        D = "D", "Category D — Intern"
        # Volunteers were folded into D, which gave them an intern's
        # entitlement. The organisation's policy grants them materially less
        # (2 annual / 3 sick against an intern's 4 / 5) and no maternity,
        # paternity or compensatory leave at all, so they need their own tier.
        E = "E", "Category E — Volunteer"
        PROBATION = "PROBATION", "Probation (<3 mo)"

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        UNDISCLOSED = "undisclosed", "Prefer not to say"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ---- Tenancy (Phase S1) ------------------------------------------------
    #
    # A user is EITHER a tenant user or a platform user, never both:
    #
    #   tenant user    organization IS NOT NULL   is_platform_staff = False
    #   platform user  organization IS NULL       is_platform_staff = True
    #
    # NULLABLE ON PURPOSE, FOR NOW. Phase S1 is the foundation only: no user
    # rows are migrated yet, so every existing NIF account still has
    # organization = NULL. The complementary half of the rule -- "a tenant user
    # MUST have an organization" -- therefore cannot be a database constraint
    # until the S2 backfill has run, and `user_tenant_has_organization` is
    # added there. See tenancy/inventory.py.
    #
    # The half that CAN be enforced today is the half that carries the risk:
    # a platform user must have no organization. That is what stops a
    # customer's admin from also holding platform-wide authority, and it is
    # enforced three ways -- the database constraint below, this model's
    # save(), and the serializer deny-list in users.serializers.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT,
        null=True, blank=True, related_name="users",
        help_text="The tenant this user belongs to. NULL for platform staff.",
    )
    is_platform_staff = models.BooleanField(
        default=False,
        help_text="Platform operator, not a customer's employee. Grants access "
                  "to the platform console and to NO tenant workspace.",
    )

    # Phase S2. NOT globally unique any more -- uniqueness is per-organization,
    # declared in Meta.constraints below. Two companies must both be able to
    # have an "admin" account, which AbstractUser's unique=True forbade.
    #
    # Everything else about the field is AbstractUser's, reproduced so the
    # validator, help text and error message do not silently change.
    username = models.CharField(
        "username",
        max_length=150,
        help_text="Required. 150 characters or fewer. Letters, digits and "
                  "@/./+/-/_ only.",
        validators=[UnicodeUsernameValidator()],
        error_messages={"unique": "A user with that username already exists."},
    )

    objects = TenantUserManager()
    # Unfiltered, and named so that every cross-tenant read of the user table
    # is one grep away in review.
    all_tenants = UnscopedUserManager()

    role = models.CharField(max_length=20, choices=Roles.choices, default=Roles.MAKER)
    # Phase 2.6: org-rank label, separate from the permission `role` above.
    employee_type = models.CharField(
        max_length=20, choices=EmployeeType.choices, default=EmployeeType.EMPLOYEE,
    )
    # Leave-category engine inputs/outputs (all additive; existing rows default to
    # PERMANENT and are re-resolved + flagged by the backfill migration).
    employment_type = models.CharField(
        max_length=20, choices=EmploymentType.choices, default=EmploymentType.PERMANENT,
    )
    gender = models.CharField(
        max_length=12, choices=Gender.choices, default=Gender.UNDISCLOSED, blank=True,
    )
    # Eligibility is a separate, HR-overridable flag rather than a hard gender
    # gate (inclusive of adoption / same-sex parents / legal edge cases). Saving
    # the user auto-defaults these from gender unless HR has set them explicitly;
    # both are always suppressed for Category D (Intern/Volunteer) at read time.
    maternity_eligible = models.BooleanField(default=False)
    paternity_eligible = models.BooleanField(default=False)
    # Cached category (leaves.category_engine.resolve_category). Recomputed on
    # save/login/rollover; kept on the row so reads don't recompute every time.
    leave_category = models.CharField(
        max_length=12, choices=LeaveCategory.choices, null=True, blank=True,
    )
    # Human-readable note when a fallback/auto-transition rule fired; surfaced to
    # HR in the review list. Null = clean resolution, nothing to review.
    category_flag = models.TextField(null=True, blank=True)
    # Legacy free-text department (Level 1). Kept for backward compatibility.
    department = models.CharField(max_length=100, blank=True, null=True)
    # Phase 4: structured department reference used by leave-policy resolution.
    # NOTE (Phase 2.5): this IS the "department FK" the corrected spec asks for -
    # it already existed, so we reuse it rather than add a duplicate.
    department_ref = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="members",
    )

    # Phase 2.5: HR profile fields (all additive/nullable so existing rows are safe).
    # Phase S2. Per-organization, not global: "NIFN-EMP-2026-0001" and
    # "ABCS-EMP-2026-0001" are different ids, but two tenants numbering their
    # own staff must not be able to block each other either way.
    employee_id = models.CharField(max_length=32, null=True, blank=True, editable=False)
    # The employee's id on the ZK biometric terminal (morx `employees.id`). This
    # is what links a logged-in user to their own punches in the biometric
    # dashboard; set by HR. Null = not linked, so no biometric data is shown.
    biometric_id = models.CharField(max_length=16, null=True, blank=True)
    designation = models.CharField(max_length=120, blank=True, null=True)
    # Small per-person interface state the server must remember across
    # devices: which guided tours this person has finished. A tour shown again
    # on every new phone is a tour people learn to dismiss unread.
    ui_state = models.JSONField(default=dict, blank=True)
    date_of_joining = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=32, blank=True, null=True)
    profile_photo = models.ImageField(upload_to=profile_photo_path,
                                       max_length=255, null=True, blank=True)
    # Employee self-service personal fields (Profile module; all additive/nullable
    # so existing rows are unaffected). Editable by the employee themselves.
    address = models.CharField(max_length=255, blank=True, default="")
    emergency_contact_name = models.CharField(max_length=120, blank=True, default="")
    emergency_contact_number = models.CharField(max_length=32, blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)
    bio = models.TextField(blank=True, default="")
    # Admin-created accounts must change their password on first login.
    must_change_password = models.BooleanField(default=True)
    last_password_change = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="users_created",
    )

    @property
    def full_name(self):
        return self.get_full_name() or self.username

    @property
    def department_name(self):
        if self.department_ref_id:
            return self.department_ref.name
        return self.department or None

    def save(self, *args, **kwargs):
        # A Board account can never carry Django's staff or superuser flag
        # (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE). Every admin guard in this
        # project accepts `role == "admin" OR is_staff OR is_superuser`, so a
        # Board member with either flag would walk straight into user management
        # and system configuration - the one thing this role is defined NOT to
        # reach. Refused at the model so no form, command or shell can create it.
        if self.role == self.Roles.BOD and (self.is_staff or self.is_superuser):
            from django.core.exceptions import ValidationError
            raise ValidationError(
                "A Board of Directors account cannot have staff or superuser access. "
                "The Board has organisation-wide visibility, not system administration.")

        # Phase S1. A platform user belongs to no tenant. Refused here as well
        # as by the `user_platform_staff_has_no_org` database constraint, so a
        # form, a management command or a shell session all get the same clear
        # error instead of an IntegrityError from the driver.
        #
        # SKIPPED WHEN EITHER FIELD IS DEFERRED, and that is not defensive
        # padding: leaves.0014_backfill_categories runs before users.0012 on a
        # fresh database and uses .defer() to cope (it cannot declare a later
        # users dependency without making Django reject every production
        # database that applied it years ago -- see that migration's comment).
        # Touching a deferred attribute here would trigger a refetch of a column
        # that does not exist yet and fail the migration. The database
        # constraint remains the authority in that window.
        if not {"is_platform_staff", "organization"} & self.get_deferred_fields():
            # Phase S2. Stamp the tenant on insert so the XOR check constraint
            # below is satisfiable by every caller that does not know about
            # organizations yet -- see TenantUserManager for why that is the
            # right trade. A platform account is never stamped.
            if (self._state.adding and self.organization_id is None
                    and not self.is_platform_staff):
                from tenancy.scoping import active_organization

                organization = active_organization(required=False)
                if organization is not None:
                    self.organization = organization

            if self.is_platform_staff and self.organization_id is not None:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    "A platform staff account cannot belong to an organization. "
                    "Platform users administer the platform; they are not any "
                    "customer's employee.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_full_name()} ({self.role})"

    # ---- Tenancy helpers (Phase S1) ---------------------------------------
    @property
    def is_platform_user(self):
        """Administers the platform. Belongs to no tenant workspace."""
        return bool(self.is_platform_staff)

    @property
    def is_tenant_user(self):
        """A customer's employee, scoped to one organization."""
        return not self.is_platform_staff and self.organization_id is not None

    class Meta(AbstractUser.Meta):
        constraints = [
            # ---- the tenant / platform XOR, now complete (Phase S2) --------
            #
            # Phase S1 could only enforce one half, because every existing row
            # still had organization = NULL. The backfill in
            # users.0013 fixed that, so the full rule is a database constraint
            # from here on: a user is a tenant user XOR a platform user, never
            # neither and never both.
            models.CheckConstraint(
                condition=~models.Q(is_platform_staff=True,
                                    organization__isnull=False),
                name="user_platform_staff_has_no_org",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(organization__isnull=False, is_platform_staff=False)
                    | models.Q(organization__isnull=True, is_platform_staff=True)
                ),
                name="user_tenant_has_organization",
            ),

            # ---- identity, scoped to the tenant (Phase S2) ----------------
            #
            # TWO CONSTRAINTS PER FIELD, NOT ONE, AND THE REASON MATTERS.
            # PostgreSQL treats NULLs as distinct in a unique index, so a
            # plain UniqueConstraint(organization, username) would NOT stop two
            # PLATFORM users (organization IS NULL) sharing a username. Each
            # field therefore gets a tenant-scoped constraint and a partial
            # constraint covering the null-organization case.
            models.UniqueConstraint(
                fields=["organization", "username"],
                condition=models.Q(organization__isnull=False),
                name="uniq_user_org_username",
            ),
            models.UniqueConstraint(
                fields=["username"],
                condition=models.Q(organization__isnull=True),
                name="uniq_platform_user_username",
            ),

            # Email is the login credential, so this is the constraint that
            # makes tenant-aware login SOUND rather than merely careful: with
            # at most one row per (organization, email), the lookup in
            # users.token_serializers cannot raise MultipleObjectsReturned.
            #
            # Lower(...) because the old code compared with __iexact in places
            # and == in others; folding case here means "Admin@x" and
            # "admin@x" cannot both exist and then resolve differently
            # depending on which endpoint was called.
            #
            # Excludes the empty string: email is blank=True and legitimately
            # unset on some accounts, so an unconditional constraint would
            # allow only ONE such user per organization.
            models.UniqueConstraint(
                Lower("email"), "organization",
                condition=models.Q(organization__isnull=False) & ~models.Q(email=""),
                name="uniq_user_org_email_ci",
            ),
            models.UniqueConstraint(
                Lower("email"),
                condition=models.Q(organization__isnull=True) & ~models.Q(email=""),
                name="uniq_platform_user_email_ci",
            ),

            # Was unique=True on employee_id alone.
            models.UniqueConstraint(
                fields=["organization", "employee_id"],
                condition=models.Q(employee_id__isnull=False),
                name="uniq_user_org_employee_id",
            ),
        ]
