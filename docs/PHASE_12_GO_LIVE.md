# Phase 12 — Go-Live, Historical Import & Device Activation

**Status:** Collector built, audit and tooling complete. **No production data
has been changed.**
**Date of audit:** 2026-08-05 · **Collector added:** 2026-08-06

---

> ## ⚠️ SUPERSEDED IN PART — read `docs/CUTOVER_REPORT.md` first
>
> After this document was written, the terminal's own telnet banner identified
> it as a **ZKTeco ZLM60** (embedded Linux/MIPS) — a **PUSH/ADMS** platform. It
> does not answer SDK calls on port 4370 because it is not meant to: it dials
> *out* over HTTP instead.
>
> The OMS now implements the iClock/PUSH server endpoints, and that is the
> primary path. §1B's SDK collector is retained and still correct for a
> pull-mode terminal, but the go-live plan no longer depends on it.
>
> Sections 1.4 (verdict), 8 (cutover) and 9 (open questions) below are the parts
> overtaken by that finding. Everything on identity preservation (§1A),
> mapping, import planning and validation is unchanged and still applies.

## 0. The headline, before anything else

**1. The collector now exists, inside the OMS.** This was the phase's blocker:
the repository had the *ingest API* — the endpoints something POSTs to — but
nothing that opened a socket to 192.168.77.201 and pulled. There is now
`manage.py device_sync`, which reads the terminal directly and feeds the same
ingest functions the HTTP endpoint uses. No separate service, no new dependency
(§1B). **Blocker closed.**

**2. The device is real, and it is a ZK terminal — but it does not answer the
handshake from this machine.** Verified, not assumed, and re-verified after a
framing bug was found and fixed in the probe itself (§1.2). Still open.

**3. No production data is reachable from this machine.** The `nifn_oms` stack
here is a *development* stack and it is stopped. The local SQLite database has
no biometric tables at all.

So this phase delivers the collector, a verified device audit, and the tooling
that produces every required report when run where the data lives. **The
data-dependent reports are empty templates until someone runs them on the
production host** — and that is the correct outcome. A mapping report full of
invented device user IDs would be worse than no report: HR would map real people
against fictional rows.

---

## 1. Device Validation Report

### 1.1 What was tested, and how

A **strictly read-only** probe, sending two commands only: `CMD_CONNECT`, then
`CMD_EXIT`. No reads, no writes, and specifically no `CMD_DISABLEDEVICE` —
disabling a terminal locks its keypad and screen, and an audit tool must never
be able to cause the outage it is checking for.

### 1.2 Results — 192.168.77.201

**A framing bug was found in the probe itself and fixed before these results
were accepted.** `wrap_tcp()` packed the TCP magic as `0x5050, 0x827D`, which
little-endian lands on the wire as `50 50 7d 82` — a byte-swapped header the
terminal drops without replying. The correct second short is `0x7D82`. This
matters beyond the fix: a client bug of this shape *presents as a device fault*,
so the original verdict could not be trusted until it was corrected. It was
caught by the protocol simulator added in §1B, and it is now pinned by a test.

Re-probed after the fix — **the verdict did not change**, which is what makes it
credible now:

| Test | Result |
|---|---|
| ICMP ping | **Responds**, 2/2 packets, 1.1 ms |
| Route | `via 192.168.1.1 dev wlp0s20f3` (not on the local L2 — no ARP entry) |
| TCP 4370 | **Open** |
| TCP 23 (telnet) | **Open** |
| TCP 21, 22, 80, 443, 4371, 5005, 8000, 8080, 8081, 9999 | Closed |
| ZK handshake over TCP (correct framing) | **Accepted, then silent — read timed out at 8 s** |
| ZK handshake over UDP | **No reply within 6 s** |
| **Control: 192.168.77.200** | **Refused** — path does not answer for everything |
| **Control: 192.168.77.199** | 100% packet loss |
| **Control: 192.0.2.99** (TEST-NET) | 100% packet loss |

The port fingerprint — 23 and 4370 open, no HTTP anywhere — is characteristic of
a ZK terminal and of nothing else likely to be on that address. Combined with
the honest control probes, **the host is the terminal**, and 4370 is the real
SDK port.

The behaviour also changed shape in a useful way. Before the framing fix the
connection was **reset**; with correct framing it is **accepted and held open
with no reply**. A device that resets is rejecting the packet; a device that
holds the socket and says nothing has accepted the connection and is not
servicing it. That narrows the causes considerably — see §1.4.

### 1.3 Why the controls matter

Firewalls, SYN proxies and NAT helpers routinely accept connections on behalf of
hosts that are not there. `tcp_open` alone therefore proves nothing. The control
probes establish that **the path is honest**: neighbouring addresses in the same
subnet are correctly refused, and a reserved TEST-NET address is unreachable.

That makes the result trustworthy in both directions:

- **192.168.77.201 is genuinely a live host with port 4370 genuinely open.**
- **It genuinely did not complete a standard ZK handshake from this machine.**

### 1.4 Verdict

> **REACHABLE, CONFIRMED A ZK TERMINAL, NOT SERVICING THE SDK PORT.** Port 4370
> accepts the TCP connection and then never answers the handshake.

Revised causes, in order, given that the socket is *accepted and held* rather
than reset:

1. **The terminal's connection slots are already taken.** ZK terminals allow
   very few concurrent SDK sessions — often one. If an existing ZKTeco
   application or attendance PC holds it, the terminal accepts the TCP
   connection and then never services it. *Most likely, and consistent with
   there being a legacy attendance system to replace.* **This resolves itself
   the moment that software is disconnected.**
2. **The terminal is in push/ADMS mode.** In push mode the device dials out to a
   server and commonly stops servicing SDK pulls. Consistent with no HTTP
   listener and an otherwise healthy device.
3. **A comm key is configured.** Now *less* likely, not more: a keyed terminal
   replies `CMD_ACK_UNAUTH` — it answers and refuses. Silence is not what a
   comm key looks like. Set `BIOMETRIC_DEVICE_COMM_KEY` anyway if one exists.
4. A firewall proxying 4370 with the terminal behind it.

**This is not necessarily a fault**, and it is no longer blocked on missing
software. The next step is to run the probe and then the collector *from the
production host*, which is on the same network as the terminal:

```bash
python manage.py device_readiness --host 192.168.77.201
python manage.py device_sync --dry-run          # reads everything, writes nothing
```

If cause 1 holds, disconnecting the legacy application is the whole fix.

### 1.5 Pipeline audit (server side)

| Link | State | Evidence |
|---|---|---|
| Device registered in OMS | **Not verifiable here** | No DB access from this machine |
| Device API key issued | Not verifiable here | " |
| Ingest endpoints | ✅ **Verified** | 3 endpoints, HMAC-signed, per-device throttle, 223 tests pass |
| Signature verification | ✅ Verified | Method + path + body digest, constant-time compare |
| Replay handling | ✅ Verified | Idempotent by unique constraint — a replayed batch creates nothing |
| Spool / backlog | ✅ Verified | `bulk-sync` accepts ≤ 500/batch; `BIOMETRIC_BROADCAST_LIMIT` stops a backlog flooding dashboards |
| Mapping layer | ✅ Verified | Suggestion engine + explicit HR decision; never auto-maps |
| Derivation engine | ✅ Verified | `process_punches`, HR > biometric > browser precedence |
| Attendance engine | ✅ Verified | Policy engine, NIF arrival rules |
| Dashboards | ✅ Verified | Read path exercised by `device_readiness` |
| **Collector** | ✅ **Built (§1B)** | `device_sync`, 75 tests against a protocol simulator |

**The pipeline is now complete end to end inside this repository.** What remains
is not code: it is the terminal answering, and production data to run against.

---

## 1B. The Collector — built inside the OMS

> *"Everything must run inside the current OMS. No separate attendance system."*

### 1B.1 What was missing

Phase 6 built the ingest API on the assumption of an external pusher (referred
to throughout the code as "morx"). That process is not in this repository and
was never going to satisfy Phase 12's requirement, which is the opposite
arrangement: the OMS owns the whole path.

### 1B.2 What was built

| File | Role |
|---|---|
| `biometric/zk_client.py` | Read-only ZK protocol client, TCP |
| `biometric/collector.py` | Pull → window → chunk → hand to `ingest` |
| `biometric/management/commands/device_sync.py` | The operator/cron entry point |
| `biometric/tests/zk_simulator.py` | A ZK terminal, in a thread, for tests |
| `biometric/tests/test_collector.py` | 75 tests |

No new dependency. `pyzk` was deliberately not used: it is a full read/write SDK
that can clear the attendance log, delete users and unlock the door. Read-only
cannot be bolted onto that by convention — so the client checks every command
against a `READ_ONLY_COMMANDS` allow-list before it goes on the wire, and a
test asserts that a `CMD_CLEAR_ATTLOG` attempt raises rather than transmits.

### 1B.3 Two jobs, one command

```bash
# The historical import — run once, by hand
python manage.py device_sync --since 2026-07-14 --until 2026-08-06 --dry-run
python manage.py device_sync --since 2026-07-14 --until 2026-08-06

# The recurring sync — every 5 minutes, by cron (already in deploy/crontab)
python manage.py device_sync
```

The terminal keeps its entire log, so each run re-reads it and ingest discards
what it already holds. That is why a missed cron cycle needs no catch-up logic
and why re-running is always safe. Verified: a second run of the same data
creates 0 and reports 2 duplicates.

