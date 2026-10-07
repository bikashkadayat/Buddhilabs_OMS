"""
Asset custody transfer (Phase ASSET-CUSTODY-TRANSFER).

Organised by what a person would ask of the feature, not by the module layout:
does custody actually move, is history kept, who may decide, what blocks an exit,
and does every step leave a trace.
"""
from datetime import date

import pytest
from django.contrib.auth.models import Group
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import AuditLog
from inventory import clearance, lifecycle, reports, services, transfers
from inventory.models import (
    AssetLifecycleEvent, AssetTransfer, AssetTransferEvent, InventoryItem,
    ItemAssignment,
)
from inventory.roles import INVENTORY_OFFICER_GROUP
from notifications.models import Category, Notification
from users.models import User

from .conftest import _user

Status = AssetTransfer.Status
REMARK = "Checked the handover and the asset condition matches the record."


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def officer(db, eng):
    """The store officer who raises transfers. Not a party, not an approver."""
    user = _user("trf_officer", User.Roles.MAKER, eng)
    group, _ = Group.objects.get_or_create(name=INVENTORY_OFFICER_GROUP)
    user.groups.add(group)
    return user


@pytest.fixture
def giver(db, eng):
    return _user("trf_giver", User.Roles.MAKER, eng)


@pytest.fixture
def receiver(db, ops):
    return _user("trf_receiver", User.Roles.MAKER, ops)


@pytest.fixture
def third(db, ops):
    return _user("trf_third", User.Roles.MAKER, ops)


@pytest.fixture
def laptop(db, eng, giver, officer):
    item = InventoryItem.objects.create(
        asset_code="NIF-INV-TRF1", name="Dell Latitude 5420", department=eng)
    services.assign_item(item.id, giver, officer)
    return InventoryItem.objects.get(pk=item.pk)


def _raise(item, to, officer, **kw):
    defaults = dict(transfer_date=date(2026, 9, 13),
                    reason=AssetTransfer.Reason.DEPARTMENT_TRANSFER,
                    condition=AssetTransfer.Condition.GOOD, remarks=REMARK)
    defaults.update(kw)
    return transfers.create_transfer(item=item, to_employee=to, requested_by=officer,
                                     **defaults)


def _drive(transfer, head, hr, admin):
    """
    Submit and clear the gate.

    Keeps `head` and `admin` in the signature although only `hr` decides: every
    caller passes them, and the point of several of those tests is that the other
    two are present and still cannot help. Phase ASSET-TRANSFER-GOVERNANCE.
    """
    transfers.submit(transfer.id, transfer.requested_by)
    return transfers.approve(transfer.id, hr, remarks="HR confirms the role change.")


# --------------------------------------------------------------------------- #
# Custody actually moves, and history is appended rather than overwritten
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_department_transfer_moves_custody_and_department(
        laptop, giver, receiver, officer, head, hr, admin, ops):
    transfer = _raise(laptop, receiver, officer)
    assert transfer.status == Status.DRAFT
    assert transfer.transfer_number.startswith("TRF-")

    done = _drive(transfer, head, hr, admin)

    assert done.status == Status.COMPLETED
    old, new = (ItemAssignment.objects.filter(item=laptop).order_by("assigned_at"))
    # The giver's custody row is CLOSED, not deleted and not rewritten to the receiver.
    assert old.assigned_to == giver and old.is_active is False
    assert old.returned_at is not None
    assert new.assigned_to == receiver and new.is_active is True
    assert done.closed_assignment == old and done.opened_assignment == new

    laptop.refresh_from_db()
    assert laptop.status == InventoryItem.Status.ASSIGNED
    assert laptop.department == ops, "the asset's department follows its new holder"


@pytest.mark.django_db
def test_every_transition_is_recorded_in_order(
        laptop, receiver, officer, head, hr, admin):
    done = _drive(_raise(laptop, receiver, officer), head, hr, admin)

    actions = list(done.events.order_by("sequence").values_list("action", flat=True))
    assert actions == [
        AssetTransferEvent.Action.CREATED, AssetTransferEvent.Action.SUBMITTED,
        # One approval, because there is one gate (Phase ASSET-TRANSFER-GOVERNANCE).
        AssetTransferEvent.Action.HR_APPROVED,
        AssetTransferEvent.Action.OWNER_CHANGED,
        AssetTransferEvent.Action.DEPARTMENT_CHANGED,
        AssetTransferEvent.Action.COMPLETED,
    ]
    asset_events = set(AssetLifecycleEvent.objects.filter(item=laptop)
                       .values_list("event", flat=True))
    assert {AssetLifecycleEvent.Event.TRANSFERRED,
            AssetLifecycleEvent.Event.DEPARTMENT_CHANGED} <= asset_events


