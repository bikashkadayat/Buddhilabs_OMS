"""
Phase 70 - the asset lifecycle, the two approval workflows, and maintenance.

Written against the HTTP layer. The question a release needs answered is "what
happens when this person sends this request", and a unit test of a service cannot
answer it - a route can forget to consult the guard, and in this project one
already had.

The rule these tests exist to protect is the one the engine is built around:
NOTHING changes an asset's status without writing a lifecycle event. Several tests
below assert the event as well as the status, because a status that moved silently
is the failure this whole phase was commissioned to fix.
"""
import pytest

from inventory.models import (
    AssetLifecycleEvent, AssetRequest, AssetReturn, InventoryItem, ItemAssignment,
    MaintenanceTicket,
)

Status = InventoryItem.Status
Event = AssetLifecycleEvent.Event


def events(item, kind=None):
    rows = AssetLifecycleEvent.objects.filter(item=item)
    if kind:
        rows = rows.filter(event=kind)
    return list(rows.order_by("sequence"))


# ===========================================================================
# 70.2 - the front and back of the lifecycle
# ===========================================================================
@pytest.mark.django_db
def test_an_ordered_asset_walks_procurement_to_stock(auth, officer, on_order_item):
    api = auth(officer)
    received = api.post(
        f"/api/v1/inventory/items/{on_order_item.id}/lifecycle/receive/",
        {"remarks": "Delivered by the vendor.", "condition": "new"}, format="json")
    assert received.status_code == 200, received.data
    on_order_item.refresh_from_db()
    assert on_order_item.status == Status.RECEIVED
    assert on_order_item.condition == "new"

    stocked = api.post(
        f"/api/v1/inventory/items/{on_order_item.id}/lifecycle/stock/",
        {"location": "Store room, 2nd floor"}, format="json")
    assert stocked.status_code == 200, stocked.data
    on_order_item.refresh_from_db()
    assert on_order_item.status == Status.AVAILABLE
    assert on_order_item.location == "Store room, 2nd floor"

    # Every transition wrote an event. This is the phase's core claim.
    kinds = [row.event for row in events(on_order_item)]
    assert Event.RECEIVED in kinds
    assert Event.STOCKED in kinds


@pytest.mark.django_db
def test_an_asset_cannot_skip_the_check_in_gate(auth, officer, on_order_item):
    """
    Received and stocked are separate because the window between them is where
    assets go missing. Marking an in-stock asset 'received' again is refused.
    """
    api = auth(officer)
    api.post(f"/api/v1/inventory/items/{on_order_item.id}/lifecycle/receive/",
             {}, format="json")
    api.post(f"/api/v1/inventory/items/{on_order_item.id}/lifecycle/stock/",
             {}, format="json")
    again = api.post(
        f"/api/v1/inventory/items/{on_order_item.id}/lifecycle/receive/",
        {}, format="json")
    assert again.status_code == 400
    assert "on order" in str(again.data).lower()


@pytest.mark.django_db
def test_an_assigned_asset_cannot_be_disposed(auth, officer, stock_item, employee):
    """
    Disposing of something somebody holds would leave a custody record pointing at
    nothing, and the holder would never be asked for it back.

    Phase ASSET-LIFECYCLE-DISPOSAL: the rule now bites when the disposal is
    REQUESTED (and again at completion), and the old one-click verb refuses.
    """
    from inventory.services import assign_item
    assign_item(stock_item.id, employee, officer)

    refused = auth(officer).post(
        "/api/v1/inventory/disposals/",
        {"item": str(stock_item.id), "disposal_type": "scrap",
         "reason": "End of useful life, replaced."}, format="json")
    assert refused.status_code == 400
    assert "still assigned" in str(refused.data)

    bypass = auth(officer).post(
        f"/api/v1/inventory/items/{stock_item.id}/lifecycle/dispose/",
        {"reason": "End of useful life, replaced."}, format="json")
    assert bypass.status_code == 409


