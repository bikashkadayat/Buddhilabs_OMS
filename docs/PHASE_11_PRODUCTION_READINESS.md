# Phase 11 — Production Readiness Audit & Hardening Plan

**Status:** Audit complete. Deliverables for approval. **No code written.**
**Method:** every finding below was read out of the codebase, not inferred. File
and line references are given so each can be verified independently.

---

## 0. Verdict

The system is **substantially production-ready and has been built with unusual
care for security**. DRF fails closed, the device ingest path is HMAC-signed,
public `/media/` was already replaced with signed URLs, the audit log is
immutable at the model level, and boot-time guards refuse to start a production
process configured insecurely.

Four issues stand between this and go-live, and they cluster in two places:
**rate-limit identity** (which makes the login endpoint effectively unlimited)
and **backups** (which currently protect the database and nothing else).

| Severity | Count | Go-live blocking? |
|---|---|---|
| High | 4 | **Yes** — all four |
| Medium | 10 | 3 blocking, 7 first-month |
| Low | 6 | No — backlog |

Nothing found requires a business-logic change. Every fix is configuration,
middleware, a management command, or an operational procedure.

---

## 1. Production Audit Report — the twelve areas

### 1.1 Authentication — **Good, with one hole**

Verified in `users/token_serializers.py`, `users/authentication.py`,
`config/settings.py`.

✅ Unified email login; identical error for unknown email and wrong password (no
enumeration through the response body).
✅ Access token 30 min, refresh 7 days, `ROTATE_REFRESH_TOKENS` +
`BLACKLIST_AFTER_ROTATION` — a stolen refresh token is single-use and revocable.
✅ Logout blacklists the refresh token (`LogoutView`).
✅ `SafeTokenRefreshView` re-checks `is_active` on refresh, so a deactivated
account cannot mint new access tokens.
✅ Deactivated-account login is refused **and audited**.
✅ Password validators include `CommonPasswordValidator` and
`NumericPasswordValidator`; `must_change_password` forces a first-login change.

❌ **No account lockout and no failed-login audit record** (H2).
❌ The only rate limit protecting login is `anon: 20/min`, which is bypassable
(H1).

### 1.2 Authorization — **Strong**

✅ `DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]` — endpoints are private
unless they explicitly opt out. Only `/health/`, login and refresh do.
✅ Role scoping has **one** implementation, `dashboard_views.scoped_employees`,
reused by the Phase 9 dashboards, report scoping and the Phase 10 analytics
scope. Four consumers, one rule.
✅ Object-level scoping is applied in `get_queryset`, not after fetch — a
department head's `ReportRun` list is filtered at the database.
✅ Report scope is **injected server-side** (`ReportsViewSet._scoped_params`), so
editing the request body cannot widen an export.
✅ Phase 10 adds a mechanical test that no analytics response contains any user
id, name or employee id.

No authorization defects found.

### 1.3 Attendance workflows — **Sound**

✅ Status derivation has a single seam (`services.recompute_status` →
`policy.engine.evaluate`); browser check-in, biometric derivation and revert all
pass through it.
✅ Source precedence HR > Biometric > Browser is enforced in the engine, so an
HR correction survives re-derivation.
✅ Corrections snapshot `previous_*` columns, making the workflow reversible —
the only route back from an HR-locked row.
✅ A partial unique index allows one open correction per employee-day while
permitting a day to be corrected again later.
✅ `WORK_FROM_HOME` is excluded from `MANUAL_STATUSES`, so it cannot be typed in
to bypass "approval alone is not attendance".

⚠️ Correction attachments are unvalidated (M2).

### 1.4 Biometric ingestion — **Well designed**

Verified in `biometric/authentication.py`, `throttling.py`, `views.py`.

✅ HMAC-SHA256 over method + path + timestamp + body digest. Method and path are
in the signing string, so a `/punch/` signature cannot be replayed at
`/bulk-sync/`.
✅ `hmac.compare_digest` — constant-time comparison.
✅ `request.user` deliberately stays anonymous; the device lands in
`request.auth`, so it can never inherit a User's permissions.
✅ Per-device throttle scope, so one chatty terminal cannot starve the others.
✅ Punch ingest is idempotent via a unique constraint, so a replayed batch
creates zero duplicates.
✅ `BIOMETRIC_BROADCAST_LIMIT` prevents an 11k-punch backlog sync from being
replayed into every open browser.

⚠️ Replay protection is a ±300s window only — there is no nonce cache (M8).
⚠️ The HMAC secret is `sha256(raw_key)`, so a database read yields forgery
capability. Already documented honestly in the module docstring (L1).

### 1.5 Workforce management — **Sound**

✅ Correction workflow is fully audited via `workforce/services.py`.
✅ WFH approval stays HR-only; managers see the queue but cannot act.
✅ Conflict detection is read-only — no leave is refunded automatically.
✅ Comp-off earn is idempotent under re-derivation (partial unique constraint).

### 1.6 Reports — **Good, one audit gap**

✅ Async generation, signed expiring download URLs (600s TTL), retention purge,
scheduled email delivery.
✅ Failures are captured onto the `ReportRun` row rather than lost.
✅ Department heads see only their own runs — someone else's file may cover
employees outside their scope.

❌ **Download is not audited** (M3) — only the request is.
⚠️ Generation runs in a raw thread; a restart mid-generation strands the run
(M4).

### 1.7 Analytics — **Clean** (Phase 10, just shipped)

