"""
The circular lifecycle: draft, optional review, issue, and the guards around them.

Written against the HTTP layer. The question a release needs answered is "what
happens when this person sends this request", and a unit test of a predicate cannot
answer it - a route can forget to consult the predicate.
"""
import pytest

from circulars.models import Circular, CircularWorkflowStep
from users.models import User

from .conftest import act, chain_rows, drive_to_issued, make_circular, submit

Status = Circular.Status
StepStatus = CircularWorkflowStep.StepStatus


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_circular_is_numbered_CIR_year_six_digits(cast):
    from django.utils import timezone
    circular = make_circular(cast)
    year = timezone.now().year
    assert circular.circular_number.startswith(f"CIR-{year}-")
    assert circular.circular_number == f"CIR-{year}-000001"


@pytest.mark.django_db
def test_circular_numbers_are_sequential_and_unique(cast):
    numbers = [make_circular(cast).circular_number for _ in range(3)]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == 3
    assert numbers[-1].endswith("000003")


@pytest.mark.django_db
def test_the_number_survives_past_999999_by_widening(cast):
    """
    The counter is an integer, so the sequence widens rather than colliding or
    sorting wrongly - the failure mode a zero-padded string counter has at its
    boundary.
    """
    from django.utils import timezone

    from circulars.models import CircularNumberSequence
    from circulars.services import generate_circular_number

    CircularNumberSequence.objects.update_or_create(
        year=timezone.now().year, defaults={"last_value": 999999})
    assert generate_circular_number().endswith("-1000000")


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_circular_needs_exactly_one_issuer(api, cast, draft):
    api.force_authenticate(cast["author"])
    none = api.post(f"/api/v1/circulars/{draft.id}/chain/",
                    {"workflow": chain_rows((cast["reviewer"], "reviewer"))},
                    format="json")
    assert none.status_code == 400
    assert "exactly one issuer" in str(none.data)

    two = api.post(f"/api/v1/circulars/{draft.id}/chain/",
                   {"workflow": chain_rows((cast["reviewer"], "issuer"),
                                           (cast["issuer"], "issuer"))},
                   format="json")
    assert two.status_code == 400


@pytest.mark.django_db
def test_the_issuer_must_be_the_last_step(api, cast, draft):
    api.force_authenticate(cast["author"])
    response = api.post(f"/api/v1/circulars/{draft.id}/chain/",
                        {"workflow": chain_rows((cast["issuer"], "issuer"),
                                                (cast["reviewer"], "reviewer"))},
                        format="json")
    assert response.status_code == 400
    assert "Review comes before issue" in str(response.data)


@pytest.mark.django_db
def test_the_author_cannot_review_or_issue_their_own_circular(api, cast, draft):
    api.force_authenticate(cast["author"])
    response = api.post(f"/api/v1/circulars/{draft.id}/chain/",
                        {"workflow": chain_rows((cast["author"], "issuer"))},
                        format="json")
    assert response.status_code == 400
    assert "cannot review or issue their own" in str(response.data)


@pytest.mark.django_db
def test_one_person_cannot_hold_two_steps(api, cast, draft):
    api.force_authenticate(cast["author"])
    response = api.post(f"/api/v1/circulars/{draft.id}/chain/",
                        {"workflow": chain_rows((cast["issuer"], "reviewer"),
                                                (cast["issuer"], "issuer"))},
                        format="json")
    assert response.status_code == 400
    assert "appears twice" in str(response.data)


@pytest.mark.django_db
def test_the_chain_is_frozen_once_submitted(api, cast, draft):
    submit(api, draft, (cast["issuer"], "issuer"))
    api.force_authenticate(cast["author"])
    response = api.post(f"/api/v1/circulars/{draft.id}/chain/",
                        {"workflow": chain_rows((cast["reviewer"], "reviewer"),
                                                (cast["issuer"], "issuer"))},
                        format="json")
    assert response.status_code == 400
    assert "chain is fixed" in str(response.data)