@pytest.mark.django_db
def test_disposal_needs_a_reason_and_archiving_needs_disposal_first(
        auth, officer, head, admin, stock_item):
    from .conftest import dispose_via_workflow

    api = auth(officer)
    thin = api.post("/api/v1/inventory/disposals/",
                    {"item": str(stock_item.id), "disposal_type": "scrap", "reason": "old"},
                    format="json")
    assert thin.status_code == 400

    early = api.post(f"/api/v1/inventory/items/{stock_item.id}/lifecycle/archive/",
                     {}, format="json")
    assert early.status_code == 400
    assert "disposed asset" in str(early.data)

    dispose_via_workflow(stock_item, officer, head, admin,
                         reason="Beyond economic repair after liquid damage.",
                         disposal_type="recycle")
    stock_item.refresh_from_db()
    assert stock_item.status == Status.DISPOSED
    assert stock_item.disposed_at is not None
    assert stock_item.disposal_method

    archived = api.post(
        f"/api/v1/inventory/items/{stock_item.id}/lifecycle/archive/",
        {}, format="json")
    assert archived.status_code == 200
    stock_item.refresh_from_db()
    assert stock_item.status == Status.ARCHIVED


@pytest.mark.django_db
def test_a_disposed_asset_cannot_be_requested_or_maintained(
        auth, officer, head, admin, employee, stock_item):
    from .conftest import dispose_via_workflow

    dispose_via_workflow(stock_item, officer, head, admin, reason="Written off after the flood.",
                         disposal_type="write_off")

    api = auth(employee)
    refused = api.post("/api/v1/inventory/requests/",
                       {"item": str(stock_item.id),
                        "purpose": "I would like this laptop please."},
                       format="json")
    assert refused.status_code == 400
    assert "disposed" in str(refused.data).lower()

    ticket = api.post("/api/v1/inventory/maintenance/",
                      {"item": str(stock_item.id),
                       "issue": "The screen flickers intermittently."},
                      format="json")
    assert ticket.status_code == 400


# ===========================================================================
# 70.5 - the assignment request workflow
# ===========================================================================
@pytest.mark.django_db
def test_the_full_request_chain_ends_in_acceptance(
        auth, employee, supervisor, officer, stock_item):
    """Request, supervisor, inventory officer, handover, acceptance."""
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id),
         "purpose": "My current laptop cannot run the analysis tooling."},
        format="json")
    assert created.status_code == 201, created.data
    request_id = created.data["id"]
    assert created.data["status"] == AssetRequest.Status.PENDING
    assert created.data["reference"].startswith("NIF-AR-")

    gate_one = auth(supervisor).post(
        f"/api/v1/inventory/requests/{request_id}/supervisor/",
        {"approve": True, "remarks": "Agreed, the workload justifies it."},
        format="json")
    assert gate_one.status_code == 200, gate_one.data
    assert gate_one.data["status"] == AssetRequest.Status.SUPERVISOR_APPROVED

    gate_two = auth(officer).post(
        f"/api/v1/inventory/requests/{request_id}/inventory/",
        {"approve": True, "remarks": "One in stock.", "accessories": "Charger, bag"},
        format="json")
    assert gate_two.status_code == 200, gate_two.data
    assert gate_two.data["status"] == AssetRequest.Status.INVENTORY_APPROVED

    handed = auth(officer).post(
        f"/api/v1/inventory/requests/{request_id}/handover/",
        {"condition": "good"}, format="json")
    assert handed.status_code == 200, handed.data
    assert handed.data["status"] == AssetRequest.Status.HANDED_OVER

    stock_item.refresh_from_db()
    assert stock_item.status == Status.ASSIGNED
    assert ItemAssignment.objects.filter(
        item=stock_item, assigned_to=employee, is_active=True).exists()

    accepted = auth(employee).post(
        f"/api/v1/inventory/requests/{request_id}/accept/",
        {"remarks": "Received with charger and bag."}, format="json")
    assert accepted.status_code == 200, accepted.data
    assert accepted.data["status"] == AssetRequest.Status.ACCEPTED

    kinds = [row.event for row in events(stock_item)]
    for expected in (Event.REQUESTED, Event.REQUEST_APPROVED, Event.HANDED_OVER,
                     Event.ACCEPTED):
        assert expected in kinds, expected


@pytest.mark.django_db
def test_only_the_recipient_can_accept(auth, employee, supervisor, officer,
                                       other_employee, stock_item):
    """
    The step that makes the record worth keeping. Without it, "assigned" means
    only that an officer says they handed it over.
    """
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "Needed for the field survey."},
        format="json")
    rid = created.data["id"]
    auth(supervisor).post(f"/api/v1/inventory/requests/{rid}/supervisor/",
                          {"approve": True}, format="json")
    auth(officer).post(f"/api/v1/inventory/requests/{rid}/inventory/",
                       {"approve": True}, format="json")
    auth(officer).post(f"/api/v1/inventory/requests/{rid}/handover/",
                       {}, format="json")

    for impostor in (officer, other_employee, supervisor):
        refused = auth(impostor).post(
            f"/api/v1/inventory/requests/{rid}/accept/", {}, format="json")
        assert refused.status_code in (403, 404), impostor.username