@pytest.mark.django_db
def test_shared_audit_log_carries_each_decision(
        laptop, receiver, officer, head, hr, admin):
    done = _drive(_raise(laptop, receiver, officer), head, hr, admin)
    rows = AuditLog.objects.filter(object_id=str(done.id))
    transitions = [r.changes.get("transition") for r in rows.order_by("created_at")]
    assert "created" in transitions and "completed" in transitions
    assert rows.filter(action=AuditLog.Action.APPROVE).count() == 1, "one gate"
    # The brief's three auditable moments, each under its own indexed verb rather
    # than all three as "update".
    assert rows.filter(action=AuditLog.Action.SUBMIT).exists(), "transfer requested"
    assert rows.filter(action=AuditLog.Action.APPROVE).exists(), "transfer approved"
    owner_changed = rows.get(action=AuditLog.Action.OWNERSHIP_CHANGED)
    assert owner_changed.changes["from_owner"] and owner_changed.changes["to_owner"]


@pytest.mark.django_db
def test_reassignment_chain_keeps_every_owner(
        laptop, giver, receiver, third, officer, head, hr, admin, ops):
    """
    Scenario 3: Bikash (Engineering) -> Raj (Operations) -> Amar.

    Three owners, three rows, one active. Both legs are decided by HR; the heads
    of the giving and receiving departments are present for each and neither can
    act, which is the governance change this phase makes.
    """
    _drive(_raise(laptop, receiver, officer), head, hr, admin)

    second = _raise(laptop, third, officer, reason=AssetTransfer.Reason.EMPLOYEE_EXIT)
    transfers.submit(second.id, officer)
    # No head has a say in either leg now; the giving department's head used to
    # release the asset and no longer does.
    with pytest.raises(PermissionDenied):
        transfers.approve(second.id, head, remarks=REMARK)
    ops_head = _user("trf_ops_head2", User.Roles.CHECKER, ops)
    with pytest.raises(PermissionDenied):
        transfers.approve(second.id, ops_head, remarks=REMARK)
    assert transfers.approve(second.id, hr, remarks=REMARK).status == Status.COMPLETED

    rows = list(ItemAssignment.objects.filter(item=laptop).order_by("assigned_at"))
    assert [r.assigned_to for r in rows] == [giver, receiver, third]
    assert [r.is_active for r in rows] == [False, False, True]
    assert AssetTransfer.objects.filter(item=laptop, status=Status.COMPLETED).count() == 2


# --------------------------------------------------------------------------- #
# Who may decide
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.parametrize("who", ["giver", "receiver", "officer"])
def test_a_party_can_never_approve(request, who, laptop, receiver, officer):
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)
    actor = request.getfixturevalue(who)
    with pytest.raises(PermissionDenied):
        transfers.approve(transfer.id, actor, remarks=REMARK)


@pytest.mark.django_db
def test_hr_is_the_only_gate(laptop, receiver, officer, head, hr, admin):
    """
    Phase ASSET-TRANSFER-GOVERNANCE. Three gates became one, and HR holds it.

    A head and an administrator are both present and uninvolved, and neither can
    decide: the head because heads no longer approve transfers at all, the
    administrator because HR is available and an administrator is a stand-in, not
    an override.
    """
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)
    assert AssetTransfer.objects.get(pk=transfer.pk).status == Status.HR_REVIEW

    for who in (head, admin):
        with pytest.raises(PermissionDenied):
            transfers.approve(transfer.id, who, remarks=REMARK)
    assert transfers.approve(transfer.id, hr, remarks=REMARK).status == Status.COMPLETED


