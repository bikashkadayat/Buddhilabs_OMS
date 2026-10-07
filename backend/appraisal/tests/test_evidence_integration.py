"""
Evidence integration (APM-02).

The instruction was explicit: consume the existing evidence layer, do not
rebuild it. These tests assert BOTH halves — that task evidence reaches the
appraisal, and that this module contains no second implementation producing it.
"""
import datetime

import pytest
from django.utils import timezone

from appraisal import evidence_link
from appraisal.models import Appraisal, EvidenceReference

from .conftest import APPRAISALS
from tenancy.stamping import stamp_all

pytestmark = pytest.mark.django_db

Status = Appraisal.Status


@pytest.fixture
def with_tasks(cast, cycle, db):
    """Real task activity for the employee, built through the task engine."""
    from tasks import workflow as task_workflow
    from tasks.models import Task, TaskChecklistItem
    from tasks.services import generate_task_number, user_snapshot

    made = []
    for index, target in enumerate(["closed", "closed", "in_progress"]):
        task = Task.objects.create(
            task_number=generate_task_number(),
            title=f"Appraisal-period task {index}",
            created_by=cast["supervisor"],
            created_by_name=user_snapshot(cast["supervisor"])["name"],
            reviewer=cast["supervisor"],
            reviewer_name=user_snapshot(cast["supervisor"])["name"],
            due_date=timezone.localdate() + datetime.timedelta(days=5))
        task_workflow.record_creation(task, cast["supervisor"])
        task_workflow.set_assignees(task, [cast["employee"]],
                                    cast["supervisor"])
        TaskChecklistItem.objects.bulk_create(stamp_all([
            TaskChecklistItem(task=task, text=f"Step {n}", position=n)
            for n in range(2)]))
        task_workflow.assign(task, cast["supervisor"])
        task_workflow.accept(task, cast["employee"])
        task_workflow.start(task, cast["employee"])
        if target == "closed":
            task_workflow.submit_for_review(task, cast["employee"])
            task_workflow.approve_review(task, cast["supervisor"])
            task_workflow.close(task, cast["hr"])
        made.append(task)
    return made


# ---------------------------------------------------------------------------
# No second evidence system
# ---------------------------------------------------------------------------
def test_this_module_computes_no_metric_of_its_own():
    """
    The structural guarantee. A second implementation would drift from the first
    within a release, and the two would disagree in front of the person being
    appraised — the worst possible place to find out.
    """
    import ast
    import pathlib

    source = pathlib.Path(evidence_link.__file__).read_text()
    tree = ast.parse(source)
    # Nothing in this module queries the task tables directly to build a figure.
    assert "Task.objects" not in source
    assert "aggregate(" not in source
    assert "annotate(" not in source
    # And it imports the CONTRACT, never the task module.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
    assert "evidence" in imported
    assert "tasks" not in imported


def test_the_appraisal_module_defines_no_evidence_model():
    """
    `EvidenceReference` is a CITATION — provenance plus what the source said.
    It must not grow into a table of independently computed metrics.
    """
    fields = {f.name for f in EvidenceReference._meta.get_fields()}
    # The metrics live in one opaque JSON field copied from the source, not in
    # columns this module would have to keep in step.
    assert "metrics" in fields
    for computed in ("completion_percent", "tasks_completed", "on_time_percent"):
        assert computed not in fields, computed


# ---------------------------------------------------------------------------
# Evidence reaches the appraisal
# ---------------------------------------------------------------------------
def test_task_evidence_appears_on_the_appraisal(cast, auth, cycle,
                                                make_appraisal, with_tasks):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["employee"]).get(f"{APPRAISALS}{appraisal.id}/evidence/").data

    assert body["available"] is True
    metrics = {row["key"]: row["value"] for row in body["headline"]}
    assert metrics["tasks_assigned"] == 3
    assert metrics["tasks_completed"] == 2


def test_all_seven_specified_figures_are_displayed(cast, auth, cycle,
                                                   make_appraisal, with_tasks):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    keys = [row["key"] for row in
            auth(cast["employee"]).get(
                f"{APPRAISALS}{appraisal.id}/evidence/").data["headline"]]
    assert keys == evidence_link.HEADLINE_KEYS
    assert set(keys) == {
        "tasks_assigned", "tasks_completed", "task_completion_percent",
        "on_time_percent", "review_participation", "evidence_uploaded",
        "checklist_completion"}


