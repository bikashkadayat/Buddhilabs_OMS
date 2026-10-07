"""
The enterprise matrix workflow (Phases 2, 4, 5, 6, 7).

Draft -> Draft For Review -> Under Review -> Recommended -> Supported
      -> Approved -> Archived, with rejection returning the memo to the author.
"""
import pytest

from memos.services import MIN_COMMENT_LENGTH
from memos.models import Memo, MemoWorkflowStep
from memos.services import generate_memo_number
from users.models import User

RoleType = MemoWorkflowStep.RoleType
StepStatus = MemoWorkflowStep.StepStatus


def _user(username, role=User.Roles.MAKER, department="Finance", designation="Officer"):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role,
        department=department, designation=designation,
    )


@pytest.fixture
def chain(db):
    """An author plus one employee for each of the four role types."""
    return {
        "author": _user("wf_author"),
        "reviewer": _user("wf_reviewer", designation="Senior Officer"),
        "recommender": _user("wf_recommender", User.Roles.CHECKER, designation="Dept Head"),
        "supporter": _user("wf_supporter", User.Roles.CHECKER, designation="Manager"),
        "approver": _user("wf_approver", User.Roles.APPROVER, designation="Director"),
        "approver2": _user("wf_approver2", User.Roles.APPROVER, designation="CEO"),
    }


@pytest.fixture
def memo(db, chain):
    return Memo.objects.create(
        subject="Procurement approval", memo_type=Memo.MemoType.GENERAL,
        status=Memo.Status.DRAFT, created_by=chain["author"],
        memo_number=generate_memo_number(Memo.MemoType.GENERAL),
    )


def _matrix(chain):
    return [
        {"assignee_id": str(chain["reviewer"].id), "role_type": RoleType.REVIEWER},
        {"assignee_id": str(chain["recommender"].id), "role_type": RoleType.RECOMMENDER},
        {"assignee_id": str(chain["supporter"].id), "role_type": RoleType.SUPPORTER},
        {"assignee_id": str(chain["approver"].id), "role_type": RoleType.APPROVER},
    ]


def _act(api, actor, memo, decision="proceed",
         remarks="Looks good to me.".ljust(MIN_COMMENT_LENGTH, ".")):
    api.force_authenticate(actor)
    return api.post(f"/api/v1/memos/{memo.id}/act/",
                    {"decision": decision, "remarks": remarks}, format="json")


# ---------------------------------------------------------------------------
# Phase 4 - building the matrix
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_author_can_build_matrix_of_any_employees(api, chain, memo):
    """Phase 4: no role restriction - a plain employee may be the Reviewer."""
    api.force_authenticate(chain["author"])
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/",
                   {"workflow": _matrix(chain)}, format="json")
    assert res.status_code == 200, res.data

    steps = memo.workflow_steps.order_by("sequence")
    assert [s.sequence for s in steps] == [1, 2, 3, 4]
    assert [s.role_type for s in steps] == ["reviewer", "recommender", "supporter", "approver"]
    assert all(s.status == StepStatus.PENDING for s in steps)
    # Designation/department are snapshotted so the matrix survives a transfer.
    assert steps[0].designation == "Senior Officer"
    assert steps[0].department_label == "Finance"


