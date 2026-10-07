"""Tenant bootstrap: the configuration a new workspace cannot open without.

Phase S6 Part 4. The requirement is one sentence -- "a new customer must be
productive immediately; the system must never open completely empty" -- and
the reason it is a requirement is a specific failure found at the end of Phase
S5:

    test_leave_isolation  ->  Http404: No LeaveType matches 'annual'

That was not a bug in the test. ``provision_organization`` created an
Organization, Settings, Branding and Subscription, and stopped. Every piece of
configuration this product needs in order to DO anything -- leave types,
departments, shifts, an attendance policy, minute types -- existed only as
rows written by data migrations against the single pre-SaaS database. NIF had
them because the migrations ran once, on NIF. A tenant provisioned afterwards
got none of them, and could not approve a day's leave.

WHY THIS IS CODE AND NOT A MIGRATION
------------------------------------
A data migration runs once per DATABASE. Bootstrap data has to be written once
per TENANT, and tenants arrive after the migrations have all run. That is the
whole distinction, and it is why the existing seeds could not simply be
re-pointed: ``leaves/0005`` is a historical record of what happened to NIF's
database in 2026 and must keep saying exactly that.

So the catalogues below are a SECOND copy of several migration literals, and
that is deliberate. A test asserts the two agree where they are meant to
(``test_bootstrap.py``), which is the only honest way to hold two copies: name
them, and compare them automatically.

WHAT IT IS NOT
--------------
Not holidays -- a holiday calendar is specific to a country and a year, and
seeding Nepal's 2026 public holidays into a tenant in another country would be
worse than seeding none.

Not users. A workspace needs configuration to be usable; it needs PEOPLE to be
used, and who they are is the operator's decision. See
``services.provision_organization(admin_email=...)``.

IDEMPOTENT. Every write is a get_or_create keyed on the natural key within the
tenant, so re-running it on an existing tenant repairs what is missing and
touches nothing an administrator has since edited. That is what makes it safe
to expose as the console's "repair configuration" action.
"""
import logging
from datetime import date, time
from decimal import Decimal

from django.db import transaction

from .context import tenant_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The catalogues
# ---------------------------------------------------------------------------
# Part 4 names five: HR, Administration, Finance, Operations, IT. The codes
# match the four `leaves/0009` seeded for NIF (ICT, ADMIN, OPS, HR) so a
# tenant's departments are recognisable to anyone who knows NIF's, with
# Finance added because Part 4 asks for it.
DEPARTMENTS = [
    ("HR", "Human Resource Department"),
    ("ADMIN", "Administrative Department"),
    ("FIN", "Finance Department"),
    ("OPS", "Operational Department"),
    ("ICT", "ICT Department"),
]

# The brief names four: Annual, Sick, Unpaid, Special.
#
# NIF has five (Casual and Maternity as well). Those are NIF's staff policy
# rather than a platform default, and a leave type a tenant does not offer is
# worse than one they have to add: it appears in the apply dropdown and gets
# requested.
#
# SPECIAL AND UNPAID ARE SEEDED WITH ZERO DAYS, which is not an oversight.
# Both are granted by arrangement -- bereavement, marriage, study, leave
# without pay -- so there is no yearly allocation to guess, and a non-zero
# default here would hand every employee an entitlement the customer never
# agreed to. They carry no `ENTITLEMENT_MATRIX` rows for the same reason
# (`leaves.category_engine` allocates only what the matrix names), so they are
# offerable from day one and allocate nothing until an administrator decides
# what they are worth.
#
# The tuple shape and every value here is deliberately the same as
# leaves/0005_seed_leave_types_and_holidays.LEAVE_TYPES for the three rows
# they share. test_bootstrap asserts that.
#
# code, name, days, is_paid, half, carry, max_carry, doc, notice, color
LEAVE_TYPES = [
    ("ANNUAL", "Annual Leave", "18.00", True, True, True, "9.00", False, 3, "#3B82F6"),
    ("SICK", "Sick Leave", "12.00", True, True, False, None, True, 0, "#EF4444"),
    ("UNPAID", "Unpaid Leave", "0.00", False, True, False, None, False, 0, "#6B7280"),
    ("SPECIAL", "Special Leave", "0.00", True, True, False, None, False, 0, "#8B5CF6"),
]