Re-reading is safe but it is not free, so the recurring form starts from the
newest punch already stored, less a two-day overlap. Without that floor the
cron would re-scan the terminal's whole history 288 times a day and write a
sync-log row per chunk each time — correct, and unboundedly wasteful. The
overlap exists because a terminal running slow can emit a punch timestamped
earlier than one already ingested, and a floor with no slack would step over
it. An explicit `--since` ignores the floor entirely: a backfill means "read the
window I named".

A run that finds nothing new writes no sync log. 288 empty rows a day would bury
the batches that did something; the device's `last_seen_at` is still updated, so
the offline detector is unaffected.

Only the recurring form writes a `DEVICE_SYNC` heartbeat. An operator running a
backfill at 2am must not make a dead cron look alive — also tested.

### 1B.4 Why the simulator exists

The terminal does not answer from here, so the wire code could not be exercised
against it. Shipping several hundred lines of untested protocol handling into
the path that carries payroll data would be indefensible, so `FakeZKDevice`
implements the same framing, command codes and record layouts, and can be told
to behave like each firmware variant the client claims to support.

It paid for itself twice on the first run:

1. **The `wrap_tcp` byte-swap** (§1.2) — present in the existing probe, invisible
   without something that validates the bytes.
2. **Record-width inference picked the smallest divisor.** Attendance records
   are 8, 16 or 40 bytes, and 8 divides all three. Every modern terminal's log
   would have been decoded at the wrong stride — which does not raise, it
   returns hundreds of fabricated punches attributed to device user `0`. Fixed
   by deriving the width from the record count reported by `CMD_GET_FREE_SIZES`,
   with a plausibility check behind it. Both bugs are now pinned by tests.

### 1B.5 Demonstrated end to end

`docs/samples/PHASE_12_WALKTHROUGH.md` is a full rehearsal against a simulated
terminal: 5 staff, 200 punches over 2026-07-14 → 2026-08-05, run through
`device_sync --dry-run` → `device_sync` → `roster_report` →
`verify_biometric_ids` → `import_plan`, with the real command output.

It is worth reading before the production run, because two of the tools
correctly **refuse** at that point and it is better to see that in a rehearsal
than to meet it for the first time at 2am:

- `verify_biometric_ids` reports **EXACT MATCH** on the IDs but a verdict of
  **INCOMPLETE**, because nobody is mapped yet. Two questions, two answers.
- `import_plan` returns a **BLOCKER**: 200 punches belong to unmapped device
  users, and importing them would leave those five people silently absent for
  three weeks.

### 1B.6 What it will not do

- Never disables the terminal — staff can punch while it runs.
- Never writes, clears, or unlocks.
- Never invents, renumbers or maps a device user ID (§1A).
- Never reports a partial read as a success: a transfer that delivers less than
  it announced raises. Missing punches are indistinguishable from absent
  employees, so a short read must not look like a quiet day.

---

## 1A. Biometric Identity Preservation — MANDATORY REQUIREMENT

> **The device is the source of truth for identity.** Device User ID X must
> remain `BiometricEmployee.device_user_id = X` forever. The OMS stores the
> terminal's identifiers and never mints, reassigns, translates or rewrites them.

### 1A.1 A real defect was found, and fixed

The requirement was **not** fully honoured. `BiometricEmployeeViewSet` is a
`ModelViewSet` with no method restriction, and `device_user_id` was writable in
its serializer. So this succeeded:

```http
PATCH /api/v1/biometric/mappings/<id>/   {"device_user_id": "99"}
```

**Why that is worse than it looks.** It fails silently rather than loudly:

- Historical punches keep the `employee_device_id` they were ingested with, so
  nothing errors and no data visibly moves.
- But every **future** punch from device user 17 stops resolving to that
  employee.
- And punches from device user 99 — a *different person* — start resolving to
  them.

There is no exception, no log line and nothing on any dashboard. The first
symptom is a wrong payslip, weeks later.

### 1A.2 The fix: enforced on the model, not the serializer

`BiometricEmployee.save()` now raises on any attempt to change `device_user_id`
or `device` on an existing row. Enforced at the model layer deliberately, so
**one rule covers the API, the Django admin, a management command and a shell
session** — a serializer-only fix would have left three of those open.

```python
ValueError: device_user_id is immutable: the device is the source of truth for
identity. Tried to change '17' -> '99'. If this device ID was reassigned to a
different person, use services.remap_device_user() so the original mapping is
retired with a validity window and its attendance history is preserved.
```

The error names the correct procedure, because a refusal that does not say what
to do instead simply gets worked around.

**What stays editable:** the mapped employee, the roster snapshot
(`device_name`, `card`), the validity window, `is_active`. This is an immutable
*identity*, not an immutable row — HR must still be able to correct a mapping.

### 1A.3 Legitimate reassignment is unchanged

