"""
Phase 49.5 - the four governance blockers, and the security questions each one
opens.

Written against the HTTP layer, not the service functions. The question a release
needs answered is "what happens when this person sends this request", and a unit
test of a predicate cannot answer it - a route can forget to consult the predicate,
and in this project one already had.

The security probes are the point of the file as much as the happy paths: every
capability added here widens what somebody can do, and the ones that widen READ
access (archive sharing) are tested for what they must NOT do as well as what they
must.
"""
import pytest
from django.contrib.auth.models import Group

from leaves.models import Department
from memos.models import (
    Memo, MemoApprovalStep, MemoArchiveAccess, MemoAssignmentTransfer, MemoNote,
    MemoNoteRecipient, MemoStepUnavailability, MemoWorkflowStep,
)
from memos.permissions import visible_memo_filter
from users.models import User

from .conftest import act, drive_to_approval, matrix, route

Reason = MemoStepUnavailability.Reason
Kind = MemoAssignmentTransfer.Kind
Target = MemoArchiveAccess.Target
NoteStatus = MemoNoteRecipient.NoteStatus


def _user(username, role=User.Roles.MAKER, **extra):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="Test", role=role,
        department=extra.pop("department", "Finance"), **extra)


@pytest.fixture
def outsider(db):
    """Authenticated, in another department, on nothing. The control subject."""
    return _user("outsider1", department="Logistics")


@pytest.fixture
def routed(db, api, general_draft, maker, checker, approver):
    """A general memo under review: checker is the Reviewer, approver the Approver."""
    return route(api, maker, general_draft,
                 (checker, "reviewer"), (approver, "approver"))


@pytest.fixture
def archived(db, api, general_draft, maker, checker, approver):
    """The same memo, driven all the way to archived."""
    memo = drive_to_approval(api, maker, general_draft,
                             (checker, "reviewer"), (approver, "approver"))
    assert memo.status == Memo.Status.ARCHIVED
    return memo


# ===========================================================================
# Blocker 1 - the NOTED workflow
# ===========================================================================
@pytest.mark.django_db
def test_a_note_request_creates_a_pending_note_and_notifies(api, routed, maker,
                                                            other_maker):
    api.force_authenticate(maker)
    response = api.post(f"/api/v1/memos/{routed.id}/notes/",
                        {"user_ids": [str(other_maker.id)],
                         "remarks": "For your information."}, format="json")
    assert response.status_code == 201, response.data
    assert response.data["total"] == 1
    assert response.data["pending"] == 1
    assert response.data["completed"] == 0
    assert response.data["recipients"][0]["status"] == NoteStatus.PENDING

    recipient = MemoNoteRecipient.objects.get(user=other_maker)
    # Snapshotted at request time, so a register printed later stays accurate.
    assert recipient.user_name == other_maker.get_full_name()
    assert recipient.notified_at is not None


@pytest.mark.django_db
def test_noting_does_not_advance_the_workflow(api, routed, maker, other_maker,
                                              checker, step_of):
    """
    The whole point of blocker 1. A note must change nothing about the chain.
    """
    before_status = routed.status
    before_active = step_of(routed, checker).status

    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    api.force_authenticate(other_maker)
    noted = api.post(f"/api/v1/memos/{routed.id}/note/",
                     {"remarks": "Read and understood."}, format="json")
    assert noted.status_code == 200, noted.data

    routed.refresh_from_db()
    assert routed.status == before_status
    assert step_of(routed, checker).status == before_active
    # And the reviewer is still the one being waited on.
    assert routed.active_step.assignee_id == checker.id


@pytest.mark.django_db
def test_a_note_is_recorded_with_its_remarks_and_date(api, routed, maker,
                                                      other_maker):
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    api.force_authenticate(other_maker)
    response = api.post(f"/api/v1/memos/{routed.id}/note/",
                        {"remarks": "Noted; no comment on the costing."},
                        format="json")
    row = response.data["recipients"][0]
    assert row["status"] == NoteStatus.NOTED
    assert row["remarks"] == "Noted; no comment on the costing."
    assert row["noted_at"] is not None
    assert response.data["is_complete"] is True


