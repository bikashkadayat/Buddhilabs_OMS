"""Cluster 1 regressions — asset-state corruption (bugs 1, 2, 3) + bug 7.

Each test pins a specific defect that was live in production code:
  bug 1  mark_returned flipped an item to 'available' while still assigned
  bug 2  return_item orphaned an APPROVED take-out -> item never takeable again
  bug 3  approve_takeout never re-validated -> retired items could go 'out'
  bug 7  backdated take-out requests were accepted
"""
from datetime import timedelta

import pytest
from rest_framework.exceptions import ValidationError

from inventory import services
from inventory.models import InventoryItem, ItemAssignment, TakeOutRequest

pytestmark = pytest.mark.django_db


def _takeout(item, requester, today, soon, **kw):
    return services.create_takeout(
        item=item, requester=requester, purpose=TakeOutRequest.Purpose.HOME,
        reason=kw.pop("reason", "work from home"),
        expected_out_date=kw.pop("expected_out_date", today),
        expected_return_date=kw.pop("expected_return_date", soon))


# --------------------------------------------------------------------------- #
# BUG 1 — a take-out return must not wipe the underlying assignment
# --------------------------------------------------------------------------- #
def test_takeout_return_keeps_the_assignment(item, employee, admin, today, soon):
    services.assign_item(item.id, employee, admin)
    req = _takeout(item, employee, today, soon)
    services.approve_takeout(req.id, admin)

    services.mark_returned(req.id, admin)

    item.refresh_from_db()
    # The item is back in the office but STILL held by the employee.
    assert item.status == InventoryItem.Status.ASSIGNED
    active = ItemAssignment.objects.get(item=item, is_active=True)
    assert active.assigned_to_id == employee.id
    assert active.returned_at is None


def test_takeout_return_frees_an_unassigned_item(item, employee, admin, today, soon):
    """No holder -> the item genuinely is available again."""
    req = _takeout(item, employee, today, soon)
    services.approve_takeout(req.id, admin)

    services.mark_returned(req.id, admin)

    item.refresh_from_db()
    assert item.status == InventoryItem.Status.AVAILABLE
    assert not ItemAssignment.objects.filter(item=item, is_active=True).exists()


def test_takeout_return_does_not_resurrect_a_retired_item(item, employee, admin, today, soon):
    req = _takeout(item, employee, today, soon)
    services.approve_takeout(req.id, admin)
    InventoryItem.objects.filter(pk=item.pk).update(status=InventoryItem.Status.RETIRED)

    services.mark_returned(req.id, admin)

    item.refresh_from_db()
    assert item.status == InventoryItem.Status.RETIRED


# --------------------------------------------------------------------------- #
# BUG 2 — returning an item must close its APPROVED take-out
# --------------------------------------------------------------------------- #
def test_return_item_closes_approved_takeout_and_frees_future_requests(
        item, employee, admin, today, soon):
    services.assign_item(item.id, employee, admin)
    req = _takeout(item, employee, today, soon)
    services.approve_takeout(req.id, admin)

    services.return_item(item.id, admin)

    req.refresh_from_db()
    item.refresh_from_db()
    assert req.status == TakeOutRequest.Status.RETURNED, "approved take-out must reach a terminal state"
    assert req.actual_return_date is not None
    assert item.status == InventoryItem.Status.AVAILABLE
    # The whole point: the item is takeable again.
    fresh = _takeout(item, employee, today, soon)
    assert fresh.status == TakeOutRequest.Status.PENDING


def test_return_item_leaves_pending_requests_alone(item, employee, admin, today, soon):
    """A PENDING request is still legitimately awaiting a decision — closing it
    would silently discard a real request."""
    services.assign_item(item.id, employee, admin)
    req = _takeout(item, employee, today, soon)

    services.return_item(item.id, admin)

    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.PENDING


def test_mark_returned_makes_item_takeable_again(item, employee, admin, today, soon):
    req = _takeout(item, employee, today, soon)
    services.approve_takeout(req.id, admin)
    services.mark_returned(req.id, admin)

    fresh = _takeout(item, employee, today, soon)
    assert fresh.status == TakeOutRequest.Status.PENDING


# --------------------------------------------------------------------------- #
# BUG 3 — approval must re-validate the item's CURRENT state
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_status,fragment", [
    (InventoryItem.Status.RETIRED, "retired"),
    (InventoryItem.Status.MAINTENANCE, "under maintenance"),
])
def test_cannot_approve_takeout_for_unavailable_item(
        item, employee, admin, today, soon, bad_status, fragment):
    req = _takeout(item, employee, today, soon)
    InventoryItem.objects.filter(pk=item.pk).update(status=bad_status)

    with pytest.raises(ValidationError) as exc:
        services.approve_takeout(req.id, admin)

    assert fragment in str(exc.value)
    item.refresh_from_db()
    req.refresh_from_db()
    assert item.status == bad_status, "a retired/maintenance item must never flip to 'out'"
    assert req.status == TakeOutRequest.Status.PENDING


def test_healthy_request_still_approves(item, employee, admin, today, soon):
    """Re-validation must not block a request against itself (regression guard)."""
    req = _takeout(item, employee, today, soon)

    services.approve_takeout(req.id, admin)

    req.refresh_from_db()
    item.refresh_from_db()
    assert req.status == TakeOutRequest.Status.APPROVED
    assert item.status == InventoryItem.Status.OUT


def test_cannot_approve_takeout_for_deleted_item(item, employee, admin, today, soon):
    req = _takeout(item, employee, today, soon)
    item.delete()

    with pytest.raises(ValidationError):
        services.approve_takeout(req.id, admin)

    req.refresh_from_db()
    assert req.status == TakeOutRequest.Status.PENDING


def test_second_request_cannot_be_approved_once_item_is_out(
        item, employee, head, admin, today, soon):
    """Two pending requests can't normally coexist, but if one is approved the
    other must not also flip the item."""
    first = _takeout(item, employee, today, soon)
    second = TakeOutRequest.objects.create(
        reference="NIF-OUT-TEST-9999", item=item, item_code=item.asset_code,
        item_name=item.name, requested_by=head, requested_by_name="Head",
        purpose=TakeOutRequest.Purpose.HOME, reason="also me",
        expected_out_date=today, expected_return_date=soon,
        status=TakeOutRequest.Status.PENDING)
    services.approve_takeout(first.id, admin)

    with pytest.raises(ValidationError):
        services.approve_takeout(second.id, admin)


# --------------------------------------------------------------------------- #
# BUG 7 — no backdated take-outs
# --------------------------------------------------------------------------- #
def test_backdated_takeout_rejected(item, employee, today):
    with pytest.raises(ValidationError) as exc:
        _takeout(item, employee, today, today,
                 expected_out_date=today - timedelta(days=365),
                 expected_return_date=today - timedelta(days=364))
    assert "past" in str(exc.value)


def test_today_takeout_allowed(item, employee, today, soon):
    assert _takeout(item, employee, today, soon).expected_out_date == today


def test_future_takeout_allowed(eng, employee, today):
    other = InventoryItem.objects.create(
        asset_code="NIF-INV-T002", name="Future Laptop", department=eng)
    later = _takeout(other, employee, today, today,
                     expected_out_date=today + timedelta(days=5),
                     expected_return_date=today + timedelta(days=6))
    assert later.expected_out_date == today + timedelta(days=5)


def test_return_before_out_still_rejected(item, employee, today):
    with pytest.raises(ValidationError) as exc:
        _takeout(item, employee, today, today,
                 expected_out_date=today + timedelta(days=5),
                 expected_return_date=today + timedelta(days=1))
    assert "before" in str(exc.value)