✅ Read-only. The only rows it creates are `ReportRun` records for exports.
✅ Cache keys hash *scope*, not user id, and carry a code version and a
generation counter.
✅ No individual data, enforced by test.
✅ Kill switch (`ANALYTICS_ENABLED`) disables the whole layer with no deploy.
✅ Measured 27–48 ms cold, 1 query warm.

### 1.8 Scheduled jobs — **Works, but under-monitored**

There is **no Celery**. Background work is (a) `cron` in a dedicated container
and (b) raw Python threads for reports and notification email.

✅ 11 cron jobs; `TZ=Asia/Kathmandu` is set on the container (a `CRON_TZ` line
would not work under vixie-cron — correctly noted in the compose file).
✅ Jobs are idempotent and safe to re-run.

❌ **Only 4 of 11 jobs report a heartbeat** (M5). `process_punches`,
`check_device_health`, `run_scheduled_reports`, `purge_expired_reports`,
`send_weekly_digest`, `reconcile_approval_notifications` and `backup.sh` write
no audit event, so a silently dead job is invisible.
❌ **No healthcheck on the `cron` container** (M10) — if the daemon dies, the
container stays "up" and every scheduled job stops.
❌ Thread-based report/email work has no retry and no dead-letter (M4).

### 1.9 Redis — **Correctly treated as optional**

✅ `ResilientRedisCache` degrades to a miss rather than raising, so a Redis
outage cannot take the API down through the throttle path.
✅ Cache and channel layer are on **separate database numbers** — `FLUSHDB` on
one cannot wipe the other.
✅ AOF `everysec`, `noeviction`, channel capacity and expiry bounded.
✅ Redis is deliberately excluded from the liveness probe.

⚠️ `maxmemory 256mb` + `noeviction`: sustained channel-layer growth causes hard
write failures rather than degradation (L6). Needs an alert, not a policy change
— silently dropping channel keys would be worse.

### 1.10 Database — **Solid**

✅ 12-factor configuration; no hardcoded credentials; fails closed on a missing
password in production.
✅ `CONN_MAX_AGE=60` connection reuse.
✅ Indexes are present on every hot path; the Phase 10 audit found no
sequential-scan risk needing new indexes.
✅ Migration drift is checked in CI (`makemigrations --check`).
✅ `pg_isready` healthcheck gates dependent containers.

⚠️ Migrations run in the container `CMD` on every boot. Fine for a single host;
worth a documented pre-deploy backup step (see §3).

### 1.11 Backups — **The weakest area**

✅ `pg_dump -F c` → gzip → AES-256 (PBKDF2, salted) → off-host, with local
retention rotation.
✅ `restore.sh` restores into a **scratch database** by default so a restore can
be verified before promotion. That is the right default.
✅ Runs daily at 01:30 from the cron container.

❌ **`media_data` is not backed up** (H3). It holds memo attachments and
vouchers, correction evidence, profile photos and generated reports. Losing that
volume loses every uploaded document permanently — the database only holds paths.
❌ **No verification and no failure alerting** (H4). `BACKUP_ENCRYPTION_KEY` is
optional in `docker-compose.prod.yml` (`${BACKUP_ENCRYPTION_KEY:-}`) but
mandatory in `backup.sh` (`:?`), so an unset key means the job aborts **every
night, silently**, and the first anyone learns of it is a restore that has
nothing to restore.
❌ No off-host target is enforced — the script warns to stdout and continues
LOCAL ONLY, which is not a backup.
❌ No documented, rehearsed restore drill.

### 1.12 Docker deployment — **Good topology, ordinary hardening gaps**

✅ Nothing published to the host; the ingress reaches containers by name over an
external network, so Gunicorn is unreachable from outside Docker.
✅ Pinned base images, no build tools in the runtime layer, no secrets baked in.
✅ Named volumes survive redeploys; healthchecks gate `depends_on`.
✅ ASGI via UvicornWorker under Gunicorn — one process serves HTTP and
WebSockets.
✅ The `/health/` SSL-redirect exemption is anchored so it cannot leak to
`/health/detailed/`.

❌ **Containers run as root** — no `USER` directive (M6).
❌ No memory/CPU limits and no Docker log rotation — a log flood fills the disk
(M6).

---

## 2. Security Audit — findings by severity

### HIGH — all four block go-live

---

**H1 · Rate limiting is bypassable; login is effectively unlimited**

`NUM_PROXIES` is not set anywhere in the project. With it unset, DRF's
`BaseThrottle.get_ident` returns the **whole `X-Forwarded-For` header** as the
throttle identity. That header is attacker-controlled, so rotating it gives a
fresh 20/min bucket on every request.

*Consequence:* unlimited password guessing against `/api/v1/auth/login/`, and
every other anonymous limit is decorative.

*Fix:* set `NUM_PROXIES` to the real hop count so DRF reads the correct position
in the header, and add a dedicated `login` scope throttle (e.g. 5/min per IP,
20/hour per email). Both are settings changes.

---

**H2 · Failed logins are neither recorded nor limited**

`EmailLoginView` audits `LOGIN_SUCCESS` and `LOGIN_BLOCKED_INACTIVE` but writes
nothing on a wrong password. There is no lockout or backoff.

*Consequence:* with H1, an attacker can brute-force at full speed and leave no
forensic trace. After a breach there is no way to answer "when did this start".

*Fix:* audit `LOGIN_FAILED` with the attempted email and client IP; add
progressive lockout (N failures → temporary block) with an HR unlock path.

---

