# Operations Runbook

Written to be read at 03:00 by someone who did not build this. Every procedure
is a numbered list you can follow without understanding the codebase.

**First rule: check `/monitoring` before touching anything.** It tells you which
subsystem is unhealthy and what the threshold was, which is usually faster than
guessing from symptoms.

```bash
docker compose -f docker-compose.prod.yml ps          # what is running
docker compose -f docker-compose.prod.yml logs -f --tail=100 backend
curl -s localhost:8000/api/v1/health/                 # liveness (no auth)
```

---

## 1. Alert response

Each alert email names the condition and what to do. This table is the index.

| Alert | Severity | First action |
|---|---|---|
| Database unreachable | Critical | §2.1 |
| Disk space low | Critical | §2.2 |
| Database backup missing | Critical | §3.1 |
| Media backup missing | Critical | §3.1 |
| Backup not verified | Critical | §3.2 |
| Punch queue growing | Critical | §4.2 |
| Login failure spike | Critical | §5.1 |
| Scheduled jobs not running | Critical | §4.4 |
| Device offline | Warning | §4.1 |
| Sync failures | Warning | §4.3 |
| Punches awaiting mapping | Warning | HR maps them in Biometric → Unmapped |
| Redis memory high | Warning | §2.3 |
| Reports stuck / failing | Warning | §4.5 |
| Accounts locking out | Warning | §5.2 |
| Redis unreachable | Info | §2.3 — **no action required** |

---

## 2. Infrastructure

### 2.1 Database unreachable

```bash
docker compose -f docker-compose.prod.yml ps db
docker compose -f docker-compose.prod.yml logs --tail=100 db
docker compose -f docker-compose.prod.yml restart db
```

If it will not start, check disk first (§2.2) — a full disk is the most common
cause, and PostgreSQL refuses to start rather than risk corruption. If the data
directory is damaged, go to §3.3 (restore).

### 2.2 Disk space low

```bash
df -h
docker system df
du -sh /var/lib/docker/volumes/*
```

In order of safety:
1. `docker system prune -f` — removes dangling images and build cache.
2. Old backups: retention is 14 days locally, but off-host copies are the ones
   that matter. Deleting local artifacts that exist off-host is safe.
3. `docker compose exec backend python manage.py purge_expired_reports`
4. Docker logs are capped at 20 MB × 5 per container (Phase 11); if they are
   still large, a container is log-flooding — find it and fix the cause.

**Never** delete anything from the media volume to free space. Those files are
referenced by database rows and there is no way to know which are unused.

### 2.3 Redis

**Redis being down needs no emergency response.** The system is built to survive
it: the cache degrades to a miss, throttles fail open, analytics recompute, and
live dashboards fall back to 20-second polling. Restart when convenient.

```bash
docker compose -f docker-compose.prod.yml restart redis
```

Memory high is different. The policy is `noeviction`, so at the ceiling Redis
**refuses writes** rather than evicting — which surfaces as missing dashboard
events, not an obvious outage. Restart Redis (the cache and channel layer are
both reconstructible) or raise `--maxmemory` in the compose file.

---

## 3. Backup and restore

### 3.1 Backup missing

```bash
docker compose -f docker-compose.prod.yml exec cron ls -la /backups
docker compose -f docker-compose.prod.yml logs cron | grep -i backup
docker compose -f docker-compose.prod.yml exec cron sh /app/deploy/backup.sh
```

Most common causes, in order:
1. `BACKUP_ENCRYPTION_KEY` not set — the cron container now refuses to start
   without it, so this should be impossible on a current deployment.
2. No off-host target. `BACKUP_REQUIRE_OFFHOST=1` fails the job deliberately: a
   local-only copy is not a backup.
3. Disk full (§2.2).
4. The cron daemon is dead (§4.4).

### 3.2 Backup not verified

```bash
docker compose -f docker-compose.prod.yml exec cron python manage.py backup_verify
```

A failure here means the artifact exists but is **not restorable** — treat it as
having no backup at all. Check the passphrase first: verification decrypts, so a
changed `BACKUP_ENCRYPTION_KEY` fails here while the backup job still appears to
succeed.

### 3.3 Database restore

Restores go to a **scratch database** first. Do not skip that.

