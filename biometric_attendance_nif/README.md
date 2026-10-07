# morx-collector

Attendance collector for **any ZK-protocol biometric terminal** — Morx BioTime,
ZKTeco, and the many clones that speak the same protocol. It connects to one or
more devices, syncs the employee roster, backfills stored punches, then streams
live scans into CSV or SQLite.

No IP, port, or model is hardcoded. Pointing it at a different device is one
line of config.

---

## Quick start

```bash
cd /home/dell/Desktop/test

# 1. Activate the virtualenv
source zk-env/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure your device
cp .env.example .env
# then edit .env and set MORX_DEVICE_HOST to your device's IP

# 4. Check the config is what you expect (talks to nothing)
python -m morx config

# 5. Probe the device
python -m morx info

# 6. Collect
python -m morx run


# 7. Look at what was collected
python -m morx web --open
```

Stop with `Ctrl-C`. Shutdown is graceful — in-flight writes finish first.

---

## Running the tests

The suite is **fully offline**: every device is faked, so it passes on a laptop
with no hardware on the network.

```bash
source zk-env/bin/activate
pip install -r requirements.txt      # installs pytest

pytest tests/ -q                     # all tests, quiet
```

Expected output:

```
........................................................................ [100%]
83 passed in 3.80s
```

Other useful invocations:

```bash
pytest tests/ -v                          # show each test name
pytest tests/test_storage.py              # one file
pytest tests/ -k "dedup"                  # one topic
pytest tests/ -k "sqlite"                 # one backend
pytest tests/ -x                          # stop at first failure
pytest tests/ --tb=short                  # shorter tracebacks
```

If `pytest: command not found`, the virtualenv is not active — use
`./zk-env/bin/python -m pytest tests/ -q` instead.

### What the tests cover

| File | Covers |
| --- | --- |
| `tests/test_models.py` | Punch labelling, whitespace trimming, and the dedup key — including that the *same* punch seen live and in the backlog collapses to one row |
| `tests/test_config.py` | Env parsing, validation errors, single-device and multi-device inventories, typo detection |
| `tests/test_storage.py` | Idempotent writes on both backends, employee upsert, legacy-CSV migration, dedup surviving a restart |
| `tests/test_collector.py` | Retry/backoff on an offline device, roster sync, graceful stop, backlog + live not double-writing |
| `tests/test_web.py` | Dashboard: the mixed-layout CSV collapsing to one row per punch, roster dedup, wall-clock timestamps surviving the trip to the browser, the HTTP endpoints over a real socket, and that the built React bundle's assets resolve |

`tests/conftest.py` clears all `MORX_*` variables before each test, so your real
`.env` can never leak in and change a result.

### Testing against real hardware

Only these two commands touch a device, and neither writes to it:

```bash
python -m morx info      # read-only probe: identity, employee count, date range
python -m morx sync      # one backlog pull, then exit
```

`sync` is safe to run repeatedly — writes are idempotent, so a second run
reports `0 new`. That is the quickest end-to-end proof that the whole path
works.

---

## Commands

| Command | Does |
| --- | --- |
| `python -m morx run` | Collect continuously. Reconnects on its own. This is the service. |
| `python -m morx sync` | Pull the stored backlog once and exit. Good for cron. |
| `python -m morx info` | Probe each device and print what it reports. |
| `python -m morx config` | Print resolved settings without touching the network. |
| `python -m morx web` | Serve the read-only dashboard over the collected data. |

Global flags: `--log-level DEBUG`, `--version`.

Exit codes: `0` ok, `2` configuration error, `3` a device could not be reached.

---

## Configuration

All settings are environment variables, read from the real environment or
`.env`. See `.env.example` for the annotated list.

### One device

```bash
MORX_DEVICE_HOST=192.168.77.201
MORX_DEVICE_PORT=4370
```

### Many devices