# ---------------------------------------------------------------------------
# Review is OPTIONAL - the structural claim of this module's engine
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_circular_with_no_reviewer_goes_straight_to_ready_for_issue(
        api, cast, draft):
    circular = submit(api, draft, (cast["issuer"], "issuer"))
    assert circular.status == Status.READY_FOR_ISSUE
    assert circular.active_step.assignee_id == cast["issuer"].id


@pytest.mark.django_db
def test_a_circular_with_a_reviewer_goes_under_review_first(api, cast, draft):
    circular = submit(api, draft, (cast["reviewer"], "reviewer"),
                      (cast["issuer"], "issuer"))
    assert circular.status == Status.UNDER_REVIEW
    assert circular.active_step.assignee_id == cast["reviewer"].id

    assert act(api, cast["reviewer"], circular,
               remarks="Checked against the staff handbook.").status_code == 200
    circular.refresh_from_db()
    assert circular.status == Status.READY_FOR_ISSUE
    assert circular.active_step.assignee_id == cast["issuer"].id


@pytest.mark.django_db
def test_several_reviewers_act_one_after_another(api, cast, draft):
    circular = submit(api, draft, (cast["reviewer"], "reviewer"),
                      (cast["reviewer_two"], "reviewer"),
                      (cast["issuer"], "issuer"))
    # The second reviewer cannot jump the first.
    early = act(api, cast["reviewer_two"], circular, remarks="Trying to go early.")
    assert early.status_code == 403
    assert "It is not your turn" in str(early.data)

    act(api, cast["reviewer"], circular, remarks="First review complete.")
    circular.refresh_from_db()
    assert circular.status == Status.UNDER_REVIEW
    assert circular.active_step.assignee_id == cast["reviewer_two"].id


@pytest.mark.django_db
def test_the_tracker_reports_review_as_skipped_when_nobody_reviewed(api, cast,
                                                                    draft):
    """
    Not "pending": a circular nobody was asked to review is not forever waiting on
    a review. The same distinction the other two modules settled on.
    """
    circular = submit(api, draft, (cast["issuer"], "issuer"))
    api.force_authenticate(cast["author"])
    tracker = {s["key"]: s["state"] for s in
               api.get(f"/api/v1/circulars/{circular.id}/").data["tracker"]}
    assert tracker["reviewed"] == "skipped"
    assert tracker["issued"] == "active"


# ---------------------------------------------------------------------------
# Issue
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_issuing_records_who_issued_it_and_when(api, cast, draft):
    circular = drive_to_issued(api, draft, (cast["issuer"], "issuer"))
    assert circular.status == Status.ISSUED
    assert circular.issued_at is not None
    assert circular.issued_by_id == cast["issuer"].id
    assert circular.issued_by_name == cast["issuer"].get_full_name()


@pytest.mark.django_db
def test_a_reviewer_must_say_something_but_an_issuer_need_not(api, cast, draft):
    """
    A review with no remarks is not a review. An issuer's signature speaks for
    itself, so remarks there are optional.
    """
    circular = submit(api, draft, (cast["reviewer"], "reviewer"),
                      (cast["issuer"], "issuer"))
    silent = act(api, cast["reviewer"], circular, remarks="")
    assert silent.status_code == 400

    act(api, cast["reviewer"], circular, remarks="Checked and consistent.")
    circular.refresh_from_db()
    api.force_authenticate(cast["issuer"])
    issued = api.post(f"/api/v1/circulars/{circular.id}/act/",
                      {"decision": "proceed"}, format="json")
    assert issued.status_code == 200, issued.data


@pytest.mark.django_db
def test_content_is_frozen_once_issued(api, cast, draft):
    """
    The content is the text somebody signed. Not even an administrator may rewrite
    it - the shape the minute module got wrong, where its archive guard reads
    `and not _is_admin(actor)` and hands an admin a rewrite override.
    """
    circular = drive_to_issued(api, draft, (cast["issuer"], "issuer"))
    for actor in (cast["author"], cast["admin"], cast["hr"]):
        api.force_authenticate(actor)
        refused = api.patch(f"/api/v1/circulars/{circular.id}/",
                            {"subject": "Rewritten after issue"}, format="json")
        assert refused.status_code == 403, (actor.username, refused.status_code)
    circular.refresh_from_db()
    assert circular.subject.startswith("Revised office hours")