@pytest.mark.django_db
def test_no_department_head_can_decide_a_transfer(laptop, receiver, officer, head, ops):
    """Their own department's or anybody else's: a head is a reader now."""
    other_head = _user("trf_ops_head", User.Roles.CHECKER, ops)
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)
    for who in (head, other_head):
        assert transfers.can_act_at_stage(who, transfer) is False
        with pytest.raises(PermissionDenied):
            transfers.approve(transfer.id, who, remarks=REMARK)
        with pytest.raises(PermissionDenied):
            transfers.reject(transfer.id, who, remarks="I do not agree with this move.")


@pytest.mark.django_db
def test_a_department_head_cannot_raise_a_transfer(laptop, receiver, head):
    """Raising one is store work, and a head no longer does store work."""
    with pytest.raises(PermissionDenied):
        _raise(laptop, receiver, head)


@pytest.mark.django_db
def test_admin_fills_the_hr_gate_when_the_only_hr_user_is_a_party(
        laptop, officer, head, hr, admin):
    """
    HR is receiving the asset and is the only HR user, so Admin decides.

    Not a leftover from the old chain: without a stand-in this transfer would rest
    at a gate literally nobody could clear.
    """
    transfer = _raise(laptop, hr, officer)
    transfers.submit(transfer.id, officer)
    with pytest.raises(PermissionDenied):
        transfers.approve(transfer.id, hr, remarks=REMARK)
    assert transfers.approve(transfer.id, admin, remarks=REMARK).status == Status.COMPLETED


@pytest.mark.django_db
def test_admin_cannot_override_hr_when_an_uninvolved_hr_user_exists(
        laptop, eng, receiver, officer, head, hr, admin):
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)
    with pytest.raises(PermissionDenied):
        transfers.approve(transfer.id, admin, remarks=REMARK)
    assert transfers.approve(transfer.id, hr, remarks=REMARK).status == Status.COMPLETED


@pytest.mark.django_db
def test_an_employee_cannot_raise_a_transfer(laptop, giver, receiver):
    with pytest.raises(PermissionDenied):
        _raise(laptop, receiver, giver)


# --------------------------------------------------------------------------- #
# Rejection
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_the_gate_can_reject_and_custody_stays_put(
        laptop, giver, receiver, officer, hr):
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)

    decider = hr
    stage = AssetTransfer.objects.get(pk=transfer.pk).status
    rejected = transfers.reject(transfer.id, decider,
                                remarks="Receiver is not yet in post; hold the asset.")

    assert rejected.status == Status.REJECTED
    assert rejected.rejected_stage == stage
    holder = ItemAssignment.objects.get(item=laptop, is_active=True)
    assert holder.assigned_to == giver, "a rejected transfer must change nothing"


@pytest.mark.django_db
@pytest.mark.parametrize("remarks", ["", "   ", "no"])
def test_rejection_without_a_reason_is_refused(remarks, laptop, receiver, officer, hr):
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)
    with pytest.raises(ValidationError):
        transfers.reject(transfer.id, hr, remarks=remarks)
    assert AssetTransfer.objects.get(pk=transfer.pk).status == Status.HR_REVIEW


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_one_transfer_in_flight_per_asset(laptop, receiver, third, officer):
    _raise(laptop, receiver, officer)
    with pytest.raises(ValidationError):
        _raise(laptop, third, officer)


@pytest.mark.django_db
def test_a_lost_asset_cannot_be_handed_on(laptop, receiver, officer):
    transfer = _raise(laptop, receiver, officer, condition=AssetTransfer.Condition.LOST)
    with pytest.raises(ValidationError):
        transfers.submit(transfer.id, officer)


@pytest.mark.django_db
def test_an_unassigned_asset_has_nothing_to_transfer(db, eng, receiver, officer):
    loose = InventoryItem.objects.create(asset_code="NIF-INV-TRF9", name="Spare mouse",
                                         department=eng)
    with pytest.raises(ValidationError):
        _raise(loose, receiver, officer)


@pytest.mark.django_db
def test_completion_refuses_when_custody_moved_underneath_it(
        laptop, giver, receiver, officer, head, hr, admin):
    """If the asset was returned while the transfer sat in review, it must not complete."""
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)
    services.return_item(laptop.id, officer)          # custody ends out of band

    with pytest.raises(ValidationError):
        transfers.approve(transfer.id, hr, remarks=REMARK)
    assert AssetTransfer.objects.get(pk=transfer.pk).status == Status.HR_REVIEW
    assert not ItemAssignment.objects.filter(item=laptop, assigned_to=receiver).exists()