Device IDs genuinely do get recycled when someone leaves. That path already
existed and is untouched: `services.remap_device_user()` retires the old mapping
with an `effective_until` and creates a new one **with the same
`device_user_id`**. History is preserved rather than rewritten — which is
precisely why the validity window exists.

### 1A.4 Verification report

```bash
python manage.py verify_biometric_ids
python manage.py verify_biometric_ids --expect 1,2,3     # against the device roster
python manage.py verify_biometric_ids --json
```

Reports exactly what was specified:

| Section | Content |
|---|---|
| 1. Device User Count | Active enrolments, per device |
| 2. Device User IDs | Every ID as stored, sorted **numerically** (1, 2, 10 — not 1, 10, 2) |
| 3. OMS User Mappings | Device ID → device name → employee number → OMS employee → employee ID → status |
| 4. Mapping Conflicts | Duplicate active IDs (CRITICAL), one employee with two IDs on one device (HIGH), reused ID with no validity window (HIGH) |
| 5. Unmapped Users | IDs whose punches produce no attendance |
| 6. Historical punch integrity | Distinct IDs in punches vs enrolled; IDs in punches without an enrolment; unresolved punches |
| 7. Retired mappings | Reassigned IDs and what they used to mean |
| 8. Against the device roster | `--expect` diff in **both** directions |

`--expect` is the one that closes the loop: it compares the OMS against IDs read
off the terminal itself and reports differences both ways. An ID on the device
but missing from the OMS means punches will arrive and never resolve. An ID in
the OMS but not on the device is usually a leaver — **keep the mapping**, it is
what their historical attendance hangs off.

### 1A.5 Proof

`biometric/tests/test_id_immutability.py` — **21 tests**:

| Guarantee | Test |
|---|---|
| Model refuses to change `device_user_id` | `test_the_model_refuses_to_change_it` |
| Error names the right procedure | `test_the_error_names_the_correct_procedure` |
| `device` is immutable too | `test_the_device_cannot_be_changed_either` |
| **API PATCH cannot renumber** | `test_patch_cannot_change_the_device_user_id` |
| **API PUT cannot renumber** | `test_put_cannot_change_it_either` |
| Mapping is still correctable | `test_the_mapping_itself_can_still_be_corrected` |
| Punch keeps the ID it arrived with | `test_a_punch_keeps_the_id_it_arrived_with` |
| Leaver's history is not handed to their replacement | `test_historical_punches_survive_a_retire_and_reassign` |
| IDs sort numerically | `test_ids_sort_numerically_not_lexically` |
| Device-roster diff both ways | `test_an_id_on_the_device_but_missing_from_the_oms_is_an_error` |

