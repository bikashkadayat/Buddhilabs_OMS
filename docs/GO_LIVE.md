# Go-Live Checklist

Six audiences, sequenced T-7 days to launch. Every box is verifiable — if you
cannot demonstrate it, it is not ticked.

**Blocking items are marked ⛔.** Do not go live with any of them open.

---

## 1. Infrastructure — Admin, T-7 days

### Secrets and configuration
- [ ] ⛔ `.env` complete; every `:?` variable set (compose refuses to start otherwise)
- [ ] ⛔ `DJANGO_DEBUG=False`
- [ ] ⛔ `DJANGO_ALLOWED_HOSTS` an explicit host list, no `*`
- [ ] ⛔ `DJANGO_SECRET_KEY` freshly generated, never used elsewhere
- [ ] ⛔ `DJANGO_SECRET_KEY`, `DATABASE_PASSWORD` and `BACKUP_ENCRYPTION_KEY` stored in a password manager, **recoverable by a second person**
- [ ] `CORS_ALLOW_ALL_ORIGINS=False`; `DJANGO_CSRF_TRUSTED_ORIGINS` exact
- [ ] HSTS decision made consciously (`DJANGO_SECURE_HSTS_SECONDS`, preload is hard to reverse)

### Trusted proxy ⛔
- [ ] `TRUSTED_PROXY_DEPTH` set for the real chain (Cloudflare + ingress nginx = 2)
- [ ] Verified with evidence, not reasoning:
      `docker compose exec backend python manage.py verify_proxy_config --from-audit`
- [ ] Output shows varied public addresses, not one repeated private address

> Too low means everyone shares a throttle bucket — visible and harmless. Too
> high means caller-supplied text is trusted as the client IP, which re-opens the
> rate-limit bypass. **Verify, do not assume.**

### Backups ⛔
- [ ] `BACKUP_ENCRYPTION_KEY` set
- [ ] `BACKUP_S3_BUCKET` **or** `BACKUP_RSYNC_TARGET` set (`BACKUP_REQUIRE_OFFHOST=1`)
- [ ] Database backup run manually and succeeded
- [ ] Media backup run manually and succeeded
- [ ] `backup_verify` run and passed
- [ ] **A restore rehearsed on staging** and the wall-clock time recorded
- [ ] Off-host storage has ≥ 90 days of headroom

### Monitoring and alerting ⛔
- [ ] `ALERT_EMAILS` and/or `ALERT_WEBHOOK_URL` set
- [ ] `/monitoring` reachable and all-green for HR and Admin
- [ ] A test alert delivered and **received by a human**
- [ ] `SENTRY_DSN` set, or a conscious decision recorded not to use it

### Containers
- [ ] Backend runs as `nifn`, not root (`docker compose exec backend whoami`)
- [ ] ⛔ **Upgrading an existing deployment:** the `media_data` volume was
      created while the container ran as root, so its contents are root-owned
      and the new `nifn` user cannot write to it. Uploads will fail on the first
      attempt after the upgrade, not at boot — so this must be fixed *before*
      go-live, not diagnosed after:
      ```bash
      docker compose -f docker-compose.prod.yml run --rm --user root backend \
        chown -R 1001:1001 /app/media
      ```
      Then confirm with a real upload (a profile photo is the quickest test).
      A brand-new deployment needs none of this — the volume inherits the
      image's ownership.
- [ ] Memory limits and log rotation active (`docker inspect`)
- [ ] `cron` container healthy (its healthcheck greps for the daemon)
- [ ] Disk ≥ 50 GB free
- [ ] Redis AOF on; `nifn-shared` network exists
- [ ] SMTP verified by a real send

---

## 2. HR — T-3 days

- [ ] ⛔ Every employee created: role, department, employment type, joining date
- [ ] ⛔ Joining dates correct — they set the attendance floor and every analytics denominator
- [ ] Leave types, entitlement rules and balances seeded and spot-checked
- [ ] Holiday calendar loaded for the full year
- [ ] Attendance policy and shifts confirmed against the written NIF rules
- [ ] ⛔ `ATTENDANCE_TRACKING_START` and `ATTENDANCE_POLICY_GO_LIVE` pinned to explicit dates (never left empty in production)
- [ ] ⛔ Biometric roster collected: `device_sync --roster-only`
- [ ] ⛔ Every biometric enrolment mapped — **zero unmapped**
- [ ] ⛔ `verify_biometric_ids --expect <ids read off the terminal>` returns
      **EXACT MATCH** — the device is the source of truth for identity and no
      ID may differ (see `docs/PHASE_12_GO_LIVE.md` §1A)
- [ ] One report of each type generated and checked
- [ ] Analytics reconciled against a manual count for one department
- [ ] HR knows how to unlock an account (Admin → Users → Unlock)

---

## 3. Managers — T-1 day

- [ ] Each department head can sign in and reach their team dashboard
- [ ] Team roster correct: nobody missing, nobody else's team visible
- [ ] Approval queues reachable
- [ ] One leave approval and one correction approval completed end-to-end on staging
- [ ] Department-scoped analytics show their department, and other departments appear only as a rank

---

## 4. Employees — T-1 day

- [ ] Every employee can sign in
- [ ] First-login password change enforced and completed
- [ ] Check-in and check-out work **from a real phone on the real network**
- [ ] Own attendance, leave balance and history are correct
- [ ] Leave application works end-to-end
- [ ] One-page quick guide distributed

---

## 5. Devices — T-2 days

- [ ] Every terminal reachable and powered
- [ ] ⛔ Clocks synchronised — drift < 60 s (the signature window is ±300 s)
- [ ] API keys issued and **stored offline** (they are unrecoverable once shown)
- [ ] Collectors configured and reporting
- [ ] Test punch appears within 2 minutes and derives correctly
- [ ] Offline → recovery tested: unplug, punch, replug, confirm the backlog syncs
- [ ] Device-offline alert fires when a terminal goes quiet

---

## 6. Go-live day

### Before cutover
- [ ] ⛔ Fresh backup taken **and verified**
- [ ] ⛔ All health checks green
- [ ] Support contact published to staff
- [ ] ⛔ Rollback decision point agreed, with a **named decision-maker**

### First 8 hours
- [ ] `/monitoring` checked hourly
- [ ] First check-ins confirmed arriving from both browser and device
- [ ] `DEVICE_SYNC` heartbeat green — it is the only path attendance takes in,
      so a red tile there means nobody is being recorded as present
- [ ] First leave request completes its full workflow
- [ ] Audit log shows real client IPs, not the proxy's
- [ ] No unexpected alerts

### End of day one
- [ ] Attendance for the day reconciled against a manual count
- [ ] Backup ran and verified overnight
- [ ] Every cron job shows a green heartbeat
- [ ] Issues logged with owners

### Week one
- [ ] Daily `/monitoring` review
- [ ] Backup verified daily
- [ ] `verify_proxy_config --from-audit` re-run with a week of real traffic
- [ ] Login failure patterns reviewed
- [ ] Retrospective held; this checklist updated with what was missing

---

## Rollback triggers

Go back if any of these are true, without debate:

- Attendance data is being recorded incorrectly and cannot be corrected in place
- Employees cannot sign in
- Biometric punches are not becoming attendance and the queue is growing
- Any data loss is suspected

Rollback procedure: `docs/RUNBOOK.md` §7.