@pytest.mark.django_db
def test_matrix_can_be_reordered_by_resubmitting_the_list(api, chain, memo):
    """
    Sequence is list order, so reordering is a plain re-POST (Phase 4).

    Reordered WITHIN a rank rather than across ranks: Phase 15 requires the role
    hierarchy to be non-decreasing, so swapping the reviewer and the recommender
    is now correctly refused (see the ordering tests below). Two supporters swap
    freely, which is the case reordering actually exists for.
    """
    api.force_authenticate(chain["author"])
    second_supporter = _user("wf_supporter2", User.Roles.CHECKER, designation="Deputy")
    rows = [
        {"assignee_id": str(chain["supporter"].id), "role_type": RoleType.SUPPORTER},
        {"assignee_id": str(second_supporter.id), "role_type": RoleType.SUPPORTER},
        {"assignee_id": str(chain["approver"].id), "role_type": RoleType.APPROVER},
    ]
    rows[0], rows[1] = rows[1], rows[0]
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/", {"workflow": rows}, format="json")
    assert res.status_code == 200, res.data
    assert [s.assignee_id for s in memo.workflow_steps.order_by("sequence")] == [
        second_supporter.id, chain["supporter"].id, chain["approver"].id,
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("mutate,expected", [
    # A chain that never reaches an approver could never become Approved.
    (lambda rows, c: rows[:1], "final step must be an Approver"),
    # An approver mid-chain would approve with steps still outstanding behind it.
    (lambda rows, c: [rows[3], rows[0],
                      {"assignee_id": str(c["approver2"].id), "role_type": RoleType.APPROVER}],
     "not the last step"),
    # One person holding two control points defeats separation of duties.
    (lambda rows, c: [rows[0], rows[0], rows[3]], "more than once"),
    (lambda rows, c: [], "at least one approver"),
])
def test_invalid_matrix_is_rejected(api, chain, memo, mutate, expected):
    api.force_authenticate(chain["author"])
    rows = mutate(_matrix(chain), chain)
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/", {"workflow": rows}, format="json")
    assert res.status_code == 400
    assert expected.lower() in str(res.data).lower()
    assert not memo.workflow_steps.exists()


@pytest.mark.django_db
def test_author_cannot_put_themselves_in_their_own_matrix(api, chain, memo):
    api.force_authenticate(chain["author"])
    rows = _matrix(chain)
    rows[0]["assignee_id"] = str(chain["author"].id)
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/", {"workflow": rows}, format="json")
    assert res.status_code == 400
    assert "your own approval workflow" in str(res.data)


@pytest.mark.django_db
def test_matrix_locked_once_workflow_is_live(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/",
                   {"workflow": _matrix(chain)}, format="json")
    assert res.status_code == 400
    assert "only be changed while the memo is a draft" in str(res.data)


# ---------------------------------------------------------------------------
# Phase 2 / 5 / 6 - the full chain
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_full_workflow_walks_every_status_and_auto_archives(api, chain, memo):
    api.force_authenticate(chain["author"])
    res = api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
                   {"workflow": _matrix(chain)}, format="json")
    assert res.status_code == 200, res.data
    memo.refresh_from_db()
    assert memo.status == Memo.Status.DRAFT_FOR_REVIEW
    assert memo.workflow_steps.get(sequence=1).status == StepStatus.ACTIVE

    assert _act(api, chain["reviewer"], memo).status_code == 200
    memo.refresh_from_db()
    assert memo.status == Memo.Status.UNDER_REVIEW
    assert memo.workflow_steps.get(sequence=2).status == StepStatus.ACTIVE

    assert _act(api, chain["recommender"], memo).status_code == 200
    memo.refresh_from_db()
    assert memo.status == Memo.Status.RECOMMENDED

    assert _act(api, chain["supporter"], memo).status_code == 200
    memo.refresh_from_db()
    assert memo.status == Memo.Status.SUPPORTED

    assert _act(api, chain["approver"], memo).status_code == 200
    memo.refresh_from_db()
    # Phase 6: approval archives immediately.
    assert memo.status == Memo.Status.ARCHIVED
    assert memo.approved_at is not None
    assert memo.archived_at is not None
    assert all(s.status == StepStatus.COMPLETED for s in memo.workflow_steps.all())


@pytest.mark.django_db
def test_approver_cannot_act_before_supporter(api, chain, memo):
    """Phase 5: 'Approver cannot approve before supporter.'"""
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    res = _act(api, chain["approver"], memo)
    assert res.status_code == 400
    assert "must be completed before your" in str(res.data)

    memo.refresh_from_db()
    assert memo.status == Memo.Status.DRAFT_FOR_REVIEW
    assert memo.workflow_steps.get(sequence=4).status == StepStatus.PENDING


