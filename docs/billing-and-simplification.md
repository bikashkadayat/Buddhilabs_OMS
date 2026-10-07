# Billing Operations, Forgot Password and Product Simplification

**Phase:** Product simplification, billing operations and platform admin experience.
**For:** the product owner, and the team that runs billing and support.
**Status:** built, tested and driven live. Not committed.

**Live verification**, on the production-shaped stack (Daphne, `DEBUG=0`, PostgreSQL with row-level security as the app role, Redis, real SMTP):
- **37/37 checks**, 0 server 5xx, 0 ERROR log lines.
- Screenshots in headless Chrome showed no page errors.

---

## The three success criteria that were hard

| Criterion | Before | Now |
|---|---|---|
| **Forgot password works** | Did not exist. Every forgotten password was an administrator's job; a customer's only administrator needed platform support | Self-service, branded, tenant-aware, one-hour single-use link, signs out every other session |
| **Payment approvals work, no engineer** | Read-only queue; approval was a Django-shell procedure | Payment Center: Approve, Reject (reason required) or Ask for more, in one step. The subscription activates, the audit is written, the customer is emailed |
| **Payment methods fully configurable** | Editable only in Django admin; no Khalti, Fonepay or QR | Console page: add, edit, hide, archive, restore. Bank, eSewa, Khalti, Fonepay, QR, other |

---

## 1. Overlapping pages inventory

From two code audits and the browser runs. "Overlap" means the same data in two places, not two pages on one subject.

| Area | Pages | Same data? | Verdict |
|---|---|---|---|
| Task review | `/tasks/pending-review`, `/tasks/review-queue`, `/tasks/needs-me`, `/tasks/mine` | The first two: **yes**, under-review tasks from the same scoped query; the queue adds age and reviewer. The last two: no (my own work) | **MERGE** pending-review → review-queue. Done |
| Leave analytics | `/admin/analytics`, `/analytics/leave` | Overlapping figures; the first's API is **admin-only**, so HR and heads got an error page | **MERGE** → `/analytics/leave`. Done |
| Executive dashboards | `/executive`, `/analytics/executive` | **No**: Board front door vs attendance-health drill-down | KEEP both; consider showing `/executive` to the Board only |
| My assets | `/assets`, `/inventory/dashboard`, `/inventory/my-assets` | Launcher, scoped dashboard, own list. For an employee the last two coincide | KEEP for now; later MERGE my-assets into the dashboard for employees |
| Calendars | `/calendar`, `/leave/calendar`, `/leaves/my-calendar`, `/tasks/calendar` | **No**: festivals, team leave, my leave, task due dates | KEEP |
| People hubs | `/people`, `/workforce` | Launcher vs personal portal | KEEP |
| Team views | `/workforce/team`, `/attendance/records`, `/leaves/team-attendance` | Live tiles vs raw rows vs monthly heatmap | KEEP; the team dashboard is the one manager view (see §10) |
| Overdue tasks | `/tasks/overdue`, `/tasks/overdue-screen` | Similar lists | Search label disambiguated last phase ("Chase overdue tasks"); MERGE candidate |
| Profile | `/me`, `/profile` | Summary vs editable details and security | KEEP; the account menu routes between them |
| Dashboard figures | Console "Monthly forecast" and "Payments to verify" tiles vs the new Revenue section | **Yes** | **REMOVED** from the top band. Done |

## 2. Pages to merge

**Done** (with redirects, so bookmarks keep working):
- `/tasks/pending-review` → `/tasks/review-queue`. Navigation, the Home widget and the task dashboard were retargeted.
- `/admin/analytics` → `/analytics/leave`.
- Console top-band money tiles → the Revenue section.

**Recommended next:**
- `/inventory/my-assets` into the inventory dashboard's self view, for employees.
- The two overdue-task pages.
- `/memos/my` (an alias of `/memos/outbox`).
- `/admin/leaves/calendar-events` (an alias of holidays, with a tab preselected).

## 3. Pages to remove

**Not deleted.** This was a report-only brief, and deleting files can't be undone without version control. Recommended:
- `components/layout/LeaveSidebar.jsx`: only its tests import it; the live rail is `navConfig.js`.
- `pages/RoleLanding.jsx`: never imported; it was kept as a rollback.
- The two alias routes above, once their redirects have been in place for a release.