@pytest.mark.django_db
def test_a_requester_cannot_approve_their_own_request(auth, supervisor,
                                                       stock_item):
    """A control one person can satisfy on both sides is not a control."""
    created = auth(supervisor).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "I need this for my own work."},
        format="json")
    assert created.status_code == 201
    refused = auth(supervisor).post(
        f"/api/v1/inventory/requests/{created.data['id']}/supervisor/",
        {"approve": True}, format="json")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_the_gates_run_in_order(auth, employee, supervisor, officer, stock_item):
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "Replacement for a failed unit."},
        format="json")
    rid = created.data["id"]

    # The inventory officer cannot go first.
    early = auth(officer).post(f"/api/v1/inventory/requests/{rid}/inventory/",
                               {"approve": True}, format="json")
    assert early.status_code == 400
    assert "supervisor approval first" in str(early.data)

    # And nothing can be handed over before the second gate.
    auth(supervisor).post(f"/api/v1/inventory/requests/{rid}/supervisor/",
                          {"approve": True}, format="json")
    premature = auth(officer).post(f"/api/v1/inventory/requests/{rid}/handover/",
                                   {}, format="json")
    assert premature.status_code == 400


@pytest.mark.django_db
def test_a_plain_employee_cannot_approve_at_either_gate(
        auth, employee, other_employee, supervisor, stock_item):
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "Needed for the audit fieldwork."},
        format="json")
    rid = created.data["id"]
    refused = auth(other_employee).post(
        f"/api/v1/inventory/requests/{rid}/supervisor/",
        {"approve": True}, format="json")
    assert refused.status_code in (403, 404)

    auth(supervisor).post(f"/api/v1/inventory/requests/{rid}/supervisor/",
                          {"approve": True}, format="json")
    refused = auth(other_employee).post(
        f"/api/v1/inventory/requests/{rid}/inventory/",
        {"approve": True}, format="json")
    assert refused.status_code in (403, 404)


@pytest.mark.django_db
def test_a_request_can_name_a_category_and_the_officer_chooses_the_asset(
        auth, employee, supervisor, officer, stock_item, laptop_category):
    """
    "I need a laptop" is the common case. Making an employee pick an asset code
    whose availability they cannot see is how paper forms get filled in wrong.
    """
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"requested_category": str(laptop_category.id),
         "purpose": "Starting on Monday and have no machine."},
        format="json")
    assert created.status_code == 201, created.data
    rid = created.data["id"]
    assert created.data["item"] is None

    auth(supervisor).post(f"/api/v1/inventory/requests/{rid}/supervisor/",
                          {"approve": True}, format="json")
    chosen = auth(officer).post(
        f"/api/v1/inventory/requests/{rid}/inventory/",
        {"approve": True, "item": str(stock_item.id)}, format="json")
    assert chosen.status_code == 200, chosen.data
    assert chosen.data["item_code"] == stock_item.asset_code


@pytest.mark.django_db
def test_a_request_naming_nothing_is_refused(auth, employee):
    refused = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"purpose": "I would like something, anything really."}, format="json")
    assert refused.status_code == 400
    assert "specific asset or a category" in str(refused.data)


@pytest.mark.django_db
def test_two_requests_cannot_both_hold_one_asset(
        auth, employee, other_employee, supervisor, officer, stock_item):
    """
    A request does NOT reserve on submission - two people may ask for the same
    laptop. The inventory officer's approval is what decides it.
    """
    first = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "Needed for the migration work."},
        format="json")
    second = auth(other_employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "Needed for the reporting work."},
        format="json")
    assert first.status_code == 201
    assert second.status_code == 201, "a request must not reserve on submission"

    for rid in (first.data["id"], second.data["id"]):
        auth(supervisor).post(f"/api/v1/inventory/requests/{rid}/supervisor/",
                              {"approve": True}, format="json")

    won = auth(officer).post(
        f"/api/v1/inventory/requests/{first.data['id']}/inventory/",
        {"approve": True}, format="json")
    assert won.status_code == 200
    lost = auth(officer).post(
        f"/api/v1/inventory/requests/{second.data['id']}/inventory/",
        {"approve": True}, format="json")
    assert lost.status_code == 400
    assert "already held against request" in str(lost.data)