@pytest.mark.django_db
def test_nobody_can_note_without_being_asked(api, routed, outsider, other_maker,
                                             maker):
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    # An outsider cannot even see the memo, let alone note it.
    api.force_authenticate(outsider)
    assert api.post(f"/api/v1/memos/{routed.id}/note/", {},
                    format="json").status_code == 404
    # And somebody who CAN see it but was not asked is refused.
    api.force_authenticate(routed.created_by)
    refused = api.post(f"/api/v1/memos/{routed.id}/note/", {}, format="json")
    assert refused.status_code == 400
    assert "not been asked" in str(refused.data)


@pytest.mark.django_db
def test_nobody_can_note_twice(api, routed, maker, other_maker):
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    api.force_authenticate(other_maker)
    assert api.post(f"/api/v1/memos/{routed.id}/note/", {},
                    format="json").status_code == 200
    again = api.post(f"/api/v1/memos/{routed.id}/note/", {}, format="json")
    assert again.status_code == 400
    assert "already noted" in str(again.data)


@pytest.mark.django_db
def test_a_reviewer_may_request_a_note_but_an_uninvolved_reader_may_not(
        api, routed, checker, other_maker, other_checker):
    """
    The brief's list is Recommender, Reviewer, Supporter, Approver - so holding a
    step is the qualification, not merely being able to read.
    """
    api.force_authenticate(checker)
    allowed = api.post(f"/api/v1/memos/{routed.id}/notes/",
                       {"user_ids": [str(other_maker.id)]}, format="json")
    assert allowed.status_code == 201, allowed.data

    # other_checker is a department head who can read the general memo but holds
    # no step on it.
    api.force_authenticate(other_checker)
    refused = api.post(f"/api/v1/memos/{routed.id}/notes/",
                       {"user_ids": [str(other_checker.id)]}, format="json")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_a_note_request_is_idempotent_and_never_resets_a_given_note(
        api, routed, maker, other_maker):
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    api.force_authenticate(other_maker)
    api.post(f"/api/v1/memos/{routed.id}/note/", {"remarks": "Seen."},
             format="json")

    api.force_authenticate(maker)
    again = api.post(f"/api/v1/memos/{routed.id}/notes/",
                     {"user_ids": [str(other_maker.id)]}, format="json")
    assert again.status_code == 201
    assert again.data["total"] == 1
    assert again.data["completed"] == 1, "a re-request must not clear a given note"


@pytest.mark.django_db
def test_a_given_note_cannot_be_withdrawn_but_an_outstanding_one_can(
        api, routed, maker, other_maker, other_checker):
    api.force_authenticate(maker)
    created = api.post(
        f"/api/v1/memos/{routed.id}/notes/",
        {"user_ids": [str(other_maker.id), str(other_checker.id)]}, format="json")
    ids = {r["name"]: r["id"] for r in created.data["recipients"]}

    api.force_authenticate(other_maker)
    api.post(f"/api/v1/memos/{routed.id}/note/", {}, format="json")

    api.force_authenticate(maker)
    given = ids[other_maker.get_full_name()]
    refused = api.delete(f"/api/v1/memos/{routed.id}/notes/{given}/")
    assert refused.status_code == 400
    assert "cannot be withdrawn" in str(refused.data)

    outstanding = ids[other_checker.get_full_name()]
    removed = api.delete(f"/api/v1/memos/{routed.id}/notes/{outstanding}/")
    assert removed.status_code == 200
    assert removed.data["total"] == 1


@pytest.mark.django_db
def test_a_note_reaches_the_timeline_and_the_dashboard(api, routed, maker,
                                                       other_maker):
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")

    api.force_authenticate(other_maker)
    counts = api.get("/api/v1/memos/dashboard/").data
    assert counts["pending_notes"] == 1
    assert counts["completed_notes"] == 0
    # NOT folded into pending_actions: a note does not block the workflow, so it
    # must not appear in the badge that means "the memo is waiting on you".
    assert counts["pending_actions"] == 0

    api.post(f"/api/v1/memos/{routed.id}/note/", {"remarks": "Noted."},
             format="json")
    counts = api.get("/api/v1/memos/dashboard/").data
    assert counts["pending_notes"] == 0
    assert counts["completed_notes"] == 1

    timeline = api.get(f"/api/v1/memos/{routed.id}/timeline/").data
    assert any(entry["action"] == "noted" for entry in timeline)


