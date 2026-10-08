"""Phase S7 Parts 6, 7 and 8: what a new administrator sees first.

THE CHECKLIST IS MEASURED, NOT REMEMBERED.
Every step that can be answered from the tenant's own data is answered from
it, every time -- so a customer who uploads a logo sees that step tick without
anything having to record the act, and a customer who deletes their logo sees
it untick. A checklist stored as five booleans drifts away from reality the
first time somebody undoes something, and then it is lying to the person who
trusts it most: somebody in their first hour on the platform.

Two steps have no data to measure, and they are the honest exceptions:

  * "Invite your team" -- sending an invitation leaves no row in this
    codebase, and counting users would make it a duplicate of "add
    employees".
  * "Review departments" -- the bootstrap creates five, so there is nothing
    for the customer to create. The step exists to prompt a look, and a
    customer who looks and is happy has to be able to say so.

Both are tickable, and the tick is stored in ``OrganizationSettings``.

PART 6 AND PART 7 ARE TWO LISTS AND THEY ARE NOT THE SAME LIST.
Part 6 is "what already works" -- the reassurance that the workspace is not
empty, which is what the whole of S6's bootstrap exists to provide. Part 7 is
"what to do next". Collapsing them would produce a single list of eleven items
where the five already-done ones look like work.
"""
import logging

from django.utils import timezone

from .context import tenant_context

logger = logging.getLogger(__name__)

# Part 7's five steps, in the order the brief lists them.
#
# EVERY ONE OF THEM IS MEASURABLE, which is a change worth recording. An
# earlier version of this list had two steps with nothing to measure --
# "review your departments" (five are seeded, so there is nothing a customer
# must create) and "invite your team" (sending an invitation leaves no row) --
# and they could only be ticked by hand. This brief names five that all leave
# evidence behind, so the tick-by-hand path is gone and the checklist cannot
# disagree with the workspace.
STEPS = [
    # The Getting Started order: what a new administrator does, in the order
    # each step makes the next one worth doing. EVERY STEP IS MEASURED from
    # the workspace's own rows -- a tick by hand is only an override.
    {
        "key": "logo",
        "title": "Upload your logo",
        "detail": "It appears on your sign-in page, your documents and your "
                  "email.",
        "action": "/settings/branding",
    },
    {
        "key": "team",
        "title": "Add your employees",
        "detail": "Everything else — leave, attendance, tasks — is about "
                  "them.",
        "action": "/admin/users",
    },
    {
        "key": "attendance",
        "title": "Configure attendance",
        "detail": "A general shift and a default policy are ready. Set the "
                  "office hours, grace period and half-day rules — or choose "
                  "how attendance is taken and connect a biometric device "
                  "under Settings → Attendance & biometric devices.",
        "action": "/admin/attendance/policies",
    },
    {
        "key": "department",
        "title": "Set up your departments",
        "detail": "Give each department a head, so leave and corrections "
                  "reach the right person.",
        "action": "/admin/leaves/departments",
    },
    {
        "key": "invite",
        "title": "Invite your team",
        "detail": "Send people their sign-in details. This ticks when someone "
                  "besides you has signed in.",
        "action": "/admin/users",
    },
    {
        "key": "first_task",
        "title": "Create your first task",
        "detail": "Raise one from a template to see the workflow end to end.",
        "action": "/tasks/create",
    },
    {
        "key": "approve_leave",
        "title": "Approve your first leave",
        "detail": "When someone applies, approve it from Leave → Review "
                  "requests. You'll see exactly what your staff see.",
        "action": "/leave/pending",
    },
    {
        "key": "leave_policy",
        "title": "Review your leave policy",
        "detail": "Annual, sick, unpaid and special leave are configured. "
                  "Set the days to match your own staff policy.",
        "action": "/admin/leaves/leave-types",
    },
]


