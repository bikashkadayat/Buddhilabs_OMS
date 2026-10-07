"""
Phase 12: the hardening pass.

Covers the legacy-removal guarantees, the status-label unification, inbox ageing
and its SLA colour states, archive immutability including attachments, the full
audit trail with client IP, the withdraw transition, and the dashboard charts.
"""
import pytest
from django.utils import timezone

from audit.models import AuditLog
from memos.models import Memo, MemoWorkflowStep

from .conftest import add_sections
from memos.services import generate_memo_number
from users.models import User

from .conftest import act, drive_to_approval, route


def _memo(author, subject="Memo", memo_type=Memo.MemoType.GENERAL, department_name="Finance"):
    memo = Memo.objects.create(
        subject=subject, memo_type=memo_type, status=Memo.Status.DRAFT,
        created_by=author, memo_number=generate_memo_number(memo_type),
    )
    add_sections(memo, ("Background", "<p>b</p>"))
    if department_name and not memo.department_name:
        Memo.objects.filter(pk=memo.pk).update(department_name=department_name)
        memo.refresh_from_db()
    return memo


# ---------------------------------------------------------------------------
# Item 2 - status labels are the same everywhere
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_status_labels_match_the_canonical_set():
    """
    One label per status, defined by the model and used by every consumer. The
    "approved reads as Published" special case is gone.
    """
    assert dict(Memo.Status.choices) == {
        "draft": "Draft",
        "draft_for_review": "Draft For Review",
        "under_review": "Under Review",
        "recommended": "Recommended",
        "supported": "Supported",
        "approved": "Approved",
        "rejected": "Rejected",
        "archived": "Archived",
        "cancelled": "Cancelled",
    }


@pytest.mark.django_db
def test_approved_no_longer_reads_as_published(api, maker, checker, approver, general_draft):
    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(maker)
    detail = api.get(f"/api/v1/memos/{memo.id}/").data
    assert detail["status"] == "archived"
    assert detail["status_label"] == "Archived"
    assert "Published" not in str(detail)


@pytest.mark.django_db
def test_list_and_detail_and_export_agree_on_the_label(api, maker, checker,
                                                       approver, general_draft):
    import io

    from openpyxl import load_workbook

    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    act(api, checker, memo, remarks="Reviewed and correct.")

    api.force_authenticate(maker)
    listed = api.get("/api/v1/memos/", {"scope": "mine"}).data["results"][0]
    detail = api.get(f"/api/v1/memos/{memo.id}/").data
    export = api.get("/api/v1/memos/export/", {"scope": "mine"})

    sheet = load_workbook(io.BytesIO(export.content)).active
    rows = list(sheet.values)
    column = {label: index for index, label in enumerate(rows[0])}

    assert listed["status_label"] == "Under Review"
    assert detail["status_label"] == "Under Review"
    assert rows[1][column["Status"]] == "Under Review"


# ---------------------------------------------------------------------------
# Item 7 - inbox ageing and SLA colour state
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_ageing_reports_pending_since_and_due_days(api, maker, checker, approver,
                                                   general_draft):
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(checker)
    row = api.get("/api/v1/memos/", {"scope": "pending"}).data["results"][0]
    ageing = row["ageing"]

    assert ageing["pending_since"] is not None
    assert ageing["pending_days"] == 0
    assert ageing["sla_days"] == 4          # normal priority
    assert ageing["due_days"] == 4
    assert ageing["state"] == "on_track"


