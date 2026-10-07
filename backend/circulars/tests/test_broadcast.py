"""
The broadcast engine: audience resolution, recipients, read tracking and
acknowledgement.

This is the part of the module that is not a memo, so it is the part most worth
testing hard. The questions are set-shaped rather than sequence-shaped: who was
reached, who was reached twice, who was reached and should not have been.
"""
import pytest
from django.contrib.auth.models import Group

from circulars.models import (
    Circular, CircularAcknowledgement, CircularBroadcast, CircularReadLog,
)
from leaves.models import Department
from users.models import User

from .conftest import broadcast, drive_to_broadcast, drive_to_issued, make_circular

Status = Circular.Status
AckState = CircularAcknowledgement.State


@pytest.fixture
def issued(api, cast, draft):
    return drive_to_issued(api, draft, (cast["issuer"], "issuer"))


@pytest.fixture
def ack_circular(cast):
    return make_circular(cast, acknowledgement_required=True,
                         acknowledgement_due_days=5,
                         subject="Revised code of conduct — acknowledgement required")


# ---------------------------------------------------------------------------
# Audience
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_broadcasting_to_the_organisation_reaches_everyone_but_the_issuer(
        api, cast, issued):
    response = broadcast(api, issued, cast["issuer"])
    assert response.status_code == 200, response.data
    issued.refresh_from_db()

    reached = set(issued.recipients.values_list("user_id", flat=True))
    everyone = set(User.objects.filter(is_active=True).values_list("id", flat=True))
    # The issuer is excluded: they do not need telling about their own circular,
    # and counting them would make every read percentage start at 1/n.
    assert reached == everyone - {cast["issuer"].id}
    assert issued.status == Status.BROADCASTED
    assert issued.broadcast_at is not None


@pytest.mark.django_db
def test_broadcasting_to_a_department_reaches_only_that_department(api, cast,
                                                                    issued):
    finance = Department.objects.create(name="Finance Unit", code="P50-FIN")
    logistics = Department.objects.create(name="Logistics Unit", code="P50-LOG")
    for user, dept in ((cast["staff_a"], finance), (cast["staff_b"], logistics)):
        user.department_ref = dept
        user.save(update_fields=["department_ref"])

    response = broadcast(api, issued, cast["issuer"], audience="departments",
                         department_ids=[str(finance.id)])
    assert response.status_code == 200, response.data
    issued.refresh_from_db()
    reached = set(issued.recipients.values_list("user_id", flat=True))
    assert cast["staff_a"].id in reached
    assert cast["staff_b"].id not in reached


@pytest.mark.django_db
def test_a_department_broadcast_reaches_sub_units_when_asked(api, cast, issued):
    """
    leaves.Department is self-nesting, so a "unit" and a "sub-unit" are department
    rows with parents - which is why the brief's three audience levels are one
    target type here rather than three.
    """
    parent = Department.objects.create(name="Operations", code="P50-OPS")
    unit = Department.objects.create(name="Operations - Logistics",
                                     code="P50-OPS-LOG", parent=parent)
    cast["staff_a"].department_ref = unit
    cast["staff_a"].save(update_fields=["department_ref"])

    response = broadcast(api, issued, cast["issuer"], audience="departments",
                         department_ids=[str(parent.id)], include_children=True)
    assert response.status_code == 200
    issued.refresh_from_db()
    assert issued.recipients.filter(user=cast["staff_a"]).exists()


@pytest.mark.django_db
def test_a_department_broadcast_stops_at_the_named_level_when_told_to(api, cast,
                                                                      issued):
    parent = Department.objects.create(name="Corporate", code="P50-CORP")
    unit = Department.objects.create(name="Corporate - Legal", code="P50-CORP-LEG",
                                     parent=parent)
    cast["staff_a"].department_ref = unit
    cast["staff_a"].save(update_fields=["department_ref"])
    cast["staff_b"].department_ref = parent
    cast["staff_b"].save(update_fields=["department_ref"])

    broadcast(api, issued, cast["issuer"], audience="departments",
              department_ids=[str(parent.id)], include_children=False)
    issued.refresh_from_db()
    assert issued.recipients.filter(user=cast["staff_b"]).exists()
    assert not issued.recipients.filter(user=cast["staff_a"]).exists()


@pytest.mark.django_db
def test_broadcasting_to_a_group_reaches_its_members_only(api, cast, issued):
    auditors = Group.objects.create(name="Internal Auditors")
    cast["staff_a"].groups.add(auditors)

    broadcast(api, issued, cast["issuer"], audience="groups",
              group_ids=[auditors.id])
    issued.refresh_from_db()
    reached = set(issued.recipients.values_list("user_id", flat=True))
    assert reached == {cast["staff_a"].id}