# Mirrors inventory/0006_seed_default_categories, which is how NIF got these.
#
# THAT MIGRATION IS WHY THIS LIST EXISTS. It ran once, before tenancy, so its
# ten rows belong to NIF alone -- a tenant provisioned afterwards gets an
# Inventory module with no categories at all, and the asset-registration form
# cannot be submitted without one. Seeding the same ten here means a new
# customer's inventory behaves like the one this system was built against.
#
# name, description
INVENTORY_CATEGORIES = [
    ("Laptop", "Portable computers issued to staff"),
    ("Desktop", "Fixed workstations"),
    ("Monitor", "Displays and screens"),
    ("Printer", "Printers, scanners and multifunction devices"),
    ("Projector", "Projectors and presentation equipment"),
    ("Network Device", "Routers, switches, access points and firewalls"),
    ("Mobile Device", "Phones, tablets and dongles"),
    ("Furniture", "Desks, chairs, cabinets and fittings"),
    ("Accessories", "Chargers, bags, keyboards, mice and cables"),
    ("Other", "Anything not covered by the categories above"),
]

# code, name, start, end, crosses_midnight
SHIFTS = [
    ("general", "General Shift", time(10, 0), time(18, 0), False),
    ("morning", "Morning Shift", time(6, 0), time(14, 0), False),
    ("evening", "Evening Shift", time(14, 0), time(22, 0), False),
]

DEFAULT_POLICY_NAME = "Default Policy"

# Every new behaviour the attendance engine can apply is seeded OFF, exactly as
# attendance/0005 did for NIF: a tenant's first month of attendance must be
# computed by the plainest rules available, so an operator comparing the system
# against a paper register sees the same answer. Overtime and comp-off are
# opt-in through a policy edit.
DEFAULT_POLICY = {
    "description": "Seeded at provisioning. Behaviour-identical to the "
                   "platform defaults; every optional rule is off.",
    "office_start_time": time(10, 0),
    "absent_cutoff_time": time(18, 0),
    "grace_minutes": 0,
    "half_day_hours": Decimal("5.00"),
    "full_day_hours": Decimal("8.00"),
    "deduct_breaks": False,
    "overtime_threshold_hours": None,
    "overtime_min_minutes": 30,
    "comp_off_enabled": False,
}

# The floor a global policy assignment starts from, so re-deriving a
# historical month resolves a real policy instead of falling back to settings.
# Same value and same reason as attendance/0005.
POLICY_EFFECTIVE_FLOOR = date(2000, 1, 1)

# The manual's vocabulary (minutes/0009), which is the set the Minute Type
# dropdown offers. 0002's longer taxonomy is NIF history and is not replayed.
MINUTE_TYPES = [
    ("department", "DEPARTMENT", 1),
    ("branch", "BRANCH", 2),
    ("mancom", "MANCOM", 3),
    ("others", "OTHERS", 4),
]

# Part 4: "Default Task Templates". Three, covering the three shapes a
# template is actually for -- a recurring obligation, an onboarding sequence,
# and a review. Each carries a checklist, because a template with no checklist
# is just a title and teaches nobody what templates are for.
#
# name, title_template, priority, due_in_days, [(section, [items])]
TASK_TEMPLATES = [
    ("Monthly Report", "Monthly report — {month}", "medium", 7, [
        ("Prepare", [
            "Collect the month's figures from each department",
            "Reconcile against last month",
            "Draft the narrative summary",
        ]),
        ("Review and submit", [
            "Circulate the draft for comment",
            "Incorporate comments",
            "Submit to the reviewer",
        ]),
    ]),
    ("Employee Onboarding", "Onboarding — {name}", "high", 14, [
        ("Before the first day", [
            "Create the user account and assign a role",
            "Assign a department and a shift",
            "Prepare the workstation and assets",
        ]),
        ("First week", [
            "Introduce the team and the reporting line",
            "Walk through leave and attendance policy",
            "Confirm emergency contact and personal details",
        ]),
    ]),
    ("Quarterly Review", "Quarterly review — {quarter}", "medium", 21, [
        ("Gather", [
            "Collect completed tasks for the quarter",
            "Collect attendance and leave summaries",
        ]),
        ("Assess", [
            "Record what was achieved against the goals set",
            "Agree the goals for next quarter",
        ]),
    ]),
]