@pytest.mark.django_db
def test_notes_cannot_be_requested_on_a_draft(api, general_draft, maker,
                                              other_maker):
    """A document that has not left its author is nobody else's to read."""
    api.force_authenticate(maker)
    refused = api.post(f"/api/v1/memos/{general_draft.id}/notes/",
                       {"user_ids": [str(other_maker.id)]}, format="json")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_the_note_stage_appears_on_the_tracker_only_when_notes_were_requested(
        api, routed, maker, other_maker):
    api.force_authenticate(maker)
    tracker = {s["key"]: s for s in api.get(
        f"/api/v1/memos/{routed.id}/").data["tracker"]}
    assert tracker["noted"]["state"] == "skipped", (
        "a memo nobody was asked to note is not waiting on a note")
    # Reviewed is on the tracker now - the Phase 49 audit found it missing.
    assert "reviewed" in tracker

    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    tracker = {s["key"]: s for s in api.get(
        f"/api/v1/memos/{routed.id}/").data["tracker"]}
    assert tracker["noted"]["state"] == "active"

    api.force_authenticate(other_maker)
    api.post(f"/api/v1/memos/{routed.id}/note/", {}, format="json")
    api.force_authenticate(maker)
    tracker = {s["key"]: s for s in api.get(
        f"/api/v1/memos/{routed.id}/").data["tracker"]}
    assert tracker["noted"]["state"] == "done"


@pytest.mark.django_db
def test_the_reviewed_tracker_stage_reflects_a_real_review(api, routed, maker,
                                                           checker):
    api.force_authenticate(maker)
    before = {s["key"]: s for s in api.get(
        f"/api/v1/memos/{routed.id}/").data["tracker"]}
    assert before["reviewed"]["state"] == "active"

    act(api, checker, routed, remarks="Reviewed and verified in full.")
    api.force_authenticate(maker)
    after = {s["key"]: s for s in api.get(
        f"/api/v1/memos/{routed.id}/").data["tracker"]}
    assert after["reviewed"]["state"] == "done"
    assert after["reviewed"]["actor"] == checker.get_full_name()


# ===========================================================================
# Blocker 2 - the unavailable approver
# ===========================================================================
@pytest.mark.django_db
def test_marking_a_step_unavailable_records_who_marked_it_and_why(
        api, routed, checker):
    api.force_authenticate(checker)
    response = api.post(f"/api/v1/memos/{routed.id}/unavailable/",
                        {"reason": Reason.ON_LEAVE,
                         "reason_note": "Back on the 20th."}, format="json")
    assert response.status_code == 200, response.data
    entry = response.data["assignment_history"]["unavailabilities"][0]
    assert entry["reason"] == Reason.ON_LEAVE
    assert entry["reason_label"] == "On Leave"
    assert entry["absent"] == checker.get_full_name()
    assert entry["marked_by"] == checker.get_full_name()
    assert entry["is_open"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("reason", [r.value for r in Reason])
def test_every_named_unavailability_reason_is_accepted(api, routed, checker,
                                                       reason):
    api.force_authenticate(checker)
    assert api.post(f"/api/v1/memos/{routed.id}/unavailable/",
                    {"reason": reason}, format="json").status_code == 200


@pytest.mark.django_db
def test_an_unknown_reason_is_refused(api, routed, checker):
    api.force_authenticate(checker)
    assert api.post(f"/api/v1/memos/{routed.id}/unavailable/",
                    {"reason": "on_holiday"}, format="json").status_code == 400


@pytest.mark.django_db
def test_a_replacement_takes_over_the_step_and_the_memo_continues(
        api, routed, maker, checker, approver, other_checker, step_of):
    """The end-to-end flow: unavailable -> replacement -> the memo advances."""
    api.force_authenticate(checker)
    api.post(f"/api/v1/memos/{routed.id}/unavailable/",
             {"reason": Reason.ON_LEAVE}, format="json")

    api.force_authenticate(maker)
    replaced = api.post(f"/api/v1/memos/{routed.id}/replacement/",
                        {"user_id": str(other_checker.id), "kind": "acting",
                         "reason": "Standing in while on sick leave."},
                        format="json")
    assert replaced.status_code == 200, replaced.data

    routed.refresh_from_db()
    step = routed.workflow_steps.get(role_type="reviewer")
    # Same sequence, same role, same activation - only the holder changed.
    assert step.sequence == 1
    assert step.role_type == "reviewer"
    assert step.status == MemoWorkflowStep.StepStatus.ACTIVE
    assert step.assignee_id == other_checker.id
    assert step.assignee_name == other_checker.get_full_name()

    # The stand-in can act immediately, and the original holder cannot.
    assert act(api, checker, routed).status_code == 403
    assert act(api, other_checker, routed).status_code == 200
    routed.refresh_from_db()
    assert routed.status == Memo.Status.UNDER_REVIEW
    assert routed.active_step.assignee_id == approver.id


@pytest.mark.django_db
def test_a_replacement_needs_an_open_unavailability(api, routed, maker,
                                                    other_checker):
    """
    No silent reassignment through the replacement route: the record has to say
    why the step changed hands. Moving a step for any other reason goes through
    /reassign/, which records its own kind.
    """
    api.force_authenticate(maker)
    refused = api.post(f"/api/v1/memos/{routed.id}/replacement/",
                       {"user_id": str(other_checker.id),
                        "reason": "No particular reason at all."},
                       format="json")
    assert refused.status_code == 400
    assert "not marked unavailable" in str(refused.data)


@pytest.mark.django_db
def test_a_replacement_records_both_who_declared_the_absence_and_who_authorised_the_stand_in(
        api, routed, maker, checker, other_checker):
    """
    The control the Phase 49 audit found missing. Under the old admin-override
    workaround the record said only that an override happened.
    """
    api.force_authenticate(checker)
    api.post(f"/api/v1/memos/{routed.id}/unavailable/",
             {"reason": Reason.ON_TRAINING}, format="json")
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{routed.id}/replacement/",
             {"user_id": str(other_checker.id),
              "reason": "Training course runs all week."}, format="json")

    record = MemoStepUnavailability.objects.get(memo=routed)
    assert record.marked_by_name == checker.get_full_name()
    assert record.resolved_by_name == maker.get_full_name()
    assert record.resolved_at is not None

    transfer = MemoAssignmentTransfer.objects.get(memo=routed)
    assert transfer.from_user_name == checker.get_full_name()
    assert transfer.to_user_name == other_checker.get_full_name()
    assert transfer.reason == "Training course runs all week."
    assert transfer.actor_name == maker.get_full_name()
    assert transfer.unavailability_id == record.id