@pytest.mark.django_db
def test_broadcasting_to_selected_employees_reaches_exactly_them(api, cast, issued):
    broadcast(api, issued, cast["issuer"], audience="employees",
              employee_ids=[str(cast["staff_a"].id), str(cast["staff_b"].id)])
    issued.refresh_from_db()
    assert set(issued.recipients.values_list("user_id", flat=True)) == {
        cast["staff_a"].id, cast["staff_b"].id}


@pytest.mark.django_db
def test_an_empty_audience_is_refused(api, cast, issued):
    """
    Reporting a delivery that did not happen is worse than refusing to send.
    """
    empty = Group.objects.create(name="Nobody At All")
    response = broadcast(api, issued, cast["issuer"], audience="groups",
                         group_ids=[empty.id])
    assert response.status_code == 400
    assert "resolves to nobody" in str(response.data)


@pytest.mark.django_db
def test_an_inactive_employee_is_never_a_recipient(api, cast, issued):
    """
    A disabled account would create an unread row nobody can ever clear, which
    makes every unread figure permanently wrong.
    """
    cast["staff_b"].is_active = False
    cast["staff_b"].save(update_fields=["is_active"])
    broadcast(api, issued, cast["issuer"])
    issued.refresh_from_db()
    assert not issued.recipients.filter(user=cast["staff_b"]).exists()


@pytest.mark.django_db
def test_an_unissued_circular_cannot_be_broadcast(api, cast, draft):
    """Issuing is what makes a circular official; broadcasting an unofficial one
    would announce something nobody signed."""
    from .conftest import submit
    submit(api, draft, (cast["issuer"], "issuer"))
    response = broadcast(api, draft, cast["issuer"])
    assert response.status_code == 400
    assert "Only an issued circular" in str(response.data)


# ---------------------------------------------------------------------------
# Re-broadcast
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_extending_a_broadcast_adds_only_the_people_not_already_on_it(api, cast,
                                                                      issued):
    broadcast(api, issued, cast["issuer"], audience="employees",
              employee_ids=[str(cast["staff_a"].id)])
    issued.refresh_from_db()
    assert issued.recipients.count() == 1

    second = broadcast(api, issued, cast["issuer"], audience="employees",
                       employee_ids=[str(cast["staff_a"].id),
                                     str(cast["staff_b"].id)])
    assert second.status_code == 200
    issued.refresh_from_db()
    assert issued.recipients.count() == 2

    event = CircularBroadcast.objects.get(circular=issued, sequence=2)
    assert event.recipient_count == 1, "only the new person is counted as added"
    # Reported rather than hidden: "extended to two, one of whom already had it".
    assert event.already_present_count == 1


@pytest.mark.django_db
def test_a_person_appears_at_most_once_however_many_broadcasts_reach_them(
        api, cast, issued):
    broadcast(api, issued, cast["issuer"], audience="employees",
              employee_ids=[str(cast["staff_a"].id)])
    broadcast(api, issued, cast["issuer"])   # the whole organisation
    issued.refresh_from_db()
    assert issued.recipients.filter(user=cast["staff_a"]).count() == 1


@pytest.mark.django_db
def test_extending_does_not_move_the_original_broadcast_date(api, cast,
                                                              ack_circular):
    circular = drive_to_issued(api, ack_circular, (cast["issuer"], "issuer"))
    broadcast(api, circular, cast["issuer"], audience="employees",
              employee_ids=[str(cast["staff_a"].id)])
    circular.refresh_from_db()
    first_at = circular.broadcast_at
    first_due = CircularAcknowledgement.objects.get(
        recipient__user=cast["staff_a"]).due_date

    broadcast(api, circular, cast["issuer"], audience="employees",
              employee_ids=[str(cast["staff_b"].id)])
    circular.refresh_from_db()
    assert circular.broadcast_at == first_at, (
        "an extension must not restart the acknowledgement clock for the people "
        "who already had it")
    assert CircularAcknowledgement.objects.get(
        recipient__user=cast["staff_a"]).due_date == first_due


@pytest.mark.django_db
def test_the_audience_preview_counts_without_sending(api, cast, issued):
    api.force_authenticate(cast["issuer"])
    preview = api.post(f"/api/v1/circulars/{issued.id}/audience-preview/",
                       {"audience": "organisation"}, format="json")
    assert preview.status_code == 200
    assert preview.data["total"] > 0
    assert preview.data["already_present"] == 0
    issued.refresh_from_db()
    assert issued.recipients.count() == 0, "a preview must not send anything"
    assert issued.status == Status.ISSUED


# ---------------------------------------------------------------------------
# Read tracking
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_opening_a_circular_records_a_read(api, cast, draft):
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    api.get(f"/api/v1/circulars/{circular.id}/")

    log = CircularReadLog.objects.get(recipient__user=cast["staff_a"])
    assert log.view_count == 1
    assert log.opened_at is not None
    assert log.last_viewed_at == log.opened_at


