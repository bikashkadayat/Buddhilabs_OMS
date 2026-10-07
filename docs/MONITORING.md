# Monitoring & Alerting

## Why this and not Prometheus

Single host, one organisation, no dedicated ops team. A monitoring stack nobody
maintains is worse than a page inside the app people already sign into — it
rots quietly and is trusted anyway. The metrics are shaped so a `/metrics`
exporter is a small change if the deployment ever goes multi-host.

## Surfaces

| Surface | Audience | Purpose |
|---|---|---|
| `GET /api/v1/health/` | container, deploy gate | Liveness. Deliberately minimal — no cache, no throttle, no auth |
| `GET /api/v1/health/detailed/` | Admin | Legacy deep status (Phase 5) |
| `GET /api/v1/monitoring/health/` | HR, Admin | Full board, 21 metrics with thresholds |
| `GET /api/v1/monitoring/cron/` | HR, Admin | Every scheduled job and how late it is |
| `GET /api/v1/monitoring/alerts/` | HR, Admin | Firing alerts and 7 days of history |
| `/monitoring` | HR, Admin | The board, red-first |

`/health/` stays separate on purpose. It is the container healthcheck: if a slow
Redis probe could fail it, Docker would restart a backend that was serving
traffic perfectly well.

## Metrics

Thresholds live **beside the measurement** in `monitoring/metrics.py`, so the
dashboard, the alert rules and this document cannot drift apart.

| Section | Metric | Amber | Red |
|---|---|---|---|
| Database | reachable | — | not reachable |
| | latency | ≥ 50 ms | ≥ 200 ms |
| | disk free | ≤ 20% | ≤ 10% |
| Redis | reachable | — | not reachable |
| | memory used | ≥ 60% | ≥ 85% |
| Devices | online/total | any offline | all offline |
| | punches awaiting derivation | ≥ 50 | ≥ 500 |
| | punches awaiting mapping | ≥ 1 | ≥ 100 |
| | unmapped enrolments | ≥ 1 | ≥ 20 |
| Sync | failed batches (24 h) | ≥ 1 | ≥ 5 |
| | success rate | ≤ 95% | ≤ 80% |
| Queues | reports stuck | ≥ 1 | ≥ 2 |
| | reports failed (24 h) | ≥ 1 | ≥ 5 |
| Backups | DB / media / verified age | ≥ 26 h | ≥ 48 h, or never run |
| Cron | each job, by its own interval | 1.5× | 2×, or failed |
| Auth | failed logins (15 min) | ≥ 20 | ≥ 200 |
| | lockouts (15 min) | ≥ 1 | ≥ 5 |

Two deliberate choices:

**Derivation and mapping queues are separate.** Punches awaiting mapping are
blocked on an HR decision, not a stuck worker. Conflating them makes a normal
queue look like an outage and hides a real one.

**Approval backlogs are never red.** Open corrections and pending leave are
shown but always green — they are workload, not faults, and nobody should be
paged because HR has not opened their queue yet.

**26 hours, not 24, for backups.** A daily job must not go amber merely because
today's run has not reached its slot.

## Heartbeats

Every scheduled job records `SYSTEM_<JOB>_OK` or `_FAILED` into the audit log.
The registry in `monitoring/heartbeat.py` holds each job's expected interval, so
"last run" becomes "last run, and is that late".

A job that has **never** run is **red**, not blank. Never-run is the most likely
kind of broken, and "no data" reads as "not my problem".

Adding a cron entry without registering it here fails
`test_the_registry_covers_every_cron_line`, so a job cannot ship unmonitored.

**`DEVICE_SYNC` is the one to look at first.** It runs every 5 minutes and is
the only path attendance takes from the terminal into the system. If it stops,
nothing errors and no dashboard looks broken — everybody simply appears not to
have come to work, and the first person to notice is whoever runs payroll. Every
other red tile degrades something; this one silently falsifies the record.

## Alerting

Rules live in `monitoring/alerts.py`, evaluated every 5 minutes by
`check_alerts`.

### Deduplication

The hard part of alerting is not detection; it is not sending the same alert 288
times over a weekend. Alerts fire on **state transitions only**:

| Trigger | Notifies |
|---|---|
| New condition | yes |
| Still firing | no |
| Amber → red | yes (escalation is new information) |
| Firing > 24 h | yes, once (digest) |
| Resolved | yes, once |

State lives in the audit log, not the cache — a Redis restart must not re-fire
every active alert, and "when did this start" is worth answering months later.

### Severity

| Severity | Delivery |
|---|---|
| Critical | Email + webhook; page someone |
| Warning | Email |
| Info | Recorded only, never sent |

**Redis down is Info.** The system is designed to survive it — the cache
degrades, throttles fail open, dashboards poll. Paging at 03:00 for a condition
that degrades nothing trains people to ignore alerts, and then they miss the one
that matters.

### Configuration

```bash
ALERT_EMAILS=ops@nif.org.np,admin@nif.org.np
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...   # optional
```

With neither set, alerts are still **evaluated and recorded** — only delivery is
skipped, a warning is logged, and `/monitoring` shows a banner. A green board
with nobody to tell is worse than no board.

### Testing

```bash
docker compose exec backend python manage.py check_alerts --dry-run
docker compose exec backend python manage.py check_alerts
```

## Logging

All application loggers emit at `DJANGO_LOG_LEVEL` (default INFO), JSON in
production, with a correlation id per request. Before Phase 11 only
`memos`, `leaves` and `audit` were named, so everything else fell to the root
logger at WARNING and every `logger.info` elsewhere was discarded — including
"Cache unavailable, degrading to no-cache".

Optional Sentry via `SENTRY_DSN`. `send_default_pii` is off and credentials are
scrubbed from every event before it leaves the process: this system handles
attendance records and HR documents, so PII in a stack trace is a
data-protection incident of its own.

## Benchmarking

```bash
docker compose exec backend python manage.py benchmark_production --seed --employees 250 --months 24
docker compose exec backend python manage.py benchmark_production
```

Reports p50/p95/max and the cold query count per endpoint against the published
budgets. Run on **PostgreSQL** — the query planner is what is being measured,
and SQLite's is not the one production uses.
