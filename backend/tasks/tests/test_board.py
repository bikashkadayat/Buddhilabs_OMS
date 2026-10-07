"""
Phase T3 Part 1 — the board.

The two claims worth more than the rest: no status can silently fall off the
board, and the board never shows a task the caller could not open.
"""
import pytest

from tasks import board as board_config
from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
BOARD = f"{LIST}board/"


def columns(response):
    return {c["key"]: c for c in response.data["columns"]}


# ---------------------------------------------------------------------------
# The mapping itself
# ---------------------------------------------------------------------------
def test_the_board_names_the_four_columns_the_specification_asks_for():
    """
    Recut again in TASK-SIMPLIFICATION: To Do / In Progress / Review / Done.
    Backlog went with the distinction between a draft and an assigned task,
    which was a detail of this system rather than a state of the work.
    """
    assert [c["key"] for c in board_config.COLUMNS] == [
        "todo", "in_progress", "review", "done"]
    assert [c["label"] for c in board_config.COLUMNS] == [
        "To Do", "In Progress", "Review", "Done"]
    # A draft is still on the board - it sits in To Do rather than in a lane of
    # its own, so nothing falls off.
    assert Status.DRAFT in board_config.COLUMNS[0]["statuses"]


def test_every_status_is_either_on_the_board_or_deliberately_excluded():
    """
    The guard that matters. A tenth status added later must be placed in a
    column or listed as excluded — it cannot silently vanish, which on a board
    looks like data loss rather than a configuration gap.
    """
    placed = set(board_config.BOARD_STATUSES)
    excluded = set(board_config.EXCLUDED_STATUSES)
    every = {value for value, _ in Task.Status.choices}

    assert placed | excluded == every, every - (placed | excluded)
    assert not placed & excluded, "a status cannot be both placed and excluded"


def test_no_status_appears_in_two_columns():
    seen = []
    for column in board_config.COLUMNS:
        seen.extend(column["statuses"])
    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------
def test_each_task_lands_in_its_column(cast, auth, make_task):
    expected = {
        Status.ASSIGNED: "todo",
        Status.ACCEPTED: "todo",
        Status.IN_PROGRESS: "in_progress",
        Status.UNDER_REVIEW: "review",
        Status.COMPLETED: "done",
        Status.CLOSED: "done",
        # Blocked work is started work that cannot finish: it sits with the rest
        # of the started work, carrying its Blocked badge, rather than in a
        # siding nobody looks at.
        Status.BLOCKED: "in_progress",
    }
    built = {}
    for status, column in expected.items():
        task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                         status=status)
        built[str(task.id)] = column

    body = columns(auth(cast["hod"]).get(BOARD))
    for task_id, column in built.items():
        ids = [t["id"] for t in body[column]["tasks"]]
        assert task_id in ids, f"{task_id} missing from {column}"


def test_accepted_sits_in_to_do_not_in_progress(cast, auth, make_task):
    """
    Accepting is an acknowledgement, not a start. A column of its own would put
    a card in front of a manager meaning "nothing has happened yet, politely".
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    body = columns(auth(cast["hod"]).get(BOARD))
    assert [t["id"] for t in body["todo"]["tasks"]] == [str(task.id)]
    assert body["in_progress"]["tasks"] == []


def test_blocked_work_stays_in_progress_and_says_so_on_the_card(cast, auth,
                                                                make_task):
    """
    It has no column of its own on a five-column board. It must not therefore
    disappear into Done, and it must not look healthy: the card still carries
    the exact status.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.BLOCKED)
    body = columns(auth(cast["hod"]).get(BOARD))
    card = body["in_progress"]["tasks"][0]
    assert card["id"] == str(task.id)
    assert card["status"] == Status.BLOCKED
    assert card["status_label"] == "Blocked"