@pytest.mark.django_db
def test_reopening_updates_the_last_view_but_never_the_first(api, cast, draft):
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    api.get(f"/api/v1/circulars/{circular.id}/")
    first = CircularReadLog.objects.get(recipient__user=cast["staff_a"])
    opened_at = first.opened_at

    api.get(f"/api/v1/circulars/{circular.id}/")
    again = CircularReadLog.objects.get(recipient__user=cast["staff_a"])
    assert again.view_count == 2
    assert again.opened_at == opened_at, "opened_at is the FIRST open, by definition"
    assert again.last_viewed_at >= opened_at


@pytest.mark.django_db
def test_the_author_reading_their_own_circular_is_not_counted(api, cast, draft):
    """
    The author and the issuer can open a circular without being in its audience,
    and record_read is a no-op for them. Counting them would make every read
    percentage report a reader who was never asked to read.
    """
    circular = drive_to_broadcast(api, draft, cast, audience="employees",
                                  employee_ids=[str(cast["staff_a"].id)])
    assert circular.recipients.count() == 1

    for actor in (cast["author"], cast["issuer"]):
        api.force_authenticate(actor)
        assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 200

    from circulars.broadcast import read_summary
    assert read_summary(circular) == {"total": 1, "read": 0, "unread": 1,
                                      "percent": 0}
    assert not CircularReadLog.objects.filter(
        recipient__circular=circular).exists()


@pytest.mark.django_db
def test_read_figures_are_counts_not_a_walk(api, cast, draft):
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    api.get(f"/api/v1/circulars/{circular.id}/")
    api.force_authenticate(cast["staff_b"])
    api.get(f"/api/v1/circulars/{circular.id}/")

    from circulars.broadcast import read_summary
    summary = read_summary(circular)
    assert summary["read"] == 2
    assert summary["unread"] == summary["total"] - 2
    assert summary["percent"] == round(2 * 100 / summary["total"])


@pytest.mark.django_db
def test_the_unread_scope_lists_what_i_have_not_opened(api, cast, draft):
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    unread = api.get("/api/v1/circulars/?scope=unread").data
    rows = unread.get("results", unread)
    assert str(circular.id) in [str(row["id"]) for row in rows]

    api.get(f"/api/v1/circulars/{circular.id}/")
    after = api.get("/api/v1/circulars/?scope=unread").data
    rows = after.get("results", after)
    assert str(circular.id) not in [str(row["id"]) for row in rows]


# ---------------------------------------------------------------------------
# Acknowledgement
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_acknowledgement_rows_are_created_only_when_the_circular_asks(api, cast,
                                                                      draft,
                                                                      ack_circular):
    plain = drive_to_broadcast(api, draft, cast)
    assert CircularAcknowledgement.objects.filter(
        recipient__circular=plain).count() == 0, (
        "an information-only circular carries no empty acknowledgement rows")

    asking = drive_to_broadcast(api, ack_circular, cast)
    assert CircularAcknowledgement.objects.filter(
        recipient__circular=asking).count() == asking.recipients.count()


@pytest.mark.django_db
def test_a_recipient_can_acknowledge_and_the_tally_moves(api, cast, ack_circular):
    circular = drive_to_broadcast(api, ack_circular, cast)
    api.force_authenticate(cast["staff_a"])
    response = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                        {"accept": True, "remarks": "Read and understood."},
                        format="json")
    assert response.status_code == 200, response.data
    assert response.data["acknowledgement"]["acknowledged"] == 1
    assert response.data["my_acknowledgement"]["state"] == AckState.ACKNOWLEDGED


@pytest.mark.django_db
def test_a_decline_needs_a_reason_and_an_accept_does_not(api, cast, ack_circular):
    """
    "I have read this" needs no explanation. "I have read this and do not accept
    it" is only useful to whoever reads the register if it says why.
    """
    circular = drive_to_broadcast(api, ack_circular, cast)
    api.force_authenticate(cast["staff_a"])
    silent = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                      {"accept": False, "remarks": "no"}, format="json")
    assert silent.status_code == 400

    declined = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                        {"accept": False,
                         "remarks": "The effective date clashes with my contract."},
                        format="json")
    assert declined.status_code == 200
    assert declined.data["acknowledgement"]["declined"] == 1

    api.force_authenticate(cast["staff_b"])
    accepted = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                        {"accept": True}, format="json")
    assert accepted.status_code == 200


@pytest.mark.django_db
def test_nobody_can_acknowledge_twice_or_revise_a_response(api, cast, ack_circular):
    circular = drive_to_broadcast(api, ack_circular, cast)
    api.force_authenticate(cast["staff_a"])
    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/", {"accept": True},
             format="json")
    again = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                     {"accept": False, "remarks": "Changed my mind entirely."},
                     format="json")
    assert again.status_code == 400
    assert "already responded" in str(again.data)