```bash
docker compose -f docker-compose.prod.yml stop backend cron

docker compose -f docker-compose.prod.yml exec cron \
  sh /app/deploy/restore.sh /backups/nif-leave_system-<STAMP>.dump.gz.enc

# Verify BEFORE promoting
docker compose -f docker-compose.prod.yml exec db \
  psql -U leave_user -d leave_system_restore \
  -c "SELECT count(*) FROM users_user; SELECT max(date) FROM attendance_attendance;"
```

Promote only once the counts look right:

```bash
docker compose -f docker-compose.prod.yml exec db psql -U leave_user -d postgres -c \
  "ALTER DATABASE leave_system RENAME TO leave_system_old;
   ALTER DATABASE leave_system_restore RENAME TO leave_system;"

docker compose -f docker-compose.prod.yml start backend cron
```

Keep `leave_system_old` until the restore is confirmed good. Then §3.5.

### 3.4 Media restore

**Restore media from the same night as the database.** A newer database against
older media produces rows pointing at files that do not exist — every attachment
404s, which reads as a bug rather than a partial restore.

```bash
# Newest full first, then every incremental after it, in order
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
  -in /backups/nif-media-full-<STAMP>.tar.gz.enc | gunzip | \
  tar --listed-incremental=/dev/null -C /app/media -xf -
```

### 3.5 After any restore

- [ ] Sign in; check attendance, a leave balance and a report download
- [ ] Open an attachment (proves media and database are from the same point)
- [ ] `/monitoring` all green
- [ ] Take a fresh backup and verify it
- [ ] Record the incident and the data-loss window
- [ ] **Re-issue biometric device keys if this was a host rebuild** — keys are
      stored hashed and cannot be recovered

---

## 4. Application

### 4.1 Device offline

**192.168.77.201 is a PUSH device.** It dials out to `/iclock/` and keeps its
own buffer, deleting a punch only once the OMS answers `OK`. So no data is lost
while the network, the server or the device itself is down — the terminal
retries on its `ErrorDelay` and everything arrives when the path comes back.

Triage in this order:

1. **Is the OMS answering?** `grep iclock` the backend log. No entries for
   longer than the device's `Delay` (10 s) means it is not reaching us.
2. **Is `/iclock/` routed?** A request that returns the SPA's HTML instead of
   `OK` is the nginx block missing — and that one is actively dangerous, because
   firmware reading 200 as success deletes the punches it just sent.
3. **Is the serial registered?** An unknown serial is refused on purpose, so the
   device retains its data. Register it and everything buffered arrives.
4. Power and network at the terminal itself.

```bash
docker compose -f docker-compose.prod.yml logs --tail=200 backend | grep -i iclock
docker compose -f docker-compose.prod.yml exec backend \
  python manage.py golive_status
```

**SDK path (only if that terminal is pull-mode):**

```bash
docker compose -f docker-compose.prod.yml exec backend \
  python manage.py device_readiness --host 192.168.77.201
docker compose -f docker-compose.prod.yml exec backend \
  python manage.py device_sync --dry-run
```

The dry run reads the terminal and writes nothing, so it is safe at any hour. It
is also the fastest triage: if it prints the user list, the terminal is fine and
the problem is downstream.

`device_sync` runs every 5 minutes and re-reads the terminal's whole log each
time, discarding what is already stored. A missed cycle therefore needs no
catch-up — the next one collects it. `BIOMETRIC_BROADCAST_LIMIT` stops a large
recovery sync flooding open dashboards.

**If the terminal accepts TCP 4370 but never replies**, something else is
holding its SDK connection slot. ZK terminals allow very few concurrent
sessions. Find and close the other client.

### 4.2 Punch queue growing

```bash
docker compose -f docker-compose.prod.yml exec backend \
  python manage.py process_punches
docker compose -f docker-compose.prod.yml logs --tail=200 backend | grep -i derivation
```

Check `/monitoring` for **which** queue is growing. "Awaiting mapping" is an HR
task, not a system fault — those punches belong to device users nobody has
linked to an account. "Awaiting derivation" is the system falling behind.

### 4.3 Sync failures

Two different failures wear the same name.

**Pulled sync (`device_sync`, the normal path).** The command fails loudly with
a message naming the cause — unreachable, comm key wrong, unknown record layout,
transfer truncated. It never reports a partial read as success, because missing
punches are indistinguishable from absent employees. Check the `DEVICE_SYNC`
heartbeat on `/monitoring` first: a red tile means the job stopped running,
which is a different problem from the job running and failing.