def test_every_percentage_arrives_with_its_denominator(cast, auth, cycle,
                                                       make_appraisal,
                                                       with_tasks):
    """Carried through from the T6 contract, not re-derived here."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    rows = auth(cast["employee"]).get(
        f"{APPRAISALS}{appraisal.id}/evidence/").data["headline"]
    for row in rows:
        if row["unit"] == "percent":
            assert row["basis_of"], row["key"]


def test_the_evidence_carries_its_disclaimer_and_the_appraisals_own_note(
        cast, auth, cycle, make_appraisal, with_tasks):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["employee"]).get(f"{APPRAISALS}{appraisal.id}/evidence/").data
    assert "not an assessment" in body["disclaimer"]
    assert "is computed from it" in body["note"]   # "nothing ... is computed from it"


def test_a_thin_period_is_flagged_on_the_appraisal(cast, auth, cycle,
                                                   make_appraisal, with_tasks):
    """
    An appraisal is precisely where noise gets mistaken for a pattern about a
    person, so the task module's low-volume flag is surfaced here.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["employee"]).get(f"{APPRAISALS}{appraisal.id}/evidence/").data
    assert body["low_volume"] is True      # three tasks is below the threshold


def test_evidence_reports_which_sources_are_connected(cast, auth, cycle,
                                                      make_appraisal):
    """
    Six of the seven declared sources have no provider. An appraisal pack must
    say "leave evidence is not connected" rather than implying there was none.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    sources = {row["source"]: row["available"] for row in
               auth(cast["hr"]).get(
                   f"{APPRAISALS}{appraisal.id}/evidence/").data["sources"]}
    assert sources["task"] is True
    assert sources["leave"] is False


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------
def test_evidence_can_be_frozen_onto_the_record(cast, auth, cycle,
                                                make_appraisal, with_tasks):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    response = auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal.id}/attach-evidence/",
        {"note": "My task record for the year."}, format="json")

    assert response.status_code == 200
    reference = appraisal.evidence_references.get()
    assert reference.source == "task"
    assert reference.contract_version
    assert reference.metrics
    assert reference.disclaimer


def test_a_frozen_citation_does_not_move_when_the_tasks_do(cast, auth, cycle,
                                                           make_appraisal,
                                                           with_tasks):
    """
    The reason citations are frozen: the figure in the discussion and the figure
    in the record must still match months later, or the person appraised has no
    way to show what they were actually shown.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    auth(cast["employee"]).post(f"{APPRAISALS}{appraisal.id}/attach-evidence/",
                                {}, format="json")
    frozen = appraisal.evidence_references.get()
    before = {m["key"]: m["value"] for m in frozen.metrics}

    from tasks.models import Task
    Task.objects.all().delete()

    frozen.refresh_from_db()
    after = {m["key"]: m["value"] for m in frozen.metrics}
    assert after == before
    assert before["tasks_assigned"] == 3

    # ...while the LIVE endpoint honestly reports the new reality.
    live = auth(cast["employee"]).get(
        f"{APPRAISALS}{appraisal.id}/evidence/").data["headline"]
    assert {m["key"]: m["value"] for m in live}["tasks_assigned"] == 0


def test_attaching_twice_does_not_duplicate_the_citation(cast, auth, cycle,
                                                         make_appraisal,
                                                         with_tasks):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    for _ in range(3):
        auth(cast["employee"]).post(
            f"{APPRAISALS}{appraisal.id}/attach-evidence/", {}, format="json")
    assert appraisal.evidence_references.count() == 1


def test_attaching_is_recorded_on_the_timeline(cast, auth, cycle,
                                               make_appraisal, with_tasks):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    auth(cast["employee"]).post(f"{APPRAISALS}{appraisal.id}/attach-evidence/",
                                {}, format="json")
    actions = [row["action"] for row in auth(cast["employee"]).get(
        f"{APPRAISALS}{appraisal.id}/timeline/").data]
    assert "evidence_attached" in actions


def test_an_outsider_cannot_read_or_attach_evidence(cast, auth, cycle,
                                                    make_appraisal, with_tasks):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    client = auth(cast["outsider"])
    assert client.get(f"{APPRAISALS}{appraisal.id}/evidence/").status_code == 404
    assert client.post(f"{APPRAISALS}{appraisal.id}/attach-evidence/", {},
                       format="json").status_code == 404


def test_snapshots_are_read_not_written(cast, auth, cycle, make_appraisal,
                                        with_tasks):
    """
    This module never writes a task snapshot and never asks the task module to.
    """
    from django.core.management import call_command
    from tasks.models import EmployeeTaskEvidenceSnapshot

    call_command("snapshot_task_evidence", verbosity=0)
    before = EmployeeTaskEvidenceSnapshot.objects.count()

    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    auth(cast["employee"]).get(f"{APPRAISALS}{appraisal.id}/evidence/")
    auth(cast["employee"]).post(f"{APPRAISALS}{appraisal.id}/attach-evidence/",
                                {}, format="json")

    assert EmployeeTaskEvidenceSnapshot.objects.count() == before
