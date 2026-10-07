# Alerting Coverage

**Phase S11 Part 3. Closes R7.**

Phase S10 found fifteen alert rules that all worked and all described the
*previous* product — database, disk, Redis, biometric devices, punch queues,
backups. Nothing fired when the SaaS platform itself was in trouble.

## How alerting behaves

Rules live in `monitoring/alerts.py` and are evaluated by `check_alerts`
**every five minutes**. Alerts fire on **state transitions only**: once when
a condition starts, once when it clears, and one digest a day while it
persists. State is kept in the audit log, not the cache, so a Redis restart
does not re-fire every active alert and "when did this start" is answerable
months later.

Delivery goes to `ALERT_EMAILS` and/or `ALERT_WEBHOOK_URL` — **the platform
team**, never a tenant's users. `L017` fails the launch gate if neither is
set, because the fallback is a log line nobody reads.

### Severity means who is inconvenienced, not how unusual it is

| | |
|---|---|
| **critical** | a customer cannot work right now, or money is stuck |
| **warning** | a customer is waiting on us, or it is work for office hours |
| **info** | recorded, never sent |

An amber metric on a critical rule is downgraded to a warning. Only red
earns the rule's full severity.

## The nine categories (ten metrics)

| Metric | Fires when | Severity | Why that severity |
|---|---|---|---|
| `saas_provisioning_stuck` | any tenant in `provisioning` | **critical** | Provisioning is one atomic sub-second call. A tenant still in this state is broken, not busy, and a customer is already waiting |
| `saas_provisioning_failed` | ≥1 failure in 24 h (amber), ≥3 (red) | **critical** | Verified registrations producing no workspace |
| `saas_registration_stalled` | a signup verified >1 h ago with no workspace | **critical** | The worst state in the product: the customer did everything asked and was told so, and we owe them a workspace |
| `saas_verification_failed` | ≥3 unverified signups in 24 h (amber), ≥10 (red) | warning | Some is people abandoning a form. A *cliff* means mail stopped being delivered — which the platform cannot see, because its own send succeeded |
| `saas_domain_failed` | a domain `failed`, or pending with ≥3 checks | warning | A customer paid for an address that does not work. Three checks means they have misread their DNS panel and will not work it out alone |
| `saas_export_failed` | ≥1 failed export in 24 h | warning | A customer — often one who is leaving — waiting for data that is not coming |
| `saas_restore_needed` | archived while the subscription is live | **critical** | Either a restore that did not complete or a wrongly archived customer. Both need the same phone call |
| `saas_subscription_expiry` | expiring ≤7 d (amber); in grace/expired/suspended (red) | warning | Amber is office-hours sales work. Red means customers cannot work this morning |
| `saas_payment_backlog` | oldest unreviewed receipt >24 h (amber), >72 h (red) | **critical** | **Measured in hours, not count.** Twenty receipts this morning is a busy day; one from Friday still waiting on Monday is a customer who paid and is locked out — a count cannot tell those apart |
| `saas_5xx_rate` | >1 % of responses (amber), >5 % (red), over 5 min | **critical** | Customers never see internal errors by design, so nothing else will tell you. Phase S10's load test produced 339 identical 500s that nothing was counting |

Plus one rule that is not a product condition:

| `saas_platform` | the SaaS probe itself raised | **critical** | `metrics.safe` turns a probe exception into one red metric named after the function. Without a rule for it, a broken probe takes all nine alerts offline and fires nothing — monitoring that fails quiet, which is worse than none because it is trusted |

## Two design decisions worth knowing

### The 5xx rate is counted in the cache, not the database

It has to work on the one path where the database is least trustworthy: a
request that is already failing. With `ATOMIC_REQUESTS`, a 500 means the
transaction is rolling back, so a row written during it would vanish — and a
row written after it needs a connection at the moment connections may be the
problem.

The cost is honest: the numbers reset when the cache does, and with a
per-process cache each worker counts only its own traffic. One more reason
the gate insists on Redis.

Rates are **suppressed below 20 requests** in the window. One error in three
requests is 33 % and means nothing; a rate on a tiny sample is how a
monitoring system teaches people to ignore it.

### These metrics are not on the tenant-facing board

`monitoring/views.py` serves `metrics.collect()` to `IsOperator` — a role
held by HR and Admin users **inside a customer's workspace**. Every SaaS
metric is platform-wide: which tenants are stuck, which hostnames are
failing, the payment backlog across all customers.

So `metrics.collect()` **excludes** the SaaS section unless asked. The
tenant-facing views never ask; `check_alerts`, which has no HTTP caller,
does. `monitoring/tests/test_saas_alerts.py` asserts that boundary directly
— including that no `saas_` key appears under *any* section of the default
board, so moving a metric into an existing section cannot slip past it.

## Still not covered

| Gap | Why it is acceptable for launch |
|---|---|
| Per-tenant error rates | The 5xx rate is platform-wide. A single tenant with a broken integration shows up only if it moves the whole rate |
| Queue depth / worker liveness for the ASGI workers | Covered indirectly: a dead worker shows as a 5xx rate or a cron overdue |
| Synthetic end-to-end probe (register → verify → provision every hour) | Would catch a silent provisioning break faster than waiting for a real customer to hit it. Recommended as the next monitoring task |

## Verifying alerting works

```bash
manage.py check_alerts --dry-run          # evaluate without sending
manage.py check_alerts                    # evaluate and deliver
```

To prove delivery end to end, create the condition rather than the alert —
e.g. set a tenant to `provisioning` in a staging database and wait one cycle.
An alert nobody has ever seen arrive is an alert nobody knows the address of.
