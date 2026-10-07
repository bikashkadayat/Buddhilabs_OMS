# Phase 10 — Executive Analytics & Intelligence Layer

**Status:** Approved and implemented. See §11 for where the built system differs
from this design and why.
**Audience:** Management, HR leadership, Executive, Directors.
**Scope boundary:** insight only. Phase 10 writes no operational data, approves nothing,
and never scores an individual employee.

---

## 0. Grounding — what already exists

Phase 10 is built entirely on top of what Phases 5–9.1 shipped. Nothing here is speculative:

| Capability | Where it lives today |
|---|---|
| Role-scoped employee resolution | `attendance/dashboard_views.py::scoped_employees` (re-exported by `attendance/workforce/aggregates.py`) |
| Grouped attendance aggregates, no N+1 | `attendance/workforce/aggregates.py` |
| Operational dashboards (me / team / HR) | `attendance/workforce/dashboards.py` |
| 8 workforce reports, Excel/PDF/CSV | `reports/workforce_reports.py` + `BUILDERS` registry |
| Async report generation, download, retention, scheduling | `reports/report_service.py`, `reports/models.py::ReportRun`, cron |
| Report permissions (Admin / HR / Dept Head) | `reports/permissions.py` |
| Redis cache that degrades instead of failing | `config/cache.py::ResilientRedisCache` |
| Recharts, React Query, lazy routes, `can(role, action)` | `frontend/` (recharts 3.9.2 already a dependency) |
| Device health, sync logs, mapping state | `biometric/models.py` |

**Reuse rule for this phase:** an analytics figure is either (a) read straight from a stored
column (`Attendance.status`, `overtime_hours`, `late_minutes`, `comp_off_days`) or (b) computed
by a function in `analytics/metrics/` that is also the single source for the matching export.
Analytics never re-implements a number that already has an owner.

---

## 1. Analytics Architecture

### 1.1 A new Django app, `analytics`

Not an extension of `attendance.workforce`, and not an extension of `reports`. The three have
genuinely different contracts:

| | `attendance.workforce` | `reports` | `analytics` (new) |
|---|---|---|---|
| Question answered | "what is happening right now / who must I action" | "give me a file" | "what is the trend / is this healthy" |
| Freshness | live (WebSocket + 20s poll) | point-in-time snapshot | 5-minute staleness is fine, by design |
| Returns | operational payloads incl. named individuals | binary file | aggregate series, never a person |
| Cacheable | no (queue counts must be exact) | n/a | yes, aggressively |
| Audience | everyone, scoped | Dept Head+ | Dept Head+ |

Mixing them would force the operational dashboards to inherit an analytics cache, or force
analytics to inherit the operational query cost. Keeping them apart is what makes the 250 ms
budget reachable.

### 1.2 Module layout

```
backend/analytics/
  __init__.py
  apps.py
  periods.py            # window + granularity parsing; day/month/quarter/year bucketing
  scope.py              # role → (employee ids, department ids, is_org_wide)
  permissions.py        # CanViewAnalytics / IsOrgAnalytics / analytics_scope(request)
  calendar.py           # expected-working-days engine (see §3.1) — ONE query, pure Python after
  cache.py              # analytics_cached(): key building, TTL policy, version bump
  metrics/
    __init__.py
    attendance.py       # rates, late/absent/half/OT trends, period comparisons
    departments.py      # per-department rollups + compliance ranking
    leave.py            # type usage, consumption trend, balance utilization, forecast
    wfh.py              # trend, approval rate, by-department, monthly
    comp_off.py         # earned / used / pending, department split, historical
    devices.py          # online/offline, sync success, pending punches, mapping progress
    kpis.py             # composition only: executive / hr / management payloads
  serializers.py        # response envelope + chart-contract validation
  exports.py            # 5 export builders, registered into reports.BUILDERS
  views.py              # 9 thin APIViews
  urls.py
  tests/
    conftest.py         # reuses attendance/tests/conftest.py fixtures where possible
    test_metrics.py         # arithmetic correctness, edge cases, empty windows
    test_permissions.py     # role × endpoint matrix, 403s
    test_scoping.py         # department containment; no cross-department leakage
    test_chart_contracts.py # every series is dense, sorted, JSON-safe, no NaN/None holes
    test_exports.py         # 5 builders produce openable files with matching totals
    test_performance.py     # query budgets + wall-clock budgets
```

Every function in `metrics/` is a **pure function of (scope, window) → dict**. No `request`, no
`Response`, no permission logic. That is what makes them reusable by `exports.py` (so a PDF and
the dashboard it was exported from can never disagree) and cheap to unit-test.

### 1.3 Endpoint surface

Two tiers, mirroring how Phase 9.1 already works: one composed payload per *page*, plus
drill-down endpoints per *domain*.

**Composed dashboards** (one request renders the whole page):

| Method | Path | Audience |
|---|---|---|
| GET | `/api/v1/analytics/executive/` | HR, Admin |
| GET | `/api/v1/analytics/hr/` | HR, Admin |
| GET | `/api/v1/analytics/management/` | Dept Head (scoped), HR, Admin |

**Domain endpoints** (drill-down pages and chart refreshes):

| Method | Path | Audience |
|---|---|---|
| GET | `/api/v1/analytics/attendance/` | Dept Head (scoped), HR, Admin |
| GET | `/api/v1/analytics/departments/` | Dept Head (own dept + org average), HR, Admin |
| GET | `/api/v1/analytics/leave/` | Dept Head (scoped), HR, Admin |
| GET | `/api/v1/analytics/wfh/` | Dept Head (scoped), HR, Admin |
| GET | `/api/v1/analytics/comp-off/` | Dept Head (scoped), HR, Admin |
| GET | `/api/v1/analytics/devices/` | HR, Admin only (infrastructure, not attendance) |
| GET | `/api/v1/analytics/meta/` | any analytics-capable role — window presets, department list, KPI definitions |

`/analytics/meta/` exists so the KPI definitions in §3 are served by the API and rendered as
tooltips in the UI. A KPI nobody can define is a KPI nobody trusts.

**Common query parameters** (all optional, all validated in `periods.py`):

```
?from=YYYY-MM-DD&to=YYYY-MM-DD     # explicit window
?period=mtd|qtd|ytd|last_30d|last_6m|last_12m   # preset; default last_12m for trends, mtd for KPI tiles
?granularity=day|month|quarter|year # bucket size; defaults by endpoint
?department=<uuid|code>             # narrows further; never widens (§5)
?compare=previous|year_ago          # adds a comparison series
```

Invalid dates / inverted ranges raise `ValidationError` exactly as `dashboards._window` does today.
Windows are hard-capped at 36 months (`periods.MAX_WINDOW_MONTHS`) — the cap is returned in the
response envelope rather than silently applied.

