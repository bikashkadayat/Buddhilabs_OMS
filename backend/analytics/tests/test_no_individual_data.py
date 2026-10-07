"""The phase's hardest constraint, enforced mechanically.

"Do NOT score employees." A department is the smallest unit of analysis in
Phase 10, and the easiest way to break that is by accident -- a convenient
``employee_name`` added to a payload during a debugging session and never
removed. So rather than trusting review, every analytics response is walked and
searched for any trace of a person.

Individual detail is not lost: it lives in the Phase 9 workforce endpoints and
the Phase 9 per-employee reports, which carry their own scoping.
"""
import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

ENDPOINTS = [
    "analytics-executive", "analytics-hr", "analytics-management",
    "analytics-attendance", "analytics-departments", "analytics-leave",
    "analytics-wfh", "analytics-comp-off", "analytics-devices",
    "analytics-meta",
]

# Field names that would carry a person. Device rows legitimately carry an `id`
# (a terminal is not a person), so `id` alone is not on this list -- the id
# check below is done by value against the real user primary keys instead.
FORBIDDEN_KEYS = {
    "employee", "employee_id", "employee_name", "user", "user_id", "username",
    "first_name", "last_name", "full_name", "email", "name_of_employee",
}
# Device payloads legitimately describe hardware by name.
ALLOWED_NAME_PATHS = ("devices", "fleet")


@pytest.fixture
def populated(org, last_month_days, mark, take_leave, comp_entry, wfh_request,
              device, enrol):
    for member in org["eng"] + org["ops"] + org["tiny"]:
        mark(member, last_month_days[:5], overtime_hours="1.00")
    take_leave(org["eng"][0], last_month_days[-1:])
    comp_entry(org["eng"][0], "1.00", source_date=last_month_days[0])
    wfh_request(org["ops"][0], last_month_days[0])
    gate = device("Main Gate")
    enrol(gate, user=org["eng"][0])
    return org


def walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key, value
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


@pytest.mark.parametrize("url_name", ENDPOINTS)
def test_no_person_shaped_key_appears(url_name, populated, auth):
    response = auth(populated["admin"]).get(reverse(url_name))
    assert response.status_code == 200
    for path, key, _value in walk(response.data):
        assert key not in FORBIDDEN_KEYS, f"{url_name} exposes '{key}' at {path}"


@pytest.mark.parametrize("url_name", ENDPOINTS)
def test_no_employee_name_appears_anywhere(url_name, populated, auth):
    response = auth(populated["admin"]).get(reverse(url_name))
    body = str(response.data)
    everyone = (populated["eng"] + populated["ops"] + populated["tiny"]
                + [populated["manager"], populated["hr"], populated["admin"]])
    for person in everyone:
        assert person.get_full_name() not in body, f"{url_name} names {person}"
        assert person.username not in body
        assert person.email not in body


@pytest.mark.parametrize("url_name", ENDPOINTS)
def test_no_user_primary_key_appears(url_name, populated, auth):
    """A UUID is not obviously a person on sight, which is exactly why it needs
    checking by value rather than by field name."""
    response = auth(populated["admin"]).get(reverse(url_name))
    body = str(response.data)
    everyone = (populated["eng"] + populated["ops"] + populated["tiny"]
                + [populated["manager"], populated["hr"], populated["admin"]])
    for person in everyone:
        assert str(person.pk) not in body, f"{url_name} leaks the id of {person}"


def test_the_guard_would_actually_catch_a_leak(populated):
    """A negative control: if the walk cannot find a planted name, the three
    tests above prove nothing."""
    person = populated["eng"][0]
    planted = {"data": {"rows": [{"employee_name": person.get_full_name()}]}}
    keys = {key for _path, key, _value in walk(planted)}
    assert "employee_name" in keys
    assert person.get_full_name() in str(planted)
