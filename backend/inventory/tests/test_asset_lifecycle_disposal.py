"""
Asset lifecycle, depreciation and disposal (Phase ASSET-LIFECYCLE-DISPOSAL).

Grouped by the question each part answers: what is it worth, when does its cover
run out, who may take it off the books, and can the record ever be erased.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import AuditLog
from inventory import disposals, reports, services
from inventory.depreciation import add_months, depreciation, months_elapsed
from inventory.management.commands.send_asset_lifecycle_alerts import collect
from inventory.models import (
    AssetDisposal, AssetDisposalEvent, AssetLifecycleEvent, InventoryCategory,
    InventoryItem, ItemAssignment,
)
from notifications.models import Category, Notification
from users.models import User

from .conftest import _user, dispose_via_workflow

Status = AssetDisposal.Status
Event = AssetLifecycleEvent.Event
REASON = "Beyond economic repair after four years of service."


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def laptops(db, eng):
    return InventoryCategory.objects.create(name="Laptops (disposal tests)",
                                            default_useful_life_months=48)


@pytest.fixture
def asset(db, eng, laptops, today):
    """A laptop bought a year ago for 120,000 with a four-year life."""
    return InventoryItem.objects.create(
        asset_code="NIF-INV-DSP1", name="Dell Latitude 5420", department=eng,
        category=laptops, status=InventoryItem.Status.AVAILABLE,
        purchase_date=add_months(today, -12), purchase_cost=Decimal("120000.00"))


def _raise(item, officer, **kw):
    kw.setdefault("disposal_type", AssetDisposal.DisposalType.SCRAP)
    kw.setdefault("reason", REASON)
    return disposals.create_disposal(item=item, requested_by=officer, **kw)


def _drive(disposal, officer, head, admin):
    """Carry an already-raised disposal through both gates."""
    disposals.submit(disposal.id, officer)
    disposals.approve(disposal.id, head, remarks="The department agrees to write it off.")
    return disposals.approve(disposal.id, admin, remarks="Approved for disposal.")


# --------------------------------------------------------------------------- #
# What is it worth
# --------------------------------------------------------------------------- #
def test_month_arithmetic_clamps_to_a_shorter_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 15), 13) == date(2027, 2, 15)
    # A month counts once its day arrives, not before.
    assert months_elapsed(date(2026, 1, 15), date(2026, 2, 14)) == 0
    assert months_elapsed(date(2026, 1, 15), date(2026, 2, 15)) == 1
    assert months_elapsed(date(2026, 1, 31), date(2026, 2, 28)) == 1
    assert months_elapsed(date(2026, 3, 1), date(2026, 1, 1)) == 0, "no negative months"


@pytest.mark.django_db
def test_straight_line_depreciation(asset, today):
    d = depreciation(asset, as_of=today)
    assert d["depreciable"] is True
    assert d["useful_life_months"] == 48 and d["useful_life_source"] == "category"
    assert d["months_elapsed"] == 12
    assert d["monthly_charge"] == Decimal("2500.00")
    assert d["accumulated"] == Decimal("30000.00")
    assert d["book_value"] == Decimal("90000.00")
    assert d["percent_depreciated"] == 25
    assert d["fully_depreciated"] is False
    assert d["end_of_life_date"] == add_months(asset.purchase_date, 48)


@pytest.mark.django_db
def test_an_asset_never_depreciates_below_its_salvage_value(asset, today):
    asset.salvage_value = Decimal("20000.00")
    asset.purchase_date = add_months(today, -60)       # past its four-year life
    asset.save()
    d = depreciation(asset, as_of=today)
    assert d["fully_depreciated"] is True
    # Exactly the salvage value: computed in one division, so the last month
    # cannot land a few paisa above or below it.
    assert d["accumulated"] == Decimal("100000.00")
    assert d["book_value"] == Decimal("20000.00")
    assert d["percent_depreciated"] == 100


@pytest.mark.django_db
def test_the_asset_overrides_the_category_life(asset, today):
    asset.useful_life_months = 24
    asset.save()
    d = depreciation(asset, as_of=today)
    assert d["useful_life_source"] == "asset"
    assert d["monthly_charge"] == Decimal("5000.00")


@pytest.mark.django_db
@pytest.mark.parametrize("missing,wording", [
    ("purchase_cost", "purchase cost"), ("purchase_date", "purchase date"),
])
def test_an_asset_without_the_inputs_says_what_is_missing(asset, missing, wording):
    setattr(asset, missing, None)
    asset.save()
    d = depreciation(asset)
    assert d["depreciable"] is False
    assert wording in d["reason"]
    assert d["book_value"] is None


@pytest.mark.django_db
def test_depreciation_stops_on_the_day_of_disposal(asset, officer, head, admin, today):
    done = dispose_via_workflow(asset, officer, head, admin, reason=REASON)
    asset.refresh_from_db()
    frozen = depreciation(asset, as_of=today + timedelta(days=400))
    assert frozen["frozen_at_disposal"] is True
    assert frozen["book_value"] == done.book_value_at_disposal


# --------------------------------------------------------------------------- #
# When does its cover run out
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.parametrize("days,state", [(-1, "expired"), (30, "expiring"), (400, "active")])
def test_amc_state_is_derived_from_the_date(asset, today, days, state):
    asset.amc_end = today + timedelta(days=days)
    asset.save()
    assert asset.amc_state == state


@pytest.mark.django_db
def test_end_of_life_follows_the_useful_life(asset, today):
    assert asset.end_of_life_state == "in_life"
    asset.purchase_date = add_months(today, -47)      # one month left of 48
    asset.save()
    assert asset.end_of_life_state == "approaching"
    asset.purchase_date = add_months(today, -60)
    asset.save()
    assert asset.end_of_life_state == "past"


@pytest.mark.django_db
def test_alerts_cover_warranty_amc_and_end_of_life_and_skip_disposed(
        asset, officer, head, admin, today, django_capture_on_commit_callbacks):
    asset.warranty_expiry = today + timedelta(days=10)
    asset.amc_end = today + timedelta(days=5)
    asset.purchase_date = add_months(today, -47)
    asset.save()
    kinds = {kind for kind, *_ in collect()}
    assert kinds == {"warranty", "amc", "end_of_life"}

    with django_capture_on_commit_callbacks(execute=True):
        for kind, item, due, state in collect():
            from inventory import notifications as inv_notify
            inv_notify.lifecycle_alert(kind, item, due, state)
    assert Notification.objects.filter(category=Category.INVENTORY_WARRANTY_EXPIRING).exists()
    assert Notification.objects.filter(category=Category.INVENTORY_AMC_EXPIRING).exists()
    assert Notification.objects.filter(category=Category.INVENTORY_END_OF_LIFE).exists()
    before = Notification.objects.count()

    # Running the job again the next morning must not announce the same asset twice.
    with django_capture_on_commit_callbacks(execute=True):
        for kind, item, due, state in collect():
            from inventory import notifications as inv_notify
            inv_notify.lifecycle_alert(kind, item, due, state)
    assert Notification.objects.count() == before

    dispose_via_workflow(asset, officer, head, admin, reason=REASON)
    assert collect() == [], "a disposed asset is not chased for a warranty renewal"


# --------------------------------------------------------------------------- #
# Who may take it off the books
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_the_full_disposal_chain_writes_the_figures_and_the_history(
        asset, officer, head, admin, today):
    disposal = _raise(asset, officer, disposal_type=AssetDisposal.DisposalType.SALE,
                      expected_proceeds=Decimal("40000.00"))
    assert disposal.disposal_number.startswith("DSP-")
    done = _drive(disposal, officer, head, admin)

    asset.refresh_from_db()
    assert done.status == Status.DISPOSED
    assert asset.status == InventoryItem.Status.DISPOSED
    assert asset.disposed_at is not None
    # Book value 90,000 against 40,000 raised: 50,000 written off, no gain.
    assert done.book_value_at_disposal == Decimal("90000.00")
    assert done.proceeds == Decimal("40000.00")
    assert done.written_off == Decimal("50000.00")
    assert done.gain_on_disposal == 0

    assert list(done.events.order_by("sequence").values_list("action", flat=True)) == [
        AssetDisposalEvent.Action.CREATED, AssetDisposalEvent.Action.SUBMITTED,
        AssetDisposalEvent.Action.DEPT_HEAD_APPROVED,
        AssetDisposalEvent.Action.ADMIN_APPROVED, AssetDisposalEvent.Action.DISPOSED,
    ]
    assert AssetLifecycleEvent.objects.filter(item=asset, event=Event.DISPOSED).exists()
    assert AuditLog.objects.filter(object_id=str(done.id),
                                   action=AuditLog.Action.APPROVE).count() == 2


@pytest.mark.django_db
def test_a_sale_above_book_value_is_a_gain_not_a_write_off(asset, officer, head, admin):
    done = dispose_via_workflow(asset, officer, head, admin, reason=REASON,
                             disposal_type=AssetDisposal.DisposalType.SALE,
                             proceeds=Decimal("95000.00"))
    assert done.gain_on_disposal == Decimal("5000.00")
    assert done.written_off == 0


@pytest.mark.django_db
@pytest.mark.parametrize("gate", ["head", "admin"])
def test_either_gate_can_reject_and_the_asset_stays_on_the_books(
        request, gate, asset, officer, head, admin):
    disposal = _raise(asset, officer)
    disposals.submit(disposal.id, officer)
    if gate == "admin":
        disposals.approve(disposal.id, head, remarks="Department agrees.")
    with pytest.raises(ValidationError):
        disposals.reject(disposal.id, request.getfixturevalue(gate), remarks="no")
    rejected = disposals.reject(disposal.id, request.getfixturevalue(gate),
                                remarks="It is still serviceable; keep it another year.")
    asset.refresh_from_db()
    assert rejected.status == Status.REJECTED
    assert asset.status != InventoryItem.Status.DISPOSED


@pytest.mark.django_db
def test_the_requester_cannot_approve_their_own_disposal(asset, head, admin):
    """The officer here is also an admin, so only the party rule can stop them."""
    admin_requester = admin
    disposal = _raise(asset, admin_requester)
    disposals.submit(disposal.id, admin_requester)
    disposals.approve(disposal.id, head, remarks="Department agrees to write it off.")
    with pytest.raises(PermissionDenied):
        disposals.approve(disposal.id, admin_requester, remarks="Approving my own request.")


@pytest.mark.django_db
def test_admin_stands_in_for_a_department_head_only_when_there_is_none(
        asset, officer, head, admin):
    disposal = _raise(asset, officer)
    disposals.submit(disposal.id, officer)
    with pytest.raises(PermissionDenied):
        disposals.approve(disposal.id, admin, remarks="Standing in unnecessarily.")
    head.is_active = False
    head.save(update_fields=["is_active"])
    assert disposals.approve(disposal.id, admin,
                             remarks="No head in post; standing in.").status == Status.ADMIN_APPROVAL


@pytest.mark.django_db
def test_a_head_of_another_department_cannot_decide(asset, officer, ops):
    other = _user("dsp_ops_head", User.Roles.CHECKER, ops)
    disposal = _raise(asset, officer)
    disposals.submit(disposal.id, officer)
    with pytest.raises(PermissionDenied):
        disposals.approve(disposal.id, other, remarks="Not my department's asset.")


@pytest.mark.django_db
def test_an_employee_cannot_request_a_disposal(asset, employee):
    with pytest.raises(PermissionDenied):
        _raise(asset, employee)


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_one_disposal_in_flight_per_asset(asset, officer):
    _raise(asset, officer)
    with pytest.raises(ValidationError):
        _raise(asset, officer)


@pytest.mark.django_db
def test_a_held_asset_can_only_leave_as_lost(asset, employee, officer, head, admin):
    services.assign_item(asset.id, employee, officer)
    with pytest.raises(ValidationError):
        _raise(asset, officer)

    lost = dispose_via_workflow(asset, officer, head, admin,
                                disposal_type=AssetDisposal.DisposalType.LOST,
                                reason="Stolen from the field office; police report filed.")
    asset.refresh_from_db()
    assert lost.status == Status.DISPOSED
    assert lost.last_holder == employee, "the chain of custody keeps who was holding it"
    assert asset.status == InventoryItem.Status.DISPOSED
    assert not ItemAssignment.objects.filter(item=asset, is_active=True).exists()
    assert AssetLifecycleEvent.objects.filter(item=asset, event=Event.LOST).exists()
    assert lost.events.filter(action=AssetDisposalEvent.Action.CUSTODY_CLOSED).exists()
    # A lost asset brings nothing back: the whole book value is written off.
    assert lost.proceeds == 0 and lost.written_off == Decimal("90000.00")


@pytest.mark.django_db
def test_an_open_maintenance_ticket_blocks_a_disposal(asset, officer, employee):
    from inventory import lifecycle

    lifecycle.report_maintenance(item=asset, actor=employee,
                                 issue="The screen flickers intermittently.")
    with pytest.raises(ValidationError):
        _raise(asset, officer)


@pytest.mark.django_db
def test_a_sale_must_say_what_it_expects_to_raise(asset, officer):
    with pytest.raises(ValidationError):
        _raise(asset, officer, disposal_type=AssetDisposal.DisposalType.SALE)


# --------------------------------------------------------------------------- #
# Nothing is ever deleted
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_the_api_refuses_to_delete_an_asset(auth, officer, admin, asset):
    for user in (officer, admin):
        res = auth(user).delete(f"/api/v1/inventory/items/{asset.id}/")
        assert res.status_code == 405, f"{user} could delete an asset"
        assert "disposal" in str(res.data).lower()
    assert InventoryItem.objects.filter(pk=asset.pk).exists()


@pytest.mark.django_db
def test_the_admin_site_cannot_delete_assets_or_custody():
    from django.contrib import admin as django_admin

    from inventory.models import TakeOutRequest

    for model in (InventoryItem, ItemAssignment, TakeOutRequest):
        site_admin = django_admin.site._registry[model]
        assert site_admin.has_delete_permission(None) is False, model.__name__


@pytest.mark.django_db
def test_the_one_click_dispose_endpoint_now_refuses(auth, officer, asset):
    res = auth(officer).post(f"/api/v1/inventory/items/{asset.id}/lifecycle/dispose/",
                             {"reason": "Skipping both approvals."}, format="json")
    assert res.status_code == 409
    asset.refresh_from_db()
    assert asset.status != InventoryItem.Status.DISPOSED


# --------------------------------------------------------------------------- #
# The timeline
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_direct_store_actions_now_appear_in_the_timeline(auth, officer, employee, asset):
    """
    Regression: assigning, handing over and returning from the Assignment page
    changed custody and wrote nothing to the asset's own history.
    """
    api = auth(officer)
    other = _user("dsp_other", User.Roles.MAKER, None)
    assert api.post(f"/api/v1/inventory/items/{asset.id}/assign/",
                    {"assigned_to": str(employee.id)}, format="json").status_code == 200
    assert api.post(f"/api/v1/inventory/items/{asset.id}/handover/",
                    {"assigned_to": str(other.id)}, format="json").status_code == 200
    assert api.post(f"/api/v1/inventory/items/{asset.id}/return/",
                    {"return_condition": "fair"}, format="json").status_code == 200

    events = list(AssetLifecycleEvent.objects.filter(item=asset)
                  .order_by("sequence").values_list("event", flat=True))
    assert events == [Event.ASSIGNED, Event.REASSIGNED, Event.RETURNED]


@pytest.mark.django_db
def test_the_lifecycle_endpoint_groups_history_into_the_brief_s_phases(
        auth, officer, employee, admin, head, asset):
    services.assign_item(asset.id, employee, officer)
    services.return_item(asset.id, officer)
    dispose_via_workflow(asset, officer, head, admin, reason=REASON)

    body = auth(admin).get(f"/api/v1/inventory/items/{asset.id}/lifecycle-summary/").data
    phases = {p["key"]: p for p in body["phases"]}
    assert list(phases) == ["purchased", "assigned", "transferred", "returned",
                            "maintained", "disposed"]
    assert phases["disposed"]["count"] >= 1
    # No creation event was recorded for this asset, so the purchase milestone is
    # derived from its purchase date - and says so rather than inventing an event.
    assert phases["purchased"]["derived"] is True
    assert phases["purchased"]["first_at"] == asset.purchase_date
    assert body["depreciation"]["depreciable"] is True
    assert body["can_request_disposal"] is False, "already disposed"


@pytest.mark.django_db
def test_an_employee_cannot_read_another_persons_asset_lifecycle(auth, asset, employee):
    assert auth(employee).get(
        f"/api/v1/inventory/items/{asset.id}/lifecycle-summary/").status_code == 403
    services.assign_item(asset.id, employee, employee)
    assert auth(employee).get(
        f"/api/v1/inventory/items/{asset.id}/lifecycle-summary/").status_code == 200


# --------------------------------------------------------------------------- #
# Reports and dashboard
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_the_lifecycle_reports(asset, officer, head, admin, today):
    spare = InventoryItem.objects.create(asset_code="NIF-INV-DSP9", name="Spare monitor")
    register = {r["code"]: r for r in reports.depreciation_register()}
    assert register[asset.asset_code]["book_value"] == "90000.00"
    # An asset that cannot be depreciated is LISTED with the reason, not dropped.
    assert register[spare.asset_code]["book_value"] is None
    assert "purchase cost" in register[spare.asset_code]["note"]

    done = _drive(_raise(asset, officer, disposal_type=AssetDisposal.DisposalType.SALE,
                         expected_proceeds=Decimal("40000.00")), officer, head, admin)
    write_offs = {r["reference"]: r for r in reports.write_off_register()}
    assert write_offs[done.disposal_number]["written_off"] == "50000.00"
    assert write_offs[done.disposal_number]["source"] == "Approved disposal"

    lifecycle_rows = {r["code"]: r for r in reports.lifecycle_summary()}
    assert lifecycle_rows[asset.asset_code]["events_on_record"] >= 1
    assert {"depreciation", "lifecycle", "write_off", "lost", "amc_expiring",
            "warranty_expiring", "disposed"} <= set(reports.REPORTS)

    tiles = reports.dashboard()
    assert tiles["disposed_this_year"] == 1
    assert tiles["written_off_this_year"] == "50000.00"
    assert Decimal(tiles["book_value"]) >= 0


@pytest.mark.django_db
def test_lost_assets_report_names_who_was_holding_it(asset, employee, officer, head, admin):
    services.assign_item(asset.id, employee, officer)
    dispose_via_workflow(asset, officer, head, admin,
                         disposal_type=AssetDisposal.DisposalType.LOST,
                         reason="Stolen from the field office; police report filed.")
    row = reports.lost_assets()[0]
    assert row["last_holder"] == employee.get_full_name()
    assert row["status"] == "Disposed"
    assert row["book_value_lost"] == "90000.00"
