# Final Cutover Report — Biometric Go-Live

**Device:** 192.168.77.201 (ZKTeco ZLM60) · **Window:** 2026-07-14 → today
**Confirmed on the device:** 32 employees, 10,900+ attendance records
**Status: NOT GONE LIVE.** No real fingerprint has been verified end to end
*inside the OMS*.

---

## 0. Read this first

The requirement ends with a sentence I am holding to:

> *Do not mark the project complete until a REAL fingerprint punch has been
> verified end-to-end inside OMS.*

It has not been. Everything below is arranged so the moment you run the import
and someone punches, one command gives a definitive answer.

### A correction to my previous report

I previously concluded the terminal was **PUSH-only** and did not service the
SDK on port 4370. **That was wrong, and it is worth saying why**, because the
reasoning error is instructive:

From this development machine the route to the device is
`via 192.168.1.1` — a different subnet. Along that path the TCP connection is
accepted and the payload is silently dropped. That is indistinguishable, from
the client side, from a device that accepts connections and declines to answer.
I ran control probes to rule out a lying network and they came back honest, so
I trusted the result and reached for the next hypothesis.

**Your successful connection settles it: the SDK works from a host on the
device's own network.** The correct conclusion was "test from the right host",
not "the device speaks a different protocol".

Two things survive that error, and both matter:

1. **The two protocol bugs I found are on the SDK path you are now using** —
   see §2. Without those fixes the import would have failed or, worse, produced
   fabricated records.
2. The PUSH server built on the wrong hypothesis is still a working real-time
   path and costs nothing to keep. See §5.

---

## 1. The short version

Two commands, run on a host on the terminal's network, with HR's mapping in
between:

```bash
# 1. Everything: register, roster, ID check, history, derive, verify
python manage.py golive_import --host 192.168.77.201 --since 2026-07-14

# 2. HR maps the 32 enrolments
python manage.py roster_report --csv mapping.csv

# 3. Re-run — this is what attributes the already-imported punches
python manage.py golive_import --device main-gate --since 2026-07-14
```

**Step 3 is not optional and is easy to skip.** Mapping alone does not
re-attribute history — `map_employee` deliberately leaves it untouched, because
re-attributing history is the operation that can silently hand one person's
attendance to another. After mapping, the punches are still attached to nobody
and the dashboards are still empty, with no error anywhere. Re-running
`golive_import` runs the bounded backfill that fixes it.

`docs/samples/IMPORT_AT_SCALE.md` shows both passes at 32 users / 11,520
records, with the numbers moving between them.

Then the gate:

```bash
# Someone punches. Watch it happen. Then:
python manage.py verify_realtime_punch --device-user-id 28
python manage.py golive_status
```

The long form below explains each step and what to check.

---

## 1b. What to run — the whole cutover, in order

Run these **on a host on the device's network** (the one your collector worked
from). Nothing here writes to the device.

### Step 1 — Diagnose before importing

```bash
python manage.py device_diagnose --host 192.168.77.201
```

Expect: `32 users`, `10900 attendance records`, and decoded samples showing real
IDs and names — `28` / `Chetan` among them.

**This is the step that de-risks everything else.** The collector's wire code
was tested against a simulator, which cannot reproduce a firmware quirk it was
not told about. If a table fails to decode, this command dumps the raw bytes so
the layout can be added without guessing — one round trip instead of an
afternoon.

**Check the decoded count matches the declared count.** A mismatch means the
record width is wrong and every field is shifted, which produces plausible
nonsense rather than an error.

### Step 2 — Register the device

```bash
python manage.py register_device \
  --label main-gate \
  --host 192.168.77.201 \
  --name "Main Gate" \
  --timezone Asia/Kathmandu \
  --serial <read from Menu > Info > Device>
```

`--timezone` decides which day every punch lands on. Asia/Kathmandu is +05:45,
so getting it wrong misfiles the entire import with nothing raising. The serial
is only needed for the PUSH path (§5); the SDK pull works without it.

### Step 3 — Pull the roster (32 users), write nothing yet

```bash
python manage.py device_sync --device main-gate --roster-only --dry-run
python manage.py device_sync --device main-gate --roster-only
```

Expect 32 enrolments, IDs exactly as on the terminal.

*(`golive_import` does this step and Steps 6–7 in one pass. The individual
commands are here for when something needs isolating.)*