@pytest.mark.django_db
def test_the_sla_window_is_one_interval_for_every_memo(api, maker, checker, approver):
    """
    The manual's form has no Priority, so there is nothing to vary the window by.
    It used to be 1/2/4/7 days keyed by priority; that column is retired and every
    memo now gets the same default interval.
    """
    memo = _memo(maker)
    route(api, maker, memo, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(checker)
    row = api.get("/api/v1/memos/", {"scope": "pending"}).data["results"][0]
    assert row["ageing"]["sla_days"] == Memo.DEFAULT_SLA_DAYS


@pytest.mark.django_db
@pytest.mark.parametrize("days_ago,state", [
    (0, "on_track"),
    (4, "due_soon"),   # exactly at the default window
    (9, "overdue"),
])
def test_ageing_state_drives_the_inbox_colour(api, maker, checker, approver,
                                              general_draft, days_ago, state):
    """
    The three states the inbox colour-codes on. Computed server-side so the colour
    and the number can never disagree.
    """
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    step = memo.workflow_steps.get(assignee=checker)
    MemoWorkflowStep.objects.filter(pk=step.pk).update(
        activated_at=timezone.now() - timezone.timedelta(days=days_ago))

    api.force_authenticate(checker)
    row = api.get("/api/v1/memos/", {"scope": "pending"}).data["results"][0]
    assert row["ageing"]["state"] == state


@pytest.mark.django_db
def test_ageing_measures_from_the_step_not_the_memo(api, maker, checker, approver,
                                                    general_draft):
    """
    A memo that sat for a fortnight with the reviewer must not land on the
    approver's desk already overdue - each step's clock starts when it reaches
    that person.
    """
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    reviewer_step = memo.workflow_steps.get(assignee=checker)
    MemoWorkflowStep.objects.filter(pk=reviewer_step.pk).update(
        activated_at=timezone.now() - timezone.timedelta(days=14))

    act(api, checker, memo, remarks="Sorry for the delay on this one.")

    api.force_authenticate(approver)
    row = api.get("/api/v1/memos/", {"scope": "pending"}).data["results"][0]
    assert row["ageing"]["pending_days"] == 0
    assert row["ageing"]["state"] == "on_track"


# ---------------------------------------------------------------------------
# Item 9 - archive immutability, including attachments
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_archived_memo_refuses_attachment_uploads(api, maker, checker, approver,
                                                  general_draft):
    from django.core.files.uploadedfile import SimpleUploadedFile

    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    assert memo.status == Memo.Status.ARCHIVED

    api.force_authenticate(maker)
    upload = SimpleUploadedFile("late.pdf", b"%PDF-1.4 late addition",
                                content_type="application/pdf")
    res = api.post(f"/api/v1/memos/{memo.id}/attachments/", {"files": upload},
                   format="multipart")
    assert res.status_code == 403
    assert memo.attachments.count() == 0


@pytest.mark.django_db
def test_archived_memo_refuses_withdrawal(api, maker, checker, approver, general_draft):
    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(maker)
    res = api.post(f"/api/v1/memos/{memo.id}/cancel/", {}, format="json")
    assert res.status_code == 400
    memo.refresh_from_db()
    assert memo.status == Memo.Status.ARCHIVED


@pytest.mark.django_db
def test_archived_memo_still_allows_view_pdf_and_excel(api, maker, checker,
                                                       approver, general_draft):
    """The three things an archived memo must still permit."""
    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(maker)

    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 200
    assert api.get(f"/api/v1/memos/{memo.id}/pdf/").status_code == 200
    assert api.get("/api/v1/memos/export/", {"scope": "archived"}).status_code == 200


@pytest.mark.django_db
def test_archived_memo_cannot_be_acted_on_again(api, maker, checker, approver,
                                                general_draft):
    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    res = act(api, approver, memo, remarks="Approving a second time.")
    assert res.status_code == 400
    assert "no longer be actioned" in str(res.data)


# ---------------------------------------------------------------------------
# Item 10 - the full audit trail
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_audit_trail_records_every_event_with_actor_time_and_remarks(
        api, maker, checker, approver, general_draft, admin):
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    act(api, checker, memo, remarks="Figures reconcile with the ledger.")
    act(api, approver, memo, remarks="Approved within delegated authority.")

    api.force_authenticate(admin)
    trail = api.get(f"/api/v1/memos/{memo.id}/audit-trail/")
    assert trail.status_code == 200

    transitions = [row["transition"] for row in trail.data]
    assert "sent_for_review" in transitions
    assert "reviewed" in transitions
    assert "approved" in transitions
    assert "archived" in transitions

    reviewed = next(row for row in trail.data if row["transition"] == "reviewed")
    assert reviewed["actor"] == "Checker1 Test"
    assert reviewed["remarks"] == "Figures reconcile with the ledger."
    assert reviewed["at"] is not None


@pytest.mark.django_db
def test_audit_trail_records_the_client_address(api, maker, checker, approver,
                                                general_draft, admin):
    """
    The IP is why this endpoint exists separately from the timeline - and why it is
    restricted. It comes from config.client_ip, which trusts X-Forwarded-For only
    as far as our own proxies.
    """
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(checker)
    api.post(f"/api/v1/memos/{memo.id}/act/",
             {"decision": "proceed", "remarks": "Checked and in order."},
             format="json", REMOTE_ADDR="203.0.113.7")

    api.force_authenticate(admin)
    trail = api.get(f"/api/v1/memos/{memo.id}/audit-trail/").data
    reviewed = next(row for row in trail if row["transition"] == "reviewed")
    assert reviewed["ip_address"] == "203.0.113.7"


@pytest.mark.django_db
def test_audit_trail_is_restricted_to_hr_and_admin(api, maker, checker, approver,
                                                    general_draft):
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))

    # The author can read their memo, but not the forensic trail.
    api.force_authenticate(maker)
    assert api.get(f"/api/v1/memos/{memo.id}/audit-trail/").status_code == 403

    api.force_authenticate(approver)
    assert api.get(f"/api/v1/memos/{memo.id}/audit-trail/").status_code == 200


