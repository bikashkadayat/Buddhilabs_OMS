"""
An asset's owner history, told as the sentences a person would use
(Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD).

    Assigned to Bikash Kadayat
    Transferred to Raj Kumar
    Transferred to Amar
    Returned to Inventory

WHY THIS IS DERIVED AND NOT STORED
----------------------------------
Custody already has a permanent, append-only record: one ItemAssignment row per
spell of ownership, closed rather than deleted. Every sentence above is a fact
about those rows - who opened a spell, how it was opened, and how it ended. A
second "owner history" table would be a copy that could disagree with the
original, and when a copy disagrees with the record, the copy is the one people
read. So this reads the rows, and cannot drift from them.

HOW A SPELL IS NAMED
--------------------
Opening a spell:
  * a COMPLETED transfer opened it          -> "Transferred to X"   (with the TRF number)
  * a direct handover opened it             -> "Handed over to X"
  * anything else                           -> "Assigned to X"

Closing a spell:
  * a transfer or handover closed it        -> nothing: the next spell's opening
                                               already says where it went, and a
                                               "Returned" line between the two would
                                               claim the asset visited the store
  * a lost-asset disposal closed it         -> "Reported lost"
  * otherwise                               -> "Returned to Inventory"

Distinguishing "Transferred" from "Handed over" matters for an audit: the first
was approved by HR under a numbered request; the second was done at the store
desk. Calling both "transferred" would claim an approval that one of them never
had.
"""
from .models import AssetDisposal, AssetTransfer, ItemAssignment

LOST_PREFIX = "Reported lost"


def ownership_timeline(item):
    """Every change of hands for `item`, oldest first, as display-ready entries."""
    spells = list(ItemAssignment.objects.filter(item=item).order_by("assigned_at", "id"))
    completed = AssetTransfer.objects.filter(item=item, status=AssetTransfer.Status.COMPLETED)
    opened_by = {t.opened_assignment_id: t for t in completed if t.opened_assignment_id}
    closed_by = {t.closed_assignment_id: t for t in completed if t.closed_assignment_id}

    entries = []
    for index, spell in enumerate(spells):
        following = spells[index + 1] if index + 1 < len(spells) else None
        transfer = opened_by.get(spell.id)

        if transfer is not None:
            kind, verb = "transferred", "Transferred to"
        elif spell.is_handover:
            kind, verb = "handed_over", "Handed over to"
        else:
            kind, verb = "assigned", "Assigned to"
        entries.append({
            "kind": kind,
            "label": f"{verb} {spell.assigned_to_name}",
            "owner": spell.assigned_to_name,
            "at": spell.assigned_at,
            "date": spell.assigned_date,
            "by": spell.assigned_by_name,
            "reference": transfer.transfer_number if transfer else "",
            "from_owner": transfer.from_employee_name if transfer else "",
            "condition": spell.handover_condition,
            "is_current": spell.is_active,
        })

        if spell.is_active:
            continue
        # Closed by the move that opened the next spell: that spell's own line
        # already records where the asset went.
        moved_on = spell.id in closed_by or (
            following is not None and (following.is_handover or following.id in opened_by))
        if moved_on:
            continue
        lost = (spell.return_remarks or "").startswith(LOST_PREFIX)
        entries.append({
            "kind": "lost" if lost else "returned",
            "label": "Reported lost" if lost else "Returned to Inventory",
            "owner": "",
            "at": spell.returned_at,
            "date": spell.returned_at.date() if spell.returned_at else None,
            "by": "",
            "reference": spell.return_remarks if lost else "",
            "from_owner": spell.assigned_to_name,
            "condition": spell.return_condition,
            "is_current": False,
        })

    disposal = (AssetDisposal.objects
                .filter(item=item, status=AssetDisposal.Status.DISPOSED)
                .order_by("-disposed_at").first())
    if disposal is not None:
        entries.append({
            "kind": "disposed",
            "label": f"Disposed — {disposal.get_disposal_type_display()}",
            "owner": "", "at": disposal.disposed_at,
            "date": disposal.disposed_at.date() if disposal.disposed_at else None,
            "by": disposal.approved_by_name, "reference": disposal.disposal_number,
            "from_owner": "", "condition": "", "is_current": False,
        })
    return entries