### 1.4 Response envelope

Every analytics endpoint returns the same outer shape, so the frontend has one loading/error/stale
pattern rather than nine:

```jsonc
{
  "window":      { "from": "2025-08-01", "to": "2026-08-05", "granularity": "month",
                   "period": "last_12m", "truncated": false },
  "scope":       { "level": "organization" | "department", "department": "Engineering" | null,
                   "headcount": 214 },
  "generated_at": "2026-08-05T10:14:03+05:45",
  "cached":       true,
  "stale_after":  "2026-08-05T10:19:03+05:45",
  "data":        { /* endpoint-specific, see §4 */ }
}
```

`generated_at` + `cached` drive an "as of 10:14" line on every dashboard. An executive number with
no timestamp is a number that gets argued about.

### 1.5 Exports

Exports do **not** get a new pipeline. Five new builders are registered into the existing
`reports.workforce_reports.BUILDERS` registry, which gives them the async worker, the signed
download URL, audit logging, retention purge and email scheduling for free:

| Report type | Format | Builder |
|---|---|---|
| `executive_summary` | PDF | `analytics/exports.py::build_executive_summary` |
| `department_analytics` | PDF | `build_department_analytics` |
| `attendance_analytics` | Excel | `build_attendance_analytics` |
| `overtime_analytics` | Excel | `build_overtime_analytics` |
| `workforce_analytics` | CSV | `build_workforce_analytics` |

Each builder calls the *same* `metrics/` function as its dashboard, so a PDF handed to a director
carries the same figures they saw on screen. Formats are pinned per §"EXPORTS" (PDF/PDF/Excel/
Excel/CSV) in `REPORT_FORMATS`; adding a second format later is a one-line change.

### 1.6 Frontend

```
frontend/src/
  services/analyticsService.js        # mirrors workforceService.js conventions
  hooks/useAnalytics.js               # React Query hooks + KEYS, mirrors useWorkforce.js
  pages/analytics/
    Executive.jsx        /analytics/executive
    HRKpi.jsx            /analytics/hr
    Management.jsx       /analytics/management
    AttendanceTrends.jsx /analytics/attendance
    Departments.jsx      /analytics/departments
    LeaveAnalytics.jsx   /analytics/leave
    WfhAnalytics.jsx     /analytics/wfh
    CompOffAnalytics.jsx /analytics/comp-off
    DeviceAnalytics.jsx  /analytics/devices
  components/analytics/
    KpiTile.jsx          # value + delta vs comparison period + definition tooltip
    KpiGrid.jsx
    TrendChart.jsx       # line/area, N series, shared axis + tooltip styling
    ComparisonBarChart.jsx
    DepartmentRankTable.jsx
    DistributionPieChart.jsx
    PeriodPicker.jsx     # presets + custom range + granularity
    ExportMenu.jsx       # posts to /reports/request/, polls status, downloads
    ChartFrame.jsx       # title, "as of", empty state, error state, a11y table fallback
    chartTheme.js        # ONE palette + axis/tooltip/legend config for all charts
```

All nine pages are `lazy()`-loaded exactly as the Phase 9.1 workforce pages are — recharts is
already code-split out of the initial bundle and this phase must not change that.

`chartTheme.js` is deliberate: nine dashboards drawn by different components drift into nine
colour schemes. One palette module, one set of axis/tooltip props, imported everywhere. Status
colours reuse the existing semantics (`present` green / `late` amber / `wfh` violet / `absent` red)
already established in `components/workforce/AttendanceTrendChart.jsx` and `statusMeta.js`.

Every chart renders an accessible `<table>` fallback behind a "View data" toggle. Charts alone are
not accessible, and executives paste tables into decks.

---

## 2. Data Model Impact

### 2.1 Headline: no new tables, no data migration

Phase 10 is read-only over existing models. The complete migration footprint:

| Change | Type | DDL emitted | Reversible |
|---|---|---|---|
| 5 new members on `reports.ReportType` | Python `TextChoices` | `ALTER … choices` only — **no column change**, stays `varchar(40)` | yes, trivially |
| `REPORT_FORMATS` entries for those 5 | Python dict | none | yes |
| Up to 3 covering indexes (§2.3) | `AddIndex` | `CREATE INDEX CONCURRENTLY`-safe | yes (`DROP INDEX`) |

That is the entire schema impact. Precedent: Phase 9 added 8 report types the same way
(`reports/migrations/0002_alter_reportrun_report_type_and_more.py`).

### 2.2 Models read (and only read)

| Model | Fields used | Notes |
|---|---|---|
| `attendance.Attendance` | `employee_id`, `date`, `status`, `working_hours`, `regular_hours`, `overtime_hours`, `late_minutes`, `comp_off_days`, `is_wfh`, `source` | all pre-computed by the Phase 8 policy engine — analytics never recomputes status |
| `attendance.WFHRequest` | `user_id`, `status`, `start_date`, `end_date`, `created_at`, decision timestamps | approval rate, approval time |
| `attendance.workforce.AttendanceCorrectionRequest` | `status`, `created_at`, stage timestamps | correction volume, approval turnaround |
| `leaves.LeaveDayRecord` | `user_id`, `date`, `day_portion`, `leave_type_id`, `status`, `is_holiday`, `is_weekend` | the day-grain leave source; `portion_days` gives half-day weight |
| `leaves.Leave` | `status`, `created_at`, `department_head_action_date`, `hr_action_date` | approval-time KPIs |
| `leaves.EnterpriseLeaveBalance` | `entitled_days`, `carried_forward_days`, `used_days`, `pending_days` | balance utilization |
| `leaves.LeaveType` | `code`, `name`, `display_color` | chart labels + colours come from the model, not hardcoded |
| `leaves.CompensatoryLedger` | `entry_type`, `status`, `days`, `source_date` | earned / used / pending |
| `leaves.Holiday` | `date`, `is_active` | working-day calendar |
| `leaves.Department` | `id`, `name`, `code`, `is_active` | grouping + ranking |
| `users.User` | `id`, `is_active`, `department_ref_id`, `date_joined`, `date_of_joining`, `role` | headcount, eligibility floor, scoping |
| `biometric.BiometricDevice` | `connection_status`, `last_seen_at`, `last_sync_at`, `pending_punches`, `successful_batches`, `failed_batches` | device analytics |
| `biometric.BiometricEmployee` | `user_id`, `is_active`, `device_id` | mapping progress |
| `biometric.DeviceSyncLog` | `status`, `records_*`, `started_at` | sync success rate |

### 2.3 Index plan — measured, not speculative

Existing indexes already cover most of it:

- `Attendance`: `Index(employee, date)` + `date` db_index — covers every window scan. ✔
- `LeaveDayRecord`: `Index(date, status)` + `Index(user, year, month)` — covers window and monthly rollups. ✔
- `CompensatoryLedger`: `Index(user, entry_type, status)` — covers the balance rollup. ✖ for `source_date` window scans.
- `WFHRequest`: `Index(user, start_date, end_date)` + `Index(status)`. ✔

Candidate additions, to be added **only if** `test_performance.py` shows them needed on a seeded
250-employee × 36-month dataset:

1. `CompensatoryLedger(source_date, entry_type)` — the comp-off monthly trend currently filters
   `source_date BETWEEN` with no index on it.
2. `Attendance(date, status)` — org-wide monthly rollups group by date+status without an employee
   filter narrow enough to use the composite index.
3. `DeviceSyncLog(started_at, status)` — sync-success-rate windows.

Each is additive and independently revertible. Migration `analytics/migrations/` is deliberately
empty; index migrations land in the app that owns the model, as Django requires.

### 2.4 What we are explicitly NOT building, and when to revisit

**No materialized summary tables in Phase 10.** `WeeklyLeaveSummary` / `MonthlyLeaveSummary`
already exist and are cron-recomputed, and adding an `AttendanceMonthlySummary` would bring a
backfill migration, a staleness bug class, and a second definition of every number.

Documented escape hatch, with a trigger condition rather than a hunch:

> If `test_performance.py` measures p95 cold-cache latency above 250 ms on the executive endpoint
> at 250 employees × 36 months, add `analytics_attendance_monthly` (grain: employee × year × month;
> columns: present/late/half/wfh/absent day counts, worked/regular/overtime hours, late minutes)
> populated by a `recompute_attendance_summaries` management command on the existing cron, with the
> same `--this-month` incremental pattern `recompute_summaries` already uses. That is Phase 10.2,
> not Phase 10.

---

## 3. KPI Definition Matrix

Every KPI below has an unambiguous numerator, denominator and source. These definitions are served
by `/api/v1/analytics/meta/` and shown as tooltips.

### 3.1 The denominator contract — expected working days

Most attendance KPIs need a denominator, and getting it wrong is the classic way an analytics layer
produces flattering nonsense.

**Definition.** For an employee *e* and window *W*:

```
expected_working_days(e, W) =
    | { d ∈ W :  d.weekday ≠ Saturday
              ∧  d ∉ active_holidays
              ∧  d ≥ eligibility_floor(e)
              ∧  d ≤ today } |
```

`eligibility_floor(e)` is `max(User.date_joined.date(), User.date_of_joining)` — the same rule
`attendance.services.absent_floor` already uses, so analytics and the dashboards agree about when a
person's history starts. Future dates are excluded so a month-to-date figure is not divided by a
month that has not happened.

`organization_expected_days(W) = Σ_e expected_working_days(e, W)`.

**Cost:** one query for holidays, one `values()` query for employee floor dates, then pure Python
with a sorted calendar array + `bisect` — O(N log D), no per-employee query.

> ⚠️ **Known divergence, flagged for your decision.** The Phase 9 `department_attendance` report
> uses `denominator = present + half + absent` *attendance rows*. Because absent days generally have
> no stored row (rows exist only for a real check-in or an HR entry), that denominator is smaller
> than reality and its Attendance % reads high. Phase 10's definition is the correct one. Options:
> **(A)** leave Phase 9 untouched and label the two figures differently in the UI, or
> **(B)** switch `build_department_attendance` onto `analytics.calendar` so there is one definition
> system-wide. **Recommendation: (B)** — one definition, and the report becomes more accurate. It is
> a change to approved Phase 9 code, so it needs your explicit sign-off.

### 3.2 Attendance KPIs

| KPI | Formula | Source | Edge cases |
|---|---|---|---|
| **Headcount** | count of `User(is_active=True)` in scope | `users.User` | excludes deactivated; snapshot at request time, not window end |
| **Present %** | `(present + late + wfh + 0.5×half_day) / expected_working_days` | `Attendance.status` | half-day counts as 0.5 present and 0.5 absent |
| **Late %** | `late_days / attended_days` where `attended_days = present + late + wfh + half_day` | `Attendance.status` | denominator is days actually worked, not expected — being late requires turning up. Late = arrival after the policy boundary (11:45 under the current NIF policy) |
| **Absent %** | `1 − Present% − Leave%` … computed directly as `(expected − attended_weighted − leave_days) / expected` | derived | clamped to `[0, 1]`; a negative value means a data-integrity issue and is surfaced as a warning, not silently zeroed |
| **Half Day %** | `half_day_days / attended_days` | `Attendance.status` | |
| **WFH %** | `wfh_days / attended_days` | `Attendance.status = wfh` | approved-but-not-worked days are NOT counted (Phase 8 rule: approval alone is not attendance) |
| **Leave %** | `approved_leave_day_weight / expected_working_days` | `LeaveDayRecord(status=approved)`, weighted by `portion_days` | excludes records flagged `is_weekend`/`is_holiday` |
| **Overtime Hours** | `Σ Attendance.overtime_hours` | stored column | already policy-threshold-adjusted by Phase 8; payroll-ready |
| **Attendance Compliance %** | `(expected_working_days − unexplained_absences) / expected_working_days` where an unexplained absence is an expected working day with no attendance row **and** no approved leave **and** no approved WFH | derived | this is the *governance* metric: leave is compliant, silence is not. Distinct from Present %, and both are shown |
| **Average Working Hours/Day** | `Σ working_hours / attended_days` | stored column | `working_hours` is the gross span (Phase 8 semantics); `regular_hours` is shown alongside |

### 3.3 Department KPIs

Same formulas as §3.2, grouped by `User.department_ref_id`, with `"Unassigned"` as an explicit
bucket (never dropped silently).

| KPI | Note |
|---|---|
| Department Headcount | active users with that `department_ref` |
| Department Attendance % / Leave % / WFH % / Late % | §3.2 formulas, denominators scoped to that department's employees |
| Department Overtime | `Σ overtime_hours`, plus per-capita `overtime / headcount` — raw totals just rank the biggest team |
| **Department Health Score** | Composite, 0–100, published formula: `0.50 × Attendance Compliance% + 0.20 × (100 − Late%) + 0.20 × (100 − Absent%) + 0.10 × (100 − OvertimeStress%)` where `OvertimeStress% = min(100, overtime_hours_per_capita / 20 × 100)`. Weights live in one constant, `metrics/departments.py::HEALTH_WEIGHTS`, are returned in the API response, and are shown in the UI tooltip. A score with a hidden formula is a political weapon; a score with a published one is a management tool |
| **Compliance Rank** | dense rank over Attendance Compliance % only, descending — per the phase brief, ranking is on compliance and nothing else. Departments with fewer than 3 active employees are listed but marked `rank: null, reason: "small_sample"` so a 1-person department cannot top the table on a single good day |