### Step 4 — Prove no ID was altered, before mapping

**Read the ID list off the terminal's own menu**, then:

```bash
python manage.py verify_biometric_ids --expect <that list>
```

Must report **EXACT MATCH**. Read the list from the device, not from the OMS —
checking the OMS against the OMS is circular, and this is the check the whole
identity requirement rests on.

### Step 5 — HR maps the 32 enrolments

```bash
python manage.py roster_report --csv mapping.csv
```

Every `AMBIGUOUS` row decided by a person. Two staff with the same name both
score 1.0, and the tool refuses to call that a match precisely because it is a
coin flip.

Re-run `verify_biometric_ids` — PASS, zero conflicts.

### Step 6 — Back up, then import the history

```bash
sh deploy/backup.sh && python manage.py backup_verify     # the only rollback point

python manage.py device_sync --device main-gate --since 2026-07-14 --dry-run
python manage.py device_sync --device main-gate --since 2026-07-14
```

The dry run reports exactly what the real run will do. The import is idempotent
— re-running creates nothing — so it is safe to repeat if interrupted.

10,900 records is comfortably within range; they are chunked at
`BIOMETRIC_MAX_BATCH` (500) and deduplicated by a database constraint.

### Step 7 — Validate

```bash
python manage.py import_plan --from 2026-07-14        # must show zero blockers
python manage.py process_punches
python manage.py validate_import --from 2026-07-14
python manage.py verify_biometric_ids                 # still an exact match
```

Then **reconcile one employee's full month by hand** against the device log.
Not optional: every automated check can pass on systematically wrong data.

### Step 8 — Turn on continuous collection

Already in `deploy/crontab`, every 5 minutes:

```
*/5 * * * * cd /app && python manage.py device_sync
```

It re-reads from the newest stored punch (less a 2-day overlap) rather than the
whole log, so it stays cheap forever. A missed cycle needs no catch-up — the
next one collects it. No manual step, ever.

For **second-level** delivery instead of 5-minute, use PUSH (§5).

### Step 9 — The gate

Someone punches. **Watch it happen.** Then:

```bash
python manage.py verify_realtime_punch --device-user-id 28    # Chetan
python manage.py golive_status
```

`verify_realtime_punch` traces the full chain and reports a verdict per link:

```
Punch received · Arrival latency · Enrolment known · Device ID unchanged
Punch attributed · Attendance derived · Attendance source
Dashboard · Reports · Analytics
```

It prints `CHAIN COMPLETE`, **not** "verified" — because a row produced by a
real finger and one produced by a simulated punch are identical, and no query
can tell them apart. Only you can. That is the last signature.

---

## 2. The two bugs that would have broken this import

Both were found by tests and experiment, not inspection. Both are on the SDK
path you are about to use.

### 2.1 The TCP framing magic was byte-swapped

`wrap_tcp()` packed `0x5050, 0x827D`, which little-endian lands on the wire as
`50 50 7d 82`. Proven by experiment against your actual device:

| Header sent | Device response |
|---|---|
| `50 50 82 7d` (correct) | Accepted, connection held |
| `50 50 7d 82` (the old bug) | **`ConnectionResetError`** |

Every outgoing packet was malformed. Fixed and pinned by a test.

### 2.2 Record-width inference picked the smallest divisor

Attendance records are 8, 16 or 40 bytes — and **8 divides all three**. The
original logic picked the smallest divisor that fit, so every modern terminal's
log would have been decoded at the wrong stride.

That does not raise. It returns hundreds of **fabricated punches attributed to
device user `0`**, with real-looking timestamps. Against your 10,900 records it
would have produced roughly 54,500 invented rows.

Fixed: the width is now derived from the record count the device reports
(`CMD_GET_FREE_SIZES`), with a plausibility check behind it and a hard refusal
if neither settles it.

---

## 3. Identity — the requirement, and how it is enforced

Device User ID 28 stays `BiometricEmployee.device_user_id = "28"`. Forever.

```
fingerprint
  → device user ID 28                      (terminal)
  → zk_client.parse_users / parse_attendance  (decoded, NOT transformed)
  → collector hands "28" to ingest         (a pipe; it does not rewrite)
  → employee_device_id = "28"              (AttendancePunch, written once)
  → device_user_id = "28"                  (BiometricEmployee, immutable)
  → mapped employee                        (the ONLY thing HR chooses)
  → Attendance
```

