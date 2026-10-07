# Go-Live Certification — Multi-Tenant SaaS Platform

**Phase S10.** Certification record for the conversion of the NIF Office
Management System from a single-tenant deployment into a multi-tenant SaaS
platform (Phases S0–S9).

Everything in this document was **measured**, not asserted. Each row names
the evidence and how to reproduce it. Where something was not measured, it
says so.

---

## 0. What "certified" means here, and what it does not

This certifies the **code**: isolation, authorisation, recovery, and the
behaviour of the platform under load. It is reproducible from this
repository.

It does **not** certify a particular deployment. Five of the sixteen launch
checks grade *configuration* — the cache backend, the mail transport, the
payment instructions, the error sink, and whether `DEBUG` is off. Those are
set by whoever operates the platform, and the gate reads them at deploy time
rather than being told about them here.

So there are two numbers, and conflating them would be the single most
misleading thing this document could do:

| | Score | Verdict |
|---|---|---|
| This development harness, as it stands | **70** | Not Launch Ready — `email`, `debug` blocking |
| The same code with the five configuration items set | **100** | **Launch Ready** |

Both are produced by the same scorer (`tenancy.launch.audit`), not by
judgement. Reproduce with `manage.py launch_readiness`, or
`manage.py check --deploy` for the gate.

---

## 0.1 Verification performed

Every suite below was run to completion for this certification, in this order,
strictly sequentially.

| Suite | Result |
|---|---|
| Backend — SQLite | **4 560 passed**, 43 skipped, 0 failed |
| Backend — PostgreSQL with RLS enforced, as `nifn_app` | **4 601 passed**, 2 skipped, 0 failed |
| Frontend — Vitest | **1 511 passed** across 131 files, 0 failed |
| Penetration suite (the 8 scenarios) under RLS | **45 passed**, 0 failed |
| Live conformance drive — Daphne, RLS on, real hostnames | **200 / 200 checks, 0 server errors** |
| Load test — 100 tenants, 1 000 users | **2 921 requests, 0 errors, 0 5xx** |
| Disaster recovery drill — 101 tenants, 1 001 users | **24 / 24 checks** |

The PostgreSQL run is the authoritative one. The SQLite run exists because it
is fast; it has no row-level security, so on its own it proves that the ORM
layer holds and nothing about the database's own enforcement.

---

## 1. Security assessment

### 1.1 Isolation — exact coverage, no gaps

| Measure | Result |
|---|---|
| Models classified tenant-scoped in `tenancy.inventory` | **106** |
| PostgreSQL tables carrying an RLS policy | **106** |
| Tables with `FORCE ROW LEVEL SECURITY` | **106** |
| Scoped tables **without** a policy | **0** |
| Policied tables **not** in the inventory | **0** |

The registry is the single source of truth: it drives the policies, the
export's completeness check and the conformance sweep, and CI fails if a new
model is not classified. Verified against the live database as `nifn_app`
(`NOSUPERUSER`, `NOBYPASSRLS`, not the table owner).

### 1.2 Penetration tests — all eight scenarios refused

`backend/tenancy/tests/test_penetration_s10.py`, **45 tests, 45 passing
under enforced RLS.** Written from the attacker's side: each test starts from
a capability a real attacker would hold and tries to turn it into something
it should not reach. Each also asserts the attacker's *legitimate* equivalent
still works, because a refusal that refuses everybody is an outage, not
security.

| # | Scenario | Outcome |
|---|---|---|
| 1 | Cross-tenant reads | **Refused.** Including raw SQL inside tenant A asking for tenant B's rows — the question the ORM cannot answer about itself. |
| 2 | Cross-tenant writes | **Refused.** Insert, update-by-id, delete-by-id, and moving one's own row into another tenant (the case a read filter cannot catch; the RLS `WITH CHECK` clause is what stops it). |
| 3 | Domain hijacking | **Refused.** Claiming resolves nothing; another tenant's hostname cannot be claimed, verified or withdrawn; the platform's own hostnames cannot be claimed; a look-alike suffix is not an allowed host. |
| 4 | JWT manipulation | **Refused.** A genuine token replayed on another tenant's host, a platform token inside a workspace, a tenant token on the console host, a tampered signature, a token naming a deleted account, a deactivated account's unexpired token. |
| 5 | Signed URL abuse | **Refused.** Minting for another tenant's path, forged signature, expired link, moving the expiry forward, the platform media prefix, and `../` traversal out of the tenant prefix. |
| 6 | Export abuse | **Refused.** Tenants have no export endpoint at all; bundles are not reachable by guessing an id; the slug and the id must agree; a bundle provably contains one tenant. |
| 7 | Archive abuse | **Refused.** No tenant can archive or restore anybody, including itself; an archived customer cannot reopen itself; archiving destroys nothing. |
| 8 | Console abuse | **Refused.** All 16 console routes swept against a tenant administrator and an anonymous caller; privilege escalation to platform staff blocked by a database constraint; the platform audit log is append-only; a tenant cannot provision itself a second workspace. |