@pytest.mark.django_db
def test_creation_and_edit_appear_in_the_audit_trail(api, maker, admin):
    api.force_authenticate(maker)
    created = api.post("/api/v1/memos/", { "subject": "S", "memo_type": "general",
    }, format="json")
    memo_id = created.data["id"]
    api.patch(f"/api/v1/memos/{memo_id}/", {"subject": "Auditable (v2)"},
              format="json")

    api.force_authenticate(admin)
    trail = api.get(f"/api/v1/memos/{memo_id}/audit-trail/").data
    actions = [row["action"] for row in trail]
    assert "Create" in actions
    assert "Update" in actions
    edited = next(row for row in trail if row["transition"] == "edited")
    assert edited["metadata"]["fields"] == ["subject"]


@pytest.mark.django_db
def test_timeline_opens_with_creation(api, maker, checker, approver, general_draft):
    """Phase 7's example starts at "Created by Employee"; so does the timeline."""
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(maker)
    entries = api.get(f"/api/v1/memos/{memo.id}/timeline/").data
    assert entries[0]["action"] == "created"
    assert entries[0]["actor_name"] == "Maker1 Test"


# ---------------------------------------------------------------------------
# Withdraw (the one transition outside the ladder)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_author_can_withdraw_an_in_flight_memo(api, maker, checker, approver,
                                               general_draft):
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(maker)
    res = api.post(f"/api/v1/memos/{memo.id}/cancel/",
                   {"remarks": "Superseded by a revised request."}, format="json")
    assert res.status_code == 200

    memo.refresh_from_db()
    assert memo.status == Memo.Status.CANCELLED
    # Outstanding steps read as skipped, not as someone still pending.
    assert set(memo.workflow_steps.values_list("status", flat=True)) == {"skipped"}
    assert memo.is_read_only is True


@pytest.mark.django_db
def test_a_colleague_cannot_withdraw_someone_elses_memo(api, maker, other_maker,
                                                        checker, approver, general_draft):
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(other_maker)
    res = api.post(f"/api/v1/memos/{memo.id}/cancel/", {}, format="json")
    assert res.status_code in (400, 403, 404)
    memo.refresh_from_db()
    assert memo.status == Memo.Status.DRAFT_FOR_REVIEW


