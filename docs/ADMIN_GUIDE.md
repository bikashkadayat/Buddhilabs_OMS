# Administrator Guide

For the person who runs NIF OMS day to day: user accounts, roles, org structure,
policy data, devices, and the scheduled jobs that keep it all moving.

**Related:** [DEPLOYMENT](DEPLOYMENT.md) (install) ·
[RUNBOOK](RUNBOOK.md) (when something breaks) ·
[DISASTER_RECOVERY](DISASTER_RECOVERY.md) (when the host is gone) ·
[MONITORING](MONITORING.md) · [GO_LIVE](GO_LIVE.md)

---

## 1. The four roles, and what they actually mean

The database stores four permission roles. The UI shows business names; the
engine and every server-side check use the internal values, and the two must
never be confused when reading code or logs.

| Shown in the UI | Stored as | Scope |
|---|---|---|
| Employee | `maker` | Their own records only |
| Department Head | `checker` | Their team, plus their own |
| HR | `approver` | Organisation-wide |
| Admin | `admin` | Organisation-wide + system administration |

Two rules worth knowing before you assign anybody:

- **Admin is an oversight role, not a super-employee.** It deliberately has *no*
  personal self-service: an Admin cannot apply for leave or create a memo. If
  the person also needs to do their own work in the system, they need a second,
  non-Admin account — or, better, they should be HR.
- **Appraisal visibility does not follow the org chart.** A Department Head is
  *not* entitled to the appraisal of everybody in their department — only of the
  people named as their direct reports on each appraisal. Committee membership
  is per-appraisal and grants nothing beyond it.

Two further attributes are independent of the permission role and are set
per-user: **employee type** (the org-chart rank) and **employment type**
(permanent / probation / intern / volunteer, which drives the leave engine).

---

## 2. Routine administration

All of these are under **Administration** in the sidebar (Admin only).

| Task | Where |
|---|---|
| Create / deactivate users, set roles | `/admin/users` |
| Employees and departments | `/admin/leaves/employees` |
| Leave policies and leave types | `/admin/leaves/policies` |
| Public holidays | `/admin/leaves/holidays` |
| Biometric devices | `/admin/biometric-attendance` |
| Bulk leave actions | `/admin/leaves/bulk-actions` |
| System health | `/monitoring` |

### Deactivating somebody who is leaving

**Deactivate, never delete.** Records carry snapshot names (`employee_name`,
`supervisor_name`, `decided_by_name`) precisely so a departure cannot turn a
historical memo, task or appraisal into a blank cell. Deleting the user row is
also refused wherever it would orphan a record.

Before deactivating, check what is still with them: open work queues, tasks
where they are the reviewer, and appraisals where they are the named supervisor
or a committee member. Reassign those first — HR can change an appraisal's
supervisor or committee at any stage.

---

## 3. Scheduled jobs

Twenty jobs run from `backend/deploy/crontab` inside the `cron` container.
Every one records a heartbeat, so a job that stops running becomes a red tile on
`/monitoring` and an alert, rather than silence.

**Any job added to the crontab must also be registered in
`monitoring.heartbeat.CRON_JOBS`** — a guard test fails the build otherwise, and
an unregistered job is reported as unmonitored.

The ones worth knowing by name:

| Job | When | Critical | If it stops |
|---|---|---|---|
| `device_sync` | every 5 min | ⛔ yes | Nobody is recorded as present; dashboards look plausible until payroll |
| `process_punches` | every 10 min | ⛔ yes | Punches never become attendance |
| `check_alerts` | every 5 min | ⛔ yes | Nothing alerts on anything |
| `backup.sh` | 01:30 daily | ⛔ yes | No recoverable database |
| `backup_media.sh` | 02:15 daily + Sun full | ⛔ yes | Rows point at files that do not exist |
| `backup_verify` | 03:15 daily | ⛔ yes | Backups become an untested hypothesis |
| `send_task_reminders` | 07:00 daily | ⛔ yes | **Fails silently** — nobody is chased, no escalation climbs, and the first symptom is a missed deadline |
| `snapshot_task_evidence` | 23:30 daily | no | One day of evidence granularity; recoverable with `--date` |

Run any of them by hand:

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py <command>
```

`send_task_reminders` and `snapshot_task_evidence` are both safe to re-run:
sends are guarded by a (task, kind, day) unique row, and snapshots are keyed by
date.

---

## 4. Opening an appraisal cycle

1. **People & Attendance → Appraisal → Cycles → New cycle.** Set the period and
   the three deadlines. Deadlines are **advisory** — they drive reminders and
   the overdue columns, and never lock somebody out of their own appraisal.
2. **Activate** the cycle.
3. **Open an appraisal** per employee, naming the supervisor and, where a view
   from outside the reporting line is wanted, a committee.
4. Watch **Cycle dashboard → Review Delays** for records that have stopped
   moving. It names who each one is with.

A cycle holding appraisals cannot be deleted — only closed. Those are records
somebody may have to produce years later.

---

## 5. Things that will bite you

- **`BACKUP_ENCRYPTION_KEY` must be stored offline, separately from the
  backups.** An encrypted backup whose key sits in the same place is not a
  backup. The cron container refuses to start without it.
- **`BACKUP_REQUIRE_OFFHOST=1` is deliberate.** A local-only copy is not a
  backup, and the job fails rather than pretending.
- **The database stores only *paths* to uploaded files.** Restoring a database
  without its matching media archive gives you rows pointing at files that do
  not exist, which reads as a bug rather than a partial restore. Always restore
  a DB and a media archive **from the same night**.
- **`TRUSTED_PROXY_DEPTH` must match your actual proxy chain.** Set it wrong and
  client IPs in the audit log are your proxy's. See RUNBOOK §6.