**One hardening change was made as a result.** The media *signer* did not
refuse `../` while the *serving* view always has, so `org/<mine>/../<theirs>/
secret.pdf` produced a valid-looking link that then returned 403. Not
exploitable — but the asymmetry is the exact shape of the Phase S9 branding
defect, where one end normalised a token and the other did not. Both ends now
share `_unsafe_path`.

### 1.3 Authentication and transport

| Control | Setting |
|---|---|
| Refresh token rotation | on, with blacklist-after-rotation |
| Refresh lifetime | 7 days |
| Login throttle | dedicated scope, plus `AnonRateThrottle` |
| Account lockout | enabled, 900 s |
| Password validators | four, including similarity and common-password |
| HSTS | 31 536 000 s, `includeSubDomains`, `preload` (off in DEBUG) |
| Secure cookies / SSL redirect | on when `DEBUG` is off |
| Tenant binding | `user.organization_id` compared against the **host-resolved** organization — no org claim in the token is trusted, so there is nothing to forge |

### 1.4 Areas audited with no new findings

Authorization (role gates plus `require_tenant_admin`'s three conditions),
registration (throttles, verification-before-provisioning, no enumeration
oracle), provisioning (failure recovery, Phase S6.75), payments (no gateway,
state machine requires claim-before-decision, receipts outside the tenant
media tree), branding (hex-only colour validation, asset allow-list),
exports (integrity pass, one tenant per bundle), platform console
(host-scoped, staff-only, append-only audit).

---

## 2. Load test results

**100 tenants, 1,000 users**, provisioned through the real service layer, then
driven over real HTTP against Daphne with `TENANCY_ENABLED=1`,
`TENANCY_RLS_ENABLED=1`, as `nifn_app`, on per-tenant hostnames.

### 2.1 Provisioning

| Measure | Result |
|---|---|
| Tenants provisioned | 100 (plus the platform operator) |
| Time | 76.9 s total — **p50 0.86 s**, p95 0.93 s, max 0.98 s each |
| Users created | 900 (+100 admins = 1,000) |
| Database size | 24 MB |

Per-tenant cost is **constant**, not growing with tenant count — the property
that matters for a shared-schema design.

### 2.2 Request performance (2,921 requests, 32 concurrent sessions)

| Endpoint | n | p50 | p95 | p99 |
|---|---|---|---|---|
| login | 100 | 878 ms | 1 150 ms | 1 155 ms |
| `auth/user` | 400 | 282 ms | 512 ms | 583 ms |
| leave-types | 400 | 273 ms | 411 ms | 551 ms |
| users | 400 | 250 ms | 407 ms | 433 ms |
| leaves | 400 | 251 ms | 375 ms | 398 ms |
| tenant branding | 400 | 263 ms | 400 ms | 428 ms |
| tenant subscription | 400 | 271 ms | 377 ms | 406 ms |
| notifications | 400 | 258 ms | 376 ms | 414 ms |
| **overall** | **2 921** | **267 ms** | **444 ms** | **964 ms** |
| console dashboard | 5 | 24 ms | 31 ms | — |
| console tenant list | 5 | 24 ms | 30 ms | — |

**Error rate 0.00 % (0 of 2,921). 5xx: 0.** Throughput 105 req/s on a single
Daphne process.

Login is ~3× the read endpoints because password hashing is deliberately
slow. That is a capacity-planning input, not a defect.

The console is *faster* than tenant endpoints despite aggregating across 101
tenants, because its dashboards read counters rather than querying tenant
tables.

### 2.3 Database and cache load

| Measure | Result |
|---|---|
| Transactions committed | 9 657 (0 rolled back) |
| Rows returned / fetched | 4.82 M / 4.26 M |
| Buffer cache hit ratio | **100.00 %** (5 000 331 hit / 0 read) |
| Deadlocks | **0** |
| Temp files spilled | **0** |
| Peak connections | **33** — exactly the 32 concurrent requests plus one |

### 2.4 The defect the load test found

At the shipped default `CONN_MAX_AGE=60`, the same load produced **339 HTTP
500s — a 14.5 % error rate** — with:

```
FATAL: remaining connection slots are reserved for roles with
       the SUPERUSER attribute
```

One Daphne process reached **97 connections, 95 of them idle**, against
`max_connections=100`. Cause: Django closes connections on
`request_finished`, which closes the connection belonging to *the thread the
signal fires on* — while under ASGI the view ran on a pool thread. A
connection held by a pool thread is reclaimed by nothing but `CONN_MAX_AGE`
expiring.

Setting `CONN_MAX_AGE=0` took the identical load to **0 errors and a peak of
33 connections**. Now gated as `tenancy.L014` (Connection budget), which also
accepts a declared pooler — pgbouncer in **transaction** mode is safe here,
because tenant binding is `SET LOCAL` inside `ATOMIC_REQUESTS` rather than a
session-scoped `SET`.

Reproduce: `bash load_run.sh` with `LOAD_CONN_MAX_AGE=60` then `=0`.

---

## 3. Recovery results

**24 of 24 recovery checks passed** against the loaded platform — 101
organizations, 1,001 users, 24 MB.

| Recovery | Target | Measured |
|---|---|---|
| Database dump | minutes | **0.10 s** (2.6 MB of SQL) |
| Database restore into an empty database | minutes | **2.39 s** |
| **RTO (dump + restore)** | < 1 h | **2.49 s** |
| Tenant restore (archive → open) | minutes | **0.01 s** |
| Export production | minutes | **0.18 s**, checksum verified |
| Export re-import | — | **still not possible** without engineering work |

### What the drill proved that a document could not

1. **The policies came back.** 106 policies before, 106 after, with `FORCE
   ROW LEVEL SECURITY` still set on 106 tables. A restore that brought the
   data without the policies would hand the recovered platform every
   tenant's rows in one unguarded pile — and would look successful.
2. **The application role can still use it.** Five tenants were each asked
   for their own user list under RLS after the restore; each saw exactly its
   own ten, and two sampled tenants had **zero overlap**.
3. **Media restores per tenant.** A file written under one tenant's prefix
   after the restore was servable to its owner and refused to another (the
   refusal logs at ERROR).
4. **Domains survive.** A domain was claimed, verified and resolved to the
   right tenant after the restore.

### One operational caveat found

Nothing prunes the media tree. A database restored to an earlier point leaves
every file written after it unreferenced — unreachable, but still on disk and
in every later backup. The drill environment held **347 orphaned tenant media
directories**. An orphan sweep is a housekeeping job that needs an owner.

---

## 4. Monitoring coverage

| Area | State |
|---|---|
| Logging | JSON to stdout, request-id correlation, **20 application loggers** |
| Error tracking | Sentry wired, **optional** — gated as `L015` |
| Metrics | `tenancy.metrics` — 8 panels + registration funnel; `monitoring` app |
| Health checks | `/api/v1/health/`, `/api/v1/health/detailed/`, `/api/v1/platform/health/` |
| Launch gate | 16 checks, `manage.py check --deploy` |
| Alerts | **15 rules**, evaluated every 5 minutes, firing on state transitions only — **none of them SaaS-aware**; see R7 |

### Alerting: present, and aimed at the previous product

`monitoring/alerts.py` holds **15 rules**, evaluated every 5 minutes by
`check_alerts`, firing on **state transitions only** so a weekend outage
sends one alert rather than 288. The design is sound and the plumbing works.

Every rule, however, is a single-tenant or infrastructure concern: database
reachability and latency, disk, Redis, biometric devices, punch queues,
stuck reports, backup freshness, departments without a head.

**Nothing alerts on anything the SaaS platform does.** Specifically, no rule
fires when:

| Nobody is told that… | Consequence |
|---|---|
| provisioning is failing | registrations verify and silently produce no workspace |
| the registration funnel has collapsed | people sign up all week and none completes |
| a domain verification is stuck (≥3 failed checks) | a customer cannot use the address they paid for |
| payment receipts are ageing unreviewed | customers who paid stay locked out |
| subscriptions are entering grace or expiring | renewals are missed silently |
| an export failed | a leaving customer waits for data that is not coming |
| the 5xx rate is rising | precisely the load-test failure — 339 identical 500s that only purposeful watching caught |

The underlying data already exists: `tenancy.metrics.dashboard()` produces
eight panels including provisioning attempts and failures, and
`registration_funnel()` produces the stage-by-stage rates. The gap is wiring
between `tenancy.metrics` and the `monitoring` alert engine, which reads its
own metric sections.

**Deliberately not built in this phase.** New metric collectors, rules and
their tests would be a substantive change introduced at the end of a
certification run, and shipping unverified monitoring during a go-live
certification is the wrong trade. It is recorded as R7 with the specific
rules named, and it is the first post-launch monitoring task.

### The gap this phase found and fixed

`tenancy` logs from **thirty files** and had **no logger configured**, so
everything it recorded below WARNING fell through to the root logger (at
WARNING) and was discarded. Four other packages too (`appraisal`,
`circulars`, `tasks`, `drafts`, `evidence`).

WARNING and ERROR still reached the root logger, so the gap was invisible in
normal operation and total during an incident: *"domain X failed verification:
no TXT record at …"* is logged at INFO, and that is the one line support needs
when a customer says their address does not work.

This is the Phase 11 audit finding M9 recurring for every package added
since. So the fix is paired with a check that tests the **shape** rather than
a list — `tenancy.L016` walks the app registry and fails if any app that
calls `getLogger` has no logger configured. Its own test caught the first
version of that check passing vacuously.

---

## 5. Go-live checklist

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Database | **PASS** | reachable; 106/106 RLS policies; `L001` |
| 2 | Redis / cache | **CONFIG** | round trip works; LocMemCache is graded down — `L002` |
| 3 | SMTP | **CONFIG** | `L008`; blocking until a real transport is set |
| 4 | Storage | **PASS** | probe writes, reads and deletes; `L003` |
| 5 | RLS | **PASS** | enforced; this connection cannot bypass it; `L005`; 45/45 pen tests |
| 6 | Provisioning | **PASS** | 100 tenants, p50 0.86 s, newest verifies clean; `L010` |
| 7 | Registration | **PASS** | live drive: 4 sectors register, verify, provision, reach "Tenant Ready" |
| 8 | Payments | **CONFIG** | state machine and queue verified; no instructions configured — `L009` |
| 9 | Domains | **PASS** | claim → verify → serve end-to-end over HTTP; `L004` |
| 10 | Branding | **PASS** | three tenants, three brands, employee-visible; emails and PDFs branded |
| 11 | Exports | **PASS** | checksummed, integrity clean, one tenant per bundle; 24/24 DR checks |
| 12 | Connection budget | **PASS** at `CONN_MAX_AGE=0` | `L014`; measured both ways |
| 13 | Error tracking | **CONFIG** | `L015` |
| 14 | Application logging | **PASS** | 20 loggers; `L016` |
| 15 | `DEBUG` off | **CONFIG** | `L011`; blocking |

**PASS** = certified by this repository. **CONFIG** = correct in code,
requires a deployment decision; each is gated so it cannot be forgotten.

---

## 6. Risk register

| # | Risk | Sev | Status | Owner |
|---|---|---|---|---|
| R1 | **Custom domains have no TLS path.** The wildcard certificate covers `*.<platform domain>`, not `hr.customer.com`. No ACME automation (an integration, deliberately out of scope). With `SECURE_SSL_REDIRECT` on, an un-certificated custom domain redirects to an `https://` URL that cannot complete, so the customer sees a TLS error rather than guidance. | **High** | **Open.** Documented in the deployment checklist and support runbook §5c. Treat custom domains as *self-service to verify, operator-assisted to go live*. | Ops |
| R2 | **`CONN_MAX_AGE` + ASGI without a pooler** → 14.5 % 5xx under load. | **High** | **Mitigated.** `CONN_MAX_AGE=0` measured clean; gated as `L014`. | Ops |
| R3 | **LocMemCache in a multi-process deployment**: every rate limit multiplied by the worker count; resolver and host caches not shared, so a verified domain takes up to 60 s to serve on each worker and a withdrawn one up to 60 s to stop. | **Medium** | **Gated** as `L002`. Fix is Redis. | Ops |
| R4 | **No error tracking unless `SENTRY_DSN` is set.** Customers never see internal errors by design, so nothing else surfaces them. | **Medium** | **Gated** as `L015`. | Ops |
| R5 | **Export re-import is not possible.** The platform can always hand a customer their data and cannot take it back. | **Medium** | **Open, documented** (disaster-recovery §5). Pre-existing, unchanged. | Eng |
| R6 | **Media orphans accumulate.** Nothing prunes `media/org/`; a point-in-time restore leaves unreferenced files on disk and in later backups. 347 orphaned directories in the drill. | **Low** | **Open, documented.** Needs a housekeeping owner. | Ops |
| R7 | **Alerting is not SaaS-aware.** 15 rules exist and work, but all cover single-tenant infrastructure (database, disk, Redis, devices, punch queues, backups). Nothing fires on provisioning failure, funnel collapse, a stuck domain verification, ageing payment receipts, subscription expiry, a failed export, or a rising 5xx rate. | **Medium** | **Open, scoped.** The data already exists in `tenancy.metrics`; the gap is wiring it to the `monitoring` engine. Deliberately not built during certification — see §4. Interim: alert on `levelname=ERROR` in the JSON log stream, which `L015`'s hint names. | Ops / Eng |
| R8 | **Stale single-tenant documentation.** `DISASTER_RECOVERY.md` and `disaster-recovery.md` both exist, plus pre-SaaS `GO_LIVE.md` / `PHASE_12_GO_LIVE.md`. An operator could follow the single-tenant procedure. | **Low** | **Open.** Recommend retiring the superseded files. | Eng |
| R9 | Login p50 ≈ 878 ms at 32-way concurrency on one process (password hashing). | **Info** | Not a defect; capacity input. | — |

**No critical issues. No high-severity issues in the code** — R1 and R2 are
both deployment concerns, one documented and one gated.

---

## 7. Scores

| Dimension | Score | Basis |
|---|---|---|
| **Security** | **98 / 100** | 8/8 attack scenarios refused (45 tests under RLS); 106/106 policy coverage with zero gaps either way; token, transport and lockout controls in place. −2 for R1: a feature is sold as self-service that cannot be served over HTTPS without manual work. |
| **Readiness** | **100 / 100** configured · **70** as this harness stands | `tenancy.launch.audit`, same scorer both ways. All five gaps are configuration; none is a code defect. |
| **Operational** | **88 / 100** | RTO measured at 2.5 s for 101 tenants; 24/24 recovery checks; 20 loggers; health checks, metrics and a working alert engine. −12 because the alert engine is aimed entirely at the previous single-tenant product (R7) and nothing prunes media (R6). |
| **Support** | **96 / 100** | All six required runbooks present and specific, including the two this phase added (payment failure, domain verification failure). −4 for R8: superseded single-tenant documents still in `docs/`. |
| **Overall** | **95 / 100** | Weighted toward security and readiness. |

---

## 8. Recommendation

### **GO — conditional on five deployment settings and one acknowledgement.**

The code is certified. Every isolation, authorisation and recovery claim in
this document was executed and measured, in both deployments, including the
one that only a real request can test.

**Before taking the first paying customer, set:**

1. `DJANGO_DEBUG=0` — blocking (`L011`)
2. A real SMTP transport — blocking (`L008`); without it no registration ever
   becomes a workspace
3. `CACHES` → Redis (`L002`)
4. `DATABASE_CONN_MAX_AGE=0`, **or** pgbouncer in transaction mode with
   `DATABASE_POOLER=1` (`L014`)
5. `SENTRY_DSN`, or an equivalent sink with an alert on `levelname=ERROR`
   (`L015`)

Then run **`manage.py check --deploy`**. It is the gate, it reads all five,
and it returns non-zero until they are right.

**Also configure before selling:** at least one payment instruction (`L009`),
or the subscription page tells customers what they owe and shows them no way
to pay it.

**Acknowledge two things:**

* Custom domains (R1) need a certificate issued per domain by an operator.
  Sell them as assisted, not self-service, until that is automated.
* Alerting (R7) will not tell anyone that provisioning has broken, that the
  registration funnel has collapsed, or that paid receipts are going
  unreviewed. Until those rules exist, somebody has to read the platform
  dashboard — the console's Launch Readiness and Registration Funnel panels
  are where that information lives.

### Why conditional rather than unconditional

Not one of the five is a code defect, and all five are gated — the platform
refuses to certify itself until they are set. Calling this an unconditional
GO would mean asserting things about a deployment this repository cannot see.

---

## 9. Reproducing this certification

```bash
# Backend, SQLite (fast, no RLS)
DJANGO_SETTINGS_MODULE=sqlite_settings pytest -q

# Backend, PostgreSQL with row-level security enforced — the authoritative run
python rls_harness.py
DJANGO_SETTINGS_MODULE=pg_rls_settings pytest -q --reuse-db

# The eight attack scenarios, specifically
DJANGO_SETTINGS_MODULE=pg_rls_settings pytest -q --reuse-db \
    tenancy/tests/test_penetration_s10.py

# Frontend
cd frontend && npx vitest run

# Live conformance drive: Daphne, RLS on, real hostnames
bash live_run.sh                       # 200 checks

# Load: 100 tenants, 1000 users
bash load_run.sh                       # add LOAD_CONN_MAX_AGE=60 to reproduce R2

# Disaster recovery drill
python dr_validate.py                  # 24 checks

# The gate
manage.py check --deploy
manage.py launch_readiness
```

The harness scripts live in the session scratchpad, not in the repository;
they are listed so the procedure is reconstructable.
