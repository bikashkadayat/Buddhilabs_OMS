"""
Phase 4 - Enterprise Leave Records business logic.

Design principle: LeaveDayRecord is the single source of truth. Every balance
and every weekly/monthly summary is *derived* from it and can be recomputed at
any time, so all recompute_* functions are idempotent.
"""
import calendar as _calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from .models import (
    Department,
    EnterpriseLeaveBalance,
    Holiday,
    Leave,
    LeaveDayRecord,
    LeavePolicy,
    LeaveType,
    MonthlyLeaveSummary,
    WeeklyLeaveSummary,
)
from tenancy.stamping import stamp_all

User = get_user_model()

ZERO = Decimal("0.00")
WEEKEND_WEEKDAYS = {5}  # Saturday only — Nepal's weekly holiday. Sunday is a working day. (weekday(): Mon=0 .. Sat=5 .. Sun=6)

# Day-record statuses that hold balance. A leave in the two-stage flow writes its
# Leave.status verbatim onto its day records (see sync_leave_day_records), so a
# record can carry "pending_hr" even though LeaveDayRecord.Status has no such
# member. Both in-flight statuses reserve balance; only APPROVED consumes it, but
# for availability BOTH must be subtracted (mirrors EnterpriseLeaveBalance.available_days).
PENDING_DAY_STATUSES = ("pending", "pending_hr")
# "Committed" = anything not yet released (rejected/cancelled): reserved or consumed.
COMMITTED_DAY_STATUSES = ("approved", "pending", "pending_hr")


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------
# Map the semantic event strings this module uses onto the shared AuditLog
# action enum (the audit app already owns the immutable table).
_ACTION_MAP = {
    "LEAVE_DAY_RECORDS_GENERATED": AuditLog.Action.CREATE,
    "LEAVE_STATUS_SYNCED": AuditLog.Action.UPDATE,
    "LEAVE_BALANCE_RECOMPUTED": AuditLog.Action.UPDATE,
    "LEAVE_BALANCE_ADJUSTED": AuditLog.Action.UPDATE,
    "LEAVE_POLICY_APPLIED": AuditLog.Action.CREATE,
    "YEAR_END_CARRY_FORWARD": AuditLog.Action.UPDATE,
}


def audit_log(actor, action, target=None, metadata=None, request=None):
    """
    Central audit helper for the leaves module. `action` is a semantic event
    string; it is mapped to the shared AuditLog enum and preserved verbatim in
    the metadata so nothing is lost. IP / user-agent are extracted from request.
    """
    metadata = dict(metadata or {})
    metadata.setdefault("event", action)
    return log_action(
        actor,
        _ACTION_MAP.get(action, AuditLog.Action.OTHER),
        instance=target,
        changes=metadata,
        request=request,
    )


# ---------------------------------------------------------------------------
# Working-day / holiday helpers
# ---------------------------------------------------------------------------
def _active_holiday_dates(start_date, end_date):
    return set(
        Holiday.objects.filter(
            is_active=True, date__gte=start_date, date__lte=end_date
        ).values_list("date", flat=True)
    )


def calculate_working_days(start_date, end_date, exclude_weekends=True):
    """
    Count working days in [start_date, end_date] inclusive, skipping weekends
    (Sat/Sun) and active public holidays. Returns a Decimal.
    """
    if end_date < start_date:
        return ZERO
    holidays = _active_holiday_dates(start_date, end_date)
    total = ZERO
    current = start_date
    while current <= end_date:
        is_weekend = current.weekday() in WEEKEND_WEEKDAYS
        is_holiday = current in holidays
        if not (exclude_weekends and is_weekend) and not is_holiday:
            total += Decimal("1.0")
        current += timedelta(days=1)
    return total


def resolve_leave_type(leave_request):
    """
    Map a legacy Leave.leave_type string (e.g. "annual") to the CMS LeaveType
    (code "ANNUAL"). Falls back to name match, then any active type.
    """
    raw = (leave_request.leave_type or "").strip()
    lt = LeaveType.objects.filter(code__iexact=raw).first()
    if lt is None:
        lt = LeaveType.objects.filter(name__iexact=raw).first()
    return lt