@pytest.mark.django_db
def test_every_acting_role_must_supply_a_comment(api, chain, memo):
    """
    WIDENED FROM THE REVIEWER ALONE (Phase MEMO-WORKFLOW-FINAL-ENTERPRISE).

    This used to assert the manual's narrower rule — Phase 5 annotates only the
    Reviewer step "(Comment Required)" — and its last line explicitly checked
    that a recommender COULD proceed with `remarks=""`. That is the line worth
    remembering: every other role could advance a memo leaving no record of
    why, and afterwards the history showed the action with an empty remark and
    nothing to say whether the actor had read the attachments.

    The manual and the code now disagree on this point, deliberately.
    """
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    # Too short is refused for the reviewer...
    res = _act(api, chain["reviewer"], memo, remarks="ok")
    assert res.status_code == 400
    assert "comment of at least" in str(res.data).lower()
    assert _act(api, chain["reviewer"], memo, remarks="Verified the figures.").status_code == 200

    # ...and an empty remark is now refused for the roles that follow, where it
    # used to be accepted.
    for role in ("recommender", "supporter", "approver"):
        blank = _act(api, chain[role], memo, remarks="")
        assert blank.status_code == 400, f"{role} advanced with no remarks"
        assert "comment of at least" in str(blank.data).lower()
        assert _act(api, chain[role], memo,
                    remarks=f"Checked as {role}; figures and attachments are in order."
                    ).status_code == 200


@pytest.mark.django_db
def test_outsider_cannot_act(api, chain, memo):
    stranger = _user("wf_stranger")
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    api.force_authenticate(stranger)
    res = api.post(f"/api/v1/memos/{memo.id}/act/",
                   {"decision": "proceed", "remarks": "letting this through"}, format="json")
    assert res.status_code in (403, 404)
    memo.refresh_from_db()
    assert memo.status == Memo.Status.DRAFT_FOR_REVIEW


