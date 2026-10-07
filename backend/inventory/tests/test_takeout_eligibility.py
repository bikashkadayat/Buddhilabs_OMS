"""
Phase 70.19 — the take-out eligible-asset engine.

Two things are being defended here, and they are different:

  * the SELECTOR shows the right assets (70.19-A/B/C), and
  * the CREATE path refuses everything the selector did not show (70.19-D).

Phase 70.18 found those two disagreeing: the list hid a colleague's laptop and the
create path accepted it anyway. So every scope test below has an ownership twin —
proving a role cannot SEE an asset is only half a control if it can still POST it.
"""
from datetime import timedelta

import pytest

from inventory import services, takeout_eligibility as elig
from inventory.models import InventoryItem, ItemAssignment, TakeOutRequest

pytestmark = pytest.mark.django_db

URL = "/api/v1/inventory/takeout/eligible-assets/"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _assign(item, user, by):
    item.status = InventoryItem.Status.ASSIGNED
    item.save(update_fields=["status"])
    return ItemAssignment.objects.create(
        item=item, item_code=item.asset_code, item_name=item.name,
        assigned_to=user, assigned_to_name=user.get_full_name(),
        assigned_by=by, is_active=True)


def _codes(response):
    return sorted(r["asset_code"] for r in response.data["results"])


def _mkitem(code, dept, status=InventoryItem.Status.AVAILABLE, name="Asset"):
    return InventoryItem.objects.create(
        asset_code=code, name=name, department=dept, status=status)


def _post_takeout(client, item, today, soon):
    return client.post("/api/v1/inventory/takeouts/", {
        "item": str(item.id), "purpose": "home", "reason": "test",
        "expected_out_date": str(today), "expected_return_date": str(soon),
    }, format="json")


# --------------------------------------------------------------------------- #
# 70.19-A — the endpoint exists and is reachable by the people it is for
# --------------------------------------------------------------------------- #
def test_employee_may_call_the_endpoint_at_all(employee, auth):
    """The whole point: /inventory/items/ answers 403 here, this must not."""
    assert auth(employee).get(URL).status_code == 200


def test_anonymous_is_refused(api):
    assert api.get(URL).status_code in (401, 403)


def test_no_assets_returns_empty_list_with_a_reason_not_an_error(employee, auth, stock_item):
    """Stock exists but none of it is theirs -> 200 + explanation, never a bare []."""
    r = auth(employee).get(URL)
    assert r.status_code == 200
    assert r.data["count"] == 0
    assert r.data["results"] == []
    assert "assigned to you" in r.data["empty_reason"]


# --------------------------------------------------------------------------- #
# 70.19-B — role scope
# --------------------------------------------------------------------------- #
def test_employee_sees_one_assigned_asset(employee, auth, item, admin):
    _assign(item, employee, admin)
    r = auth(employee).get(URL)
    assert _codes(r) == [item.asset_code]
    assert r.data["scope"] == elig.SCOPE_OWN
    assert r.data["results"][0]["is_mine"] is True


def test_employee_sees_multiple_assigned_assets(employee, auth, admin, eng):
    for code in ("NIF-INV-M1", "NIF-INV-M2", "NIF-INV-M3"):
        _assign(_mkitem(code, eng), employee, admin)
    assert _codes(auth(employee).get(URL)) == ["NIF-INV-M1", "NIF-INV-M2", "NIF-INV-M3"]


def test_employee_does_not_see_stock_or_a_colleagues_asset(
        employee, other_employee, auth, admin, eng, stock_item):
    theirs = _assign(_mkitem("NIF-INV-OTHER", eng), other_employee, admin).item
    r = auth(employee).get(URL)
    assert r.data["count"] == 0
    assert theirs.asset_code not in _codes(r)
    assert stock_item.asset_code not in _codes(r)


def test_supervisor_is_scoped_to_own_assets_only(supervisor, auth, admin, eng, stock_item):
    """Seniority is about approving other people's requests, not taking their kit."""
    _assign(_mkitem("NIF-INV-SUP", eng), supervisor, admin)
    r = auth(supervisor).get(URL)
    assert _codes(r) == ["NIF-INV-SUP"]
    assert r.data["scope"] == elig.SCOPE_OWN


def test_inventory_officer_sees_own_assets_plus_stock(officer, auth, admin, eng, stock_item):
    _assign(_mkitem("NIF-INV-OFF", eng), officer, admin)
    r = auth(officer).get(URL)
    assert _codes(r) == ["NIF-INV-OFF", stock_item.asset_code]
    assert r.data["scope"] == elig.SCOPE_OWN_PLUS_STOCK


def test_department_head_sees_own_department(head, auth, eng, ops, stock_item):
    _mkitem("NIF-INV-OPS", ops)          # another department's stock
    r = auth(head).get(URL)
    assert stock_item.asset_code in _codes(r)     # eng stock, their department
    assert "NIF-INV-OPS" not in _codes(r)
    assert r.data["scope"] == elig.SCOPE_DEPARTMENT


