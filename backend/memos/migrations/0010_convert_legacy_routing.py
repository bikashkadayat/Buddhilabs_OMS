"""
Convert every legacy-routed memo into MemoWorkflowStep rows, so migration 0010
can drop Memo.current_reviewer / Memo.current_approver without stranding a memo
mid-approval.

WHY THIS RUNS BEFORE THE COLUMNS GO
-----------------------------------
The old engine expressed routing as two scalar columns, which is why it could
only ever have two approvers. The matrix engine expresses it as rows. Dropping
the columns without converting first would leave any in-flight memo unroutable
and invisible to every queue - so the conversion happens here, in its own
migration, and 0010 only removes what is by then unused.

MAPPING
-------
  draft / cancelled   nothing to convert - no routing was active. The author
                      builds a matrix when they next send it for review.
  submitted           the checker held it and the approver had not been chosen
                      yet (the old engine picked one at review time). Becomes
                      [1 reviewer ACTIVE, 2 approver PENDING], with the approver
                      resolved by the same rule the old engine used. Status
                      normalises to draft_for_review.
  under_review        the checker had already acted. Becomes
                      [1 reviewer COMPLETED, 2 approver ACTIVE].
  approved            a completed chain, recorded for the audit trail. The status
                      is deliberately NOT changed to archived: auto-archiving is
                      the new rule for new approvals, and retroactively filing
                      historical memos would assert something that never happened.
  rejected            the chain up to the rejection, with the last step REJECTED.

WHEN A MEMO CANNOT BE CONVERTED
------------------------------
Three cases end with the memo returned to DRAFT, its routing cleared, and the
count printed:

  * `submitted` with no reviewer recorded;
  * `submitted` with no active approver available anywhere to complete the chain;
  * `submitted` where the same person held both slots - a single approver step
    would mean their next action approved the memo outright, skipping the
    approval gate the old engine would still have applied afterwards;
  * `under_review` with no approver recorded, which is unroutable either way.

Returning them is deliberate. A memo the author must re-send is recoverable and
its history is intact; a one-step chain would be treated by the engine as final,
and the very next action on it would silently approve the memo.

The approver-resolution rule is inlined rather than imported from
memos.services, because a migration must keep behaving the same way after the
application code around it changes.
"""
from django.db import migrations

# Mirrors the roles the old engine treated as final approvers, and its
# escalation rule for financial / urgent memos.
ROLE_APPROVER = "approver"
ROLE_ADMIN = "admin"
ESCALATION_TYPES = {"financial"}
ESCALATION_PRIORITIES = {"urgent"}


def _resolve_approver(User, memo):
    """The approver the old engine would have picked, or None."""
    escalate = (
        memo.memo_type in ESCALATION_TYPES
        or memo.priority in ESCALATION_PRIORITIES
    )
    if escalate:
        senior = (User.objects.filter(role=ROLE_ADMIN, is_active=True)
                  .order_by("date_joined", "id").first())
        if senior is not None:
            return senior

    approvers = (User.objects.filter(role=ROLE_APPROVER, is_active=True)
                 .order_by("date_joined", "id"))
    author_dept = getattr(memo.created_by, "department", None)
    if author_dept:
        in_dept = approvers.filter(department=author_dept).first()
        if in_dept is not None:
            return in_dept
    return approvers.first()


def _full_name(user):
    """
    The user's display name, composed from the fields directly.

    Deliberately not `user.get_full_name()`: `apps.get_model()` returns a
    HISTORICAL model, rebuilt from migration state with only its fields - none of
    AbstractUser's methods come with it, so calling one raises AttributeError at
    migrate time. This is the same reason the approver-resolution rule below is
    inlined rather than imported.
    """
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or user.username


def _row(Step, memo, sequence, user, role_type, status, acted_at=None):
    return Step(
        memo=memo,
        sequence=sequence,
        assignee=user,
        role_type=role_type,
        status=status,
        # Snapshot the same way the live engine does, so a converted matrix reads
        # identically to one built through the UI - and survives the account being
        # deleted later.
        assignee_name=_full_name(user),
        designation=getattr(user, "designation", "") or "",
        department_label=(getattr(user, "department", "") or ""),
        activated_at=acted_at,
        acted_at=acted_at,
    )


