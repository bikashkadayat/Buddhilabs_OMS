# Local Runbook

Commands only. Every one below was run on this machine on 2026-08-06 unless
marked `# not run here`.

Repo root: `/home/dell/Desktop/NIFN_OMS`

---

## 0. Everything at once (start here)

```bash
cd /home/dell/Desktop/NIFN_OMS
./run-local.sh
```

Starts Redis, the backend, the biometric collector and the frontend, and stops
all four on Ctrl-C. First run writes `.env.local` with a generated secret key.

| | |
|---|---|
| Frontend | http://localhost:5173 |
| Backend  | http://localhost:8001 |
| Collector | every 60s from `main-gate` (192.168.77.201) |

```bash
./run-local.sh --check      # verify setup, start nothing
./run-local.sh --no-sync    # app only, no device polling
BACKEND_PORT=8002 ./run-local.sh
SYNC_INTERVAL=30 ./run-local.sh
```

Three things about this machine specifically, all of which the script handles:

* **Backend is on 8001, not 8000.** Port 8000 is taken by an unrelated Laravel
  project. `frontend/.env.local` points Vite's proxy at 8001 to match.
* **The database is SQLite**, at `backend/db.sqlite3` — that is where this
  machine's imported biometric history actually lives (11,052 punches). The
  repo's `.env` selects PostgreSQL because that is the production and
  all-in-Docker layout; a local run pointed there finds an empty database and
  looks like the history has been lost. `run-local.sh` reads `.env.local`
  instead and never touches `.env`.
* **Node.** The default `node` here is 20.12, below the 20.19 Vite needs, and
  the failure is an unrelated-looking `ERR_INVALID_ARG_VALUE` stack trace. The
  script picks a newer nvm install automatically.