@pytest.mark.django_db
def test_a_rejection_needs_a_reason(auth, employee, supervisor, stock_item):
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "For the new project work."},
        format="json")
    rid = created.data["id"]
    silent = auth(supervisor).post(
        f"/api/v1/inventory/requests/{rid}/supervisor/",
        {"approve": False, "remarks": "no"}, format="json")
    assert silent.status_code == 400

    rejected = auth(supervisor).post(
        f"/api/v1/inventory/requests/{rid}/supervisor/",
        {"approve": False,
         "remarks": "The existing machine was replaced three months ago."},
        format="json")
    assert rejected.status_code == 200
    assert rejected.data["status"] == AssetRequest.Status.REJECTED
    assert rejected.data["rejection_reason"]


@pytest.mark.django_db
def test_a_handed_over_request_cannot_be_cancelled(
        auth, employee, supervisor, officer, stock_item):
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "For the quarterly fieldwork."},
        format="json")
    rid = created.data["id"]
    auth(supervisor).post(f"/api/v1/inventory/requests/{rid}/supervisor/",
                          {"approve": True}, format="json")
    auth(officer).post(f"/api/v1/inventory/requests/{rid}/inventory/",
                       {"approve": True}, format="json")
    auth(officer).post(f"/api/v1/inventory/requests/{rid}/handover/",
                       {}, format="json")

    refused = auth(employee).post(f"/api/v1/inventory/requests/{rid}/cancel/",
                                  {}, format="json")
    assert refused.status_code == 400
    assert "already been handed over" in str(refused.data)


# ===========================================================================
# 70.6 - the return workflow
# ===========================================================================
@pytest.fixture
def held_item(auth, employee, officer, stock_item):
    """An asset in the employee's hands, by the direct route."""
    from inventory.services import assign_item
    assign_item(stock_item.id, employee, officer)
    stock_item.refresh_from_db()
    return stock_item


@pytest.mark.django_db
def test_the_full_return_chain(auth, employee, officer, held_item):
    created = auth(employee).post(
        "/api/v1/inventory/returns/",
        {"item": str(held_item.id), "reason": "Leaving the project.",
         "declared_condition": "good"}, format="json")
    assert created.status_code == 201, created.data
    rid = created.data["id"]
    assert created.data["reference"].startswith("NIF-RT-")

    verified = auth(officer).post(f"/api/v1/inventory/returns/{rid}/verify/",
                                  {}, format="json")
    assert verified.status_code == 200
    assert verified.data["status"] == AssetReturn.Status.VERIFYING

    inspected = auth(officer).post(
        f"/api/v1/inventory/returns/{rid}/inspect/",
        {"condition": "good", "remarks": "As declared."}, format="json")
    assert inspected.status_code == 200
    assert inspected.data["condition_disputed"] is False

    accepted = auth(officer).post(f"/api/v1/inventory/returns/{rid}/accept/",
                                  {}, format="json")
    assert accepted.status_code == 200
    assert accepted.data["status"] == AssetReturn.Status.ACCEPTED

    held_item.refresh_from_db()
    assert held_item.status == Status.AVAILABLE
    assert not ItemAssignment.objects.filter(
        item=held_item, is_active=True).exists()
    assert Event.RETURNED in [row.event for row in events(held_item)]


@pytest.mark.django_db
def test_only_the_holder_can_raise_a_return(auth, other_employee, held_item):
    refused = auth(other_employee).post(
        "/api/v1/inventory/returns/",
        {"item": str(held_item.id), "reason": "Not mine but returning it anyway."},
        format="json")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_a_disputed_condition_is_recorded_and_must_be_explained(
        auth, employee, officer, held_item):
    """
    The system's job is to record that the two did not agree, not to decide who
    was right - but a disagreement with no account of it is worse than useless.
    """
    created = auth(employee).post(
        "/api/v1/inventory/returns/",
        {"item": str(held_item.id), "reason": "End of assignment.",
         "declared_condition": "good"}, format="json")
    rid = created.data["id"]

    silent = auth(officer).post(f"/api/v1/inventory/returns/{rid}/inspect/",
                                {"condition": "damaged"}, format="json")
    assert silent.status_code == 400

    inspected = auth(officer).post(
        f"/api/v1/inventory/returns/{rid}/inspect/",
        {"condition": "damaged",
         "remarks": "Hinge cracked and the casing is split at the corner."},
        format="json")
    assert inspected.status_code == 200
    assert inspected.data["condition_disputed"] is True
    assert inspected.data["declared_condition"] == "good"
    assert inspected.data["inspected_condition"] == "damaged"


