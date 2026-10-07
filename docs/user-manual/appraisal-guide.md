# Performance Appraisal — User Guide

The appraisal module routes a performance cycle through a **10-stage workflow**:

> **Goal Setting** → **Goal Approval** → **Mid-Year Review** →
> **Self Assessment** → **Supervisor Review** → **Review Committee** →
> **Final Review** → **Development Plan** → **Training Plan** → **Closed**

Only HR opens an appraisal. Everything after that is a conversation between
named people, recorded.

---

## What this system does and does not do

It puts evidence in front of people, routes the conversation to the right
person, and records what was decided.

It does **not** decide. There is no score of any kind, no overall or composite
rating, no ranking, no leaderboard, and no algorithm that turns your task
activity into a judgement about you. Competency levels are recorded as **words**
— "Meets Expectations", not a 3 out of 5 — precisely so nobody can average them
into a number that would then be quoted for years.

Every judgement in your record was made by a person, who had to write down why.

---

## Where things live (**People & Attendance → Appraisal**)

| Screen | Who sees it | What it answers |
|---|---|---|
| **My appraisal** `/appraisals` | everyone | Where mine is, what needs me |
| **My goals** `/appraisals/goals` | everyone | Objectives, weights, progress |
| **My evidence** `/appraisals/evidence` | everyone | What the system knows |
| **Team reviews** `/appraisals/team` | supervisors | Direct reports only |
| **Calibration** `/appraisals/committee` | committee members | Reviews you sit on |
| **Cycle dashboard** `/appraisals/hr` | HR / Admin | The round, organisation-wide |
| **Cycles** `/appraisals/cycles` | HR / Admin | Open and close a round |

---

## By role

### Employee — your own appraisal

1. **Goal setting.** Write your objectives. Each has a weight, and **the
   weights must total 100%** before you can submit them — the page tells you how
   much is still unallocated. Objectives are yours to *propose*, so you submit
   them yourself.
2. **Goal approval.** Your supervisor accepts them, or sends them back with a
   reason. Objectives are frozen while they are in front of them: a set that
   can be edited while it is being approved is a set nobody can be held to.

   Each objective shows its own state — **Draft** while you are writing,
   **Approved** once your supervisor has accepted it, **Locked** after the
   mid-year checkpoint. That badge is what settles "we never actually agreed
   that": until it says Approved, nobody has.
3. **Target vs achievement.** *Target* is what you agreed success looks like.
   *Achievement* is what actually happened. They are separate fields on purpose —
   overwriting one with the other is how a year's objectives quietly become
   whatever was delivered.
4. **Self assessment.** Five prompts: Achievements, Challenges, Lessons Learned,
   Future Goals, Comments. **Every one is optional** — answer the ones that apply
   to your year. Saving keeps it; it is not with your supervisor until you
   **submit** it.
5. **Cite evidence.** On your appraisal, attach the figures that support an
   objective, with a note saying why they are relevant. You choose what is cited.
   Citations are frozen with the date they were read, so a number quoted months
   later can be checked against the day it was true.
6. **Read the feedback.** Supervisor and committee comments are gathered in one
   place on your record.

### Supervisor — your direct reports

**Team reviews** shows the people you actually supervise — not everybody in your
department. Appraisals hold self-assessments and promotion recommendations, so
membership is explicit rather than inferred from an org chart.

1. **Pending Reviews** is what is with *you*. Everything else is with somebody
   else, and the page says which.
2. Open a record to review the goals, rate competencies, read the evidence and
   write your review.
3. **Complete supervisor review** to pass it on, or **Send it back a stage** with
   a written reason. The reason is required, and it is the only thing the person
   receiving it back will actually read.
4. **Final review** is where you record the written summary and, if it applies,
   a promotion readiness recommendation.

### Committee — calibration

**Calibration** shows only the appraisals you were placed on, across teams.
Sitting on one committee gives you no view of any other record.

Calibration here means reading across teams so that "exceeds expectations" means
something comparable between one supervisor and another. **It is not moderation
to a distribution:** there is no curve, no quota, no forced ranking, and no
aggregate of your queue for you to balance. Every appraisal is read on its own
record.

### HR — run the cycle

1. **Cycles** — create a round with its period and its three advisory
   deadlines. Deadlines drive reminders and the overdue columns; they never lock
   anybody out of their own appraisal.
2. **Open an appraisal** for each employee, naming a supervisor and optionally a
   committee. Nobody supervises or sits on the committee for their own record.
3. **Cycle dashboard** — completion, where the round is stuck, department
   progress, training needs, promotion readiness, development plans and review
   delays.
4. **Decide training.** Approving, scheduling or declining a request is HR's,
   because it costs money and a calendar slot. Every decision carries a name and
   a note — a declined request nobody owns is how training needs disappear.

---

## Promotion readiness

Four states, three of which are recorded answers:

- **Ready**
- **Ready With Development** — recommended, conditional on named development
- **Development Required** — not yet, with what would change that
- **Not Considered** — the question was never reached

"Not considered" and "development required" are different things, and the system
keeps them apart. Anybody without a recommendation is **absent** from the
reports, not listed as a no. **A rationale is mandatory in all three
directions** — including the negative, because a no with no stated development
is a verdict nobody can act on or appeal.

Nothing here is computed, scored or ranked, and the list is ordered by name.

---

## Training plan

Four kinds, because only two of them cost money:

| Kind | What it is | Who supplies it |
|---|---|---|
| **Training** | A course | Budget |
| **Certification** | A qualification | Budget |
| **Mentorship** | A named colleague's time | A mentor |
| **On-the-job** | A stretch assignment | Your manager |

Each carries a priority, a status, and the name of whoever decided it.

---

## Your evidence

**My evidence** shows what the system knows about your activity, grouped by
source: Tasks, Memos, Minutes, Circulars, Attendance, Leave and Inventory.

Today only **Tasks** publishes evidence. The other six are shown as *not
collected on this system* — and that wording matters. It is an **absence, not a
zero**: your memo and minute work simply is not measured here, and nothing in
your appraisal should be read as though it were.

Every percentage is shown with the number it was taken over. Evidence is
labelled **Used** (cited on your record), **Suggested** (available, not yet
cited) or not collected.