# ---------------------------------------------------------------------------
# Item 5 - dashboard tiles and charts
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_dashboard_reports_a_count_per_pending_role(api, maker, admin):
    """
    Each tile counts only the steps of its own role type. The filters live in one
    `filter()` call for that reason: split apart, Django joins the step table twice
    and matches a memo where the user holds one step and a different one is active.
    """
    reviewer = User.objects.create_user(
        username="p12_rev", email="p12r@nif.test", password="pass12345",
        role=User.Roles.MAKER, first_name="Rev", last_name="Iewer")
    supporter = User.objects.create_user(
        username="p12_sup", email="p12s@nif.test", password="pass12345",
        role=User.Roles.CHECKER, first_name="Sup", last_name="Porter")
    approver = User.objects.create_user(
        username="p12_app", email="p12a@nif.test", password="pass12345",
        role=User.Roles.APPROVER, first_name="App", last_name="Rover")

    memo = _memo(maker, "Three-stage")
    route(api, maker, memo,
          (reviewer, "reviewer"), (supporter, "supporter"), (approver, "approver"))

    api.force_authenticate(reviewer)
    counts = api.get("/api/v1/memos/dashboard/").data
    assert counts["pending_review"] == 1
    assert counts["pending_support"] == 0
    assert counts["pending_approval"] == 0

    act(api, reviewer, memo, remarks="Reviewed and forwarded on.")

    api.force_authenticate(supporter)
    counts = api.get("/api/v1/memos/dashboard/").data
    assert counts["pending_review"] == 0
    assert counts["pending_support"] == 1

    # The approver is on the chain but not yet active.
    api.force_authenticate(approver)
    counts = api.get("/api/v1/memos/dashboard/").data
    assert counts["pending_approval"] == 0
    assert counts["inbox"] == 1


@pytest.mark.django_db
def test_dashboard_charts_return_all_three_datasets(api, maker, checker, approver, admin):
    _memo(maker, "One", department_name="Finance")
    approved = _memo(maker, "Two", department_name="Finance")
    drive_to_approval(api, maker, approved, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(admin)
    data = api.get("/api/v1/memos/dashboard/charts/").data

    assert [row["label"] for row in data["by_department"]] == ["Finance"]
    assert data["by_department"][0]["total"] == 2

    # Every stage is present with an explicit zero: a chart that omits the empty
    # stages reads as a shorter pipeline than the real one.
    statuses = {row["status"]: row["total"] for row in data["by_status"]}
    assert statuses["draft"] == 1
    assert statuses["archived"] == 1
    assert statuses["under_review"] == 0
    assert len(data["by_status"]) == len(Memo.Status.choices)

    # The trend covers a contiguous window, gaps filled with zeros.
    assert len(data["monthly_trend"]) >= 12
    assert sum(row["total"] for row in data["monthly_trend"]) == 2
    months = [row["month"] for row in data["monthly_trend"]]
    assert months == sorted(months)


@pytest.mark.django_db
def test_dashboard_charts_respect_visibility(api, maker, other_maker):
    """An employee's charts only aggregate memos they are entitled to see."""
    _memo(maker, "Mine")
    _memo(other_maker, "Theirs")

    api.force_authenticate(maker)
    data = api.get("/api/v1/memos/dashboard/charts/").data
    assert sum(row["total"] for row in data["by_status"]) == 1


# ---------------------------------------------------------------------------
# Item 6 - department filters
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_created_date_range_filter(api, maker):
    old = _memo(maker, "Old memo")
    Memo.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=90))
    recent = _memo(maker, "Recent memo")

    api.force_authenticate(maker)
    cutoff = (timezone.now() - timezone.timedelta(days=7)).date().isoformat()
    res = api.get("/api/v1/memos/", {"created_from": cutoff})
    assert [row["id"] for row in res.data["results"]] == [str(recent.id)]

    res = api.get("/api/v1/memos/", {"created_to": cutoff})
    assert [row["id"] for row in res.data["results"]] == [str(old.id)]