- No translation layer, no renumbering step, no OMS-side ID generation.
- `007` keeps its leading zeros; `NIF007` is not coerced to a number. Both tested.
- `BiometricEmployee.save()` **raises** on any attempt to change
  `device_user_id` or `device` — so the API, the Django admin, a shell session
  and every management command are covered by one rule. This closed a real
  hole: the field was writable via PATCH.
- A device ID legitimately reassigned to a new hire is handled by retiring the
  old mapping with a validity window, never by overwriting it. That is what
  stops a leaver's history being handed to their replacement.

21 tests cover this specifically.

---

## 4. What "automatic forever" rests on

| Failure | What absorbs it |
|---|---|
| OMS restart | `restart: unless-stopped` on every container; the device keeps its log regardless |
| Server restart | Same; the next cron cycle collects whatever was missed |
| Collector restart | There is no separate collector process to restart — it is a management command |
| Network outage | The terminal retains its records; the next sync re-reads them |
| Device reconnect | Nothing to do; the log is read from the device each cycle |
| A missed cron cycle | The next one collects it — no catch-up logic exists because none is needed |

The one thing that must be watched is the job itself. `DEVICE_SYNC` is
registered as a **critical** heartbeat: if it stops running, `/monitoring` goes
red and an alert fires. That matters more than it sounds — a stopped collector
produces no errors and no broken pages. Everyone simply appears not to have come
to work, and the first person to notice is whoever runs payroll.

---

## 5. Optional: second-level delivery via PUSH

The OMS also implements the ZKTeco iClock/ADMS server (`/iclock/`). If you point
the terminal at it (Menu > Comm > Cloud Server), punches arrive **seconds** after
the finger lands instead of within 5 minutes, and the device buffers through
outages by itself.

Both paths feed the same ingest functions and share the same dedup constraint,
so running both cannot double-count.

Three things to know before enabling it:

1. **The protocol has no authentication.** A device identifies itself with
   `?SN=<serial>` and nothing else; the firmware has no key to send. Devices are
   allow-listed by serial, `BIOMETRIC_PUSH_ALLOWED_IPS` restricts source IPs,
   and the network is the real boundary. Keep `/iclock/` off the internet.
2. **`OK` tells the device to delete its copy**, so the server returns OK only
   after a durable commit and non-2xx on any doubt. Seven tests exist solely to
   hold that line.
3. Two deployment settings fail *silently* if missed, both now fixed and pinned
   by tests: `/iclock/` must be exempt from `SECURE_SSL_REDIRECT` (the device
   cannot follow a redirect to TLS), and nginx must route `/iclock/` (otherwise
   it hits the SPA fallback and the device gets **200 + HTML**, which firmware
   reads as success before deleting the punches it just sent).

Also add the server's LAN IP to `DJANGO_ALLOWED_HOSTS` — the device connects by
IP, so a missing entry gives 400 DisallowedHost and silence.

Verify without waiting for the device:

```bash
curl -s "http://<oms-host>/iclock/cdata?SN=<serial>&options=all"
```

---

## 6. Rollback

Restore the backup from Step 6. The import writes `AttendancePunch` and
`Attendance` rows; there is no partial undo, which is why the backup comes first.

To stop collection without a deploy: set the device `is_active = False`.

### Triggers — no debate

- Attendance recorded incorrectly and not correctable in place
- Employees cannot sign in
- Punches arriving but not becoming attendance, queue growing
- Any suspected data loss

---

## 7. Still open

| # | Item | Owner |
|---|---|---|
| **1** | **A real fingerprint verified end to end inside the OMS** | You — requires watching a punch |
| 2 | Steps 1–8 above, run on a host on the device's network | Admin |
| 3 | Read the enrolled-ID list off the terminal for Step 4 | Admin, on site |
| 4 | HR mapping of all 32 enrolments | HR |
| 5 | Human UAT (`docs/UAT.md`, 80 cases) | HR + Admin |

---

## 8. Verdict

**The software is complete and the pipeline is closed end to end inside the
OMS.** The path your device actually speaks is implemented and its two real bugs
are fixed; identity preservation is enforced at the model layer and tested;
first-contact diagnostics exist so a firmware surprise costs one round trip; and
the verification tooling will prove the chain the moment a real punch happens.

**Go-live is not declared, and the project is not complete.** One criterion
remains. It is the one that matters most, and it needs a finger.