def _sets_up_the_workspace(user):
    """Only an administrator configures the workspace.

    FOUND BY LOOKING AT AN EMPLOYEE'S HOME PAGE. The checklist -- upload the
    logo, review the leave policy, the attendance rules -- was shown to every
    member of the organization, with "Mark done" buttons that worked: an
    employee could dismiss the setup checklist for the whole workspace, or
    tick "leave policy reviewed" on the administrator's behalf. Neither is
    theirs to do, and the first thing a new teacher saw on signing in was a
    list of jobs that belonged to the principal.
    """
    return getattr(user, "role", None) == "admin"


def state(user):
    """The whole first-login payload for this user's own organization.

    Scoped by the USER, not by an id in the request: an administrator can
    only ask about the workspace they are signed in to, because there is no
    parameter with which to ask about another.
    """
    organization = getattr(user, "organization", None)
    if organization is None:
        # A platform operator has no workspace to be onboarded into.
        return {"applicable": False,
                "detail": "This account does not belong to a workspace."}
    if not _sets_up_the_workspace(user):
        return {"applicable": False,
                "detail": "Workspace setup is for administrators."}

    from . import bootstrap

    settings_row = getattr(organization, "settings", None)
    ticked = dict(getattr(settings_row, "onboarding_steps", None) or {})
    measured = _measure(organization)
    gaps = bootstrap.verify_organization(organization)
    subscription = getattr(organization, "subscription", None)

    steps = []
    for step in STEPS:
        # MEASURED FIRST, and a stored tick only as an override.
        #
        # Every one of this brief's five steps leaves evidence, so the
        # measurement is the answer. The stored tick survives because a
        # customer may legitimately want a step out of their way -- they use
        # a single shift and do not care about attendance rules -- and a
        # checklist that cannot be satisfied is one people learn to ignore.
        # It can only ever ADD a tick, never remove one, so it cannot
        # contradict the workspace.
        done = bool(measured.get(step["key"], False)
                    or ticked.get(step["key"]))
        steps.append({**step, "done": done,
                      # Kept in the payload because the console uses it to
                      # decide whether to offer a "mark done" control.
                      "measured": bool(measured.get(step["key"], False))})
    completed = sum(1 for step in steps if step["done"])

    return {
        "applicable": True,
        # Part 6: the welcome wizard, and whether to show it at all.
        "show_wizard": _should_show(settings_row, completed, len(steps)),
        "dismissed_at": getattr(settings_row, "onboarding_dismissed_at", None),
        "organization": {
            "name": organization.name,
            "slug": organization.slug,
            "status": organization.status,
            "status_display": organization.get_status_display(),
            "industry": organization.industry,
            "country": organization.country,
            "document_prefix": organization.document_prefix,
            "created_at": organization.created_at,
            # The full address, not the slug. "Your workspace is at abc" told
            # a new administrator nothing they could bookmark or pass on.
            "login_url": _login_url(organization),
        },
        "subscription": {
            "status": organization.subscription_status,
            "plan": getattr(getattr(subscription, "plan", None), "code", None),
            "trial_ends_on": getattr(subscription, "trial_end", None),
            "period_ends_on": organization.subscription_expiry,
            "days_remaining": (subscription.days_until_expiry()
                               if subscription else None),
            "is_trial": organization.subscription_status == "trial",
        },
        # Part 6: "what already works", which is the reassurance that the
        # workspace is not empty. Derived from the SAME verifier the platform
        # console uses, so the customer and the operator are reading one
        # answer rather than two that might differ.
        "ready": _ready_list(gaps, measured, organization=organization,
                             user=user),
        # Part 8: the verdict, in the words the console uses.
        "health": {
            "verdict": "Tenant Ready" if not gaps else "Missing Configuration",
            "configuration_gaps": gaps,
        },
        # Part 7: what to do next, and how far along they are.
        "checklist": steps,
        "progress": {
            "completed": completed,
            "total": len(steps),
            "percent": round(100 * completed / len(steps)) if steps else 0,
        },
    }


