"""
The minute lifecycle, as the E-minute manual defines it.

These tests are written against the MANUAL, not against the previous implementation.
Where the two disagreed the manual won, so the first class here pins the thing that
changed: a minute has no approver, and the only way it reaches Archived is that every
member present acknowledged it.

The OTP the manual puts in front of acknowledging (p.9) is excluded by instruction, so
`acknowledge` takes remarks and nothing else. Its absence is asserted, so nobody
reintroduces it by accident.
"""
import datetime

import pytest

from minutes.models import Minute, MinuteParticipant
from .conftest import (  # noqa: F401  (fixtures)
    LIST, acknowledge, api, cast, make_minute, minute, open_round,
    send_for_acknowledgement, send_for_review, set_members, taxonomy,
)


def detail(api, actor, minute):
    api.force_authenticate(actor)
    response = api.get(f"{LIST}{minute.id}/")
    assert response.status_code == 200, response.data
    return response.data


# ---------------------------------------------------------------------------
# The shape of the workflow
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestThereIsNoApprovalChain:
    """
    The manual gives a draft exactly two buttons - Submit for Draft Review and Submit
    for Acknowledge (p.7) - and four states. Anything that would let a minute be
    "approved" is gone.
    """

    def test_the_status_vocabulary_is_the_manuals_four(self):
        assert [s.value for s in Minute.Status] == [
            "draft", "draft_for_review", "pending_acknowledgement", "archived"]

    def test_no_approval_endpoints_are_exposed(self, api, minute, cast):
        api.force_authenticate(cast["initiator"])
        for path in ("act", "matrix", "withdraw"):
            response = api.post(f"{LIST}{minute.id}/{path}/", {}, format="json")
            assert response.status_code == 404, f"{path} should not exist"

    def test_the_detail_payload_offers_no_approval_capability(self, api, minute, cast):
        data = detail(api, cast["initiator"], minute)
        assert "can_act" not in data
        assert "workflow_steps" not in data
        assert {"can_send_for_review", "can_send_for_acknowledgement",
                "can_acknowledge"} <= set(data)


# ---------------------------------------------------------------------------
# Create / edit / delete (manual pp. 4-6)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCreateAndEdit:
    def test_creates_with_the_manuals_fields(self, api, cast, taxonomy):
        api.force_authenticate(cast["initiator"])
        response = api.post(LIST, {
            "subject": "Weekly departmental",
            "minute_type": str(taxonomy["department"].id),
            "meeting_date": "2026-08-20",
            "meeting_time": "10:00",
            "fro_id": str(cast["fro"].id),
            "agenda_body": "<p>Agenda</p>",
        }, format="json")
        assert response.status_code == 201, response.data
        assert response.data["status"] == "draft"
        assert response.data["minute_number"].startswith("MIN-")
        assert response.data["fro"]["id"] == str(cast["fro"].id)

    def test_time_is_required(self, api, cast, taxonomy):
        """Date* and Time* are the two starred fields on the form (p.4)."""
        api.force_authenticate(cast["initiator"])
        response = api.post(LIST, {
            "minute_type": str(taxonomy["department"].id),
            "meeting_date": "2026-08-20",
        }, format="json")
        assert response.status_code == 400
        assert "meeting_time" in response.data

    def test_subject_is_optional(self, api, cast, taxonomy):
        """The manual's form has no Subject; the lists identify by type and date."""
        api.force_authenticate(cast["initiator"])
        response = api.post(LIST, {
            "minute_type": str(taxonomy["department"].id),
            "meeting_date": "2026-08-20",
            "meeting_time": "09:00",
        }, format="json")
        assert response.status_code == 201, response.data
        assert response.data["title"].startswith("DEPARTMENT")

    def test_agenda_html_is_sanitized_on_the_way_in(self, api, cast, taxonomy):
        api.force_authenticate(cast["initiator"])
        response = api.post(LIST, {
            "minute_type": str(taxonomy["department"].id),
            "meeting_date": "2026-08-20", "meeting_time": "09:00",
            "agenda_body": '<p>ok</p><script>alert(1)</script>',
        }, format="json")
        assert response.status_code == 201, response.data
        assert "<script" not in response.data["agenda_body"]
        assert "ok" in response.data["agenda_body"]

    def test_a_draft_can_be_deleted_by_its_author(self, api, minute, cast):
        api.force_authenticate(cast["initiator"])
        assert api.delete(f"{LIST}{minute.id}/").status_code == 204
        assert not Minute.objects.filter(pk=minute.pk).exists()

    def test_the_author_may_edit_and_delete_even_an_archived_minute(
            self, api, minute, cast):
        """
        Policy update (Sept 2026): the person who raised a minute may edit and
        delete it at ANY stage, archived included. A non-author still cannot.
        """
        open_round(api, minute, (cast["member_a"], "present"))
        assert acknowledge(api, cast["member_a"], minute).status_code == 200
        minute.refresh_from_db()
        assert minute.status == Minute.Status.ARCHIVED

        # A participant who did not raise it is still refused.
        api.force_authenticate(cast["member_a"])
        assert api.patch(f"{LIST}{minute.id}/", {"subject": "x"},
                         format="json").status_code == 403
        assert api.delete(f"{LIST}{minute.id}/").status_code == 403

        # The author may now edit it, and delete it.
        api.force_authenticate(cast["initiator"])
        assert api.patch(f"{LIST}{minute.id}/", {"subject": "Corrected subject"},
                         format="json").status_code == 200
        assert api.delete(f"{LIST}{minute.id}/").status_code == 204
        assert not Minute.objects.filter(pk=minute.pk).exists()