Point `MORX_DEVICES_FILE` at a JSON inventory (see
`config/devices.example.json`):

```json
{
  "defaults": { "port": 4370, "timeout": 10 },
  "devices": [
    { "name": "main-gate",  "host": "192.168.77.201" },
    { "name": "warehouse",  "host": "192.168.77.202", "force_udp": true }
  ]
}
```

Each device gets its own thread. One going offline retries on its own and
cannot stall the others.

### Making it work with an awkward device

| Symptom | Setting |
| --- | --- |
| Connects then immediately drops | `MORX_DEVICE_FORCE_UDP=true` — older clones are UDP-only |
| `connection failed` but the device pings fine from a browser | `MORX_DEVICE_OMIT_PING=true` — ICMP is blocked or you lack raw-socket permission |
| Employee names are mojibake | `MORX_DEVICE_ENCODING=latin-1` (or `gbk`) |
| Times out on a slow link | raise `MORX_DEVICE_TIMEOUT` |
| `Unauthenticated` / refuses commands | set `MORX_DEVICE_PASSWORD` to the device's comm key |

---

## Storage

Set `MORX_STORAGE_BACKEND` to `csv` (default) or `sqlite`.

Both backends are **idempotent**: a punch is identified by
`(device, employee_id, timestamp, punch)`, so re-running the collector or
replaying a device's backlog never duplicates a row. This is the main
correctness property — the collector re-pulls the full backlog on every
reconnect *on purpose*, because that is what backfills punches missed while it
was offline.

### CSV (default)

`data/attendance.csv`, `data/employees.csv`. Employees are rewritten rather
than appended, so the roster file no longer grows on every sync.

### SQLite

```bash
MORX_STORAGE_BACKEND=sqlite
MORX_SQLITE_PATH=data/attendance.db
```

Dedup is enforced by a `UNIQUE` constraint. WAL mode is on, so reporting
queries can read while the collector writes:

```bash
sqlite3 data/attendance.db \
  "SELECT employee_id, name, timestamp, punch_label FROM attendance
   ORDER BY timestamp DESC LIMIT 10;"
```

### Migrating existing data

Your current `data/*.csv` files use the older column set. They are upgraded
**automatically** the first time the collector opens them, and the originals are
kept as `attendance.csv.bak` / `employees.csv.bak`. Nothing is lost — the old
`employees.csv` had duplicate rows from repeated syncs, and the migration
collapses them.




---

## Dashboard

```bash
python -m morx web            # http://127.0.0.1:8765
python -m morx web --open     # ...and open a browser
python -m morx web --port 9000 --host 0.0.0.0
```

It reads the same `data/` the collector writes and **never writes to it**, so
running it against a live deployment is safe — SQLite is opened read-only, and
CSV is only ever read. Run it alongside `morx run`; it notices new punches
within about 20 seconds and refreshes itself.

The UI is React (source in `frontend/`); the built bundle is committed under
`morx/web/static/`, so **the commands above need no Node** — only editing the
UI does. See *Working on the UI* below.

**The Python side stays dependency-free.** The server is `http.server`, and
nothing is fetched from a CDN, so it still works on a box with no internet.

### The five tabs

Everything is scoped by one toolbar at the top — date range, and a **Settings**
popover for the workday start and (with more than one terminal) the device. Every
figure on screen is computed from the same slice, so the numbers always agree.

| Tab | Shows | Answers |
| --- | --- | --- |
| **Overview** | hero figure + five stat tiles, people-present-per-day with a 7-day average, and a calendar of every day shaded by headcount | how are we doing overall |
| **Register** | one row per person, one column per day, shaded by time on site | who was in on the 14th, and whose row is empty |
| **People** | punctuality ranges (earliest → latest arrival with a median dot, against the workday line), then a sortable table with an inline bar per attendance rate | who is reliably on time, and who swings |
| **Daily pattern** | people on site through the day, attendance by weekday, first/last punch by hour, weekday × hour heatmap | when is the office actually busy |
| **Trends** | arrival and departure over time, day-length distribution, punch-type mix | is anything drifting, and how spread out are the days |

