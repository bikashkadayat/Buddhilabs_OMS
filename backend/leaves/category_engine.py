"""
Experience-based leave-category engine.

Resolves every user to exactly one leave category (A/B/C/D/PROBATION) from their
employment_type + continuous service (date_of_joining -> today, Nepal time), then
generates category-driven LeaveBalance rows from the DB-configurable
EntitlementRule matrix. Never returns NULL: unmapped/edge cases fall back to the
lowest applicable tier and set a human-readable flag for HR review.

Kept as a separate module (not services.py) so the policy logic is easy to find,
test, and reconfigure. All balance writes go through the race-safe helpers in
services (sync_simple_balance), so the atomic guarantees from Cluster A hold.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    CompensatoryLedger,
    EntitlementRule,
    LeaveBalance,
    LeaveType,
)

User = get_user_model()

ZERO = Decimal("0.00")

# Service thresholds, in whole months of continuous service.
PROBATION_MONTHS = 3      # < 3 mo  => probation floor
ONE_YEAR_MONTHS = 12
THREE_YEAR_MONTHS = 36

# ---------------------------------------------------------------------------
# DB-seeded entitlement matrix (source of truth lives in EntitlementRule rows;
# this literal is used ONLY to seed/reset them). Tuple = (leave_type_code, days,
# is_working_day_based, applicable[, by_arrangement]). Compensatory is applicable
# but 0-allocated for the categories that HAVE it, because it is EARNED into
# CompensatoryLedger rather than granted yearly; for Intern and Volunteer it is
# applicable=False, which is the policy's "Not Applicable".
# ---------------------------------------------------------------------------
ENTITLEMENT_MATRIX = {
    "A": [  # Permanent > 3 yrs
        ("ANNUAL", 12, True, True), ("SICK", 12, True, True),
        ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
        ("COMPENSATORY", 0, False, True),
    ],
    "B": [  # Permanent 1–3 yrs
        ("ANNUAL", 10, True, True), ("SICK", 10, True, True),
        ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
        ("COMPENSATORY", 0, False, True),
    ],
    "C": [  # Post-Probation / Permanent < 1 yr
        ("ANNUAL", 8, True, True), ("SICK", 10, True, True),
        ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
        ("COMPENSATORY", 0, False, True),
    ],
    "D": [  # Intern
        ("ANNUAL", 4, True, True), ("SICK", 5, True, True),
        # "As per organization policy": offered, but with no fixed yearly
        # allocation — see EntitlementRule.by_arrangement.
        ("MATERNITY", 0, False, True, True), ("PATERNITY", 0, False, True, True),
        ("COMPENSATORY", 0, False, False),
    ],
    "E": [  # Volunteer
        ("ANNUAL", 2, True, True), ("SICK", 3, True, True),
        ("MATERNITY", 0, False, False), ("PATERNITY", 0, False, False),
        ("COMPENSATORY", 0, False, False),
    ],
    "PROBATION": [  # < 3 mo — configurable floor (Annual 0, Sick pro-rated 5)
        ("ANNUAL", 0, True, True), ("SICK", 5, True, True),
        ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
        ("COMPENSATORY", 0, False, True),
    ],
}

# LeaveType codes that map onto a simple dashboard LeaveBalance row. Compensatory
# is excluded — it is surfaced from the ledger, not a fixed allocation.
BALANCE_LEAVE_CODES = ("ANNUAL", "SICK", "MATERNITY", "PATERNITY")


# ---------------------------------------------------------------------------
# Service + category resolution
# ---------------------------------------------------------------------------
def nepal_today():
    """Current date in Asia/Kathmandu (settings.TIME_ZONE)."""
    return timezone.localdate()


def service_months(date_of_joining, today=None):
    """Whole months of continuous service, or None if no joining date."""
    if not date_of_joining:
        return None
    today = today or nepal_today()
    months = (today.year - date_of_joining.year) * 12 + (today.month - date_of_joining.month)
    if today.day < date_of_joining.day:
        months -= 1
    return max(0, months)


def service_label(date_of_joining, today=None):
    """Human string like 'Permanent · 3 yrs 2 mo' component ('3 yrs 2 mo')."""
    months = service_months(date_of_joining, today)
    if months is None:
        return "—"
    yrs, mos = divmod(months, 12)
    parts = []
    if yrs:
        parts.append(f"{yrs} yr{'s' if yrs != 1 else ''}")
    parts.append(f"{mos} mo")
    return " ".join(parts)


def resolve_category(employment_type, months):
    """
    Pure resolver -> (category, flag). `flag` is None on a clean match, else a
    note for HR. Order matters (mirrors the spec's numbered rule set). Never NULL.
    """
    ET = User.EmploymentType
    LC = User.LeaveCategory

    # 1. Engagement type decides outright for the two non-employee tiers, and
    #    service length never promotes them — a volunteer of ten years is still
    #    a volunteer. These were a single branch returning D for both, which
    #    paid volunteers an intern's entitlement.
    if employment_type == ET.INTERN:
        return LC.D, None
    if employment_type == ET.VOLUNTEER:
        return LC.E, None

    # No joining date => cannot measure service; apply the floor and flag.
    if months is None:
        return LC.PROBATION, "No date of joining recorded; defaulted to probation floor — HR to confirm."

    if employment_type == ET.PERMANENT:
        if months > THREE_YEAR_MONTHS:
            return LC.A, None
        if months > ONE_YEAR_MONTHS:
            return LC.B, None
        return LC.C, None  # 4. Permanent < 1 yr fallback

    if employment_type == ET.POST_PROBATION:
        if months > ONE_YEAR_MONTHS:
            return LC.B, "Post-Probation over 1 yr — auto-treated as Permanent (Category B); convert employment type to Permanent."
        if months >= PROBATION_MONTHS:
            return LC.C, None
        return LC.PROBATION, "Post-Probation under 3 mo — inconsistent label; HR to confirm."

    if employment_type == ET.PROBATION:
        if months >= PROBATION_MONTHS:
            return LC.C, "Probation completed 3 months — promote employment type to Post-Probation."
        return LC.PROBATION, "In probation (under 3 mo) — probation floor applies."

    # 8. Nothing matched: lowest tier + flag.
    return LC.PROBATION, "No category rule matched; lowest tier applied — HR review required."


def resolve_and_cache(user, today=None, save=True):
    """Resolve the user's category from current data, cache it on the row, and
    return (category, flag). Called on create/update/login/rollover so the cached
    value auto-promotes as service crosses the 3-month / 1-year / 3-year marks."""
    months = service_months(user.date_of_joining, today)
    category, flag = resolve_category(user.employment_type, months)
    changed = user.leave_category != category or (user.category_flag or None) != (flag or None)
    user.leave_category = category
    user.category_flag = flag
    if save and changed:
        user.save(update_fields=["leave_category", "category_flag"])
    return category, flag


def default_eligibility(gender):
    """Auto-default (maternity_eligible, paternity_eligible) from gender."""
    return (gender == User.Gender.FEMALE, gender == User.Gender.MALE)


# ---------------------------------------------------------------------------
# Entitlement + balance generation
# ---------------------------------------------------------------------------
def _code_applies_to_user(user, code, rule):
    """Category + eligibility gate for a single leave code."""
    if not rule.applicable:
        return False
    # Maternity/Paternity: always hidden for Category D; otherwise gated on the
    # per-user eligibility flag (which HR can override).
    # Whether a category offers maternity/paternity at all is `rule.applicable`,
    # already checked above. This used to read `!= LeaveCategory.D`, which meant
    # every new category had to be remembered here as well as in the matrix —
    # and Category E, added later, would silently have inherited maternity and
    # paternity leave that the policy does not give volunteers.
    if code == "MATERNITY":
        return user.maternity_eligible
    if code == "PATERNITY":
        return user.paternity_eligible
    return True


def entitlements_for_user(user):
    """
    The applicable (leave_type, rule) rows for a user's cached category, after the
    eligibility gate. Resolves+caches the category first if missing.
    """
    if not user.leave_category:
        resolve_and_cache(user)
    rules = (
        EntitlementRule.objects
        .filter(category=user.leave_category, applicable=True)
        .select_related("leave_type")
    )
    out = []
    for rule in rules:
        code = rule.leave_type.code.upper()
        if _code_applies_to_user(user, code, rule):
            out.append(rule)
    return out


@transaction.atomic
def ensure_category_balances(user, year):
    """
    Generate/refresh this user's simple LeaveBalance rows for `year` from the
    entitlement matrix for their (freshly resolved) category. Idempotent:
    total_allocated is updated to the current entitlement; used_so_far is left to
    the derived, race-safe sync_simple_balance (Cluster A). Compensatory is not a
    fixed allocation and is skipped here (surfaced from the ledger).
    """
    from . import services  # avoid import cycle at module load

    resolve_and_cache(user)
    created = []
    for rule in entitlements_for_user(user):
        code = rule.leave_type.code.upper()
        if code not in BALANCE_LEAVE_CODES:
            continue
        # No fixed allocation means no balance row: a 0-day row renders as
        # "0 of 0 remaining" and tells an intern they have no maternity leave,
        # when the policy grants it by arrangement. The entitlement is still
        # returned by entitlements_for_user, so the UI can show it as such.
        if rule.by_arrangement:
            continue
        simple_code = code.lower()
        balance, _ = LeaveBalance.objects.get_or_create(
            user=user, leave_type=simple_code, year=year,
            defaults={"total_allocated": int(rule.entitlement_days), "used_so_far": 0},
        )
        if balance.total_allocated != int(rule.entitlement_days):
            balance.total_allocated = int(rule.entitlement_days)
            balance.save(update_fields=["total_allocated"])
        # Re-derive used_so_far from day records (keeps dashboard consistent).
        services.sync_simple_balance(user, simple_code, year)
        created.append(balance)
    return created



def seed_entitlement_matrix(*, reset=False):
    """
    Write ENTITLEMENT_MATRIX above into EntitlementRule rows.

    The matrix literal spent its life as a comment: the only code that ever
    wrote these rows was migration 0013, which carries its own frozen copy. Two
    copies of a policy table with nothing comparing them is how an organisation
    ends up granting entitlements no document describes, so the literal is now
    the thing HR can re-apply, and a test asserts the database still agrees
    with it.

    Idempotent. Rows are created when missing and corrected when they differ
    from policy; `reset` is accepted for callers that want the intent stated
    explicitly at the call site.

    :returns: {"created": n, "corrected": n, "unchanged": n}
    """
    stats = {"created": 0, "corrected": 0, "unchanged": 0}
    for category, rows in ENTITLEMENT_MATRIX.items():
        for row in rows:
            code, days, working_day_based, applicable = row[:4]
            by_arrangement = row[4] if len(row) > 4 else False
            leave_type = LeaveType.objects.filter(code=code).first()
            if leave_type is None:
                continue
            wanted = {
                "entitlement_days": Decimal(str(days)),
                "is_working_day_based": working_day_based,
                "applicable": applicable,
                "by_arrangement": by_arrangement,
            }
            rule = EntitlementRule.objects.filter(
                category=category, leave_type=leave_type).first()
            if rule is None:
                EntitlementRule.objects.create(
                    category=category, leave_type=leave_type, **wanted)
                stats["created"] += 1
            elif any(getattr(rule, k) != v for k, v in wanted.items()):
                for k, v in wanted.items():
                    setattr(rule, k, v)
                rule.save(update_fields=list(wanted))
                stats["corrected"] += 1
            else:
                stats["unchanged"] += 1
    return stats

# ---------------------------------------------------------------------------
# Compensatory ledger
# ---------------------------------------------------------------------------
def comp_summary(user):
    """{'earned','used','available','pending'} confirmed comp days for a user."""
    rows = CompensatoryLedger.objects.filter(user=user)
    earned = rows.filter(
        entry_type=CompensatoryLedger.EntryType.EARN,
        status=CompensatoryLedger.Status.CONFIRMED,
    ).aggregate(s=Sum("days"))["s"] or ZERO
    used = rows.filter(
        entry_type=CompensatoryLedger.EntryType.USE,
    ).aggregate(s=Sum("days"))["s"] or ZERO
    pending = rows.filter(
        entry_type=CompensatoryLedger.EntryType.EARN,
        status=CompensatoryLedger.Status.PENDING,
    ).aggregate(s=Sum("days"))["s"] or ZERO
    return {
        "earned": earned, "used": used,
        "available": earned - used, "pending": pending,
    }


def comp_available(user):
    return comp_summary(user)["available"]


# ---------------------------------------------------------------------------
# Apply-time validation (Phase 7) + comp usage reconciliation
# ---------------------------------------------------------------------------
def applicable_type_codes(user):
    """Uppercase LeaveType codes this user's category may apply for (after the
    maternity/paternity eligibility gate). Compensatory included only if the user
    has available comp days."""
    codes = []
    for rule in entitlements_for_user(user):
        code = rule.leave_type.code.upper()
        if code == "COMPENSATORY" and comp_available(user) <= ZERO:
            continue
        codes.append(code)
    return codes


def check_apply(user, code, start, end):
    """
    Validate a leave application against the user's category entitlement.
    Returns an error string, or None if the request is allowed. Counts working
    days for working-day-based types, calendar days otherwise; compensatory is
    validated against the earned ledger balance.
    """
    from . import services

    code = (code or "").lower()
    leave_type = LeaveType.objects.filter(code__iexact=code).first()
    if leave_type is None:
        return None  # legacy/unknown type: no category guard (backward compatible)

    if not user.leave_category:
        resolve_and_cache(user)
    rule = EntitlementRule.objects.filter(
        category=user.leave_category, leave_type=leave_type,
    ).first()
    if rule is None or not rule.applicable:
        label = user.get_leave_category_display() if user.leave_category else "your category"
        return f"{leave_type.name} is not available for {label}."

    ucode = code.upper()
    if ucode == "MATERNITY" and (user.leave_category == User.LeaveCategory.D or not user.maternity_eligible):
        return "Maternity leave is not available for your profile."
    if ucode == "PATERNITY" and (user.leave_category == User.LeaveCategory.D or not user.paternity_eligible):
        return "Paternity leave is not available for your profile."

    working_days = int(services.calculate_working_days(start, end))
    calendar_days = (end - start).days + 1
    requested = working_days if rule.is_working_day_based else calendar_days

    if ucode == "COMPENSATORY":
        available = comp_available(user)
        if requested > available:
            return (f"Requested {requested} compensatory day(s) exceed your earned "
                    f"balance ({available:g} available).")
        return None

    balance = LeaveBalance.objects.filter(user=user, leave_type=code, year=start.year).first()
    remaining = (balance.total_allocated - balance.used_so_far) if balance else 0
    if balance and requested > remaining:
        unit = "working " if rule.is_working_day_based else ""
        return (f"Requested {requested} {unit}day(s) exceed your remaining "
                f"{leave_type.name} balance ({remaining:g} day(s) left).")
    return None


def reconcile_comp_usage(leave):
    """
    Keep the comp ledger's USE entry in sync with a compensatory leave's status.
    Approved comp leave consumes earned days (one confirmed USE entry); any other
    status (rejected/cancelled/soft-deleted) releases them. Idempotent.
    """
    if (getattr(leave, "leave_type", "") or "").lower() != "compensatory":
        return
    from . import services

    is_active = (leave.status == "approved") and not getattr(leave, "is_deleted", False)
    if is_active:
        days = int(services.calculate_working_days(leave.start_date, leave.end_date))
        CompensatoryLedger.objects.update_or_create(
            leave=leave, entry_type=CompensatoryLedger.EntryType.USE,
            defaults={
                "user": leave.user, "days": days,
                "source": CompensatoryLedger.Source.LEAVE_USE,
                "status": CompensatoryLedger.Status.CONFIRMED,
            },
        )
    else:
        CompensatoryLedger.objects.filter(
            leave=leave, entry_type=CompensatoryLedger.EntryType.USE,
        ).delete()