def _measure(organization):
    """All five steps, answered from the tenant's own rows.

    MEASURED ON EVERY READ, never remembered. A customer who uploads a logo
    sees that step tick without anything recording the act, and one who
    deletes it sees it untick. A checklist stored as five booleans starts
    lying the first time somebody undoes something -- to the person least
    able to tell, in their first hour on the platform.

    Each measurement asks "has the customer been here", not "is this
    configured", because provisioning already configured all of it. For the
    two review steps that means comparing against what the bootstrap seeded:
    a value that still equals the default has not been reviewed.
    """
    from decimal import Decimal

    from attendance.policy.models import AttendancePolicy, Shift
    from leaves.models import LeaveType
    from tasks.models import Task
    from users.models import User

    from . import bootstrap

    branding = getattr(organization, "branding", None)
    seeded_days = {row[0]: Decimal(row[2]) for row in bootstrap.LEAVE_TYPES}
    seeded_shifts = {code: (start, end) for code, _, start, end, _
                     in bootstrap.SHIFTS}

    with tenant_context(organization):
        # More than one, because provisioning creates the administrator --
        # counting them would tick "invite your team" before the customer had
        # invited anybody.
        team = User.objects.filter(is_active=True).count() > 1

        # Compared PER CODE. Comparing the set of day counts would read
        # "sick leave changed from 12 to 18" as unchanged, 18 being annual
        # leave's seeded value.
        leave_reviewed = False
        for leave_type in LeaveType.objects.all():
            default = seeded_days.get(leave_type.code)
            if default is None or leave_type.default_days_per_year != default:
                leave_reviewed = True
                break

        # Attendance: a shift whose hours were changed or which the customer
        # added, or a policy that is not the seeded one, or the seeded
        # policy's own fields edited.
        attendance_reviewed = False
        for shift in Shift.objects.all():
            seeded = seeded_shifts.get(shift.code)
            if seeded is None or (shift.start_time, shift.end_time) != seeded:
                attendance_reviewed = True
                break
        if not attendance_reviewed:
            policies = list(AttendancePolicy.objects.all())
            if len(policies) > 1 or any(
                    policy.name != bootstrap.DEFAULT_POLICY_NAME
                    for policy in policies):
                attendance_reviewed = True
            else:
                for policy in policies:
                    for field, value in bootstrap.DEFAULT_POLICY.items():
                        if getattr(policy, field, value) != value:
                            attendance_reviewed = True
                            break

        # A customer whose attendance setup IS the gate terminal -- or who
        # chose app-only / biometric-only deliberately -- has configured
        # attendance as surely as one who edited the shift. Before the
        # legacy device integration this step could never tick for them.
        if not attendance_reviewed:
            from biometric.models import BiometricDevice

            attendance_reviewed = (
                BiometricDevice.objects.exists()
                or bool(getattr(getattr(organization, "settings", None),
                                "attendance_mode", "")))

        first_task = Task.objects.exists()

        from leaves.models import Department, Leave

        # A department with a head: the bootstrap seeds departments but
        # cannot know who runs them, and routing needs that.
        department = Department.objects.filter(head__isnull=False).exists()
        # Somebody other than the person reading this has signed in -- the
        # only evidence that "invite your team" actually happened.
        invited = User.objects.filter(is_active=True, last_login__isnull=False) \
            .count() > 1
        approved_leave = Leave.objects.filter(status=Leave.Status.APPROVED).exists()

    return {
        "logo": bool(getattr(branding, "logo_primary", None)
                     or getattr(branding, "logo_login", None)
                     or getattr(organization, "logo", None)),
        "team": team,
        "leave_policy": leave_reviewed,
        "attendance": attendance_reviewed,
        "first_task": first_task,
        "department": department,
        "invite": invited,
        "approve_leave": approved_leave,
    }


