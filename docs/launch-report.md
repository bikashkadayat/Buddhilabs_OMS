# Production Readiness Report

**Phase S11 Part 10 (outputs 6 and 7: Risk Review, Final Launch Recommendation).**
**For:** whoever decides whether this platform takes paying customers, and the people who will run it afterwards.
**Date:** 2026-10-06.

---

## 1. The answer first

**Conditional GO.** The platform can open public registration and run trials now. It should not **sell paid plans at volume** until the condition in §8 is met.

The full customer lifecycle ran end to end on a production-shaped stack, under the real deployment configuration, with no server errors. Registration, email verification, provisioning, trial, subscription, payment submission, domain verification, export, archive and restore all worked.

One gap blocks paid sales at volume, and it is a missing screen, not a broken one. **A customer can pay and submit a receipt, but nobody can approve it from the console.** Approving it takes an engineer with a Django shell. Until there is a button for it, each paid conversion depends on a person with server access, and money waits for that person.

---

## 2. The production drill (Part 9)

### What "real deployment configuration" meant

| Component | In the drill |
|---|---|
| Application | Daphne (ASGI), `DEBUG=0`, behind `X-Forwarded-Proto: https` |
| Database | PostgreSQL 16, row-level security on, connected as `nifn_app` (no superuser, no BYPASSRLS, not the owner); migrated as `nifn_migrate` |
| Cache, rate limits, channels | Redis 6.2 |
| Mail | A real SMTP server with AUTH. The verification email was delivered over the wire and the link was taken from it |
| Error tracking | Sentry SDK configured, pointed at a local sink |
| DNS | dnspython making real UDP queries to a local authoritative nameserver. Only the choice of nameserver was arranged |
| Settings guards | Every `DEBUG=0` fail-closed guard active: secret key, explicit allowed hosts, database password, SMTP credentials |
| Platform data | The migrated plan catalogue. The drill buys the real `annual` plan at NPR 9,990.00 |

### Result

```
manage.py check --deploy                     no issues           exit 0
launch_readiness --strict (fresh deploy)     16/17, 0 critical   exit 0   (one warning, see N3)
the drill                                    34/34 checks passed
launch_readiness --strict (after repair)     17/17, 0 critical   exit 0
server log                                   0 Internal Server Error, 0 tracebacks
SMTP                                         1 message accepted
```

| # | Step | Proved |
|---|---|---|
| 0 | Default organization repair | The repair verb fills the gaps a migration cannot, and `nif` then verifies "Tenant Ready". It only adds rows; no NIF data was removed |
| 1 | Registration | Subdomain checked before it is claimed. Public form answers 202. **No workspace exists yet** |
| 2 | Verification | Email delivered over SMTP to the registrant, with a working link |
| 3 | Provisioning | The link builds the workspace, which reports "Tenant Ready" and answers on its own hostname |
| 4 | Trial | 14-day trial. The owner signs in with the password they chose and is not asked to change it |
| 5 | Subscription | Plans and payment methods shown. Requesting a plan opens a payment whose amount **the server** resolved. The subscription does **not** move yet |
| 6 | Payment submission | Receipt submitted, and it reaches the platform's verification queue |
| 7 | Domain verification | Claim works. An unpublished record **fails** the check (rather than being reported as "cannot look it up"). Publishing the TXT record activates the domain, and the platform then **serves that hostname** |
| 8 | Export | Full export in one request, with a manifest, and the archive downloads |
| 9 | Archive | Owner refused at sign-in (402). The archived tenant is still exportable |
| 10 | Restore | Tenant restored to its previous status, and the owner is admitted again |

### What the drill found on the way to 34/34

Every failing run was investigated to its cause. None was treated as flaky.

| Finding | Where the fault was | Outcome |
|---|---|---|
| Refresh and sign-out answered **500** for platform operators, so their sessions could not be renewed or ended, and "log out" revoked nothing | **Product** | Fixed (`tenancy/tokens.py`, `TenantSafeRefreshToken`). Covered by a live test, proven by reverting the fix |
| A fresh deployment's `nif` organization has configuration gaps | Product behaviour | Recorded as N3. The drill repairs it the way an operator would |
| Registration answered 202 while the mail server was refusing auth | **Product** (by design, but under-signalled) | Recorded as N4 |
| Domain verification answered 503 "cannot look" | Drill harness: the stub nameserver's receive loop exited on its own timeout | Fixed in the harness |
| Domain verification answered "Nothing found" after the record was published | Drill harness: the record was written in one process and served from another | Fixed in the harness |
| Two domain checks expected the wrong status | Drill harness: the endpoint answers 200 and reports the outcome in `status`, which goes straight to `active` on the customer path | Assertions corrected to the product's contract |
| The readiness step printed a `[FAIL]` line and still reported success | Drill harness: run without `--strict`, which **always** exits 0 | Harness now gates on `--strict`, before and after repair. See N5 |

### Test suites

| Suite | Result |
|---|---|
| Backend, SQLite | 4690 passed, 0 failed |
| Backend, PostgreSQL + RLS as `nifn_app` | 4731 passed, 0 failed |
| Frontend (vitest) | 1553 passed, 0 unhandled errors |