@pytest.mark.django_db
def test_a_step_already_acted_on_cannot_be_reassigned(api, routed, maker, checker,
                                                      other_checker):
    """Moving a completed step would reassign an approval that has been given."""
    act(api, checker, routed)
    routed.refresh_from_db()
    done = routed.workflow_steps.get(role_type="reviewer")

    api.force_authenticate(maker)
    refused = api.post(f"/api/v1/memos/{routed.id}/reassign/",
                       {"user_id": str(other_checker.id), "kind": "assign_new",
                        "step_id": str(done.id),
                        "reason": "Trying to move a finished step."},
                       format="json")
    assert refused.status_code == 400
    assert "already been acted on" in str(refused.data)


@pytest.mark.django_db
def test_an_unavailability_cannot_be_declared_twice_on_one_step(api, routed,
                                                                checker):
    api.force_authenticate(checker)
    api.post(f"/api/v1/memos/{routed.id}/unavailable/",
             {"reason": Reason.ON_CONFERENCE_MEETING}, format="json")
    again = api.post(f"/api/v1/memos/{routed.id}/unavailable/",
                     {"reason": Reason.ON_LEAVE}, format="json")
    assert again.status_code == 400
    assert "already marked unavailable" in str(again.data)


@pytest.mark.django_db
def test_an_outsider_cannot_park_somebody_elses_step(api, routed, outsider):
    api.force_authenticate(outsider)
    assert api.post(f"/api/v1/memos/{routed.id}/unavailable/",
                    {"reason": Reason.ON_LEAVE},
                    format="json").status_code == 404