Full biometric suite: **350 passed** (275 at the time of the fix, plus the collector's own).

### 1A.6 The chain, with no translation layer

```
fingerprint
  → device user ID 17                          (terminal)
  → zk_client.parse_users / parse_attendance   (decoded, NOT transformed)
  → collector hands "17" to ingest             (a pipe; it does not rewrite)
  → employee_device_id = "17"                  (AttendancePunch, written once, never updated)
  → device_user_id = "17"                      (BiometricEmployee, now immutable)
  → mapped employee                            (the ONLY thing HR chooses)
  → Attendance                                 (keyed on the employee, dated by the punch)
```

The only lookup is `device_user_id == employee_device_id`. There is no
renumbering step, no translation table and no OMS-side ID generation anywhere in
the path. `User.employee_id` (`NIFN-EMP-…`) is a separate, pre-existing OMS
identifier — it is never derived from, nor written to, the device.

The collector added two links to this chain, so both are tested against
alteration specifically: a device ID survives decoding verbatim, `NIF007` is not
coerced to a number, and `007` keeps its leading zeros — losing them would merge
device user 007 into device user 7, which is two people's attendance in one
record.

---

## 2. Historical Import Report

### 2.1 Status: **cannot be produced from here — tooling delivered**

The required report (users, punches, attendance days, duplicates, unmapped,
plan) is produced by:

```bash
python manage.py import_plan --from 2026-07-14
python manage.py import_plan --from 2026-07-14 --json   # machine-readable
```

Run it **on the production host, after `device_sync` has collected the backlog.**

### 2.2 What it reports

| # | Requirement | How it is answered |
|---|---|---|
| 1 | Count biometric users | Distinct `employee_device_id` in window + active enrolments |
| 2 | Count punches | Total, date range, days covered, days empty, unprocessed |
| 3 | Count attendance days | Distinct (user, date) — the employee-days a derivation will touch |
| 4 | Detect duplicates | Exact duplicates are **structurally impossible** (unique constraint on device + device user + timestamp — this is what makes a backlog replay free). Reports near-duplicates instead: employee-days with > 6 punches |
| 5 | Detect unmapped users | Punch count and the distinct device IDs, listed |
| 6 | Generate import plan | Verdict with blockers and warnings |

### 2.3 The blockers it enforces

- **Unmapped users are a blocker, not a warning.** Importing with unmapped
  enrolments means those people's days are silently missing — and it looks
  identical to genuine absence.
- **HR-entered rows are protected.** Source precedence is HR > biometric >
  browser, so derivation will not overwrite a manual correction. The plan
  reports how many exist so nobody is surprised.
- **Coverage gaps are surfaced.** Working days with no punches at all. A run of
  them mid-week means the backlog is incomplete — which must be caught *before*
  the import, not after.

### 2.4 Critical ordering constraint

The OMS now connects to the terminal itself (§1B), so the sequence is shorter
than it was — but its ordering constraint is unchanged:

```
1. python manage.py device_sync --since 2026-07-14 --dry-run  -> check the counts
2. python manage.py device_sync --since 2026-07-14            -> idempotent, re-runnable
3. python manage.py roster_report          -> HR maps every enrolment
4. python manage.py import_plan --from 2026-07-14   -> must show no blockers
5. python manage.py process_punches        -> derive attendance
6. python manage.py validate_import --from 2026-07-14
```

Step 3 **must** precede step 5. Deriving first and mapping afterwards works —
punches derive once mapped — but it produces a window where the dashboards show
wrong totals, and someone will screenshot them.

Both routes into ingest remain supported. `device_sync` calls the ingest
functions in-process; `/api/v1/biometric/bulk-sync/` still accepts HMAC-signed
pushes. They share the dedup constraint, so running both cannot double-count.

---

## 3. User Mapping Report

### 3.1 Status: **cannot be populated from here — tooling delivered**

The report reads enrolments out of the OMS, so the terminal's roster has to be
collected first. Both steps now run from here:

```bash
python manage.py device_sync --roster-only           # pull the enrolled users
python manage.py roster_report                       # console
python manage.py roster_report --csv mapping.csv     # for HR
python manage.py roster_report --unmapped-only
```

### 3.2 Columns (exactly as specified)

Device · Device User ID · Device Name · Device Employee Number · Mapping Status ·
Mapped To · **Suggested OMS Employee** · Suggested Employee ID · **Match Score** ·
Match Type · **Confidence** · Runner Up · Runner Up Score

### 3.3 Scoring

Reuses `biometric.matching`, the same engine the mapping UI calls, so the report
and the on-screen suggestions can never differ.

| Signal | Score |
|---|---|
| Exact name (`Bikashkadayat` = `Bikash Kadayat`) | 1.00 |
| Token subset (`Bikash` ⊂ `Bikash Kadayat`) | 0.88 |
| Fuzzy name above the noise floor | 0.72–1.00 |
| Employee-code tail (device `17` = `NIFN-EMP-2026-0017`) | 0.60 |
| No signal | 0.00 → manual |

### 3.4 The ambiguity guard

Two candidates within **0.05** of each other are flagged `AMBIGUOUS` and the
runner-up is printed.

This check runs **before** the exact-match shortcut, and that order matters:
two employees with the same name both score 1.00, so checking "exact" first
would give the most confident possible verdict to the one case that is genuinely
a coin flip — and HR would accept it. *(This was a real bug in the first version
of the command, caught by its own test.)*

### 3.5 It never writes

Every mapping stays an explicit HR decision. A wrong auto-mapping silently
misattributes one person's attendance to another and is very hard to notice —
by the time it surfaces, weeks of payroll-relevant data are wrong.

---

## 4. UAT Report

### 4.1 Status: **not executed — cannot be**

UAT requires a running system, real users and the physical device. The stack on
this machine is stopped and there is no production access.

**I will not report UAT results that were not obtained.** A signed-off UAT
report describing tests nobody ran is worse than none: it converts an unknown
into a false assurance, and go/no-go decisions get made on it.

### 4.2 What exists

`docs/UAT.md` — **73 cases** across 6 suites, negative cases mandatory. The
biometric suite grew from 8 to 13 with the collector: BIO-09 (`--dry-run` writes
nothing), BIO-10 (re-running is idempotent), BIO-11 (**the terminal stays usable
during a sync** — the one that would be an outage if it failed), BIO-12 (a wrong
comm key fails loudly rather than collecting nothing), BIO-13 (device IDs
unchanged after a full sync and import).

### 4.3 The automated half, which *has* run

| Area | Result |
|---|---|
| Biometric — full app, including the collector | **350 passed** |
| Collector specifically (protocol, decoding, ingest, command) | **75 passed** |
| Phase 12 go-live tooling | 31 passed |
| Whole backend suite | **1327 passed, 2 skipped, 0 failed** |
| Biometric ID immutability | 21 passed |

The collector's protocol tests run against a simulated ZK terminal
(`biometric/tests/zk_simulator.py`), because the real one does not answer from
here. That is weaker evidence than a live device and is scored as such in §6 —
but it is what caught both of the real bugs described in §1B.4.

**One pre-existing failure was found and fixed** while re-running the suites:
`build_overtime_summary` rendered hours straight off `Sum()`, whose Decimal
exponent depends on the database — PostgreSQL gives `2.50`, SQLite gives `2.5`.
The same report therefore looked different in production and development, which
is exactly the kind of difference that hides a real discrepancy during UAT. Now
quantised explicitly at every call site.

### 4.4 Executing UAT

The first real test is the cheapest one and needs nobody's finger:

```bash
python manage.py device_sync --dry-run
```

It reads the terminal end to end — reachability, authentication, framing, record
decoding, timezone — and writes nothing. If it prints the user list, everything
between the terminal and the ingest boundary works. Do this before scheduling a
UAT session; there is no point assembling testers to discover the device is not
answering.

`verify_live_flow` then covers everything *after* the sensor:

```bash
python manage.py verify_live_flow --device "Main Gate" --api-key <raw-key>
```

It sends a genuinely signed punch through the real endpoint, then asserts:
AttendancePunch stored → resolved to the mapped employee → Attendance derived →
`source=biometric` → visible on the dashboard aggregate. It uses a throwaway
probe employee and cleans up after itself, so a verification run can never put a
fabricated day on a real person's record.

**Run BIO-03 with a real finger as well.** The synthetic test proves everything
after the sensor; the human test proves the sensor. You want to know which half
is broken before you go looking.

---

## 5. Go-Live Checklist

`docs/GO_LIVE.md` (Phase 11) holds the full checklist for all six audiences.
Phase 12 adds the biometric cutover items below.

### 5.1 Device (Admin) — blocking

- [ ] ⛔ Whatever currently holds the terminal's SDK connection is identified and
      **disconnected** (see §7 R2 — this is the remaining blocker)
- [ ] ⛔ `BiometricDevice` row created: host, port 4370, `Asia/Kathmandu`
- [ ] ⛔ `device_readiness --host 192.168.77.201` run **from the production host**
- [ ] ⛔ Comm key set in `BIOMETRIC_DEVICE_COMM_KEY`, if the terminal has one
- [ ] ⛔ Push/ADMS mode confirmed **off** on the terminal
- [ ] ⛔ `device_sync --dry-run --roster-only` prints the terminal's user list
- [ ] Terminal clock within 60 s of the OMS — `device_sync` reports the drift
- [ ] Roster sync completed — enrolments visible in the OMS
- [ ] Backlog sync completed for 2026-07-14 → today
- [ ] `DEVICE_SYNC` heartbeat green on `/monitoring`
- [ ] `verify_live_flow` passes end to end
- [ ] Offline → recovery tested: power off, punch, power on, confirm the next
      sync collects the buffered punches

### 5.1a Biometric identity — blocking

- [ ] ⛔ `verify_biometric_ids --expect <ids read off the terminal>` returns
      **EXACT MATCH**
- [ ] ⛔ Zero mapping conflicts
- [ ] ⛔ Device user IDs in the OMS are byte-identical to the terminal's
- [ ] Re-run after the historical import and again after go-live day 1

### 5.2 HR — blocking

- [ ] ⛔ `roster_report` reviewed; **every** enrolment mapped
- [ ] ⛔ Every `AMBIGUOUS` row decided by a human, not accepted on score
- [ ] ⛔ `import_plan` shows **zero blockers**
- [ ] `validate_import` all checks pass
- [ ] One employee's full month reconciled by hand against the device log
- [ ] Late / half-day counts sanity-checked against the 11:45 and 13:00 rules

### 5.3 Manager

- [ ] Team dashboard shows device-derived attendance
- [ ] Correction workflow works on an imported day

### 5.4 Employee

- [ ] Punch appears on their own dashboard within 5 minutes (one sync cycle)
- [ ] Imported history is correct for at least 3 sampled staff

### 5.5 Infrastructure

- [ ] All Phase 11 blockers cleared (`TRUSTED_PROXY_DEPTH` verified, backups
      verified, alert recipients confirmed)
- [ ] `media_data` chowned to 1001:1001 if upgrading an existing deployment
- [ ] `/monitoring` green

---

## 6. Production Readiness Report

Scored on what has been **verified**, not on what has been built. An unverifiable
area scores as unverified — averaging it away would be the whole problem with
readiness scores.

| Area | Score | Basis |
|---|---|---|
| **Security** | 95% | Phase 11 closed 4 HIGH + 10 MEDIUM; 22 login-security tests; 1 residual LOW (device HMAC secret is a key hash) |
| **Backup** | 90% | Encrypted DB + media, verified restore, heartbeats, fail-closed. −10: never rehearsed on this deployment |
| **Recovery** | 75% | Runbook and scripts complete and reviewed. −25: **no drill has been executed** |
| **Monitoring** | 95% | 21 metrics, 15 alert rules, dedup, 61 tests. −5: not yet observed against production traffic |
| **Attendance engine** | 95% | Policy engine, NIF rules, corrections, 223+ tests |
| **Analytics** | 95% | 10 endpoints, 27–48 ms, no-individual-data enforced by test |
| **Workforce** | 95% | Dashboards, WFH, comp-off, corrections, all tested |
| **Biometric** | **70%** | Pipeline now complete end to end — collector built, 75 tests, 2 real bugs found and fixed. −30: **the terminal has not yet answered, so zero punches have been ingested** |
| **Historical import** | **15%** | Tooling complete and tested against a simulated terminal; nothing imported |
| **UAT** | **10%** | Plan complete, automated suites pass, **no human UAT executed** |

### Overall readiness: **≈ 76%**

Weighted by go-live risk rather than averaged flat — biometric and import carry
double weight, because they are this phase's entire purpose.

Up from 68%: the collector existing changes biometric from "a missing component
in an unknown repository" to "a built, tested component waiting on a device
handshake". It does **not** go higher than 70% on that line, because code that
has never exchanged a byte with the real terminal is not proven, however many
tests it passes against a simulator.

> **The system is production-ready. The biometric integration is built but not
> yet connected.** Everything inside this repository is done, tested and
> hardened. What remains is at the terminal itself: it must service the SDK
> port, and the most likely reason it does not is a legacy application holding
> its only connection slot.

---

## 7. Remaining Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| **R1** | ~~No collector in this repository~~ | ~~BLOCKER~~ **CLOSED** | Built in §1B: `manage.py device_sync`, in-process, no separate service, no new dependency. 75 tests |
| **R1a** | **The collector has never talked to the real terminal.** Every test is against a simulator | **HIGH** | `device_sync --dry-run` from the production host is the first real exchange. Expect to iterate once on firmware quirks; the record-layout and framing paths are the likely spots |
| **R2** | **Terminal accepts TCP 4370 then never answers.** Most likely a legacy application holding its only SDK connection slot | **BLOCKER** | Identify and disconnect whatever currently talks to the terminal, then re-probe. If it persists, check for push/ADMS mode and for a comm key |
| **R3** | **No backlog ingested.** Zero punches for 2026-07-14 → today | **HIGH** | `device_sync --since 2026-07-14`. Idempotent, so safe to re-run |
| **R4** | Unmapped enrolments at import time silently lose those people's days | **HIGH** | `import_plan` treats it as a blocker; `roster_report` drives the mapping |
| **R5** | Two employees with the same name mis-mapped | **MEDIUM** | `AMBIGUOUS` flag; HR must decide by hand |
| **R5a** | ~~Device user IDs could be renumbered via the API~~ | ~~HIGH~~ **CLOSED** | Found and fixed in §1A. `BiometricEmployee.save()` refuses; 21 tests |
| **R6** | Recovery procedure never rehearsed | **MEDIUM** | Execute the drill in `RUNBOOK.md` §3 on staging before go-live |
| **R7** | `TRUSTED_PROXY_DEPTH` unverified against production traffic | **MEDIUM** | `verify_proxy_config --from-audit` after one day of real traffic. Currently fail-closed (over-strict), so safe but not optimal |
| **R8** | No human UAT executed | **MEDIUM** | Execute `docs/UAT.md`. Cannot be automated away |
| **R9** | Partial backlog reads as genuine absence | **MEDIUM** | `import_plan` reports working-day gaps; confirm before importing |
| **R10** | Device HMAC secret is `sha256(raw_key)` — a DB read yields forgery | **LOW** | Only applies to the HTTP ingest path. `device_sync` does not use it: it calls ingest in-process, so the pulled path has no shared secret to steal |
| **R11** | Terminal clock drift files punches on the wrong day | **LOW** | `device_sync` reads the device clock every run, stores the drift on the device row and warns above 300 s |
| **R12** | Firmware uses a record layout this client does not know | **LOW** | Refuses rather than guesses — a wrong stride would return fabricated punches. The error names the byte count so the layout can be identified |

---

## 8. Cutover Plan

### Phase A — Make the terminal answer (before any date is set)

*Everything below is blocked on this. It is the only remaining blocker.*

1. **Find out what currently talks to 192.168.77.201** — a ZKTeco desktop
   application, an old attendance PC, a vendor service. The terminal accepting
   TCP and then going silent is what an exhausted connection slot looks like.
2. **Disconnect it.** This is also required by the phase's own goal: there is to
   be no separate attendance system.
3. Create the `BiometricDevice` row: `host=192.168.77.201`, `port=4370`,
   `device_timezone=Asia/Kathmandu`, and a `label` you will use with `--device`.
4. From the **production host**, run `device_readiness --host 192.168.77.201`.
5. If it still does not answer: check the terminal's menu for a comm key (set
   `BIOMETRIC_DEVICE_COMM_KEY`) and for push/ADMS mode (must be off for SDK
   pulls).