**H3 · Uploaded files and generated reports are not backed up**

`backup.sh` dumps PostgreSQL only. The `media_data` volume — memo attachments
and vouchers, correction evidence, profile photos, generated report files — has
no backup at all.

*Consequence:* a volume loss is unrecoverable. Worse, the database still holds
the file paths, so the system appears intact while every download 404s.

*Fix:* add a media backup leg (tar → same encryption → same off-host target),
weekly full plus daily incremental. Detailed in §3.

---

**H4 · Backups are unverified, and their failure is silent**

Three compounding problems: `BACKUP_ENCRYPTION_KEY` is optional in compose but
required by the script (so a missing key = nightly silent failure); no off-host
target is enforced; and nothing ever verifies a dump is restorable.

*Consequence:* the most likely disaster-recovery outcome today is discovering at
restore time that there is nothing to restore.

*Fix:* `backup_verify` management command (decrypt → restore into scratch →
count rows → drop), a `SYSTEM_BACKUP_OK` audit heartbeat, an alert when the
heartbeat is older than 26 hours, and a startup guard that refuses to run the
backup job without both a key and an off-host target.

---

### MEDIUM

**M1 · Audit log records the proxy's IP, not the client's.**
`audit/services.log_action` reads `REMOTE_ADDR`. nginx correctly sets
`X-Forwarded-For` and `X-Real-IP`, but nothing reads them. Every audit row
therefore carries the same useless address, which defeats the point of storing
an IP. *Blocking* — an audit trail that cannot identify origin is not an audit
trail. Fix alongside H1 (same trusted-proxy configuration).

**M2 · Correction attachments are unvalidated and served without sniff
protection.** `memos/serializers.py` sniffs magic bytes, enforces an extension
allowlist and a size cap. `AttendanceCorrectionRequest.attachment` (Phase 9) has
**none of the three**, and its download in `corrections.py:157` returns a plain
`FileResponse` without the `X-Content-Type-Options: nosniff` and
`Content-Security-Policy: sandbox` headers that `ProtectedMediaView` sets.
`as_attachment=True` and nginx's `client_max_body_size 25m` limit the damage.
*Blocking* — reuse the memo validator and add the headers.

**M3 · Report downloads are not audited.** The *request* is logged; the
*download* is not. For payroll-relevant exports, who took the file matters more
than who asked for it. *Blocking* — one `log_action` call in the download action
and in `ProtectedMediaView`.

**M4 · Thread-based background work has no recovery.** `_spawn_generation` runs
report generation in a daemon thread. A container restart mid-generation leaves
`ReportRun` in `generating` forever; there is no retry, timeout or dead-letter.
Same pattern for notification email. *Fix:* a `reap_stuck_reports` cron job that
fails runs stuck over N minutes, plus an alert.

**M5 · Cron heartbeat covers 4 of 11 jobs.** `health_views.CRON_EVENTS` lists
four events; seven jobs — including the backup — report nothing.

**M6 · Container hardening gaps.** Runs as root, no resource limits, no Docker
log rotation.

**M7 · Signed media URLs are unbound bearer capabilities.** The HMAC covers
`name|exp` only, not the requesting user. A URL leaked via browser history, a
chat paste or a referrer works for anyone until it expires (default 3600s; 600s
for reports). *Fix:* bind the user id into the signature, and shorten the
default TTL to 300s.

**M8 · Replay window on device requests.** A captured signed request can be
replayed within ±300s. Punch ingest is idempotent so the impact is nil there;
roster-sync is not obviously so. *Fix:* a Redis nonce cache keyed on the
signature, TTL = the skew window.

**M9 · No error tracking, and application logs are being discarded.**
`LOGGING.loggers` names only `memos`, `leaves` and `audit` at `LOG_LEVEL`;
everything else falls to the root logger at **WARNING**. So every `logger.info`
in `biometric`, `attendance`, `analytics` and `config.cache` is dropped —
including "Cache unavailable, degrading" and "WebSocket connected". There is no
Sentry or equivalent.

**M10 · No healthcheck on the `cron` container.** A dead cron daemon leaves the
container reporting healthy while every scheduled job silently stops.

### LOW — backlog, not go-live

| | Finding |
|---|---|
| L1 | Device HMAC secret is `sha256(raw_key)`; a DB read yields forgery. Asymmetric signing would fix it — a collector-side change. Already documented in code. |
| L2 | WebSocket authorises at connect only; a socket opened before deactivation stays open. The socket is read-only, so exposure is limited to the stream. |
| L3 | Login timing side channel — the unknown-email path returns before any password hash runs. |
| L4 | `collectstatic --clear` on every boot briefly empties static files. |
| L5 | `SECURE_HSTS_PRELOAD` defaults on — a commitment that is slow to reverse. Make it a conscious decision. |
| L6 | Redis `maxmemory 256mb` + `noeviction` fails writes hard under pressure. Correct policy; needs an alert. |

### What is already right — recorded so it is not "fixed" away

Fail-closed DRF defaults · boot guards that refuse insecure production config ·
signed media replacing the public `/media/` catch-all · CSP + nosniff on media
responses · immutable `AuditLog` (raises on update and delete) · HMAC device
auth with method/path binding and constant-time compare · WebSocket token in the
subprotocol rather than the query string (keeps JWTs out of nginx access logs) ·
resilient cache · separate Redis databases · CI running ruff, pytest with
coverage, migration-drift check, `pip-audit` and `npm audit`.

---

## 3. Backup Strategy

