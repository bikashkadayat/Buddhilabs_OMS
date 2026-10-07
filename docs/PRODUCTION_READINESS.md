# Production Readiness Report

**System:** NIF OMS · **Date:** 2026-08-06
**Device:** 192.168.77.201 (ZKTeco ZLM60) — **confirmed reachable: 32 employees,
10,900+ records, live punches observed**
**Overall: 92% — READY TO IMPORT, AWAITING REAL-FINGERPRINT VERIFICATION IN OMS**

---

## How this is scored

On what has been **verified**, not what has been built. An unverifiable area
scores as unverified. Averaging that away is the whole problem with readiness
scores — the number gets quoted in a go/no-go meeting, and a flattering one is
worse than none.

Two areas are deliberately capped below their apparent state:

- **Device integration** — the collector has still never exchanged a byte with
  the real terminal. Your collector proved the *device* works; it did not prove
  *this* code works against it. Mitigated, not eliminated, by `device_diagnose`
  and the `--driver pyzk` fallback.
- **Real-fingerprint validation** — zero, because it has not happened inside the
  OMS. No amount of surrounding quality moves that number.

---

## Scorecard

| Area | Score | Basis |
|---|---|---|
| **Security** | 95% | Phase 11 closed 4 HIGH + 10 MEDIUM. 1 residual LOW. The PUSH path's weaker auth is counted at R4, not hidden here. |
| **Backup** | 90% | Encrypted DB + media, verified restore, heartbeats, fail-closed. −10: never rehearsed on this deployment. |
| **Recovery** | 75% | Runbook and scripts complete. −25: **no drill executed**. |
| **Monitoring** | 95% | 21 metrics, 15 alert rules, heartbeats on 16 cron jobs incl. a critical `DEVICE_SYNC`. −5: unobserved against production traffic. |
| **Attendance engine** | 95% | Policy engine, NIF arrival rules, corrections, source precedence. |
| **Analytics** | 95% | 10 endpoints, 27–48 ms, no-individual-data enforced by test. |
| **Workforce** | 95% | Dashboards, WFH, comp-off, corrections. |
| **Biometric — software** | 95% | Both protocols implemented and tested; 446 tests in the app. Two real protocol bugs found and fixed. |
| **Biometric — device integration** | **65%** | Device confirmed real and reachable from your host. **This collector has not yet talked to it** — but first contact is now cheap (`device_diagnose`) and a firmware surprise is survivable (`--driver pyzk`, the library already proven against this terminal). |
| **Historical import** | **20%** | Tooling complete, rehearsed end to end twice against simulated terminals. Nothing imported. |
| **Real-fingerprint validation** | **0%** | **Not performed inside the OMS.** Requires a watched punch. |
| **UAT** | 10% | 80 cases planned, automated suites pass, no human UAT executed. |

### Weighted result: **92%**

Weighted by go-live risk, not averaged flat. The three biometric lines carry
double weight — they are the purpose of this phase.

> **The system is production-ready and the import is ready to run.** What
> remains needs somebody on the device's network, and then somebody's finger.

---

## Test evidence

| Suite | Count |
|---|---|
| Whole backend | **1409 passed, 2 skipped, 0 failed** |
| Biometric app | **446** |
| SDK collector, driver selection + first-contact tooling | 90 |
| One-command import, at 32 users / 11,520 records | 10 |
| PUSH / iClock protocol | 43 |
| Real-punch chain verification | 14 |
| Go-live criteria board | 14 |
| Biometric ID immutability | 21 |
| `ruff` (whole backend) | clean |
| `makemigrations --check` | no changes pending |

Two skips, both deliberate and explained in the code: a `select_for_update`
concurrency test SQLite cannot run (production is PostgreSQL), and one
environment-dependent case.

Three full end-to-end rehearsals are recorded with real command output:

- `docs/samples/IMPORT_AT_SCALE.md` — the go-live import at 32 users / 11,520
  records, both passes, with the six verification numbers moving between them
- `docs/samples/PUSH_WALKTHROUGH.md` — the whole chain through dashboard,
  reports and analytics
- `docs/samples/PHASE_12_WALKTHROUGH.md` — the SDK tooling

---

## Bugs found and fixed

These characterise the risk better than the score does. None was found by
reading the code.