def test_department_head_without_a_department_falls_back_to_own_assets(
        head, auth, admin, eng, stock_item):
    """A None department must not compile to `IS NULL` and match everything."""
    head.department_ref = None
    head.save(update_fields=["department_ref"])
    _assign(_mkitem("NIF-INV-HEADOWN", eng), head, admin)
    assert _codes(auth(head).get(URL)) == ["NIF-INV-HEADOWN"]


@pytest.mark.parametrize("who", ["hr", "admin"])
def test_hr_and_admin_see_every_eligible_asset(who, request, auth, eng, ops, stock_item):
    user = request.getfixturevalue(who)
    _mkitem("NIF-INV-OPS", ops)
    r = auth(user).get(URL)
    assert {stock_item.asset_code, "NIF-INV-OPS"} <= set(_codes(r))
    assert r.data["scope"] == elig.SCOPE_ALL


# --------------------------------------------------------------------------- #
# 70.19-C — status eligibility
# --------------------------------------------------------------------------- #
BLOCKED = [
    InventoryItem.Status.PROCUREMENT, InventoryItem.Status.RECEIVED,
    InventoryItem.Status.OUT, InventoryItem.Status.MAINTENANCE,
    InventoryItem.Status.RETIRED, InventoryItem.Status.DISPOSED,
    InventoryItem.Status.ARCHIVED,
]


@pytest.mark.parametrize("status", BLOCKED)
def test_blocked_statuses_are_never_eligible(status, admin, auth, eng):
    """PROCUREMENT / RECEIVED / DISPOSED / ARCHIVED were takeable before 70.19."""
    blocked = _mkitem("NIF-INV-BLOCKED", eng, status=status)
    assert blocked.asset_code not in _codes(auth(admin).get(URL))
    with pytest.raises(Exception):
        services.assert_item_state_takeable(blocked)


@pytest.mark.parametrize("status", [InventoryItem.Status.AVAILABLE, InventoryItem.Status.ASSIGNED])
def test_eligible_statuses_pass_the_gate(status, eng):
    services.assert_item_state_takeable(_mkitem("NIF-INV-OK", eng, status=status))


def test_a_blocked_asset_assigned_to_you_is_still_blocked(employee, auth, admin, eng):
    """Ownership must not be able to override the lifecycle."""
    item = _assign(_mkitem("NIF-INV-MAINT", eng), employee, admin)
    item.item.status = InventoryItem.Status.MAINTENANCE
    item.item.save(update_fields=["status"])
    assert auth(employee).get(URL).data["count"] == 0


# --------------------------------------------------------------------------- #
# 70.19-D — ownership validation on CREATE (the bypass Phase 70.18 found)
# --------------------------------------------------------------------------- #
def test_employee_cannot_take_out_a_colleagues_asset(
        employee, other_employee, auth, admin, eng, today, soon):
    theirs = _assign(_mkitem("NIF-INV-THEIRS", eng), other_employee, admin).item
    r = _post_takeout(auth(employee), theirs, today, soon)
    assert r.status_code == 403
    assert not TakeOutRequest.objects.filter(item=theirs).exists()


def test_employee_cannot_take_out_unassigned_stock(employee, auth, stock_item, today, soon):
    """Previously 201: the item was invisible in the list but the POST succeeded."""
    r = _post_takeout(auth(employee), stock_item, today, soon)
    assert r.status_code == 403
    assert not TakeOutRequest.objects.filter(item=stock_item).exists()


def test_employee_can_take_out_their_own_asset(employee, auth, item, admin, today, soon):
    _assign(item, employee, admin)
    assert _post_takeout(auth(employee), item, today, soon).status_code == 201


def test_inventory_officer_may_take_out_stock(officer, auth, stock_item, today, soon):
    assert _post_takeout(auth(officer), stock_item, today, soon).status_code == 201


def test_admin_override_is_allowed(admin, auth, other_employee, eng, today, soon):
    theirs = _assign(_mkitem("NIF-INV-ADMINOVR", eng), other_employee, admin).item
    assert _post_takeout(auth(admin), theirs, today, soon).status_code == 201


def test_blocked_status_is_refused_on_create_even_for_admin(admin, auth, eng, today, soon):
    disposed = _mkitem("NIF-INV-DISP", eng, status=InventoryItem.Status.DISPOSED)
    assert _post_takeout(auth(admin), disposed, today, soon).status_code in (400, 403)