**Invariant, enforced by test:** no analytics response contains a per-employee row, name, or
employee id. The smallest unit of analysis in Phase 10 is a department.

### 3.4 Leave KPIs

| KPI | Formula | Source |
|---|---|---|
| Most Used Leave Types | `Σ portion_days` grouped by `leave_type`, descending | `LeaveDayRecord(approved)` |
| Leave Consumption Trend | `Σ portion_days` per month | `LeaveDayRecord(approved)` + `TruncMonth` |
| Leave Balance Utilization | `used_days / (entitled_days + carried_forward_days)` per leave type, org and per department | `EnterpriseLeaveBalance` |
| Department Leave Distribution | share of total approved leave days per department | `LeaveDayRecord` joined to `User.department_ref` |
| **Leave Forecast** | next 3 months, per month: `already_approved_future_days + seasonal_baseline`, where `seasonal_baseline = mean(same calendar month, prior 2 years)` when ≥2 years of history exist, else `mean(trailing 6 months)` | `LeaveDayRecord` | 

**Forecast honesty.** This is a naïve seasonal-baseline forecast, not a model. It is labelled
"Projection" in the UI, ships with the method name and the confidence basis
(`method: "seasonal_mean_2y" \| "trailing_6m" \| "insufficient_history"`), and returns
`null` with `insufficient_history` rather than a fabricated number when there is under 6 months of
data. Committing to anything heavier (ARIMA/Prophet) means a new dependency and a model-drift
maintenance burden that this system has no owner for.

### 3.5 WFH KPIs

| KPI | Formula | Source |
|---|---|---|
| WFH Trend | WFH days per month | `Attendance(status=wfh)` |
| WFH Approval Rate | `approved / (approved + rejected)` — cancelled and pending excluded from the denominator | `WFHRequest.status` |
| WFH Usage by Department | WFH days per department, and per-capita | `Attendance` + `department_ref` |
| WFH Monthly Trend | requests raised vs days actually worked from home, per month | `WFHRequest` + `Attendance` |
| WFH Conversion | `worked_from_home_days / approved_day_rows` | already implemented in `aggregates.wfh_summary` — reused, not re-derived |

### 3.6 Comp Off KPIs

| KPI | Formula | Source |
|---|---|---|
| Earned | `Σ days` where `entry_type=earn, status=confirmed` | `CompensatoryLedger` |
| Used | `Σ days` where `entry_type=use` | `CompensatoryLedger` |
| Pending | `Σ days` where `entry_type=earn, status=pending` | `CompensatoryLedger` |
| Available | `earned − used` | derived (matches `aggregates.comp_off_summary` exactly) |
| Department Breakdown | the four above, grouped by department | + `department_ref` |
| Historical Trend | earned (confirmed vs pending) and used, per month | `TruncMonth(source_date)`; reuses `aggregates.comp_off_trend` shape |

### 3.7 Device KPIs (HR/Admin only)

| KPI | Formula | Source |
|---|---|---|
| Devices Online / Offline | count by `connection_status` among `is_active=True` | `BiometricDevice` |
| Sync Success Rate | `success_logs / total_logs` over the window | `DeviceSyncLog.status` |
| Pending Punches | `Σ pending_punches` | `BiometricDevice` |
| Failed Sync Count | `Σ failed_batches` in window, and failed log count | `BiometricDevice`, `DeviceSyncLog` |
| Mapping Progress | `mapped / total` active `BiometricEmployee` rows, org and per device | `BiometricEmployee(user__isnull)` |
| Punch Ingest Health | `records_created / records_received`, `records_invalid`, `records_unmapped` over the window | `DeviceSyncLog` |

### 3.8 HR KPIs

| KPI | Formula | Source |
|---|---|---|
| Attendance Compliance | §3.2, org-wide + per department | derived |
| Correction Requests | raised / approved / rejected in window, plus open backlog by stage | `AttendanceCorrectionRequest` |
| **Approval Times** | median and p90 hours from `created_at` to the terminal decision, per workflow: leave (dept-head stage, HR stage), WFH, corrections (manager stage, HR stage), comp-off confirmation | `Leave.department_head_action_date` / `hr_action_date`, `WFHRequest`, `AttendanceCorrectionRequest`, `CompensatoryLedger` | 
| Pending Requests | current open counts per queue — reuses `aggregates.pending_queue_counts` | existing |
| Unmapped Employees | active `BiometricEmployee` with `user IS NULL`; plus active `User` with no biometric mapping (the reverse gap, which nothing currently reports) | `biometric` + `users` |
| WFH Approval Metrics | approval rate + median approval time | §3.5 |
| Comp Off Metrics | pending confirmations + median days-to-confirm | §3.6 |

**Median, not mean.** The existing `reports/analytics.py::dashboard_analytics` uses a mean over
`updated_at − created_at`, which one three-month-old forgotten request skews beyond usefulness.
Phase 10 reports median + p90, computed in the database with `PERCENTILE_CONT` on PostgreSQL and a
Python fallback on SQLite (dev/CI), behind one helper so the call sites do not branch.

### 3.9 Management KPIs

| KPI | Formula | Source |
|---|---|---|
| Department Trends | Attendance Compliance % per department per month | §3.3 |
| **Workforce Utilization** | `Σ regular_hours / (expected_working_days × standard_day_hours)` where `standard_day_hours` comes from the applied `AttendancePolicy`, not a hardcoded 8 | `Attendance.regular_hours` + policy |
| Attendance Overview | headcount, present %, absent %, leave %, WFH % for the window with a comparison delta | §3.2 |
| Overtime Overview | total OT hours, OT hours per capita, % of employees with any OT, top departments by OT per capita, monthly trend | `Attendance.overtime_hours` |
| **Workforce Capacity** | `available_capacity_days = expected_working_days − approved_leave_days − approved_future_leave_days`, per department, for the next 30 days | `calendar` + `LeaveDayRecord` |

---

## 4. Dashboard Design

Shared furniture on every page: `PeriodPicker` (presets + custom range + granularity),
`ExportMenu`, an "as of {generated_at}" line, and per-tile definition tooltips.