# ---------------------------------------------------------------------------
# Return for revision
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_return_needs_a_reason_and_stands_the_whole_chain_down(api, cast, draft):
    circular = submit(api, draft, (cast["reviewer"], "reviewer"),
                      (cast["issuer"], "issuer"))
    silent = act(api, cast["reviewer"], circular, decision="reject", remarks="no")
    assert silent.status_code == 400

    returned = act(api, cast["reviewer"], circular, decision="reject",
                   remarks="The effective date contradicts the staff handbook.")
    assert returned.status_code == 200
    circular.refresh_from_db()
    assert circular.status == Status.REJECTED
    # The issuer's step is stood down, not left pending: a revised circular is
    # reviewed afresh rather than resumed halfway.
    issuer_step = circular.workflow_steps.get(role_type="issuer")
    assert issuer_step.status == StepStatus.SKIPPED


@pytest.mark.django_db
def test_a_returned_circular_is_editable_and_can_be_resubmitted(api, cast, draft):
    circular = submit(api, draft, (cast["reviewer"], "reviewer"),
                      (cast["issuer"], "issuer"))
    act(api, cast["reviewer"], circular, decision="reject",
        remarks="Please correct the effective date before issue.")
    circular.refresh_from_db()

    api.force_authenticate(cast["author"])
    edited = api.patch(f"/api/v1/circulars/{circular.id}/",
                       {"subject": "Revised office hours, corrected"},
                       format="json")
    assert edited.status_code == 200, edited.data

    again = submit(api, circular, (cast["reviewer"], "reviewer"),
                   (cast["issuer"], "issuer"))
    assert again.status == Status.UNDER_REVIEW


# ---------------------------------------------------------------------------
# Cancel, delete, archive
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_draft_can_be_deleted_but_a_submitted_circular_cannot(api, cast, draft):
    other = make_circular(cast)
    api.force_authenticate(cast["author"])
    assert api.delete(f"/api/v1/circulars/{other.id}/").status_code == 204

    submit(api, draft, (cast["issuer"], "issuer"))
    api.force_authenticate(cast["author"])
    assert api.delete(f"/api/v1/circulars/{draft.id}/").status_code == 403


@pytest.mark.django_db
def test_a_broadcast_circular_cannot_be_cancelled(api, cast, draft):
    """
    People have already been told. The correction for a wrong circular is an
    amended circular, which is a governance act with its own record - not deleting
    the evidence that the first one went out.
    """
    from .conftest import drive_to_broadcast

    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["admin"])
    refused = api.post(f"/api/v1/circulars/{circular.id}/cancel/",
                       {"remarks": "Issued in error, withdrawing it."},
                       format="json")
    assert refused.status_code == 400
    assert "recipients have already been told" in str(refused.data)


@pytest.mark.django_db
def test_only_a_broadcast_circular_can_be_archived(api, cast, draft):
    circular = drive_to_issued(api, draft, (cast["issuer"], "issuer"))
    api.force_authenticate(cast["hr"])
    early = api.post(f"/api/v1/circulars/{circular.id}/archive/")
    assert early.status_code == 400

    from .conftest import broadcast
    broadcast(api, circular, cast["issuer"])
    circular.refresh_from_db()
    api.force_authenticate(cast["hr"])
    assert api.post(f"/api/v1/circulars/{circular.id}/archive/").status_code == 200
    circular.refresh_from_db()
    assert circular.status == Status.ARCHIVED
    assert circular.archived_at is not None


@pytest.mark.django_db
def test_an_archived_circular_refuses_every_mutation(api, cast, draft):
    from .conftest import drive_to_broadcast

    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["hr"])
    api.post(f"/api/v1/circulars/{circular.id}/archive/")
    circular.refresh_from_db()

    for actor in (cast["author"], cast["admin"], cast["hr"]):
        api.force_authenticate(actor)
        assert api.patch(f"/api/v1/circulars/{circular.id}/",
                         {"subject": "Rewritten"},
                         format="json").status_code == 403
        assert api.delete(f"/api/v1/circulars/{circular.id}/").status_code == 403
        assert api.post(f"/api/v1/circulars/{circular.id}/cancel/",
                        {"remarks": "Trying to unwind the record."},
                        format="json").status_code == 400