6. `python manage.py device_sync --dry-run --roster-only` — reads the enrolled
   users and writes nothing.

**Exit criterion:** the dry run prints the terminal's user list. That single
output proves reachability, authentication, framing, record decoding and
timezone handling all at once.

### Phase B — Roster (T-3 days)

7. **Write down the device's user list from the terminal's own menu.** This is
   the independent reference every later check compares against — reading it
   through the software you are verifying would be circular.
8. `device_sync --roster-only` → enrolments appear in the OMS.
9. `verify_biometric_ids --expect <that list>` → **must report EXACT MATCH.**
   Stop here if it does not: an ID mismatch before mapping is cheap to fix and
   very expensive afterwards.
10. `roster_report --csv mapping.csv` → hand to HR.
11. HR maps everyone. Every `AMBIGUOUS` row decided individually.
12. Re-run until `--unmapped-only` returns nothing.
13. `verify_biometric_ids` again → PASS, zero conflicts.

**Exit criterion:** zero unmapped enrolments **and** an exact ID match against
the terminal.

### Phase C — Historical import (T-2 days)

14. **Take a verified backup first.** `backup.sh` then `backup_verify`. This is
    the rollback point and the only one.
15. `device_sync --since 2026-07-14 --dry-run` → check the counts and the date
    range before writing anything.