def test_a_cancelled_task_is_on_no_column_at_all(cast, auth, make_task):
    """A board answers 'what is in flight and where'; a cancelled task is neither."""
    from tasks import workflow

    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    workflow.cancel(task, cast["hod"], "The programme was descoped.")

    response = auth(cast["hod"]).get(BOARD)
    every = [t["id"] for c in response.data["columns"] for t in c["tasks"]]
    assert str(task.id) not in every
    assert response.data["excluded_statuses"] == [Status.CANCELLED]
    # ...but it is still in the List view.
    assert str(task.id) in [
        r["id"] for r in auth(cast["hod"]).get(LIST).data["results"]]


def test_an_empty_column_is_kept_because_empty_is_information(cast, auth,
                                                              make_task):
    """An empty Review column says nothing is waiting on a reviewer."""
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    body = columns(auth(cast["hod"]).get(BOARD))
    assert len(body) == 4
    assert body["review"]["count"] == 0
    assert body["review"]["tasks"] == []


def test_the_mapping_is_served_with_the_payload(cast, auth):
    """
    So the client never needs a second copy of it. Two copies is two things that
    can disagree.
    """
    body = auth(cast["hod"]).get(BOARD).data
    todo = next(c for c in body["columns"] if c["key"] == "todo")
    # Draft joined this column in TASK-SIMPLIFICATION.
    assert todo["statuses"] == [Status.DRAFT, Status.ASSIGNED, Status.ACCEPTED]


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def test_a_card_carries_everything_the_specification_names(cast, auth, make_task,
                                                           tomorrow):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=tomorrow)
    auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/",
                           {"items": ["A", "B"]}, format="json")
    auth(cast["employee"]).post(f"{LIST}{task.id}/comments/",
                                {"body": "On it."}, format="json")

    card = columns(auth(cast["hod"]).get(BOARD))["todo"]["tasks"][0]
    # The nine the phase names: title, description, owner, department, priority,
    # due date, status, evidence count, comments count - plus what T2.8 already
    # put on the card.
    for field in ("task_number", "title", "summary", "owner_name",
                  "department_name", "task_type", "task_type_label",
                  "status", "status_label", "assignee_names", "priority_label",
                  "due_date", "progress_percent", "checklist_done",
                  "checklist_total", "comment_count", "attachment_count",
                  "evidence_count"):
        assert field in card, field
    assert card["checklist_total"] == 2
    assert card["comment_count"] == 1


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------
def test_the_board_never_shows_a_task_the_caller_cannot_open(cast, auth,
                                                             make_task):
    mine = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    theirs = make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED)

    every = [t["id"] for c in auth(cast["employee"]).get(BOARD).data["columns"]
             for t in c["tasks"]]
    assert str(mine.id) in every
    assert str(theirs.id) not in every


def test_a_department_head_sees_their_own_department_only(cast, auth, make_task,
                                                          departments):
    ours = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     department=departments["engineering"])
    theirs = make_task(cast["other_hod"], [cast["outsider"]],
                       status=Status.ASSIGNED, department=departments["finance"])

    every = [t["id"] for c in auth(cast["hod"]).get(BOARD).data["columns"]
             for t in c["tasks"]]
    assert str(ours.id) in every and str(theirs.id) not in every


def test_the_board_honours_the_url_filters_but_not_a_stray_scope(cast, auth,
                                                                 make_task):
    """
    A board applies its own filters. A `scope` left on the URL from the list
    view must not silently narrow it — that is the shape of a bug nobody can
    see, because the board just quietly shows less.
    """
    urgent = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    urgent.priority = Task.Priority.URGENT
    urgent.save(update_fields=["priority"])
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)

    filtered = auth(cast["hod"]).get(BOARD, {"priority": "urgent"})
    assert filtered.data["total"] == 1

    scoped = auth(cast["hod"]).get(BOARD, {"scope": "due_today"})
    assert scoped.data["total"] == 2


def test_the_board_requires_authentication(api):
    assert api.get(BOARD).status_code in (401, 403)