# ---------------------------------------------------------------------------
# Content import
# ---------------------------------------------------------------------------
def _memo_section(memo, body, title="Background"):
    """Give a memo the one content block these tests import from."""
    from memos.models import MemoSection
    return MemoSection.objects.create(memo=memo, position=0, title=title, body=body)


@pytest.mark.django_db
def test_content_can_be_imported_from_a_memo_and_edited_afterwards(api, cast,
                                                                    draft):
    from memos.models import Memo
    from memos.services import generate_memo_number

    memo = Memo.objects.create(
        subject="Revised office hours",
        memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
        created_by=cast["author"],
        memo_number=generate_memo_number(Memo.MemoType.GENERAL))
    _memo_section(
        memo, "<p>The committee agreed revised hours of 09:00 to 17:00.</p>")

    api.force_authenticate(cast["author"])
    imported = api.post(f"/api/v1/circulars/{draft.id}/import/",
                        {"memo_id": str(memo.id)}, format="json")
    assert imported.status_code == 200, imported.data
    assert "09:00 to 17:00" in imported.data["content"]
    assert imported.data["memo_reference_label"] == memo.memo_number

    # Editable after import, per the brief - and NOT a live link: editing the memo
    # afterwards must not change what the circular says.
    edited = api.patch(f"/api/v1/circulars/{draft.id}/",
                       {"content": "<p>Amended before issue.</p>"}, format="json")
    assert edited.status_code == 200
    section = memo.sections.first()
    section.body = "<p>Completely different text.</p>"
    section.save(update_fields=["body"])
    draft.refresh_from_db()
    assert draft.content == "<p>Amended before issue.</p>"


@pytest.mark.django_db
def test_importing_a_memo_you_cannot_read_is_refused(api, cast, draft):
    """Importing is a READ of the source; the endpoint must not launder access."""
    from memos.models import Memo
    from memos.services import generate_memo_number

    secret = Memo.objects.create(
        subject="Confidential",
        memo_type=Memo.MemoType.CONFIDENTIAL,
        status=Memo.Status.DRAFT, created_by=cast["outsider"],
        memo_number=generate_memo_number(Memo.MemoType.CONFIDENTIAL))

    api.force_authenticate(cast["author"])
    refused = api.post(f"/api/v1/circulars/{draft.id}/import/",
                       {"memo_id": str(secret.id)}, format="json")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_import_names_exactly_one_source(api, cast, draft):
    api.force_authenticate(cast["author"])
    neither = api.post(f"/api/v1/circulars/{draft.id}/import/", {}, format="json")
    assert neither.status_code == 400
    assert "exactly one source" in str(neither.data)


@pytest.mark.django_db
def test_content_cannot_be_imported_over_an_issued_circular(api, cast, draft):
    from memos.models import Memo
    from memos.services import generate_memo_number

    memo = Memo.objects.create(
        subject="X", memo_type=Memo.MemoType.GENERAL,
        status=Memo.Status.DRAFT, created_by=cast["author"],
        memo_number=generate_memo_number(Memo.MemoType.GENERAL))
    circular = drive_to_issued(api, draft, (cast["issuer"], "issuer"))
    api.force_authenticate(cast["author"])
    refused = api.post(f"/api/v1/circulars/{circular.id}/import/",
                       {"memo_id": str(memo.id)}, format="json")
    assert refused.status_code == 403


# ---------------------------------------------------------------------------
# Content safety
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_circular_content_is_sanitized_on_write(api, cast):
    api.force_authenticate(cast["author"])
    created = api.post("/api/v1/circulars/", {
        "subject": "Test",
        "content": '<p>Fine</p><script>alert(1)</script>'
                   '<img src="x" onerror="alert(2)">',
    }, format="json")
    assert created.status_code == 201, created.data
    content = created.data["content"]
    assert "<script>" not in content
    assert "onerror" not in content
    assert "Fine" in content
