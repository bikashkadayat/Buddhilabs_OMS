"""Take-out workflow notifications — reuses the shared dispatcher + generic email
template. Best-effort: a notification failure can never break the API action."""
import logging

from config.nepali_dates import to_bs
from notifications.dispatcher import notify
from notifications.models import Category
from users.models import User

logger = logging.getLogger("inventory.notifications")


def _safe(label, fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - notifications must never break the workflow
        logger.exception("inventory notification failed (%s)", label)


def _managers_for(requester):
    """Approvers for a take-out: the requester's Dept Head(s) + all HR + all Admin
    (deduped, excluding the requester)."""
    # Only Dept Head(s) of the requester's OWN department (they can actually approve
    # it — the take-out queue scopes a Dept Head to their department). Do NOT fall
    # back to all Dept Heads: HR + Admin below already cover a headless department,
    # and notifying out-of-department heads who cannot act was the bug.
    heads = User.objects.filter(role=User.Roles.CHECKER, is_active=True)
    dept_id = getattr(requester, "department_ref_id", None)
    heads = heads.filter(department_ref_id=dept_id) if dept_id else heads.none()
    hr = User.objects.filter(role=User.Roles.APPROVER, is_active=True)
    admin = User.objects.filter(role=User.Roles.ADMIN, is_active=True)
    seen, out = set(), []
    for u in list(heads) + list(hr) + list(admin):
        if u.id != getattr(requester, "id", None) and u.id not in seen:
            seen.add(u.id)
            out.append(u)
    return out


def _ctx(req):
    return {
        "reference": req.reference,
        "item": f"{req.item_code} · {req.item_name}",
        "employee_name": req.requested_by_name,
        "purpose": req.get_purpose_display(),
        "out_ad": str(req.expected_out_date), "out_bs": to_bs(req.expected_out_date) or "—",
        "return_ad": str(req.expected_return_date), "return_bs": to_bs(req.expected_return_date) or "—",
        "reason": req.reason or "",
        "remarks": req.approver_remarks or "",
    }


def takeout_submitted(req):
    ctx = _ctx(req)
    title = f"Take-Out Request — {req.item_name} ({req.requested_by_name})"
    body = (f"{req.requested_by_name} requested to take '{req.item_name}' "
            f"({req.get_purpose_display()}) from {ctx['out_ad']} to {ctx['return_ad']}. Awaiting your approval.")
    for m in _managers_for(req.requested_by):
        _safe(f"submitted->{m.id}", lambda m=m: notify(
            m, Category.INVENTORY_TAKEOUT_REQUESTED, title, body,
            action_url="/inventory/approvals",
            idempotency_key=f"takeout-{req.id}-submitted-{m.id}",
            email_context=ctx, object_id=str(req.id)))


def _officers():
    """
    Everyone who can act as the store.

    The Inventory Officer group FIRST, because they are the ones appointed to run
    it, then the managers who can also approve at that gate. Deduped, because an
    officer who is also a Dept Head should get one notification rather than two.
    """
    from django.contrib.auth.models import Group

    from .roles import INVENTORY_OFFICER_GROUP

    officers = User.objects.filter(
        groups__name=INVENTORY_OFFICER_GROUP, is_active=True)
    managers = User.objects.filter(
        role__in=[User.Roles.APPROVER, User.Roles.ADMIN], is_active=True)
    seen, out = set(), []
    for user in list(officers) + list(managers):
        if user.id not in seen:
            seen.add(user.id)
            out.append(user)
    return out


def _supervisors_for(user):
    """
    Who approves this person's asset request at the first gate.

    Their own Dept Head, exactly as the take-out queue scopes it. HR and Admin are
    included as a fallback so a headless department is not a dead end - a request
    nobody is told about is a request that sits forever.
    """
    heads = User.objects.filter(role=User.Roles.CHECKER, is_active=True)
    dept_id = getattr(user, "department_ref_id", None)
    heads = heads.filter(department_ref_id=dept_id) if dept_id else heads.none()
    fallback = User.objects.filter(
        role__in=[User.Roles.APPROVER, User.Roles.ADMIN], is_active=True)
    seen, out = set(), []
    for candidate in list(heads) + list(fallback):
        if candidate.id != getattr(user, "id", None) and candidate.id not in seen:
            seen.add(candidate.id)
            out.append(candidate)
    return out


def _send(user, category, title, body, url, key, **ctx):
    """
    One notification, best-effort.

    Every caller below goes through here so that a notification failure is logged
    and swallowed rather than rolling back the lifecycle transition that triggered
    it. An asset that moved must stay moved even if the mail server is down: the
    event log is the record, and the notification is only a nudge toward it.
    """
    _safe(f"{category}->{getattr(user, 'id', None)}", lambda: notify(
        user, category, title, body, action_url=url,
        idempotency_key=key, email_context=ctx, object_id=ctx.get("object_id", "")))


# --------------------------------------------------------------------------- #
# Asset requests (Phase 70.5)
# --------------------------------------------------------------------------- #
def asset_requested(req):
    """New request → whoever approves at the supervisor gate."""
    asset = req.item_label
    for user in _supervisors_for(req.requested_by):
        _send(user, Category.INVENTORY_ASSET_REQUESTED,
              f"Asset Request — {asset} ({req.requested_by_name})",
              f"{req.requested_by_name} requested {asset}. Purpose: {req.purpose} "
              f"Reference {req.reference}. Awaiting your approval.",
              "/inventory/requests", f"assetreq-{req.id}-supervisor-{user.id}",
              reference=req.reference, object_id=str(req.id))


def asset_request_at_inventory(req):
    """Supervisor approved → the store, who choose the asset and hand it over."""
    asset = req.item_label
    for user in _officers():
        _send(user, Category.INVENTORY_ASSET_APPROVAL_REQUIRED,
              f"Asset Request Approved by Supervisor — {asset}",
              f"{req.requested_by_name}'s request for {asset} was approved by "
              f"{req.supervisor_name}. Reference {req.reference}. "
              f"Awaiting inventory approval.",
              "/inventory/requests", f"assetreq-{req.id}-inventory-{user.id}",
              reference=req.reference, object_id=str(req.id))


def asset_request_ready(req):
    """Inventory approved → tell the requester it is waiting for them."""
    _send(req.requested_by, Category.INVENTORY_ASSET_READY,
          f"Asset Ready — {req.item_label}",
          f"Your request for {req.item_label} was approved. Reference "
          f"{req.reference}. The inventory officer will hand it over to you.",
          "/inventory/requests", f"assetreq-{req.id}-ready",
          reference=req.reference, object_id=str(req.id))


def asset_handed_over(req):
    """
    Handed over → the recipient, who is the only one who can confirm receipt.

    This is the notification that matters most in the chain: until they accept,
    the record says an officer handed something over, not that anybody has it.
    """
    _send(req.requested_by, Category.INVENTORY_ASSET_HANDED_OVER,
          f"Confirm Receipt — {req.item_label}",
          f"{req.item_label} was handed over to you. Reference {req.reference}. "
          f"Please confirm you have received it - custody is not recorded against "
          f"you until you do.",
          "/inventory/requests", f"assetreq-{req.id}-handover",
          reference=req.reference, object_id=str(req.id))


def asset_accepted(req):
    """Accepted → the officer who handed it over, so the store can close it off."""
    for user in _officers():
        _send(user, Category.INVENTORY_ASSET_ACCEPTED,
              f"Asset Receipt Confirmed — {req.item_label}",
              f"{req.requested_by_name} confirmed receipt of {req.item_label}. "
              f"Reference {req.reference}.",
              "/inventory/requests", f"assetreq-{req.id}-accepted-{user.id}",
              reference=req.reference, object_id=str(req.id))


def asset_request_refused(req):
    """Refused at either gate → the requester, with the reason."""
    _send(req.requested_by, Category.INVENTORY_ASSET_REJECTED,
          f"Asset Request Refused — {req.item_label}",
          f"Your request for {req.item_label} was refused by "
          f"{req.rejected_by_name}. Reason: {req.rejection_reason}",
          "/inventory/requests", f"assetreq-{req.id}-refused",
          reference=req.reference, object_id=str(req.id))


# --------------------------------------------------------------------------- #
# Returns (Phase 70.6)
# --------------------------------------------------------------------------- #
def return_raised(record):
    """A holder giving something back → the store, who must inspect it."""
    for user in _officers():
        _send(user, Category.INVENTORY_RETURN_REQUESTED,
              f"Asset Return — {record.item_code} · {record.item_name}",
              f"{record.returned_by_name} is returning "
              f"{record.item_code} · {record.item_name}, declared "
              f"{record.get_declared_condition_display()}. Reference "
              f"{record.reference}. Awaiting verification.",
              "/inventory/requests", f"assetret-{record.id}-raised-{user.id}",
              reference=record.reference, object_id=str(record.id))


def return_closed(record):
    """
    Accepted or refused → the person who handed it back.

    Refusal matters more than acceptance here: a refused return means the asset is
    still theirs, and somebody who thinks they have given it back will not find
    out any other way until an overdue report names them.
    """
    accepted = record.status == record.Status.ACCEPTED
    if accepted:
        title = f"Return Accepted — {record.item_code}"
        body = (f"Your return of {record.item_code} · {record.item_name} was "
                f"accepted. It is no longer recorded against you.")
    else:
        title = f"Return Refused — {record.item_code}"
        body = (f"Your return of {record.item_code} · {record.item_name} was not "
                f"accepted: {record.rejection_reason or 'no reason given'}. The "
                f"asset is still recorded against you.")
    _send(record.returned_by, Category.INVENTORY_RETURN_COMPLETED, title, body,
          "/inventory/requests", f"assetret-{record.id}-{record.status}",
          reference=record.reference, object_id=str(record.id))


# --------------------------------------------------------------------------- #
# Maintenance (Phase 70.8)
# --------------------------------------------------------------------------- #
def maintenance_reported(ticket):
    """A fault → the store, who decide whether it goes out for repair."""
    for user in _officers():
        _send(user, Category.INVENTORY_MAINTENANCE_REPORTED,
              f"Fault Reported — {ticket.item_code} · {ticket.item_name}",
              f"{ticket.reported_by_name} reported a "
              f"{ticket.get_priority_display().lower()} priority fault on "
              f"{ticket.item_code}: {ticket.issue} Reference {ticket.reference}.",
              "/inventory/maintenance",
              f"assetmt-{ticket.id}-reported-{user.id}",
              reference=ticket.reference, object_id=str(ticket.id))


def maintenance_done(ticket):
    """Repaired → whoever reported it, who has been waiting without the asset."""
    _send(ticket.reported_by, Category.INVENTORY_MAINTENANCE_DONE,
          f"Repair Completed — {ticket.item_code}",
          f"The fault you reported on {ticket.item_code} · {ticket.item_name} has "
          f"been resolved: {ticket.resolution} Reference {ticket.reference}.",
          "/inventory/maintenance", f"assetmt-{ticket.id}-done",
          reference=ticket.reference, object_id=str(ticket.id))


def takeout_finalized(req):
    """Approve/reject → notify the requester."""
    approved = req.status == req.Status.APPROVED
    ctx = _ctx(req)
    if approved:
        cat = Category.INVENTORY_TAKEOUT_APPROVED
        title = f"Take-Out Approved — {req.item_name}"
        body = (f"Your request to take '{req.item_name}' was approved by {req.approver_name}. "
                f"Reference {req.reference}. Please collect the gate pass.")
    else:
        cat = Category.INVENTORY_TAKEOUT_REJECTED
        title = f"Take-Out Rejected — {req.item_name}"
        body = f"Your request to take '{req.item_name}' was rejected. Reason: {req.approver_remarks}"
    _safe("finalized->requester", lambda: notify(
        req.requested_by, cat, title, body,
        action_url="/inventory/my-requests",
        idempotency_key=f"takeout-{req.id}-{req.status}",
        email_context=ctx, object_id=str(req.id)))


# --------------------------------------------------------------------------- #
# Custody transfers (Phase ASSET-CUSTODY-TRANSFER)
# --------------------------------------------------------------------------- #
def _transfer_ctx(transfer):
    return {
        "reference": transfer.transfer_number,
        "item": f"{transfer.item_code} · {transfer.item_name}",
        "from_employee": transfer.from_employee_name,
        "to_employee": transfer.to_employee_name,
        "reason": transfer.get_reason_display(),
        "condition": transfer.get_condition_display(),
        "stage": transfer.get_status_display(),
        "remarks": transfer.rejection_remarks or transfer.remarks or "",
        "object_id": str(transfer.id),
    }


def _transfer_url(transfer):
    return f"/inventory/transfers?open={transfer.id}"


def _not_a_party(users, transfer):
    """Nobody is asked to approve a transfer they raised, give or receive."""
    parties = {transfer.from_employee_id, transfer.to_employee_id,
               transfer.requested_by_id}
    seen, out = set(), []
    for user in users:
        if user.id not in parties and user.id not in seen:
            seen.add(user.id)
            out.append(user)
    return out


def _approvers_for(transfer):
    """
    Who decides the gate the transfer is now waiting at.

    One gate since Phase ASSET-TRANSFER-GOVERNANCE: HR. The department heads who
    used to be asked here are no longer approvers and must not be paged about a
    decision they cannot make.

    Kept aligned with `transfers.can_act_at_stage` by construction - it asks
    exactly the people that function admits, so nobody is invited to approve
    something the server will then refuse, and a transfer is never left waiting on
    nobody.
    """
    from .models import AssetTransfer

    if transfer.status not in AssetTransfer.REVIEW_STAGES:
        return []
    from .transfers import eligible_hr

    hr = list(eligible_hr(transfer))
    if hr:
        return hr
    # HR is giving, receiving or raised this one: Admin stands in, and is asked.
    admins = User.objects.filter(role=User.Roles.ADMIN, is_active=True)
    return _not_a_party(admins, transfer)


def _ask_for_approval(transfer):
    ctx = _transfer_ctx(transfer)
    for user in _approvers_for(transfer):
        _send(user, Category.INVENTORY_TRANSFER_APPROVAL_REQUIRED,
              f"Transfer approval required — {transfer.transfer_number}",
              f"{transfer.item_code} ({transfer.item_name}) is to move from "
              f"{transfer.from_employee_name} to {transfer.to_employee_name} "
              f"({transfer.get_reason_display()}). It is waiting for your decision at "
              f"{transfer.get_status_display()}.",
              _transfer_url(transfer),
              f"transfer-{transfer.id}-{transfer.status}-approval-{user.id}", **ctx)


def transfer_submitted(transfer):
    """Tell both parties a transfer naming them has been submitted, and ask gate one."""
    ctx = _transfer_ctx(transfer)
    for user in (transfer.from_employee, transfer.to_employee):
        if user is None:
            continue
        _send(user, Category.INVENTORY_TRANSFER_SUBMITTED,
              f"Asset transfer submitted — {transfer.transfer_number}",
              f"A transfer of {transfer.item_code} ({transfer.item_name}) from "
              f"{transfer.from_employee_name} to {transfer.to_employee_name} has been "
              "submitted for approval. Nothing changes until it is approved.",
              _transfer_url(transfer),
              f"transfer-{transfer.id}-submitted-{user.id}", **ctx)
    _ask_for_approval(transfer)


def transfer_awaiting(transfer):
    """A gate approved: tell the requester, and ask the next gate."""
    ctx = _transfer_ctx(transfer)
    if transfer.requested_by is not None:
        _send(transfer.requested_by, Category.INVENTORY_TRANSFER_APPROVED,
              f"Transfer approved at a stage — {transfer.transfer_number}",
              f"{transfer.transfer_number} has been approved and is now at "
              f"{transfer.get_status_display()}.",
              _transfer_url(transfer),
              f"transfer-{transfer.id}-advanced-{transfer.status}", **ctx)
    _ask_for_approval(transfer)


def transfer_rejected(transfer):
    ctx = _transfer_ctx(transfer)
    for user in (transfer.requested_by, transfer.from_employee, transfer.to_employee):
        if user is None:
            continue
        _send(user, Category.INVENTORY_TRANSFER_REJECTED,
              f"Asset transfer rejected — {transfer.transfer_number}",
              f"{transfer.rejected_by_name} rejected {transfer.transfer_number} at "
              f"{transfer.get_rejected_stage_display_safe()}: "
              f"{transfer.rejection_remarks}. {transfer.item_code} stays with "
              f"{transfer.from_employee_name}.",
              _transfer_url(transfer),
              f"transfer-{transfer.id}-rejected-{user.id}", **ctx)


def transfer_completed(transfer):
    """Final approval executed: tell everyone involved, and the receiver it is theirs."""
    ctx = _transfer_ctx(transfer)
    people = {}
    for user in (transfer.requested_by, transfer.from_employee, transfer.to_employee):
        if user is not None:
            people[user.id] = user
    for user in people.values():
        _send(user, Category.INVENTORY_TRANSFER_COMPLETED,
              f"Asset transfer completed — {transfer.transfer_number}",
              f"{transfer.item_code} ({transfer.item_name}) has moved from "
              f"{transfer.from_employee_name} to {transfer.to_employee_name}. "
              f"Approved by {transfer.approved_by_name}.",
              _transfer_url(transfer),
              f"transfer-{transfer.id}-completed-{user.id}", **ctx)
    if transfer.to_employee is not None:
        _send(transfer.to_employee, Category.INVENTORY_ASSET_ASSIGNED,
              f"{transfer.item_name} is now assigned to you",
              f"{transfer.item_code} ({transfer.item_name}) is now in your custody "
              f"under transfer {transfer.transfer_number}. You are responsible for it "
              "until it is transferred or returned.",
              "/inventory/my-assets",
              f"transfer-{transfer.id}-assigned-{transfer.to_employee_id}", **ctx)


# --------------------------------------------------------------------------- #
# Disposals (Phase ASSET-LIFECYCLE-DISPOSAL)
# --------------------------------------------------------------------------- #
def _disposal_ctx(disposal):
    return {
        "reference": disposal.disposal_number,
        "item": f"{disposal.item_code} · {disposal.item_name}",
        "type": disposal.get_disposal_type_display(),
        "reason": disposal.reason,
        "stage": disposal.get_status_display(),
        "remarks": disposal.rejection_remarks or "",
        "object_id": str(disposal.id),
    }


def _disposal_url(disposal):
    return f"/inventory/disposals?open={disposal.id}"


def _disposal_approvers(disposal):
    from .disposals import heads_for, is_party
    from .models import AssetDisposal

    admins = [u for u in User.objects.filter(role=User.Roles.ADMIN, is_active=True)
              if not is_party(u, disposal)]
    if disposal.status == AssetDisposal.Status.DEPT_HEAD_REVIEW:
        heads = [u for u in heads_for(disposal) if not is_party(u, disposal)]
        return heads or admins
    if disposal.status == AssetDisposal.Status.ADMIN_APPROVAL:
        return admins
    return []


def disposal_awaiting(disposal):
    """Submitted, or cleared by the department head: ask whoever decides next."""
    from .models import AssetDisposal

    ctx = _disposal_ctx(disposal)
    for user in _disposal_approvers(disposal):
        _send(user, Category.INVENTORY_DISPOSAL_APPROVAL_REQUIRED,
              f"Disposal approval required — {disposal.disposal_number}",
              f"{disposal.item_code} ({disposal.item_name}) is proposed for disposal: "
              f"{disposal.get_disposal_type_display()}. Reason: {disposal.reason}",
              _disposal_url(disposal),
              f"disposal-{disposal.id}-{disposal.status}-approval-{user.id}", **ctx)
    if disposal.status == AssetDisposal.Status.ADMIN_APPROVAL and disposal.requested_by:
        _send(disposal.requested_by, Category.INVENTORY_DISPOSAL_APPROVED,
              f"Disposal approved by the department head — {disposal.disposal_number}",
              f"{disposal.disposal_number} is now with an administrator for final approval.",
              _disposal_url(disposal), f"disposal-{disposal.id}-advanced", **ctx)


def disposal_rejected(disposal):
    ctx = _disposal_ctx(disposal)
    for user in {u.id: u for u in (disposal.requested_by, disposal.last_holder) if u}.values():
        _send(user, Category.INVENTORY_DISPOSAL_REJECTED,
              f"Disposal rejected — {disposal.disposal_number}",
              f"{disposal.rejected_by_name} rejected the disposal of {disposal.item_code} "
              f"at {disposal.get_rejected_stage_display_safe()}: "
              f"{disposal.rejection_remarks}. The asset stays on the books.",
              _disposal_url(disposal), f"disposal-{disposal.id}-rejected-{user.id}", **ctx)


def disposal_completed(disposal):
    ctx = _disposal_ctx(disposal)
    people = {u.id: u for u in _officers()}
    for user in (disposal.requested_by, disposal.last_holder):
        if user:
            people[user.id] = user
    for user in people.values():
        _send(user, Category.INVENTORY_DISPOSAL_COMPLETED,
              f"Asset disposed — {disposal.item_code}",
              f"{disposal.item_code} ({disposal.item_name}) was disposed of "
              f"({disposal.get_disposal_type_display()}) under {disposal.disposal_number}, "
              f"approved by {disposal.approved_by_name}. It stays on file with its history.",
              _disposal_url(disposal), f"disposal-{disposal.id}-completed-{user.id}", **ctx)


# --------------------------------------------------------------------------- #
# Lifecycle alerts (Phase ASSET-LIFECYCLE-DISPOSAL)
# --------------------------------------------------------------------------- #
ALERTS = {
    "warranty": (Category.INVENTORY_WARRANTY_EXPIRING, "Warranty"),
    "amc": (Category.INVENTORY_AMC_EXPIRING, "Maintenance contract"),
    "end_of_life": (Category.INVENTORY_END_OF_LIFE, "End of life"),
}


def lifecycle_alert(kind, item, due, state):
    """
    One alert about one asset, to everyone who runs the store.

    The idempotency key carries the due date and the state, so an asset is
    announced once when it enters the warning window and once more when the date
    passes - not every morning the daily job runs, which is how an alert becomes
    something people learn to ignore.
    """
    category, noun = ALERTS[kind]
    passed = state in ("expired", "past")
    verb = "ended" if passed else "ends"
    title = f"{noun} {verb} — {item.asset_code}"
    body = (f"{noun} for {item.asset_code} ({item.name}) {verb} on {due}"
            + (". Renew it or plan the replacement." if not passed else
               ". It is no longer covered."))
    ctx = {"reference": item.asset_code, "item": f"{item.asset_code} · {item.name}",
           "due": str(due), "state": state, "object_id": str(item.id)}
    for user in _officers():
        _send(user, category, title, body, f"/inventory/items/{item.id}",
              f"lifecycle-{kind}-{item.id}-{due}-{'passed' if passed else 'due'}-{user.id}",
              **ctx)
