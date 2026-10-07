"""
Phase T5.7 / T5.8 — the appraisal evidence layer.

This is the most sensitive surface in the module: it produces the numbers a
future appraisal will be argued over. The tests therefore assert what it must
NOT do at least as hard as what it must.

  * No score, rating, band or weighting anywhere in the payload.
  * Every percentage travels with its denominator.
  * A snapshot is written once and never rewritten.
  * An employee reads their own evidence and NOBODY ELSE'S — and asking for
    somebody else's is refused, not answered with an empty page.
"""
import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from tasks import evidence as evidence_service
from tasks.models import EmployeeTaskEvidenceSnapshot, Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
ANALYTICS = "/api/v1/task-analytics/"
EVIDENCE = f"{ANALYTICS}evidence/"
SNAPSHOTS = f"{ANALYTICS}evidence/snapshots/"

FORBIDDEN_WORDS = ("score", "rating", "rank", "grade", "band", "percentile",
                   "weight", "index")


# ===========================================================================
# What it must not be
# ===========================================================================
def test_the_payload_contains_no_judgement_shaped_field(cast, auth, make_task):
    """
    The difference between an evidence service and an appraisal is structural,
    not a matter of tone. A weighting decided here would be a judgement an
    organisation never got to make openly.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(EVIDENCE).data
    for key in body:
        assert not any(word in key.lower() for word in FORBIDDEN_WORDS), key


def test_the_payload_says_out_loud_what_it_is_not(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(EVIDENCE).data
    assert "not an assessment" in body["disclaimer"]


def test_the_snapshot_model_has_no_judgement_column():
    """
    A future appraisal module may weigh these numbers; it must not find the
    decision already made for it here.
    """
    fields = {f.name for f in EmployeeTaskEvidenceSnapshot._meta.get_fields()}
    for name in fields:
        assert not any(word in name.lower() for word in FORBIDDEN_WORDS), name


def test_every_percentage_travels_with_its_denominator(cast, auth, make_task,
                                                       today):
    """
    A stored percentage with no denominator is the kind of number that gets
    quoted for years by somebody who never knew it was three tasks out of four.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED, due_date=today)
    Task.objects.filter(pk=task.pk).update(completed_at=timezone.now())

    body = auth(cast["employee"]).get(EVIDENCE).data
    assert body["completion_percent"] == 100
    assert body["completion_of"] == 1
    assert body["on_time_percent"] == 100
    assert body["completed_with_due_date"] == 1
    assert body["completed_on_time"] == 1


# ===========================================================================
# Visibility (Part 12)
# ===========================================================================
def test_an_employee_reads_their_own_evidence(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(EVIDENCE).data
    assert body["employee_id"] == str(cast["employee"].id)
    assert body["tasks_assigned"] == 1


def test_an_employee_cannot_read_anybody_elses(cast, auth, make_task):
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    response = auth(cast["employee"]).get(
        EVIDENCE, {"employee": str(cast["peer"].id)})
    assert response.status_code == 403


def test_a_refusal_is_not_an_empty_page(cast, auth, make_task):
    """
    An empty evidence page reads as "this person has done nothing", which is a
    different and damaging claim from "you may not see this".
    """
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    response = auth(cast["employee"]).get(
        EVIDENCE, {"employee": str(cast["peer"].id)})
    assert response.status_code == 403
    assert "tasks_assigned" not in response.data


def test_a_manager_reads_evidence_for_somebody_in_their_scope(cast, auth,
                                                              make_task,
                                                              departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    body = auth(cast["hod"]).get(
        EVIDENCE, {"employee": str(cast["employee"].id)}).data
    assert body["employee_id"] == str(cast["employee"].id)


def test_a_manager_cannot_reach_outside_their_scope(cast, auth, make_task,
                                                    departments):
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])
    assert auth(cast["hod"]).get(
        EVIDENCE, {"employee": str(cast["outsider"].id)}).status_code == 403


def test_hr_reads_anybody(cast, auth, make_task, departments):
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])
    assert auth(cast["hr"]).get(
        EVIDENCE, {"employee": str(cast["outsider"].id)}).status_code == 200


def test_evidence_requires_authentication(api):
    assert api.get(EVIDENCE).status_code in (401, 403)