# ---------------------------------------------------------------------------
# Day-record generation & sync
# ---------------------------------------------------------------------------
@transaction.atomic
def generate_leave_day_records(leave_request, day_portion=LeaveDayRecord.DayPortion.FULL):
    """
    Create one LeaveDayRecord per calendar day in the leave's range. Weekend and
    holiday days are still recorded but flagged (is_weekend / is_holiday) so that
    aggregations can exclude them while the per-day history stays complete.

    `day_portion` applies to every generated day, which lets a caller express a
    string of half-days (e.g. two FIRST_HALF days => two 0.5 records).
    Idempotent: existing records for the request are cleared and rebuilt.
    """
    leave_type = resolve_leave_type(leave_request)
    if leave_type is None:
        raise ValueError(f"No LeaveType matches '{leave_request.leave_type}'.")

    # Rebuild from scratch so re-invocation is safe (dates may have changed).
    leave_request.day_records.all().delete()

    holidays = _active_holiday_dates(leave_request.start_date, leave_request.end_date)
    records = []
    current = leave_request.start_date
    while current <= leave_request.end_date:
        iso = current.isocalendar()
        records.append(LeaveDayRecord(
            leave_request=leave_request,
            user=leave_request.user,
            date=current,
            day_portion=day_portion,
            leave_type=leave_type,
            status=leave_request.status,
            is_weekend=current.weekday() in WEEKEND_WEEKDAYS,
            is_holiday=current in holidays,
            week_number=iso.week,
            month=current.month,
            year=current.year,
        ))
        current += timedelta(days=1)

    LeaveDayRecord.objects.bulk_create(stamp_all(records))
    # NOTE: day-record generation is a recomputable derivation, not a user
    # decision, so it is intentionally not audit-logged (keeps the audit trail
    # focused on real actions: policy changes, carry-forward, approvals).
    return records


@transaction.atomic
def sync_leave_day_records(leave_request):
    """
    Propagate a leave request's current status to all of its day records, then
    trigger balance/summary recomputes for every period the records touch.
    Called on status transitions (approved / rejected / cancelled).
    """
    records = list(leave_request.day_records.all())
    if not records:
        return

    leave_type = records[0].leave_type
    LeaveDayRecord.objects.filter(leave_request=leave_request).update(status=leave_request.status)

    user = leave_request.user
    years = {r.year for r in records}
    weeks = {(r.year, r.week_number) for r in records}
    months = {(r.year, r.month) for r in records}

    for year in years:
        recompute_leave_balance(user, leave_type, year)
        # Keep the simple dashboard balance in lock-step with the enterprise one.
        sync_simple_balance(user, leave_request.leave_type, year)
    # Compensatory leave consumes earned ledger days, not a fixed allocation.
    from . import category_engine
    category_engine.reconcile_comp_usage(leave_request)
    for year, week in weeks:
        recompute_weekly_summary(user, year, week)
    for year, month in months:
        recompute_monthly_summary(user, year, month)


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------
def _user_department(user):
    """Prefer the structured FK; fall back to matching the legacy string."""
    dept = getattr(user, "department_ref", None)
    if dept is not None:
        return dept
    legacy = getattr(user, "department", None)
    if legacy:
        return Department.objects.filter(
            Q(code__iexact=legacy) | Q(name__iexact=legacy)
        ).first()
    return None


def resolve_leave_policy(user, leave_type, on_date):
    """
    Pick the most specific applicable LeavePolicy for a user on a date.

    Specificity order (highest wins):
        department + role  >  department only  >  role only  >  org-wide
    Ties break on the latest effective_from. A policy whose department or role
    is set but does not match the user is discarded. Returns a LeavePolicy or
    None (caller then uses LeaveType.default_days_per_year).
    """
    dept = _user_department(user)
    dept_id = dept.id if dept else None
    role = getattr(user, "role", None)

    candidates = LeavePolicy.objects.filter(
        leave_type=leave_type, effective_from__lte=on_date
    ).filter(Q(effective_until__isnull=True) | Q(effective_until__gte=on_date))

    best = None
    best_key = None
    for policy in candidates:
        if policy.department_id and policy.department_id != dept_id:
            continue
        if policy.role and policy.role != role:
            continue
        score = (2 if policy.department_id else 0) + (1 if policy.role else 0)
        key = (score, policy.effective_from)
        if best_key is None or key > best_key:
            best, best_key = policy, key
    return best