@pytest.mark.django_db
def test_a_return_cannot_be_accepted_before_inspection(auth, employee, officer,
                                                        held_item):
    """The condition it comes back in is the point of the inspection."""
    created = auth(employee).post(
        "/api/v1/inventory/returns/",
        {"item": str(held_item.id), "reason": "Finished with it."}, format="json")
    refused = auth(officer).post(
        f"/api/v1/inventory/returns/{created.data['id']}/accept/", {},
        format="json")
    assert refused.status_code == 400
    assert "Inspect the asset's condition" in str(refused.data)


@pytest.mark.django_db
def test_a_rejected_return_leaves_custody_where_it_was(auth, employee, officer,
                                                        held_item):
    """The employee still holds it, which is the honest outcome."""
    created = auth(employee).post(
        "/api/v1/inventory/returns/",
        {"item": str(held_item.id), "reason": "Returning it today."},
        format="json")
    rejected = auth(officer).post(
        f"/api/v1/inventory/returns/{created.data['id']}/reject/",
        {"reason": "The asset was not actually brought to the store."},
        format="json")
    assert rejected.status_code == 200
    held_item.refresh_from_db()
    assert held_item.status == Status.ASSIGNED
    assert ItemAssignment.objects.filter(
        item=held_item, assigned_to=employee, is_active=True).exists()


@pytest.mark.django_db
def test_only_one_return_at_a_time(auth, employee, held_item):
    payload = {"item": str(held_item.id), "reason": "Handing it back."}
    assert auth(employee).post("/api/v1/inventory/returns/", payload,
                               format="json").status_code == 201
    again = auth(employee).post("/api/v1/inventory/returns/", payload,
                                format="json")
    assert again.status_code == 400
    assert "already in progress" in str(again.data)


# ===========================================================================
# 70.8 - maintenance
# ===========================================================================
@pytest.mark.django_db
def test_the_full_maintenance_chain(auth, employee, officer, held_item):
    """
    Reported by the holder - who is the most likely to notice a fault - then
    assigned, started, completed and returned to service.
    """
    reported = auth(employee).post(
        "/api/v1/inventory/maintenance/",
        {"item": str(held_item.id), "priority": "high",
         "issue": "The screen flickers and the fan runs constantly."},
        format="json")
    assert reported.status_code == 201, reported.data
    tid = reported.data["id"]
    assert reported.data["reference"].startswith("NIF-MT-")

    # Reporting does NOT take the asset away - a sticky key should not remove a
    # working laptop from somebody's desk.
    held_item.refresh_from_db()
    assert held_item.status == Status.ASSIGNED

    assigned = auth(officer).post(f"/api/v1/inventory/maintenance/{tid}/assign/",
                                  {"vendor": "TechCare Pvt Ltd"}, format="json")
    assert assigned.status_code == 200
    assert assigned.data["status"] == MaintenanceTicket.Status.ASSIGNED

    started = auth(officer).post(f"/api/v1/inventory/maintenance/{tid}/start/",
                                 {}, format="json")
    assert started.status_code == 200
    held_item.refresh_from_db()
    # NOW it goes away, and custody is closed - it is not on the holder's desk.
    assert held_item.status == Status.MAINTENANCE
    assert not ItemAssignment.objects.filter(
        item=held_item, is_active=True).exists()

    completed = auth(officer).post(
        f"/api/v1/inventory/maintenance/{tid}/complete/",
        {"resolution": "Screen cable reseated and the fan assembly replaced.",
         "condition": "good", "cost": "4500.00"}, format="json")
    assert completed.status_code == 200

    back = auth(officer).post(
        f"/api/v1/inventory/maintenance/{tid}/return-to-service/", {},
        format="json")
    assert back.status_code == 200
    held_item.refresh_from_db()
    assert held_item.status == Status.AVAILABLE
    assert held_item.condition == "good"

    kinds = [row.event for row in events(held_item)]
    assert Event.MAINTENANCE_REPORTED in kinds
    assert Event.MAINTENANCE_STARTED in kinds
    assert Event.MAINTENANCE_DONE in kinds