### 3.1 What must survive

| Asset | Where | Today | Proposed |
|---|---|---|---|
| PostgreSQL | `postgres_data` | ✅ daily encrypted | unchanged + **verified** |
| Uploaded media | `media_data` | ❌ **none** | daily incremental, weekly full |
| Generated reports | `media_data/reports` | ❌ none | covered by media (30-day retention anyway) |
| Redis | `redis_data` | ❌ none | **deliberately none** — cache + channel layer are reconstructible |
| Collector spool | collector host (separate repo) | ❌ none | documented procedure; the spool is a replay buffer, and punch ingest is idempotent |
| `.env` secrets | host filesystem | ❌ none | **offline** copy in a password manager, never in the backup |

**Redis is deliberately excluded.** Restoring stale throttle counters and dead
channel keys has no value; AOF already covers restart survival.

**The `.env` file is deliberately excluded from automated backup.** It holds
`DJANGO_SECRET_KEY`, the database password and `BACKUP_ENCRYPTION_KEY` — putting
it in a backup encrypted with a key it contains is circular. It belongs in a
password manager, verified as part of the go-live checklist.

### 3.2 Schedule

| Job | When | Retention |
|---|---|---|
| Database full | daily 01:30 | 14 local / 90 off-host |
| Media incremental | daily 02:15 | 14 local / 30 off-host |
| Media full | Sunday 02:45 | 4 weekly / 6 monthly off-host |
| **Backup verify** | daily 03:15 | result audited, not stored |
| **Backup heartbeat check** | hourly | alerts if > 26h stale |

### 3.3 Integrity

Every artifact gets a SHA-256 written beside it and re-checked after transfer.
`backup_verify` performs a **real restore** into a scratch database, asserts row
counts on `users_user`, `attendance_attendance` and `audit_auditlog` are
non-zero and within 20% of production, then drops the scratch database. A backup
that has never been restored is a hypothesis.

### 3.4 Backup checklist (operator, monthly)

- [ ] Latest DB artifact exists off-host and is < 26 hours old
- [ ] Latest media artifact exists off-host
- [ ] `SYSTEM_BACKUP_OK` audit event within the last 26 hours
- [ ] `SYSTEM_BACKUP_VERIFIED` audit event within the last 26 hours
- [ ] Checksums match between local and off-host copies
- [ ] `BACKUP_ENCRYPTION_KEY` is recoverable from the password manager by a second person
- [ ] Off-host storage has ≥ 90 days of headroom
- [ ] One full restore drill completed this quarter (§4.5)

---

## 4. Recovery Strategy

**Targets:** RPO ≤ 24 h (DB and media) · RTO ≤ 2 h (full host loss) · RTO ≤ 15 min
(single container).

### 4.1 Recovery scenarios

| # | Scenario | Procedure | Target | Data loss |
|---|---|---|---|---|
| 1 | Container restart | `docker compose restart <svc>`; healthcheck confirms | 2 min | none |
| 2 | API outage | Check `/health/`; restart backend; roll back image if the last deploy caused it | 15 min | none |
| 3 | Redis outage | **No action required** — cache degrades to no-cache, analytics recompute, throttles fail open, live dashboards fall back to 20s polling. Restart when convenient | — | none |
| 4 | Worker/cron restart | Restart `cron`; jobs are idempotent, re-run manually if a window was missed | 10 min | none |
| 5 | Collector outage | Punches spool on the collector; on reconnect the backlog syncs and `process_punches` derives. `check_device_health` flags the device offline within 15 min | device-dependent | none |
| 6 | Database restore | §4.3 | 60 min | ≤ 24 h |
| 7 | Media loss | Restore the media artifact into the volume, then reconcile orphans | 45 min | ≤ 24 h |
| 8 | Full host loss | §4.4 | 2 h | ≤ 24 h |

### 4.2 Ordering rule

Restore **database and media together, from the same night**. Restoring a newer
database against older media produces rows pointing at files that do not exist —
the failure mode is a 404 on every attachment, which reads as a bug rather than
a partial restore.

### 4.3 Database restore runbook

1. Announce downtime; stop `backend` and `cron` (leave `db` running).
2. `restore.sh <artifact>` → restores to `<name>_restore` scratch DB.
3. Verify: row counts, latest attendance date, latest audit entry.
4. Only then promote: rename production aside, rename scratch in.
5. Start `backend`; confirm `/health/` and `/health/detailed/`.
6. Spot-check one login, one attendance record, one report download.
7. Record the incident and the data-loss window in the audit log.

Step 4 is deliberately last. `restore.sh` defaults to a scratch database
precisely so a bad artifact cannot destroy a working production database.

### 4.4 Full host rebuild

1. Provision host, install Docker, restore `.env` **from the password manager**.
2. `docker network create nifn-shared` if the ingress stack is not up.
3. `docker compose -f docker-compose.prod.yml up -d db redis`.
4. Restore DB (§4.3) and media from the same night.
5. Start `backend`, `cron`, `web`.
6. Re-issue biometric device API keys (raw keys are not recoverable by design)
   and update each collector.
7. Full smoke test per the go-live checklist.

**Step 6 is the one people forget.** Device keys are stored hashed, so a rebuild
means re-onboarding every terminal.

### 4.5 Recovery drill (quarterly, on staging)

Restore latest DB + media to staging, verify, log the wall-clock time, record
what was unclear, update this document. A runbook that has never been executed
is a draft.

---

## 5. Monitoring Architecture