@pytest.mark.django_db
def test_an_uninvolved_reader_cannot_declare_an_absence_on_a_memo_they_can_read(
        api, routed, other_maker):
    """
    other_maker can be given read access but holds no step and has no standing.
    Reading is not authority to reroute.
    """
    api.force_authenticate(routed.created_by)
    api.post(f"/api/v1/memos/{routed.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    api.force_authenticate(other_maker)
    refused = api.post(f"/api/v1/memos/{routed.id}/unavailable/",
                       {"reason": Reason.ON_LEAVE}, format="json")
    assert refused.status_code in (403, 404)


# ===========================================================================
# Blocker 3 - recommender reassignment
# ===========================================================================
@pytest.mark.django_db
def test_a_supervisor_can_reassign_a_recommender(api, general_draft, maker,
                                                 checker, approver,
                                                 other_checker):
    """
    The Phase 49 audit found `employee_type` ignored entirely, so the brief's
    "Supervisor" had no authority. This is that gap closed: a plain maker whose
    employee_type is supervisor can now reassign.
    """
    memo = route(api, maker, general_draft,
                 (checker, "recommender"), (approver, "approver"))
    supervisor = _user("supervisor1",
                       employee_type=User.EmployeeType.SUPERVISOR)

    api.force_authenticate(supervisor)
    # No read access, so the memo is invisible - standing to reassign does not
    # imply standing to see. The author does it instead, below.
    assert api.post(f"/api/v1/memos/{memo.id}/reassign/",
                    {"user_id": str(other_checker.id), "kind": "assign_new",
                     "reason": "Reassigning the recommendation."},
                    format="json").status_code == 404

    api.force_authenticate(maker)
    moved = api.post(f"/api/v1/memos/{memo.id}/reassign/",
                     {"user_id": str(other_checker.id), "kind": "assign_new",
                      "reason": "Original recommender has left the department."},
                     format="json")
    assert moved.status_code == 200, moved.data
    memo.refresh_from_db()
    assert memo.workflow_steps.get(role_type="recommender").assignee_id == other_checker.id

    transfer = MemoAssignmentTransfer.objects.get(memo=memo)
    assert transfer.kind == Kind.ASSIGN_NEW
    assert transfer.role_type == "recommender"
    assert transfer.from_user_name == checker.get_full_name()
    assert transfer.to_user_name == other_checker.get_full_name()


@pytest.mark.django_db
def test_a_recommender_change_is_a_distinct_timeline_event_from_an_approver_change(
        api, general_draft, maker, checker, approver, other_checker, admin):
    memo = route(api, maker, general_draft,
                 (checker, "recommender"), (approver, "approver"))
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{memo.id}/reassign/",
             {"user_id": str(other_checker.id), "kind": "assign_new",
              "reason": "Recommender moved teams."}, format="json")

    approver_step = memo.workflow_steps.get(role_type="approver")
    api.post(f"/api/v1/memos/{memo.id}/reassign/",
             {"user_id": str(admin.id), "kind": "assign_new",
              "step_id": str(approver_step.id),
              "reason": "Approver delegated to the administrator."},
             format="json")

    actions = [e["action"] for e in
               api.get(f"/api/v1/memos/{memo.id}/timeline/").data]
    assert "recommender_changed" in actions
    assert "approver_changed" in actions


@pytest.mark.django_db
def test_self_assign_moves_the_step_to_the_caller(api, general_draft, maker,
                                                  checker, approver, admin):
    memo = route(api, maker, general_draft,
                 (checker, "recommender"), (approver, "approver"))
    api.force_authenticate(admin)
    taken = api.post(f"/api/v1/memos/{memo.id}/reassign/",
                     {"kind": "self_assign",
                      "reason": "Taking this on as the acting head."},
                     format="json")
    assert taken.status_code == 200, taken.data
    memo.refresh_from_db()
    assert memo.workflow_steps.get(role_type="recommender").assignee_id == admin.id
    assert MemoAssignmentTransfer.objects.get(memo=memo).kind == Kind.SELF_ASSIGN


@pytest.mark.django_db
def test_a_reassignment_needs_a_real_reason(api, routed, maker, other_checker):
    api.force_authenticate(maker)
    for reason in ("", "   ", "too short"):
        refused = api.post(f"/api/v1/memos/{routed.id}/reassign/",
                           {"user_id": str(other_checker.id),
                            "kind": "assign_new", "reason": reason},
                           format="json")
        assert refused.status_code == 400, reason


@pytest.mark.django_db
def test_a_reassignment_cannot_put_the_author_in_their_own_chain(api, routed,
                                                                 maker):
    api.force_authenticate(maker)
    refused = api.post(f"/api/v1/memos/{routed.id}/reassign/",
                       {"user_id": str(maker.id), "kind": "assign_new",
                        "reason": "Trying to approve my own memo."},
                       format="json")
    assert refused.status_code == 400
    assert "cannot hold a step" in str(refused.data)


@pytest.mark.django_db
def test_a_reassignment_cannot_give_one_person_two_steps(api, routed, maker,
                                                          approver):
    """
    Mirrors the uniq_memo_step_assignee constraint the matrix builder relies on -
    otherwise one approval could satisfy two separate control points.
    """
    api.force_authenticate(maker)
    refused = api.post(f"/api/v1/memos/{routed.id}/reassign/",
                       {"user_id": str(approver.id), "kind": "assign_new",
                        "reason": "Collapsing two control points into one."},
                       format="json")
    assert refused.status_code == 400
    assert "already holds step" in str(refused.data)