@pytest.mark.django_db
def test_cancelling_keeps_the_record(laptop, receiver, officer):
    transfer = _raise(laptop, receiver, officer)
    transfers.cancel(transfer.id, officer, remarks="Raised against the wrong asset.")
    transfer.refresh_from_db()
    assert transfer.status == Status.CANCELLED
    assert transfer.events.filter(action=AssetTransferEvent.Action.CANCELLED).exists()


# --------------------------------------------------------------------------- #
# Exit clearance (Scenario 1)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_exit_is_blocked_until_every_transfer_completes(
        eng, giver, receiver, officer, head, hr, admin):
    kit = []
    for code, name in (("L", "Laptop"), ("M", "Monitor"), ("K", "Keyboard"), ("S", "Mouse")):
        item = InventoryItem.objects.create(asset_code=f"NIF-INV-EXIT-{code}",
                                            name=name, department=eng)
        services.assign_item(item.id, giver, officer)
        kit.append(item)

    status = clearance.exit_clearance(giver)
    assert status["status"] == clearance.BLOCKED
    assert status["message"] == "Asset clearance required before separation."
    assert status["assets_held"] == 4 and status["unresolved"] == 4

    raised = [_raise(item, receiver, officer, reason=AssetTransfer.Reason.EMPLOYEE_EXIT)
              for item in kit]
    in_flight = clearance.exit_clearance(giver)
    # Raising transfers is not clearance: they may yet be rejected.
    assert in_flight["status"] == clearance.BLOCKED
    assert in_flight["in_transfer"] == 4 and in_flight["unresolved"] == 0

    for transfer in raised[:3]:
        _drive(transfer, head, hr, admin)
    assert clearance.exit_clearance(giver)["status"] == clearance.BLOCKED

    _drive(raised[3], head, hr, admin)
    cleared = clearance.exit_clearance(giver)
    assert cleared["status"] == clearance.CLEAR and cleared["assets_held"] == 0


@pytest.mark.django_db
def test_returning_the_asset_also_clears_the_exit(laptop, giver, officer):
    record = lifecycle.create_return(item=laptop, actor=giver,
                                     reason="Leaving the organisation this week.")
    assert clearance.exit_clearance(giver)["in_return"] == 1
    assert clearance.exit_clearance(giver)["status"] == clearance.BLOCKED

    lifecycle.verify_return(record.id, officer)
    lifecycle.inspect_return(record.id, officer, condition="good")
    lifecycle.accept_return(record.id, officer)
    assert clearance.exit_clearance(giver)["status"] == clearance.CLEAR


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_notifications_follow_the_workflow(
        django_capture_on_commit_callbacks, laptop, giver, receiver, officer,
        head, hr, admin):
    with django_capture_on_commit_callbacks(execute=True):
        transfer = _raise(laptop, receiver, officer)
        transfers.submit(transfer.id, officer)
    assert Notification.objects.filter(
        recipient=receiver, category=Category.INVENTORY_TRANSFER_SUBMITTED).exists()
    # HR is asked, and the head is NOT: paging somebody about a decision they
    # cannot make is the bug this replaced.
    assert Notification.objects.filter(
        recipient=hr, category=Category.INVENTORY_TRANSFER_APPROVAL_REQUIRED).exists()
    assert not Notification.objects.filter(
        recipient=head, category=Category.INVENTORY_TRANSFER_APPROVAL_REQUIRED).exists()

    with django_capture_on_commit_callbacks(execute=True):
        transfers.approve(transfer.id, hr, remarks=REMARK)
    assert Notification.objects.filter(
        recipient=receiver, category=Category.INVENTORY_TRANSFER_COMPLETED).exists()
    assert Notification.objects.filter(
        recipient=receiver, category=Category.INVENTORY_ASSET_ASSIGNED).exists()
    # Nobody is asked to approve a transfer they are party to.
    assert not Notification.objects.filter(
        recipient__in=[giver, receiver, officer],
        category=Category.INVENTORY_TRANSFER_APPROVAL_REQUIRED).exists()