### 5.1 Approach

Extend the existing `/health/detailed/` rather than introduce Prometheus and
Grafana. The reasoning: this is a single-host deployment for one organisation
with no dedicated ops team, and a monitoring stack nobody maintains is worse
than a dashboard inside the app people already log into. If the deployment ever
goes multi-host, the metrics below are already shaped for a `/metrics` exporter.

Two surfaces:

1. `GET /api/v1/monitoring/health/` — machine-readable, Admin+HR, JSON.
2. `/monitoring` — a React page rendering it, with red/amber/green per subsystem.

### 5.2 Metrics

| Domain | Metric | Green | Amber | Red |
|---|---|---|---|---|
| **Database** | reachable | yes | — | no |
| | connection latency | < 50 ms | < 200 ms | ≥ 200 ms |
| | disk free | > 20% | > 10% | ≤ 10% |
| **Redis** | reachable | yes | not configured | error |
| | memory used | < 60% | < 85% | ≥ 85% |
| **API** | 5xx rate (15 min) | < 0.1% | < 1% | ≥ 1% |
| | p95 latency | < 300 ms | < 1 s | ≥ 1 s |
| **WebSocket** | active connections | any | — | — |
| | channel-layer reachable | yes | — | no |
| **Devices** | online / total | all | any offline | all offline |
| | max time since last punch | < 30 min | < 2 h | ≥ 2 h |
| **Queues** | unprocessed punches (mapped) | < 50 | < 500 | ≥ 500 |
| | unmapped punches | 0 | < 20 | ≥ 20 |
| | failed sync batches (24 h) | 0 | < 5 | ≥ 5 |
| | reports stuck in `generating` | 0 | 1 | ≥ 2 |
| **Cron** | each of 11 jobs, time since last success | within window | 1.5× window | 2× window |
| **Backup** | time since `SYSTEM_BACKUP_OK` | < 26 h | < 48 h | ≥ 48 h |
| | time since `SYSTEM_BACKUP_VERIFIED` | < 26 h | < 48 h | ≥ 48 h |

`awaiting_derivation` and `awaiting_mapping` stay separated — the existing code
already does this, and it is right: unmapped punches are blocked on an HR
decision, not a stuck worker, and conflating them makes a normal queue look like
an outage.

### 5.3 Cron heartbeats

Every management command wraps its work in a `SYSTEM_<JOB>_OK` audit event on
success. `CRON_EVENTS` grows from 4 to 11 entries, each with its expected
interval, so "last run" becomes "last run, and is that late". A job that has
never run reads as red, not as missing.

### 5.4 Logging

Add `analytics`, `attendance`, `biometric`, `reports`, `config` and `users` to
the named loggers so their INFO output stops being discarded at the root
WARNING threshold. Add optional Sentry (`SENTRY_DSN` unset = disabled), scrubbing
`password`, `token`, `authorization` and `api_key`.

---

## 6. Alerting Architecture

### 6.1 Delivery

Email to an ops distribution list, reusing the notification machinery already in
the codebase, plus an optional webhook (`ALERT_WEBHOOK_URL`) for Slack. No new
dependency, no new service.

**Deduplication is mandatory.** An offline device would otherwise send an alert
every five minutes for a weekend. One alert on state entry, one on recovery,
and a daily digest while a condition persists.

### 6.2 Alert catalogue

| Alert | Trigger | Severity | Route |
|---|---|---|---|
| Device offline | no contact > `BIOMETRIC_DEVICE_OFFLINE_MINUTES` | Warning | HR + Admin |
| All devices offline | every active device offline | **Critical** | Admin + on-call |
| Sync failure | ≥ 3 failed batches in 1 h | Warning | Admin |
| Mapping backlog | ≥ 20 unmapped, or any unmapped > 48 h | Warning | HR |
| Queue growth | unprocessed punches ≥ 500, or rising 3 checks running | **Critical** | Admin |
| Database unreachable | `/health/` red twice consecutively | **Critical** | Admin + on-call |
| Disk low | free ≤ 10% | **Critical** | Admin |
| Redis down | round-trip fails 3 consecutive checks | Info | Admin |
| Redis memory | ≥ 85% of maxmemory | Warning | Admin |
| **Backup failure** | no `SYSTEM_BACKUP_OK` in 26 h | **Critical** | Admin + on-call |
| **Backup unverified** | no `SYSTEM_BACKUP_VERIFIED` in 26 h | **Critical** | Admin |
| Report failure | any `ReportRun` failed, or stuck > 15 min | Warning | Admin |
| Cron job late | any job > 2× its interval | Warning | Admin |
| Auth anomaly | ≥ 20 failed logins for one account in 15 min | Warning | Admin |
| Login spike | ≥ 200 failed logins system-wide in 15 min | **Critical** | Admin |

Redis down is deliberately **Info**: the system is designed to survive it, so
paging someone at 03:00 would train them to ignore alerts.

---

## 7. Performance Audit

### 7.1 Measured (Phase 8–10, in-suite)

| Surface | Budget | Measured |
|---|---|---|
| Employee dashboard | ≤ 12 queries | passing |
| Manager dashboard | ≤ 15 queries | passing |
| HR command centre | ≤ 20 queries | passing |
| Analytics × 10 endpoints | 4–25 queries | passing |
| Analytics cold latency | < 250 ms | **27–48 ms** (SQLite fixture) |
| Analytics warm | 1 query | passing |
| Policy resolution | cached per request | 27.5× improvement measured in Phase 8 |

