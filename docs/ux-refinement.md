# Experience Refinement

**Phase:** World-class SaaS experience refinement.
**For:** the product owner, and whoever builds the next round.
**Method:** a demo school ("ABC School") with an administrator, a department head and an employee, driven in headless Chrome against the production-shaped stack: Daphne, `DEBUG=0`, PostgreSQL with row-level security, Redis and real SMTP, at 1440 px and 390 px. Two code audits ran alongside, one of user-facing wording and one of navigation. Every finding below was seen on screen or traced in code. Not committed.

---

## 1. UX problems found

**Bugs, now fixed**

| # | Problem | Who it hit |
|---|---|---|
| 1 | **Every employee saw the administrator's setup checklist** ("Upload your logo", "Review your leave policy"…) on their home page, with working **Mark done** buttons. Any employee could dismiss the checklist for the whole organization, or tick steps on the administrator's behalf | Every non-admin user, every workspace |
| 2 | **The customer's colours vanished after the first sign-in.** The sign-in page's theme cleanup deleted the colours the signed-in app had just applied, so the workspace fell back to the platform's palette for the session. It was intermittent, because it depended on which request finished first | Any user, first sign-in |
| 3 | **"Review your attendance rules" led nowhere.** The setup checklist linked to `/admin/attendance/policies`, which did not exist. The attendance policy API was complete, but no screen could show or change office hours | Every new administrator |
| 4 | **Three asset cards led employees to "Unauthorized"** (Take-outs, Assignment, Register), plus the Asset Register rail link. Employees' own take-out requests had no link anywhere | Every employee |
| 5 | **Bulk leave action said "rejectd"** (the verb with a "d" appended) | HR and administrators |
| 6 | **Screen readers announced the password field as "Password Show"** (new sign-in page; found by the browser run before it shipped) | Assistive-technology users |
| 7 | **Page subtitles never rendered** on Settings, Branding and both Domains pages: the wrong prop name was passed | Administrators, operators |
| 8 | **Setup pages were open to non-admins in the router** (employees, policies, holidays, leave types, bulk actions). The backend refused them, so HR got pages full of errors | HR |
| 9 | **A malformed or HTML error body was shown as text**: a proxy's 502 page could appear as raw markup | Everyone, on outages |

**Experience problems, now fixed**

- Attendance, the main reason people open the system, sat at the bottom of Home as "In — · Out — · Absent". The check-in button lived on another screen.
- Signing in failed into a modal titled **"Login Failed"** with an **OK** button. The page carried NIF-era art (mandala, Himalaya, network lines) and, for a customer with no logo, read "OFFICE MANAGEMENT SYSTEM" in capitals.
- The header said **"PORTAL SYSTEM"** where the organization's name belongs, and had **no account menu**. On a phone, Sign out was a page away.
- There was **no way to change a password** after the forced first one, and **no way to see your sessions**. The profile showed neither **manager** nor **designation**.
- The API answered in framework defaults, which the screen showed verbatim:
  - "Request was throttled. Expected available in 37 seconds."
  - "No LeaveRequest matches the given query."
  - "You do not have permission to perform this action."

  About 70 screens could show axios's "Network Error" or "Request failed with status code 500", and a dozen showed "Action failed."
- Search pointed "Task board" at the wrong page, listed "Overdue tasks" twice, still used the old jargon "Calibration queue", and missed about 70 pages, including Settings and Leave balance.
- Configuration that is touched a few times a year (leave policies, leave types, holidays) sat in the administrator's daily menu. The phone's Create sheet had no "Create task", the most common thing anybody creates.

## 2. Login improvements

- **The customer's page.** A full-height brand panel in the customer's colour shows their logo (or their monogram when they have none), their name and their welcome line from Branding. The form says **"Sign in to ABC School"**. On a phone the brand collapses into a header row. "Powered by Buddhi Labs" is the only platform mark.
- **Errors inline, in plain words.** The modal is gone. The server's specific reason still shows (for example a suspended workspace), but the generic cases are rewritten:

  | Situation | Message |
  |---|---|
  | Wrong email or password | "That email and password don't match. Check them and try again." |
  | Rate limited | "Too many attempts. Please wait a minute, then try again." |
  | Server unreachable | "We can't reach the server. Check your internet connection and try again." |
  | Signed out | "You've been signed out." |