@pytest.mark.django_db
def test_rejection_notifies_the_requester_and_both_parties(
        django_capture_on_commit_callbacks, laptop, giver, receiver, officer, hr):
    transfer = _raise(laptop, receiver, officer)
    transfers.submit(transfer.id, officer)
    with django_capture_on_commit_callbacks(execute=True):
        transfers.reject(transfer.id, hr, remarks="The receiver has not started yet.")
    for person in (officer, giver, receiver):
        assert Notification.objects.filter(
            recipient=person, category=Category.INVENTORY_TRANSFER_REJECTED).exists()


# --------------------------------------------------------------------------- #
# Reports and dashboard
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_custody_reports_and_dashboard(laptop, giver, receiver, officer, head, hr, admin):
    pending = _raise(laptop, receiver, officer)
    transfers.submit(pending.id, officer)
    tiles = reports.dashboard()
    assert tiles["pending_transfers"] == 1

    assert transfers.approve(pending.id, hr, remarks=REMARK).status == Status.COMPLETED

    tiles = reports.dashboard()
    assert tiles["pending_transfers"] == 0
    assert tiles["transferred_this_month"] == 1
    assert sum(m["completed"] for m in tiles["transfer_trends"]) == 1
    assert len(tiles["transfer_trends"]) == 6

    assert {"ownership", "movement", "transfers", "exit_clearance",
            "by_department", "by_employee",
            # Phase ASSET-TRANSFER-GOVERNANCE.
            "department_assets", "organisation_assets",
            "transfer_visibility"} <= set(reports.REPORTS)
    owners = {r["asset_code"]: r["owner"] for r in reports.asset_ownership()}
    assert owners[laptop.asset_code] == receiver.get_full_name()
    assert any(r["reference"] == pending.transfer_number for r in reports.asset_movement())
    assert reports.transfer_history()[0]["transfer_number"] == pending.transfer_number


@pytest.mark.django_db
def test_clearance_report_lists_departed_holders_first(eng, officer):
    active = _user("trf_active", User.Roles.MAKER, eng)
    departed = _user("trf_departed", User.Roles.MAKER, eng)
    for code, holder in (("A", active), ("Z", departed)):
        item = InventoryItem.objects.create(asset_code=f"NIF-INV-CLR-{code}",
                                            name="Laptop", department=eng)
        services.assign_item(item.id, holder, officer)
    departed.is_active = False
    departed.save(update_fields=["is_active"])

    rows = reports.exit_clearance_assets()
    assert rows[0]["employee"] == departed.get_full_name()
    assert rows[0]["account"].startswith("Inactive")
    assert reports.dashboard()["assets_held_by_inactive_employees"] == 1


# --------------------------------------------------------------------------- #
# API and security
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_api_end_to_end(api, laptop, giver, receiver, officer, head, hr, admin):
    api.force_authenticate(officer)
    created = api.post("/api/v1/inventory/transfers/", {
        "item": str(laptop.id), "to_employee": str(receiver.id),
        "transfer_date": "2026-09-13", "reason": "department_transfer",
        "condition": "good", "remarks": REMARK}, format="json")
    assert created.status_code == 201, created.data
    tid = created.data["id"]
    assert created.data["permissions"]["can_submit"] is True

    assert api.post(f"/api/v1/inventory/transfers/{tid}/submit/").status_code == 200

    # The head can SEE it and is offered no decision on it; HR is offered both.
    api.force_authenticate(head)
    head_view = api.get(f"/api/v1/inventory/transfers/{tid}/")
    assert head_view.status_code == 200
    assert head_view.data["permissions"]["can_approve"] is False
    assert head_view.data["permissions"]["can_reject"] is False
    assert api.post(f"/api/v1/inventory/transfers/{tid}/approve/",
                    {"remarks": REMARK}, format="json").status_code == 403

    api.force_authenticate(hr)
    detail = api.get(f"/api/v1/inventory/transfers/{tid}/").data
    assert detail["permissions"]["can_approve"] is True
    # One gate, so one row of approvals awaiting a name.
    assert [row["stage"] for row in detail["stage_approvals"]] == ["hr_review"]
    res = api.post(f"/api/v1/inventory/transfers/{tid}/approve/",
                   {"remarks": REMARK}, format="json")
    assert res.status_code == 200, res.data
    assert res.data["status"] == "completed"

    custody = api.get(f"/api/v1/inventory/items/{laptop.id}/custody/").data
    assert custody["current_owner"]["name"] == receiver.get_full_name()
    assert custody["custody_status"] == "held"
    assert len(custody["ownership_history"]) == 2
    assert custody["transfers"][0]["transfer_number"] == created.data["transfer_number"]

    # A governance record is never deleted.
    assert api.delete(f"/api/v1/inventory/transfers/{tid}/").status_code == 405