# ---------------------------------------------------------------------------
# Balance / summary recomputes (idempotent)
# ---------------------------------------------------------------------------
def _working_records(user, leave_type, **filters):
    """Approved-or-pending working-day records (weekend/holiday excluded)."""
    return LeaveDayRecord.objects.filter(
        user=user, leave_type=leave_type, is_weekend=False, is_holiday=False, **filters
    )


def _sum_portion(records):
    total = ZERO
    for rec in records:
        total += rec.portion_days
    return total


@transaction.atomic
def recompute_leave_balance(user, leave_type, year):
    """
    Rebuild a user's EnterpriseLeaveBalance for a leave type / year from the
    day records. entitled_days comes from the applicable policy (or the leave
    type default). carried_forward / encashed / forfeited are preserved because
    they are set by explicit processes (year-end, encashment), not derived here.
    """
    existing = EnterpriseLeaveBalance.objects.filter(
        user=user, leave_type=leave_type, year=year
    ).first()
    if existing is not None and existing.locked_at is not None:
        # Year-end froze this balance; never silently overwrite it.
        return existing

    policy = resolve_leave_policy(user, leave_type, date(year, 12, 31))
    entitled = policy.days_per_year if policy else leave_type.default_days_per_year

    used = _sum_portion(_working_records(
        user, leave_type, year=year, status=LeaveDayRecord.Status.APPROVED
    ))
    # Count BOTH pending stages (pending = awaiting Dept Head, pending_hr =
    # awaiting HR) so an in-flight Level-2 leave still reserves balance.
    pending = _sum_portion(_working_records(
        user, leave_type, year=year, status__in=PENDING_DAY_STATUSES
    ))

    balance, _ = EnterpriseLeaveBalance.objects.get_or_create(
        user=user, leave_type=leave_type, year=year,
    )
    balance.entitled_days = entitled
    balance.used_days = used
    balance.pending_days = pending
    balance.last_recomputed_at = timezone.now()
    balance.save(update_fields=[
        "entitled_days", "used_days", "pending_days", "last_recomputed_at",
    ])
    return balance


def _by_type_breakdown(records):
    breakdown = {}
    for rec in records:
        code = rec.leave_type.code
        breakdown[code] = str(Decimal(breakdown.get(code, "0")) + rec.portion_days)
    return breakdown


def _attendance(working_days, approved_days):
    if working_days <= 0:
        return Decimal("100.00")
    pct = (Decimal(working_days) - approved_days) / Decimal(working_days) * Decimal("100")
    return max(ZERO, min(Decimal("100.00"), pct.quantize(Decimal("0.01"))))


@transaction.atomic
def recompute_weekly_summary(user, year, week_number):
    """Rebuild a WeeklyLeaveSummary from that ISO week's day records."""
    records = list(LeaveDayRecord.objects.filter(
        user=user, year=year, week_number=week_number
    ).select_related("leave_type"))

    if records:
        any_date = records[0].date
        week_start = any_date - timedelta(days=any_date.weekday())
    else:
        week_start = date.fromisocalendar(year, week_number, 1)
    week_end = week_start + timedelta(days=6)

    working = [r for r in records if r.is_working_day]
    approved = _sum_portion([r for r in working if r.status == LeaveDayRecord.Status.APPROVED])
    pending = _sum_portion([r for r in working if r.status in PENDING_DAY_STATUSES])
    rejected = _sum_portion([r for r in working if r.status == LeaveDayRecord.Status.REJECTED])
    working_days = int(calculate_working_days(week_start, week_end))

    summary, _ = WeeklyLeaveSummary.objects.get_or_create(
        user=user, year=year, week_number=week_number,
        defaults={"week_start_date": week_start, "week_end_date": week_end},
    )
    summary.week_start_date = week_start
    summary.week_end_date = week_end
    summary.total_leave_days = approved + pending
    summary.by_type = _by_type_breakdown(
        [r for r in working if r.status == LeaveDayRecord.Status.APPROVED or r.status in PENDING_DAY_STATUSES]
    )
    summary.approved_days = approved
    summary.pending_days = pending
    summary.rejected_days = rejected
    summary.working_days = working_days
    summary.attendance_percentage = _attendance(working_days, approved)
    summary.last_recomputed_at = timezone.now()
    summary.save()
    return summary