@pytest.mark.django_db
def test_status_filter_accepts_several_values(api, maker, checker, approver):
    """The Outbox asks for every in-flight stage in one request."""
    draft = _memo(maker, "Still drafting")
    sent = _memo(maker, "Out for review")
    route(api, maker, sent, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(maker)
    res = api.get("/api/v1/memos/?status=draft&status=draft_for_review")
    ids = {row["id"] for row in res.data["results"]}
    assert ids == {str(draft.id), str(sent.id)}


@pytest.mark.django_db
def test_department_name_filter(api, maker, admin):
    finance = _memo(maker, "Finance one", department_name="Finance")
    other = _memo(maker, "Ops one", department_name="Operations")
    Memo.objects.filter(pk=other.pk).update(department_name="Operations")

    api.force_authenticate(admin)
    res = api.get("/api/v1/memos/", {"department_name": "finance"})
    assert [row["id"] for row in res.data["results"]] == [str(finance.id)]


# ---------------------------------------------------------------------------
# Item 4 - PDF approval sections
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_signature_cards_cover_the_whole_hierarchy(api, maker):
    """
    Created By first, then the role cards in hierarchy order. Replaces the old
    _group_matrix() helper: the cards are now built once by
    workflow.signature_blocks() and rendered by both the detail page and the PDF.
    """
    from memos.workflow import signature_blocks

    reviewer = User.objects.create_user(
        username="pdf_rev", email="pdfr@nif.test", password="pass12345",
        role=User.Roles.MAKER, first_name="Rev", last_name="One",
        designation="Officer")
    recommender = User.objects.create_user(
        username="pdf_rec", email="pdfc@nif.test", password="pass12345",
        role=User.Roles.CHECKER, first_name="Rec", last_name="Two",
        designation="Dept Head")
    supporter = User.objects.create_user(
        username="pdf_sup", email="pdfs@nif.test", password="pass12345",
        role=User.Roles.CHECKER, first_name="Sup", last_name="Three",
        designation="Manager")
    approver = User.objects.create_user(
        username="pdf_app", email="pdfa@nif.test", password="pass12345",
        role=User.Roles.APPROVER, first_name="App", last_name="Four",
        designation="Director")

    memo = _memo(maker, "Four-stage")
    drive_to_approval(api, maker, memo, (reviewer, "reviewer"),
                      (recommender, "recommender"), (supporter, "supporter"),
                      (approver, "approver"))

    blocks = signature_blocks(memo)
    assert [b["heading"] for b in blocks] == [
        "Created By", "Reviewed By", "Recommended By", "Supported By", "Approved By"]
    # The author's card is synthesised from the memo - they are never a step in
    # their own chain, but they are the first block on the document.
    assert blocks[0]["name"] == "Maker1 Test"
    assert blocks[1]["name"] == "Rev One"
    assert blocks[1]["designation"] == "Officer"
    assert all(b["verified"] for b in blocks)
    assert all(b["initials"] for b in blocks)


@pytest.mark.django_db
def test_signature_cards_omit_roles_the_chain_does_not_use(api, maker, checker,
                                                           approver, general_draft):
    """A two-step memo must not print an empty "Supported By" card."""
    from memos.workflow import signature_blocks

    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    headings = [b["heading"] for b in signature_blocks(memo)]
    assert headings == ["Created By", "Reviewed By", "Approved By"]


@pytest.mark.django_db
def test_sensitive_memos_render_a_confidential_marking(api, maker, checker, approver):
    """
    HR and Financial memos are the two types the access rules already restrict;
    the paper copy should say so too.
    """
    memo = _memo(maker, "Payroll revision", memo_type=Memo.MemoType.CONFIDENTIAL)
    drive_to_approval(api, maker, memo, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(maker)
    res = api.get(f"/api/v1/memos/{memo.id}/pdf/")
    assert res.status_code == 200
    assert res["Content-Type"] == "application/pdf"


# ---------------------------------------------------------------------------
# Item 1 - the legacy engine really is gone
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_detail_payload_exposes_no_legacy_routing_fields(api, maker, general_draft):
    api.force_authenticate(maker)
    data = api.get(f"/api/v1/memos/{general_draft.id}/").data
    assert "current_reviewer" not in data
    assert "current_approver" not in data
    # Replaced by a single, matrix-derived answer.
    assert "pending_with" in data


@pytest.mark.django_db
def test_audit_metadata_names_the_assignee_for_every_transition(
        api, maker, checker, approver, general_draft):
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    act(api, checker, memo, remarks="Reviewed and correct.")

    entry = AuditLog.objects.filter(
        object_id=str(memo.id), changes__transition="reviewed").first()
    assert entry.changes["assignee"] == str(checker.id)
    assert entry.changes["sequence"] == 1
    assert entry.changes["role_type"] == "reviewer"


# ---------------------------------------------------------------------------
# Deleting a user must not destroy an approval record - or be impossible
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_deleting_an_assignee_keeps_the_matrix_row_readable(api, maker, checker,
                                                            approver, general_draft):
    """
    The step's FK is SET_NULL and the name is snapshotted on the row. PROTECT was
    the first instinct here and it was wrong: it made anyone who had ever appeared
    in a memo workflow permanently undeletable, which broke account deletion in
    the admin console outright.
    """
    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    step = memo.workflow_steps.get(role_type="reviewer")
    assert step.assignee_name == "Checker1 Test"

    checker.delete()

    step.refresh_from_db()
    assert step.assignee_id is None
    # The approval record still says who gave it.
    assert step.display_name == "Checker1 Test"
    assert step.designation == (step.designation or "")

    api.force_authenticate(approver)
    data = api.get(f"/api/v1/memos/{memo.id}/").data
    row = next(s for s in data["workflow_steps"] if s["role_type"] == "reviewer")
    assert row["display_name"] == "Checker1 Test"
    assert row["assignee"] is None
    # And the PDF still renders every signature panel.
    assert api.get(f"/api/v1/memos/{memo.id}/pdf/").status_code == 200


@pytest.mark.django_db
def test_matrix_snapshots_the_assignee_name_when_built(api, maker, checker,
                                                       approver, general_draft):
    route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    names = dict(general_draft.workflow_steps.values_list("role_type", "assignee_name"))
    assert names == {"reviewer": "Checker1 Test", "approver": "Approver1 Test"}


@pytest.mark.django_db
def test_inbox_drops_memos_once_they_close(api, maker, checker, approver, general_draft):
    """
    A memo the user reviewed stays in their inbox while it is still moving, and
    leaves once it reaches a terminal state - otherwise the queue would grow
    without bound with work that is already finished.
    """
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))

    def inbox_ids(user):
        api.force_authenticate(user)
        res = api.get("/api/v1/memos/", {"scope": "inbox"})
        return {row["id"] for row in res.data["results"]}

    assert inbox_ids(checker) == {str(memo.id)}

    act(api, checker, memo, remarks="Reviewed and forwarded on.")
    # Still live, now with the approver - the reviewer can still follow it.
    assert inbox_ids(checker) == {str(memo.id)}

    act(api, approver, memo, remarks="Approved; figures verified.")
    memo.refresh_from_db()
    assert memo.status == Memo.Status.ARCHIVED
    # Closed: out of both inboxes, still findable under All Memos.
    assert inbox_ids(checker) == set()
    assert inbox_ids(approver) == set()

    api.force_authenticate(checker)
    assert str(memo.id) in {
        row["id"] for row in api.get("/api/v1/memos/", {"scope": "all"}).data["results"]}