# ===========================================================================
# Blocker 4 - archived access sharing
# ===========================================================================
@pytest.mark.django_db
def test_an_employee_grant_makes_an_archived_memo_readable(api, archived, maker,
                                                           outsider):
    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 404

    api.force_authenticate(maker)
    granted = api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                       {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
                        "reason": "Required for the annual audit file."},
                       format="json")
    assert granted.status_code == 201, granted.data

    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 200
    # And it appears in their list, not just by direct id - the queryset filter
    # and the object permission have to agree.
    listed = api.get("/api/v1/memos/?scope=archived").data
    rows = listed.get("results", listed.get("items", listed))
    assert str(archived.id) in [str(row["id"]) for row in rows]


@pytest.mark.django_db
def test_revoking_a_grant_takes_effect_immediately(api, archived, maker,
                                                   outsider):
    api.force_authenticate(maker)
    created = api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                       {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
                        "reason": "Temporary access for the audit."},
                       format="json")
    grant_id = created.data["grants"][0]["id"]
    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 200

    api.force_authenticate(maker)
    revoked = api.delete(f"/api/v1/memos/{archived.id}/archive-access/{grant_id}/",
                         {"reason": "Audit closed."}, format="json")
    assert revoked.status_code == 200

    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 404, (
        "a revoked grant must stop working on the next read")


@pytest.mark.django_db
def test_a_revoked_grant_is_retained_not_deleted(api, archived, maker, outsider):
    api.force_authenticate(maker)
    created = api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                       {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
                        "reason": "For the audit file."}, format="json")
    grant_id = created.data["grants"][0]["id"]
    api.delete(f"/api/v1/memos/{archived.id}/archive-access/{grant_id}/",
               {"reason": "No longer required."}, format="json")

    history = api.get(f"/api/v1/memos/{archived.id}/archive-access/").data["grants"]
    assert len(history) == 1
    assert history[0]["is_live"] is False
    assert history[0]["revoked_by"] == maker.get_full_name()
    assert history[0]["revoke_reason"] == "No longer required."
    # Who could read it in the past stays answerable.
    assert history[0]["granted_at"] is not None


@pytest.mark.django_db
def test_a_department_grant_reaches_a_member_of_a_child_unit(api, archived,
                                                            maker):
    parent = Department.objects.create(name="Operations", code="P495-OPS")
    unit = Department.objects.create(name="Operations - Logistics", code="P495-OPS-LOG",
                                     parent=parent)
    member = _user("unitmember1", department="Operations - Logistics")
    member.department_ref = unit
    member.save(update_fields=["department_ref"])

    api.force_authenticate(member)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 404

    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.DEPARTMENT, "department_id": str(parent.id),
              "include_children": True,
              "reason": "Shared with Operations and its units."}, format="json")

    api.force_authenticate(member)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 200, (
        "include_children must reach a member of a child unit")


@pytest.mark.django_db
def test_a_department_grant_without_include_children_does_not_reach_a_child_unit(
        api, archived, maker):
    parent = Department.objects.create(name="Corporate", code="P495-CORP")
    unit = Department.objects.create(name="Corporate - Legal", code="P495-CORP-LEG",
                                     parent=parent)
    member = _user("legal1", department="Corporate - Legal")
    member.department_ref = unit
    member.save(update_fields=["department_ref"])

    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.DEPARTMENT, "department_id": str(parent.id),
              "include_children": False,
              "reason": "Shared with the Corporate office only."}, format="json")

    api.force_authenticate(member)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 404


@pytest.mark.django_db
def test_a_group_grant_reaches_its_members_only(api, archived, maker, outsider,
                                                other_maker):
    auditors = Group.objects.create(name="Internal Auditors")
    outsider.groups.add(auditors)

    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.GROUP, "group_id": auditors.id,
              "reason": "Shared with the internal audit group."}, format="json")

    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 200
    api.force_authenticate(other_maker)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 404


@pytest.mark.django_db
def test_only_an_archived_memo_can_be_shared(api, routed, maker, outsider):
    api.force_authenticate(maker)
    refused = api.post(f"/api/v1/memos/{routed.id}/archive-access/",
                       {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
                        "reason": "Trying to share a live memo."}, format="json")
    assert refused.status_code == 400
    assert "archived memo" in str(refused.data)