def _strand(memo):
    """
    Return a memo that cannot be converted to DRAFT, with its routing cleared.

    Every unconvertible case must go through here. Leaving the memo on its old
    status would be worse than resetting it: `submitted` stops being a valid
    status once 0011 runs, and a memo with no matrix has nothing to route it - so
    it would sit invisible to every menu and actionable by nobody. Back in the
    author's drafts it is at least recoverable, and the history rows and audit log
    still record everything that happened to it.
    """
    memo.status = "draft"
    memo.current_reviewer = None
    memo.current_approver = None
    memo.submitted_at = None
    memo.finalized_at = None
    memo.save(update_fields=[
        "status", "current_reviewer", "current_approver",
        "submitted_at", "finalized_at",
    ])


def convert(apps, schema_editor):
    Memo = apps.get_model("memos", "Memo")
    Step = apps.get_model("memos", "MemoWorkflowStep")
    User = apps.get_model("users", "User")

    converted = stranded = 0

    candidates = (
        Memo.objects.exclude(status__in=["draft", "cancelled"])
        .filter(workflow_steps__isnull=True)
        .select_related("created_by")
    )

    for memo in candidates.iterator(chunk_size=200):
        reviewer = memo.current_reviewer
        approver = memo.current_approver
        # One person cannot hold two positions - the matrix has a unique
        # constraint on (memo, assignee) - so a memo whose reviewer and approver
        # are the same user collapses to the approver step alone.
        if reviewer is not None and approver is not None and reviewer.pk == approver.pk:
            reviewer = None

        acted = memo.submitted_at or memo.created_at
        rows = []

        if memo.status == "submitted":
            if reviewer is None:
                _strand(memo)
                stranded += 1
                continue
            approver = approver or _resolve_approver(User, memo)
            if approver is None or approver.pk == reviewer.pk:
                # No distinct approver available: return it to the author rather
                # than build a chain whose first action would approve the memo.
                _strand(memo)
                stranded += 1
                continue
            rows.append(_row(Step, memo, 1, reviewer, "reviewer", "active", acted))
            rows.append(_row(Step, memo, 2, approver, "approver", "pending"))
            memo.status = "draft_for_review"
            memo.save(update_fields=["status"])

        elif memo.status == "under_review":
            sequence = 1
            if reviewer is not None:
                rows.append(_row(Step, memo, sequence, reviewer, "reviewer", "completed", acted))
                sequence += 1
            if approver is None:
                # Under review with nobody to approve it: unroutable either way.
                _strand(memo)
                stranded += 1
                continue
            rows.append(_row(Step, memo, sequence, approver, "approver", "active", acted))

        elif memo.status in ("approved", "archived"):
            sequence = 1
            if reviewer is not None:
                rows.append(_row(Step, memo, sequence, reviewer, "reviewer", "completed", acted))
                sequence += 1
            if approver is not None:
                rows.append(_row(
                    Step, memo, sequence, approver, "approver", "completed",
                    memo.finalized_at or acted,
                ))
            if not rows:
                continue
            # Backfill approved_at for rows that predate the column.
            if memo.approved_at is None:
                memo.approved_at = memo.finalized_at or acted
                memo.save(update_fields=["approved_at"])

        elif memo.status == "rejected":
            sequence = 1
            if reviewer is not None:
                # A memo rejected at review has no completed reviewer step; one
                # rejected while under review does. finalized_at tells them apart.
                reviewer_status = "rejected" if approver is None else "completed"
                rows.append(_row(
                    Step, memo, sequence, reviewer, "reviewer", reviewer_status,
                    memo.finalized_at or acted,
                ))
                sequence += 1
            if approver is not None:
                rows.append(_row(
                    Step, memo, sequence, approver, "approver", "rejected",
                    memo.finalized_at or acted,
                ))
            if not rows:
                continue

        if rows:
            Step.objects.bulk_create(rows)
            converted += 1

    if converted or stranded:
        print(
            f"\n  memos.0010: converted {converted} legacy-routed memo(s) to "
            f"workflow steps; {stranded} returned to draft for re-routing."
        )


def unconvert(apps, schema_editor):
    """
    Reverse by removing generated steps and restoring the legacy status name.

    Rebuilding the two routing columns exactly is not possible in general - the
    matrix carries more information than they could hold - but the columns still
    exist at this point in the graph, and the active step is enough to restore a
    usable value for the two in-flight states.
    """
    Memo = apps.get_model("memos", "Memo")
    Step = apps.get_model("memos", "MemoWorkflowStep")

    for memo in Memo.objects.filter(status="draft_for_review").iterator(chunk_size=200):
        active = memo.workflow_steps.filter(status="active").order_by("sequence").first()
        if active is not None:
            memo.status = "submitted"
            memo.current_reviewer_id = active.assignee_id
            memo.save(update_fields=["status", "current_reviewer"])

    Step.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("memos", "0009_memoworkflowstep_activated_at"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(convert, unconvert),
    ]
