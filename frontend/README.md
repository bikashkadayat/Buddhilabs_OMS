# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.

---

## Toolchain requirement

Vite 8 (rolldown bundler) requires **Node.js >= 20.19 or >= 22.12**. On older Node
(e.g. 20.12) `npm run build` and `npm test` fail with a `util.styleText` error.
Use `nvm install 22 && nvm use 22`.

## Phase 6 - Enterprise Leave Records (frontend)

Employee-facing views over the Phase 4 leave-records API. Data fetching is
standardized on **@tanstack/react-query**; charts use **recharts**; tests use
**Vitest + React Testing Library**.

### Pages (routes)

| Route | Page | Notes |
|-------|------|-------|
| `/leaves/my-history` | `MyLeaveHistory` | Year selector, balance cards, clickable monthly breakdown, recent applications, quick-stats sidebar |
| `/leaves/my-calendar` | `MyLeaveCalendar` | Month view, colour-coded by leave type, weekends greyed, holiday badges, day popover, manager "Show team" toggle |
| `/leaves/weekly-report` | `WeeklyReport` | Table + attendance line chart |
| `/leaves/monthly-report` | `MonthlyReport` | Table + stacked bar chart (leave days by type) |
| `/leaves/team-attendance` | `TeamAttendance` | Managers/HR (admin/approver): monthly attendance table by department |

### Reusable components (`src/components/leave-records/`)

`BalanceCard`, `LeaveTypeChip`, `AttendanceIndicator`, `CalendarDay`,
`YearSelector`, plus `States` (Skeleton / EmptyState / ErrorState) and
`DashboardRecordsSummary`. Each is documented with in-file JSDoc (props +
response shapes).

### Backend endpoint mapping (reconciled)

The Phase 6 brief assumed some `/leaves/*` paths that differ from the Phase 4
backend. `leaveRecordService.js` targets the **real** routes:

| Purpose | Real endpoint |
|---------|---------------|
| My history | `GET /api/v1/leaves/my-history/?year=` |
| Balances | `GET /api/v1/leave-balances/?year=` |
| Calendar | `GET /api/v1/leaves/calendar/?start=&end=&user_id=` |
| Weekly / Monthly | `GET /api/v1/weekly-summaries/` · `GET /api/v1/monthly-summaries/` |
| Leave types / Holidays | `GET /api/v1/leave-types/` · `GET /api/v1/holidays/?year=` |
| Team attendance | `GET /api/v1/leaves/team-attendance/?department=&month=YYYY-MM` |

### Accessibility

Colour-coded elements always carry text labels (colour-blind support); ARIA
labels on interactive controls; keyboard-navigable calendar/table rows; visible
focus rings; horizontally-scrollable tables on mobile (single-column layout).

### Run / verify

```bash
nvm use 22
npm run dev      # dev server (proxies /api/v1 to the backend)
npm test         # Vitest + RTL  (12 tests)
npm run lint     # ESLint
npm run build    # production build
```

### Screenshots & Lighthouse

Capture screenshots of each page against a running stack (`docker compose up`
then log in) and place them under `docs/screenshots/`. Run Lighthouse with
`npx lighthouse http://localhost:5173/leaves/my-history --view` (targets:
performance > 90, accessibility > 95). These require a running browser + backend
and were not executed in the build sandbox.

## Phase 7 - HR/Admin (frontend)

Admin-only pages (route base `/admin/leaves/*`, sidebar "Administration"):

| Route | Page |
|-------|------|
| `/admin/leaves/employees` | `EmployeeList` — searchable/filterable table with usage + attendance |
| `/admin/leaves/employees/:id` | `EmployeeDetail` — tabs: Overview / Balances (adjust) / Applications / Adjustments / Audit |
| `/admin/leaves/policies` | `PolicyManagement` — grouped by department, overlap warning, deprecate (never delete) |
| `/admin/leaves/holidays` | `HolidayManagement` — CRUD + CSV bulk import (`public/templates/holidays_template.csv`) |
| `/admin/leaves/departments` | `DepartmentManagement` — parent/child tree, head selection |
| `/admin/leaves/leave-types` | `LeaveTypeManagement` — CMS leave-type rules |
| `/admin/leaves/bulk-actions` | `BulkActions` — multi-select approve/reject with impact summary |