@pytest.mark.django_db
def test_a_department_head_who_can_read_the_archive_cannot_share_it(
        api, archived, checker, outsider):
    """
    Reading is not disclosing. A department head sees their department's archive;
    handing it to another department is a different decision.
    """
    api.force_authenticate(checker)
    refused = api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                       {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
                        "reason": "Passing this on to a colleague."},
                       format="json")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_a_grant_needs_a_reason(api, archived, maker, outsider):
    api.force_authenticate(maker)
    refused = api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                       {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
                        "reason": "audit"}, format="json")
    assert refused.status_code == 400


@pytest.mark.django_db
def test_a_grantee_can_read_but_not_act_on_the_memo(api, archived, maker,
                                                    outsider):
    """
    The security question a read grant opens. A grant adds a READER, never an
    approver - and the memo is archived anyway, so nothing about it may change.
    """
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
              "reason": "Shared for the audit file."}, format="json")

    api.force_authenticate(outsider)
    detail = api.get(f"/api/v1/memos/{archived.id}/").data
    assert detail["can_edit"] is False
    assert detail["can_act"] is False
    assert detail["can_share_archive"] is False
    assert detail["can_manage_assignments"] is False

    assert api.patch(f"/api/v1/memos/{archived.id}/",
                     {"title": "Rewritten"}, format="json").status_code == 403
    assert api.post(f"/api/v1/memos/{archived.id}/act/",
                    {"decision": "proceed"}, format="json").status_code == 403
    assert api.delete(f"/api/v1/memos/{archived.id}/").status_code == 403
    # And cannot re-share what was shared with them.
    assert api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                    {"target": Target.EMPLOYEE, "user_id": str(maker.id),
                     "reason": "Passing it along further."},
                    format="json").status_code == 403


@pytest.mark.django_db
def test_a_grantee_cannot_read_the_audit_trail(api, archived, maker, outsider):
    """A grant widens the document, not the forensic record behind it."""
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
              "reason": "Shared for the audit file."}, format="json")
    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/memos/{archived.id}/audit-trail/").status_code == 403


@pytest.mark.django_db
def test_a_grant_on_one_memo_does_not_leak_another(api, archived, maker,
                                                   outsider, checker, approver,
                                                   other_maker):
    """A grant is per-memo. The obvious way to get this wrong is a global rule."""
    from memos.services import generate_memo_number
    second = Memo.objects.create(
        subject="Other",
        memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
        created_by=maker, memo_number=generate_memo_number(Memo.MemoType.GENERAL))
    second = drive_to_approval(api, maker, second,
                               (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
              "reason": "Shared for the audit file."}, format="json")

    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/memos/{archived.id}/").status_code == 200
    assert api.get(f"/api/v1/memos/{second.id}/").status_code == 404


@pytest.mark.django_db
def test_the_queryset_filter_and_the_object_permission_agree_about_grants(
        api, archived, maker, outsider):
    """
    The invariant the memo module already holds itself to for visibility, extended
    to grants: the cheap list filter and the object check must never disagree, or
    a memo appears in a list and 403s when opened.
    """
    from memos.permissions import CanViewMemo

    def visible_by_queryset():
        return Memo.objects.filter(visible_memo_filter(outsider)).distinct()

    checker_ = CanViewMemo()

    class _Req:
        user = outsider

    assert archived not in visible_by_queryset()
    assert checker_.has_object_permission(_Req(), None, archived) is False

    api.force_authenticate(maker)
    created = api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                       {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
                        "reason": "Shared for the audit file."}, format="json")
    outsider.refresh_from_db()
    assert archived in visible_by_queryset()
    assert checker_.has_object_permission(_Req(), None, archived) is True

    grant_id = created.data["grants"][0]["id"]
    api.delete(f"/api/v1/memos/{archived.id}/archive-access/{grant_id}/",
               {"reason": "Audit closed."}, format="json")
    outsider.refresh_from_db()
    assert archived not in visible_by_queryset()
    assert checker_.has_object_permission(_Req(), None, archived) is False


@pytest.mark.django_db
def test_a_grant_appears_on_the_timeline_and_in_the_history(api, archived, maker,
                                                            outsider):
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
              "reason": "Shared with internal audit."}, format="json")
    actions = [e["action"] for e in
               api.get(f"/api/v1/memos/{archived.id}/timeline/").data]
    assert "access_granted" in actions

    detail = api.get(f"/api/v1/memos/{archived.id}/").data
    assert detail["archive_sharing"][0]["granted_to"] == outsider.get_full_name()
    assert detail["archive_sharing"][0]["reason"] == "Shared with internal audit."


