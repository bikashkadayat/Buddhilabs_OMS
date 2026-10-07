"""Which policy and shift apply to an employee on a given day.

    User assignment -> Department assignment -> Global assignment -> settings

Two properties this module guarantees:

* **It never returns None.** With no rows at all — a fresh database, a
  transactional test that flushed the seed, a deleted policy — resolution falls
  through to an unsaved policy built from the ``ATTENDANCE_*`` settings, whose
  values are exactly today's behaviour. Nothing raises, nothing changes.
* **Department assignments do not inherit up the tree.** ``leaves.Department``
  has a self-FK ``parent``, but walking it would make resolution O(depth)
  inside a per-day loop and produce action at a distance. A sub-department with
  no assignment of its own falls straight through to Global.
"""
import contextvars
from collections import namedtuple
from contextlib import contextmanager
from datetime import date as _date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings as django_settings
from django.db.models import Q

from .models import AttendancePolicy, EmployeeShift, PolicyAssignment, Shift

# ``effective_from`` is carried alongside the policy because comp-off must not
# be earned retroactively for days before the assignment existed: enabling
# comp-off would otherwise mint months of leave out of historical Saturdays.
PolicyBinding = namedtuple("PolicyBinding", ["policy", "effective_from"])

# Resolution is memoised only inside an explicit ``policy_cache()`` block, so a
# cached value can never outlive the operation that asked for it. Long-running
# workers therefore cannot serve a stale policy after an admin edits one.
_cache_var = contextvars.ContextVar("attendance_policy_cache", default=None)


@contextmanager
def policy_cache():
    """Memoise policy/shift resolution for the duration of the block.

    Wrap loops that resolve the same employee repeatedly — a 31-day calendar, a
    month-long report, a re-derivation sweep — to turn N queries into 1.
    """
    token = _cache_var.set({})
    try:
        yield
    finally:
        _cache_var.reset(token)


def _cached(key, produce):
    cache = _cache_var.get()
    if cache is None:
        return produce()
    if key not in cache:
        cache[key] = produce()
    return cache[key]


def settings_fallback_policy():
    """An unsaved policy mirroring the ``ATTENDANCE_*`` settings.

    This is the bottom of the resolution chain and the reason Phase 8 cannot
    regress behaviour: with no policy rows, every computed status is identical
    to the pre-Phase-8 result.
    """
    from attendance import services

    policy = AttendancePolicy(
        name="Settings fallback",
        office_start_time=services.office_start_time(),
        absent_cutoff_time=services.absent_cutoff_time(),
        half_day_hours=services.half_day_hours(),
        full_day_hours=services.full_day_hours(),
        grace_minutes=0,
        deduct_breaks=False,
        overtime_threshold_hours=None,   # overtime off
        comp_off_enabled=False,          # comp-off off
        # Explicitly NULL, overriding the 11:45/13:00 field defaults. The
        # fallback's whole job is to reproduce the ATTENDANCE_* settings, and
        # those describe the strict office-start rule — not the NIF arrival
        # window, which is a policy decision that belongs in a policy row.
        late_after_time=None,
        half_day_after_time=None,
    )
    # The pk field defaults to uuid4, so an unsaved instance still carries one.
    # Clearing it is load-bearing: `Attendance.applied_policy` would otherwise
    # be set to an id with no row behind it, and the FK would fail on save.
    policy.pk = None
    policy.is_fallback = True
    return policy


def _covers(row, day):
    return (row.effective_from <= day
            and (row.effective_until is None or row.effective_until >= day))


def _candidate_assignments(user):
    """Every assignment that could ever apply to this user, newest first.

    Fetched per USER, not per user-and-day, and filtered by date in Python.
    Keying the cache on the day would be useless for the workloads that matter:
    a month-long re-derivation visits each (user, day) exactly once, so no key
    would ever be hit twice. Per-user, a 30-day sweep costs one query instead of
    ninety.

    The row count is inherently tiny — one open assignment per scope, plus
    however many historical ones an admin has retired.
    """
    return list(PolicyAssignment.objects
                .filter(policy__is_active=True)
                .filter(Q(scope=PolicyAssignment.Scope.GLOBAL)
                        | Q(scope=PolicyAssignment.Scope.USER, user=user)
                        | Q(scope=PolicyAssignment.Scope.DEPARTMENT,
                            department_id=getattr(user, "department_ref_id", None)))
                .select_related("policy", "policy__default_shift")
                .order_by("-effective_from"))


def resolve_binding(user, day):
    """The policy in force for ``user`` on ``day``, plus when it took effect."""
    rows = _cached(("assignments", getattr(user, "pk", None)),
                   lambda: _candidate_assignments(user))

    for scope in PolicyAssignment.PRIORITY:          # user -> department -> global
        for row in rows:                             # already newest-first
            if row.scope == scope and _covers(row, day):
                return PolicyBinding(row.policy, row.effective_from)
    return PolicyBinding(settings_fallback_policy(), None)


def resolve_policy(user, day):
    """The policy in force for ``user`` on ``day``. Never None."""
    return resolve_binding(user, day).policy