def test_the_selector_and_the_create_path_agree(
        employee, other_employee, auth, admin, eng, stock_item, today, soon):
    """
    The invariant Phase 70.18 broke: everything visible is postable, and everything
    postable is visible. Checked over a mixed register rather than asserted.
    """
    mine = _assign(_mkitem("NIF-INV-AGREE1", eng), employee, admin).item
    _assign(_mkitem("NIF-INV-AGREE2", eng), other_employee, admin)
    _mkitem("NIF-INV-AGREE3", eng, status=InventoryItem.Status.DISPOSED)

    client = auth(employee)
    visible = set(_codes(client.get(URL)))
    assert visible == {mine.asset_code}

    for candidate in InventoryItem.objects.all():
        allowed = candidate.asset_code in visible
        got = _post_takeout(client, candidate, today, soon).status_code
        assert (got == 201) is allowed, f"{candidate.asset_code}: {got}, visible={allowed}"


# --------------------------------------------------------------------------- #
# 70.19-G — the selector carries what it has to display
# --------------------------------------------------------------------------- #
def test_row_carries_code_name_category_and_holder(employee, auth, admin, eng):
    from inventory.models import InventoryCategory
    cat, _ = InventoryCategory.objects.get_or_create(name="Laptop")
    item = _mkitem("NIF-INV-DISP1", eng, name="Dell Latitude 7440")
    item.category = cat
    item.save(update_fields=["category"])
    _assign(item, employee, admin)

    row = auth(employee).get(URL).data["results"][0]
    assert row["asset_code"] == "NIF-INV-DISP1"
    assert row["name"] == "Dell Latitude 7440"
    assert row["category_name"] == "Laptop"
    assert row["current_holder"] == employee.get_full_name()
    assert row["is_mine"] is True


def test_search_narrows_by_code_name_and_category(employee, auth, admin, eng):
    from inventory.models import InventoryCategory
    cat, _ = InventoryCategory.objects.get_or_create(name="Projector")
    laptop = _mkitem("NIF-INV-S1", eng, name="Dell Latitude")
    proj = _mkitem("NIF-INV-S2", eng, name="Epson Beamer")
    proj.category = cat
    proj.save(update_fields=["category"])
    _assign(laptop, employee, admin)
    _assign(proj, employee, admin)
    c = auth(employee)

    assert _codes(c.get(URL, {"search": "Latitude"})) == ["NIF-INV-S1"]
    assert _codes(c.get(URL, {"search": "S2"})) == ["NIF-INV-S2"]
    assert _codes(c.get(URL, {"search": "Projector"})) == ["NIF-INV-S2"]


def test_an_asset_is_not_listed_twice_after_being_reassigned(employee, auth, admin, other_employee, eng):
    """Assignment history multiplies the join; the selector must still show one row."""
    item = _mkitem("NIF-INV-DUP", eng)
    _assign(item, other_employee, admin)
    services.assign_item(item.id, employee, admin)
    assert _codes(auth(employee).get(URL)) == ["NIF-INV-DUP"]


# --------------------------------------------------------------------------- #
# 70.19-H — audit logging
# --------------------------------------------------------------------------- #
def test_opening_the_form_is_logged(employee, auth):
    from audit.models import AuditLog
    auth(employee).get(URL)
    entry = AuditLog.objects.filter(changes__event="TAKEOUT_FORM_OPENED").first()
    assert entry is not None and entry.actor_id == employee.id
    assert entry.changes["scope"] == elig.SCOPE_OWN


def test_a_refused_takeout_is_logged_with_the_control_that_fired(
        employee, other_employee, auth, admin, eng, today, soon):
    from audit.models import AuditLog
    theirs = _assign(_mkitem("NIF-INV-AUDIT", eng), other_employee, admin).item
    _post_takeout(auth(employee), theirs, today, soon)
    entry = AuditLog.objects.filter(changes__event="TAKEOUT_PERMISSION_REJECTED").first()
    assert entry is not None
    assert entry.changes["denial"] == "ownership"
    assert entry.changes["item_code"] == "NIF-INV-AUDIT"


def test_a_status_refusal_is_labelled_status_not_ownership(employee, auth, admin, eng, today, soon):
    from audit.models import AuditLog
    a = _assign(_mkitem("NIF-INV-AUDIT2", eng), employee, admin)
    a.item.status = InventoryItem.Status.RETIRED
    a.item.save(update_fields=["status"])
    _post_takeout(auth(employee), a.item, today, soon)
    entry = AuditLog.objects.filter(changes__event="TAKEOUT_PERMISSION_REJECTED").first()
    assert entry is not None and entry.changes["denial"] == "status"


# --------------------------------------------------------------------------- #
# regression guard — the generic register stays shut
# --------------------------------------------------------------------------- #
def test_the_generic_item_list_is_still_denied_to_employees(employee, auth):
    """
    70.19 must not have widened /inventory/items/ as a side effect. The dropdown was
    fixed by giving it its own endpoint, NOT by opening the register.
    """
    assert auth(employee).get("/api/v1/inventory/items/").status_code == 403
