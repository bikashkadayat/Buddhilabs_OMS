# Task Management — User Guide

The task module routes a piece of assigned work through a **9-state workflow**:

> **Draft** → **Assigned** → **In Progress** → **Under Review** → **Completed**
> · plus **On Hold**, **Blocked**, **Returned** (rework) and **Cancelled**

Everyone has tasks. **Creating and handing out** a task is a management action —
HR, Department Head and Admin only — which mirrors the server rule, so an
Employee is never shown a button that would be refused.

---

## Statuses

`draft` → `assigned` → `in_progress` → `under_review` → `completed`
plus `on_hold`, `blocked`, `cancelled`, and rework (a returned task goes back to
`in_progress` rather than getting a status of its own — see "Returned", below).

Task numbers look like `NIFN-TSK-2083-0001` and are issued once, on creation.

## Where things live (**People & Attendance → Tasks**, or the Tasks rail)

| Screen | Who sees it | What it answers |
|---|---|---|
| **Dashboard** `/tasks` | everyone | My counts, and what needs me |
| **My tasks** `/tasks/mine` | everyone | Open work assigned to me |
| **Board** `/tasks/team` | Dept Head + | Kanban across the team |
| **Calendar** `/tasks/calendar` | everyone | Day / week / month by due date |
| **Overdue** `/tasks/overdue-screen` | everyone | What is late, oldest first |
| **Workload** `/tasks/workload` | Dept Head + | Who is carrying what |
| **Reports** `/tasks/reports` | Dept Head + | Seven reports, CSV and PDF |
| **My performance** `/tasks/my-record` | everyone | Your own evidence record |

Reachable but not on the rail (use search, or the dashboard tiles): Templates,
Review queue, Analytics, Team evidence, Drafts, Assigned by me, Due today,
Completed, All.

---

## By role

### Any user — do the work

1. **Tasks → My tasks**, open one.
2. **Accept** it. Until you accept, the task sits at *Assigned* and the person
   who raised it can see that nobody has picked it up.
3. Work through the **checklist**. Ticking items moves the progress bar;
   progress is recorded in steps (0 / 25 / 50 / 75 / 100).
4. **Attach evidence** — PDF, DOCX, XLSX, images or ZIP. Attachments keep their
   history, and every download is recorded.
5. **Comment** to ask a question. `@name` notifies that person. An edited
   comment is marked as edited.
6. **Submit for review** when done. The task moves to *Under Review* and is now
   with the reviewer, not you.

### Department Head / HR / Admin — assign and review

1. **Tasks → Create task**, or start from a **template** (which brings its own
   checklist groups with it).
2. Set assignees, a reviewer, a due date and a priority. The department is taken
   from the assignees, not from you — so raising a task for another team files it
   under that team.
3. **Review queue** shows what is waiting on your decision, oldest first, with
   how long it has waited.
4. **Approve** to complete it, or **Return** it with a written reason. Returning
   puts the task back to *In Progress* with your reason attached; it is not a
   rejection and does not close anything.

### Blocked and On Hold

Both stop the clock, and they mean different things. **Blocked** is "I cannot
proceed because of something outside my control" and carries a reason. **On
Hold** is a deliberate pause by the person who owns the work. Both are visible
on the board so nobody has to ask.

---

## Reminders and escalation

Sent by the nightly `send_task_reminders` job at 07:00:

- **7 days, 3 days, 1 day and on the due date** — to the assignee.
- **Overdue** — daily to the assignee.
- **Review pending** — to the reviewer once a task has waited.
- **Escalation** — supervisor, then HR, then management, on the thresholds in
  `TASK_ESCALATION_*`.

Every send is guarded by a (task, kind, day) row, so a retry after a failed
night sends nothing twice.

---

## Your task record

**Tasks → My performance** shows what your task activity looks like, in the
shape the appraisal reads it: assigned, completed, completion %, on-time %,
reviews you performed, checklist items and evidence files — each beside the
number it was calculated over.

**It is a record of activity, not an assessment.** Nothing there is scored,
ranked, weighted or compared with a colleague's. If you have only a handful of
tasks in the period, the page says so in words, because percentages over a
handful of records are noise — and you are entitled to say that if anybody
quotes them at you.

You can see this page **before** anybody uses it in a conversation about you.
That is deliberate: a record you cannot inspect is one you cannot correct.