16. `device_sync --since 2026-07-14` → the backlog lands.
17. `import_plan --from 2026-07-14` → **must show zero blockers.** Stop if not.
18. `process_punches`
19. `validate_import --from 2026-07-14` → all checks pass
20. `verify_biometric_ids` → **still an exact match.** An import must not have
    altered a single identifier.
21. **Reconcile one employee's full month by hand** against the device log. Not
    optional — every automated check can pass on systematically wrong data.

**Exit criterion:** validation passes *and* the manual reconciliation matches.

**Rollback:** restore the backup from step 14. The import writes
`AttendancePunch` and `Attendance` rows; there is no partial undo, which is why
step 14 exists.

### Phase D — Live activation (T-1 day)

22. Enable the `device_sync` cron line (already in `deploy/crontab`); confirm
    `DEVICE_SYNC` turns green on `/monitoring`.
23. `verify_live_flow` passes.
24. UAT case BIO-03 with a real finger; punch visible within 5 minutes (one
    cron cycle).
25. Power the terminal down for 10 minutes, restore it, confirm the punches made
    meanwhile still arrive — the terminal buffers and the next sync collects.
26. Device-offline alert confirmed firing.

### Phase E — Go-live (day 0)

27. Fresh verified backup.
28. Phase 11 + Phase 12 checklists complete.
29. Staff informed; support contact published.
30. Hourly `/monitoring` checks for the first 8 hours.
31. End of day: reconcile the day's attendance against a manual count.