def _candidate_shifts(user):
    """Same per-user caching rationale as ``_candidate_assignments``."""
    return list(EmployeeShift.objects
                .filter(user=user, shift__is_active=True)
                .select_related("shift")
                .order_by("-effective_from"))


def resolve_shift(user, day, policy=None):
    """The shift in force for ``user`` on ``day``, or None.

    None is a valid answer: the policy's own ``office_start_time`` is then used
    directly, which is how every employee behaves today.
    """
    policy = policy or resolve_policy(user, day)
    rows = _cached(("shifts", getattr(user, "pk", None)),
                   lambda: _candidate_shifts(user))
    for row in rows:                                  # newest effective_from first
        if _covers(row, day):
            return row.shift
    default = None if policy.is_fallback else policy.default_shift
    return default if (default and default.is_active) else None


def start_time_and_grace(policy, shift):
    """Lateness reference point. A shift overrides the policy's office start."""
    if shift is not None:
        return shift.start_time, shift.grace_minutes
    return policy.office_start_time, policy.grace_minutes


def _minutes(t):
    return t.hour * 60 + t.minute + (t.second / 60 if t.second else 0)


def _offset(t, minutes):
    """``t`` moved by ``minutes``, clamped to the same calendar day.

    Clamping matters for a late-starting shift: a 22:00 shift would push a
    13:00 boundary past midnight, and a wrapped time would silently make every
    arrival "early". 23:59 is the honest answer — every arrival that day is
    within the window.
    """
    moved = datetime.combine(_date.min, t) + timedelta(minutes=minutes)
    if moved.date() != _date.min:
        return time(23, 59, 59) if minutes > 0 else time(0, 0)
    return moved.time()


def _shift_delta(policy, shift):
    """How far a shift moves the day relative to the policy's office start.

    The policy states its boundaries as wall-clock times anchored to
    ``office_start_time``. An employee on a different shift gets the same
    *window*, moved: with an office start of 10:00 and "late after 11:45", the
    14:00 evening shift becomes "late after 15:45". Absolute boundaries would
    instead mark every evening-shift arrival as a Half Day.
    """
    if shift is None:
        return 0
    return _minutes(shift.start_time) - _minutes(policy.office_start_time)


def status_boundaries(policy, shift):
    """``(late_after, half_day_after)`` as local wall-clock times.

    ``half_day_after`` may be None, which disables the arrival-based half-day
    rule; the duration-based one in the engine still applies.

    When ``late_after_time`` is empty the legacy path is used instead —
    ``start_time + grace_minutes``, taken from the shift if one applies. When it
    is set, the shift's own ``grace_minutes`` is deliberately ignored: the
    window is already encoded in the boundary, and stacking two grace periods
    would double-count.
    """
    delta = _shift_delta(policy, shift)

    if policy.late_after_time is None:
        start, grace = start_time_and_grace(policy, shift)
        late_at = _offset(start, grace)
    else:
        late_at = _offset(policy.late_after_time, delta)

    half_at = None
    if policy.half_day_after_time is not None:
        half_at = _offset(policy.half_day_after_time, delta)
    return late_at, half_at


def overtime_threshold(policy):
    """Hours after which work counts as overtime, or None when disabled."""
    value = policy.overtime_threshold_hours
    return Decimal(str(value)) if value is not None else None


def seed_values():
    """The values ``attendance/0006`` writes into the Global policy.

    Kept next to the fallback so the two can never drift: the seeded policy and
    the settings fallback must describe the same behaviour.
    """
    from attendance import services

    return {
        "name": "Global Policy",
        "description": "Seeded from ATTENDANCE_* settings; behaviour-identical to Phase 7.",
        "office_start_time": services.office_start_time(),
        "absent_cutoff_time": services.absent_cutoff_time(),
        "half_day_hours": services.half_day_hours(),
        "full_day_hours": services.full_day_hours(),
        "grace_minutes": 0,
        "deduct_breaks": False,
        "overtime_threshold_hours": None,
        "comp_off_enabled": False,
    }


def default_shift_seeds():
    """General/morning/evening. ``general`` matches today's 10:00 office start."""
    return [
        {"code": "general", "name": "General Shift",
         "start_time": _time(django_settings.ATTENDANCE_OFFICE_START, 10),
         "end_time": _time(django_settings.ATTENDANCE_ABSENT_CUTOFF, 18)},
        {"code": "morning", "name": "Morning Shift",
         "start_time": _time("06:00", 6), "end_time": _time("14:00", 14)},
        {"code": "evening", "name": "Evening Shift",
         "start_time": _time("14:00", 14), "end_time": _time("22:00", 22)},
    ]


def _time(raw, default_hour):
    from attendance.services import _parse_time

    return _parse_time(raw, default_hour, 0)


__all__ = [
    "AttendancePolicy", "EmployeeShift", "PolicyAssignment", "PolicyBinding", "Shift",
    "default_shift_seeds", "overtime_threshold", "policy_cache", "resolve_binding",
    "resolve_policy", "resolve_shift", "seed_values", "settings_fallback_policy",
    "start_time_and_grace", "status_boundaries",
]