@transaction.atomic
def recompute_monthly_summary(user, year, month):
    """Rebuild a MonthlyLeaveSummary from that month's day records."""
    records = list(LeaveDayRecord.objects.filter(
        user=user, year=year, month=month
    ).select_related("leave_type"))

    month_start = date(year, month, 1)
    month_end = date(year, month, _calendar.monthrange(year, month)[1])

    working = [r for r in records if r.is_working_day]
    approved = _sum_portion([r for r in working if r.status == LeaveDayRecord.Status.APPROVED])
    pending = _sum_portion([r for r in working if r.status in PENDING_DAY_STATUSES])
    working_days = int(calculate_working_days(month_start, month_end))

    summary, _ = MonthlyLeaveSummary.objects.get_or_create(
        user=user, year=year, month=month,
    )
    summary.total_leave_days = approved + pending
    summary.by_type = _by_type_breakdown(
        [r for r in working if r.status == LeaveDayRecord.Status.APPROVED or r.status in PENDING_DAY_STATUSES]
    )
    summary.approved_days = approved
    summary.pending_days = pending
    summary.working_days = working_days
    summary.attendance_percentage = _attendance(working_days, approved)
    summary.last_recomputed_at = timezone.now()
    summary.save()
    return summary


@transaction.atomic
def soft_delete_leave(leave):
    """
    Soft-delete a leave that cannot be hard-deleted (it has approved day
    records). Flags the leave, cancels its day records, and frees the balance
    by recomputing every period the records touched.
    """
    leave.is_deleted = True
    leave.deleted_at = timezone.now()
    leave.save(update_fields=["is_deleted", "deleted_at"])

    records = list(leave.day_records.all())
    LeaveDayRecord.objects.filter(leave_request=leave).update(
        status=LeaveDayRecord.Status.CANCELLED
    )
    if records:
        leave_type = records[0].leave_type
        for year in {r.year for r in records}:
            recompute_leave_balance(leave.user, leave_type, year)
            # Refund the simple dashboard balance too (H3): cancelled records
            # leave the committed set, so used_so_far drops on recompute.
            sync_simple_balance(leave.user, leave.leave_type, year)
        for year, week in {(r.year, r.week_number) for r in records}:
            recompute_weekly_summary(leave.user, year, week)
        for year, month in {(r.year, r.month) for r in records}:
            recompute_monthly_summary(leave.user, year, month)
    return leave


def recompute_weekly_summary_by_id(user_id, year, week_number):
    """Convenience wrapper used by batch commands (fetches the user by id)."""
    user = User.objects.get(pk=user_id)
    return recompute_weekly_summary(user, year, week_number)


def recompute_monthly_summary_by_id(user_id, year, month):
    user = User.objects.get(pk=user_id)
    return recompute_monthly_summary(user, year, month)


# ---------------------------------------------------------------------------
# Year-end carry-forward
# ---------------------------------------------------------------------------
@transaction.atomic
def process_year_end_carry_forward(user, year, actor=None):
    """
    For every carry-forward-eligible leave type, roll a user's unused balance
    into next year (capped at max_carry_forward_days) and record the forfeited
    remainder on the closing year. Idempotent per (user, type, year).
    """
    actor = actor or user
    for leave_type in LeaveType.objects.filter(allow_carry_forward=True, is_active=True):
        balance = recompute_leave_balance(user, leave_type, year)
        unused = balance.entitled_days + balance.carried_forward_days - balance.used_days
        if unused < ZERO:
            unused = ZERO

        cap = leave_type.max_carry_forward_days
        carry = min(unused, cap) if cap is not None else unused
        forfeit = unused - carry

        balance.forfeited_days = forfeit
        balance.save(update_fields=["forfeited_days"])

        next_balance, _ = EnterpriseLeaveBalance.objects.get_or_create(
            user=user, leave_type=leave_type, year=year + 1,
        )
        next_balance.carried_forward_days = carry
        next_balance.save(update_fields=["carried_forward_days"])
        recompute_leave_balance(user, leave_type, year + 1)

        audit_log(
            actor, "YEAR_END_CARRY_FORWARD", target=next_balance,
            metadata={
                "leave_type": leave_type.code, "year": year,
                "carried": str(carry), "forfeited": str(forfeit),
            },
        )