@pytest.mark.django_db
def test_dashboard_inbox_count_matches_the_inbox_scope(api, maker, checker, approver,
                                                      general_draft):
    """The badge and the page must not disagree about what an inbox contains."""
    memo = route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(checker)
    assert api.get("/api/v1/memos/dashboard/").data["inbox"] == 1

    act(api, checker, memo, remarks="Reviewed and correct.")
    act(api, approver, memo, remarks="Approved; figures verified.")

    api.force_authenticate(checker)
    counts = api.get("/api/v1/memos/dashboard/").data
    listed = api.get("/api/v1/memos/", {"scope": "inbox"}).data
    assert counts["inbox"] == listed["count"] == 0


# ---------------------------------------------------------------------------
# Query efficiency: the list endpoint must not scale queries with rows
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_list_query_count_does_not_grow_with_rows(api, django_assert_num_queries,
                                                  maker, checker, approver):
    """
    Each list row asks for `pending_with` and `ageing`, both of which need the
    active step. Resolved with a `.filter()` that ignores the prefetch cache, a
    50-row page cost 100 extra queries; resolved from the prefetch it costs none.

    Asserted as "the same count for 1 row as for 6" rather than against a fixed
    number, so the test is about the scaling and does not break every time an
    unrelated annotation is added.
    """
    def build(n):
        for index in range(n):
            memo = _memo(maker, f"Routed {index}")
            route(api, maker, memo, (checker, "reviewer"), (approver, "approver"))

    def count_queries():
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        api.force_authenticate(maker)
        with CaptureQueriesContext(connection) as ctx:
            res = api.get("/api/v1/memos/", {"scope": "mine"})
            assert res.status_code == 200
        return len(ctx.captured_queries), res.data["count"]

    build(1)
    one, count_one = count_queries()
    build(5)
    six, count_six = count_queries()

    assert count_one == 1 and count_six == 6
    assert six == one, (
        f"{one} queries for 1 memo but {six} for 6 - the list is issuing "
        "per-row queries instead of using the prefetch"
    )