@pytest.mark.django_db
def test_employees_see_only_transfers_about_them(api, laptop, giver, receiver, third,
                                                 officer):
    _raise(laptop, receiver, officer)
    api.force_authenticate(giver)
    assert len(api.get("/api/v1/inventory/transfers/").data) == 1
    api.force_authenticate(third)
    assert api.get("/api/v1/inventory/transfers/").data == []


@pytest.mark.django_db
def test_hr_and_every_head_see_every_transfer(api, laptop, receiver, officer, hr,
                                              head, giver, ops):
    """
    Phase ASSET-TRANSFER-GOVERNANCE: a head's view is the whole organisation.

    The old test asserted the opposite for the third case - a head of an unrelated
    department saw nothing. Visibility used to be scoped because authority was;
    with no authority left to match, the brief asks for sight of everything.
    """
    _raise(laptop, receiver, officer)
    stranger_head = _user("trf_far_head", User.Roles.CHECKER,
                          ops.__class__.objects.create(name="Finance", code="INV-FIN"))
    for who in (hr, head, stranger_head):
        api.force_authenticate(who)
        assert len(api.get("/api/v1/inventory/transfers/").data) == 1, who

    # An employee who is not a party still sees nothing.
    api.force_authenticate(_user("trf_bystander", User.Roles.MAKER, ops))
    assert api.get("/api/v1/inventory/transfers/").data == []


@pytest.mark.django_db
def test_the_decider_can_always_find_what_they_may_decide(db, officer, receiver, hr):
    """
    Regression, found in the browser and kept: whoever holds the gate must be able
    to see the transfer they are being asked about. It was a head with no recorded
    department then; it is HR now, and the rule outlives the workflow it was
    written for.
    """
    loose_giver = _user("trf_headless_giver", User.Roles.MAKER, None)
    item = InventoryItem.objects.create(asset_code="NIF-INV-TRF3", name="Tablet")
    services.assign_item(item.id, loose_giver, officer)
    transfer = transfers.submit(_raise(item, receiver, officer).id, officer)

    assert transfers.can_act_at_stage(hr, transfer)
    assert transfers.visible_to(hr).filter(pk=transfer.pk).exists()

    transfers.approve(transfer.id, hr, remarks=REMARK)
    assert transfers.visible_to(hr).filter(pk=transfer.pk).exists()


@pytest.mark.django_db
def test_exit_clearance_visibility(api, laptop, giver, third, hr):
    api.force_authenticate(giver)
    assert api.get("/api/v1/inventory/exit-clearance/").data["status"] == "BLOCKED"
    api.force_authenticate(third)
    assert api.get(f"/api/v1/inventory/exit-clearance/{giver.id}/").status_code == 403
    api.force_authenticate(hr)
    assert api.get(f"/api/v1/inventory/exit-clearance/{giver.id}/").status_code == 200


@pytest.mark.django_db
def test_an_employee_cannot_read_someone_elses_custody_record(api, laptop, third, giver):
    api.force_authenticate(third)
    assert api.get(f"/api/v1/inventory/items/{laptop.id}/custody/").status_code == 403
    api.force_authenticate(giver)
    assert api.get(f"/api/v1/inventory/items/{laptop.id}/custody/").status_code == 200


