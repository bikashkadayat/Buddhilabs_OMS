"""
Phase T2.1 — checklist groups, and the progress they drive.

Two claims are worth more than the rest here: a tick is never silently lost when
the list is rewritten, and the derived percentage never disagrees with the boxes
it is derived from.
"""
import pytest

from tasks.models import Task, TaskChecklistGroup, TaskChecklistItem
from tasks.services import recalculate_progress

from .conftest import LIST
from tenancy.stamping import stamp_all

pytestmark = pytest.mark.django_db

Status = Task.Status


def checklist_url(task):
    return f"{LIST}{task.id}/checklist/"


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------
def test_a_checklist_can_be_written_as_groups(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    response = auth(cast["hod"]).post(checklist_url(task), {
        "groups": [
            {"title": "Preparation", "items": ["Collect Data", "Draft Report"]},
            {"title": "Sign-off", "items": ["Review", "Final Submission"]},
        ],
    }, format="json")

    assert response.status_code == 200, response.data
    assert len(response.data) == 4
    assert task.checklist_groups.count() == 2
    assert [g.title for g in task.checklist_groups.all()] == ["Preparation", "Sign-off"]


def test_groups_and_loose_items_coexist(cast, auth, make_task):
    """
    A four-line checklist does not need a section header above it, so top-level
    items stay a first-class shape rather than being forced into a synthetic
    "General" bucket.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(checklist_url(task), {
        "items": ["Kick-off"],
        "groups": [{"title": "Drafting", "items": ["Write", "Edit"]}],
    }, format="json")

    rows = auth(cast["hod"]).get(checklist_url(task)).data
    assert len(rows) == 3
    loose = [r for r in rows if r["group"] is None]
    assert [r["text"] for r in loose] == ["Kick-off"]


def test_the_flat_endpoint_still_returns_a_plain_list(cast, auth, make_task):
    """
    Phase T1's shape is unchanged: a list, each item now carrying its `group`.
    Adding sections did not have to break every existing caller, so it did not.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(checklist_url(task),
                           {"items": ["One", "Two"]}, format="json")
    rows = auth(cast["hod"]).get(checklist_url(task)).data
    assert isinstance(rows, list)
    assert {"id", "text", "position", "is_done", "group"} <= set(rows[0])


def test_the_grouped_endpoint_nests_sections(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(checklist_url(task), {
        "items": ["Kick-off"],
        "groups": [{"title": "Drafting", "items": ["Write", "Edit"]}],
    }, format="json")

    body = auth(cast["hod"]).get(f"{LIST}{task.id}/checklist/grouped/").data
    assert [i["text"] for i in body["items"]] == ["Kick-off"]
    assert body["groups"][0]["title"] == "Drafting"
    assert [i["text"] for i in body["groups"][0]["items"]] == ["Write", "Edit"]
    assert body["groups"][0]["total_count"] == 2
    assert body["total"] == 3


def test_a_group_reports_its_own_tally(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(checklist_url(task), {
        "groups": [{"title": "Drafting", "items": ["Write", "Edit"]}],
    }, format="json")
    item = auth(cast["hod"]).get(checklist_url(task)).data[0]
    auth(cast["employee"]).post(
        f"{checklist_url(task)}{item['id']}/tick/", {"is_done": True},
        format="json")

    group = auth(cast["hod"]).get(
        f"{LIST}{task.id}/checklist/grouped/").data["groups"][0]
    assert group["done_count"] == 1
    assert group["total_count"] == 2


def test_deleting_a_group_takes_its_items_with_it(cast, auth, make_task):
    """An item cannot outlive the section it is in without changing which list it is in."""
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(checklist_url(task), {
        "groups": [{"title": "Drafting", "items": ["Write", "Edit"]}],
    }, format="json")
    assert TaskChecklistItem.objects.filter(task=task).count() == 2

    TaskChecklistGroup.objects.filter(task=task).delete()
    assert TaskChecklistItem.objects.filter(task=task).count() == 0


# ---------------------------------------------------------------------------
# Ticks survive a rewrite
# ---------------------------------------------------------------------------
def test_a_tick_survives_the_line_being_moved_into_a_group(cast, auth, make_task):
    """
    The tick is carried on the TEXT, so reorganising the list keeps the work
    somebody has already done. Rebuilding from row ids would have lost it,
    because the client sends text.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(checklist_url(task),
                           {"items": ["Collect Data", "Draft Report"]},
                           format="json")
    first = auth(cast["hod"]).get(checklist_url(task)).data[0]
    auth(cast["employee"]).post(f"{checklist_url(task)}{first['id']}/tick/",
                                {"is_done": True}, format="json")

    auth(cast["hod"]).post(checklist_url(task), {
        "groups": [{"title": "Preparation",
                    "items": ["Collect Data", "Draft Report"]}],
    }, format="json")

    rows = {r["text"]: r for r in auth(cast["hod"]).get(checklist_url(task)).data}
    assert rows["Collect Data"]["is_done"] is True
    assert rows["Collect Data"]["group"] is not None
    assert rows["Draft Report"]["is_done"] is False


def test_sending_neither_items_nor_groups_is_refused(cast, auth, make_task):
    """
    Omitting both is far more likely a client bug than an instruction to wipe
    the checklist. Clearing it is spelled `{"items": []}`.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    assert auth(cast["hod"]).post(checklist_url(task), {},
                                  format="json").status_code == 400


def test_an_empty_items_list_does_clear_the_checklist(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(checklist_url(task), {"items": ["One"]}, format="json")
    auth(cast["hod"]).post(checklist_url(task), {"items": []}, format="json")
    assert auth(cast["hod"]).get(checklist_url(task)).data == []


# ---------------------------------------------------------------------------
# Auto-calculated progress (T2.1 / T2.2)
# ---------------------------------------------------------------------------
def test_progress_is_derived_from_the_checklist(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["hod"]).post(checklist_url(task), {
        "items": ["Collect Data", "Draft Report", "Review", "Final Submission"],
    }, format="json")
    rows = auth(cast["hod"]).get(checklist_url(task)).data

    for expected, row in zip([25, 50, 75, 100], rows):
        response = auth(cast["employee"]).post(
            f"{checklist_url(task)}{row['id']}/tick/", {"is_done": True},
            format="json")
        assert response.data["progress_percent"] == expected
        task.refresh_from_db()
        assert task.progress_percent == expected


def test_un_ticking_moves_the_derived_figure_back_down(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["hod"]).post(checklist_url(task), {"items": ["A", "B"]},
                           format="json")
    row = auth(cast["hod"]).get(checklist_url(task)).data[0]
    auth(cast["employee"]).post(f"{checklist_url(task)}{row['id']}/tick/",
                                {"is_done": True}, format="json")
    response = auth(cast["employee"]).post(
        f"{checklist_url(task)}{row['id']}/tick/", {"is_done": False},
        format="json")
    assert response.data["progress_percent"] == 0


def test_rewriting_the_checklist_moves_the_derived_figure(cast, auth, make_task):
    """Adding two more lines to a half-done list is no longer half done."""
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["hod"]).post(checklist_url(task), {"items": ["A", "B"]},
                           format="json")
    row = auth(cast["hod"]).get(checklist_url(task)).data[0]
    auth(cast["employee"]).post(f"{checklist_url(task)}{row['id']}/tick/",
                                {"is_done": True}, format="json")
    task.refresh_from_db()
    assert task.progress_percent == 50

    auth(cast["hod"]).post(checklist_url(task),
                           {"items": ["A", "B", "C", "D"]}, format="json")
    task.refresh_from_db()
    assert task.progress_percent == 25


def test_reporting_progress_by_hand_takes_the_task_off_automatic(
        cast, auth, make_task):
    """
    The point of letting somebody type a figure is that their judgement then
    stands. A tick that silently discarded it would make the control pointless.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["hod"]).post(checklist_url(task), {"items": ["A", "B", "C", "D"]},
                           format="json")
    assert task.progress_is_auto is True

    auth(cast["employee"]).post(f"{LIST}{task.id}/progress/",
                                {"progress_percent": 75}, format="json")
    task.refresh_from_db()
    assert task.progress_is_auto is False
    assert task.progress_percent == 75

    row = auth(cast["hod"]).get(checklist_url(task)).data[0]
    auth(cast["employee"]).post(f"{checklist_url(task)}{row['id']}/tick/",
                                {"is_done": True}, format="json")
    task.refresh_from_db()
    assert task.progress_percent == 75      # the human's figure stands


def test_a_task_with_no_checklist_keeps_its_hand_reported_figure(
        cast, auth, make_task):
    """
    There is nothing to derive a percentage FROM, so resetting a reported 60% to
    0 would be actively wrong.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["employee"]).post(f"{LIST}{task.id}/progress/",
                                {"progress_percent": 60}, format="json")
    task.refresh_from_db()
    assert recalculate_progress(task) is False
    assert task.progress_percent == 60


def test_recalculation_reads_the_database_not_a_stale_prefetch(cast, make_task):
    """
    `Task.checklist_percent` reads `self.checklist.all()`, which a prefetched
    task has already cached — so after a tick it answers with the list as it was
    BEFORE the tick. The recalculation must go to the database, or the derived
    figure is permanently one change behind.
    """
    from tasks.models import Task as TaskModel

    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    TaskChecklistItem.objects.bulk_create(stamp_all([
        TaskChecklistItem(task=task, text="A", position=0),
        TaskChecklistItem(task=task, text="B", position=1),
    ]))
    # A task carrying a prefetch cache, exactly as the viewset hands one over.
    cached = TaskModel.objects.prefetch_related("checklist").get(pk=task.pk)
    list(cached.checklist.all())            # prime the cache
    TaskChecklistItem.objects.filter(task=task, text="A").update(is_done=True)

    assert recalculate_progress(cached) is True
    cached.refresh_from_db()
    assert cached.progress_percent == 50