# The ten the appraisal specification names (appraisal/0002). Verbatim: a
# competency catalogue that differs per tenant would make the platform's own
# appraisal reporting incomparable between them for no benefit, and a tenant
# can add or retire any of them.
COMPETENCIES = [
    ("leadership", "Leadership",
     "Setting direction, and taking responsibility for outcomes beyond one's "
     "own work."),
    ("communication", "Communication",
     "Being understood, in writing and in person, by the people who need to "
     "act on it."),
    ("teamwork", "Teamwork",
     "Working with others so the whole is better, including across teams."),
    ("innovation", "Innovation",
     "Finding better ways of doing things, and making the case for them."),
    ("problem_solving", "Problem Solving",
     "Getting to the real cause and dealing with it, rather than the symptom."),
    ("accountability", "Accountability",
     "Owning commitments and outcomes, including when they go wrong."),
    ("initiative", "Initiative",
     "Acting without being asked, within the bounds of the role."),
    ("technical_skills", "Technical Skills",
     "The craft the role actually requires, and keeping it current."),
    ("time_management", "Time Management",
     "Prioritising, and being realistic about what fits."),
    ("attendance_reliability", "Attendance Reliability",
     "Being where colleagues can depend on you being."),
]

# Part 4: "Default Reviewer Rules" and "Default Organization Preferences".
#
# These are written EXPLICITLY rather than left NULL. A NULL inherits the
# platform default, which is the correct behaviour and the wrong experience:
# the operator opens the settings screen, sees every field empty, and cannot
# tell whether the escalation ladder is seven days or not configured at all.
# The values are the platform defaults, so writing them changes nothing except
# that they are now visible and editable.
DEFAULT_SETTINGS = {
    # Attendance, matching DEFAULT_POLICY above so the two cannot disagree.
    "office_start": time(10, 0),
    "absent_cutoff": time(18, 0),
    "full_day_hours": Decimal("8.00"),
    "half_day_hours": Decimal("5.00"),
    # Task escalation / reviewer rules.
    "escalation_supervisor_days": 3,
    "escalation_hr_days": 7,
    "escalation_management_days": 14,
    "review_pending_days": 3,
    # Analytics suppression floor: no department smaller than this gets
    # publishable analytics, or the platform exposes individuals.
    "min_department_sample": 3,
    # Notification defaults.
    "notify_in_app_default": True,
    "notify_email_default": True,
    # Opt-in, as it is platform-wide today.
    "notify_digest_default": False,
}


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------
@transaction.atomic
def bootstrap_organization(organization, *, actor=None, audit=True,
                           request=None):
    """Write every tenant's starting configuration. Idempotent.

    Runs INSIDE ``tenant_context(organization)``, which is what makes the
    plain ``Model.objects.get_or_create`` calls below both scoped (they cannot
    see or collide with another tenant's rows) and stamped (the pre_save
    receiver supplies ``organization`` without this module naming it 40
    times). Under row-level security the same context is what binds
    ``app.current_org``, so the policy's WITH CHECK accepts the inserts.

    :returns: ``{"departments": n, "leave_types": n, ...}`` -- how many rows
        each section CREATED. All zeros on a re-run, which is the signal that
        the tenant was already complete.
    """
    from . import platform_audit

    created = {}
    with tenant_context(organization):
        created["settings"] = _apply_settings(organization)
        created["departments"] = _seed_departments()
        created["leave_types"] = _seed_leave_types()
        created["entitlement_rules"] = _seed_entitlements()
        created["shifts"], created["attendance_policies"] = _seed_attendance()
        created["minute_types"] = _seed_minute_types()
        created["task_templates"] = _seed_task_templates()
        created["competencies"] = _seed_competencies()
        created["inventory_categories"] = _seed_inventory_categories()

    total = sum(created.values())
    logger.info("bootstrapped organization %s: %s (%d rows)",
                organization.slug, created, total)
    if audit:
        platform_audit.record(
            actor, _bootstrap_action(), organization=organization,
            changes=created,
            note=f"Bootstrap created {total} configuration row(s).",
            request=request)
    return created