# --------------------------------------------------------------------------- #
# Department Head visibility and governance (Phase ASSET-TRANSFER-GOVERNANCE)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_the_head_dashboard_answers_the_five_questions_the_brief_asks(
        laptop, giver, receiver, officer, head, hr, eng, ops):
    """Total, department, assigned, unassigned, transferred - all server-derived."""
    InventoryItem.objects.create(asset_code="NIF-INV-GOV1", name="Spare", department=eng)
    InventoryItem.objects.create(asset_code="NIF-INV-GOV2", name="Other", department=ops)
    _drive(_raise(laptop, receiver, officer), head, hr, None)

    tiles = reports.dashboard(head)
    assert tiles["governance_total_assets"] == 3
    # One, not two: completing the transfer moved the laptop's department to the
    # receiver's (Operations), which is what a department transfer is for.
    assert tiles["governance_department_assets"] == 1, "Engineering keeps the spare"
    assert tiles["governance_department_name"] == eng.name
    assert tiles["governance_assigned_assets"] == 1
    assert tiles["governance_unassigned_assets"] == 2
    assert tiles["governance_transferred_assets"] == 1

    # A viewer with no recorded department gets null, not a misleading zero.
    assert reports.dashboard(_user("gov_nodept", User.Roles.CHECKER, None))[
        "governance_department_assets"] is None


@pytest.mark.django_db
def test_unassigned_counts_assets_nobody_is_accountable_for(officer, eng):
    """
    Not `status == available`. An asset marked assigned with no custody row is
    exactly the integrity fault the custody tiles already flag, and it must land in
    "unassigned" rather than fall between the two counts.
    """
    InventoryItem.objects.create(asset_code="NIF-INV-GOV3", name="Orphan",
                                 department=eng,
                                 status=InventoryItem.Status.ASSIGNED)
    tiles = reports.dashboard(officer)
    assert tiles["governance_unassigned_assets"] == 1


@pytest.mark.django_db
def test_the_three_governance_reports(laptop, giver, receiver, officer, head, hr,
                                      eng, ops):
    InventoryItem.objects.create(asset_code="NIF-INV-GOV4", name="Stays put",
                                 department=eng)
    done = _drive(_raise(laptop, receiver, officer), head, hr, None)

    dept = {r["department"]: r for r in reports.department_asset_report()}
    assert dept[eng.name]["assets_owned"] == 1, "the laptop left for Operations"
    assert dept[ops.name]["assets_owned"] == 1
    # Held BY somebody in Operations, which is the column that answers "who has it"
    # rather than "whose is it".
    assert dept[ops.name]["held_by_staff"] == 1

    org = {r["asset_code"]: r for r in reports.organisation_asset_report()}
    row = org[laptop.asset_code]
    assert row["owner"] == receiver.get_full_name()
    assert row["condition"] and row["status"] and row["department"]

    visibility = {r["reference"]: r for r in reports.transfer_visibility_report()}
    assert visibility[done.transfer_number]["waiting_on"] == "", "completed, waiting on nobody"
    assert visibility[done.transfer_number]["approved_by"] == hr.get_full_name()

    open_one = _raise(
        InventoryItem.objects.get(pk=laptop.pk), giver, officer,
        reason=AssetTransfer.Reason.ROLE_CHANGE)
    transfers.submit(open_one.id, officer)
    visibility = {r["reference"]: r for r in reports.transfer_visibility_report()}
    assert visibility[open_one.transfer_number]["waiting_on"] == "HR"


@pytest.mark.django_db
def test_a_head_can_read_every_governance_report(api, head, laptop):
    for name in ("department_assets", "organisation_assets", "transfer_visibility"):
        api.force_authenticate(head)
        res = api.get(f"/api/v1/inventory/reports/{name}/")
        assert res.status_code == 200, f"{name}: {res.status_code}"
        assert "rows" in res.data