# ---------------------------------------------------------------------------
# Admin policy management
# ---------------------------------------------------------------------------
@transaction.atomic
def adjust_leave_balance(user, leave_type, year, delta, actor, reason, request=None):
    """
    Apply a manual HR adjustment (bonus or deduction) to a balance. The delta is
    accumulated on adjustment_days, which recompute preserves, and the change is
    audit-logged with the mandatory reason.
    """
    # Ensure entitled/used/pending reflect current policy + records first, so the
    # returned balance shows the full picture (unless the year is locked).
    recompute_leave_balance(user, leave_type, year)
    balance = EnterpriseLeaveBalance.objects.select_for_update().get(
        user=user, leave_type=leave_type, year=year,
    )
    before = balance.adjustment_days
    balance.adjustment_days = before + Decimal(str(delta))
    balance.save(update_fields=["adjustment_days"])

    audit_log(
        actor, "LEAVE_BALANCE_ADJUSTED", target=balance,
        metadata={
            "user": str(user.id), "leave_type": leave_type.code, "year": year,
            "delta": str(delta), "reason": reason,
            "adjustment_before": str(before), "adjustment_after": str(balance.adjustment_days),
        },
        request=request,
    )
    return balance


@transaction.atomic
def bulk_leave_action(leave_ids, action, actor, comment="", request=None):
    """
    Approve or reject many Leave applications in one transaction. Returns a
    per-item result list; the signal layer regenerates day records / balances
    on each status change.
    """
    new_status = Leave.Status.APPROVED if action == "approve" else Leave.Status.REJECTED
    audit_action = AuditLog.Action.APPROVE if action == "approve" else AuditLog.Action.REJECT

    results = []
    for leave_id in leave_ids:
        leave = Leave.objects.filter(pk=leave_id, is_deleted=False).first()
        if leave is None:
            results.append({"id": str(leave_id), "ok": False, "error": "not found"})
            continue
        if leave.status not in (Leave.Status.PENDING,):
            results.append({"id": str(leave_id), "ok": False, "error": f"not pending (is {leave.status})"})
            continue
        leave.status = new_status
        leave.approver = actor
        leave.save()  # signal syncs day records + recomputes
        audit_log(actor, "LEAVE_BULK_" + action.upper(), target=leave,
                  metadata={"comment": comment}, request=request)
        log_action(actor, audit_action, instance=leave, changes={"bulk": True, "comment": comment}, request=request)
        results.append({"id": str(leave_id), "ok": True, "status": new_status})
    return results


@transaction.atomic
def apply_leave_policy(department, role, leave_type, days, effective_from, actor, effective_until=None):
    """Create a new LeavePolicy (admin action) and audit it."""
    policy = LeavePolicy.objects.create(
        leave_type=leave_type,
        department=department,
        role=role or None,
        days_per_year=Decimal(str(days)),
        effective_from=effective_from,
        effective_until=effective_until,
        created_by=actor,
    )
    audit_log(
        actor, "LEAVE_POLICY_APPLIED", target=policy,
        metadata={
            "leave_type": leave_type.code,
            "department": department.code if department else None,
            "role": role or "ALL",
            "days": str(days),
        },
    )
    return policy


