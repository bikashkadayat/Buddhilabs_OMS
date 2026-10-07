# User Acceptance Test Plan

**Environment:** staging, with production-shaped data (real departments, a full
holiday calendar, at least one month of attendance).
**Do not run UAT against production.** Several cases deliberately create bad
data.

## Sign-off criteria

- Zero **Critical** defects open
- No more than 2 **Major** defects open, each with a named owner and a date
- HR lead and Admin have both signed

| Severity | Meaning |
|---|---|
| Critical | Data loss, wrong attendance/leave figures, or an access-control failure |
| Major | A core workflow is blocked with no workaround |
| Minor | Workaround exists; cosmetic or convenience |

## Suites

| # | Suite | Cases | Owner |
|---|---|---|---|
| 1 | HR | 16 | HR lead |
| 2 | Employee | 12 | 3 staff members |
| 3 | Manager | 10 | 2 department heads |
| 4 | Attendance rules | 12 | HR + Admin |
| 5 | Leave | 10 | HR |
| 6 | Biometric | 20 | Admin |
| 7 | Appraisal — Employee | 12 | 3 staff members |
| 8 | Appraisal — Supervisor | 11 | 2 department heads |
| 9 | Appraisal — Committee | 7 | 2 committee members |
| 10 | Appraisal — HR | 12 | HR lead |
| | **Total** | **122** | |

**Every suite includes negative cases.** A system that does the right thing when
asked correctly is half-tested; the failures that matter come from the other
half.

---

## Suite 1 — HR (16)

| # | Case | Expected |
|---|---|---|
| HR-01 | Create an employee with role, department, joining date | Created; forced password change on first login |
| HR-02 | Deactivate an employee | Cannot sign in; existing token stops working at refresh |
| HR-03 | Reactivate | Can sign in again |
| HR-04 | Approve a correction at the HR stage | Attendance row updated, `source=HR` |
| HR-05 | Revert an applied correction | Original values restored from the snapshot |
| HR-06 | Approve a WFH request | Approved; the day is **not** attendance until a check-in |
| HR-07 | Confirm a comp-off day | Balance increases; ledger entry created |
| HR-08 | Map a biometric enrolment to an employee | Their punches begin deriving |
| HR-09 | Manual attendance entry for a missed day | Recorded, audited, `source=HR` |
| HR-10 | Change an attendance policy | Change audited; future days use the new rule |
| HR-11 | Run each report type | All generate and download |
| HR-12 | Run each analytics export | All generate and download |
| HR-13 | Executive analytics against a manual count | Figures match |
| HR-14 | Unlock a locked account | Locked user can sign in; the unlock is audited |
| HR-15 | ⛔ Open `/monitoring` | Reachable, all green |
| HR-16 | ⛔ **Negative:** open `/admin/users` as HR | 403 — admin-only |

## Suite 2 — Employee (12)

| # | Case | Expected |
|---|---|---|
| EMP-01 | First login | Forced password change |
| EMP-02 | Check in from a phone | Recorded with the correct local time |
| EMP-03 | Check out | Hours computed correctly |
| EMP-04 | Check in after 11:45 | Marked **Late** per the NIF rule |
| EMP-05 | Check in after 13:00 | Marked **Half Day** |
| EMP-06 | View own dashboard | Attendance, balances and history all correct |
| EMP-07 | Apply for leave | Submitted; department head notified |
| EMP-08 | Request WFH | Submitted, pending |
| EMP-09 | Submit a correction with an attachment | Submitted; attachment stored |
| EMP-10 | ⛔ **Negative:** upload a `.sh` file as evidence | Rejected — extension not allowed |
| EMP-11 | ⛔ **Negative:** rename an HTML file to `.pdf` and upload | Rejected — content does not match extension |
| EMP-12 | ⛔ **Negative:** open `/workforce/hr` | 403 |

## Suite 3 — Manager (10)

| # | Case | Expected |
|---|---|---|
| MGR-01 | Team dashboard | Shows exactly their department |
| MGR-02 | Approve a leave request | Approved; moves to the HR stage |
| MGR-03 | Approve a correction | Moves to the HR stage |
| MGR-04 | View team attendance trends | Correct for their department |
| MGR-05 | Run a department-scoped report | Contains only their department |
| MGR-06 | View department analytics | Own department named; others show a rank only |
| MGR-07 | ⛔ **Negative:** request another department via `?department=` | Silently returns their own scope |
| MGR-08 | ⛔ **Negative:** request the executive summary export | 403 |
| MGR-09 | ⛔ **Negative:** open `/analytics/executive` | 403 |
| MGR-10 | ⛔ **Negative:** open another department's employee record | 403 or not listed |

## Suite 4 — Attendance rules (12)

