"""
Phase T6 — standardised evidence, periodic snapshots, team visibility and
exports.

The fairness assertions are not a section at the end; they run through every
part, because that is where the risk is. This layer's whole purpose is to feed
a process that decides things about people.
"""
import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from evidence.registry import provider_for
from evidence.schema import CONTRACT_VERSION, PeriodType, Source, Unit
from tasks import evidence as evidence_service
from tasks.models import EmployeeTaskEvidenceSnapshot, Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
ANALYTICS = "/api/v1/task-analytics/"
FORBIDDEN = ("score", "rating", "rank", "grade", "band", "percentile", "weight")


# ===========================================================================
# Part 2 — standardisation
# ===========================================================================
def test_the_task_module_is_registered_as_an_evidence_source():
    assert provider_for(Source.TASK) is not None


def test_the_provider_returns_all_nine_named_metrics(cast, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    result = provider_for(Source.TASK)(
        cast["employee"], *evidence_service.default_period(), PeriodType.DAILY)

    keys = {m.key for m in result.metrics}
    for required in ("tasks_assigned", "tasks_completed",
                     "task_completion_percent", "task_overdue_percent",
                     "average_completion_days", "review_participation",
                     "checklist_completion", "evidence_uploaded",
                     "comments_added"):
        assert required in keys, required


def test_every_percentage_names_the_count_it_was_taken_over(cast, make_task):
    """
    "75%" means something very different over four tasks and over four hundred.
    A contract test refuses a ratio that does not say which.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    result = provider_for(Source.TASK)(
        cast["employee"], *evidence_service.default_period(), PeriodType.DAILY)

    for metric in result.metrics:
        if metric.unit == Unit.PERCENT:
            assert metric.basis_of, f"{metric.key} has no denominator"


def test_no_metric_is_a_score(cast, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    result = provider_for(Source.TASK)(
        cast["employee"], *evidence_service.default_period(), PeriodType.DAILY)

    for metric in result.metrics:
        for word in FORBIDDEN:
            assert word not in metric.key.lower(), metric.key
            assert word not in metric.label.lower(), metric.label


def test_a_null_average_survives_standardisation(cast, make_task):
    """"No data" and "same day" must stay different answers all the way out."""
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    result = provider_for(Source.TASK)(
        cast["employee"], *evidence_service.default_period(), PeriodType.DAILY)
    average = next(m for m in result.metrics
                   if m.key == "average_completion_days")
    assert average.value is None
    assert average.null_means_no_data is True


def test_the_provider_cannot_see_past_the_queryset_it_is_given(cast, make_task):
    """Every request path passes its own scope; the provider never widens it."""
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    empty = Task.objects.none()
    result = provider_for(Source.TASK)(
        cast["employee"], *evidence_service.default_period(), PeriodType.DAILY,
        queryset=empty)
    assigned = next(m for m in result.metrics if m.key == "tasks_assigned")
    assert assigned.value == 0


# ===========================================================================
# Part 8 — the low-volume guard (Phase T5.5 audit finding)
# ===========================================================================
def test_a_handful_of_tasks_is_flagged_as_low_volume(cast, make_task):
    """
    Somebody with two tasks showing "50% completion" is noise presented as a
    metric — the most likely way this data gets misused about a person.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    result = provider_for(Source.TASK)(
        cast["employee"], *evidence_service.default_period(), PeriodType.DAILY)
    flag = next(m for m in result.metrics if m.key == "low_volume")
    assert flag.value == 1


def test_the_flag_clears_once_there_is_enough_to_read(cast, make_task):
    for _ in range(evidence_service.LOW_VOLUME_THRESHOLD):
        make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    result = provider_for(Source.TASK)(
        cast["employee"], *evidence_service.default_period(), PeriodType.DAILY)
    flag = next(m for m in result.metrics if m.key == "low_volume")
    assert flag.value == 0


def test_the_flag_travels_on_the_live_endpoint_too(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(f"{ANALYTICS}evidence/").data
    assert body["low_volume"] is True
    assert body["contract_version"] == CONTRACT_VERSION


# ===========================================================================
# Part 3 — periodic snapshots
# ===========================================================================
@pytest.mark.parametrize("cadence", ["daily", "monthly", "quarterly", "annual"])
def test_every_cadence_can_be_written(cast, make_task, cadence):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", "--period", cadence, verbosity=0)
    assert EmployeeTaskEvidenceSnapshot.objects.filter(
        employee=cast["employee"], period_type=cadence).exists()


def test_the_cadences_coexist_rather_than_replacing_each_other(cast, make_task):
    """
    The daily series shows a trend; the monthly one is what an appraisal quotes.
    Deriving either from the other later would mean recomputing against tasks
    that have since moved.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    for cadence in ("daily", "monthly", "quarterly", "annual"):
        call_command("snapshot_task_evidence", "--period", cadence, verbosity=0)

    rows = EmployeeTaskEvidenceSnapshot.objects.filter(employee=cast["employee"])
    assert rows.count() == 4
    assert {r.period_type for r in rows} == {"daily", "monthly", "quarterly",
                                             "annual"}


def test_a_cadence_is_written_once_per_day_and_never_rewritten(cast, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", "--period", "monthly", verbosity=0)
    first = EmployeeTaskEvidenceSnapshot.objects.get(period_type="monthly")

    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", "--period", "monthly", verbosity=0)

    assert EmployeeTaskEvidenceSnapshot.objects.filter(
        period_type="monthly").count() == 1
    first.refresh_from_db()
    assert first.tasks_assigned == 1        # history preserved, not corrected


@pytest.mark.parametrize("cadence,anchor,start,end", [
    ("monthly", datetime.date(2026, 4, 1),
     datetime.date(2026, 3, 1), datetime.date(2026, 3, 31)),
    ("quarterly", datetime.date(2026, 4, 1),
     datetime.date(2026, 1, 1), datetime.date(2026, 3, 31)),
    ("annual", datetime.date(2026, 1, 1),
     datetime.date(2025, 1, 1), datetime.date(2025, 12, 31)),
    # A leap year, found by stepping rather than from a table of month lengths.
    ("monthly", datetime.date(2028, 3, 1),
     datetime.date(2028, 2, 1), datetime.date(2028, 2, 29)),
])
def test_a_closing_snapshot_covers_the_period_that_just_ended(cadence, anchor,
                                                              start, end):
    """
    Written on the FIRST of a period, covering the whole previous one. Written on
    the last day instead, the row would omit whatever happened after the job ran
    that evening — and nobody would know which hours were missing.
    """
    assert evidence_service.period_window(cadence, anchor, closing=True) == (
        start, end)


def test_a_mid_period_snapshot_is_partial_and_says_so(cast, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", "--period", "monthly", verbosity=0)
    row = EmployeeTaskEvidenceSnapshot.objects.get(period_type="monthly")
    # The window is stored on the row, so a partial period is never mistaken
    # for a whole one.
    assert row.period_start <= row.period_end
    assert row.period_start.day == 1


def test_an_explicit_period_covers_the_period_the_date_sits_in(cast, make_task,
                                                              today):
    """
    `--period monthly` means "this month so far" — the natural reading of asking
    for it by hand. The SCHEDULER's unattended run means the period that just
    closed; the two are different on purpose and both are tested.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    call_command("snapshot_task_evidence", "--period", "monthly", verbosity=0)
    row = EmployeeTaskEvidenceSnapshot.objects.get(period_type="monthly")
    assert row.period_start == today.replace(day=1)
    assert row.tasks_assigned == 1


def test_the_scheduler_writes_only_the_cadences_a_day_closes(cast, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    # The 15th closes nothing but the rolling daily row.
    call_command("snapshot_task_evidence", "--date", "2026-04-15", verbosity=0)
    assert {r.period_type for r in EmployeeTaskEvidenceSnapshot.objects.all()} \
        == {"daily"}

    # 1 April closes March and Q1.
    call_command("snapshot_task_evidence", "--date", "2026-04-01", verbosity=0)
    assert {r.period_type for r in EmployeeTaskEvidenceSnapshot.objects.filter(
        snapshot_date="2026-04-01")} == {"daily", "monthly", "quarterly"}


def test_snapshots_can_be_filtered_by_cadence(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    for cadence in ("daily", "monthly"):
        call_command("snapshot_task_evidence", "--period", cadence, verbosity=0)

    body = auth(cast["employee"]).get(
        f"{ANALYTICS}evidence/snapshots/", {"period_type": "monthly"}).data
    assert len(body["snapshots"]) == 1
    assert body["snapshots"][0]["period_type"] == "monthly"


# ===========================================================================
# Part 6 — the contract endpoint
# ===========================================================================
def test_the_contract_endpoint_describes_itself(cast, auth):
    """
    A consumer discovers the keys, units and definitions rather than hard-coding
    a list it cannot tell has changed.
    """
    body = auth(cast["employee"]).get(f"{ANALYTICS}evidence/contract/").data
    assert body["contract_version"] == CONTRACT_VERSION
    assert set(body["period_types"]) == {"daily", "monthly", "quarterly",
                                         "annual"}
    keys = {m["key"] for m in body["metrics"]}
    assert "task_completion_percent" in keys
    assert all("definition" in m and m["definition"] for m in body["metrics"])
    # It describes the schema, so it must carry no VALUES.
    assert all("value" not in m for m in body["metrics"])


def test_the_contract_reports_which_sources_are_live(cast, auth):
    sources = {row["source"]: row["available"]
               for row in auth(cast["employee"]).get(
                   f"{ANALYTICS}evidence/contract/").data["sources"]}
    assert sources["task"] is True
    assert sources["leave"] is False


def test_the_contract_states_its_own_guarantees(cast, auth):
    body = auth(cast["employee"]).get(f"{ANALYTICS}evidence/contract/").data
    joined = " ".join(body["guarantees"]).lower()
    assert "composite" in joined and "ranked" in joined


def test_the_standard_endpoint_returns_the_shared_shape(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(f"{ANALYTICS}evidence/standard/").data
    assert body["source"] == "task"
    assert body["contract_version"] == CONTRACT_VERSION
    assert body["disclaimer"]
    assert all({"key", "label", "unit", "value", "definition"} <= set(m)
               for m in body["metrics"])


# ===========================================================================
# Part 5 — the manager view
# ===========================================================================
def test_a_manager_sees_their_reports_evidence(cast, auth, make_task,
                                               departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    body = auth(cast["hod"]).get(f"{ANALYTICS}evidence/team/").data
    names = {row["employee_name"] for row in body["employees"]}
    assert cast["employee"].get_full_name() in names


def test_the_team_view_is_ordered_by_name_never_by_a_metric(cast, auth,
                                                            make_task):
    """
    A manager handed a list of their reports sorted by completion rate has been
    handed a judgement they did not make and cannot see the basis of.
    """
    for _ in range(4):
        make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                  status=Status.CLOSED)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    rows = auth(cast["hr"]).get(f"{ANALYTICS}evidence/team/").data["employees"]
    names = [r["employee_name"] for r in rows]
    assert names == sorted(names, key=str.lower)


def test_every_team_row_carries_its_own_low_volume_flag(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    rows = auth(cast["hr"]).get(f"{ANALYTICS}evidence/team/").data["employees"]
    assert all("low_volume" in row for row in rows)
    assert any(row["low_volume"] for row in rows)


def test_the_team_view_says_it_is_not_a_ranking(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    note = auth(cast["hr"]).get(f"{ANALYTICS}evidence/team/").data["note"]
    assert "not scored, ranked or weighted" in note


def test_an_employee_cannot_reach_the_team_view(cast, auth):
    assert auth(cast["employee"]).get(
        f"{ANALYTICS}evidence/team/").status_code == 403


def test_a_manager_sees_only_their_own_department(cast, auth, make_task,
                                                  departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])

    names = {r["employee_name"] for r in auth(cast["hod"]).get(
        f"{ANALYTICS}evidence/team/").data["employees"]}
    assert cast["outsider"].get_full_name() not in names


# ===========================================================================
# Part 7 — exports
# ===========================================================================
EXPORTS = ["employee-evidence", "department-summary", "task-contribution"]


@pytest.mark.parametrize("slug", EXPORTS)
def test_every_evidence_export_renders_in_all_three_formats(cast, auth,
                                                            make_task, slug):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)
    client = auth(cast["hr"])
    assert client.get(f"{LIST}reports/{slug}/").status_code == 200
    assert client.get(f"{LIST}reports/{slug}/",
                      {"export": "csv"}).status_code == 200
    pdf = client.get(f"{LIST}reports/{slug}/", {"export": "pdf"})
    assert pdf.status_code == 200 and bytes(pdf.content)[:5] == b"%PDF-"


def test_the_employee_export_is_ordered_by_name(cast, auth, make_task):
    for _ in range(4):
        make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                  status=Status.CLOSED)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    rows = auth(cast["hr"]).get(f"{LIST}reports/employee-evidence/").data["rows"]
    names = [r["employee"] for r in rows]
    assert names == sorted(names, key=str.lower)


def test_the_employee_export_carries_the_low_volume_caveat(cast, auth,
                                                           make_task):
    """
    A caveat that only exists on screen does not survive being emailed as a
    spreadsheet.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["hr"]).get(f"{LIST}reports/employee-evidence/").data
    assert any(c["key"] == "low_volume" for c in body["columns"])
    assert body["rows"][0]["low_volume"] == "yes"
    assert "not scored or ranked" in body["summary"]["note"].lower()


def test_the_contribution_export_is_the_row_level_record(cast, auth, make_task):
    """
    An aggregate nobody can drill into is an aggregate nobody can check. If
    somebody disputes "9 of 12", this is the list of twelve.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    rows = auth(cast["hr"]).get(f"{LIST}reports/task-contribution/").data["rows"]
    assert any(r["task_number"] == task.task_number for r in rows)


@pytest.mark.parametrize("slug", EXPORTS)
def test_an_evidence_export_is_scoped_like_everything_else(cast, auth,
                                                           make_task, slug):
    task = make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    body = auth(cast["outsider"]).get(f"{LIST}reports/{slug}/",
                                      {"export": "csv"}).content.decode("utf-8-sig")
    assert task.task_number not in body
    assert cast["peer"].get_full_name() not in body