@pytest.mark.django_db
def test_a_non_recipient_cannot_acknowledge(api, cast, ack_circular):
    circular = drive_to_broadcast(api, ack_circular, cast, audience="employees",
                                  employee_ids=[str(cast["staff_a"].id)])
    api.force_authenticate(cast["staff_b"])
    refused = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                       {"accept": True}, format="json")
    # Not a recipient and the circular is confidential to its audience, so the
    # circular is not even visible.
    assert refused.status_code in (400, 404)


@pytest.mark.django_db
def test_acknowledgement_is_refused_on_a_circular_that_does_not_ask(api, cast,
                                                                    draft):
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    refused = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                       {"accept": True}, format="json")
    assert refused.status_code == 400
    assert "does not ask for an acknowledgement" in str(refused.data)


@pytest.mark.django_db
def test_the_four_acknowledgement_states_are_exclusive_and_sum_to_the_total(
        api, cast, ack_circular):
    """
    The defect this guards against is real and was found in another module: two
    summaries with two meanings of "pending" rendered side by side, reporting
    eleven states for six people.
    """
    circular = drive_to_broadcast(api, ack_circular, cast)
    api.force_authenticate(cast["staff_a"])
    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/", {"accept": True},
             format="json")
    api.force_authenticate(cast["staff_b"])
    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
             {"accept": False, "remarks": "I do not accept these terms."},
             format="json")

    from circulars.broadcast import acknowledgement_summary
    summary = acknowledgement_summary(circular)
    assert (summary["pending"] + summary["acknowledged"] + summary["declined"]
            == summary["total"])
    assert summary["acknowledged"] == 1
    assert summary["declined"] == 1


@pytest.mark.django_db
def test_late_is_an_overlay_not_a_fifth_bucket(api, cast, ack_circular):
    import datetime

    from django.utils import timezone

    circular = drive_to_broadcast(api, ack_circular, cast)
    # Push the deadline into the past for one recipient only.
    row = CircularAcknowledgement.objects.get(recipient__user=cast["staff_a"])
    row.due_date = timezone.localdate() - datetime.timedelta(days=1)
    row.save(update_fields=["due_date"])

    api.force_authenticate(cast["staff_a"])
    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/", {"accept": True},
             format="json")

    from circulars.broadcast import acknowledgement_summary
    summary = acknowledgement_summary(circular)
    assert summary["acknowledged"] == 1
    assert summary["late"] == 1, "a late acknowledgement is still an acknowledgement"
    # The four buckets still sum to the total; `late` does not join them.
    assert (summary["pending"] + summary["acknowledged"] + summary["declined"]
            == summary["total"])


@pytest.mark.django_db
def test_reminders_go_only_to_the_outstanding(api, cast, ack_circular):
    circular = drive_to_broadcast(api, ack_circular, cast)
    api.force_authenticate(cast["staff_a"])
    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/", {"accept": True},
             format="json")

    api.force_authenticate(cast["issuer"])
    reminded = api.post(f"/api/v1/circulars/{circular.id}/remind/")
    assert reminded.status_code == 200
    total = circular.recipients.count()
    assert reminded.data["reminded"] == total - 1

    assert CircularAcknowledgement.objects.get(
        recipient__user=cast["staff_a"]).reminded_at is None


@pytest.mark.django_db
def test_my_acknowledgements_scope_lists_what_is_outstanding_for_me(api, cast,
                                                                     ack_circular):
    circular = drive_to_broadcast(api, ack_circular, cast)
    api.force_authenticate(cast["staff_a"])
    listed = api.get("/api/v1/circulars/?scope=my_acknowledgements").data
    rows = listed.get("results", listed)
    assert str(circular.id) in [str(row["id"]) for row in rows]

    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/", {"accept": True},
             format="json")
    after = api.get("/api/v1/circulars/?scope=my_acknowledgements").data
    rows = after.get("results", after)
    assert str(circular.id) not in [str(row["id"]) for row in rows]


@pytest.mark.django_db
def test_an_archived_circular_can_still_be_acknowledged(api, cast, ack_circular):
    """
    Archiving files the circular; it does not close the round somebody was already
    asked to answer. Refusing here would leave a pending row nobody can ever clear.
    """
    circular = drive_to_broadcast(api, ack_circular, cast)
    api.force_authenticate(cast["hr"])
    api.post(f"/api/v1/circulars/{circular.id}/archive/")
    circular.refresh_from_db()

    api.force_authenticate(cast["staff_a"])
    response = api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
                        {"accept": True}, format="json")
    assert response.status_code == 200, response.data
