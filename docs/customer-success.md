# Customer Success, Onboarding and Adoption

**Phase:** Customer success, onboarding and adoption experience.
**For:** the product owner, and whoever runs customer success and support.
**Status:** built, tested and driven live. Not committed.

**Verified:**
- **Live drive:** 20/20 checks on the production-shaped stack (Daphne, `DEBUG=0`, PostgreSQL with row-level security as the app role, Redis, real SMTP), with 0 server 5xx and 0 ERROR log lines.
- **Browser run:** all three first-sign-in tours appeared, six workflows were measured, and there were no page errors.

---

## 1. Customer success plan

The product now notices when a customer isn't succeeding, rather than finding out at cancellation.

| Signal | Where | How it's measured |
|---|---|---|
| **Inactive organizations** | Console → Customer health | No one active for 14+ days (or ever, in the window) |
| **Not using attendance** | same | No attendance recorded in 30 days |
| **No employees** | same | Seat count ≤ 1 (only the administrator) |
| **Near expiry / lapsed** | same | Subscription ends within 14 days, or has ended |
| **Needs attention** | same, default tab | Any of the above, sorted worst first with a 0–100 health score and **the reasons in words** |

**Privacy by construction.** Every figure comes from daily counters (`PlatformMetric`): a day, a kind, an organization and a number. Examples: "2 people active", "1 leave request created". The counter row holds nothing else, and a test asserts its exact columns. The console still reads **no customer records** to answer "are they using it?". Customer health says so on the page.

**Suggested weekly routine for customer success:**
1. Open **Customer health → Needs attention**.
2. Call the lowest scores first.
3. For "no employees", offer to help import staff; for "not using attendance", point them to the attendance help article and the rules page.
4. Check **Support → Open** and **Ratings**.

## 2. Onboarding improvements

**Getting Started centre** (`/getting-started`, administrators):
- Progress ring and "N of 8 done".
- A **Next:** button straight to the first unfinished step.
- Each step has **Do it** and **Doesn't apply to us**.
- Below: what's already set up for them.

The steps follow the brief's order. **All are measured from real data**; none is ticked by hand.

| Step | Ticks when |
|---|---|
| Upload your logo | a logo exists |
| Add your employees | more than one active user |
| Configure attendance | shift or policy changed from the seeded defaults |
| **Set up your departments** (new) | any department has a head |
| **Invite your team** (new) | someone besides the administrator has signed in |
| Create your first task | any task exists |
| **Approve your first leave** (new) | any leave approved |
| Review your leave policy | a leave type's days changed from the seed |

**Guided tours** (first sign-in, per role):

| Tour | Stops |
|---|---|
| **Employee** | Check in, Start something, Search, Notifications, Your account |
| **Manager** | Your attendance, Your team today, Things waiting for you, Search, Account and help |
| **Administrator** | Set up your workspace, Attendance, Run your organization, Search, Help |
| **Platform operator** | Revenue, Approve payments, Customer health, Find a customer |

- Each stop points at the **real control** (a `data-tour` anchor). A stop whose control isn't on screen (a phone layout, a role without it) is skipped, so a tour never describes a button that isn't there.
- **Remembered on the server** (`User.ui_state.tours_done`), so a new phone doesn't replay it.
- Controls: Escape or Skip, arrow keys, Back/Next.
- **Help → Take the tour again** restarts it.

## 3. Help Center design

`/help`, inside the product.

- **One search box** over 24 articles, typed as **How to** (tutorials), **Questions** (FAQs) and **Guides**, each shown only to the roles it applies to.
- Results rank title matches first. No match offers **Ask support**, with the query pre-filled.
- **Articles** show numbered steps, buttons to the real page, **"Was this helpful?"** (1–5 stars), related articles, and **Still stuck? Ask support**.
- **In Ctrl+K too.** "missed check" in the global search finds *Fix a missed check-in* in one click (measured).
- **No dead links.** A test reads every route in `App.jsx` and fails if any help link points at a page that doesn't exist.
- **Entry points:** avatar → **Help & support**; Ctrl+K; Getting Started; the tour's last stop.
- Content lives in `frontend/src/services/helpContent.js`: plain data, easy for a non-engineer to edit and review. Every claim in it was checked against what the product does.

## 4. User adoption plan

**Tracked** (Part 7):

| Metric | Definition |
|---|---|
| **Daily active users** | Distinct people per organization per day: counted once, the first time they use the API that day, deduplicated in the cache |
| **Weekly active** | Shown honestly as **person-days this week**: daily actives summed, an upper bound. Exact distinct weekly users would need per-person tracking, which the counters deliberately don't keep |
| **Attendance, leave, task and document usage** | Records **created** per day (check-in or punch, leave request, task, memo/minute/circular) |

**Shown:** Console → Customer health. Platform tiles with 30-day sparklines (active people, attendance, leave, tasks, documents), and per-organization 30-day usage on each card.

**Plan:**
1. **Week 1 of a customer:** the Getting Started centre and the tour carry them. Customer health flags "no employees" and "not using attendance" early.
2. **Weeks 2–4:** look for organizations with active people but no use of a module. That is a training gap; send them the matching help article.
3. **Ongoing:** the quarterly Home rating ("How is the workspace working for you?", asked only after a week of use) and per-article ratings feed the Support → Ratings tab, with a 30-day average.