Clicking an employee opens a **drawer** over the page: their six headline
figures, a daily-span chart where each bar runs from that day's first punch to
its last against the workday line, and a row per day with the punch sequence.

A few of these are worth knowing how to read:

* **Register** is the one the aggregate charts cannot replace. The calendar says
  the total was low on a given day; only the register says *whose* row is empty,
  and vertical stripes across the whole team are how weekends and holidays show
  up. It caps at the most recent 92 days — a wider grid stops being readable —
  and says so when it truncates.
* **People on site through the day** counts an employee-day for every hour
  between its first and last punch, so it shows what is true at 3pm rather than
  just when the edges of the day happen.
* **Punctuality** exists because a median hides the thing that matters: someone
  always at 10:20 and someone swinging between 08:00 and 13:00 have the same
  median and are completely different situations. The line length is that
  difference.
* **Attendance by weekday** divides by *calendar* days in range, not active
  ones, so a weekend nobody works averages zero instead of being hidden.
* **Arrival and departure over time** puts later *higher* (unlike the drawer's
  day-planner chart, which reads downward like a schedule), and flags the last
  point when it is today and still in progress — otherwise a half-finished day
  reads as a sudden collapse in departure times.

Each chart has a **Table** toggle showing the same numbers as text, and the tab,
range and open employee live in the URL, so a view can be pasted to someone else.

### Working on the UI

```bash
cd frontend
npm install

npm run dev      # starts the API *and* the UI, with hot reload
npm run build    # rebuilds morx/web/static/ — commit the output
```

`npm run dev` starts `python -m morx web` on :8765 for you and proxies `/api`
to it, so there is nothing to remember about a second terminal — and `Ctrl-C`
stops both. If the API is already running it says so and reuses it, rather than
failing on the port. Open the URL Vite prints: it prefers :5173 but steps to the
next free port if something else has it.

| | |
| --- | --- |
| `npm run dev:ui` | UI only, for when you are already running the API yourself |
| `PYTHON=./venv/bin/python npm run dev` | if `python3` is not your interpreter |
| `MORX_PORT=9000 npm run dev` | run the API somewhere other than :8765 |

After `npm run build`, commit what lands in `morx/web/static/` — that is what
gets served in production, and it is why deploying needs no Node. Editing
`frontend/src` without rebuilding leaves `python -m morx web` serving the old UI.

**`ECONNREFUSED 127.0.0.1:8765`** in the Vite terminal means the API isn't
running — that is what `npm run dev` exists to prevent. Start it with
`python -m morx web`, or use `npm run dev` instead of `npm run dev:ui`.

Charts are hand-rolled SVG components rather than a charting library: it keeps
the bundle at ~74 KB gzipped, avoids a CDN, and keeps full control of the
palette (which is validated for colour-vision deficiency in both themes).
Colours are referenced as CSS custom properties through `style`, never as
resolved hex — which is why switching theme repaints every chart without React
re-rendering one.

### Downloading reports

**Download ▾** in the toolbar exports everyone; the same button in an opened
employee's drawer exports just that person. Three CSVs, each scoped to whatever
the toolbar currently says:

| Report | One row per | Columns |
| --- | --- | --- |
| **Employee summary** | employee | days present, active days in range, attendance %, median arrival / departure / day length (as `h:mm` *and* decimal hours), late days, late %, punches, first and last seen |
| **Daily detail** | employee-day | date, weekday, employee, first punch, last punch, day length, punches that day, late yes/no |
| **Punch log** | punch | date, time, employee, punch code and label, device, `HISTORY` or `LIVE` |

Notes on the exports:

* **What downloads is what you see.** Same date range, device, employee,
  workday start, search box, and sort order as the table on screen. The report
  is built by the same code that drew the charts, so the totals can't disagree
  — the daily detail has exactly the "employee-days" the header claims, and the
  punch log exactly the punch count.
* **People who never came don't have rows to export**, so the summary offers
  *Include people with no punches in range* — they come out as explicit zero
  rows rather than a silent gap, which is usually the point of asking for the
  report.
* Durations are given twice: `8h 07m` to read, `8.11` to sum in a spreadsheet.
  A day with a single punch has no measurable length, so that cell is left
  **empty** rather than reported as zero.
* UTF-8 with a BOM, so Excel opens Nepali and other non-ASCII names correctly.
* Filenames carry the range: `attendance-daily_2026-07-11_to_2026-08-09.csv`,
  or `attendance-daily_Mira-19_2026-07-11_to_2026-08-09.csv` for one person.

The files are generated in the browser — nothing is written to disk on the
server, and no report endpoint is exposed.

### Definitions

* **A day's span** is first punch to last punch on the same calendar day. The
  terminal records scans, not shifts — so a forgotten check-out shortens that
  day rather than running past midnight. Medians are used throughout rather
  than means, because one 04:00 scan should not move a month's figure.
* **Attendance rate** is days present ÷ days on which *anyone* punched. Calendar
  days would be the wrong denominator — nobody punches on a holiday.
* **Late** is the first punch after the *Day starts* time in the filter bar
  (10:15 by default, which is where this data's arrival peak sits). Change it
  and every late figure on the page follows.

### Data it flags rather than hides

The dashboard reports what it found instead of quietly cleaning it up. On the
current `data/attendance.csv` that is:

* **10,985 duplicate rows.** The file holds two column layouts at once — the
  package's eight-column rows, with an older script's six-column rows appended
  behind them. Every punch is in there twice. They are collapsed on read (the
  file on disk is not touched).
* **Employees who punch but aren't in the roster** — an id enrolled and later
  deleted from the device still owns its history.
* **Punches dated in the future** (this file has seven, up to Feb 2033 — the
  device clock was wrong). They are kept, but "last 30 days" is measured from
  today rather than from the newest row, so a bad clock can't empty the view.

### Exposing it

It binds to `127.0.0.1` by default and has **no authentication**. `--host
0.0.0.0` puts every employee's movements on your LAN — put it behind a reverse
proxy with auth if you need that.

---

## Layout

```
morx/
  config.py          Env + JSON config, validated. No hardcoded devices.
  models.py          Employee, AttendanceRecord — the dedup key lives here.
  device.py          The only module that imports pyzk. Connect, read, stream.
  storage.py         Storage interface + CSV and SQLite implementations.
  collector.py       The retry/sync/stream loop. One thread per device.
  cli.py             Argument parsing, signal handling, command dispatch.
  web/
    dataset.py       Read-only loader: mixed CSV layouts, dedup, roster merge.
    server.py        stdlib HTTP server. Static assets + /api/data, /api/meta.
    static/          Built React bundle. Generated — edit frontend/ instead.
frontend/            React source for the dashboard (Vite).
  src/lib/           Time, formatting, analytics, CSV reports — no React here.
  src/components/    Shell, drawer, tables; charts/ has one file per chart.
  src/views/         One file per tab.
tests/               Offline suite — no hardware needed.
config/              Example multi-device inventory.
deploy/              systemd unit.
data/                CSV / SQLite output.
```

The layering matters: `pyzk` is confined to `device.py`, so swapping the driver
or faking a device in tests touches exactly one file. Everything above that line
speaks `Employee` and `AttendanceRecord`.

### Superseded scripts

`morx_collector.py`, `morx_csv_collector.py`, `live_test.py`, and
`data/inspect.py` are the original prototypes. Everything they did is now in the
package (`morx info` replaces `data/inspect.py`, `morx run` replaces both
collectors). They are left in place for reference — delete them when you're
satisfied with the replacement.