### 7.2 Not yet measured — the Phase 11 benchmark work

No production-representative benchmark exists. Proposed: a
`benchmark_production` command seeding **250 employees × 24 months** on
PostgreSQL, reporting p50/p95/p99 for attendance APIs, workforce APIs, analytics,
report generation, exports, dashboard composites and WebSocket fan-out.

Proposed budgets (p95, PostgreSQL): read APIs 300 ms · check-in/out 400 ms ·
dashboards 500 ms · analytics 250 ms · Excel/CSV export 5 s · PDF export 15 s ·
WebSocket fan-out 500 ms · bulk punch ingest (500 punches) 3 s.

**I will not publish numbers I have not measured.** The table above marks
exactly which figures exist today and which are targets to be established.

---

## 8. Audit Coverage Report

| Operation | Audited? | Where |
|---|---|---|
| Attendance correction (submit/approve/reject/revert) | ✅ | `workforce/services.py` |
| WFH approval / rejection | ✅ | `policy/views.py` |
| Comp-off confirm / reject | ✅ | `leaves/comp_views.py` |
| Leave approval / rejection / bulk | ✅ | `leaves/views.py`, `services.py` |
| Employee ↔ device mapping | ✅ | `biometric/services.py` |
| Attendance override (manual HR entry) | ✅ | `attendance/views.py` |
| Policy create / update / delete | ✅ | `policy/views.py` |
| Shift + assignment changes | ✅ | `policy/views.py` |
| Report **request** | ✅ | `reports/views.py` |
| **Report download** | ❌ **M3** | — |
| **Media / attachment download** | ❌ **M3** | — |
| Admin user CRUD, activate/deactivate | ✅ | `users/admin_views.py` |
| Password change | ✅ | `users/views.py` |
| Login success | ✅ | `token_serializers.py` |
| Login blocked (inactive) | ✅ | `token_serializers.py` |
| **Login failure** | ❌ **H2** | — |
| Logout | ✅ | `token_serializers.py` |
| Inventory assignment / take-out | ✅ | `inventory/views.py` |
| Memo workflow | ✅ | `memos/services.py` |
| Cron job execution | ⚠️ 4 of 11 | `health_views.CRON_EVENTS` |
| Backup success / failure | ❌ **H4** | — |

**Coverage: 18 of 22 critical operations.** Every gap is one `log_action` call
except the cron and backup heartbeats.

Two cross-cutting defects apply to all 18 that *are* covered:
- **M1** — the recorded IP is the proxy's, so origin is unusable.
- No retention policy on `AuditLog`. It grows forever; a documented retention
  (e.g. 7 years, matching employment-record norms) plus an archival export
  should be a conscious decision rather than an accident.

---

## 9. UAT Plan

Six suites, **68 cases**, executed on staging with production-shaped data.
Sign-off requires: zero Critical open, ≤ 2 Major open with owners, HR and Admin
sign-off recorded.

| Suite | Cases | Owner | Focus |
|---|---|---|---|
| HR | 16 | HR lead | corrections, WFH, comp-off, mapping, reports, analytics, user admin |
| Employee | 12 | 3 staff | check-in/out, leave apply, WFH request, corrections, own dashboard |
| Manager | 10 | 2 dept heads | team dashboard, approvals, department-scoped analytics, scoped exports |
| Attendance | 12 | HR + Admin | late/half-day boundaries, holidays, Saturday work, comp-off eligibility, overtime |
| Leave | 10 | HR | apply → dept head → HR, balances, half-days, conflicts, year rollover |
| Biometric | 8 | Admin | enrol, punch, derive, unmapped, offline/recovery, backlog sync, clock drift |

**Negative cases are mandatory** — every suite includes at least two: an
employee attempting a manager URL, a manager requesting another department's
export, an expired media link, an unsigned device request, a replayed batch, a
correction for a future date.

Deliverables: a test-case workbook, an execution log with evidence, a defect log
severity-ranked, and a sign-off sheet.

---

## 10. Go-Live Checklist

### 10.1 Infrastructure (Admin — T-7 days)

- [ ] `.env` complete; `DJANGO_DEBUG=False`; `DJANGO_ALLOWED_HOSTS` explicit (no `*`)
- [ ] `DJANGO_SECRET_KEY` freshly generated, never used elsewhere, stored in the password manager
- [ ] `NUM_PROXIES` set to the real hop count **(H1)**
- [ ] `BACKUP_ENCRYPTION_KEY` set **and** an off-host target configured **(H4)**
- [ ] TLS valid; HSTS decision made consciously **(L5)**
- [ ] `CORS_ALLOW_ALL_ORIGINS=False`; `CSRF_TRUSTED_ORIGINS` exact
- [ ] SMTP verified by a real send
- [ ] Disk ≥ 50 GB free; Docker log rotation configured **(M6)**
- [ ] Containers run as non-root; resource limits set **(M6)**
- [ ] Redis AOF confirmed; `nifn-shared` network exists
- [ ] One full backup taken, verified **and restored to staging**
- [ ] Monitoring reachable; alert recipients confirmed by a test alert

### 10.2 HR (T-3 days)

- [ ] All employees created with correct role, department, employment type, joining date
- [ ] Leave types, entitlement rules and balances seeded and spot-checked
- [ ] Holiday calendar loaded for the full year
- [ ] Attendance policy and shifts confirmed against written NIF rules
- [ ] `ATTENDANCE_TRACKING_START` and `ATTENDANCE_POLICY_GO_LIVE` pinned to explicit dates
- [ ] Every biometric enrolment mapped — **zero unmapped**
- [ ] One report of each type generated and checked
- [ ] Analytics figures reconciled against a manual count for one department