| # | Case | Expected |
|---|---|---|
| ATT-01 | Arrive before 11:45 | Present |
| ATT-02 | Arrive 11:45–13:00 | Late |
| ATT-03 | Arrive after 13:00 | Half Day |
| ATT-04 | Saturday | Weekly holiday; not an expected working day |
| ATT-05 | Public holiday | Holiday; excluded from the denominator |
| ATT-06 | Work a Saturday | Comp-off eligible; overtime recorded |
| ATT-07 | Approved leave day | On Leave; counts as compliant |
| ATT-08 | No check-in, no leave | Absent after the cut-off, not before |
| ATT-09 | Day before joining date | Not Applicable — never Absent |
| ATT-10 | Work past the threshold | Overtime hours recorded |
| ATT-11 | Approved WFH with a check-in | WFH |
| ATT-12 | ⛔ Approved WFH with **no** check-in | **Not** attendance; visible in the WFH gap report |

## Suite 5 — Leave (10)

| # | Case | Expected |
|---|---|---|
| LV-01 | Apply → dept head → HR | Approved; balance reduced |
| LV-02 | Reject at the dept-head stage | Rejected; balance untouched |
| LV-03 | Half-day leave | 0.5 deducted |
| LV-04 | Leave spanning a weekend | Saturday not counted |
| LV-05 | Leave spanning a holiday | Holiday not counted |
| LV-06 | Cancel an approved leave | Balance restored |
| LV-07 | Leave overlapping a worked day | Conflict flagged, nothing auto-corrected |
| LV-08 | Balance after several requests | Arithmetic correct |
| LV-09 | ⛔ **Negative:** apply for more than the balance | Rejected with a clear message |
| LV-10 | ⛔ **Negative:** apply on someone else's behalf | Not possible |

## Suite 6 — Biometric (20)

The terminal (a ZKTeco ZLM60) is a **PUSH** device: it posts each punch to
`/iclock/` seconds after the finger lands. Nothing polls, so "appears within 5
minutes" below is the analytics cache TTL, not a delivery delay — the punch
itself is in the database almost immediately.

**BIO-15 is the go-live gate.** Every other case can pass without anyone
touching the sensor.

| # | Case | Expected |
|---|---|---|
| BIO-01 | Enrol a user on the device | Appears as unmapped after the next sync |
| BIO-02 | Map to an employee | Punches begin deriving |
| BIO-03 | Punch in | Punch stored within seconds; analytics within 5 minutes (cache TTL) |
| BIO-04 | Punch out | Attendance completed with hours |
| BIO-05 | Power the device off, punch, power on | The terminal re-posts its buffer on reconnect; nothing lost |
| BIO-06 | Leave a device offline > 15 min | Flagged offline; alert fires |
| BIO-07 | ⛔ **Negative:** unsigned ingest request | 401 |
| BIO-08 | ⛔ **Negative:** replay a valid batch | 200, **zero duplicates** (idempotent by design) |
| BIO-09 | `device_sync --dry-run` (SDK path only, if reachable) | Prints the roster and punch counts; **no rows written** |
| BIO-10 | Run `device_sync` twice in a row | Second run creates 0 and reports duplicates |
| BIO-11 | Punch during a `device_sync` run (SDK path only) | The terminal stays usable — the sync never disables it |
| BIO-12 | ⛔ **Negative:** wrong `BIOMETRIC_DEVICE_COMM_KEY` (SDK path only) | Fails at connect naming the comm key — **does not** silently collect nothing |
| BIO-13 | Device user IDs after a full sync + import | `verify_biometric_ids --expect <ids read off the terminal>` returns **EXACT MATCH** |
| BIO-14 | Point the terminal at the OMS, watch the backend log | `PUSH handshake from <label>` appears |
| BIO-15 | ⛔ **The gate:** a real employee punches while you watch | `verify_realtime_punch --device-user-id <id>` reports CHAIN COMPLETE with no FAIL |
| BIO-16 | Full criteria board after BIO-15 | `golive_status` — every measurable criterion PASS |
| BIO-17 | Unplug the network for 10 min, punch twice, reconnect | Both punches arrive after reconnect; **nothing lost** (the terminal buffers until it gets `OK`) |
| BIO-18 | Restart the OMS mid-day, punch during the restart | The punch arrives once the server is back |
| BIO-19 | ⛔ **Negative:** post to `/iclock/cdata` with an unregistered `SN` | Non-200. Critically **not** `OK` — an OK would tell that device to delete its data |
| BIO-20 | Check `/iclock/` is not reachable from outside the LAN | Refused. The protocol has no authentication; the network is the boundary |

---

---

## Suite 7 — Appraisal, Employee (12)

Run as an employee with an open appraisal in an active cycle.