**Engineering notes:**
- **Counts never get in the way.** Each runs in its own savepoint and swallows its own failure; a counter is not worth a user's request.
- **The export includes customers' own support messages** ("everything you hold about us"). The existing export-completeness guard caught the new table on its first full run.

## 5. Support improvements

**Support Center** (`/help/contact`):
- **Ask a question**, **Report a problem** or **Request a feature**.
- The **page they came from** travels with the message.
- **Your requests** shows status and our reply.

**Platform inbox** (Console → **Support**):
- Tabs: Open, Everything, Feature requests, Ratings.
- Each card shows who wrote, from which organization and page, and when.
- **Reply and resolve** emails the reply to the person. It also appears under their requests.
- New questions and problems are emailed to `PLATFORM_SUPPORT_EMAIL` when it is set.

**Feedback** (Part 9):
- "Was this helpful?" on every help article.
- "How is the workspace working for you?" on Home, quarterly, only after a week of use.
- A comment box opens **only after a low rating**, where the "why" matters.
- Never asked twice in the same browser.
- **Ratings average** in the inbox header.

**Notification centre** (Part 5):
- **One place, seven tabs**: All, Approvals, Attendance, Leave, Tasks, Documents, Payments.
- The groups are server-side, so page two is never silently filtered.
- **Payment decisions** (approved, rejected, more information needed) now arrive **in the workspace** as well as by email. They are written before the email, so a mail outage doesn't hide them.

**Empty states** (Part 4): the remaining generic ones were rewritten to say what's missing and offer the next step. For example, "You haven't applied for leave yet. **Apply for leave**" and "You haven't added any assets yet — add laptops, phones…", with an "Add first asset" button.

### Part 10: measured friction

Measured in Chrome against the live stack. The click count starts from the screen where the job begins; seconds include server round trips; typing is excluded.

| Workflow | Clicks | Time |
|---|---|---|
| Employee checks in (from Home) | **1** | 1.4 s |
| Employee finds help on a missed check-in (Ctrl+K) | **1** | 2.3 s |
| Employee reports a problem | **3** | 1.4 s |
| Admin opens Getting Started and goes to the next step | **2** | 2.4 s |
| Anyone requests a password reset | **2** (+1 in the email) | 0.5 s |
| Operator approves a payment, from the dashboard | **3** | 0.6 s |

For comparison, before the last three phases: check-in was on a different screen; there was no help to find; reporting a problem meant email; password resets needed an administrator; payment approval needed an engineer with a shell.

**User understanding** cannot be measured in a headless browser. A five-person hallway test per role, with these six tasks timed and no help offered, is the right next measure.

## 6. Remaining UX risks

1. **Help content is product copy, and will drift** as features change. The dead-link test protects the links, but not the wording. Make "update the help article" part of each feature's definition of done.
2. **Weekly active users is an upper bound** (person-days). Exact distinct weekly users needs per-person tracking. Deliberately not done; a product decision.
3. **Tours anchor to `data-tour` attributes.** A redesign that drops one silently shortens the tour; the stop is skipped, nothing breaks. A test could pin the anchors per role.
4. **The support inbox has no assignment or SLA.** With more than one operator, two people can answer the same request. It also has no tagging. Fine at current volume.
5. **No in-app announcements or changelog** ("what's new"). Customers learn about features only by finding them.
6. **No organization-wide activity feed.** It is still waiting on your visibility decision from the last phase.
7. **The health score is a simple rule count** (25 points per issue). It is good for ordering a call list, and not a churn prediction.
8. **The ratings prompt is once per browser.** Someone using two devices may be asked twice a quarter.
9. **A test that fails depending on run order.** `test_each_new_tenant_can_use_the_product_immediately` (an expected "SPECIAL" leave type) fails when certain other test files run before it, and passes alone and in the full suite. It is pre-existing, first seen in the handover phase. Worth an hour to find the shared state before it hides a real failure.

---

## Verification

| Check | Result |
|---|---|
| Live drive (20 checks) | **20/20**. Adoption counted under RLS (2 distinct people, once each, despite repeat calls); attendance and leave use counted; an idle customer flagged with reasons; support round trip with the reply emailed; rating average; tour remembered; 8 measured steps, including department and invite; employee not shown setup; payment decision in the admin's notification centre; approvals tab. 0 server 5xx, 0 ERROR log lines |
| Browser | Employee, admin and platform tours shown on first sign-in; six workflows measured (table above); no horizontal scroll on the phone help page; 0 page errors |
| Backend SQLite, full | **4802 passed**, 1 failed: the export-completeness guard, which correctly caught the new table. Fixed (support requests included in exports); export and customer-success tests re-run: 32 passed |
| Backend PostgreSQL + RLS, as `nifn_app` | **4841 passed**, 3 failed. Two were new tests that sent requests on the wrong hostname for the row-level-security harness; the third was the same export guard. All three fixed; the affected suites re-run on a fresh database: all pass except one test known from earlier phases to fail only in certain run orders (its file passes alone, 14/14) |
| Frontend, full | **1594 passed**, 0 failed; lint clean |

**New tests:**
- Backend: 10 for customer success (adoption, health, support, tours, notification groups, Getting Started), plus 3 authority routes.
- Frontend: 8, including the help dead-link guard, tour skipping and persistence, and rating behaviour.