- **Public signup only on the platform's own page**, never on a customer's (the previous phase's fix, kept).
- The tab title reads "Sign in · ABC School". The password field has a Show/Hide toggle with a proper label.
- New tests: `pages/login.test.jsx` (6).

## 3. Dashboard improvements

**Employee home: attendance first (Part 3).** One hero card:
- **Good evening, Bikash.**, plus the customer's welcome line.
- Status chips, live:
  - "Checked in at 9:42 AM", with "· late" or "· counts as a half day" when it applies
  - "Working 4h 12m", updated every 30 seconds
  - "At Head Office", or the address
- **One large button: the next action only.** It shows Check in, or Check out, never both. On a phone it spans the card at thumb height. Biometric-only workspaces say "Your attendance is recorded at the office device." Holidays and leave days say so instead.
- Verified in the browser: check-in from Home captured location, recorded the attendance, and updated the hero.
- The hero and the attendance widget now share one hook (`hooks/useAttendancePunch.js`), so they cannot disagree.
- The "Today at a glance" strip no longer repeats attendance. It shows leave left.

**First-login experience (Part 2).**
- Shown **to administrators only**, with the API refusing others (403).
- The ready list adds **Organization created**, **Reports ready**, **Documents ready** and **Your own password is set**.
- "Review your attendance rules" now opens a real page.

**Attendance rules page (new).** Office start, late after, grace minutes, half day after, absent cutoff, full and half day hours, and overtime. Plain labels with help text, saved on the existing audited API, open to HR and administrators.

**Platform console (Part 7).**
- An **all-clear line** when there are no platform alerts, instead of empty space.
- A **Launch readiness** quick action.
- "Verify a payment, claim, check the receipt, accept" became **"Payments to review"**, because there is still no accept button (launch report N1).

**Client handover (Part 8).** The success screen is titled **Workspace ready**, has an **Open portal** button, and the email button reads **Send welcome email to…**. Everything else was delivered in the handover phase.

## 4. Platform admin improvements

- All-clear and alert states, as above.
- The Launch readiness quick action, as above.
- The recent-activity tabs, profile, sessions, menu and handover card from the earlier phases are unchanged and re-verified.
- An honest payment quick-action label.

## 5. Profile system improvements

- **Account menu on the avatar, at every width** (Part 4):
  - header with name, email, role and department
  - My profile
  - Security
  - Recent activity
  - Attendance summary
  - Leave summary
  - Sessions
  - Preferences, which opens the notification preferences tab directly
  - Sign out

  It closes on Escape or an outside click, and is a full-width sheet with 48 px rows on a phone. The separate desktop Sign out button is gone.

  *This reverses an earlier decision to make the avatar a plain link; the reason is recorded in the code.*
- **Profile page:**
  - adds **Designation** and **Manager**; the manager is the department head, the same answer attendance corrections are routed by
  - a **Password** section for changing your password any time
  - a **Where you're signed in** section listing devices with **Sign out everywhere else**

  The menu's Security and Sessions links scroll to these sections.
- The **header** shows the organization's name where "PORTAL SYSTEM" was.

## 6. Search improvements

- Fixes:
  - Task board points to `/tasks/board`
  - the duplicate "Overdue tasks" becomes "Chase overdue tasks"
  - "Calibration queue" becomes "Reviews I sit on", and "My evidence" becomes "My work record", matching the menus
  - "Reports" becomes "Build a report"
- About **30 destinations added**, each gated by role exactly as the menus are:
  - Profile, Change password, Signed-in devices, Notification preferences
  - Leave balance, Leave policy, Nepali calendar, weekly and monthly leave reports
  - My take-out requests, My task drafts
  - All memos, Returned memos, All circulars
  - Attendance records, Attendance reports, Attendance rules
  - Settings, Subscription, Branding, Custom domain
  - Employees, Departments, Leave policies, Leave types, Holidays, Bulk leave actions, Biometric attendance
- Take-out approvals is now offered only to the roles that can open it.
- **Not done:** results for employees, leave requests, attendance records or assets. The backend list endpoints for those don't support search; see §9.

## 7. Activity feed improvements

- The home feed (a day-grouped timeline of the user's notifications) keeps its design.
- Its empty state reads "You're all caught up. Updates about your leave, tasks and documents will appear here." and its error state is plain.
- **Not done:** an organization-wide feed ("Bikash checked in", "User added"). It needs a backend event stream, and a policy decision on who may see whose check-ins. See §9.

## 8. Mobile improvements

- At 390 px there is **no horizontal scroll** on Home, the menu, sign-in or the console.
- The **check-in/out button spans the hero at 60 px tall**: one thumb, no navigation.
- The account menu is a full-width sheet with 48 px rows, and **Sign out is one tap away** on a phone, where before it was a page away.
- The Create sheet has **Create task** first.
- The sign-in page collapses to one column, with the customer's brand on top.

### Human language (Part 11), across the product

- **One backend handler** (`config/exceptions.py`) rewrites DRF's stock refusals: throttling, the generic 404 and 403, missing session, bad request. Messages written on purpose by a view pass through unchanged.
- **One frontend describer** (`services/apiErrors.js`) now:
  - handles 429
  - never shows an HTML page or axios's own text
  - powers the shared error state used by about 70 screens
- About a dozen "Action failed." and "That action failed." messages now read "That didn't go through. Please try again."
- Specific backend messages rewritten:
  - password "Incorrect." → "Your current password isn't right…"
  - photo upload errors
  - deactivated account → "Your account has been turned off…"
  - the leave workflow conflict
  - "decision must be 'approve' or 'reject'" → "Please choose Approve or Reject."
- First-login password errors no longer run several messages together without spaces.
- The bulk leave toast reads "3 requests approved." or "3 of 5 requests approved. 2 couldn't be processed — they may already have been dealt with."

### Identity (Part 12)

- The console stays Buddhi Labs.
- Tenant pages carry the tenant's logo or monogram, name and colours, with "Powered by Buddhi Labs".
- The NIF strings removed in the handover phase stay removed.

## 9. Remaining UX issues

Ranked by how much each costs real users.

1. **No self-service "Forgot password".** The sign-in page now says who to ask, but a customer's only administrator still has nobody to ask except platform support.
2. **Search cannot find people, leave requests, assets or attendance.** Those endpoints ignore `?search=`. People results exist, but there is no person page to open. This needs backend `search_fields`, and a person page.
3. **No organization-wide activity feed** (Part 6's "Bikash checked in"). Needs a decision on visibility first: should colleagues see each other's check-ins?
4. **Navigation still overlaps** where pages duplicate each other. Each pair needs a decision about which page survives:

   | Area | Overlapping pages |
   |---|---|
   | Task review | four: Reviews, Review queue, Needs me, Mine |
   | Overdue tasks | two |
   | Leave analytics | two |
   | Executive dashboards | two |
   | Calendars | four |
   | "My assets" | three |
   | People hubs | `/people` and `/workforce` |

   About 40 routes are reachable only by URL. `LeaveSidebar.jsx` and `RoleLanding.jsx` are dead code. None of this was deleted here.
5. **Customers cannot change their attendance mode** (app check-in, biometric, or both). Only an operator can, from the console.
6. **The attendance rules page covers the everyday fields only.** Shifts, per-department assignment and comp-off thresholds remain API-only.
7. **Seed text** ("Seeded at provisioning…") is hidden on the attendance rules page but still visible in task template descriptions.
8. **Some modules still use their own alert dialogs** with an OK button (inventory pages), and about 20 places still assemble errors by hand rather than through the shared describer. Their wording is better, but the code is not unified.
9. **Reports can leak Python exception names** ("Generation failed: KeyError: 'dept'"). The report service stores `type(exc).__name__`. Not changed here.
10. **Payments still cannot be approved in the console** (launch report N1). The quick action now says "review" rather than promising "accept".

---

## Verification

| Check | Result |
|---|---|
| Frontend, full suite | **1576 passed**, 0 failed; lint clean across `src` |
| Backend, SQLite, full suite | **4727 passed**, 1 failed: a test asserting the old "deactivated" wording. Updated to the new wording; its file now passes (10/10) |
| Backend, PostgreSQL + RLS, connected as `nifn_app` | **4768 passed**, 1 failed: the same wording test. Re-run after the update: 10/10 |
| Browser (Chrome, live stack, three roles × desktop and phone) | every screen rendered; **0 page errors, 0 server 5xx, 0 ERROR log lines**; check-in from Home recorded attendance; tenant colours held on 16 of 16 sign-ins after the theming fix (failed on the first sign-in before it) |
| New tests | `tenancy/tests/test_onboarding_roles.py` (6), `config/test_exceptions.py` (6), `pages/login.test.jsx` (6), theming layering (2), header menu rewritten (6), describer HTML/429 (2) |