**Gated rather than removed:** `/admin/leaves/employees/:id` and `/admin/leaves/calendar-events` were open to every role in the router while their APIs refuse non-admins. They now route-guard to admin.

## 4. Forgot password design

**Flow:** sign-in → **Forgot your password?** → enter email → branded email → **Choose a new password** → sign in.

| Requirement | How |
|---|---|
| Secure | **Token:** Django's reset token. It is bound to the password hash and the last sign-in, so it dies on use or on any sign-in. **Expiry:** `PASSWORD_RESET_TIMEOUT`, one hour. **Throttle:** 10 per hour per IP. **No enumeration:** the same answer, status and body for known and unknown addresses. **Host safety:** the link is built from the organization's **canonical** address, never the request's Host header. **After a reset:** every other session is revoked, and any sign-in lockout is cleared |
| Tenant aware | The account is found exactly as sign-in finds it: by email **within this hostname's organization**. A platform operator's address typed on a customer's page gets nothing. Under row-level security the link can only reach accounts the hostname's tenant can see |
| Branded | The email carries the customer's logo, name and colour, with "Powered by" in the footer. The pages use the same shell as sign-in (`components/auth/AuthShell.jsx`) |
| Mobile | The single-column sign-in layout, with full-width controls |
| Success flow | The link is checked **before** the form is shown, so an expired link says so straight away and offers "Send a new link". On success: "Your password has been changed", then **Sign in** |

Endpoints:
- `POST /api/v1/auth/password-reset/`
- `GET` and `POST /api/v1/auth/password-reset/confirm/`

Code: `users/password_reset.py`, `pages/ForgotPassword.jsx`, `pages/ResetPassword.jsx`.

Tests: 8 backend (enumeration, Host injection, single use, expiry, weak password, sessions ended, operator refused on a tenant host, inactive account) and 5 frontend. Live: 9 checks, including the old session refused after a reset.

## 5. Search expansion

Ctrl+K now searches:

| Group | Source | Who sees results | Opens |
|---|---|---|---|
| People | the directory (no emails) | everyone | the employee record (administrators); informational for others |
| Departments | the department tree | everyone | department management (administrators) |
| Tasks, Memos, Minutes, Circulars | unchanged | per module | the record |
| **Leave** | `/leaves/?search=` (**new** `search_fields`) | own requests; a head's department; HR all | review queue or my applications |
| **Attendance** | `/attendance/?search=` (**new**), last 7 days | department heads, HR, admin | attendance records |
| **Assets** | `/inventory/items/?search=` (already supported) | register readers | the asset |

- **Every source is role-gated** (`gate`). A source the role may not query is never called, so an employee never sees "Assets couldn't load" from a 403.
- **Search only narrows**, never widens. It runs on each endpoint's existing role-scoped query. Verified live: a department head finds a team member's attendance; an employee searching a colleague gets nothing.
- The cache key includes the role.
- **Organizations** are searched in the platform console's top bar. The tenant palette never searches other organizations.
- **Bug found and fixed on the way:** a department head with no department assigned saw **every unassigned employee's attendance**. The filter on a null department became `IS NULL`. Now they see their own. A regression test is included.

## 6. Payment methods architecture

```
PaymentInstruction (platform table, no tenant)
  method       bank_transfer | esewa | khalti | fonepay | qr | other
  label        name customers see
  bank_name, branch, account_name, account_number, esewa_id, khalti_id
  qr_image     platform/payment-instructions/…   (public by design: it is what customers pay to)
  instructions_html   plain text from the console → escaped → linebreaks → sanitized
  is_active    shown to customers or hidden
  archived_at  retired (kept, because past payments name it); restorable
  sort_order
```

- **Console:** Platform → **Payment methods** (`/platform/payment-methods`). Actions: Add, Edit, Hide from customers, Show to customers, Archive, Restore.
- **Every change is on the audit trail.**
- **The server refuses a method nobody could pay with:**
  - a bank without an account number
  - eSewa or Khalti without an ID
  - Fonepay or QR without the image