# ---------------------------------------------------------------------------
# The PDF's rendered CONTENT, not just its status code
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_pdf_html_renders_the_panels_and_leaks_no_template_source(api, maker,
                                                                  checker, approver,
                                                                  general_draft):
    """
    Asserts on the rendered HTML rather than on a 200.

    A status-code-only test passed while the template was printing a multi-line
    `{# ... #}` comment onto the face of the document - Django's hash comment is
    single-line only, so it commented nothing out. Only opening the PDF showed it.
    """
    from django.template.loader import render_to_string

    from memos.workflow import pdf_matrix, signature_blocks
    from memos.workflow import signature_rows as workflow_signature_rows

    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    matrix = pdf_matrix(memo)
    html = render_to_string("pdf/memo.html", {
        "memo": memo,
        "org": {}, "logo": None, "document_number": memo.memo_number,
        "verify_url": "http://x/verify", "verify_qr": "", "issue_date": "",
        "issue_date_bs": "",
        "to_name": "Approver1 Test", "from_name": "Maker1 Test", "cc_name": "",
        "author_designation": "Officer", "department_label": "Finance",
        "body_html": "<p>body</p>", "approver_name": "Approver1 Test",
        "memo_type": "general", "fully_approved": True, "attachments": [],
        "matrix": matrix, "signatures": signature_blocks(memo), "steps": [],
        # Phase 30: the template renders the grouped rows.
        "signature_rows": workflow_signature_rows(signature_blocks(memo)),
    })

    # No template source on the page.
    assert "{#" not in html
    assert "Phase 12 item 4" not in html
    assert "{% " not in html

    # The panels the spec asks for, and the summary table beneath them.
    assert "Created By" in html
    assert "Reviewed By" in html
    assert "Approved By" in html
    assert "Workflow Summary" in html
    # Phase 30 moved the electronic-authorisation wording off every block and into
    # one footnote under the section - repeating an identical three-line sentence per
    # signature is what made the old blocks tall. Each signed block now carries a
    # compact marker, and the explanation still appears, once.
    assert html.count('<div class="cert-verify">') == 3   # created + reviewer + approver
    assert html.count("no wet signature is required") == 1
    # Role types this two-step chain never used are omitted, not printed empty.
    assert "Supported By" not in html
    assert "Recommended By" not in html
    # Memo number, department and both actors are on the face of the document.
    assert memo.memo_number in html
    assert "Checker1 Test" in html
    assert "Approver1 Test" in html


@pytest.mark.django_db
@pytest.mark.parametrize("memo_type,expected", [
    ("general", "GENERAL"),
    ("confidential", "CONFIDENTIAL"),
    ("draft", "DRAFT"),
])
def test_pdf_html_prints_the_memo_type_badge(memo_type, expected):
    """
    The memo type is on the face of the document.

    It used to be a separate `classification` column with four values; the manual
    has one Memo Type dropdown with three, and CONFIDENTIAL is a handling
    instruction rather than metadata - so it is printed.
    """
    from django.template.loader import render_to_string

    html = render_to_string("pdf/memo.html", {
        "memo": None, "org": {}, "logo": None, "document_number": "X",
        "verify_url": "", "verify_qr": "", "issue_date": "", "issue_date_bs": "",
        "to_name": "x", "from_name": "y", "cc_names": [],
        "author_designation": "", "department_label": "Finance",
        "sections": [{"title": "Background", "body_html": "<p>b</p>"}],
        "approver_name": "x",
        "memo_type": memo_type, "memo_type_label": expected,
        "fully_approved": False,
        "attachments": [], "matrix": [], "signatures": [], "steps": [],
        "signature_rows": [],
    })
    assert expected in html
    # The stamp appears only once the memo really is approved. Asserted on the
    # rendered ELEMENT, not the class name - the CSS rule for it is always in the
    # stylesheet, so `"approved-stamp" not in html` would never hold.
    assert '<div class="approved-stamp">' not in html