### 10.3 Manager (T-1)

- [ ] Each dept head can log in and reach their team dashboard
- [ ] Team roster correct; no one missing, nobody else's team visible
- [ ] Approval queues reachable; one approval end-to-end on staging

### 10.4 Employee (T-1)

- [ ] Every employee can log in and is forced through the password change
- [ ] Check-in / check-out works from a real phone on the real network
- [ ] Own attendance, leave balance and history are correct
- [ ] One-page quick guide distributed

### 10.5 Devices (T-2)

- [ ] Every terminal reachable, clock synchronised (drift < 60 s)
- [ ] API keys issued, stored offline, collectors configured
- [ ] Test punch appears within 2 minutes and derives correctly
- [ ] Offline → recovery tested: unplug, punch, replug, confirm backlog syncs
- [ ] Device health alert fires when a terminal goes quiet

### 10.6 Go-live day

- [ ] Fresh backup taken **and verified** immediately before cutover
- [ ] All health checks green
- [ ] Support contact published to staff
- [ ] Hourly monitoring for the first 8 hours
- [ ] Rollback decision point agreed in advance, with a named decision-maker

---

## 11. Proposed Phase 11 Work (on approval)

### 11.1 Code

| # | Item | Addresses | Size |
|---|---|---|---|
| 1 | Trusted-proxy config: `NUM_PROXIES` + `client_ip()` helper used by `log_action` and throttles | H1, M1 | S |
| 2 | Login throttle scope + failed-login audit + progressive lockout | H1, H2 | M |
| 3 | Media backup leg + `backup_verify` command + heartbeats | H3, H4 | M |
| 4 | Attachment validator reused for corrections + nosniff/CSP on that download | M2 | S |
| 5 | Audit report and media downloads | M3 | S |
| 6 | `reap_stuck_reports` command | M4 | S |
| 7 | Cron heartbeats for all 11 jobs; `CRON_EVENTS` expanded | M5 | S |
| 8 | Dockerfile `USER`, compose resource limits, log rotation, cron healthcheck | M6, M10 | S |
| 9 | User-bound signed media URLs; TTL 3600 → 300 | M7 | S |
| 10 | Device nonce cache | M8 | S |
| 11 | Logger config expanded; optional Sentry with scrubbing | M9 | S |
| 12 | `/api/v1/monitoring/health/` + `/monitoring` page | §5 | L |
| 13 | Alert engine with dedup + digest | §6 | M |
| 14 | `benchmark_production` command | §7 | M |

### 11.2 Tests

