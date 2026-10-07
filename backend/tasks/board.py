"""
The board's columns (Phase T3, Part 1; recut in TASK-MANAGEMENT-ASANA-MODEL).

ONE DEFINITION, SERVED TO THE CLIENT
------------------------------------
The board has four columns and the task engine has nine statuses, so something
has to map one onto the other. That mapping lives HERE and is served to the
client by `GET /api/v1/tasks/board/`, rather than being written once in Python
and again in JavaScript. Two copies of a mapping is two copies that can
disagree, and the symptom - a task that vanishes from the board because neither
side claims it - looks like data loss rather than a configuration mismatch.

THE FOUR COLUMNS
----------------
To Do, In Progress, Review, Done: the board people already know from Asana
and Trello, and the one the phase specifies. The nine statuses still exist
underneath and every card still shows its exact one - the columns say where work
IS, the status says precisely what state it is in.

WHY BLOCKED SITS IN "IN PROGRESS"
---------------------------------
It used to have a column of its own ("On Hold"), which the five-column board has
no room for. Blocked work is work somebody has started and cannot finish, so it
belongs beside the rest of the started work rather than parked in a siding where
it stops being looked at - and the card carries a Blocked badge and its reason,
so it is not disguised as healthy.

WHY CANCELLED HAS NO COLUMN
---------------------------
A board answers "what is in flight and where", and a cancelled task is neither.
Cancelled tasks are excluded from the board entirely and remain in the List
view, in search, and on the task's own page. `EXCLUDED_STATUSES` below is the one
place that decision is recorded, and `COLUMNS` is asserted in the tests to cover
every status that is not in it - so a tenth status added later cannot silently
fall off the board.
"""
from .models import Task

Status = Task.Status

# Statuses deliberately absent from the board. See the module docstring.
EXCLUDED_STATUSES = (Status.CANCELLED,)

COLUMNS = [
    {
        "key": "todo",
        "label": "To Do",
        # Draft, Assigned and Accepted. A task that has been written but not
        # started is To Do whatever stamp it carries; splitting them out gave the
        # board a Backlog column that meant "the author has not pressed Assign
        # yet", which is a detail of this system rather than a state of the work
        # (Phase TASK-SIMPLIFICATION).
        "statuses": [Status.DRAFT, Status.ASSIGNED, Status.ACCEPTED],
    },
    {
        "key": "in_progress",
        "label": "In Progress",
        # Blocked work is started work that cannot finish - see the module
        # docstring on why it is here rather than in a siding.
        "statuses": [Status.IN_PROGRESS, Status.BLOCKED],
    },
    {
        "key": "review",
        "label": "Review",
        "statuses": [Status.UNDER_REVIEW],
    },
    {
        "key": "done",
        "label": "Done",
        # Completed AND Closed - approved work and verified work. Both mean the
        # work is done; the card still shows its exact status, and the Review
        # column is where anything actually waiting sits.
        "statuses": [Status.COMPLETED, Status.CLOSED],
    },
]

# The DEPARTMENT board (Phase TASK-GOVERNANCE-HARDENING) used to be these
# columns minus Backlog. Backlog is gone for everybody now
# (Phase TASK-SIMPLIFICATION), so the two boards are the same four columns and
# this alias is kept only so `?variant=department` stays a valid request.
DEPARTMENT_COLUMNS = COLUMNS

# Flattened for querying: every status the board will show.
BOARD_STATUSES = [status for column in COLUMNS for status in column["statuses"]]

# status -> column key, for bucketing rows in one pass rather than scanning the
# column list per task.
COLUMN_FOR_STATUS = {
    status: column["key"] for column in COLUMNS for status in column["statuses"]
}


def bucket(tasks, columns=None):
    """
    Group an iterable of tasks into the four columns, in column order.

    Returns a list of {key, label, statuses, count, tasks}. Columns with nothing
    in them are KEPT: an empty Review column says nothing is waiting on a
    reviewer, which is information a manager opening the board is looking for.
    """
    columns = columns or COLUMNS
    keys = {column["key"] for column in columns}
    grouped = {column["key"]: [] for column in columns}
    for task in tasks:
        key = COLUMN_FOR_STATUS.get(task.status)
        if key in keys:
            grouped[key].append(task)
    return [
        {
            "key": column["key"],
            "label": column["label"],
            "statuses": list(column["statuses"]),
            "count": len(grouped[column["key"]]),
            "tasks": grouped[column["key"]],
        }
        for column in columns
    ]