Redis is optional. Without it the app still runs, but the channel layer falls
back to per-process memory — so the collector's punch events never reach the web
process and the dashboard updates on its 20-second poll instead of instantly.
`run-local.sh` starts it via `docker-compose.local.yml`, which publishes it on
**6380** (6379 is taken by another project's Redis).

---

## 0. Prerequisites

```bash
python3 --version          # 3.12.x
node --version             # MUST be >= 20.19  (package.json engines)
docker --version
```

Node 20.12 fails the Vite build. If `node --version` is below 20.19:

```bash
nvm install 22 && nvm use 22
```

---

## 1. Environment file

```bash
cd /home/dell/Desktop/NIFN_OMS
cp .env.example .env
python3 -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50))" >> .env
```

Then edit `.env` and set at minimum:

```
DJANGO_DEBUG=True
DATABASE_PASSWORD=<anything, for the local postgres container>
BIOMETRIC_DEVICE_COMM_KEY=0
BIOMETRIC_DEVICE_TIMEOUT=90
```

---

## 2. PostgreSQL + Redis

```bash
cd /home/dell/Desktop/NIFN_OMS
docker compose up -d db redis
```

Verify:

```bash
docker compose ps db redis
docker exec nif-db pg_isready -U leave_user -d leave_system
docker exec nif-redis redis-cli ping
```

Expected:

```
nif-db      Up (healthy)
nif-redis   Up (healthy)
/var/run/postgresql:5432 - accepting connections
PONG
```

Host port for postgres is **5434** (not 5432). Redis has **no host port** in the
dev compose file — a backend run on the host leaves `REDIS_URL` unset and uses
the in-memory fallback. That is supported.

---

## 3. Backend

```bash
cd /home/dell/Desktop/NIFN_OMS/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/development.txt
```

`development.txt` includes `base.txt`. `production.txt` is for deploys only.

### 3a. Choose a database

**SQLite (fastest, what the biometric import was verified against):**

```bash
export DJANGO_SECRET_KEY=dev-secret
export DATABASE_ENGINE=sqlite3
export DJANGO_DEBUG=1
```

**PostgreSQL (matches production):**

```bash
export DJANGO_SECRET_KEY=dev-secret
export DATABASE_ENGINE=postgresql
export DATABASE_HOST=localhost
export DATABASE_PORT=5434
export DATABASE_NAME=leave_system
export DATABASE_USER=leave_user
export DATABASE_PASSWORD=<same as .env>
export DJANGO_DEBUG=1
```

### 3b. Migrate and run

```bash
python manage.py migrate --no-input
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```

`collectstatic` is **not needed** for `runserver`. Only for production:

```bash
python manage.py collectstatic --no-input      # not run here
```

Expected on startup:

```
Django version 6.0.6, using settings 'config.settings'
Starting development server at http://0.0.0.0:8000/
```

---

## 4. Frontend

```bash
cd /home/dell/Desktop/NIFN_OMS/frontend
npm install
npm run dev
```

Expected:

```
VITE ready in ... ms
➜  Local:   http://localhost:5173/
```

---

## 5. Everything in Docker instead

```bash
cd /home/dell/Desktop/NIFN_OMS
docker compose up -d
docker compose ps
docker compose logs -f backend
```

Ports: backend `8001`, frontend `5173`, postgres `5434`.

---

## 6. Biometric — device 192.168.77.201

All read-only on the device except `golive_import`, which writes to the OMS
database only. Run from a host that can reach the terminal.

### 6.1 Diagnose

```bash
python manage.py device_diagnose --host 192.168.77.201 --timeout 60
```

Actual output:

```
Terminal diagnostic — 192.168.77.201:4370
  Connected. Read-only; nothing was written.
  Reported by the device: 32 users, 10902 attendance records
  Device clock: 2026-08-06T16:10:19

  Users
    decoded 32 record(s)
      {'employee_id': '1', 'name': 'Bikashkhatri', 'privilege': 2, ...}

  Attendance
    decoded 10902 record(s)

  Device user IDs read verbatim, e.g. ['1', '4', '5', '9', '8'].
```

### 6.2 Register the device

```bash
python manage.py register_device \
  --label main-gate \
  --host 192.168.77.201 \
  --name "Main Gate" \
  --timezone Asia/Kathmandu
```

### 6.3 Import roster + history

```bash
python manage.py golive_import --device main-gate --timeout 90 --since 2026-07-14 --dry-run
python manage.py golive_import --device main-gate --timeout 90 --since 2026-07-14
```

Whole device history instead of the window:

```bash
python manage.py golive_import --device main-gate --timeout 90 --since 2000-01-01
```

### 6.4 Mapping

```bash
python manage.py roster_report
python manage.py roster_report --csv mapping.csv
python manage.py roster_report --unmapped-only
```

HR maps each enrolment, then **re-run the import** — that is what attributes
the already-imported punches:

```bash
python manage.py golive_import --device main-gate --timeout 90 --since 2000-01-01
```

Actual output after mapping:

```
     1. Total imported users            32
     2. Total imported punches          10902
     3. Total AttendancePunch rows      10895
     4. Total Attendance rows           5665
     5. Mapped employees                32
     6. Unmapped employees              0
```

### 6.5 Verify a real fingerprint

```bash
python manage.py device_sync --device main-gate --timeout 90
python manage.py verify_realtime_punch --device-user-id 11 --minutes 30
python manage.py verify_realtime_punch --any --minutes 30
```

Actual output:

```
  [PASS] Punch received      device user 11 at 2026-08-06 16:27:10 via main-gate
  [PASS] Enrolment known     device user 11 -> Bikashkadayat (NIFN-EMP-0011)
  [PASS] Device ID unchanged the punch carries '11' and the mapping stores '11'
  [PASS] Attendance derived  2026-08-06: status=present, in=10:58, out=16:27, source=biometric
  [PASS] Dashboard / Reports / Analytics
  CHAIN COMPLETE
```

### 6.6 Recurring sync

How a punch gets from a finger to the dashboard:

```
terminal 192.168.77.201:4370          ZK protocol, TCP, read-only
  -> device_sync / device_sync_loop   collector.sync_device
  -> ingest.ingest_punches            dedup, mapping, sync log
  -> AttendancePunch (+ derived Attendance)
  -> events.punch_recorded -> Redis channel layer
  -> /ws/attendance/ -> dashboard     (falls back to a 20s poll)
```

The terminal is **polled, not pushed**: it keeps its entire log, every run
re-reads it, and the unique constraint on `AttendancePunch` discards what is
already stored. That is why a missed cycle needs no catch-up and why re-reading
is safe. (An iClock/ADMS push path also exists at `/iclock/`, but this device is
not configured for it — `BiometricDevice.serial_number` is empty, and push
requires it.)

Continuous, ~60s latency — what `run-local.sh` starts:

```bash
python manage.py device_sync_loop --device main-gate --interval 60 --timeout 90
python manage.py device_sync_loop --device main-gate --once     # one cycle, then exit
```

Manual, one shot:

```bash
python manage.py device_sync --device main-gate --timeout 90
```

Both take a per-device lock, so the loop, the crontab and a manual run can all
be active without competing for the terminal's few connection slots. A run that
finds the lock held reports `Skipped:` and exits 0 — the next run collects
whatever it missed.

Cron (already in `backend/deploy/crontab`, runs in the `cron` container):

```
*/5 * * * * cd /app && python manage.py device_sync
```

Host crontab equivalent:

```bash
crontab -e
# */5 * * * * cd /home/dell/Desktop/NIFN_OMS/backend && .venv/bin/python manage.py device_sync --device main-gate >> /tmp/device_sync.log 2>&1
```

### 6.7 Identity check

```bash
python manage.py verify_biometric_ids
python manage.py verify_biometric_ids --expect 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32
```

### 6.8 Import planning / validation

```bash
python manage.py import_plan --from 2026-07-14
python manage.py validate_import --from 2026-07-14
python manage.py process_punches
python manage.py device_readiness --host 192.168.77.201
```

---

## 7. Health checks

### OMS running

```bash
curl -s http://localhost:8000/api/v1/health/
```

Expected: `{"status": "ok", ...}`

### Database connected

```bash
python manage.py check --database default
python manage.py shell -c "from django.db import connection; connection.ensure_connection(); print('db ok', connection.vendor)"
```

### Redis connected

```bash
docker exec nif-redis redis-cli ping
python manage.py shell -c "from django.core.cache import cache; cache.set('k','v',10); print('cache ok', cache.get('k'))"
```

### Biometric device reachable

```bash
python manage.py device_diagnose --host 192.168.77.201 --timeout 60
```

### Collector running

```bash
python manage.py golive_status --from 2026-07-14
docker compose logs --tail=50 cron | grep device_sync
```

Look for: `✅ [PASS  ] Future attendance continues automatically`

### AttendancePunch + Attendance records visible

```bash
python manage.py shell -c "
from biometric.models import AttendancePunch, BiometricEmployee
from attendance.models import Attendance
print('AttendancePunch :', AttendancePunch.objects.count())
print('Attendance      :', Attendance.objects.count())
print('Enrolments      :', BiometricEmployee.objects.count())
print('Mapped          :', BiometricEmployee.objects.filter(user__isnull=False).count())
print('Unmapped        :', BiometricEmployee.objects.filter(user__isnull=True).count())
"
```

Actual output here:

```
AttendancePunch : 10904
Attendance      : 5665
Enrolments      : 32
Mapped          : 32
Unmapped        : 0
```

### Workforce dashboard working

```bash
python manage.py shell -c "
from django.utils import timezone
from django.db.models import Count
from attendance.models import Attendance
t = timezone.localdate()
print(list(Attendance.objects.filter(date=t).values('status').annotate(n=Count('id'))))
"
```

Actual: `[{'status': 'present', 'n': 17}, {'status': 'half_day', 'n': 2}]`

### Analytics working

```bash
python manage.py shell -c "
from django.utils import timezone
from analytics import periods, scope, calendar as acal
from analytics.metrics import attendance as att
d = timezone.localdate()
w = periods.Window(start=d, end=d, granularity='day')
print(att.summary(scope.resolve_org_scope(), w, acal.WorkCalendar(w, holidays=set(), today=d)))
"
```

Actual: `expected_days 32, attended_days 19, present_days 17, half_days 2`

### Reports working

```bash
python manage.py shell -c "
from django.utils import timezone
from reports.workforce_reports import build_attendance_vs_leave
t = timezone.localdate().isoformat()
c, n, ct = build_attendance_vs_leave({'from': t, 'to': t, 'format': 'csv'})
print(n, len(c), 'bytes'); print(c.decode('utf-8-sig').splitlines()[0])
"
```

---

## 8. Troubleshooting

### Device accepts TCP but never replies

```bash
timeout 5 bash -c "echo > /dev/tcp/192.168.77.201/4370" && echo OPEN || echo CLOSED
python manage.py device_diagnose --host 192.168.77.201 --timeout 60
```

If `device_diagnose` fails but the port is open, fall back to the proven driver:

```bash
pip install pyzk==0.9
python manage.py device_diagnose --host 192.168.77.201 --timeout 60   # native
python manage.py golive_import --device main-gate --driver pyzk --since 2026-07-14
```

### Sync succeeds but collects nothing

```bash
python manage.py shell -c "
from biometric.models import BiometricDevice
d = BiometricDevice.objects.get(label='main-gate')
print('last_punch_at =', d.last_punch_at)
"
```

A `last_punch_at` in the future means a device clock glitch. It is clamped
automatically and logged as:

```
WARNING main-gate reports a newest punch dated 2033-02-22, which is in the future
```

To force a full re-read:

```bash
python manage.py device_sync --device main-gate --since 2026-07-14 --timeout 90
```

### Punches imported but no attendance

```bash
python manage.py shell -c "
from biometric.models import AttendancePunch
print('unattributed:', AttendancePunch.objects.filter(user__isnull=True).count())
"
python manage.py roster_report --unmapped-only
python manage.py golive_import --device main-gate --since 2000-01-01 --timeout 90
```

`process_punches` alone will **not** fix this. Re-running `golive_import` runs
the backfill that attributes them.

### Port already in use

```bash
ss -ltnp | grep -E ':(8000|8001|5173|5434)'
docker compose down
```

### Reset the local database

```bash
# sqlite
rm -f backend/db.sqlite3 && python manage.py migrate --no-input

# postgres
docker compose down -v && docker compose up -d db redis && python manage.py migrate --no-input
```

### Tests

```bash
cd backend
export DATABASE_ENGINE=sqlite3 DJANGO_SECRET_KEY=test-secret DJANGO_DEBUG=1
python -m pytest -q
python -m pytest biometric/ -q
ruff check .
python manage.py makemigrations --check --dry-run
```

Both exports matter, and neither failure names its cause:

* Without `DATABASE_ENGINE=sqlite3` the suite tries PostgreSQL on
  localhost:5432, where an unrelated project's server answers and rejects the
  credentials — every test errors with `OperationalError`.
* Without `DJANGO_DEBUG=1`, `SECURE_SSL_REDIRECT` is on (it defaults to
  `not DEBUG`) and every API test gets a 301 instead of its expected status.

---

## 9. One-shot startup

```bash
cd /home/dell/Desktop/NIFN_OMS
docker compose up -d db redis

cd backend
source .venv/bin/activate
export DJANGO_SECRET_KEY=dev-secret DATABASE_ENGINE=sqlite3 DJANGO_DEBUG=1
python manage.py migrate --no-input
python manage.py runserver 0.0.0.0:8000 &

cd ../frontend && npm run dev &

# biometric
cd ../backend
python manage.py device_diagnose --host 192.168.77.201 --timeout 60
python manage.py register_device --label main-gate --host 192.168.77.201 --timezone Asia/Kathmandu
python manage.py golive_import --device main-gate --since 2026-07-14 --timeout 90
python manage.py golive_status --from 2026-07-14
```

Open:

- Frontend `http://localhost:5173`
- API `http://localhost:8000/api/v1/health/`
- Admin `http://localhost:8000/admin/`