| # | Case | Expected |
|---|---|---|
| APE-01 | Open **People & Attendance → Appraisal** | Own appraisal, its cycle and its stage |
| APE-02 | Add three objectives totalling 90% | Saved; the page says **10% still to allocate** |
| APE-03 | Try to submit at 90% | Refused, naming the 100% rule |
| APE-04 | Adjust to 100% and submit | Moves to **Goal Approval**; objectives stay **Draft** |
| APE-05 | Try to edit an objective while it is being approved | Refused — a set being approved cannot be edited |
| APE-06 | After approval, re-open **My goals** | Objectives now read **Approved** |
| APE-07 | Write a self assessment across the five sections | Saved; message says it is **not with the supervisor until submitted** |
| APE-08 | Leave "Challenges" empty and save | Accepted — every section is optional |
| APE-09 | Open **My evidence** | Tasks shows figures; the other six say **not collected on this system** |
| APE-10 | Cite evidence against one objective, with a reason | Appears under **Used**, with the date it was read |
| APE-11 | Cite the same evidence again | No duplicate citation is created |
| APE-12 | ⛔ **Negative:** open a colleague's appraisal by URL | 404 — not 403, which would confirm it exists |

## Suite 8 — Appraisal, Supervisor (11)

Run as a department head with at least two direct reports and one colleague's report they do **not** supervise.

| # | Case | Expected |
|---|---|---|
| APS-01 | Open **Team reviews** | Direct reports only, ordered by name |
| APS-02 | Check **Pending Reviews** | Only appraisals at a stage you own |
| APS-03 | Approve a submitted objective set | Moves to Mid-Year; every objective becomes **Approved** |
| APS-04 | Send an objective set back with a reason | Returns to Goal Setting; objectives revert to **Draft** |
| APS-05 | Try to send it back with a 3-character reason | Refused — a reason is required |
| APS-06 | Record the mid-year review | Objectives become **Locked** |
| APS-07 | Rate a competency without a comment | Refused — a level with no reasoning is not accepted |
| APS-08 | Record supervisor review | Employee's and your ratings both visible, side by side |
| APS-09 | Record a promotion recommendation with no rationale | Refused, in all four states |
| APS-10 | Set **Ready With Development** with a rationale | Saved; the middle answer survives to the HR dashboard unchanged |
| APS-11 | ⛔ **Negative:** open an appraisal in your department that you do not supervise | 404 — department is not entitlement |

## Suite 9 — Appraisal, Committee (7)

Run as somebody named on at least one appraisal's committee and not on another's.

| # | Case | Expected |
|---|---|---|
| APC-01 | Open **Calibration** | Only appraisals you were placed on |
| APC-02 | Check the queue for aggregates | None — no curve, quota or distribution to balance |
| APC-03 | Read an appraisal from another team | Full record, including its cited evidence |
| APC-04 | Record a committee comment and complete the review | Saved and moves on |
| APC-05 | Check **Review History** after a cycle closes | Closed appraisals you sat on, linking to each record |
| APC-06 | ⛔ **Negative:** open an appraisal you are not on | 404 |
| APC-07 | ⛔ **Negative:** try to record a supervisor review | 403 — committee membership grants only the committee stage |

## Suite 10 — Appraisal, HR (12)

| # | Case | Expected |
|---|---|---|
| APH-01 | Create a cycle with its three deadlines | Saved as Draft |
| APH-02 | Activate it | Status **Active** |
| APH-03 | Open an appraisal naming employee, supervisor and committee | Created at Goal Setting |
| APH-04 | ⛔ **Negative:** name the employee as their own supervisor | Refused |
| APH-05 | ⛔ **Negative:** put the employee on their own committee | Refused |
| APH-06 | Open **Cycle dashboard** | Completion, stage distribution, department progress |
| APH-07 | Check **Review Delays** | Longest wait first, naming who each record is with |
| APH-08 | Check **Promotion Readiness** | Alphabetical, with rationales; nobody unrecommended is listed |
| APH-09 | Check the per-answer counts | Three recorded answers only — no "Not Considered" tile |
| APH-10 | Approve a training request with a note | Decision recorded against your name |
| APH-11 | Export a report as CSV and as PDF | Both download; columns match the screen |
| APH-12 | Reopen a closed appraisal with a reason | Allowed for HR only, and visibly different from a return in the trail |

### Appraisal sign-off — additional criteria

Beyond the standard criteria above, this module does not sign off unless a
reviewer has confirmed, in the UI:

- **No score, rating out of N, rank, league table or leaderboard appears
  anywhere**, on any screen, for any role.
- **Every percentage is shown with the number it was taken over.**
- **Every list of people is ordered by name**, and no column offers a sort.
- **"Not considered" is never rendered as a negative** — those people are
  absent from the promotion list, not listed as unready.

These are the properties the module exists to hold. A defect against any of
them is **Critical**, not cosmetic.

---

## Execution

Record for every case: tester, date, result (pass/fail/blocked), evidence
(screenshot or ID), and a defect reference if failed.

| Field | |
|---|---|
| Build / commit | |
| Environment | staging |
| Start / end date | |
| HR sign-off | name, date |
| Admin sign-off | name, date |
| Critical open | must be 0 |
| Major open | must be ≤ 2, with owners |