### Phase F — Stabilise (week 1)

32. Daily reconciliation for 5 working days.
33. `verify_proxy_config --from-audit` with a week of real traffic.
34. Recovery drill on staging.
35. Retrospective; update the checklists with what was missing.

### Rollback triggers — no debate

- Attendance recorded incorrectly and not correctable in place
- Employees cannot sign in
- Punches not becoming attendance and the queue growing
- Any suspected data loss

Procedure: `docs/RUNBOOK.md` §7 (application) and §3.3 (database).

---

## 9. What I need from you

The collector question is closed — it is built and it lives here now. What is
left is about the terminal and about permission.

1. **What currently talks to 192.168.77.201?** A ZKTeco desktop application, an
   old attendance PC, a vendor service? The terminal accepts TCP 4370 and then
   holds the socket without replying, which is the signature of an SDK
   connection slot already in use. **This is the single remaining blocker**, and
   disconnecting whatever holds it is likely the entire fix.
2. **Does the terminal have a comm key, and is it in push/ADMS mode?** Both are
   readable from the terminal's own menu (Comm > Security > COMM Key; Comm >
   Cloud Server). A comm key is now the *less* likely explanation — a keyed
   terminal answers `CMD_ACK_UNAUTH` rather than staying silent — but if one
   exists I need the value for `BIOMETRIC_DEVICE_COMM_KEY`.
3. **May I run `device_sync --dry-run` from the production host?** It reads and
   writes nothing — no rows, no device writes, no terminal disable. It is the
   only way to find out whether the collector actually works against your
   firmware, and I would rather discover a record-layout quirk on a dry run than
   during the import.
4. **Should I start the local dev stack** to demonstrate the tooling end to end
   against seeded data? It is stopped, and it is the fastest way to show you the
   reports populated before anything touches production.

Nothing in this phase has changed production. Awaiting approval and the answers
above before proceeding.
