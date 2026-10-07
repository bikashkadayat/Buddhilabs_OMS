# Memo Module — User Guide

The memo module routes an office memo through a **3-level workflow**:

> **Author (any role)** → **Checker** (reviews) → **Approver** (approves/rejects)

Anyone who is logged in — maker, checker, approver, or admin — can create a memo.
The workflow actions (review, approve) are restricted to the assigned checker and
approver for that specific memo.

---

## Statuses
`draft` → `submitted` → `under_review` → `approved` / `rejected` · plus `cancelled`
and `returned` (checker sends a submitted memo back to the author as a draft).

## Where things live (sidebar → **Memos**)
- **Create Memo** — everyone
- **My Memos** — memos you created (tabs: All / Draft / Submitted / Approved / Rejected)
- **All Memos** — everything you're allowed to see
- **Pending Reviews** — checkers (and admins)
- **Pending Approvals** — approvers (and admins)

---

## By role

### Any user — create & submit
1. **Memos → Create Memo**.
2. Optionally **Load from template** (General Announcement, HR Notice, Meeting Minutes).
3. Fill **Title**, **Subject**, choose **Type** + **Priority**, write the **Body**
   (rich text), and optionally attach files.

   **Attachment limits.** Up to **10 files per upload** and **20 per memo**, each
   at most **10 MB**, with a total of **50 MB per upload**. Send a larger set in
   more than one batch. Accepted types: `pdf`, `doc`, `docx`, `xls`, `xlsx`,
   `csv`, `txt`, `ppt`, `pptx`, `png`, `jpg`, `jpeg`, `webp`, `zip`.

   Files are checked by their **contents**, not their name, so renaming a file
   to `.pdf` will not get it past the check. If an upload is refused, the
   message names the limit and what you sent.
4. **Assign Checker** — pick a specific checker, or leave **Auto-assign by
   department**. (The approver is chosen by the checker during review.)
5. **Save as Draft** (edit later) or **Submit for Review**. A success dialog shows
   the generated **memo number** (e.g. `NIFN-GEN-2026-0042`).
6. As the author you can **Cancel** a draft, and **Duplicate** a rejected memo into
   a fresh draft.

### Checker — review
1. **Memos → Pending Reviews** shows memos assigned to you (auto-refreshes every 30s).
2. Open a memo → **Review → approver**: add a comment and **assign the approver**
   (specific person or auto). This advances the memo to *under review*.
3. Alternatively **Return to author** (with a comment ≥ 10 chars) or **Reject**
   (comment required).
4. Bulk-reject is available from the queue via multi-select.

### Approver — approve
1. **Memos → Pending Approvals** shows memos assigned to you.
2. Open → **Approve** (optional comment) or **Reject** (comment required).
3. Bulk approve/reject from the queue.

### Admin
Admins can perform any workflow action, see all memos, and manage the memo
**templates** from the admin panel (`MemoTemplate`).

---

## Approved memos → PDF
Once a memo is **approved**, a **Download PDF** button appears on its detail page.
The PDF carries the letterhead, approval trail, signatures, and a QR code that
links to the public verification page (`/api/v1/verify/<document_number>/`).

## Notifications
Every transition notifies the relevant person (in-app bell + email, subject to each
user's notification preferences):
- **Submitted** → the assigned checker
- **Reviewed** → the assigned approver (and the author)
- **Approved / Rejected / Returned** → the author

## The approval trail
Each memo records an immutable, ordered timeline of every action (who, what, when,
and any comment), shown on the memo detail page and mirrored in the audit log.