- **Hardcoded nowhere:** the customer's subscription page reads exactly these rows.
- **Instructions are typed as plain text** and stored as sanitized HTML, so a compromised operator account cannot place a script on every customer's page. A test sends `<script>`.
- **No active method:** the page warns, because the customer would see what they owe and no way to pay it.

## 7. Payment approval workflow

```
customer requests plan ─► awaiting_proof ─(receipt)─► submitted ─┬─ Approve ─► verified ─► subscription active / extended
                                                                  ├─ Ask for more ─► needs_info ─(receipt again)─► submitted
                                                                  └─ Reject (reason) ─► rejected ─(receipt again)─► submitted
```

**Payment Center** (Platform → Payments):
- **Tabs:** To review, Waiting on customer, Decided.
- **Each card shows** (Part 11):
  - organization, plan, amount
  - method, their transaction ID, paid on
  - submitted at, by whom
  - their note
  - **View receipt**, which opens inside the console from an authenticated, hardened response

**Approve** (Part 12). In one transaction:
1. Claim the payment if nobody has.
2. Verify, which runs the existing locked, idempotent service.
3. The subscription activates or extends, with its subscription event.
4. Write the platform audit entry.

Then the customer is emailed, and revenue figures update, since they read verified payments. A payment another operator is reviewing is **not taken** from them; the console says who has it. Approving twice is refused.

**Reject** (Part 13): a reason is required. The customer's subscription page shows the reason, the next step, and the upload form, in plain words. The email says the same.

**Ask for more** (new state `needs_info`): the reviewer's question reaches the customer; their resubmission clears it and returns the payment to the queue.

**"Submitted by"** is stored on the payment at submission. The live run found it blank: under row-level security the console cannot read the tenant's user row, which is correct. SQLite tests could not show this.

**Customer side** (Parts 9 and 10):
- Shows the current plan, renewal amount, expiry, days remaining, every active payment method (including Khalti) with instructions, and plan choice (renew, upgrade, activate).
- The receipt form takes method (now including Khalti, Fonepay, QR and other), transaction ID, date paid, screenshot and notes.
- **Statuses shown:** awaiting proof, submitted, under review, more information requested, verified, rejected.

**Docs:**
- Support runbook §5b was rewritten: a console procedure, with the shell only as a fallback.
- Launch report N1 is **closed**.

## 8. Revenue dashboard design

Platform console, **Revenue** section (Part 14):

| Tile | Meaning |
|---|---|
| **Collected this month** (last month beneath) | Cash: verified payments, by the day verified |
| **Monthly recurring** | What active subscriptions are worth per month (committed, not collected) |
| **Annual run-rate** | Monthly × 12 |
| **Payments** | Count to review; approved and rejected in 30 days. Links to the Payment Center |

Plus, unchanged: trials, active organizations, expiring subscriptions (the attention strip), and lifecycle counts.

**Collected is not committed.** Both are shown because each answers a different question: whether the business is growing, and whether billing is keeping up. Verified live: approving NPR 9,990 moved "Collected this month" by exactly that.

Money now reads "NPR 832.50", not "NPR 832.5".

## 9. Platform admin improvements

- **Alerts strip** (Part 15) adds:
  - **failed provisioning in 30 days** ("Somebody verified their email and got no workspace")
  - **trials ending this week**, by name
  - pending payments, now worded honestly: "waiting for you to confirm it", not "cannot work"
- The all-clear state shows when there are no alerts.
- **Quick actions:** New organization, **Payments to review**, Review domains, **Launch readiness**, Audit trail, Platform health.
- **Recent lists:** registrations, organizations, payments, activity and sign-ins, unchanged from the earlier phase.
- **Navigation:** **Payment methods** added to the console sidebar and console Settings.
- **Profile centre** (Part 16) for all four roles:
  - avatar menu, profile page, password change, security, sessions with **Sign out everywhere else**, recent activity and preferences
  - built last phase for tenants and in the handover phase for operators
  - operators now also have **Forgot password** on the console host

## 10. Product simplification plan