@pytest.mark.django_db
def test_the_stage_tracker_still_names_the_gates_an_old_transfer_passed(
        laptop, receiver, officer, hr):
    """
    A record approved under the three-gate workflow must keep showing the
    department head and the administrator who signed it. Simulated by stamping the
    fields the old workflow wrote, because the code that wrote them is gone.
    """
    done = _drive(_raise(laptop, receiver, officer), None, hr, None)
    done.dept_head_name, done.dept_head_at = "Old Head", done.created_at
    done.save(update_fields=["dept_head_name", "dept_head_at"])
    AssetTransferEvent.objects.create(
        transfer=done, sequence=99, action=AssetTransferEvent.Action.ADMIN_APPROVED,
        actor_name="Old Admin")

    keys = [stage["key"] for stage in transfers.stage_tracker(done)]
    assert "dept_head_review" in keys and "admin_approval" in keys

    # A transfer raised today draws neither: they are gates nobody is waiting on.
    fresh = _raise(InventoryItem.objects.get(pk=laptop.pk), officer, officer) \
        if False else None
    plain = AssetTransfer.objects.create(
        transfer_number="TRF-2026-9999", transfer_date=date(2026, 9, 13),
        reason=AssetTransfer.Reason.ROLE_CHANGE,
        condition=AssetTransfer.Condition.GOOD)
    assert [s["key"] for s in transfers.stage_tracker(plain)] == [
        "draft", "submitted", "hr_review", "completed"]


# --------------------------------------------------------------------------- #
# Owner history and clearance visibility (Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_owner_history_reads_as_the_brief_s_example(
        laptop, giver, receiver, third, officer, hr):
    """
    Assigned to Bikash -> Transferred to Raj -> Transferred to Amar -> Returned.
    Built from the custody rows themselves, so it cannot drift from them.
    """
    from inventory.ownership import ownership_timeline

    _drive(_raise(laptop, receiver, officer), None, hr, None)
    _drive(_raise(InventoryItem.objects.get(pk=laptop.pk), third, officer,
                  reason=AssetTransfer.Reason.EMPLOYEE_EXIT), None, hr, None)
    services.return_item(laptop.id, officer)

    lines = [e["label"] for e in ownership_timeline(laptop)]
    assert lines == [
        f"Assigned to {giver.get_full_name()}",
        f"Transferred to {receiver.get_full_name()}",
        f"Transferred to {third.get_full_name()}",
        "Returned to Inventory",
    ]
    # A transfer line says which approved request moved it, and from whom.
    second = ownership_timeline(laptop)[1]
    assert second["reference"].startswith("TRF-")
    assert second["from_owner"] == giver.get_full_name()


@pytest.mark.django_db
def test_a_desk_handover_is_not_called_a_transfer(laptop, receiver, officer):
    """
    "Transferred" means HR approved a numbered request. A handover at the store
    desk had no such approval, and calling it a transfer would claim one.
    """
    from inventory.ownership import ownership_timeline

    services.handover_item(laptop.id, receiver, officer)
    lines = [e["label"] for e in ownership_timeline(laptop)]
    assert lines[-1] == f"Handed over to {receiver.get_full_name()}"
    assert "Returned to Inventory" not in lines, "it never went back to the store"


@pytest.mark.django_db
def test_the_custody_endpoint_carries_the_owner_timeline(api, laptop, head, giver):
    api.force_authenticate(head)
    body = api.get(f"/api/v1/inventory/items/{laptop.id}/custody/").data
    assert body["owner_timeline"][0]["label"] == f"Assigned to {giver.get_full_name()}"
    assert body["owner_timeline"][0]["is_current"] is True


@pytest.mark.django_db
def test_any_department_head_can_read_any_employee_s_clearance(api, laptop, giver, ops):
    """
    Heads were still department-scoped here after they were given organisation-wide
    sight of assets and owners - they could see that someone held a laptop and not
    whether that person could leave.
    """
    far_head = _user("clr_far_head", User.Roles.CHECKER, ops)
    api.force_authenticate(far_head)
    res = api.get(f"/api/v1/inventory/exit-clearance/{giver.id}/")
    assert res.status_code == 200
    assert res.data["status"] == "BLOCKED"
    assert res.data["assets_held"] == 1


@pytest.mark.django_db
def test_visibility_options_are_for_readers_of_the_whole_register(api, head, hr, giver, eng):
    for reader in (head, hr):
        api.force_authenticate(reader)
        body = api.get("/api/v1/inventory/visibility-options/").data
        assert body["read_only"] is True
        assert {"statuses", "conditions", "asset_types", "departments", "owners"} <= set(body)
        # Departments come from the Department table, so one that owns nothing yet
        # is still a filter a head can choose.
        assert eng.name in {d["label"] for d in body["departments"]}
    api.force_authenticate(giver)
    assert api.get("/api/v1/inventory/visibility-options/").status_code == 403
