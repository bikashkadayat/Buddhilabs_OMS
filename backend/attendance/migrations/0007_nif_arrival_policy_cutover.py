"""Cut over to the NIF arrival-based rules on a date, not retroactively.

The rule change itself is small — Present until 11:45, Late until 13:00, Half
Day after. Applying it is not, because ``PolicyAssignment`` is date-effective
and the seeded Global assignment starts in 2000: writing the new times onto that
row would mean the next ``rederive_attendance`` over any past month restated it,
silently turning months of recorded Late days into Present.

So this migration does a cutover instead of an edit:

1. Pins the existing ``Global Policy`` to its current behaviour by setting both
   new boundaries to NULL — the legacy ``office_start_time + grace_minutes``
   path, which stays fully supported.
2. Closes that policy's open assignment the day before go-live.
3. Creates ``NIF Attendance Policy`` (identical in every other respect) with
   11:45 / 13:00, assigned globally from go-live onward.

Result: history re-derives byte-identically, and every day from go-live uses
the new rules. Existing attendance rows are never touched by this migration —
their stored status stands until something re-derives them.

Go-live defaults to the date the migration runs. Set ATTENDANCE_POLICY_GO_LIVE
to pin staging and production to the same date.
"""
from datetime import date, time, timedelta

from django.db import migrations

LEGACY_NAME = "Global Policy"
NIF_NAME = "NIF Attendance Policy"
LATE_AFTER = time(11, 45)
HALF_DAY_AFTER = time(13, 0)

# Copied verbatim onto the new policy so the cutover changes the arrival rules
# and nothing else.
CARRIED_OVER = [
    "office_start_time", "grace_minutes", "absent_cutoff_time",
    "half_day_hours", "full_day_hours", "deduct_breaks",
    "overtime_threshold_hours", "overtime_min_minutes",
    "comp_off_enabled", "comp_off_on_saturday", "comp_off_on_holiday",
    "comp_off_min_hours", "comp_off_half_day_hours", "comp_off_full_day_hours",
    "default_shift_id",
]


def go_live_date():
    from django.conf import settings
    from django.utils import timezone

    raw = (getattr(settings, "ATTENDANCE_POLICY_GO_LIVE", "") or "").strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return timezone.localdate()


def cutover(apps, schema_editor):
    AttendancePolicy = apps.get_model("attendance", "AttendancePolicy")
    PolicyAssignment = apps.get_model("attendance", "PolicyAssignment")

    legacy = AttendancePolicy.objects.filter(name=LEGACY_NAME).first()
    if legacy is None:
        # 0005 was faked or the policy was renamed; nothing to cut over from.
        return
    if AttendancePolicy.objects.filter(name=NIF_NAME).exists():
        return  # already cut over

    # 1. Pin the old policy's behaviour explicitly rather than relying on the
    #    field defaults, which are the NEW rules.
    legacy.late_after_time = None
    legacy.half_day_after_time = None
    legacy.save(update_fields=["late_after_time", "half_day_after_time"])

    go_live = go_live_date()

    # 2. Close the old assignment first: only one open global assignment may
    #    exist at a time (uniq_open_global_policy_assignment).
    old = PolicyAssignment.objects.filter(
        scope="global", policy=legacy, effective_until__isnull=True).first()
    if old is not None:
        # Never let the window invert on a same-day go-live.
        old.effective_until = max(go_live - timedelta(days=1), old.effective_from)
        old.save(update_fields=["effective_until"])

    # 3. Open the new one.
    nif = AttendancePolicy.objects.create(
        name=NIF_NAME,
        description=(f"NIF arrival rules from {go_live}: Present until "
                     f"{LATE_AFTER:%H:%M}, Late until {HALF_DAY_AFTER:%H:%M}, "
                     f"then Half Day."),
        is_active=True,
        late_after_time=LATE_AFTER,
        half_day_after_time=HALF_DAY_AFTER,
        **{field: getattr(legacy, field) for field in CARRIED_OVER},
    )
    PolicyAssignment.objects.create(
        policy=nif, scope="global", department=None, user=None,
        effective_from=go_live, effective_until=None)


def uncutover(apps, schema_editor):
    """Hand the open global assignment back to the legacy policy."""
    AttendancePolicy = apps.get_model("attendance", "AttendancePolicy")
    PolicyAssignment = apps.get_model("attendance", "PolicyAssignment")

    PolicyAssignment.objects.filter(scope="global", policy__name=NIF_NAME).delete()
    AttendancePolicy.objects.filter(name=NIF_NAME).delete()

    legacy = AttendancePolicy.objects.filter(name=LEGACY_NAME).first()
    if legacy is None:
        return
    assignment = (PolicyAssignment.objects
                  .filter(scope="global", policy=legacy)
                  .order_by("-effective_from").first())
    if assignment is not None:
        assignment.effective_until = None
        assignment.save(update_fields=["effective_until"])


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0006_arrival_status_boundaries"),
    ]

    operations = [
        migrations.RunPython(cutover, uncutover),
    ]
