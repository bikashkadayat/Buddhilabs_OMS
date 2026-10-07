# The import, at your device's scale — what to expect

Rehearsed against a simulated terminal holding **32 users** and **11,520
attendance records**, matching what your device reports. Real command output,
unedited.

## The one thing that will surprise you

**It takes two passes, and the first one shows zero Attendance rows.** That is
correct, not a failure.

On the first pass the roster and the punches arrive together, so every punch
lands against an enrolment nobody has mapped yet. The punches are *stored* — 
nothing is lost — but they belong to no employee, so no attendance derives.

Then HR maps the 32 people. **And nothing changes.** Mapping deliberately does
not re-attribute history: `services.map_employee` keeps the two apart because
re-attributing history is the operation that can silently hand one person's
attendance to another. There is no error, no warning, and the dashboards stay
empty.

**Re-running `golive_import` is what closes that gap.** Its backfill step claims
the already-imported punches for their now-mapped employees, bounded to the
window you named — bounded, because an unbounded claim is exactly what could
give a leaver's attendance to their replacement.

Watch the numbers move between the two passes:

| | Pass 1 | Pass 2 |
|---|---|---|
| Total imported users | 32 | 32 |
| Total imported punches | 1,344 | 1,344 |
| Total AttendancePunch rows | 1,344 | 1,344 |
| **Total Attendance rows** | **0** | **672** |
| **Mapped employees** | **0** | **32** |
| **Unmapped employees** | **32** | **0** |

672 = 21 working days × 32 employees. Saturdays excluded, per the NIF calendar.

Note also that pass 2 reports `created 0, duplicate 1344` — the import is
idempotent, so re-running it is free and safe. If it dies halfway, run it again.

(1,344 of 11,520 records fall inside 2026-07-14 → today in this rehearsal; the
rest are older history the window correctly excludes. Your own split will differ.)

## Transcript

Verbatim. Only Django's `INFO` log lines removed.

```

==============================================================================
  Simulated terminal: 32 users, 11520 attendance records
==============================================================================

PASS 1  golive_import --since 2026-07-14   (roster + history)
------------------------------------------------------------------------------
Device: main-gate at 127.0.0.1:40045 (Asia/Kathmandu)

1/5  Reading the enrolled users
     32 enrolled users on the terminal
     IDs: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32

2/5  Confirming no device ID was altered
     32 IDs stored, identical to the terminal's
     Verify against the device's own menu with: verify_biometric_ids --expect <ids>

3/5  Reading attendance from 2026-07-14 to today
     A full history transfer takes a while; this is one read.
     11520 records on the terminal, 1344 inside the window
     range 2026-07-14T09:01:00+05:45 .. 2026-08-06T17:32:00+05:45
     created 1344, duplicate 0 (already imported), unmapped 1344

4/5  Attributing punches to employees and deriving attendance
     1344 punch(es) are waiting on an employee mapping — see /api/v1/biometric/unmapped/.

5/5  Verification

  Import verification — 2026-07-14 to 2026-08-06

     1. Total imported users            32
     2. Total imported punches          1344
     3. Total AttendancePunch rows      1344
     4. Total Attendance rows           0
     5. Mapped employees                0
     6. Unmapped employees              32

     Device user IDs in OMS: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32

  32 enrolment(s) are not mapped to an employee: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32
  Their punches are STORED, not lost — but they produce no
  attendance, so those people read as absent until mapped. Do:
      python manage.py roster_report --csv mapping.csv
      # HR maps each one, then RE-RUN THIS COMMAND:
      python manage.py golive_import --device <label> --since 2026-07-14

  Re-running is what attributes the already-imported punches.
  Mapping alone does not: map_employee deliberately leaves history
  untouched, so `process_punches` on its own would find nothing to
  do and everything would still read as absent.

  Next: have someone punch, watch it happen, then
      python manage.py verify_realtime_punch --any
      python manage.py golive_status

==============================================================================
  HR has now mapped all 32 enrolments (via roster_report).
==============================================================================

PASS 2  golive_import --device main-gate --since 2026-07-14
------------------------------------------------------------------------------
Device: main-gate at 127.0.0.1:40045 (Asia/Kathmandu)

1/5  Reading the enrolled users
     32 enrolled users on the terminal
     IDs: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32

2/5  Confirming no device ID was altered
     32 IDs stored, identical to the terminal's
     Verify against the device's own menu with: verify_biometric_ids --expect <ids>

3/5  Reading attendance from 2026-07-14 to today
     A full history transfer takes a while; this is one read.
     11520 records on the terminal, 1344 inside the window
     range 2026-07-14T09:01:00+05:45 .. 2026-08-06T17:32:00+05:45
     created 0, duplicate 1344 (already imported), unmapped 0

4/5  Attributing punches to employees and deriving attendance
     1344 previously unattributed punch(es) claimed by their now-mapped employee
     Processed 0 employee-day(s): 0 derived, 0 reverted, 0 failed.

5/5  Verification

  Import verification — 2026-07-14 to 2026-08-06

     1. Total imported users            32
     2. Total imported punches          1344
     3. Total AttendancePunch rows      1344
     4. Total Attendance rows           672
     5. Mapped employees                32
     6. Unmapped employees              0

     Device user IDs in OMS: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32

  Every enrolment is mapped.

  Next: have someone punch, watch it happen, then
      python manage.py verify_realtime_punch --any
      python manage.py golive_status
```