def _login_url(organization):
    """Where this workspace signs in. Never raises: it is a nicety."""
    try:
        from .handover import login_url

        return login_url(organization)
    except Exception:                              # noqa: BLE001
        logger.debug("could not resolve login URL for onboarding",
                     exc_info=True)
        return None


def _ready_list(gaps, measured, *, organization=None, user=None):
    """Part 6's "already works" list, in the order the brief names it.

    These are not achievements the customer earned -- provisioning did them --
    and saying so is the point. The first screen of a new SaaS is usually
    empty; this one is not, and a new administrator has no way of knowing that
    unless somebody tells them.

    READ FROM THE SAME VERIFIER THE PLATFORM CONSOLE USES, so the customer and
    the operator are looking at one answer rather than two that can differ.
    """
    return [
        # First, because it is the news. Always true by the time anybody can
        # read it -- which is the point of saying it.
        {"key": "organization", "label": "Organization created",
         "ready": True},
        {"key": "departments", "label": "Departments ready",
         "ready": "departments" not in gaps},
        {"key": "leave", "label": "Leave management ready",
         "ready": not {"leave_types", "entitlement_rules"} & set(gaps)},
        {"key": "attendance", "label": "Attendance ready",
         "ready": not {"attendance_policy", "shifts"} & set(gaps)},
        {"key": "tasks", "label": "Task management ready",
         "ready": "task_templates" not in gaps},
        {"key": "minutes", "label": "Minutes ready",
         "ready": "minute_types" not in gaps},
        {"key": "inventory", "label": "Inventory ready",
         "ready": "inventory_categories" not in gaps},
        {"key": "appraisal", "label": "Appraisals ready",
         "ready": "competencies" not in gaps},
        # Documents need no seed rows: memos, circulars and letters number
        # themselves from the organization's prefix. Ready exactly when
        # there is a prefix to number them with.
        # Reports read the workspace's own data and need no setup.
        {"key": "reports", "label": "Reports ready", "ready": True},
        {"key": "documents", "label": "Documents ready",
         "ready": bool(getattr(organization, "document_prefix", "")
                       if organization is not None else True)},
        # Measured, like everything else here. A first administrator was
        # given a temporary password and made to replace it before they
        # could reach this page, so this ticks the brief's "change your
        # password" step with evidence rather than a reminder.
        {"key": "administrator", "label": "Your own password is set",
         "ready": not getattr(user, "must_change_password", False)},
    ]


def _should_show(settings_row, completed, total):
    """Show the wizard until it is dismissed or the list is finished.

    Not "show it once": somebody who signs in, is interrupted, and comes back
    tomorrow should find it where they left it. Once every step is done it
    stops appearing on its own, so nobody has to dismiss a list of ticks.
    """
    if settings_row is None:
        return True
    if getattr(settings_row, "onboarding_dismissed_at", None):
        return False
    return completed < total


def update(user, *, dismissed=None, step_done=None):
    """Dismiss the wizard, or tick a step the customer wants out of the way."""
    organization = getattr(user, "organization", None)
    if organization is None:
        return {"applicable": False}
    if not _sets_up_the_workspace(user):
        # Refused, not ignored: the view turns this into a 403.
        raise PermissionError("Only an administrator can change workspace setup.")

    from .models import OrganizationSettings

    settings_row, _ = OrganizationSettings.objects.get_or_create(
        organization=organization)
    fields = []

    if dismissed is not None:
        settings_row.onboarding_dismissed_at = (
            timezone.now() if dismissed else None)
        fields.append("onboarding_dismissed_at")

    if step_done:
        known = {step["key"] for step in STEPS}
        if step_done not in known:
            return {"applicable": True,
                    "error": f"'{step_done}' is not a checklist step."}
        steps = dict(settings_row.onboarding_steps or {})
        steps[step_done] = True
        settings_row.onboarding_steps = steps
        fields.append("onboarding_steps")

    if fields:
        settings_row.save(update_fields=fields + ["updated_at"])
    return state(user)
