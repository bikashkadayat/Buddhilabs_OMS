import pytest
from rest_framework.test import APIClient

from users.models import User
from memos.models import Memo, MemoWorkflowStep
from memos.services import MIN_COMMENT_LENGTH
from tenancy.stamping import stamp_all


# ---------------------------------------------------------------------------
# Matrix-workflow helpers
#
# Every test that needs a memo in flight goes through these, so there is one
# definition of "a routed memo" in the suite. Before the legacy engine was
# removed each test set up its own two-slot routing by hand, which is why so many
# of them broke at once when the columns went.
# ---------------------------------------------------------------------------
def _record_creation(memo, author):
    """Write the "Created" history row the API writes on every real creation."""
    from memos import workflow
    workflow.record_creation(memo, author)


def matrix(*pairs):
    """
    Build a matrix payload from (user, role_type) pairs, in chain order.

        matrix((checker, "reviewer"), (approver, "approver"))
    """
    return [
        {"assignee_id": str(user.id), "role_type": role_type}
        for user, role_type in pairs
    ]


def route(api, author, memo, *pairs):
    """Attach a matrix to `memo` and send it for review as `author`."""
    api.force_authenticate(author)
    response = api.post(
        f"/api/v1/memos/{memo.id}/send-for-review/",
        {"workflow": matrix(*pairs)}, format="json",
    )
    assert response.status_code == 200, response.data
    memo.refresh_from_db()
    return memo


# Long enough for the workflow's minimum-comment policy WHATEVER that minimum
# becomes. Deriving it rather than writing a literal is the point: the floor
# moved once already (mandatory remarks at MIN_COMMENT_LENGTH), and every test
# whose remark happened to be shorter broke at once — one of them by a single
# character. Tests that assert a SHORT remark is REFUSED pass their own literal
# and are deliberately left alone.
VALID_REMARK = "Checked and in order.".ljust(MIN_COMMENT_LENGTH, ".")


def act(api, actor, memo, decision="proceed", remarks=VALID_REMARK):
    """Take the caller's workflow action on `memo`."""
    api.force_authenticate(actor)
    return api.post(
        f"/api/v1/memos/{memo.id}/act/",
        {"decision": decision, "remarks": remarks}, format="json",
    )


def drive_to_approval(api, author, memo, *pairs):
    """Route the memo and walk every step to approval (which auto-archives)."""
    route(api, author, memo, *pairs)
    for user, _role in pairs:
        response = act(api, user, memo)
        assert response.status_code == 200, response.data
    memo.refresh_from_db()
    return memo


@pytest.fixture
def api():
    return APIClient()


def _make_user(username, role, department="Finance"):
    return User.objects.create_user(
        username=username,
        email=f"{username}@nif.test",
        password="pass12345",
        first_name=username.capitalize(),
        last_name="Test",
        role=role,
        department=department,
    )


@pytest.fixture
def maker(db):
    return _make_user("maker1", User.Roles.MAKER)


@pytest.fixture
def other_maker(db):
    return _make_user("maker2", User.Roles.MAKER)


@pytest.fixture
def checker(db):
    return _make_user("checker1", User.Roles.CHECKER)


@pytest.fixture
def other_checker(db):
    return _make_user("checker2", User.Roles.CHECKER)


@pytest.fixture
def approver(db):
    return _make_user("approver1", User.Roles.APPROVER)


@pytest.fixture
def admin(db):
    return _make_user("admin1", User.Roles.ADMIN)


def add_sections(memo, *blocks):
    """
    Give a memo its content blocks (E-memo-manual p.3).

    Defaults to the pair the form opens with - Background and Recommendation -
    so a fixture that does not care about content still looks like a real memo.
    """
    from memos.models import MemoSection
    rows = list(blocks) or [(title, "") for title in MemoSection.DEFAULT_TITLES]
    titles = [row[0] for row in rows]
    for title in MemoSection.DEFAULT_TITLES:
        if title not in titles:
            rows.append((title, ""))
    MemoSection.objects.bulk_create(stamp_all([
        MemoSection(memo=memo, position=index, title=title, body=body)
        for index, (title, body) in enumerate(rows)
    ]))
    return memo


@pytest.fixture
def draft_memo(db, maker):
    """A bare draft memo authored by `maker`."""
    from memos.services import generate_memo_number
    memo = Memo.objects.create(
        subject="Quarterly budget request",
        to_line="CEO",
        memo_type=Memo.MemoType.CONFIDENTIAL,
        status=Memo.Status.DRAFT,
        created_by=maker,
        memo_number=generate_memo_number(Memo.MemoType.CONFIDENTIAL),
    )
    add_sections(memo, ("Background", "<p>Please approve the attached budget.</p>"))
    # The API records this on create; fixtures that build a memo through the ORM
    # must too, or their timelines start a step later than a real memo's.
    _record_creation(memo, maker)
    return memo


@pytest.fixture
def general_draft(db, maker):
    """
    A non-restricted draft. Separate from `draft_memo` (which is CONFIDENTIAL, and
    therefore restricted to its author and the people routed on it) because any
    test about department-level or general visibility needs a memo type the
    confidentiality rules do not lock down.
    """
    from memos.services import generate_memo_number
    memo = Memo.objects.create(
        subject="Offsite budget", to_line="CEO",
        memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
        created_by=maker, memo_number=generate_memo_number(Memo.MemoType.GENERAL),
    )
    add_sections(memo, ("Background", "<p>Request.</p>"))
    _record_creation(memo, maker)
    return memo


@pytest.fixture
def routed_memo(db, api, draft_memo, maker, checker, approver):
    """`draft_memo` sent for review through checker (Reviewer) -> approver."""
    return route(api, maker, draft_memo, (checker, "reviewer"), (approver, "approver"))


@pytest.fixture
def step_of():
    """Fetch a memo's step for a given assignee, for asserting on step state."""
    def _get(memo, user):
        return MemoWorkflowStep.objects.get(memo=memo, assignee=user)
    return _get
