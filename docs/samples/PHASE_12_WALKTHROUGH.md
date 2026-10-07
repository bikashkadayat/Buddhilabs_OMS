# Phase 12 — the collector chain, demonstrated end to end

**This is a rehearsal, not production data.** Five invented staff, a simulated
ZK terminal (`biometric/tests/zk_simulator.py`), and 200 punches across
2026-07-14 → 2026-08-05. It exists because the real terminal does not answer
from the development machine, and because "the tooling is written" is a much
weaker claim than "here is what it prints".

Every command below is the real one, unmodified. What differs in production is
only the terminal it reads and the people in it.

## What to look for

1. **Device user IDs are unaltered at every step** — `1, 2, 7, 17, 23` off the
   terminal, the same five in the roster report, the same five in the identity
   verification. No renumbering anywhere in the chain.
2. **The dry run and the real run report identical counts.** That is the point
   of the dry run: it tells you what the import will do before it does it.
3. **`verify_biometric_ids` says EXACT MATCH but the verdict is INCOMPLETE.**
   Those are two different questions and it answers both: no ID has changed
   *and* nobody is mapped yet. Collapsing them into one verdict would let a
   real problem hide behind a green tick.
4. **`import_plan` refuses to proceed.** 200 punches belong to unmapped device
   users; importing them would leave those five people silently absent for
   three weeks. It blocks rather than warns.
5. **The coverage gap is reported** — 2026-08-06 is a working day with no
   punches. Here that is because the simulated log ends on the 5th. In
   production the same line is how you notice the terminal was off.

## Transcript

Verbatim command output. Only Django's `INFO`/`WARNING` log lines and blank
lines have been removed; no figure or wording has been edited.

```
==============================================================================
STEP 1  device_sync --dry-run --since 2026-07-14   (reads, writes nothing)
==============================================================================
Device sync — main-gate (127.0.0.1)
  DRY RUN — nothing was written.
  Window: 2026-07-14 .. today
  On device: 5 users, 200 stored records
  Clock: not readable
  Roster
    read 5 enrolled users
    device user IDs: 1, 2, 7, 17, 23
  Punches
    read 200, in window 200 (skipped 0 before, 0 after)
    range 2026-07-14T09:02:00+05:45 .. 2026-08-05T17:39:00+05:45
==============================================================================
STEP 2  device_sync --since 2026-07-14             (the import)
==============================================================================
Device sync — main-gate (127.0.0.1)
  Window: 2026-07-14 .. today
  On device: 5 users, 200 stored records
  Clock: not readable
  Roster
    read 5 enrolled users
    created 5, updated 0, unmapped total 5
    device user IDs: 1, 2, 7, 17, 23
  Punches
    read 200, in window 200 (skipped 0 before, 0 after)
    range 2026-07-14T09:02:00+05:45 .. 2026-08-05T17:39:00+05:45
    created 200, duplicate 0, unmapped 200
  200 punches have no employee mapping. They are stored, not lost — resolve them in the HR mapping queue and the attendance backfills automatically.
==============================================================================
STEP 3  roster_report                              (for HR)
==============================================================================
Employee mapping report
Device User  Device Name            Status    Suggested OMS Employee      Score  Confidence
--------------------------------------------------------------------------------------------
1            Bikashkadayat          UNMAPPED  Bikash Kadayat              1.000  exact
17           Hari Bahadur           UNMAPPED  Hari Bahadur                1.000  exact
2            Ram Thapa              UNMAPPED  Ram Thapa                   1.000  exact
23           Anita Shrestha         UNMAPPED  Anita Shrestha              1.000  exact
7            Sita Gurung            UNMAPPED  Sita Gurung                 1.000  exact
  Total enrolments : 5
  Mapped           : 0
  Unmapped         : 5
  Unmapped enrolments produce punches that never become attendance.
  Map them before go-live, or that person's days are simply missing.
==============================================================================
STEP 4  verify_biometric_ids --expect 1,2,7,17,23  (identity check)
==============================================================================
Biometric identity verification
  The device is the source of truth. The OMS stores its identifiers
  and never renumbers, reassigns or replaces them.
  1. Device user count
      Active enrolments : 5
      Device            : Main Gate (127.0.0.1)
  2. Device user IDs (as stored)
      1, 2, 7, 17, 23
  3. OMS user mappings
      Device ID   Device Name          OMS Employee             Employee ID          Status
      1           Bikashkadayat        —                        —                    UNMAPPED
      17          Hari Bahadur         —                        —                    UNMAPPED
      2           Ram Thapa            —                        —                    UNMAPPED
      23          Anita Shrestha       —                        —                    UNMAPPED
      7           Sita Gurung          —                        —                    UNMAPPED
  4. Mapping conflicts
      None.
  5. Unmapped users
      5: 1, 17, 2, 23, 7
      Their punches are stored but produce no attendance until mapped.
  6. Historical punch integrity
      Punches stored              : 200
      Distinct IDs in punches     : 5
      Distinct IDs enrolled       : 5
  8. Against the device roster you supplied
      On device : 5    In OMS: 5
      EXACT MATCH — every device ID is present in the OMS, unchanged.
  Verdict
      INCOMPLETE — no ID has been changed, but 5 enrolment(s) are unmapped.
      Enforcement is not advisory: BiometricEmployee.save() raises
      on any attempt to change device_user_id or device, so the API,
      the admin, a shell and a management command are all covered.
==============================================================================
STEP 5  import_plan --from 2026-07-14
==============================================================================
Historical import plan — 2026-07-14 to 2026-08-06 (24 calendar days)
  1. Biometric users
      Distinct device users in window              5
      Active enrolments on record                  5
      Enrolments still unmapped                    5
  2. Punches
      Total                                        200
      Date range                                   2026-07-14 to 2026-08-05
      Days with punches                            20
      Days without punches                         4
      Not yet processed                            200
  3. Attendance days
      Employee-days the import will produce        0
      Attendance rows already in window            0
        of which from the device                   0
        of which HR entries (PROTECTED)            0
  4. Duplicates
      Exact duplicates                             0
      Employee-days with >6 punches                0
      Structurally impossible: AttendancePunch has a unique constraint on (device, device user, timestamp), which is what makes a backlog replay free.
  5. Unmapped users
      Punches from unmapped device users           200
      Distinct unmapped device IDs                 5
      IDs: 1, 17, 2, 23, 7
  6. Coverage gaps
      Working days with no punches                 1
      2026-08-06
  Verdict
      BLOCKER: 200 punches belong to unmapped users. Import them now and those days are silently missing for those people — map first (`roster_report`).
  To execute, after resolving the above:
      python manage.py process_punches            # derive attendance
      python manage.py validate_import --from 2026-07-14
```