**Pushed sync (the HTTP ingest API).** Usually device clock drift. The signature
window is ±300 s, so a client more than five minutes out has every request
rejected.

Either way, `/analytics/devices` shows the drift per terminal; `device_sync`
records it on every run and warns above 300 s. A drifting clock also files
punches near midnight on the wrong day, so fix it on the device rather than
compensating for it.

### 4.4 Scheduled jobs not running

```bash
docker compose -f docker-compose.prod.yml ps cron        # healthcheck greps for the daemon
docker compose -f docker-compose.prod.yml logs --tail=100 cron
docker compose -f docker-compose.prod.yml restart cron
docker compose -f docker-compose.prod.yml exec cron crontab -l
```

Every job is idempotent, so re-run anything that missed its window. `/monitoring`
lists each job with how late it is.

### 4.5 Reports stuck or failing

`reap_stuck_reports` clears stuck runs every 15 minutes automatically. If they
recur, the worker is dying mid-run — check memory limits and the backend logs.
Failures carry their reason in `ReportRun.error`.

---

## 5. Security

### 5.1 Login failure spike

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "
from audit.models import AuditLog
from django.utils import timezone; from datetime import timedelta
from collections import Counter
rows = AuditLog.objects.filter(created_at__gte=timezone.now()-timedelta(hours=1),
                               changes__event='LOGIN_FAILED').values_list('changes', flat=True)
print('by IP   :', Counter(r.get('client_ip') for r in rows).most_common(10))
print('by email:', Counter(r.get('email') for r in rows).most_common(10))"
```

- **One IP, many accounts** → credential stuffing. Block the IP at Cloudflare.
- **Many IPs, one account** → targeted. Contact the user, force a password reset.
- **One IP, one account** → probably a real user. Unlock them (§5.2).

If addresses look internal or are all identical, `TRUSTED_PROXY_DEPTH` is wrong
— see §6.

### 5.2 Unlock an account

Admin → Users → the person → **Unlock**. Or:

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "
from users.login_security import unlock; unlock('person@nif.org.np')"
```

Both are audited. Lockout is 10 failures in 15 minutes, releasing after 15
minutes on its own.

### 5.3 Suspected compromise

1. Deactivate the account (Admin → Users → Deactivate) — this also invalidates
   refresh at the next rotation.
2. Read their audit trail: `AuditLog.objects.filter(actor__email=...)`.
3. Rotate `DJANGO_SECRET_KEY` if session integrity is in doubt — **note this
   invalidates every signed media URL and every session**.
4. Re-issue biometric device keys if a device key may have leaked.

---

## 6. Verifying TRUSTED_PROXY_DEPTH

This setting decides both rate-limit identity and the address in the audit log.
Too low = everyone shares a throttle bucket (safe, visible). Too high = caller
input is trusted (the bypass).

```bash
docker compose -f docker-compose.prod.yml exec backend \
  python manage.py verify_proxy_config --from-audit
```

If it reports private addresses or a single address for all traffic, raise the
value by one and re-check.

---

## 6a. Uploads failing with a permission error

Symptom: everything works, but any upload (profile photo, memo attachment,
correction evidence) fails, and the backend log shows a permission denied on
`/app/media`.

Cause: the backend now runs as `nifn` (uid 1001). On a deployment that existed
before Phase 11, the `media_data` volume was created while the container ran as
root, so its contents are root-owned.

```bash
docker compose -f docker-compose.prod.yml run --rm --user root backend \
  chown -R 1001:1001 /app/media
docker compose -f docker-compose.prod.yml restart backend
```

Then upload a profile photo to confirm. This is a one-time fix — new files
inherit the right ownership.

---

## 7. Deploy and rollback

```bash
# Deploy
git pull && docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps          # wait for healthy
curl -s localhost:8000/api/v1/health/

# Rollback
git checkout <previous-tag>
docker compose -f docker-compose.prod.yml up -d --build
```

**Take a backup before any deploy that includes a migration.** Migrations run
automatically on container start and Django does not roll them back for you.

Feature kill switches (no deploy needed):

| Flag | Effect |
|---|---|
| `ANALYTICS_ENABLED=0` | Analytics routes 404; nothing else affected |
| `LOGIN_LOCKOUT_ENABLED=False` | Throttling and auditing continue; no account locks |
| `REPORTS_RUN_SYNC=True` | Report generation runs inline instead of in a thread |
