"""Cluster 2 regressions — authorization (bugs 4, 5).

  bug 4  any manager could approve their OWN take-out request (no segregation of
         duties, unlike leaves which guards this)
  bug 5  a manager with department_ref=None filtered on `department_id=None`, which
         compiles to IS NULL and matched every unscoped row
"""
import pytest

from inventory.models import InventoryItem, TakeOutRequest
from inventory import services
from users.models import User

pytestmark = pytest.mark.django_db


def _mk_request(item, requester, today, soon):
    return services.create_takeout(
        item=item, requester=requester, purpose=TakeOutRequest.Purpose.HOME,
        reason="need it", expected_out_date=today, expected_return_date=soon)


# --------------------------------------------------------------------------- #
# BUG 4 — no self-approval
# --------------------------------------------------------------------------- #
def test_dept_head_cannot_approve_own_takeout(auth, item, head, today, soon):
    req = _mk_request(item, head, today, soon)

    resp = auth(head).post(f"/api/v1/inventory/takeouts/{req.id}/approve/", {}, format="json")

    assert resp.status_code == 403
    assert "your own" in str(resp.data).lower()
    req.refresh_from_db()
    item.refresh_from_db()
    assert req.status == TakeOutRequest.Status.PENDING
    assert item.status != InventoryItem.Status.OUT


@pytest.mark.parametrize("role_fixture", ["hr", "admin"])
def test_no_manager_role_can_approve_own_takeout(auth, item, today, soon, request, role_fixture):
    actor = request.getfixturevalue(role_fixture)
    req = _mk_request(item, actor, today, soon)

    resp = auth(actor).post(f"/api/v1/inventory/takeouts/{req.id}/approve/", {}, format="json")

    assert resp.status_code == 403
    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.PENDING


def test_another_manager_can_approve_that_request(auth, item, head, hr, today, soon):
    """The request must still be actionable — by a DIFFERENT authorized approver."""
    req = _mk_request(item, head, today, soon)

    resp = auth(hr).post(f"/api/v1/inventory/takeouts/{req.id}/approve/", {}, format="json")

    assert resp.status_code == 200
    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.APPROVED
    assert req.approver_id == hr.id


def test_manager_can_still_approve_someone_elses_request(auth, item, employee, head, today, soon):
    req = _mk_request(item, employee, today, soon)

    resp = auth(head).post(f"/api/v1/inventory/takeouts/{req.id}/approve/", {}, format="json")

    assert resp.status_code == 200
    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.APPROVED


# --------------------------------------------------------------------------- #
# BUG 5 — a manager with no department sees nothing department-scoped
# --------------------------------------------------------------------------- #
@pytest.fixture
def headless(db):
    return User.objects.create_user(
        username="inv_headless", email="inv_headless@nif.test", password="pass12345",
        first_name="Headless", last_name="T", role=User.Roles.CHECKER,
        department_ref=None)


# --------------------------------------------------------------------------- #
# Phase ASSET-TRANSFER-GOVERNANCE
#
# The three tests that used to live here asserted the OPPOSITE of what follows: a
# Department Head saw only their own department, and a head with no department saw
# nothing at all. The brief reverses that deliberately - a head now accounts for
# the whole organisation's assets - so the tests are rewritten rather than
# deleted, and what replaces them is the other half of the bargain: a head who can
# see everything must be able to change nothing.
# --------------------------------------------------------------------------- #
def test_a_head_sees_every_asset_in_every_department(auth, head, eng, ops):
    mine = InventoryItem.objects.create(asset_code="NIF-INV-M1", name="Mine", department=eng)
    theirs = InventoryItem.objects.create(asset_code="NIF-INV-O1", name="Theirs", department=ops)
    nobodys = InventoryItem.objects.create(asset_code="NIF-INV-NULL", name="Unscoped")

    resp = auth(head).get("/api/v1/inventory/items/")

    assert resp.status_code == 200
    body = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
    codes = {r["asset_code"] for r in body}
    assert {mine.asset_code, theirs.asset_code, nobodys.asset_code} <= codes