def _bootstrap_action():
    from .models import PlatformAuditLog

    return PlatformAuditLog.Action.TENANT_BOOTSTRAPPED


def verify_organization(organization):
    """What is MISSING from a tenant's configuration. Empty dict == complete.

    The console's tenant-health check, and the assertion the Part 8
    conformance test makes. Deliberately a separate function from the one that
    writes: "did provisioning work" must not be answered by the code whose
    correctness is in question.
    """
    from appraisal.models import Competency
    from attendance.policy.models import AttendancePolicy, PolicyAssignment, Shift
    from inventory.models import InventoryCategory
    from leaves.models import Department, LeaveType
    from minutes.models import MinuteType
    from tasks.models import TaskTemplate

    gaps = {}
    with tenant_context(organization):
        expectations = [
            ("departments", Department, {code for code, _ in DEPARTMENTS}, "code"),
            ("leave_types", LeaveType,
             {row[0] for row in LEAVE_TYPES}, "code"),
            ("shifts", Shift, {code for code, *_ in SHIFTS}, "code"),
            ("minute_types", MinuteType,
             {code for code, _, _ in MINUTE_TYPES}, "code"),
            ("task_templates", TaskTemplate,
             {name for name, *_ in TASK_TEMPLATES}, "name"),
            ("competencies", Competency,
             {code for code, _, _ in COMPETENCIES}, "code"),
            ("inventory_categories", InventoryCategory,
             {name for name, _ in INVENTORY_CATEGORIES}, "name"),
        ]
        for label, model, wanted, field in expectations:
            present = set(model.objects.values_list(field, flat=True))
            missing = sorted(wanted - present)
            if missing:
                gaps[label] = missing

        if not AttendancePolicy.objects.filter(is_active=True).exists():
            gaps["attendance_policy"] = ["no active policy"]
        elif not PolicyAssignment.objects.filter(scope="global").exists():
            gaps["attendance_policy"] = ["no global assignment"]

        if not hasattr(organization, "settings"):
            gaps["settings"] = ["missing"]
        if not hasattr(organization, "branding"):
            gaps["branding"] = ["missing"]
        if not hasattr(organization, "subscription"):
            gaps["subscription"] = ["missing"]
    return gaps


# ---------------------------------------------------------------------------
# The sections
# ---------------------------------------------------------------------------
def _apply_settings(organization):
    """Fill the tenant's OrganizationSettings defaults, without overwriting.

    Only NULL/blank fields are written, so re-running this never reverts a
    policy an administrator has since changed -- which is the difference
    between a repair action and a reset action.
    """
    from django.core.exceptions import ObjectDoesNotExist

    from .models import OrganizationSettings

    # THROUGH THE CACHED RELATION, not a fresh get_or_create.
    #
    # `provision_organization` creates this row moments earlier, and Django
    # populates `organization.settings` when it does. Fetching a second
    # instance here would update the database and leave the caller holding
    # the stale one -- so `provision_organization` would return an
    # organization whose `.settings` still showed every default as NULL.
    try:
        settings_row = organization.settings
    except ObjectDoesNotExist:
        settings_row = OrganizationSettings.objects.create(
            organization=organization)
    changed = []
    for field, value in DEFAULT_SETTINGS.items():
        if getattr(settings_row, field, None) is None:
            setattr(settings_row, field, value)
            changed.append(field)
    if changed:
        settings_row.save(update_fields=[*changed, "updated_at"])
    return 1 if changed else 0


def _seed_departments():
    from leaves.models import Department

    created = 0
    for code, name in DEPARTMENTS:
        _, made = Department.objects.get_or_create(
            code=code, defaults={"name": name, "is_active": True})
        created += int(made)
    return created


def _seed_leave_types():
    from leaves.models import LeaveType

    created = 0
    for (code, name, days, is_paid, half, carry, max_carry, doc, notice,
         color) in LEAVE_TYPES:
        _, made = LeaveType.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "default_days_per_year": Decimal(days),
                "is_paid": is_paid,
                "allow_half_day": half,
                "allow_carry_forward": carry,
                "max_carry_forward_days": (
                    Decimal(max_carry) if max_carry else None),
                "requires_document": doc,
                "min_notice_days": notice,
                "is_active": True,
                "display_color": color,
            })
        created += int(made)
    return created