@pytest.mark.django_db
def test_the_same_target_cannot_be_granted_twice(api, archived, maker, outsider):
    api.force_authenticate(maker)
    payload = {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
               "reason": "Shared for the audit file."}
    assert api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                    payload, format="json").status_code == 201
    duplicate = api.post(f"/api/v1/memos/{archived.id}/archive-access/",
                         payload, format="json")
    assert duplicate.status_code == 400
    assert "already has access" in str(duplicate.data)


# ===========================================================================
# The edit event, and the PDF's verification guard
# ===========================================================================
@pytest.mark.django_db
def test_an_edit_reaches_the_timeline(api, general_draft, maker):
    api.force_authenticate(maker)
    api.patch(f"/api/v1/memos/{general_draft.id}/",
              {"subject": "Revised subject"}, format="json")
    timeline = api.get(f"/api/v1/memos/{general_draft.id}/timeline/").data
    edits = [e for e in timeline if e["action"] == "edited"]
    assert len(edits) == 1
    assert "subject" in edits[0]["remarks"]


@pytest.mark.django_db
def test_a_no_op_patch_is_not_recorded_as_an_edit(api, general_draft, maker):
    """Otherwise a client could pad the timeline with meaningless entries."""
    api.force_authenticate(maker)
    api.patch(f"/api/v1/memos/{general_draft.id}/", {}, format="json")
    timeline = api.get(f"/api/v1/memos/{general_draft.id}/timeline/").data
    assert not [e for e in timeline if e["action"] == "edited"]


@pytest.mark.django_db
def test_no_pdf_ever_carries_a_localhost_verification_url(settings):
    """
    The Phase 49 finding: SITE_URL defaulted to http://localhost:8001 silently,
    and an archived PDF already exported cannot be corrected afterwards.
    """
    from documents.pdf import common_context, site_url_is_publishable, verify_url

    for bad in ("", "http://localhost:8001", "https://127.0.0.1",
                "http://0.0.0.0:8000", "http://host.docker.internal:8001"):
        settings.SITE_URL = bad
        assert site_url_is_publishable() is False, bad
        assert verify_url("NIFN-GEN-2026-0001") is None, bad
        context = common_context("NIFN-GEN-2026-0001")
        assert context["verify_qr"] is None, bad
        assert context["verify_url"] is None, bad
        assert "SITE_URL" in context["verify_warning"]

    settings.SITE_URL = "https://oms.nif.org.np"
    assert site_url_is_publishable() is True
    context = common_context("NIFN-GEN-2026-0001")
    assert context["verify_url"] == (
        "https://oms.nif.org.np/api/v1/verify/NIFN-GEN-2026-0001/")
    assert context["verify_qr"].startswith("data:image/png;base64,")
    assert context["verify_warning"] is None


@pytest.mark.django_db
def test_the_pdf_prints_the_noted_and_sharing_registers(api, archived, maker,
                                                        other_maker, outsider,
                                                        settings):
    settings.SITE_URL = "https://oms.nif.org.np"
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    api.force_authenticate(other_maker)
    api.post(f"/api/v1/memos/{archived.id}/note/",
             {"remarks": "Noted for the record."}, format="json")
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/archive-access/",
             {"target": Target.EMPLOYEE, "user_id": str(outsider.id),
              "reason": "Shared with internal audit."}, format="json")

    response = api.get(f"/api/v1/memos/{archived.id}/pdf/")
    assert response.status_code == 200
    assert len(response.content) > 1000


@pytest.mark.django_db
def test_the_note_round_and_grants_survive_on_the_detail_payload(api, archived,
                                                                 maker,
                                                                 other_maker):
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{archived.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    detail = api.get(f"/api/v1/memos/{archived.id}/").data
    assert detail["notes"]["total"] == 1
    assert detail["notes"]["pending"] == 1
    assert detail["can_request_notes"] is True
    assert detail["can_note"] is False, "the author was not asked to note"
    assert detail["assignment_history"] == {"unavailabilities": [], "transfers": []}
    assert detail["archive_sharing"] == []

    api.force_authenticate(other_maker)
    assert api.get(f"/api/v1/memos/{archived.id}/").data["can_note"] is True