These were measured after the last product change in this phase. CI runs PostgreSQL, so a green SQLite run alone does not show that tenancy works.

---

## 3. Risk review (output 6)

### Carried from the go-live certification (`go-live-certification.md` §6)

| # | Risk | Sev | Status now | Owner |
|---|---|---|---|---|
| R1 | **Custom domains have no TLS path.** The wildcard certificate does not cover `hr.customer.com`, and there is no ACME automation. The drill proved verification and serving over HTTP; it did not, and cannot, prove a certificate | **High** | **Open.** Custom domains are *self-service to verify, operator-assisted to go live*. Procedure: `custom-domain-operations.md`, support runbook §5c | Ops |
| R2 | `CONN_MAX_AGE` with ASGI and no pooler gave 14.5 % 5xx under load | High | **Mitigated.** `CONN_MAX_AGE=0`, gated | Ops |
| R3 | LocMemCache in a multi-process deployment | Medium | **Gated.** The drill ran on Redis | Ops |
| R4 | No error tracking without `SENTRY_DSN` | Medium | **Gated.** The drill ran with Sentry configured | Ops |
| R5 | An export cannot be re-imported | Medium | **Open, documented** (`disaster-recovery.md` §5) | Eng |
| R6 | Media orphans accumulate under `media/org/` | Low | **Open.** Needs a housekeeping owner | Ops |
| R7 | Alerting was not SaaS-aware | Medium | **Closed by S11 Part 3.** Eleven SaaS rules; see §5 | — |
| R8 | Superseded single-tenant documents (`DISASTER_RECOVERY.md`, `GO_LIVE.md`, `PHASE_12_GO_LIVE.md`) sit beside the SaaS ones | Low | **Still open.** All three are still present. An operator could follow the single-tenant procedure | Eng |
| R9 | Login p50 ≈ 878 ms at 32-way concurrency on one process | Info | Capacity input, not a defect | — |

### Found in this phase

| # | Risk | Sev | Status | Owner |
|---|---|---|---|---|
| **N1** | ~~Nobody can approve a payment from the product.~~ | High (commercial) | **Closed** in the billing-operations phase: the console's Payment Center approves, rejects (with a reason) and asks for more information, in one step. It activates the subscription, writes the audit entry and emails the customer. Verified live end to end (`docs/billing-and-simplification.md`). | Eng |
| N2 | A platform operator's session calling a tenant API gets **500** (`TenantScopeMissing`), not 403 | Medium | **Partly mitigated.** The console no longer makes such calls (operators are redirected to `/platform`). A direct call still answers 500, and each one counts towards `saas_5xx_rate`. The 500 is deliberate as a loud signal of a missing scope; the fix is a narrow 403 for platform tokens on tenant routes, not a global conversion | Eng |
| N3 | A fresh deployment's default organization (`nif`, created by migration) is missing departments, leave types and task templates | Low | **Open, with a one-click fix.** Readiness warns (IMPORTANT) but does not block. Repair it from the console after the first deploy. The repair only adds rows | Ops |
| N4 | **When mail fails, registration still answers "verification sent".** The send failure is logged at WARNING, not ERROR, so the interim "alert on ERROR" net misses it | Medium | **Detected late.** `saas_verification_failed` notices the drop in verified signups (≥3 in 24 h amber, ≥10 red), which can take hours. Support runbook §1 covers the customer side | Eng / Ops |
| N5 | `launch_readiness` without `--strict` always exits 0, including when a CRITICAL check fails | Low | **Documented.** The deployment checklist already requires `--strict`. The drill harness made exactly this mistake, which is why it is listed | Ops |

**No open risk allows one tenant's data to reach another.** The isolation results from S10 still hold: 4731 tests under RLS, connected as a role that cannot bypass policy.

---

## 4. Operational procedures

| When | Do | Where it is written |
|---|---|---|
| Every deploy | Gate, migrate as `nifn_migrate`, collect static, start, verify | `deployment-checklist.md` §3, `production-deployment.md` §7 |
| First deploy only | Repair `nif` from the console, and confirm `launch_readiness --strict` reads 17/17 | This report, N3 |
| Daily | Scheduled jobs: alerts every 5 min, expiry, retention | `deployment-checklist.md` §4, `production-deployment.md` §3 |
| A payment arrives | **Shell procedure** (claim, verify or reject, with a note). Do not promise a customer a timeline the team cannot meet | `support-runbook.md` §5b |
| A custom domain is verified | Issue its certificate **before** telling the customer it works | `custom-domain-operations.md` |
| Export, archive, restore | From the console | `operator-runbook.md` §1–3 |
| Deletion request | Retention policy first, then the procedure | `tenant-retention-and-deletion-policy.md`, `operator-runbook.md` §4 |
| Database or file loss | Measured RTO 2.5 s for 101 tenants | `disaster-recovery.md` §3, §4, §6 |
| Escalation | L1 → L2 → L3 triggers, and what never to do | `operator-runbook.md` §5 |