@pytest.mark.django_db
def test_actor_cannot_act_twice(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    assert _act(api, chain["reviewer"], memo, remarks="Checked and correct.").status_code == 200

    res = _act(api, chain["reviewer"], memo, remarks="Checked again, still fine.")
    assert res.status_code == 400
    assert "already completed your step" in str(res.data)


# ---------------------------------------------------------------------------
# Rejection and revision
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_rejection_returns_memo_to_author_and_skips_later_steps(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    assert _act(api, chain["reviewer"], memo, remarks="Figures reconcile fine.").status_code == 200

    res = _act(api, chain["recommender"], memo, decision="reject",
               remarks="The budget line cited does not exist this year.")
    assert res.status_code == 200
    memo.refresh_from_db()
    assert memo.status == Memo.Status.REJECTED
    assert memo.workflow_steps.get(sequence=2).status == StepStatus.REJECTED
    # Steps that never ran read as skipped, not as pending forever.
    assert memo.workflow_steps.get(sequence=3).status == StepStatus.SKIPPED
    assert memo.workflow_steps.get(sequence=4).status == StepStatus.SKIPPED
    # Nothing is left pending on anyone: the matrix is the only routing record.
    assert memo.active_step is None

    # The author regains edit rights (Phase 2: "return to draft with comments").
    api.force_authenticate(chain["author"])
    detail = api.get(f"/api/v1/memos/{memo.id}/").data
    assert detail["can_edit"] is True
    assert detail["can_edit_matrix"] is True


@pytest.mark.django_db
def test_rejection_requires_a_reason(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    res = _act(api, chain["reviewer"], memo, decision="reject", remarks="no")
    assert res.status_code == 400
    assert "reason of at least" in str(res.data).lower()


@pytest.mark.django_db
def test_rejected_memo_can_be_edited_and_resubmitted_through_the_same_chain(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    _act(api, chain["reviewer"], memo, decision="reject",
         remarks="Attach the vendor quotation please.")

    api.force_authenticate(chain["author"])
    patched = api.patch(f"/api/v1/memos/{memo.id}/",
                        {"body": "<p>Quotation attached.</p>"}, format="json")
    assert patched.status_code == 200

    res = api.post(f"/api/v1/memos/{memo.id}/send-for-review/", {}, format="json")
    assert res.status_code == 200, res.data
    memo.refresh_from_db()
    assert memo.status == Memo.Status.DRAFT_FOR_REVIEW
    # The chain restarts from the top with every step reset.
    assert memo.workflow_steps.get(sequence=1).status == StepStatus.ACTIVE
    assert memo.workflow_steps.get(sequence=4).status == StepStatus.PENDING
    assert memo.workflow_steps.get(sequence=2).remarks == ""


# ---------------------------------------------------------------------------
# Phase 6 - archive is read-only
# ---------------------------------------------------------------------------
def _drive_to_archive(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    _act(api, chain["reviewer"], memo, remarks="Verified against the ledger.")
    _act(api, chain["recommender"], memo)
    _act(api, chain["supporter"], memo)
    _act(api, chain["approver"], memo)
    memo.refresh_from_db()
    return memo


@pytest.mark.django_db
def test_archived_memo_is_read_only(api, chain, memo, admin):
    memo = _drive_to_archive(api, chain, memo)
    assert memo.status == Memo.Status.ARCHIVED

    api.force_authenticate(chain["author"])
    assert api.patch(f"/api/v1/memos/{memo.id}/", {"subject": "Rewritten"},
                     format="json").status_code == 403
    assert api.delete(f"/api/v1/memos/{memo.id}/").status_code == 403
    assert api.post(f"/api/v1/memos/{memo.id}/matrix/",
                    {"workflow": _matrix(chain)}, format="json").status_code == 400

    # Not even an admin may delete an archived memo - it is the record.
    api.force_authenticate(admin)
    assert api.delete(f"/api/v1/memos/{memo.id}/").status_code == 403

    memo.refresh_from_db()
    assert memo.subject != "Rewritten"   # the refused PATCH changed nothing
    assert Memo.objects.filter(pk=memo.pk).exists()


@pytest.mark.django_db
def test_archived_memo_reports_read_only_capabilities(api, chain, memo):
    memo = _drive_to_archive(api, chain, memo)
    api.force_authenticate(chain["author"])
    data = api.get(f"/api/v1/memos/{memo.id}/").data
    assert data["is_read_only"] is True
    assert data["can_edit"] is False
    assert data["can_act"] is False
    assert data["can_delete"] is False
    assert data["can_download_pdf"] is True


@pytest.mark.django_db
def test_pdf_available_for_archived_memo(api, chain, memo):
    """Approval auto-archives, so gating the PDF on `approved` alone would make
    it unreachable for every completed memo."""
    memo = _drive_to_archive(api, chain, memo)
    api.force_authenticate(chain["author"])
    res = api.get(f"/api/v1/memos/{memo.id}/pdf/")
    assert res.status_code == 200
    assert res["Content-Type"] == "application/pdf"


# ---------------------------------------------------------------------------
# Phase 7 - timeline
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_timeline_shows_completed_and_outstanding_steps(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    _act(api, chain["reviewer"], memo, remarks="Verified against the ledger.")

    api.force_authenticate(chain["author"])
    entries = api.get(f"/api/v1/memos/{memo.id}/timeline/").data

    history = [e for e in entries if e["kind"] == "history"]
    upcoming = [e for e in entries if e["kind"] == "upcoming"]

    assert [e["action"] for e in history] == ["sent_for_review", "reviewed"]
    assert history[-1]["actor_name"] == "Wf_reviewer T"
    assert history[-1]["remarks"] == "Verified against the ledger."

    # The three steps still to come are visible with who they are waiting on -
    # which the history-only design could not express at all.
    assert [e["state"] for e in upcoming] == ["active", "pending", "pending"]
    assert upcoming[0]["actor_name"] == "Wf_recommender T"
    assert "Awaiting Recommender" in upcoming[0]["label"]


@pytest.mark.django_db
def test_my_step_tells_each_actor_whether_it_is_their_turn(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    api.force_authenticate(chain["reviewer"])
    mine = api.get(f"/api/v1/memos/{memo.id}/").data["my_step"]
    assert mine["is_my_turn"] is True
    assert mine["role_type"] == "reviewer"
    assert mine["requires_comment"] is True

    api.force_authenticate(chain["approver"])
    theirs = api.get(f"/api/v1/memos/{memo.id}/").data["my_step"]
    assert theirs["is_my_turn"] is False
    assert theirs["sequence"] == 4


# ---------------------------------------------------------------------------
# Phase 9 - notifications
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_each_stage_notifies_the_next_assignee_with_its_own_category(api, chain, memo):
    from notifications.models import Notification

    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    assert Notification.objects.filter(
        recipient=chain["reviewer"], category="MEMO_REVIEW_REQUIRED").exists()

    _act(api, chain["reviewer"], memo, remarks="Verified against the ledger.")
    assert Notification.objects.filter(
        recipient=chain["recommender"], category="MEMO_RECOMMENDATION_REQUIRED").exists()

    _act(api, chain["recommender"], memo)
    assert Notification.objects.filter(
        recipient=chain["supporter"], category="MEMO_SUPPORT_REQUIRED").exists()

    _act(api, chain["supporter"], memo)
    assert Notification.objects.filter(
        recipient=chain["approver"], category="MEMO_APPROVAL_REQUIRED").exists()

    _act(api, chain["approver"], memo)
    assert Notification.objects.filter(
        recipient=chain["author"], category="MEMO_APPROVED").exists()
    assert Notification.objects.filter(
        recipient=chain["author"], category="MEMO_ARCHIVED").exists()


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_every_transition_writes_history_and_audit_rows(api, chain, memo):
    from audit.models import AuditLog

    memo = _drive_to_archive(api, chain, memo)

    actions = list(memo.approval_steps.order_by("step_order")
                   .values_list("action", flat=True))
    assert actions == [
        "sent_for_review", "reviewed", "recommended", "supported",
        "approved", "archived",
    ]

    transitions = {
        entry.changes.get("transition")
        for entry in AuditLog.objects.filter(object_id=str(memo.id))
    }
    assert {"sent_for_review", "reviewed", "recommended", "supported",
            "approved", "archived"} <= transitions


@pytest.mark.django_db
def test_admin_override_is_flagged_in_the_audit_trail(api, chain, memo, admin):
    from audit.models import AuditLog

    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    assert _act(api, admin, memo, remarks="Actioned on behalf of the reviewer.").status_code == 200
    memo.refresh_from_db()
    assert memo.status == Memo.Status.UNDER_REVIEW

    entry = AuditLog.objects.filter(
        object_id=str(memo.id), changes__transition="reviewed").first()
    assert entry is not None
    assert entry.changes["admin_override"] is True


# ---------------------------------------------------------------------------
# Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE
#
# The work queue sent {decision: "approve", remarks: ""}. Both halves were wrong
# and each produced its own 400, the first hiding the second. The list row now
# tells the client the rule, from the constants the endpoint enforces.
# ---------------------------------------------------------------------------
def _pending_row(api, user, memo):
    api.force_authenticate(user)
    res = api.get("/api/v1/memos/", {"scope": "pending"})
    assert res.status_code == 200, res.data
    rows = res.data.get("results", res.data.get("items", res.data)) \
        if isinstance(res.data, dict) else res.data
    return next((r for r in rows if str(r["id"]) == str(memo.id)), None)


@pytest.mark.django_db
def test_the_two_stacked_400s_the_queue_hit(api, chain, memo):
    """Captured from the browser, verbatim - pinned so neither can silently return."""
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    wrong_decision = _act(api, chain["reviewer"], memo, decision="approve", remarks="")
    assert wrong_decision.status_code == 400
    assert "decision" in wrong_decision.data

    no_comment = _act(api, chain["reviewer"], memo, decision="proceed", remarks="")
    assert no_comment.status_code == 400
    assert "10 characters" in str(no_comment.data["remarks"])

    assert _act(api, chain["reviewer"], memo, decision="proceed",
                remarks="Go ahead 11").status_code == 200


@pytest.mark.django_db
def test_each_role_is_told_its_own_step_and_rule(api, chain, memo):
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    for role, label in (("reviewer", "Review"), ("recommender", "Recommend"),
                        ("supporter", "Support"), ("approver", "Approve")):
        row = _pending_row(api, chain[role], memo)
        assert row is not None, f"{role} should have the memo pending"
        step = row["my_step"]
        assert step["role_type"] == role
        assert step["action_label"] == label
        assert step["requires_comment"] is True
        assert step["min_comment_length"] == MIN_COMMENT_LENGTH
        assert step["decisions"] == ["proceed", "reject"]
        # Nobody else is told it is their step.
        for other in ("reviewer", "recommender", "supporter", "approver"):
            if other != role:
                api.force_authenticate(chain[other])
                inbox = api.get("/api/v1/memos/", {"scope": "inbox"}).data
                rows = inbox.get("results", inbox.get("items", inbox)) \
                    if isinstance(inbox, dict) else inbox
                mine = next((r for r in rows if str(r["id"]) == str(memo.id)), None)
                assert mine is None or mine["my_step"] is None, f"{other} saw {role}'s step"
        assert _act(api, chain[role], memo).status_code == 200


@pytest.mark.django_db
def test_my_step_adds_no_query_per_row(api, chain):
    """Read from the prefetched steps, as `approved_by` is - not one query per memo."""
    api.force_authenticate(chain["author"])
    for n in range(6):
        m = Memo.objects.create(subject=f"Bulk {n}", memo_type=Memo.MemoType.GENERAL,
                                status=Memo.Status.DRAFT, created_by=chain["author"],
                                memo_number=generate_memo_number(Memo.MemoType.GENERAL))
        api.post(f"/api/v1/memos/{m.id}/send-for-review/",
                 {"workflow": _matrix(chain)}, format="json")
    api.force_authenticate(chain["reviewer"])
    api.get("/api/v1/memos/", {"scope": "pending"})          # warm caches
    import django.db
    from django.test.utils import CaptureQueriesContext
    with CaptureQueriesContext(django.db.connection) as six:
        api.get("/api/v1/memos/", {"scope": "pending"})
    extra = Memo.objects.create(subject="Bulk 7", memo_type=Memo.MemoType.GENERAL,
                                status=Memo.Status.DRAFT, created_by=chain["author"],
                                memo_number=generate_memo_number(Memo.MemoType.GENERAL))
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{extra.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    api.force_authenticate(chain["reviewer"])
    with CaptureQueriesContext(django.db.connection) as seven:
        api.get("/api/v1/memos/", {"scope": "pending"})
    assert len(seven.captured_queries) == len(six.captured_queries)


@pytest.mark.django_db
def test_a_withdrawn_memo_says_so_instead_of_denying_membership(api, chain, memo):
    """
    Found in the browser: the author withdrew the memo while the reviewer's
    comment dialog was open, and the reviewer was told they were "not part of
    this memo's approval workflow". Still refused - with the true reason.
    """
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    api.post(f"/api/v1/memos/{memo.id}/cancel/",
             {"remarks": "Withdrawn before review."}, format="json")

    res = _act(api, chain["reviewer"], memo)
    assert res.status_code == 403
    assert "cancelled" in str(res.data["detail"]).lower()
    assert "not part of" not in str(res.data["detail"])

    # Somebody never on the chain still gets the membership message.
    outsider = _user("wf_outsider")
    api.force_authenticate(outsider)
    other = Memo.objects.create(subject="Live one", memo_type=Memo.MemoType.GENERAL,
                                status=Memo.Status.DRAFT, created_by=chain["author"],
                                memo_number=generate_memo_number(Memo.MemoType.GENERAL))
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{other.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")
    denied = _act(api, outsider, other)
    assert denied.status_code in (403, 404)


@pytest.mark.django_db
def test_my_step_says_where_the_step_sits_and_what_comes_next(api, chain, memo):
    """Phase MEMO-QUEUE-UX-HARDENING: role, current step, next step."""
    api.force_authenticate(chain["author"])
    api.post(f"/api/v1/memos/{memo.id}/send-for-review/",
             {"workflow": _matrix(chain)}, format="json")

    expected = [
        ("reviewer", 1, "Recommender", chain["recommender"]),
        ("recommender", 2, "Supporter", chain["supporter"]),
        ("supporter", 3, "Approver", chain["approver"]),
        ("approver", 4, None, None),
    ]
    for role, position, next_label, next_user in expected:
        step = _pending_row(api, chain[role], memo)["my_step"]
        assert step["position"] == position
        assert step["total_steps"] == 4
        assert step["author_name"] == chain["author"].get_full_name()
        if next_label:
            assert step["next_step"]["role_label"] == next_label
            assert step["next_step"]["assignee_name"] == next_user.get_full_name()
            assert step["is_final_step"] is False
        else:
            assert step["next_step"] is None
            assert step["is_final_step"] is True
        assert _act(api, chain[role], memo).status_code == 200