def _seed_entitlements():
    """Write the entitlement matrix for the leave types this tenant has.

    Without these rows every employee's annual allocation is zero, so leave
    can be applied for and approved but never counted -- which is the subtler
    half of the Phase S5 failure. ``seed_entitlement_matrix`` skips a category
    row whose leave type does not exist, so a tenant with three leave types
    gets rules for exactly those three.
    """
    from leaves import category_engine

    stats = category_engine.seed_entitlement_matrix()
    return stats.get("created", 0)


def _seed_attendance():
    """The three shifts, the default policy, and the global assignment.

    All three or the module does not work: a policy with no shift has no
    expected start time, and a policy with no global assignment is never
    resolved for anybody.
    """
    from attendance.policy.models import (AttendancePolicy, PolicyAssignment,
                                          Shift)

    shifts = {}
    shifts_created = 0
    for code, name, start, end, crosses in SHIFTS:
        shift, made = Shift.objects.get_or_create(
            code=code,
            defaults={"name": name, "start_time": start, "end_time": end,
                      "grace_minutes": 0, "break_minutes": 0,
                      "crosses_midnight": crosses, "is_active": True})
        shifts[code] = shift
        shifts_created += int(made)

    policy, policy_made = AttendancePolicy.objects.get_or_create(
        name=DEFAULT_POLICY_NAME,
        defaults={**DEFAULT_POLICY, "is_active": True,
                  "default_shift": shifts["general"]})

    assignment_made = False
    if not PolicyAssignment.objects.filter(scope="global").exists():
        PolicyAssignment.objects.create(
            policy=policy, scope="global", department=None, user=None,
            effective_from=POLICY_EFFECTIVE_FLOOR, effective_until=None)
        assignment_made = True

    return shifts_created, int(policy_made) + int(assignment_made)


def _seed_minute_types():
    from minutes.models import MinuteType

    created = 0
    for code, name, ordering in MINUTE_TYPES:
        _, made = MinuteType.objects.get_or_create(
            code=code,
            defaults={"name": name, "ordering": ordering, "is_active": True})
        created += int(made)
    return created


def _seed_task_templates():
    from tasks.models import TaskTemplate, TaskTemplateGroup, TaskTemplateItem

    created = 0
    for name, title, priority, due_days, sections in TASK_TEMPLATES:
        template, made = TaskTemplate.objects.get_or_create(
            name=name,
            defaults={"title_template": title, "priority": priority,
                      "default_due_in_days": due_days, "is_active": True,
                      "description": "Seeded at provisioning. Edit or retire "
                                     "it freely."})
        created += int(made)
        if not made:
            # The checklist belongs to the template. Re-seeding it onto a
            # template an administrator has edited would restore lines they
            # deleted on purpose.
            continue
        for position, (section_title, items) in enumerate(sections):
            group = TaskTemplateGroup.objects.create(
                template=template, title=section_title, position=position)
            created += 1
            for item_position, text in enumerate(items):
                TaskTemplateItem.objects.create(
                    template=template, group=group, text=text,
                    position=item_position)
                created += 1
    return created


def _seed_inventory_categories():
    """The asset categories, matched on NAME because that is the unique key.

    `InventoryCategory` has no code column: its constraint is
    (organization, name), so `get_or_create(name=...)` is both the idempotent
    form and the one that cannot collide with another tenant's "Laptop".
    """
    from inventory.models import InventoryCategory

    created = 0
    for name, description in INVENTORY_CATEGORIES:
        _, made = InventoryCategory.objects.get_or_create(
            name=name, defaults={"description": description})
        created += int(made)
    return created


def _seed_competencies():
    from appraisal.models import Competency

    created = 0
    for position, (code, name, description) in enumerate(COMPETENCIES, start=1):
        _, made = Competency.objects.get_or_create(
            code=code,
            defaults={"name": name, "description": description,
                      "ordering": position * 10, "is_active": True})
        created += int(made)
    return created
