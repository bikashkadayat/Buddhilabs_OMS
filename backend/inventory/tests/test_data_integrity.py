"""Cluster 3 regressions — data integrity & validation (bugs 6, 8) + the bug 2
stuck-record repair command.

  bug 6  ItemAssignment.item was CASCADE -> deleting an asset erased custody history
  bug 8  _parse_date swallowed malformed input and silently substituted "today"
"""
from io import StringIO

import pytest
from django.core.management import call_command

from inventory import services
from inventory.models import InventoryItem, ItemAssignment, TakeOutRequest

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# BUG 6 — custody history survives item deletion
# --------------------------------------------------------------------------- #
def test_assignment_history_survives_item_deletion(item, employee, admin):
    services.assign_item(item.id, employee, admin)
    services.return_item(item.id, admin)
    code, name = item.asset_code, item.name

    item.delete()

    row = ItemAssignment.objects.get(assigned_to=employee)
    assert row.item_id is None, "FK must be SET_NULL, not cascade-deleted"
    assert row.item_code == code, "snapshot must keep the asset code readable"
    assert row.item_name == name
    assert row.assigned_to_name  # who held it is still known
    assert str(row)  # __str__ must not blow up on a NULL item


def test_assignment_snapshot_is_written_on_assign(item, employee, admin):
    a = services.assign_item(item.id, employee, admin)
    assert a.item_code == item.asset_code
    assert a.item_name == item.name


def test_assignment_history_api_readable_after_deletion(auth, item, employee, admin):
    services.assign_item(item.id, employee, admin)
    services.return_item(item.id, admin)
    item.delete()

    resp = auth(admin).get("/api/v1/inventory/assignments/?all=1")

    assert resp.status_code == 200
    body = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
    row = next(r for r in body if r["assigned_to_name"].startswith("Inv_emp"))
    assert row["item_code"] == "NIF-INV-T001"
    assert row["item_name"] == "Test Laptop"


def test_takeout_history_still_survives_item_deletion(item, employee, today, soon):
    """Pre-existing behaviour must not regress."""
    req = services.create_takeout(
        item=item, requester=employee, purpose=TakeOutRequest.Purpose.HOME,
        reason="x", expected_out_date=today, expected_return_date=soon)
    item.delete()
    req.refresh_from_db()
    assert req.item_id is None
    assert req.item_code == "NIF-INV-T001"


# --------------------------------------------------------------------------- #
# BUG 8 — malformed dates are rejected, never silently replaced with today
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45", "31-12-2026", "2026/01/01"])
def test_assign_rejects_malformed_date(auth, item, employee, admin, bad):
    resp = auth(admin).post(
        f"/api/v1/inventory/items/{item.id}/assign/",
        {"assigned_to": str(employee.id), "assigned_date": bad}, format="json")

    assert resp.status_code == 400
    assert "assigned_date" in resp.data
    assert not ItemAssignment.objects.filter(item=item).exists(), \
        "nothing may be written when the date is invalid"


def test_assign_accepts_a_valid_date(auth, item, employee, admin, today):
    resp = auth(admin).post(
        f"/api/v1/inventory/items/{item.id}/assign/",
        {"assigned_to": str(employee.id), "assigned_date": "2026-03-04"}, format="json")

    assert resp.status_code == 200
    assert str(ItemAssignment.objects.get(item=item).assigned_date) == "2026-03-04"


def test_assign_without_a_date_defaults_to_today(auth, item, employee, admin, today):
    """Omitting the optional field keeps the documented default — only *malformed*
    input is an error."""
    resp = auth(admin).post(
        f"/api/v1/inventory/items/{item.id}/assign/",
        {"assigned_to": str(employee.id)}, format="json")

    assert resp.status_code == 200
    assert ItemAssignment.objects.get(item=item).assigned_date == today


def test_handover_rejects_malformed_date(auth, item, employee, head, admin):
    services.assign_item(item.id, employee, admin)
    resp = auth(admin).post(
        f"/api/v1/inventory/items/{item.id}/handover/",
        {"assigned_to": str(head.id), "assigned_date": "garbage"}, format="json")

    assert resp.status_code == 400
    assert ItemAssignment.objects.get(item=item, is_active=True).assigned_to_id == employee.id


# --------------------------------------------------------------------------- #
# BUG 2 — repair command for historically stuck records
# --------------------------------------------------------------------------- #
def _stick(item, requester, today, soon):
    """Recreate the damage the old return_item bug left behind: an APPROVED
    take-out whose item is no longer out."""
    req = services.create_takeout(
        item=item, requester=requester, purpose=TakeOutRequest.Purpose.HOME,
        reason="x", expected_out_date=today, expected_return_date=soon)
    TakeOutRequest.objects.filter(pk=req.pk).update(status=TakeOutRequest.Status.APPROVED)
    InventoryItem.objects.filter(pk=item.pk).update(status=InventoryItem.Status.AVAILABLE)
    return req


def test_fix_stuck_takeouts_dry_run_reports_and_changes_nothing(item, employee, today, soon):
    req = _stick(item, employee, today, soon)
    out = StringIO()

    with pytest.raises(SystemExit) as exc:
        call_command("fix_stuck_takeouts", stdout=out)

    assert exc.value.code == 1, "dry run must exit non-zero so it can gate CI"
    assert req.reference in out.getvalue()
    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.APPROVED, "dry run must not write"


def test_fix_stuck_takeouts_closes_stuck_records(item, employee, today, soon):
    req = _stick(item, employee, today, soon)
    out = StringIO()

    call_command("fix_stuck_takeouts", "--fix", stdout=out)

    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.RETURNED
    assert req.actual_return_date is not None
    # The item is usable again.
    services.assert_item_takeable(item)


def test_fix_stuck_takeouts_leaves_genuinely_out_items_alone(item, employee, admin, today, soon):
    req = services.create_takeout(
        item=item, requester=employee, purpose=TakeOutRequest.Purpose.HOME,
        reason="x", expected_out_date=today, expected_return_date=soon)
    services.approve_takeout(req.id, admin)  # item really is OUT
    out = StringIO()

    call_command("fix_stuck_takeouts", "--fix", stdout=out)

    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.APPROVED, "a live take-out must not be closed"


def test_fix_stuck_takeouts_clean_db_is_a_noop(db):
    out = StringIO()
    call_command("fix_stuck_takeouts", stdout=out)
    assert "No stuck take-out requests" in out.getvalue()