# ===========================================================================
# The numbers (Part 7)
# ===========================================================================
def test_evidence_reports_everything_part_seven_names(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(EVIDENCE).data
    for key in ("tasks_assigned", "tasks_completed", "completion_percent",
                "average_completion_days", "on_time_percent",
                "reviews_performed", "checklist_items_completed",
                "evidence_files_submitted"):
        assert key in body, key


def test_average_completion_is_none_not_zero_when_nothing_finished(cast, auth,
                                                                   make_task):
    """"No data" and "same day" are different answers."""
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    assert auth(cast["employee"]).get(EVIDENCE).data[
        "average_completion_days"] is None


def test_review_participation_counts_decisions_made_not_received(cast, auth,
                                                                 make_task):
    """A reviewer's participation is what they decided, not what was decided
    about them."""
    task = make_task(cast["hr"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED)
    Task.objects.filter(pk=task.pk).update(completed_at=timezone.now())

    reviewer = auth(cast["hod"]).get(EVIDENCE).data
    assignee = auth(cast["employee"]).get(EVIDENCE).data
    assert reviewer["reviews_performed"] == 1
    assert assignee["reviews_performed"] == 0


def test_evidence_files_are_counted(cast, auth, make_task):
    import io

    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    payload = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
               + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
               + b"\x1f\x15\xc4\x89" + b"\x00" * 32)
    handle = io.BytesIO(payload)
    handle.name = "proof.png"
    auth(cast["employee"]).post(f"{LIST}{task.id}/attachments/",
                                {"files": handle}, format="multipart")

    assert auth(cast["employee"]).get(EVIDENCE).data[
        "evidence_files_submitted"] == 1


def test_the_window_can_be_narrowed(cast, auth, make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    far_past = (today - datetime.timedelta(days=300)).isoformat()
    body = auth(cast["employee"]).get(EVIDENCE, {
        "from": far_past, "to": (today - datetime.timedelta(days=200)).isoformat(),
    }).data
    assert body["tasks_assigned"] == 0


def test_a_backwards_window_is_refused(cast, auth, today):
    assert auth(cast["employee"]).get(EVIDENCE, {
        "from": today.isoformat(),
        "to": (today - datetime.timedelta(days=5)).isoformat(),
    }).status_code == 400


# ===========================================================================
# Snapshots (Part 8)
# ===========================================================================
# ---------------------------------------------------------------------------
# A note on `period_type="daily"` below.
#
# `snapshot_task_evidence` writes the DAILY row every night and, on the first of
# a month, also a closing MONTHLY row for the month that just ended (plus a
# quarterly one in Jan/Apr/Jul/Oct, and an annual one in January). That is
# deliberate — the monthly row is written on the 1st precisely so it covers the
# month in full.
#
# These tests are about the daily row, and used to fetch it with a bare
# `.get(employee=...)`. That worked on 30 days out of 31 and raised
# MultipleObjectsReturned on the other one — so the suite went red on the first
# of every month, and would have gone red on four rows on 1 January. Scoping to
# the cadence under test is the fix; the product behaviour was never wrong.
# ---------------------------------------------------------------------------


def test_the_snapshot_command_freezes_todays_figures(cast, make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", verbosity=0)

    row = EmployeeTaskEvidenceSnapshot.objects.get(
        employee=cast["employee"], period_type="daily")
    assert row.snapshot_date == today
    assert row.tasks_assigned == 1
    assert row.employee_name == cast["employee"].get_full_name()


def test_a_snapshot_is_written_once_and_never_rewritten(cast, make_task, today):
    """
    A snapshot is what the data SAID that day. A second run must not quietly
    produce a different version of the same day's truth — the fix for a wrong
    day is a new day and an explanation, not rewritten history.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", verbosity=0)
    first = EmployeeTaskEvidenceSnapshot.objects.get(
        employee=cast["employee"], period_type="daily")

    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", verbosity=0)

    assert EmployeeTaskEvidenceSnapshot.objects.filter(
        employee=cast["employee"], period_type="daily").count() == 1
    first.refresh_from_db()
    assert first.tasks_assigned == 1        # unchanged by the second run


def test_a_snapshot_survives_the_tasks_moving(cast, make_task, today):
    """
    The whole reason snapshots exist: a figure recomputed in December against
    March's work is not March's figure.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", verbosity=0)
    task.delete()

    row = EmployeeTaskEvidenceSnapshot.objects.get(
        employee=cast["employee"], period_type="daily")
    assert row.tasks_assigned == 1


def test_only_people_with_activity_get_a_row(cast, make_task):
    """A year of zero rows for somebody who has never had a task is a table
    nobody can read."""
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", verbosity=0)
    assert not EmployeeTaskEvidenceSnapshot.objects.filter(
        employee=cast["peer"]).exists()


def test_a_snapshot_can_be_backfilled_for_a_past_date(cast, make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    yesterday = today - datetime.timedelta(days=1)
    call_command("snapshot_task_evidence", "--date", yesterday.isoformat(),
                 verbosity=0)
    assert EmployeeTaskEvidenceSnapshot.objects.filter(
        snapshot_date=yesterday).exists()


def test_the_snapshot_endpoint_is_scoped_like_live_evidence(cast, auth,
                                                            make_task):
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", verbosity=0)

    own = auth(cast["employee"]).get(SNAPSHOTS)
    assert own.status_code == 200
    assert own.data["snapshots"] == []       # they have none of their own
    assert auth(cast["employee"]).get(
        SNAPSHOTS, {"employee": str(cast["peer"].id)}).status_code == 403


def test_the_snapshot_endpoint_returns_the_frozen_rows(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", verbosity=0)

    body = auth(cast["employee"]).get(
        SNAPSHOTS, {"period_type": "daily"}).data
    assert len(body["snapshots"]) == 1
    assert body["snapshots"][0]["tasks_assigned"] == 1
    assert body["employee_name"] == cast["employee"].get_full_name()