def test_a_head_with_no_department_still_sees_everything(auth, headless, eng):
    """
    The old rule made a department-less head blind, because scoping on a null
    department compiled to IS NULL and would have matched every unscoped row. With
    no scoping left there is nothing to guard against, and a head whose department
    has simply not been recorded is no longer locked out of their own job.
    """
    InventoryItem.objects.create(asset_code="NIF-INV-ENG", name="Eng item", department=eng)
    InventoryItem.objects.create(asset_code="NIF-INV-NULL9", name="Unscoped", department=None)

    resp = auth(headless).get("/api/v1/inventory/items/")

    assert resp.status_code == 200
    body = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
    assert {"NIF-INV-ENG", "NIF-INV-NULL9"} <= {r["asset_code"] for r in body}


def test_a_head_sees_who_holds_what_across_the_organisation(auth, headless, admin, eng):
    """The 'who has what' board answers "all owners", so it is not scoped either."""
    orphan_item = InventoryItem.objects.create(asset_code="NIF-INV-NULL2", name="Unscoped2")
    nobody = User.objects.create_user(
        username="inv_nodept", email="inv_nodept@nif.test", password="pass12345",
        role=User.Roles.MAKER, department_ref=None)
    services.assign_item(orphan_item.id, nobody, admin)

    resp = auth(headless).get("/api/v1/inventory/assignments/")

    assert resp.status_code == 200
    body = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
    assert orphan_item.asset_code in {r["item_code"] for r in body}


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/api/v1/inventory/items/", {"name": "New", "asset_type": "it"}),
    ("patch", "ITEM", {"name": "Renamed by a head"}),
    ("delete", "ITEM", None),
])
def test_a_head_cannot_write_to_the_register(auth, head, item, method, path, payload):
    """
    Read-only means read-only. Seeing every asset in the organisation must not come
    with the right to edit any of them - that pairing is the whole reason the
    permission class stopped answering read and write with one question.
    """
    url = f"/api/v1/inventory/items/{item.id}/" if path == "ITEM" else path
    resp = getattr(auth(head), method)(url, payload, format="json") if payload is not None \
        else getattr(auth(head), method)(url)
    assert resp.status_code == 403, f"{method} {url} returned {resp.status_code}"


@pytest.mark.parametrize("verb", ["assign", "handover", "return"])
def test_a_head_cannot_move_custody(auth, head, admin, employee, stock_item, verb):
    """
    "Cannot change ownership", enforced on the three endpoints that change it.
    Take-outs and the assignment board are deliberately NOT in this list: neither
    changes who owns an asset, and the brief does not take them away.
    """
    if verb != "assign":
        services.assign_item(stock_item.id, employee, admin)
    body = {"assigned_to": str(employee.id)} if verb != "return" else {"return_condition": "good"}
    resp = auth(head).post(f"/api/v1/inventory/items/{stock_item.id}/{verb}/", body,
                           format="json")
    assert resp.status_code == 403


def test_departmentless_head_sees_only_own_takeouts(auth, headless, employee, item, today, soon):
    """A None department must fall back to own-requests-only, not every unscoped one."""
    orphan_item = InventoryItem.objects.create(asset_code="NIF-INV-NULL3", name="Unscoped3")
    someone_elses = _mk_request(orphan_item, employee, today, soon)  # department=None
    mine = _mk_request(item, headless, today, soon)

    resp = auth(headless).get("/api/v1/inventory/takeouts/")

    assert resp.status_code == 200
    body = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
    refs = {r["reference"] for r in body}
    assert mine.reference in refs
    assert someone_elses.reference not in refs


def test_admin_still_sees_everything(auth, admin, eng, ops):
    InventoryItem.objects.create(asset_code="NIF-INV-A1", name="A", department=eng)
    InventoryItem.objects.create(asset_code="NIF-INV-A2", name="B", department=ops)
    InventoryItem.objects.create(asset_code="NIF-INV-A3", name="C", department=None)

    resp = auth(admin).get("/api/v1/inventory/items/")

    body = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
    codes = [r["asset_code"] for r in body]
    for c in ("NIF-INV-A1", "NIF-INV-A2", "NIF-INV-A3"):
        assert c in codes


def test_employee_cannot_list_items(auth, employee, item):
    resp = auth(employee).get("/api/v1/inventory/items/")
    assert resp.status_code == 403