Guards: every destructive action goes through `ConfirmModal` (typed
confirmation and/or mandatory reason); results surface via `Toast`. Service:
`adminLeaveService.js`. Backend endpoints live under `/api/v1/leaves/admin/*`.

## Phase 8 - Reports & Analytics (frontend)

| Route | Page |
|-------|------|
| `/reports` | `ReportsHub` — cards per report type + recent generations |
| `/reports/build/:type` | `ReportBuilder` — dynamic form, async generate + poll + download |
| `/reports/history` | `ReportHistory` — all your reports (re-download for 30 days) |
| `/admin/analytics` | `Analytics` — KPI row + recharts (trend line, type donut, dept/top-10 bars, heatmap) |

Service `reportService.js` (blob download via `saveBlob`). Backend under
`/api/v1/reports/*` (Excel via openpyxl, PDF via weasyprint). Async flow:
`POST /reports/request/` → poll `/reports/{id}/status/` → `GET /reports/{id}/download/`.
Print-optimized `@media print` rules let employees print history directly.
Sample outputs of every report live in `docs/sample-reports/`.

## Phase 9 - Notifications (frontend)

- `NotificationBell` in the header: unread badge, 30s polling, dropdown of the
  last 5, click marks read + navigates to `action_url`.
- `/notifications` (`NotificationsPage`): full list with category filter,
  mark-all-read, delete, and a **Preferences** tab (per-category in-app/email
  toggles).

Service `notificationService.js`; backend under `/api/v1/notifications/*`.
Emails (HTML + plain-text, branded, with unsubscribe → preferences) are sent by
the backend dispatcher respecting each user's `NotificationPreference`. Configure
SMTP via `EMAIL_*` env vars (console backend by default).

## Phase 10 - PDF documents (frontend)

`LeaveDocActions` renders **Download PDF**, **Download Certificate** and
**Preview** (iframe modal) for approved leaves in *My Applications* — hidden
until the leave is approved, with a per-action progress spinner. Service
`documentService.js`. Backend endpoints: `GET /api/v1/memos/{id}/pdf/`,
`GET /api/v1/leaves/{id}/pdf/`, `GET /api/v1/leaves/{id}/certificate/`
(approved-only), plus the public `GET /api/v1/verify/{document_number}/`.

Every PDF carries a QR code linking to the public verification page. Sample
memo/leave/certificate PDFs are in `docs/samples/`.

## Phase 2.5 - Unified login & User Management (frontend)

- **Single login** (`/login`, kept & enhanced) for every role. On success the
  backend returns `user.role` + `must_change_password`; the app forces a
  password change first, then redirects by role. Public self-registration was
  removed ("Contact your administrator").
- **First-login password change** (`/auth/first-login-change-password`) —
  protected, standalone, cannot be dismissed, with a strength meter.
- **`RequireAuth`** guards on auth, first-login change, and (optionally) role
  (`allowedRoles`), redirecting to `/unauthorized` otherwise.
- **User Management** (`/admin/users`, admin-only): create employees (with a
  one-time generated-credentials modal), search/filter, change role, reset
  password, activate/deactivate. Service `userMgmtService.js`; backend under
  `/api/v1/users/admin/users/`. Bootstrap the first admin with
  `python manage.py seed_admin`.

## Memos - 3-level workflow (all roles can create)

Sidebar **Memos** section (between Leave Management and My Records): Create Memo
(everyone), My Memos, All Memos, plus Pending Reviews (checkers) and Pending
Approvals (approvers).

| Route | Page |
|-------|------|
| `/memos` | `MemoList` - filterable table |
| `/memos/create` | `CreateMemo` - template loader, TipTap editor, checker picker, live preview |
| `/memos/my` | `MyMemos` - your memos (status tabs) |
| `/memos/:id` | `MemoDetail` - content, metadata, approval timeline, role-aware actions, Download PDF (approved) |
| `/memos/pending-reviews` | `PendingMemoReviews` - checker queue |
| `/memos/pending-approvals` | `PendingMemoApprovals` - approver queue |

Workflow: **author (any role) -> checker -> approver**. The maker optionally picks a
checker (else auto by department); the checker picks the approver during review.
Service `memoService.js`; rich text via **TipTap** (HTML output). Every transition
notifies the relevant user (bell + email). Full per-role guide:
[`docs/user-manual/memo-guide.md`](../docs/user-manual/memo-guide.md).