# ---------------------------------------------------------------------------
# Members Present / Absent / Invitee (manual p.4)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestMembers:
    def test_records_the_three_groups(self, api, minute, cast):
        set_members(api, minute,
                    (cast["member_a"], "present"),
                    (cast["absentee"], "absent"),
                    (cast["invitee"], "invitee"))
        groups = {p.user_id: p.attendance for p in minute.participants.all()}
        assert groups[cast["member_a"].id] == "present"
        assert groups[cast["absentee"].id] == "absent"
        assert groups[cast["invitee"].id] == "invitee"

    def test_the_list_is_frozen_once_the_round_opens(self, api, minute, cast):
        """
        Adding a present member mid-round would change the denominator of a tally
        people have already answered - and could un-archive a completed minute.
        """
        open_round(api, minute, (cast["member_a"], "present"))
        api.force_authenticate(cast["initiator"])
        response = api.post(
            f"{LIST}{minute.id}/participants/",
            {"participants": [{"user_id": str(cast["member_b"].id),
                               "attendance": "present"}]}, format="json")
        assert response.status_code == 403

    def test_one_person_cannot_appear_twice(self, api, minute, cast):
        api.force_authenticate(cast["initiator"])
        response = api.post(
            f"{LIST}{minute.id}/participants/",
            {"participants": [
                {"user_id": str(cast["member_a"].id), "attendance": "present"},
                {"user_id": str(cast["member_a"].id), "attendance": "absent"},
            ]}, format="json")
        assert response.status_code == 400
        assert "more than once" in str(response.data)


# ---------------------------------------------------------------------------
# Submit for Draft Review (manual p.7)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestDraftReview:
    def test_sends_the_draft_to_the_fro(self, api, minute, cast):
        minute.fro = cast["fro"]
        minute.fro_name = "Min Fro T"
        minute.save(update_fields=["fro", "fro_name"])

        assert send_for_review(api, minute).status_code == 200
        minute.refresh_from_db()
        assert minute.status == Minute.Status.DRAFT_FOR_REVIEW
        assert minute.sent_for_review_at is not None

    def test_refuses_when_no_fro_was_chosen(self, api, minute, cast):
        """The manual's FRO field is where a draft review goes; without one there is
        nowhere to send it."""
        assert minute.fro_id is None
        assert send_for_review(api, minute).status_code == 403

    def test_the_fro_can_return_it_to_the_author(self, api, minute, cast):
        minute.fro = cast["fro"]
        minute.save(update_fields=["fro"])
        send_for_review(api, minute)

        api.force_authenticate(cast["fro"])
        response = api.post(f"{LIST}{minute.id}/return-review/",
                            {"remarks": "Please add the attendance."}, format="json")
        assert response.status_code == 200, response.data
        minute.refresh_from_db()
        assert minute.status == Minute.Status.DRAFT

    def test_somebody_else_cannot_return_it(self, api, minute, cast):
        """
        A member who can READ the minute still cannot act as its reviewer. Tested with
        a participant rather than a stranger on purpose: a stranger is refused by the
        visibility filter with a 404, which proves nothing about the review rule.
        """
        minute.fro = cast["fro"]
        minute.save(update_fields=["fro"])
        set_members(api, minute, (cast["member_a"], "present"))
        send_for_review(api, minute)

        api.force_authenticate(cast["member_a"])
        assert api.post(f"{LIST}{minute.id}/return-review/", {},
                        format="json").status_code == 403

    def test_review_is_optional(self, api, minute, cast):
        """Both buttons sit on the draft together (p.7): review can be skipped."""
        set_members(api, minute, (cast["member_a"], "present"))
        assert send_for_acknowledgement(api, minute).status_code == 200
        assert minute.status == Minute.Status.PENDING_ACKNOWLEDGEMENT