### 4.1 `/analytics/executive` — Executive Dashboard (HR, Admin)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Executive analytics            [MTD ▾] [Custom…]        as of 10:14  [⤓]  │
├────────────────────────────────────────────────────────────────────────────┤
│ KPI GRID (4 × 3)                                                           │
│  Headcount 214    Present 91.2% ▲1.4   Late 4.1% ▼0.6   Absent 3.2% ▼0.3   │
│  WFH 6.8% ▲2.1    Leave 5.4% ▬        Overtime 312h ▲44  Compliance 96.8%  │
│  Comp earned 41d  Comp used 27d       Comp pending 9d    Avg hrs/day 7.9   │
├─────────────────────────────────────┬──────────────────────────────────────┤
│ Monthly workforce trends            │ Department health                    │
│ (stacked AreaChart, 12 months:      │ (horizontal BarChart, health score,  │
│  present / late / wfh / absent /    │  colour-banded ≥90 / 75–89 / <75)    │
│  leave — % of expected days)        │                                      │
├─────────────────────────────────────┴──────────────────────────────────────┤
│ Attendance compliance by department (ranked table: rank, dept, headcount,  │
│ compliance %, Δ vs previous period, sparkline)                             │
└────────────────────────────────────────────────────────────────────────────┘
```

Charts: stacked `AreaChart` (trends), horizontal `BarChart` (health), sparkline `LineChart` in
table rows. Every KPI tile shows a delta against the comparison period and links to its drill-down.

### 4.2 `/analytics/attendance` — Attendance Trends (Dept Head scoped, HR, Admin)

- KPI row: attendance rate, late %, absent %, half-day %, overtime hours.
- **Attendance rate trend** — `LineChart`, granularity-driven (day / month / quarter / year), with
  an optional `compare=year_ago` dashed series.
- **Exception trends** — `LineChart`, 3 series: late, absent, half-day.
- **Overtime trend** — `AreaChart`, hours per bucket with a per-capita secondary axis.
- **Department comparison** — grouped `BarChart`, attendance % by department for the window.
- **Period comparison** — a `ComparisonBarChart` tab strip: Monthly (last 12 months) / Quarterly
  (last 8 quarters) / Yearly (last 3 years), each with the same 5 measures.

### 4.3 `/analytics/departments` — Department Analytics

- Ranked table (compliance only), one row per department: headcount, attendance %, leave %, WFH %,
  late %, overtime (total + per capita), health score, rank.
- `BarChart`: attendance % by department, org average as a `ReferenceLine`.
- `LineChart`: multi-department compliance trend (up to 8 series; beyond that, top 8 by headcount
  plus an "Others" aggregate — and the truncation is stated in the chart footer, not hidden).
- For a Dept Head: their own department's full row and trend, plus the org average line and their
  percentile — no other department's name or figures (§5).

### 4.4 `/analytics/leave` — Leave Analytics

- `PieChart`: most-used leave types (colours from `LeaveType.display_color`).
- `AreaChart`: leave consumption trend, 12 months.
- `BarChart`: balance utilization % per leave type, with a 100% `ReferenceLine`.
- `BarChart`: department leave distribution.
- `LineChart`: consumption history + a dashed 3-month **projection** segment, clearly labelled with
  its method and a "Projection" legend entry.

### 4.5 `/analytics/wfh` — WFH Analytics

- KPI row: WFH %, approval rate, conversion rate, median approval time.
- `AreaChart`: WFH days per month.
- `BarChart`: WFH days by department (total + per capita toggle).
- `LineChart`: requests raised vs days actually worked from home — the gap is the story.

### 4.6 `/analytics/comp-off` — Comp Off Analytics

- KPI row: earned, used, pending, available.
- `BarChart` (stacked): earned / used / pending by department.
- `LineChart`: historical earned (confirmed vs pending) and used, per month.
- Liability callout: available days × headcount context, since unused comp days are a real accrual.

### 4.7 `/analytics/devices` — Device Analytics (HR, Admin)

- Status tiles: online, offline, pending punches, failed syncs, mapping progress %.
- `LineChart`: sync success rate over time.
- `BarChart`: pending punches per device.
- Progress bars: mapping completion per device.
- Table: per-device last seen / last sync / drift / failed batches.

### 4.8 `/analytics/hr` — HR KPI Dashboard (HR, Admin)

- KPI grid: attendance compliance, correction volume, open queues by stage, unmapped employees
  (both directions), WFH approval rate, comp-off pending.
- `BarChart`: median approval time by workflow, with a p90 whisker and an SLA `ReferenceLine`.
- `LineChart`: correction requests raised vs resolved per month — divergence means a growing backlog.
- Queue-age breakdown: `<1d / 1–3d / 3–7d / >7d` per queue.

### 4.9 `/analytics/management` — Management KPI Dashboard (Dept Head scoped, HR, Admin)

- KPI grid: workforce utilization, attendance overview, overtime overview.
- `LineChart`: department compliance trends.
- `AreaChart`: workforce capacity, next 30 days (available vs on-leave).
- `BarChart`: overtime per capita by department.

### 4.10 Navigation

A new "Analytics" sidebar group in `LeaveSidebar.jsx`, visible only when
`can(role, 'analyticsView')`. Executive / HR items are further gated on `analyticsOrg`. The
existing `/admin/analytics` (Phase 8 leave analytics, Admin-only) stays exactly where it is and is
not touched — it answers a different question and rewiring it is out of scope.

---

## 5. Permission Matrix

### 5.1 Role mapping — one decision needed

The system has four roles: `maker` (Employee), `checker` (Department Head), `approver` (HR),
`admin` (Admin). There is **no** Executive/Director role. The brief's audience list
(Management, HR Leadership, Executive, Directors) has no distinct representation in the data model.

> ⚠️ **Decision required.** Recommendation: map Executive → `approver` + `admin`, i.e. the same gate
> as the existing `workforceHR` capability. If NIF needs a genuine director audience that is *not*
> HR, the clean options are (A) a new `User.Roles` member, which touches the workflow engine and is
> a much larger change than Phase 10 should carry, or (B) an additive
> `User.employee_type` based gate reusing the existing `DEPARTMENT_HEAD`/`SYSTEM_ADMIN` values.
> **Recommendation: proceed with `approver`+`admin` now**, and treat a distinct executive role as a
> separate, explicitly-scoped change.

### 5.2 Endpoint × role

| Endpoint | Employee (`maker`) | Dept Head (`checker`) | HR (`approver`) | Admin (`admin`) |
|---|---|---|---|---|
| `/analytics/executive/` | 403 | 403 | ✔ org | ✔ org |
| `/analytics/hr/` | 403 | 403 | ✔ org | ✔ org |
| `/analytics/management/` | 403 | ✔ own department | ✔ org | ✔ org |
| `/analytics/attendance/` | 403 | ✔ own department | ✔ org | ✔ org |
| `/analytics/departments/` | 403 | ✔ own dept + org avg + percentile | ✔ org, all named | ✔ org, all named |
| `/analytics/leave/` | 403 | ✔ own department | ✔ org | ✔ org |
| `/analytics/wfh/` | 403 | ✔ own department | ✔ org | ✔ org |
| `/analytics/comp-off/` | 403 | ✔ own department | ✔ org | ✔ org |
| `/analytics/devices/` | 403 | 403 | ✔ | ✔ |
| `/analytics/meta/` | 403 | ✔ | ✔ | ✔ |
| Export: `executive_summary` | 403 | 403 | ✔ | ✔ |
| Export: `department_analytics` | 403 | ✔ own dept only | ✔ | ✔ |
| Export: `attendance_analytics` | 403 | ✔ own dept only | ✔ | ✔ |
| Export: `overtime_analytics` | 403 | ✔ own dept only | ✔ | ✔ |
| Export: `workforce_analytics` | 403 | ✔ own dept only | ✔ | ✔ |

### 5.3 How scoping is enforced

1. `analytics/scope.py::analytics_scope(user)` returns `(employee_ids, department_ids, level)` by
   delegating to the existing `attendance.workforce.aggregates.scoped_employees` — the same helper
   the Phase 9 dashboards and the report scoping use. One scoping rule, four consumers.
2. The `?department=` parameter can only **narrow**. A Dept Head passing another department's id
   gets their own scope back, not a 403 and not the other department — silently narrowing is the
   behaviour that cannot leak.
3. Export scope is injected server-side by `ReportsViewSet._scoped_params`, which already does
   exactly this for Phase 9 workforce reports. New analytics report types are added to
   `WORKFORCE_REPORT_TYPES` (or a parallel `ANALYTICS_REPORT_TYPES` list) so `allowed_report_types`
   admits Dept Heads with the same narrowing.
4. **Small-department suppression.** Even within their own scope, a Dept Head's department-level
   figures are suppressed (`null` + `reason: "small_sample"`) when the department has fewer than 3
   active employees, because a 2-person department's "attendance %" is one person's attendance
   record. This is the closest Phase 10 comes to individual scoring, and it is closed off.
5. **No individuals, ever.** A test asserts that no analytics response body contains a `User` id,
   `employee_id`, or name field. Individual detail remains in Phase 9's workforce endpoints, which
   have their own scoping.

### 5.4 Frontend gates

Added to `PERMISSIONS` in `frontend/src/services/roles.js`:

```js
analyticsView:  ['checker', 'approver', 'admin'],  // any analytics page
analyticsOrg:   ['approver', 'admin'],             // executive + HR + devices
analyticsExport:['checker', 'approver', 'admin'],  // scoped server-side
```

These hide pages that would only 403. The API remains the security boundary, exactly as the Phase
9.1 comment in `App.jsx` states.

---

## 6. Performance Strategy

Target: **< 250 ms** dashboard responses.

### 6.1 Query budgets (enforced by `django_assert_max_num_queries`, as Phase 9 does)

| Endpoint | Budget (cold) | Budget (warm) |
|---|---|---|
| `/analytics/executive/` | 18 | 1 (auth only) |
| `/analytics/hr/` | 20 | 1 |
| `/analytics/management/` | 14 | 1 |
| `/analytics/attendance/` | 10 | 1 |
| `/analytics/departments/` | 10 | 1 |
| `/analytics/leave/` | 10 | 1 |
| `/analytics/wfh/` | 7 | 1 |
| `/analytics/comp-off/` | 6 | 1 |
| `/analytics/devices/` | 7 | 1 |
| `/analytics/meta/` | 2 | 1 |

Budgets are constants at the top of `test_performance.py`, exactly like `BUDGET_ME = 12` in
`test_workforce_dashboards.py`. Lowering one is a win; raising one requires a comment saying why.

### 6.2 No-N+1 rules (each has a test)

1. Every metric is a single grouped `.values(...).annotate(...)` or `.aggregate(...)`. No query
   inside a loop, ever.
2. Bucketing uses `TruncMonth` / `TruncQuarter` / `TruncYear` in SQL, never Python iteration over
   dates issuing per-bucket queries.
3. Sparse buckets are densified in Python from a single result set (the pattern
   `aggregates.attendance_trend` already uses) — a month with no data is a zero row, not a missing
   one, so charts have no holes.
4. Department grouping is `values("employee__department_ref_id")`, not a loop over departments.
5. The working-day calendar is built once per request and passed down; `calendar.py` takes and
   returns plain data, never a queryset.
6. No `resolve_day_status` in analytics. It is a per-employee-per-day Python call and is the single
   biggest way this budget could be blown. Analytics reads stored `Attendance.status`.

### 6.3 Caching

Backend: the existing `config.cache.ResilientRedisCache` (Redis db 1, `KEY_PREFIX='nifn'`). With
`REDIS_URL` unset — dev, CI, pytest — Django falls back to LocMemCache and everything still works,
just per-process. Cache unavailability degrades to a slower correct response, never an error.

**Key:** `analytics:v{CODE_VERSION}:{gen}:{endpoint}:{scope_digest}:{param_digest}`

- `CODE_VERSION` — a constant bumped whenever a formula changes, so a deploy cannot serve
  yesterday's definition of a KPI.
- `gen` — a global generation counter read from `analytics:gen`. Bumped by signals on
  **history-altering** events only: correction applied/reverted, backdated attendance edit
  (`Attendance.date < today`), leave approval/cancellation, WFH approval affecting a past date,
  comp-ledger confirmation. Ordinary same-day punches do **not** bump it — they are covered by TTL.
- `scope_digest` — hash of `(level, sorted department_ids)`, **not** the user id, so every manager
  in a department shares one cache entry and HR/Admin share the org entry.
- `param_digest` — hash of the normalized window/granularity/compare parameters.

**TTL policy:**

| Window | TTL | Why |
|---|---|---|
| ends today (MTD, QTD, YTD, last_30d) | 300 s | executives do not need sub-5-minute figures; this is explicitly not transaction processing |
| ends in the past (closed month/quarter/year) | 86 400 s | historical data changes only via corrections, which bump `gen` |
| `/analytics/devices/` | 60 s | infrastructure health decays fast and HR acts on it |
| `/analytics/meta/` | 3 600 s | definitions and department lists barely move |

**Frontend:** React Query with `staleTime` matched to the backend TTL (5 min / 1 min for devices)
and `placeholderData: (prev) => prev` so changing the period does not flash an empty dashboard.
No WebSocket subscription on analytics pages — the live stream exists for operational dashboards
and hooking it here would defeat the caching entirely.

### 6.4 Payload discipline

- Series are capped: 36 monthly points, 90 daily points, 8 quarterly, 5 yearly. A request for daily
  granularity over 3 years is downgraded to monthly and `window.truncated: true` is set — visible,
  not silent.
- Multi-series department charts cap at 8 series + "Others", with the truncation stated in the
  response.
- Decimals are serialized as numbers rounded to 2 dp (or 1 dp for percentages) — not `Decimal`
  strings — because every value here goes straight into a chart axis.
- Target uncompressed payload: < 100 KB per dashboard.

### 6.5 Measurement

`analytics/tests/test_performance.py`:

1. A seeded dataset fixture: 250 employees, 12 departments, 36 months of attendance, leave, WFH and
   comp-off rows (built once per session with `bulk_create`).
2. Query-budget assertions per §6.1.
3. Wall-clock assertions: cold-cache p95 under 250 ms for every endpoint, measured over 20
   iterations, with a generous CI multiplier and the raw timing printed so a regression is visible
   in the log even when the assertion passes.
4. A cache-hit test proving a warm request issues exactly one query (the auth lookup).
5. An `EXPLAIN` smoke test on PostgreSQL asserting no sequential scan on `attendance_attendance`
   for a windowed department query — this is what actually decides whether §2.3's candidate indexes
   are needed.

If a wall-clock budget fails at seed scale, the §2.4 summary-table escape hatch is triggered —
before shipping, not after.

---

## 7. Rollback Plan

Phase 10 is designed so that rollback is a deploy, not a recovery.

### 7.1 Why rollback is cheap

- **No writes.** Analytics endpoints issue `SELECT` only. The sole rows Phase 10 creates are
  `ReportRun` records for exports, which the existing retention job already purges.
- **No data migration.** Nothing is backfilled, nothing is transformed. §2.1 is the whole footprint.
- **Purely additive.** No existing endpoint, model, serializer, permission class or page is
  modified — with one flagged exception (§3.1 option B, which requires separate sign-off and can be
  omitted without affecting anything else in Phase 10).

### 7.2 Feature flag

`ANALYTICS_ENABLED` (env var, default `True` in dev, gated per environment in prod):

- Backend: when false, `analytics/urls.py` registers nothing, so every analytics path 404s and the
  code is inert.
- Frontend: when `VITE_ANALYTICS_ENABLED` is false, the routes are not registered and the sidebar
  group is hidden.

This gives a **sub-minute kill switch** that needs no code deploy — the correct first response to a
production problem.

### 7.3 Rollback ladder

| Level | Action | Time | Impact |
|---|---|---|---|
| 1 | Set `ANALYTICS_ENABLED=false`, restart backend | < 1 min | analytics gone; everything else untouched |
| 2 | Purge the analytics cache namespace (`analytics:*` keys) | < 1 min | fixes a stale/bad-cache problem without disabling the feature |
| 3 | Bump `CODE_VERSION` | next deploy | invalidates every cached figure after a formula fix |
| 4 | Revert the frontend build to the previous image | ~5 min | UI gone, API still available |
| 5 | Revert the backend to the previous image | ~5 min | app package unused; report types remain in the DB as inert strings |
| 6 | Reverse the index migrations (`migrate <app> <previous>`) | ~1 min | only if an index caused a write regression |
| 7 | Reverse the `ReportType` choices migration | ~1 min | last resort; any `ReportRun` rows with a Phase 10 type become unlabelled but harmless (`varchar`, no FK, no constraint) |

Levels 1–3 handle every realistic failure. Levels 4–7 exist because a rollback plan that stops at
"turn it off" is not a plan.

### 7.4 Forward-fix conditions

Rollback is **not** the right response to: a wrong KPI formula (fix the formula, bump
`CODE_VERSION`), a slow endpoint (raise the TTL, then optimise), or a chart rendering bug (frontend
patch). Reserve the ladder for a permission leak, a query that degrades the database for other
users, or a correctness failure that would mislead a decision.

### 7.5 Data-safety statement

There is no scenario in which rolling back Phase 10 loses operational data, because Phase 10 never
creates any. The worst case is that generated export files (already covered by
`purge_expired_reports`) are orphaned.

---

## 8. Test Plan (per the brief's TESTING section)

| Area | File | What is asserted |
|---|---|---|
| Analytics APIs | `test_metrics.py`, `test_api.py` | every endpoint 200s, envelope shape, correct arithmetic against hand-computed fixtures, empty-window and zero-headcount behaviour, division-by-zero returns `null` not `0` |
| Permission rules | `test_permissions.py` | full §5.2 matrix, all four roles × all endpoints × all export types |
| Department scoping | `test_scoping.py` | a Dept Head's totals never include another department; `?department=` cannot widen; other departments are unnamed in their ranking payload |
| Chart data integrity | `test_chart_contracts.py` | series are dense (no missing buckets), sorted ascending by period, JSON-serializable, no `NaN`/`Infinity`/`Decimal`, percentages within `[0, 100]`, stacked series sum to the stated total |
| Exports | `test_exports.py` | each of the 5 builders returns `(bytes, filename, content_type)`, files open (openpyxl / PDF magic bytes / CSV parse), and totals match the dashboard endpoint for the same window |
| Performance budgets | `test_performance.py` | §6.5 |
| No individual scoring | `test_no_individual_data.py` | no response body contains a user id, employee id, or person name — the phase's hardest constraint, enforced mechanically |

Frontend: Vitest tests mirroring the Phase 9.1 pattern (`Dashboards.test.jsx`, `routes.test.jsx`) —
role-gated route rendering, loading/error/empty states, KPI tile formatting, and chart data
reshaping (the memoised transforms, not recharts' internals).

---

## 9. Decisions needed before coding

1. **Executive audience mapping** (§5.1) — proceed with `approver` + `admin` as the executive gate?
   *Recommended: yes.*
2. **Attendance % denominator** (§3.1) — adopt the expected-working-days definition for Phase 10
   and also retrofit the Phase 9 `department_attendance` report onto it (option B), or keep the two
   definitions separate and labelled (option A)? *Recommended: B.*
3. **Department Health Score weights** (§3.3) — accept the proposed 50/20/20/10 split, or specify
   your own? The weights are one constant and are published in the API either way.
4. **Manager visibility of other departments** (§5.2) — own department + org average + percentile,
   with other departments unnamed. *Recommended as specified;* the alternative is full named
   visibility for Dept Heads, which is a policy call, not a technical one.
5. **Leave forecast method** (§3.4) — naïve seasonal baseline, labelled as a projection.
   *Recommended: yes;* anything heavier needs a model owner.

---

## 10. Build sequence (after approval)

1. `analytics` app skeleton, `periods.py`, `scope.py`, `permissions.py`, `calendar.py` + their tests.
2. `metrics/attendance.py` + `metrics/departments.py` + tests (the two everything else leans on).
3. `metrics/leave.py`, `wfh.py`, `comp_off.py`, `devices.py` + tests.
4. `metrics/kpis.py` (executive / hr / management composition), `views.py`, `urls.py`, `serializers.py`.
5. `cache.py` + cache tests + `test_performance.py`.
6. `exports.py` + registration into `reports` + export tests.
7. Frontend: `analyticsService.js`, `useAnalytics.js`, `chartTheme.js`, shared chart components.
8. Frontend: the nine pages, routes, sidebar, `roles.js` capabilities + Vitest coverage.
9. Full-suite run, performance measurement, budget lock-in.

Phase 11 is out of scope and will not be started.

---

## 11. As built — deviations from this design

All five decisions in §9 were approved as recommended, including option **B** on
the denominator. Everything else below is a change made during implementation
because the design turned out to be wrong or imprecise. Each is a deliberate
choice, not a shortcut.

### 11.1 Approval percentiles are computed in Python, not `PERCENTILE_CONT`

§3.8 promised a database percentile with a Python fallback. The built version
computes both stages in Python for every backend, from two timestamp columns
pulled with `values_list`, capped at `APPROVAL_SAMPLE_CAP = 5000` rows.

Reason: one code path that behaves identically on PostgreSQL and on the SQLite
used by dev and CI, with no custom `Aggregate` subclass to maintain. The row
count is genuinely bounded — these are decisions taken inside the window by an
internal HR team, which is hundreds of rows — and the cap makes that bound
explicit rather than assumed. The interpolation matches `PERCENTILE_CONT`, so
moving it into SQL later changes nothing about the numbers.

### 11.2 Comp-off turnaround is reported as unmeasurable

`CompensatoryLedger` records no confirmation timestamp, so the time from earning
a comp day to HR confirming it cannot be derived from the data. Rather than
substituting a proxy that looks like a measurement, the payload returns
`median_hours: null` with `unmeasurable_reason: "no_confirmation_timestamp"`,
the pending count, and the age of the oldest pending entry. The HR dashboard
prints that explanation.

Adding the timestamp is a one-column migration if the figure is wanted — but
that is a change to Phase 8's ledger and was not in this phase's scope.

WFH approval time has the same shortcoming and is handled the same way: it reads
`updated_at`, is therefore an upper bound, and is flagged `approximate: true`
everywhere it appears.

### 11.3 The leave forecast takes the maximum, not the sum

§3.4 wrote `approved_future_days + seasonal_baseline`. That double-counts: the
historical baseline already includes the days that were eventually booked, so
adding the two roughly doubles the nearest month. The built version projects
`max(approved, baseline)` — approved leave is a floor because those days are
committed, and the baseline is the expectation for the month as a whole.

### 11.4 Query budget for `/analytics/hr` is 25, not 20

The §6.1 numbers were estimates written before the code existed. Eight of the
ten came in at or under budget; the HR dashboard measured 24. It composes six
domains in one payload and each is already a fixed grouped query set.

The count is **constant** — a test asserts it does not move when the
organisation grows — which is the property the budget exists to protect.
Splitting the page into six requests to make one number smaller would be worse
for the user and no cheaper for the database. The remaining budgets are as
designed or lower.

Measured cold-cache latency on the seeded fixture (SQLite, single process — not
the production Postgres, so treat these as a floor rather than a forecast):

| Endpoint | Cold | | Endpoint | Cold |
|---|---|---|---|---|
| `/analytics/attendance/` | 48.1 ms | | `/analytics/management/` | 41.5 ms |
| `/analytics/hr/` | 43.1 ms | | `/analytics/departments/` | 38.2 ms |
| `/analytics/executive/` | 37.4 ms | | `/analytics/devices/` | 36.6 ms |
| `/analytics/comp-off/` | 33.9 ms | | `/analytics/wfh/` | 32.3 ms |
| `/analytics/leave/` | 32.2 ms | | `/analytics/meta/` | 26.8 ms |

Every endpoint is inside the 250 ms target with room to spare, and a warm cache
serves each in a single auth query. The §2.4 summary-table escape hatch was
therefore **not** triggered and remains available if production scale ever
breaches the budget.

### 11.5 Department heads cannot export the executive summary

Not in the original matrix. An "Executive Summary" narrowed to one department
would carry a title its contents do not support, so that one report type is
HR/Admin only. The other four analytics exports are available to department
heads, scope-injected server-side exactly as the Phase 9 workforce reports are.

### 11.6 The Phase 9 department report gained two columns

Decision B changed `build_department_attendance` to the shared denominator. Its
"Absent" column now means *expected working days covered by neither attendance
nor approved leave*, which is what readers always assumed it meant, and an
explicit **On Leave** and **Compliance %** column were added so the two senses
are visible side by side rather than conflated. The subtitle states the
denominator on every generated copy.

### 11.7 Chart palettes were validated, not chosen

The design said "one palette module". The built `chartTheme.js` carries two —
reserved status colours (matching the Phase 9 workforce dashboards, so green
never means "series 3") and a fixed-order categorical set for department
identity — and both were run through a colour validator for lightness band,
chroma floor, protan/deutan separation, normal-vision separation and surface
contrast in light and dark. Worst adjacent CVD ΔE: 8.9 status, 9.1 categorical
light, 8.4 categorical dark, all above the ≥8 target.

Two light-mode hues fall below 3:1 contrast on a white card. The remedy is
relief, not a colour change: `ChartFrame` gives every chart a legend and a
"View data" table, and no chart in the app is rendered outside it.

### 11.8 Test counts as built

| Suite | Tests |
|---|---|
| `analytics/tests/test_metrics.py` | 37 |
| `analytics/tests/test_permissions.py` + `test_scoping.py` | 68 |
| `analytics/tests/test_chart_contracts.py` | 59 |
| `analytics/tests/test_no_individual_data.py` + `test_exports.py` | included above |
| `analytics/tests/test_performance.py` | 36 |
| Frontend (`vitest`, whole suite) | 170 |

Combined backend run (`reports/ attendance/tests/ analytics/`): **593 passed, 1
failed, 2 skipped**. The single failure is pre-existing and unrelated to this
phase — see §11.9.

The frontend build and test suite require Node ≥ 20.19 (already documented in
`frontend/package.json`); on an older Node, `vite build` and `vitest` both fail
before reaching any application code.

### 11.9 One pre-existing failure, left alone

`reports/test_workforce_reports.py::test_overtime_summary_reports_real_overtime`
fails under SQLite. It asserts the CSV contains `2.50`, and the file contains
`2.5`.

The cause is backend coercion, not Phase 10: `Sum()` over a `DecimalField`
returns a `Decimal` with the field's scale on PostgreSQL but a bare `float` on
SQLite, so `str(...)` drops the trailing zero. `build_overtime_summary` is Phase
9 code and was not touched by this phase — the only function edited in that
module is `build_department_attendance`, under approved decision B.

Left as-is deliberately: it is approved Phase 9 code, the production database is
PostgreSQL where the test passes, and quietly editing a neighbouring phase's
report to make a local run go green would hide a real portability difference.
The fix, if wanted, is one line — quantise the sum before formatting — and
belongs in a Phase 9 patch, not here.
