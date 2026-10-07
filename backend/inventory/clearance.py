"""
Exit clearance: does this employee still hold organisation assets?
(Phase ASSET-CUSTODY-TRANSFER)

WHAT "CLEAR" MEANS
------------------
An employee is clear when they hold NO active custody row. Not when a transfer or
a return has been raised - when it has FINISHED. A return that is still being
inspected means the laptop may yet come back refused, and a transfer awaiting HR
may yet be rejected. Counting either as clearance would sign somebody out of the
organisation with an asset still in their name.

So each asset they hold is reported with how it is being resolved (a transfer in
progress, a return in progress, or nothing yet), and the status is BLOCKED until
the last one has actually completed.

WHY THIS DOES NOT BLOCK ACCOUNT DEACTIVATION
--------------------------------------------
There is no separation workflow in this system to plug into, and the one existing
"employee leaves" action is deactivating their account. That must NOT be gated on
assets. When somebody is dismissed, their access has to end the same minute,
whether or not their laptop is back; a rule that kept a departing employee's
login alive until a device was returned would turn an asset control into a
security hole. Clearance is therefore a status HR checks and reports on, and it
names inactive accounts that still hold assets as the case that most needs
chasing.
"""
from django.db.models import Count, Q

from .models import AssetReturn, AssetTransfer, ItemAssignment

BLOCKED = "BLOCKED"
CLEAR = "CLEAR"
BLOCKED_MESSAGE = "Asset clearance required before separation."
CLEAR_MESSAGE = "No organisation assets are held. Asset clearance is complete."


def _open_transfer_by_item(item_ids):
    return {t.item_id: t for t in AssetTransfer.objects.filter(
        item_id__in=item_ids, status__in=AssetTransfer.OPEN_STATUSES)}


def _open_return_by_item(item_ids):
    return {r.item_id: r for r in AssetReturn.objects.filter(
        item_id__in=item_ids, status__in=AssetReturn.OPEN_STATUSES)}


def exit_clearance(employee):
    """The clearance status for one employee, with each asset they still hold."""
    holdings = list(
        ItemAssignment.objects.filter(assigned_to=employee, is_active=True)
        .select_related("item", "item__category").order_by("item_code"))
    item_ids = [h.item_id for h in holdings if h.item_id]
    transfers = _open_transfer_by_item(item_ids)
    returns = _open_return_by_item(item_ids)

    assets = []
    for holding in holdings:
        transfer = transfers.get(holding.item_id)
        return_record = returns.get(holding.item_id)
        if transfer is not None:
            resolution = "transfer_in_progress"
        elif return_record is not None:
            resolution = "return_in_progress"
        else:
            resolution = "unresolved"
        item = holding.item
        assets.append({
            "item_id": str(holding.item_id) if holding.item_id else None,
            "asset_code": holding.item_code,
            "name": holding.item_name,
            "category": getattr(getattr(item, "category", None), "name", "") or "",
            "assigned_date": holding.assigned_date,
            "condition": getattr(item, "condition", "") or holding.handover_condition,
            "resolution": resolution,
            "transfer": ({"id": str(transfer.id), "number": transfer.transfer_number,
                          "status": transfer.status,
                          "status_label": transfer.get_status_display(),
                          "to": transfer.to_employee_name}
                         if transfer else None),
            "return": ({"id": str(return_record.id),
                        "reference": return_record.reference,
                        "status": return_record.status,
                        "status_label": return_record.get_status_display()}
                       if return_record else None),
        })

    blocked = bool(assets)
    return {
        "employee_id": str(employee.id),
        "employee": employee.get_full_name() or employee.username,
        "email": employee.email,
        "department": getattr(employee, "department_name", None) or "",
        "is_active": employee.is_active,
        "status": BLOCKED if blocked else CLEAR,
        "message": BLOCKED_MESSAGE if blocked else CLEAR_MESSAGE,
        "assets_held": len(assets),
        "unresolved": sum(1 for a in assets if a["resolution"] == "unresolved"),
        "in_transfer": sum(1 for a in assets if a["resolution"] == "transfer_in_progress"),
        "in_return": sum(1 for a in assets if a["resolution"] == "return_in_progress"),
        "assets": assets,
    }


def exit_clearance_report():
    """
    Everyone who holds assets, inactive accounts first.

    An inactive account holding assets is somebody who has already left with the
    organisation's property still in their name - the row that most needs chasing,
    so it sorts to the top rather than into alphabetical order.
    """
    from users.models import User

    holders = (User.objects.annotate(
        held=Count("inventory_assignments",
                   filter=Q(inventory_assignments__is_active=True)))
        .filter(held__gt=0))
    rows = []
    for user in holders:
        detail = exit_clearance(user)
        # Shaped for a report table, whose columns are derived from the keys: no
        # raw ids, and not the same sentence repeated on every row.
        rows.append({
            "employee": detail["employee"],
            "email": detail["email"],
            "department": detail["department"],
            "account": "Active" if detail["is_active"] else "Inactive — already left",
            "status": detail["status"],
            "assets_held": detail["assets_held"],
            "not_started": detail["unresolved"],
            "in_transfer": detail["in_transfer"],
            "in_return": detail["in_return"],
            "asset_codes": ", ".join(a["asset_code"] for a in detail["assets"]),
            "_is_active": detail["is_active"],
        })
    rows.sort(key=lambda r: (r["_is_active"], r["employee"].lower()))
    for row in rows:
        row.pop("_is_active")
    return rows