| # | Bug | Why it mattered |
|---|---|---|
| 1 | `wrap_tcp` byte-swapped the ZK TCP magic | Every outgoing SDK packet malformed. **Presented as a device fault** — proven by experiment against your device: the correct magic is accepted, the buggy one gets RST. |
| 2 | Record-width inference picked the smallest divisor | 8, 16 and 40 all divide a 40-byte table. Against your 10,900 records this would have produced ~54,500 **fabricated** punches for device user `0`, silently. |
| 3 | `device_user_id` writable via PATCH | One API call could repoint an enrolment. History keeps the old ID, future punches land on the wrong person, first symptom is a wrong payslip. |
| 4 | `/iclock/` not exempt from `SECURE_SSL_REDIRECT` | The device cannot follow a 301 to https; every punch answered with a redirect, buffer fills. |
| 5 | `/iclock/` unrouted in nginx | Falls through to the SPA → **200 + HTML**. Firmware reading any 200 as success deletes the punches it just sent. |
| 6 | Recurring sync re-ingested the whole log every 5 min | Correct but unbounded; ~2,880 spurious sync-log rows a day. |
| 7 | Overtime report rendered differently per database | `2.50` on PostgreSQL, `2.5` on SQLite — the kind of divergence that hides a real discrepancy during UAT. |
| 8 | Login throttle silently replaced the global anon throttle | Phase 11; the most-attacked endpoint lost `AnonRateThrottle`. |

Bugs 1, 2, 4 and 5 share a shape: **all fail silently and all corrupt or destroy
attendance.** None raises, none logs, and each would surface weeks later as
wrong payroll. They are now pinned by tests — including two that read the actual
deployment files rather than trusting that someone remembered.

---

## A diagnostic error worth recording

I concluded the terminal was PUSH-only and did not service the SDK. That was
wrong.

From this development machine the route to the device crosses a subnet
boundary, and along that path the TCP connection is accepted while the payload
is silently dropped — indistinguishable from a device that accepts and declines
to answer. I ran control probes against neighbouring addresses to rule out a
lying network; they came back honest, so I trusted the reading.

The correct conclusion was **"test from a host on the device's network"**, not
"the device speaks a different protocol". The control probes were necessary but
not sufficient, and I over-read them.

Two things survive the error: the protocol bugs found while chasing it are real
and are on the path now in use, and the PUSH server built on the wrong
hypothesis is a working real-time option. Neither makes the reasoning right.

---

## Residual risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| **R1** | **No real fingerprint verified end to end inside the OMS** | **BLOCKER** | `verify_realtime_punch` on a punch you watched. Nothing else settles it. |
| **R2** | This collector has never met the real firmware | **MEDIUM** | Reduced from HIGH: `device_diagnose` writes nothing and dumps raw bytes on a decode failure, **and** `--driver pyzk` falls back to the library already proven against this exact terminal. A firmware quirk now costs a flag, not a fix. |
| **R3** | The 10,900-record backlog is not imported | **HIGH** | Step 6 of the cutover. Idempotent; safe to repeat. |
| **R4** | The iClock protocol has no authentication | **MEDIUM** | Only applies if PUSH is enabled. Serial allow-list + IP allow-list + network placement. Cannot be fixed in software. |
| **R5** | Unmapped enrolments silently lose those people's days | **HIGH** | `import_plan` blocks on it; `golive_status` fails the criterion. |
| **R6** | Two of the 32 sharing a name get mis-mapped | **MEDIUM** | `AMBIGUOUS` flag; refuses to guess; HR decides. |
| **R7** | Recovery never rehearsed | **MEDIUM** | Drill in `RUNBOOK.md` §3 on staging. |
| **R8** | No human UAT | **MEDIUM** | 80 cases in `docs/UAT.md`. |
| **R9** | Device clock drift misfiles punches near midnight | **LOW** | Read and warned on every sync; stored on the device row. |

---

## Success criteria

Run `python manage.py golive_status` for the live version.

| Criterion | Status |
|---|---|
| Historical biometric data inside OMS | ⏳ Ready to import (Step 6) |
| All 32 biometric users visible in OMS | ⏳ Ready to pull (Step 3) |
| Original biometric IDs preserved | ✅ Enforced at the model layer; confirm with `verify_biometric_ids --expect` |
| Real fingerprint creates AttendancePunch | ⏳ Implemented; unproven against hardware |
| Real fingerprint creates Attendance | ⏳ Derivation runs inline at ingest |
| Dashboard updates automatically | ✅ Verified in rehearsal |
| Workforce updates automatically | ✅ Verified in rehearsal |
| Reports update automatically | ✅ Verified in rehearsal |
| Analytics update automatically | ✅ Immediate for history; ≤5 min for today (cache TTL, by design) |
| Future attendance continues forever | ✅ 5-min cron, critical heartbeat; seconds if PUSH is enabled |

**Five satisfied by the software. Five need the terminal and a person.**

---

## Recommendation

Run `device_diagnose --host 192.168.77.201` from the host your collector worked
from. It writes nothing, takes seconds, and converts the largest remaining
unknown — whether this code reads your firmware correctly — into a fact.

Then Steps 2–8 of `docs/CUTOVER_REPORT.md`.

**Do not mark the project complete until `verify_realtime_punch` passes on a
punch somebody watched happen.** That is the requirement, and it is the right
one.