Follow `disaster-recovery.md`, **not** `DISASTER_RECOVERY.md` (R8).

---

## 5. Alert coverage

Alerts go to `ALERT_EMAILS` and/or `ALERT_WEBHOOK_URL`, which reach the platform team and never a tenant. The launch gate fails if neither is set. Rules fire on state transitions, and once a day as a digest while a condition persists. Full rationale: `alerting-coverage.md`.

| Rule | Fires when | Severity |
|---|---|---|
| `saas_provisioning_stuck` | any tenant in `provisioning` | critical |
| `saas_provisioning_failed` | ≥1 failure in 24 h | critical |
| `saas_registration_stalled` | verified more than 1 h ago, no workspace | critical |
| `saas_restore_needed` | archived while the subscription is live | critical |
| `saas_payment_backlog` | oldest unreviewed receipt more than 24 h old | critical |
| `saas_5xx_rate` | more than 1 % of responses over 5 min | critical |
| `saas_platform` | the SaaS probe itself raised | critical |
| `saas_verification_failed` | ≥3 unverified signups in 24 h | warning |
| `saas_domain_failed` | a domain failed, or is pending after 3 checks | warning |
| `saas_export_failed` | a failed export in 24 h | warning |
| `saas_subscription_expiry` | expiring within 7 days, or lapsed | warning |

Infrastructure rules remain alongside these: the original 15 covering database, disk, Redis, devices, punch queues and backups.

**Not covered:** per-tenant error rates (the 5xx rate is platform-wide), and mail failure at the moment it happens (N4). The drill checked that alert delivery is configured. It did not trigger a live alert from end to end; the rules are covered by the test suite.

---

## 6. Support coverage

| Workflow | Runbook | Can L1 resolve it from the console? |
|---|---|---|
| Registration | `support-runbook.md` §1–3 | Yes |
| Login, including wrong-hostname cases | §4, §4b | Yes |
| Provisioning | §3 | Yes (repair verb) |
| Billing | §5, §5b | Yes: the Payment Center (N1 closed) |
| Custom domain | §5c | Verification yes; certificate needs Ops (R1) |
| Export | §6 | Yes |
| Restore | §7 | Yes |

Every console action is audited against the tenant with the operator's name and reason, and the customer is shown that reason.

---

## 7. Deployment checklist

The full list is `deployment-checklist.md`. This is the minimum for a launch.

**Once, before the first SaaS deploy**
- [ ] Wildcard DNS and wildcard TLS for `*.<TENANCY_BASE_DOMAIN>`, and the console hostname
- [ ] Database roles `nifn_app`, `nifn_migrate` and `nifn_admin` created as `production-deployment.md` §2 specifies. The app role must **not** own tables or bypass RLS
- [ ] `DJANGO_SECRET_KEY` of at least 50 characters, explicit `DJANGO_ALLOWED_HOSTS`, `DATABASE_PASSWORD`, SMTP host, user and password. Each refuses to start with `DEBUG=0` if missing
- [ ] `REDIS_URL` (R3), `SENTRY_DSN` (R4), `ALERT_EMAILS` or `ALERT_WEBHOOK_URL`
- [ ] At least one purchasable plan and one payment instruction (`/admin/tenancy/paymentinstruction/`, not the console)
- [ ] One platform operator: `manage.py create_platform_admin`

**Every deploy**
- [ ] `manage.py check --deploy` reports no issues
- [ ] `manage.py launch_readiness --strict` exits 0. Always use `--strict` (N5)
- [ ] `migrate` as `nifn_migrate`
- [ ] Static files collected; Daphne started; scheduled jobs installed

**After the first deploy**
- [ ] Repair `nif` from the console, then `launch_readiness --strict` reads 17/17 (N3)
- [ ] Sign in to the console, refresh, sign out, and confirm the old token is refused (the refresh and sign-out 500s fixed in this phase must not return)
- [ ] Register a test workspace on the real domain, and confirm the email **arrives** (N4)
- [ ] Name the person who will review payments in the Payment Center, and add at least one payment method customers can use
- [ ] Retire or rename `DISASTER_RECOVERY.md`, `GO_LIVE.md` and `PHASE_12_GO_LIVE.md` (R8)

---

## 8. Final launch recommendation (output 7)

**GO for public registration and trials, now**, provided the §7 checklist is complete.

**GO for paid plans when one of these is true:**

1. **Payment approve and reject actions exist in the console (N1). Done in the billing-operations phase**, so this condition is met.
2. **Or** the team accepts manual approval: a named L3 engineer with shell access, a stated response time, and `saas_payment_backlog` routed to them. This works for a handful of customers a week and does not scale beyond that.

**Custom domains** may be offered as operator-assisted only, until TLS issuance exists (R1). Do not describe them as instant.

**Before the first hundred tenants**, fix N2 (a narrow 403) and N4 (log the mail failure at ERROR, or tell the registrant). Each is small, and each makes a failure visible that is currently quiet.

Nothing found in this phase puts one tenant's data at risk from another, and nothing found loses data.