# ---------------------------------------------------------------------------
# Submit for Acknowledge, and archival (manual pp. 8-10)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestAcknowledgement:
    def test_assigns_only_to_the_members_present(self, api, minute, cast):
        """
        "the minute will be assigned to the members present in the meeting" (p.8).
        Absent members and invitees are on the record but are not chased.
        """
        open_round(api, minute,
                   (cast["member_a"], "present"),
                   (cast["absentee"], "absent"),
                   (cast["invitee"], "invitee"))
        states = {p.user_id: p.ack_status for p in minute.participants.all()}
        assert states[cast["member_a"].id] == MinuteParticipant.AckStatus.PENDING
        assert states[cast["absentee"].id] == MinuteParticipant.AckStatus.NOT_REQUIRED
        assert states[cast["invitee"].id] == MinuteParticipant.AckStatus.NOT_REQUIRED

    def test_refuses_to_open_a_round_with_nobody_present(self, api, minute, cast):
        set_members(api, minute, (cast["absentee"], "absent"))
        response = send_for_acknowledgement(api, minute)
        assert response.status_code == 403
        assert minute.status == Minute.Status.DRAFT

    def test_archives_only_when_every_present_member_has_answered(
            self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"), (cast["member_b"], "present"))

        assert acknowledge(api, cast["member_a"], minute).status_code == 200
        assert minute.status == Minute.Status.PENDING_ACKNOWLEDGEMENT, \
            "one of two is not all of them"

        assert acknowledge(api, cast["member_b"], minute).status_code == 200
        assert minute.status == Minute.Status.ARCHIVED
        assert minute.archived_at is not None

    def test_an_absent_member_is_not_asked_and_cannot_acknowledge(
            self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"), (cast["absentee"], "absent"))
        assert acknowledge(api, cast["absentee"], minute).status_code == 403
        # ...and their silence does not hold the minute open.
        assert acknowledge(api, cast["member_a"], minute).status_code == 200
        assert minute.status == Minute.Status.ARCHIVED

    def test_a_stranger_cannot_acknowledge(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        assert acknowledge(api, cast["stranger"], minute).status_code in (403, 404)

    def test_acknowledging_twice_is_refused(self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"), (cast["member_b"], "present"))
        assert acknowledge(api, cast["member_a"], minute).status_code == 200
        assert acknowledge(api, cast["member_a"], minute).status_code == 403

    def test_no_otp_is_required_or_accepted(self, api, minute, cast):
        """
        The manual emails a one-time password before recording an acknowledgement
        (p.9). That step is excluded here: the acknowledgement is recorded on the
        first request, with no challenge and no second round trip.
        """
        open_round(api, minute, (cast["member_a"], "present"))
        api.force_authenticate(cast["member_a"])
        response = api.post(f"{LIST}{minute.id}/acknowledge/", {}, format="json")
        assert response.status_code == 200, response.data
        minute.refresh_from_db()
        assert minute.status == Minute.Status.ARCHIVED

    def test_remarks_are_kept_against_the_member(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        acknowledge(api, cast["member_a"], minute, remarks="Noted in full.")
        row = minute.participants.get(user=cast["member_a"])
        assert row.remarks == "Noted in full."
        assert row.acknowledged_at is not None

    def test_reminds_only_those_outstanding(self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"), (cast["member_b"], "present"))
        acknowledge(api, cast["member_a"], minute)

        api.force_authenticate(cast["initiator"])
        response = api.post(f"{LIST}{minute.id}/remind-acknowledgements/", {},
                            format="json")
        assert response.status_code == 200, response.data
        assert response.data["reminded"] == 1


# ---------------------------------------------------------------------------
# Signature blocks (manual pp. 8-9)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSignatureBlocks:
    def test_stamps_acknowledged_and_absent(self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"),
                   (cast["member_b"], "present"),
                   (cast["absentee"], "absent"))
        acknowledge(api, cast["member_a"], minute)

        blocks = {b["name"]: b["stamp"]
                  for b in detail(api, cast["initiator"], minute)["signatures"]}
        assert blocks["Min Member A T"] == "ACKNOWLEDGED"
        assert blocks["Min Absentee T"] == "ABSENT"
        # Present but not yet answered: a blank block to sign in.
        assert blocks["Min Member B T"] == ""


# ---------------------------------------------------------------------------
# Reference No. search (manual p.5)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestReferenceLookup:
    def test_returns_the_previous_minutes_agenda(self, api, cast, taxonomy):
        previous = make_minute(cast, taxonomy,
                               agenda_body="<p>Last month's decisions.</p>")
        api.force_authenticate(cast["initiator"])
        response = api.get(f"{LIST}reference-lookup/",
                           {"reference": previous.minute_number})
        assert response.status_code == 200, response.data
        assert response.data["agenda_body"] == "<p>Last month's decisions.</p>"

    def test_will_not_surface_a_minute_the_caller_cannot_open(
            self, api, cast, taxonomy):
        previous = make_minute(cast, taxonomy)
        api.force_authenticate(cast["stranger"])
        response = api.get(f"{LIST}reference-lookup/",
                           {"reference": previous.minute_number})
        assert response.status_code == 404

    def test_unknown_reference_is_a_404_not_an_empty_200(self, api, cast, taxonomy):
        api.force_authenticate(cast["initiator"])
        assert api.get(f"{LIST}reference-lookup/",
                       {"reference": "MIN-1999-000001"}).status_code == 404


# ---------------------------------------------------------------------------
# Queues (manual p.3 menus, plus this HRMS's consolidated two)
# ---------------------------------------------------------------------------
def ids(response):
    rows = response.data.get("results", response.data)
    return {row["id"] for row in rows}


def fetch(api, user, scope):
    api.force_authenticate(user)
    response = api.get(LIST, {"scope": scope})
    assert response.status_code == 200, response.data
    return response


@pytest.mark.django_db
class TestQueues:
    def test_draft_for_review_lists_what_is_with_the_fro(self, api, minute, cast):
        minute.fro = cast["fro"]
        minute.save(update_fields=["fro"])
        send_for_review(api, minute)
        assert str(minute.id) in ids(fetch(api, cast["fro"], "draft_for_review"))

    def test_my_acknowledgements_lists_only_what_is_outstanding(
            self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        assert str(minute.id) in ids(
            fetch(api, cast["member_a"], "my_acknowledgements"))

        acknowledge(api, cast["member_a"], minute)
        assert str(minute.id) not in ids(
            fetch(api, cast["member_a"], "my_acknowledgements"))
        assert str(minute.id) in ids(fetch(api, cast["member_a"], "acknowledged"))

    def test_needs_me_covers_both_kinds_of_outstanding_work(self, api, cast, taxonomy):
        review = make_minute(cast, taxonomy, subject="To review", fro=cast["fro"])
        send_for_review(api, review)
        to_ack = make_minute(cast, taxonomy, subject="To acknowledge")
        open_round(api, to_ack, (cast["fro"], "present"))

        listed = ids(fetch(api, cast["fro"], "needs_me"))
        assert {str(review.id), str(to_ack.id)} <= listed

    def test_needs_me_counts_one_minute_once(self, api, cast, taxonomy):
        """
        The FRO is also a member present. The badge is one count, so the queue must
        not list the same minute twice.
        """
        subject = make_minute(cast, taxonomy, fro=cast["fro"])
        open_round(api, subject, (cast["fro"], "present"))
        response = fetch(api, cast["fro"], "needs_me")
        rows = response.data.get("results", response.data)
        assert [r["id"] for r in rows].count(str(subject.id)) == 1

        api.force_authenticate(cast["fro"])
        counts = api.get(f"{LIST}dashboard/")
        assert counts.data["needs_my_action"] == len(rows)

    def test_mine_includes_drafts_and_archived(self, api, cast, taxonomy):
        draft = make_minute(cast, taxonomy, subject="Still a draft")
        done = make_minute(cast, taxonomy, subject="Finished")
        open_round(api, done, (cast["member_a"], "present"))
        acknowledge(api, cast["member_a"], done)

        listed = ids(fetch(api, cast["initiator"], "mine"))
        assert {str(draft.id), str(done.id)} <= listed

    def test_archived_holds_the_completed_record(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        acknowledge(api, cast["member_a"], minute)
        assert str(minute.id) in ids(fetch(api, cast["initiator"], "archived"))


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestVisibility:
    def test_a_stranger_cannot_open_it(self, api, minute, cast):
        api.force_authenticate(cast["stranger"])
        assert api.get(f"{LIST}{minute.id}/").status_code in (403, 404)

    def test_the_fro_can_open_it(self, api, minute, cast):
        minute.fro = cast["fro"]
        minute.save(update_fields=["fro"])
        api.force_authenticate(cast["fro"])
        assert api.get(f"{LIST}{minute.id}/").status_code == 200

    def test_a_member_can_open_it(self, api, minute, cast):
        set_members(api, minute, (cast["member_a"], "present"))
        api.force_authenticate(cast["member_a"])
        assert api.get(f"{LIST}{minute.id}/").status_code == 200

    def test_hr_reads_everything(self, api, minute, cast):
        api.force_authenticate(cast["hr"])
        assert api.get(f"{LIST}{minute.id}/").status_code == 200


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestAuditTrail:
    def test_records_every_transition_in_order(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        acknowledge(api, cast["member_a"], minute)

        api.force_authenticate(cast["initiator"])
        response = api.get(f"{LIST}{minute.id}/audit-trail/")
        assert response.status_code == 200, response.data
        actions = [row["action"] for row in response.data]
        assert actions[0] == "created"
        for expected in ("participants_changed", "sent_for_ack", "acknowledged",
                         "archived"):
            assert expected in actions, f"{expected} missing from {actions}"

    def test_a_stranger_cannot_read_it(self, api, minute, cast):
        set_members(api, minute, (cast["member_a"], "present"))
        api.force_authenticate(cast["member_a"])
        # A member may read the minute but not its audit trail.
        assert api.get(f"{LIST}{minute.id}/audit-trail/").status_code == 403


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestExports:
    def test_pdf_renders(self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"), (cast["absentee"], "absent"))
        acknowledge(api, cast["member_a"], minute)

        api.force_authenticate(cast["initiator"])
        response = api.get(f"{LIST}{minute.id}/pdf/")
        assert response.status_code == 200, response.content[:400]
        assert response["Content-Type"] == "application/pdf"
        assert response.content[:4] == b"%PDF"

    def test_acknowledgement_sheet_renders(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        api.force_authenticate(cast["initiator"])
        response = api.get(f"{LIST}{minute.id}/acknowledgement-sheet/")
        assert response.status_code == 200, response.content[:400]
        assert response.content[:4] == b"%PDF"

    def test_excel_renders(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        api.force_authenticate(cast["initiator"])
        response = api.get(f"{LIST}{minute.id}/excel/")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"  # xlsx is a zip

    def test_a_stranger_cannot_export(self, api, minute, cast):
        api.force_authenticate(cast["stranger"])
        assert api.get(f"{LIST}{minute.id}/pdf/").status_code in (403, 404)


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestInvalidTransitions:
    def test_cannot_submit_for_acknowledgement_twice(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        assert send_for_acknowledgement(api, minute).status_code == 403

    def test_cannot_send_an_open_round_for_review(self, api, minute, cast):
        minute.fro = cast["fro"]
        minute.save(update_fields=["fro"])
        open_round(api, minute, (cast["member_a"], "present"))
        assert send_for_review(api, minute).status_code == 403

    def test_cannot_acknowledge_a_draft(self, api, minute, cast):
        set_members(api, minute, (cast["member_a"], "present"))
        assert acknowledge(api, cast["member_a"], minute).status_code == 403

    def test_cannot_remind_when_nothing_is_outstanding(self, api, minute, cast):
        open_round(api, minute, (cast["member_a"], "present"))
        acknowledge(api, cast["member_a"], minute)
        api.force_authenticate(cast["initiator"])
        assert api.post(f"{LIST}{minute.id}/remind-acknowledgements/", {},
                        format="json").status_code == 403


@pytest.mark.django_db
class TestProgressTrail:
    def test_the_last_stage_reads_done_on_an_archived_minute(self, api, minute, cast):
        """
        Archived is terminal. Reporting it as the "active" stage made a finished
        minute look as though something were still in progress - caught in a
        screenshot, so it is pinned here.
        """
        open_round(api, minute, (cast["member_a"], "present"))
        acknowledge(api, cast["member_a"], minute)

        stages = detail(api, cast["initiator"], minute)["tracker"]
        assert {s["key"]: s["state"] for s in stages}["archived"] == "done"
        assert all(s["state"] == "done" for s in stages)

    def test_shows_the_review_stage_only_when_there_is_an_fro(
            self, api, minute, cast, taxonomy):
        keys = [s["key"] for s in detail(api, cast["initiator"], minute)["tracker"]]
        assert "review" not in keys

        with_fro = make_minute(cast, taxonomy, fro=cast["fro"])
        keys = [s["key"] for s in detail(api, cast["initiator"], with_fro)["tracker"]]
        assert "review" in keys


@pytest.mark.django_db
class TestDocumentOutput:
    """
    What the PDF actually SAYS, not merely that it rendered.

    Asserted against the rendered HTML rather than the PDF bytes: WeasyPrint subsets
    its fonts, so scraping the text layer out of the binary finds fragments and proves
    nothing either way.
    """

    def render(self, minute, template="pdf/minute.html"):
        from django.template.loader import render_to_string
        from documents.pdf import common_context
        from minutes.exports import (
            build_acknowledgement_sheet_context, build_pdf_context,
        )
        context = common_context(minute.minute_number)
        context.update(build_pdf_context(minute)
                       if template == "pdf/minute.html"
                       else build_acknowledgement_sheet_context(minute))
        return render_to_string(template, context)

    def test_the_minute_pdf_carries_the_manuals_sections(self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"),
                   (cast["member_b"], "present"),
                   (cast["absentee"], "absent"),
                   (cast["invitee"], "invitee"))
        acknowledge(api, cast["member_a"], minute)

        html = self.render(minute)
        for needle in ("Date:", "Time:", "Minute Type:", "Members Present:",
                       "Members Absent:", "Invitee Members:",
                       "Meeting Agenda/Discussion/Decisions"):
            assert needle in html, f"{needle} missing from the minute PDF"

        # The two stamps the manual shows on the signature blocks (pp. 8-9).
        assert "ACKNOWLEDGED" in html
        assert "ABSENT" in html
        # The agenda table the secretary typed survives into the document.
        assert "<table" in html

    def test_the_pdf_names_every_member_in_the_right_group(self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"), (cast["absentee"], "absent"))
        html = self.render(minute)
        present = html.index("Members Present:")
        absent = html.index("Members Absent:")
        assert html.index("Min Member A T") > present
        assert html.index("Min Absentee T") > absent

    def test_the_signature_sheet_rules_a_column_per_member(self, api, minute, cast):
        open_round(api, minute,
                   (cast["member_a"], "present"), (cast["absentee"], "absent"))
        html = self.render(minute, "pdf/minute_acknowledgement.html")
        assert "Acknowledgement Sheet" in html
        assert "Signature" in html
        assert html.count("<tr>") >= 3  # header plus one row per member


@pytest.mark.django_db
def test_the_timeline_names_who_acted(api, minute, cast):
    """
    The timeline feeds the shared WorkflowTimeline component, which reads `actor_name`.
    Emitting the name under a different key rendered a history of blank actors.
    """
    open_round(api, minute, (cast["member_a"], "present"))
    acknowledge(api, cast["member_a"], minute)

    timeline = detail(api, cast["initiator"], minute)["timeline"]
    assert timeline, "a minute always has at least its creation event"
    assert all("actor_name" in row for row in timeline)
    assert any(row["actor_name"] == "Min Member A T" for row in timeline)