# ---------------------------------------------------------------------------
# Approval routing
# ---------------------------------------------------------------------------
def department_has_dept_head(applicant):
    """True if the applicant's department has at least one active Department Head
    (role=checker) other than the applicant. Used to decide whether HR/Admin may
    step in as a fallback grantor so a request can never get stuck with no one to
    approve it. Scoped strictly to the applicant's department (no global fallback:
    a global 'any Dept Head' would wrongly deny the HR/Admin fallback)."""
    from users.models import User

    qs = User.objects.filter(role=User.Roles.CHECKER, is_active=True).exclude(id=applicant.id)
    if applicant.department_ref_id:
        return qs.filter(department_ref_id=applicant.department_ref_id).exists()
    if applicant.department:
        return qs.filter(department__iexact=applicant.department).exists()
    # Applicant has no department set: any active Dept Head can own the review.
    return qs.exists()


# ---------------------------------------------------------------------------
# Simple-model leave balances (dashboard) - entitlements sourced from the CMS
# LeaveType (configurable by HR/Admin), keyed by the lowercase LeaveBalance codes.
# ---------------------------------------------------------------------------
ENTITLEMENT_CODES = ["annual", "sick", "casual"]


def entitlement_for(code):
    """Configurable yearly entitlement for a simple leave code, read from the
    CMS LeaveType.default_days_per_year (not hardcoded)."""
    lt = LeaveType.objects.filter(code__iexact=code).first()
    return int(lt.default_days_per_year) if lt else 0


def ensure_leave_balances(user, year):
    """Create this user's LeaveBalance rows for `year` from the entitlement
    policy (idempotent). Called on user creation, balance reads, and yearly reset."""
    from .models import LeaveBalance

    created = []
    for code in ENTITLEMENT_CODES:
        obj, was_created = LeaveBalance.objects.get_or_create(
            user=user, leave_type=code, year=year,
            defaults={"total_allocated": entitlement_for(code), "used_so_far": 0},
        )
        if was_created:
            created.append(obj)
    return created


def working_leave_days(leave):
    """Working days for a leave request, skipping Saturdays + public holidays."""
    return int(calculate_working_days(leave.start_date, leave.end_date))


@transaction.atomic
def sync_simple_balance(user, code, year):
    """
    Recompute the simple dashboard LeaveBalance.used_so_far for one
    (user, leave code, year) from the LeaveDayRecord source of truth, so the
    dashboard/serializer can never drift from the enterprise balance.

    `used_so_far` holds *committed* working days = approved + both pending stages
    (weekends & holidays excluded). This means:
      * remaining (= total_allocated - used_so_far) already reserves in-flight
        leave, so a user cannot over-apply (H4);
      * cancelling/rejecting an approved leave frees the balance automatically,
        because its day records leave the committed set (H3);
      * the value is derived, never incremented, so it cannot double-deduct (M3)
        and cannot exceed the allocation once the apply-time guard holds (H4 cap).

    Race-safe: the balance row is locked with select_for_update for the life of
    the enclosing transaction (M4). No-op for codes without a simple balance.
    """
    from .models import EntitlementRule, LeaveBalance

    code = (code or "").lower()
    if not code:
        return None
    leave_type = LeaveType.objects.filter(code__iexact=code).first()
    if leave_type is None:
        return None

    LeaveBalance.objects.get_or_create(
        user=user, leave_type=code, year=year,
        defaults={"total_allocated": entitlement_for(code), "used_so_far": 0},
    )
    balance = LeaveBalance.objects.select_for_update().get(
        user=user, leave_type=code, year=year,
    )

    # Annual/Sick are counted in working days (Sat + holidays excluded); fixed
    # entitlements like Maternity/Paternity are calendar-day based, so their
    # weekend/holiday records still consume the allocation.
    rule = EntitlementRule.objects.filter(leave_type=leave_type).first()
    working_day_based = rule.is_working_day_based if rule is not None else True

    qs = LeaveDayRecord.objects.filter(
        user=user, leave_type=leave_type, year=year, status__in=COMMITTED_DAY_STATUSES,
    )
    if working_day_based:
        qs = qs.filter(is_weekend=False, is_holiday=False)
    committed = _sum_portion(qs)

    balance.used_so_far = float(committed)
    balance.save(update_fields=["used_so_far"])
    return balance


def sync_simple_balance_by_id(user_id, code, year):
    """Convenience wrapper for batch jobs / migrations (fetches the user by id)."""
    user = User.objects.get(pk=user_id)
    return sync_simple_balance(user, code, year)