@pytest.mark.django_db
def test_one_open_ticket_per_asset(auth, employee, stock_item):
    payload = {"item": str(stock_item.id),
               "issue": "It makes an unpleasant grinding noise."}
    assert auth(employee).post("/api/v1/inventory/maintenance/", payload,
                               format="json").status_code == 201
    again = auth(employee).post("/api/v1/inventory/maintenance/", payload,
                                format="json")
    assert again.status_code == 400
    assert "already has an open maintenance ticket" in str(again.data)


@pytest.mark.django_db
def test_a_plain_employee_cannot_drive_the_maintenance_chain(auth, employee,
                                                              stock_item):
    reported = auth(employee).post(
        "/api/v1/inventory/maintenance/",
        {"item": str(stock_item.id), "issue": "The keyboard has stopped working."},
        format="json")
    tid = reported.data["id"]
    for verb in ("assign", "start", "complete", "return-to-service", "cancel"):
        refused = auth(employee).post(
            f"/api/v1/inventory/maintenance/{tid}/{verb}/", {}, format="json")
        assert refused.status_code == 403, verb


@pytest.mark.django_db
def test_cancelling_an_in_progress_ticket_brings_the_asset_back(
        auth, employee, officer, stock_item):
    """
    Otherwise the asset is stranded in MAINTENANCE with no open ticket and nothing
    to close.
    """
    reported = auth(employee).post(
        "/api/v1/inventory/maintenance/",
        {"item": str(stock_item.id), "issue": "Reported in error, wrong asset."},
        format="json")
    tid = reported.data["id"]
    auth(officer).post(f"/api/v1/inventory/maintenance/{tid}/start/", {},
                       format="json")
    stock_item.refresh_from_db()
    assert stock_item.status == Status.MAINTENANCE

    cancelled = auth(officer).post(
        f"/api/v1/inventory/maintenance/{tid}/cancel/",
        {"reason": "Raised against the wrong asset code."}, format="json")
    assert cancelled.status_code == 200
    stock_item.refresh_from_db()
    assert stock_item.status == Status.AVAILABLE


@pytest.mark.django_db
def test_maintenance_steps_run_in_order(auth, employee, officer, stock_item):
    reported = auth(employee).post(
        "/api/v1/inventory/maintenance/",
        {"item": str(stock_item.id), "issue": "Battery no longer holds charge."},
        format="json")
    tid = reported.data["id"]
    early = auth(officer).post(f"/api/v1/inventory/maintenance/{tid}/complete/",
                               {"resolution": "Replaced the battery pack."},
                               format="json")
    assert early.status_code == 400

    auth(officer).post(f"/api/v1/inventory/maintenance/{tid}/start/", {},
                       format="json")
    premature = auth(officer).post(
        f"/api/v1/inventory/maintenance/{tid}/return-to-service/", {},
        format="json")
    assert premature.status_code == 400


# ===========================================================================
# 70.11 - the asset history
# ===========================================================================
@pytest.mark.django_db
def test_the_history_records_every_transition_with_who_and_when(
        auth, employee, supervisor, officer, stock_item):
    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(stock_item.id), "purpose": "Required for the survey work."},
        format="json")
    rid = created.data["id"]
    auth(supervisor).post(f"/api/v1/inventory/requests/{rid}/supervisor/",
                          {"approve": True}, format="json")
    auth(officer).post(f"/api/v1/inventory/requests/{rid}/inventory/",
                       {"approve": True}, format="json")
    auth(officer).post(f"/api/v1/inventory/requests/{rid}/handover/", {},
                       format="json")

    timeline = auth(officer).get(
        f"/api/v1/inventory/items/{stock_item.id}/history/")
    assert timeline.status_code == 200
    rows = timeline.data
    assert len(rows) >= 4
    for row in rows:
        assert row["at"]
        assert "label" in row
    # Sequence is monotonic, so the timeline has a defined order even when two
    # events land in the same second.
    assert [r["sequence"] for r in rows] == sorted(r["sequence"] for r in rows)
    assert any(r["event"] == Event.HANDED_OVER for r in rows)


@pytest.mark.django_db
def test_an_uninvolved_employee_cannot_read_an_assets_history(
        auth, other_employee, stock_item):
    refused = auth(other_employee).get(
        f"/api/v1/inventory/items/{stock_item.id}/history/")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_a_holder_can_read_the_history_of_what_they_hold(auth, employee,
                                                          held_item):
    """The history is what makes a custody record trustworthy to its holder."""
    allowed = auth(employee).get(
        f"/api/v1/inventory/items/{held_item.id}/history/")
    assert allowed.status_code == 200
