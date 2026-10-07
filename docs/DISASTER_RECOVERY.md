# Disaster Recovery

What to do when the host is gone, the database is corrupt, or the media volume
is lost. The procedures live in [BACKUP.md](BACKUP.md) and
[RUNBOOK.md](RUNBOOK.md) §3; **this page is the plan around them** — the
objectives, the decision order, and the drill that proves any of it works.

> **A backup that has never been restored is a hypothesis.** The nightly
> `backup_verify` job restores the newest artifact into a scratch database
> automatically. That covers the database. It does **not** cover the full
> procedure below, which is why §5 exists.

---

## 1. Objectives

| Objective | Target | How it is met |
|---|---|---|
| **RPO** (data you can afford to lose) | ≤ 24 h | Nightly DB backup 01:30, media 02:15 |
| **RTO** — full host loss | ≤ 2 h | Rebuild from `docker-compose.prod.yml` + restore |
| **RTO** — database only | ≤ 15 min | Restore into scratch, verify, promote |
| **Attendance data loss** | ≈ 0 | The terminal keeps its own log; `device_sync` re-reads it in full and discards duplicates |

The attendance row is worth reading twice. Punches are the one dataset that
survives independently of this system: the biometric terminal holds its own
log, and the collector re-reads the whole log every five minutes. A missed
window needs no catch-up, and a restored database re-collects what it lost.

---

## 2. Decide what actually happened

Work down this list. Stop at the first that matches.

| Symptom | It is | Go to |
|---|---|---|
| App is down, data intact | An outage, not a disaster | RUNBOOK §2, §4 |
| Rows missing / bad data, DB up | A data incident | §3 (restore to scratch, compare, promote) |
| Database will not start or is corrupt | DB loss | §3 |
| Files 404 but rows exist | Media loss | §4 |
| Host is gone | Full loss | §3 + §4, after rebuilding the stack |

**Never restore straight over production.** Every procedure below restores into
a *scratch* target first, precisely so you can compare before you commit.

---

## 3. Database recovery

```bash
# 1. What have we got?
docker compose -f docker-compose.prod.yml exec cron ls -la /backups

# 2. Restore the chosen artifact into a SCRATCH database (the default target).
docker compose -f docker-compose.prod.yml exec cron \
  sh /app/deploy/restore.sh /backups/nif-<db>-<STAMP>.dump.gz.enc
#    -> restores into <db>_restore

# 3. Verify BEFORE promoting. Compare row counts against what you expect.
docker compose -f docker-compose.prod.yml exec db \
  psql -U "$DATABASE_USER" -d "<db>_restore" \
  -c 'SELECT count(*) FROM users_user;' \
  -c 'SELECT count(*) FROM attendance_attendancerecord;'

# 4. Promote: stop the app, rename, restart.
docker compose -f docker-compose.prod.yml stop backend web cron
#    rename <db> -> <db>_broken, <db>_restore -> <db>
docker compose -f docker-compose.prod.yml up -d
```

**Pair it with the media archive from the same night** (§4). A newer database
against older media yields rows pointing at files that do not exist.

---

## 4. Media recovery

Uploaded files — memo and task attachments, evidence, photos — live in the
`media_data` volume. **The database stores only paths to them.**

Restore the newest **full** archive first, then every **incremental** after it,
in order:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
  -in /backups/nif-media-full-<STAMP>.tar.gz.enc | gunzip | \
  tar --listed-incremental=/dev/null -C /app/media -xf -
```

The Sunday full backup resets the incremental chain, so a restore never needs
every archive ever written — at most one full plus six incrementals.

---

## 5. The drill — do this quarterly

The nightly verification proves the *database artifact* is restorable. It does
not prove that **you** can bring the system back, that the encryption key is
where you think it is, or that media and database line up. Only a drill does.

1. On a scratch host, bring up the stack from `docker-compose.prod.yml`.
2. Fetch **last night's** database and media artifacts from the off-host target
   — not from the local `/backups` volume, which would not exist in a real host
   loss.
3. Restore both (§3, §4).
4. Log in. Then check the things that only break when DB and media disagree:
   - open a memo with an attachment and **download it**;
   - open a task with evidence files and download one;
   - open an appraisal and confirm its cited evidence still shows its figures;
   - check `/monitoring` for red tiles.
5. Record the wall-clock time and compare it with the RTO targets in §1.
6. Tear the scratch host down.

**Write down what went wrong.** A drill that produced no findings usually means
the drill was too gentle.

---

## 6. Failure modes that have actually been designed for

| Risk | Mitigation | Where |
|---|---|---|
| Backup encrypted, key lost with the host | `BACKUP_ENCRYPTION_KEY` stored **offline and separately** | ADMIN_GUIDE §5 |
| Local-only backups | `BACKUP_REQUIRE_OFFHOST=1` fails the job rather than pretending | BACKUP.md |
| Backup silently stops | Heartbeat → amber 26 h, red 48 h, Critical alert | MONITORING.md |
| Restore never tested | Nightly `backup_verify` into a scratch DB | BACKUP.md |
| DB restored without media | Cron ordering pairs them nightly; §3 and §4 say to take both | crontab |
| Attendance gap during the outage | Terminal keeps its own log; collector re-reads it in full | §1 |

## 7. What is deliberately not backed up

Generated report files, expired autosave drafts, and the container images
themselves. All are reproducible: reports regenerate, drafts expire by policy,
images rebuild from the repository. See BACKUP.md § "What is deliberately NOT
backed up" for the full list and the reasoning.
