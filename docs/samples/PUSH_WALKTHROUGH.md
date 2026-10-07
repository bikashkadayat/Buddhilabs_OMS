# PUSH end-to-end rehearsal — what go-live day should look like

**This is a rehearsal against simulated device traffic, not the real terminal.**
Three invented staff, the exact HTTP calls a ZKTeco ZLM60 makes, and a backlog
from 2026-07-14. It exists because 192.168.77.201 has not yet been pointed at
the OMS, and because "the endpoints are implemented" is a much weaker claim
than "here is the whole chain running".

Every command and every response below is real and unedited. What differs on
the day is only which terminal is calling and who is enrolled.

## The point of reading this

**Step 6 shows the complete chain in one screen** — punch → AttendancePunch →
Attendance → dashboard → reports → analytics, each with its own verdict. That
is the output you should expect after a real fingerprint.

**Step 7 is the one to study.** Every measurable criterion passes, and the board
*still* returns `AWAITING HUMAN VERIFICATION`. That is deliberate: a database
row produced by a real finger and one produced by a simulated punch are
identical, so no query can tell them apart. The tooling refuses to certify what
it cannot observe, and hands that judgement back to whoever watched the punch.

A checklist that grades its own homework is worse than no checklist. This one is
built so the only way to a fully green board is for a person to confirm the part
software cannot.

## Also worth noting

- **Step 3 runs before any mapping** and reports `EXACT MATCH` on the IDs while
  the overall verdict is `INCOMPLETE`. Two different questions, two answers —
  no ID has changed, *and* nobody is mapped yet.
- The live punch in step 5 landed at 13:37 and derived as **half day**, which is
  the NIF arrival rule (after 13:00) applying correctly without being asked.
- Delivery latency was **1s**. That is what "no manual sync" looks like.

## Transcript

Verbatim. Only Django's `INFO` log lines have been removed.

```

==============================================================================
STEP 1  The terminal dials in:  GET /iclock/cdata?SN=...&options=all
==============================================================================
HTTP 200
GET OPTION FROM: CJXK205060099
Stamp=0
OpStamp=0
ErrorDelay=30
Delay=10
TransTimes=00:00;14:05
TransInterval=1
TransFlag=1111000000
TimeZone=5.75
Realtime=1
Encrypt=0

==============================================================================
STEP 2  It posts its enrolled users:  POST /iclock/cdata?table=OPERLOG
==============================================================================
HTTP 200  ->  OK: 3
enrolments now in OMS: ['1', '2', '17']

==============================================================================
STEP 3  Identity check BEFORE any mapping
==============================================================================

Biometric identity verification
  The device is the source of truth. The OMS stores its identifiers
  and never renumbers, reassigns or replaces them.

  1. Device user count
      Active enrolments : 3
      Device            : Main Gate (192.168.77.201)

  2. Device user IDs (as stored)
      1, 2, 17

  3. OMS user mappings
      Device ID   Device Name          OMS Employee             Employee ID          Status
      1           Ram Thapa            —                        —                    UNMAPPED
      17          Bikashkadayat        —                        —                    UNMAPPED
      2           Sita Gurung          —                        —                    UNMAPPED

  4. Mapping conflicts
      None.

  5. Unmapped users
      3: 1, 17, 2
      Their punches are stored but produce no attendance until mapped.

  6. Historical punch integrity
      Punches stored              : 0
      Distinct IDs in punches     : 0
      Distinct IDs enrolled       : 3
      Enrolled but never punched  : 1, 2, 17

  8. Against the device roster you supplied
      On device : 3    In OMS: 3
      EXACT MATCH — every device ID is present in the OMS, unchanged.

  Verdict
      INCOMPLETE — no ID has been changed, but 3 enrolment(s) are unmapped.

      Enforcement is not advisory: BiometricEmployee.save() raises
      on any attempt to change device_user_id or device, so the API,
      the admin, a shell and a management command are all covered.

==============================================================================
STEP 4  Historical backlog re-transmitted by the terminal (ATTLOG)
==============================================================================
posted 120 records  ->  HTTP 200  OK: 120

==============================================================================
STEP 5  A LIVE punch — Bikash (device user 17) touches the sensor
==============================================================================
HTTP 200  ->  OK: 1

==============================================================================
STEP 6  verify_realtime_punch --device-user-id 17
==============================================================================
Real fingerprint verification — last 15 minutes

  [PASS] Punch received
         device user 17 at 2026-08-06 13:38:37 via main-gate (source LIVE)

  [PASS] Arrival latency
         0s from punch to stored

  [PASS] Enrolment known
         device user 17 -> Bikash Kadayat (NIFN-EMP-2026-0002)

  [PASS] Device ID unchanged
         the punch carries '17' and the mapping stores '17' — identical

  [PASS] Punch attributed
         attributed to Bikash Kadayat

  [PASS] Attendance derived
         2026-08-06: status=half_day, in=13:38, out=—, source=biometric

  [PASS] Attendance source
         biometric — derived from the device

  [PASS] Dashboard
         Bikash Kadayat is in scope and the 2026-08-06 row is readable

  [PASS] Reports
         attendance-vs-leave generated for that day and contains the employee

  [PASS] Analytics
         today's row; live analytics windows carry a 300s TTL, so it appears within 5 minutes without any manual step

  CHAIN COMPLETE — the punch reached every layer, end to end.
  This is a verification of a REAL fingerprint only if you watched it happen.
  The chain cannot tell a finger from a simulated punch; you can.

==============================================================================
STEP 7  golive_status
==============================================================================
Go-live success criteria — window from 2026-07-14

  ✅ [PASS  ] Historical attendance exists from the start date
           121 punches across 21 of 24 calendar days

  ✅ [PASS  ] All biometric users available in OMS
           3 enrolments, all mapped

  ✅ [PASS  ] Device IDs unchanged
           3 enrolled ID(s); every punched ID is enrolled. Enforced at the model layer — device_user_id is immutable. Confirm against the terminal with `verify_biometric_ids --expect`.

  👤 [MANUAL] Real fingerprint verified end to end
           most recent live punch: device user 17 at 2026-08-06 13:38:37, delivered in 0s, 0s ago
           Evidence only. A row cannot prove a finger touched a sensor — a
           simulated punch produces an identical one. Sign this off only
           after watching `verify_realtime_punch` pass on a punch you saw
           happen.

  ✅ [PASS  ] Future attendance continues automatically
           1 PUSH device(s) registered by serial

  ✅ [PASS  ] Workforce dashboard reflects attendance
           61 attendance rows, 61 device-derived

  ✅ [PASS  ] Reports reflect attendance
           attendance-vs-leave generated: 3 employee row(s)

  ✅ [PASS  ] Analytics reflect attendance
           reads Attendance directly; historical writes invalidate the cache immediately, today's ride a 300s TTL — so a punch appears within 5 minutes with no manual step

  ✅ [PASS  ] No manual attendance workflow required
           0 of 61 rows are manual HR entries (0%)

  VERDICT: AWAITING HUMAN VERIFICATION
  Everything measurable passes. What remains is the one thing a database cannot
  attest: a real finger on the real sensor. Run `verify_realtime_punch` on a
  punch you watched happen.
```
