"""
Shared fixtures for the circular suite.

Every test that needs a circular in flight goes through these helpers, so there is
exactly one definition of "a routed circular". Both other document modules learned
this the hard way: when each test built its own routing by hand, an engine change
broke all of them at once.
"""
import pytest
from rest_framework.test import APIClient

from circulars.services import MIN_REMARK_LENGTH
from circulars.models import Circular
from circulars.services import generate_circular_number
from users.models import User


@pytest.fixture
def api():
    return APIClient()


def make_user(username, role=User.Roles.MAKER, designation="Officer",
              department="Finance", **extra):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.replace("_", " ").title(), last_name="T",
        role=role, designation=designation, department=department, **extra)


@pytest.fixture
def cast(db):
    """The people a circular needs, plus the controls its tests need."""
    return {
        "author": make_user("cir_author"),
        "reviewer": make_user("cir_reviewer", designation="Internal Auditor"),
        "reviewer_two": make_user("cir_reviewer2", designation="Compliance"),
        # HR is the APPROVER role - the stored values are maker/checker/approver/
        # admin and the business labels sit on top of them.
        "issuer": make_user("cir_issuer", User.Roles.APPROVER, "Chief Executive"),
        "hr": make_user("cir_hr", User.Roles.APPROVER, "HR Manager"),
        "admin": make_user("cir_admin", User.Roles.ADMIN, "Administrator"),
        "head": make_user("cir_head", User.Roles.CHECKER, "Dept Head"),
        "staff_a": make_user("cir_staff_a", designation="Officer"),
        "staff_b": make_user("cir_staff_b", designation="Officer"),
        # In a different department, on nothing. The control subject for every
        # visibility test.
        "outsider": make_user("cir_outsider", department="Logistics"),
    }


def make_circular(cast, **kwargs):
    fields = {
        "subject": "Revised office hours from the first of next month",
        "content": "<p>With effect from the first of next month, office hours "
                   "will be 09:00 to 17:00 Sunday to Thursday.</p>",
        "category": Circular.Category.ADMINISTRATIVE,
        "classification": Circular.Classification.INTERNAL,
        "created_by": cast["author"],
        "status": Circular.Status.DRAFT,
    }
    fields.update(kwargs)
    fields.setdefault("circular_number", generate_circular_number())
    circular = Circular.objects.create(**fields)
    from circulars import workflow
    workflow.record_creation(circular, circular.created_by)
    return circular


@pytest.fixture
def draft(cast):
    return make_circular(cast)


def chain_rows(*pairs):
    """[(user, role_type), ...] -> the payload the chain endpoint accepts."""
    return [{"assignee_id": str(user.id), "role_type": role}
            for user, role in pairs]


def submit(api, circular, *pairs, actor=None):
    """Attach a chain and submit. Defaults to the author."""
    actor = actor or circular.created_by
    api.force_authenticate(actor)
    response = api.post(f"/api/v1/circulars/{circular.id}/send-for-review/",
                        {"workflow": chain_rows(*pairs)}, format="json")
    assert response.status_code == 200, response.data
    circular.refresh_from_db()
    return circular


def act(api, actor, circular, decision="proceed",
        remarks="Checked and in order.".ljust(MIN_REMARK_LENGTH, ".")):
    api.force_authenticate(actor)
    return api.post(f"/api/v1/circulars/{circular.id}/act/",
                    {"decision": decision, "remarks": remarks}, format="json")


def drive_to_issued(api, circular, *pairs):
    """Submit and walk every step, ending at ISSUED."""
    submit(api, circular, *pairs)
    for user, _role in pairs:
        response = act(api, user, circular)
        assert response.status_code == 200, response.data
    circular.refresh_from_db()
    assert circular.status == Circular.Status.ISSUED, circular.status
    return circular


def broadcast(api, circular, actor, audience="organisation", **payload):
    api.force_authenticate(actor)
    return api.post(f"/api/v1/circulars/{circular.id}/broadcast/",
                    {"audience": audience, **payload}, format="json")


def drive_to_broadcast(api, circular, cast, *pairs, audience="organisation",
                       **payload):
    """Issue the circular and broadcast it. The state most tests need."""
    pairs = pairs or ((cast["issuer"], "issuer"),)
    drive_to_issued(api, circular, *pairs)
    response = broadcast(api, circular, cast["issuer"], audience=audience,
                         **payload)
    assert response.status_code == 200, response.data
    circular.refresh_from_db()
    return circular


__all__ = [
    "act", "api", "broadcast", "cast", "chain_rows", "draft", "drive_to_broadcast",
    "drive_to_issued", "make_circular", "make_user", "submit",
]