Security (throttle bypass, lockout, upload rejection, signature replay, expired
link, scope escalation) · monitoring (each metric's thresholds) · backup
(verify succeeds on good artifact, fails on corrupt) · recovery (restore into
scratch, row-count assertions) · permissions (full matrix re-run) · audit
coverage (every operation in §8 writes an entry, with the correct client IP).

### 11.3 Documentation

`docs/RUNBOOK.md` · `docs/MONITORING.md` · `docs/UAT.md` · `docs/GO_LIVE.md` ·
updates to `BACKUP.md` and `DEPLOYMENT.md`.

### 11.4 Sequencing

**Week 1 — blockers:** items 1–5 (all High plus M1/M2/M3).
**Week 2 — operability:** items 6–11.
**Week 3 — visibility:** items 12–14.
**Week 4 — validation:** UAT execution, recovery drill, go-live checklist walk.

---

## 12. Decisions needed before coding

1. **Trusted-proxy hop count.** Production runs behind Cloudflare → shared
   ingress nginx → backend. `NUM_PROXIES` must match exactly, or the fix
   re-introduces the bypass. Please confirm the chain. *This is the one item I
   cannot determine from the codebase.*
2. **Lockout policy.** Recommend 10 failures in 15 min → 15-minute lock, with an
   HR unlock. Alternative: no lockout, alerting only (avoids a
   lock-out-the-CEO-before-a-board-meeting scenario). *Recommend lockout.*
3. **Monitoring approach.** In-app dashboard as designed, or Prometheus +
   Grafana? *Recommend in-app* — no ops team to maintain a second stack.
4. **Alert transport.** Email only, or email + Slack webhook? *Recommend both.*
5. **Audit retention.** Currently unbounded. Recommend 7 years with annual
   archival export.
6. **Backup off-host target.** S3-compatible bucket or `rsync`/`scp` host? Needed
   to finish the backup work. *Recommend S3-compatible.*
7. **Media backup scope.** Everything, or exclude regenerable report files
   (30-day retention anyway)? *Recommend excluding reports* — smaller, faster
   backups and nothing irreplaceable is lost.

---

---

## 13. As built

Approved and implemented. All four HIGH findings and all ten MEDIUM findings are
closed. Below is what changed against what was proposed.

### 13.1 Findings closed

| # | Finding | How |
|---|---|---|
| H1 | Rate-limit bypass | `config/client_ip.py` + `NUM_PROXIES` bound to `TRUSTED_PROXY_DEPTH`; dedicated `login` throttle scope at 10/min |
| H2 | No failed-login record or lockout | `users/login_security.py`: `LOGIN_FAILED` / `LOGIN_LOCKED` audit events, 10-in-15-min lockout, HR unlock endpoint |
| H3 | Media not backed up | `deploy/backup_media.sh` — full weekly, incremental daily, same encryption and off-host target |
| H4 | Backups unverified, failures silent | `backup_verify` performs a real restore into a scratch DB with row-count assertions; `BACKUP_ENCRYPTION_KEY` now `:?` in compose; `BACKUP_REQUIRE_OFFHOST=1`; heartbeats on every run |
| M1 | Audit recorded the proxy IP | `log_action` uses `client_ip()` |
| M2 | Correction attachments unvalidated | `config/uploads.py` shared by memos and corrections; `harden_file_response` on that download |
| M3 | Downloads unaudited | `REPORT_DOWNLOADED`, `MEDIA_DOWNLOADED`, `CORRECTION_ATTACHMENT_DOWNLOADED` |
| M4 | Stuck reports | `reap_stuck_reports`, every 15 min |
| M5 | 4-of-11 cron coverage | `monitoring/heartbeat.py`, 15 registered jobs, all wrapped |
| M6 | Container hardening | Non-root `nifn` user, memory limits, 20 MB × 5 log rotation |
| M7 | Unbound signed URLs | Signature now covers `name\|exp\|user_id`; TTL 3600 → 300 |
| M8 | Replay window | **Closed as mitigated by design — see 13.2** |
| M9 | Logs discarded, no error tracking | 13 named loggers; optional Sentry with credential scrubbing |
| M10 | No cron healthcheck | `pgrep cron` healthcheck on the container |
| L4 | `collectstatic --clear` | `--clear` removed |

### 13.2 M8 was implemented, then deliberately reverted

The nonce cache was built exactly as designed. Four existing ingest tests failed
immediately: `test_replaying_a_valid_request_creates_no_duplicates`,
`test_full_backlog_replay_is_free_the_second_time`,
`test_duplicate_single_punch_returns_200_not_201`, `test_roster_sync_is_idempotent`.

Those tests encode how the collectors actually behave. A terminal whose response
times out **retries the same batch byte for byte**. Rejecting the retry would
break at-least-once delivery and silently lose attendance on any flaky link —
far more likely, and more damaging, than the attack a nonce prevents (which
requires having already broken TLS).

Every ingest endpoint is idempotent by database constraint, which is a *stronger*
property than replay rejection: a replay is not merely detected, it is harmless.
The finding is closed as mitigated by design, and the reasoning is recorded in
`biometric/authentication.py` so nobody re-adds it in six months.

**This is the one place where implementing the audit's recommendation made the
system worse, and the tests are what caught it.**

### 13.3 Decision #1 was not answered, so it was engineered around

The proxy hop count was the one value the codebase could not supply, and it was
not specified at approval. Rather than guess — a wrong guess re-opens H1 — the
fix is configurable, **fail-closed, and verifiable**:

* The depth is clamped to the addresses actually present, so a caller cannot
  shorten the header to promote their own value into the trusted position.
* The default (1) errs low. Too low means every client shares a throttle bucket:
  too strict, visible, harmless. Too high is the bypass.
* `verify_proxy_config --from-audit` reads addresses the audit log actually
  recorded and reports whether they look like clients or like infrastructure.

⛔ **Run it against production before go-live.** Until then the deployment is
safe but over-strict. This is on the go-live checklist as a blocking item.

### 13.4 Deliverables

| Item | Location |
|---|---|
| Runbook | `docs/RUNBOOK.md` |
| Monitoring & alerting | `docs/MONITORING.md` |
| UAT plan (68 cases) | `docs/UAT.md` |
| Go-live checklist | `docs/GO_LIVE.md` |
| Monitoring API | `/api/v1/monitoring/{health,cron,alerts}/` |
| Monitoring dashboard | `/monitoring` (HR/Admin) |
| Benchmark harness | `manage.py benchmark_production` |

### 13.5 Test results

| Suite | Result |
|---|---|
| `users/test_login_security.py` | 22 passed |
| `monitoring/` | 61 passed |
| `biometric/` | 223 passed |
| `memos/` + `documents/` | 101 passed |
| Frontend (whole suite) | **181 passed**, 26 files |
| Frontend build + lint | clean (21 pre-existing warnings) |

Three tests deserve mention because they assert things that are easy to get
wrong and easy to believe you got right:

* `test_a_truncated_header_cannot_promote_caller_input` — the H1 bypass,
  asserted closed.
* `test_a_cache_outage_does_not_lock_anyone_out` — lockout fails open, so an
  availability incident cannot become an authentication incident.
* `test_the_registry_covers_every_cron_line` — a job added to the crontab
  without a heartbeat entry fails the build instead of shipping unmonitored.

### 13.6 One upgrade step this introduces

Running the backend as a non-root user (M6) has a consequence for **existing**
deployments that a fresh install does not have: the `media_data` volume was
created while the container ran as root, so its contents are root-owned and
`nifn` cannot write to them.

The failure appears on the **first upload after the upgrade**, not at boot — so
it would be diagnosed live rather than during the deploy. The one-time fix is on
the go-live checklist as a blocking item and in `RUNBOOK.md` §6a:

```bash
docker compose -f docker-compose.prod.yml run --rm --user root backend \
  chown -R 1001:1001 /app/media
```

### 13.7 Still open, by choice

The six LOW findings remain as backlog, unchanged from §2 — except L4, which was
fixed in passing. L1 (asymmetric device signing) requires a collector change in
another repository and is the only one with real security weight.

**Phase 12 is out of scope and was not started.**