**Done this phase:**
- **Employee (Part 2):** attendance stays the hero, with greeting, status, working time, location and one button. The order below it is Tasks, Leave, Documents, Profile.
- **Manager (Part 3):** **one starting point**. Home shows **Your team today** under the manager's own attendance: present, late, half day, on leave and absent counts, and who is not in yet. It links to the single team dashboard (`/workforce/team`). Approvals are already Home's "Pending reviews". The duplicate review page was merged.
- **Admin (Part 4):** Home carries an administrator row: Add or manage users, Departments, Attendance rules, Leave policies, Reports, Settings. Each is one click instead of three menus. Setup moved to Settings last phase.

**Next, in order:**
1. Merge the overdue-task pair and the employee "my assets" pair; drop the two alias routes.
2. Delete `LeaveSidebar.jsx` and `RoleLanding.jsx` after one release.
3. Give department heads and HR a Reports entry. They reach reports only through search today.
4. Show `/executive` to the Board only.
5. Correct the navigation file's `UNLISTED` notes, which claim links that don't exist.

### Part 7: activity feed, a proposal (not implemented)

| Event | Recommended visibility | Why |
|---|---|---|
| Attendance (check-in/out) | **Private + team lead** | Colleagues watching each other's arrival times is surveillance, not information. Managers already see it on the team dashboard |
| Leave (applied, approved) | **Private + approvers**; **team**: "on leave today" with no reason | The reason may be medical; that someone is away is useful to the team |
| Task events | **Team**: participants and the department | Work is shared; this is the feed's real value |
| Approval events | **Private** to requester and approvers | A decision about a person is theirs |
| Document events (memo, circular published) | **Organization-wide** for circulars; **addressees** for memos | Circulars are broadcasts by definition |

**Recommendation:** build it as a **team feed** (tasks, circulars, "on leave today"), not an organization-wide stream of who did what. The visibility rules should live server-side in the event query, never in the client. It needs a decision from you on attendance visibility before it is built.

## 11. Remaining UX risks

1. **The activity feed waits on a visibility decision** (§10, Part 7).
2. **No online payments.** Every payment is still a human review, now one click. A gateway (eSewa, Khalti or Fonepay APIs) would remove the wait, and is an integration.
3. **Emails are synchronous** from the console (approve, reject, ask). A slow SMTP server slows the button, though the decision is already committed. Fine at current volume; move to a queue at scale.
4. **People search opens a page only for administrators.** Other roles see who someone is, with nowhere to click. That needs a person page.
5. **Two pairs of overlapping pages remain** (overdue tasks, employee "my assets"), plus two alias routes and two dead files (§2, §3).
6. **Customers cannot change their attendance mode**, and shifts and comp-off rules are API-only (carried from last phase).
7. **Report generation can still surface Python exception names** to users (carried).
8. **Payment method QR images are public files.** That is by design, since every customer is meant to see them, but it means anyone with the URL can see the platform's payment QR.

---

## Verification

| Check | Result |
|---|---|
| Live drive (37 checks) | **37/37**. Payment methods CRUD and validation; a customer requests, submits, is asked for more, resubmits; approval activates the subscription with email, audit and revenue; rejection with reason and next step; forgot password end to end (same answer for unknown addresses, canonical link, single use, old session dead, new sign-in works); scoped search for leave, attendance and assets, including employee refusals. 0 server 5xx, 0 ERROR log lines |
| Backend SQLite, full suite | **4786 passed**, 0 failed |
| Backend PostgreSQL + RLS, as `nifn_app` | **4828 passed**, 0 failed. This phase's 325 payment, reset, search and console tests were re-run on a fresh database after the last change: 325 passed |
| Frontend, full suite | **1586 passed**, 0 failed; lint clean across `src` |
| Browser screenshots (console, Payment Center, payment methods, forgot/reset on desktop and phone, manager and admin Home, tenant subscription, search) | 0 page errors |

**New tests:**

| Area | Tests |
|---|---|
| Payment Center (backend) | 14 |
| Forgot password (backend) | 8 |
| Attendance scope and search | 2 |
| Leave search | 2 |
| Console authority, new functions and routes | 9 functions and 8 routes |
| Payment Center (UI) | 4 |
| Forgot and reset (UI) | 5 |
| Search gating | 2 |
