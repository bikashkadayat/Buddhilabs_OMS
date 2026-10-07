# Database backup & restore runbook

Automated daily via the `cron` container (`backend/deploy/crontab` → `deploy/backup.sh`).

## What runs
`pg_dump -F c` → `gzip -9` → `openssl aes-256-cbc (pbkdf2)` → off-host copy → retention rotation.
Files: `nif-<db>-<timestamp>.dump.gz.enc` in `/backups` (a named volume) and, if configured,
copied to S3 or a remote host.

## Configure (`.env`)
```dotenv
BACKUP_ENCRYPTION_KEY=<a long random passphrase — store OFFLINE, separate from backups>
BACKUP_RETENTION_DAYS=14
# choose ONE off-host target:
BACKUP_S3_BUCKET=s3://my-bucket/nif-backups        # needs aws-cli in the image
# BACKUP_RSYNC_TARGET=backupuser@host:/srv/nif-backups   # needs scp/ssh key
```
> Without an off-host target the backup is **local only** (the script warns) — set one before go-live.
> For S3, add `awscli` to `Dockerfile.backend` (or use S3 media + bucket versioning).

## Run a backup manually
```bash
docker compose -f docker-compose.prod.yml exec cron sh /app/deploy/backup.sh
```

## Restore (into a SCRATCH db first — verify before promoting)
```bash
docker compose -f docker-compose.prod.yml exec cron \
  sh /app/deploy/restore.sh /backups/nif-leave_system-2026-07-14_013000.dump.gz.enc
# -> restores into leave_system_restore; inspect it, then repoint / rename when correct.
```

## Verified
The dump → encrypt → decrypt → restore roundtrip was tested: source vs restored
`users_user` row counts matched exactly (7 = 7). Re-run this check after any change
to the backup pipeline.

## Media
Uploaded files live in the `media_data` volume. Either snapshot that volume alongside
the DB, or move media to S3 (`USE_S3=True`) and enable bucket versioning.

---

# Phase 11 additions

The audit (`docs/PHASE_11_PRODUCTION_READINESS.md`, findings H3 and H4) found
two gaps in the backup system that this section closes.

## What changed, and why

**1. Media is now backed up.** Only PostgreSQL was. The `media_data` volume
holds memo attachments and vouchers, attendance-correction evidence, profile
photos and generated reports — and the database stores only the *paths* to those
files. A media loss therefore left the system looking perfectly intact while
every download 404'd, which is a worse failure mode than an obvious outage.

**2. Backups are verified and their failure is loud.** Previously
`BACKUP_ENCRYPTION_KEY` was optional in compose but mandatory in `backup.sh`, so
an unset key meant the job aborted **every single night in silence**. The first
anyone would have learned of it was a restore with nothing to restore.

## Schedule

| Job | When | Retention |
|---|---|---|
| Database full | 01:30 daily | 14 local / 90 off-host |
| Media incremental | 02:15 daily | 14 local |
| Media full | 02:45 Sunday | 42 local (3× incremental) |
| **Verify** | 03:15 daily | heartbeat only |

Ordering matters: database, then media, then verification. That way the artifact
being verified is the one just written, and a restore drill always pairs a
database and a media archive **from the same night**.

## Verification

`backup_verify` performs a real restore into a scratch database, compares
`users_user`, `attendance_attendance` and `audit_auditlog` against production,
then drops the scratch database.

Each sentinel is judged against what production actually holds. A table with
rows in production and none in the restore is a shell and fails; a table more
than 20% short of production is stale or truncated and fails; a table that is
**empty in production too** proves nothing about the artifact, so it is skipped
and said so in the output. That last case is not hypothetical: before the
attendance terminal is registered, `attendance_attendance` is legitimately
empty, and the drill used to fail every night against a restore that was
perfect. If no sentinel has any production rows the drill fails outright, rather
than reporting a pass it never earned.

```bash
docker compose -f docker-compose.prod.yml exec cron python manage.py backup_verify
```

It fails loudly on: a wrong passphrase, a checksum mismatch, a restore that
produces an empty schema, or row counts far below production. Each writes a
`SYSTEM_BACKUP_VERIFIED_FAILED` heartbeat, which the alert rules escalate as
Critical.

## Fail-closed guards

| Guard | Effect |
|---|---|
| `BACKUP_ENCRYPTION_KEY` is `:?` in compose | The cron container refuses to start without it |
| `BACKUP_REQUIRE_OFFHOST=1` | A local-only backup fails the job. A local copy is not a backup |
| Dump size < 1 KB | Fails — that is an error that exited 0, not a database |
| `trap` on every exit path | Writes a FAILED heartbeat before exiting. Uses `${LINENO-unknown}`: these scripts run under dash, which has no `LINENO`, and the bare form killed the trap before the heartbeat |
| Partial artifact removed on failure | `openssl` creates its output file before the pipeline finishes, so an abort left a truncated stub that `backup_verify` would then pick as the newest artifact |

## What is deliberately NOT backed up

**Redis.** Restoring stale throttle counters and dead channel-layer keys has no
value; AOF already covers restart survival, and both the cache and the channel
layer are fully reconstructible.

**The `.env` file.** It contains `BACKUP_ENCRYPTION_KEY`, so putting it in a
backup encrypted with a key it contains is circular. It belongs in a password
manager — and the go-live checklist requires it to be recoverable by a **second
person**.

## Monitoring

`/monitoring` shows the age of each of the three backup heartbeats. Amber at 26
hours (not 24 — a daily job must not go amber merely because today's run has not
reached its slot), red at 48 hours or never-run. All three raise Critical alerts.

## Media restore

Restore the newest **full** archive first, then every **incremental** after it,
in order:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
  -in /backups/nif-media-full-<STAMP>.tar.gz.enc | gunzip | \
  tar --listed-incremental=/dev/null -C /app/media -xf -
```

Full procedure and the post-restore checklist: `docs/RUNBOOK.md` §3.

## Drill record

A restore drill is only worth what its last real run proves. Record each one
here.

| Date | Artifact | Backup written | Restore completed | Result |
|---|---|---|---|---|
| 2026-09-21 | `nif-leave_system-2026-09-21_141518.dump.gz.enc` (372,448 B) | 14:15:18 +0545 | 14:15:23 +0545 | **PASS** - checksum OK, restored into `leave_system_verify`, 18 users / 1,871 audit rows consistent with production, scratch database dropped |
| 2026-09-21 | `nif-media-full-2026-09-21_140716.tar.gz.enc` (254,090,240 B) | 14:07:16 +0545 | 14:07:41 +0545 | **PASS** - checksum OK, 3,325 of 3,325 files restored, one file per category byte-identical (memos, tasks, inventory, circulars, minutes, profiles) |

Media restore verification is a decrypt-and-extract into a scratch path followed
by a file count and a hash comparison against the live tree - never into
`/app/media` itself, for the same reason `backup_verify` never restores over the
production database.
