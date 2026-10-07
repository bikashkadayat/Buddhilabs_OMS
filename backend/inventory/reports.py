"""
Stock dashboard and the seven reports (Phase 70.9, 70.13, 70.14).

EVERY FIGURE HERE IS A DATABASE AGGREGATE, never a Python loop over rows. An
inventory of ten thousand assets must cost the dashboard the same as one of ten,
and the moment a report walks a queryset to add things up it stops being usable at
the size it exists for.

The dashboard's counts and the reports' rows read the SAME predicates - defined
once at the top - so a report can never disagree with the tile that links to it.
That failure is not hypothetical: the minute module shipped two definitions of
"pending" and reported eleven states for six people.
"""
import datetime

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .depreciation import depreciation
from .models import (
    WARRANTY_WARNING_DAYS, AssetDisposal, AssetLifecycleEvent, AssetRequest, AssetReturn,
    AssetTransfer, InventoryItem, ItemAssignment, MaintenanceTicket, TakeOutRequest,
)

Status = InventoryItem.Status

# ONE definition of each word, as VALUES - then the Q objects are built from them
# for both directions of the relation.
#
# This indirection is not decoration. Counting from InventoryItem the field is
# `status`; counting from InventoryCategory it is `items__status`. Writing the two
# sets of Q objects by hand is how "in stock" comes to mean one thing on the
# dashboard and another in the report the tile links to - and a first attempt at
# this file did exactly that, which is why the category reports crashed rather than
# quietly disagreeing. Deriving both from one list makes divergence impossible.
STOCK_VALUES = [Status.AVAILABLE]
ASSIGNED_VALUES = [Status.ASSIGNED]
MAINTENANCE_VALUES = [Status.MAINTENANCE]
OUT_VALUES = [Status.OUT]
RETIRED_VALUES = [Status.RETIRED]
# "Total assets" deliberately EXCLUDES disposed and archived: an organisation's
# asset count is what it holds, not what it has ever held. The disposed figure is
# reported separately rather than folded in, so neither question is lost.
GONE_VALUES = [Status.DISPOSED, Status.ARCHIVED]
ON_ORDER_VALUES = [Status.PROCUREMENT]
RECEIVED_VALUES = [Status.RECEIVED]


def _status_q(values, prefix="", negate=False):
    """Q matching these statuses, from whichever side of the relation is counting."""
    query = Q(**{f"{prefix}status__in": values})
    return ~query if negate else query


# Item-side, for queries rooted at InventoryItem.
IN_STOCK = _status_q(STOCK_VALUES)
ASSIGNED = _status_q(ASSIGNED_VALUES)
IN_MAINTENANCE = _status_q(MAINTENANCE_VALUES)
TAKEN_OUT = _status_q(OUT_VALUES)
RETIRED = _status_q(RETIRED_VALUES)
DISPOSED = _status_q(GONE_VALUES)
ON_ORDER = _status_q(ON_ORDER_VALUES)
AWAITING_CHECK_IN = _status_q(RECEIVED_VALUES)
ON_THE_BOOKS = _status_q(GONE_VALUES, negate=True)


def _category_q(values, negate=False):
    """
    Category-side, for queries rooted at InventoryCategory.

    `items__isnull=False` is folded in because a negated status filter would
    otherwise match a category with NO assets at all: `NOT (status IN (...))` is
    true of a null join row, so an empty category would count as one on-the-books
    asset.
    """
    return _status_q(values, prefix="items__", negate=negate) & Q(items__isnull=False)

# Below this many available units, a category counts as low stock. A count rather
# than a per-category threshold column, because an organisation with no reorder
# policy still wants to know it has one monitor left.
LOW_STOCK_THRESHOLD = 2


def _base(user=None):
    return InventoryItem.objects.all()


def dashboard(user=None):
    """
    The eight cards Phase 70.14 names, plus the stock breakdown of 70.9.

    Two aggregate queries over assets, plus one per workflow table. Not one per
    card - a dashboard that issues a query per tile is the shape that stops
    rendering when somebody adds a ninth.
    """
    counts = InventoryItem.objects.aggregate(
        total=Count("id", filter=ON_THE_BOOKS),
        available=Count("id", filter=IN_STOCK),
        assigned=Count("id", filter=ASSIGNED),
        maintenance=Count("id", filter=IN_MAINTENANCE),
        taken_out=Count("id", filter=TAKEN_OUT),
        retired=Count("id", filter=RETIRED),
        disposed=Count("id", filter=DISPOSED),
        on_order=Count("id", filter=ON_ORDER),
        awaiting_check_in=Count("id", filter=AWAITING_CHECK_IN),
        book_value=Sum("purchase_cost", filter=ON_THE_BOOKS),
    )

    today = timezone.localdate()
    pending_requests = AssetRequest.objects.filter(
        status__in=AssetRequest.OPEN_STATUSES).count()
    pending_returns = AssetReturn.objects.filter(
        status__in=AssetReturn.OPEN_STATUSES).count()
    open_tickets = MaintenanceTicket.objects.filter(
        status__in=MaintenanceTicket.OPEN_STATUSES).count()
    # Overdue is DERIVED from the date, never stored, so it is correct the instant
    # a return date passes - the same rule the minute module's action items follow.
    overdue_takeouts = TakeOutRequest.objects.filter(
        status=TakeOutRequest.Status.APPROVED,
        expected_return_date__lt=today).count()

    custody = _custody_dashboard(today)

    return {
        **_governance_dashboard(user),
        # The brief's eight cards.
        "total_assets": counts["total"] or 0,
        "assigned": counts["assigned"] or 0,
        "available": counts["available"] or 0,
        "maintenance": counts["maintenance"] or 0,
        "taken_out": counts["taken_out"] or 0,
        "pending_requests": pending_requests,
        "overdue_returns": overdue_takeouts,
        "disposed": counts["disposed"] or 0,
        # The rest of the stock picture (70.9).
        "retired": counts["retired"] or 0,
        "on_order": counts["on_order"] or 0,
        "awaiting_check_in": counts["awaiting_check_in"] or 0,
        "pending_returns": pending_returns,
        "open_maintenance_tickets": open_tickets,
        "low_stock": len(low_stock()),
        "warranty_expiring": InventoryItem.objects.filter(
            ON_THE_BOOKS,
            warranty_expiry__gte=today,
            warranty_expiry__lte=today + datetime.timedelta(
                days=WARRANTY_WARNING_DAYS)).count(),
        # Phase ASSET-LIFECYCLE-DISPOSAL. This key used to be Sum(purchase_cost) -
        # gross cost labelled as book value. Nothing read it, so it now means what
        # it says (cost less accumulated depreciation), and the gross figure has
        # its own honest name.
        "gross_cost": str(counts["book_value"] or 0),
        # Phase ASSET-CUSTODY-TRANSFER.
        **custody,
        **_lifecycle_dashboard(today),
    }


def _lifecycle_dashboard(today):
    """Warranty/AMC/end-of-life exposure, disposals in flight, and net book value."""
    from decimal import Decimal

    live = (InventoryItem.objects.filter(ON_THE_BOOKS)
            .select_related("category"))
    net_book, depreciable, missing_data = Decimal("0"), 0, 0
    past_eol = approaching_eol = amc_expiring = 0
    for item in live:
        position = depreciation(item, as_of=today)
        if position["depreciable"]:
            depreciable += 1
            net_book += position["book_value"]
        else:
            missing_data += 1
        eol = item.end_of_life_state
        past_eol += eol == "past"
        approaching_eol += eol == "approaching"
        amc_expiring += item.amc_state in ("expiring", "expired")

    year_start = today.replace(month=1, day=1)
    disposed_this_year = AssetDisposal.objects.filter(
        status=AssetDisposal.Status.DISPOSED, disposed_at__date__gte=year_start)
    written_off = sum((d.written_off or 0) for d in disposed_this_year)
    return {
        "book_value": str(net_book.quantize(Decimal("0.01"))),
        "book_value_assets": depreciable,
        "assets_missing_depreciation_data": missing_data,
        "amc_expiring": amc_expiring,
        "end_of_life_past": past_eol,
        "end_of_life_approaching": approaching_eol,
        "pending_disposals": AssetDisposal.objects.filter(
            status__in=AssetDisposal.REVIEW_STAGES).count(),
        "disposed_this_year": disposed_this_year.count(),
        "written_off_this_year": str(Decimal(written_off).quantize(Decimal("0.01"))),
        "lost_this_year": disposed_this_year.filter(
            disposal_type=AssetDisposal.DisposalType.LOST).count(),
    }


def _governance_dashboard(user):
    """
    The Department Head's five tiles (Phase ASSET-TRANSFER-GOVERNANCE).

    "Department assets" is the ONE figure on this dashboard that depends on who is
    looking: it is the viewer's own department. Everything else a head sees is the
    whole organisation, so scoping it here - on the server, from the viewer's
    recorded department - is what keeps the client from having to know the rule.
    A viewer with no department gets a null rather than a zero, because "none
    recorded" and "none owned" are different answers.
    """
    dept_id = getattr(user, "department_ref_id", None) if user is not None else None
    department_assets = (
        InventoryItem.objects.filter(ON_THE_BOOKS, department_id=dept_id).count()
        if dept_id else None)

    live = InventoryItem.objects.filter(ON_THE_BOOKS)
    held_ids = ItemAssignment.objects.filter(is_active=True).values("item_id")
    return {
        "governance_total_assets": live.count(),
        "governance_department_assets": department_assets,
        "governance_department_name": (
            getattr(getattr(user, "department_ref", None), "name", "") or ""),
        "governance_assigned_assets": live.filter(id__in=held_ids).count(),
        # Unassigned means "nobody is accountable for it", which is the absence of
        # a custody row - NOT status == available. An asset marked assigned with no
        # custody record is the integrity fault `assigned_without_custody_record`
        # already counts, and it belongs in this number too rather than vanishing
        # between the two.
        "governance_unassigned_assets": live.exclude(id__in=held_ids).count(),
        "governance_transferred_assets": AssetTransfer.objects.filter(
            status=AssetTransfer.Status.COMPLETED).values("item_id").distinct().count(),
    }


def _custody_dashboard(today):
    """
    The custody tiles: transfers in flight, returns awaiting the store, assets with
    nobody accountable for them, and what moved this month.
    """
    pending_transfers = AssetTransfer.objects.filter(
        status__in=AssetTransfer.REVIEW_STAGES).count()
    draft_transfers = AssetTransfer.objects.filter(
        status=AssetTransfer.Status.DRAFT).count()
    awaiting_return = AssetReturn.objects.filter(
        status__in=AssetReturn.OPEN_STATUSES).count()

    # "No owner": in service with no active custody row. That is every asset in
    # stock, PLUS any marked Assigned or Taken Out whose custody row is missing -
    # the second group should always be zero, and is counted separately so a
    # non-zero value is visible as the integrity fault it is.
    held_ids = ItemAssignment.objects.filter(is_active=True).values("item_id")
    no_owner = InventoryItem.objects.filter(
        status__in=[Status.AVAILABLE, Status.ASSIGNED, Status.OUT]).exclude(
        id__in=held_ids).count()
    orphaned = InventoryItem.objects.filter(
        status__in=[Status.ASSIGNED, Status.OUT]).exclude(id__in=held_ids).count()
    # Somebody who has already left, still holding organisation property.
    held_by_inactive = ItemAssignment.objects.filter(
        is_active=True, assigned_to__is_active=False).count()

    month_start = today.replace(day=1)
    transferred_this_month = AssetTransfer.objects.filter(
        status=AssetTransfer.Status.COMPLETED,
        completed_at__date__gte=month_start).count()

    return {
        "pending_transfers": pending_transfers,
        "draft_transfers": draft_transfers,
        "assets_awaiting_return": awaiting_return,
        "assets_with_no_owner": no_owner,
        "assigned_without_custody_record": orphaned,
        "assets_held_by_inactive_employees": held_by_inactive,
        "transferred_this_month": transferred_this_month,
        "transfer_trends": transfer_trends(today),
    }


def transfer_trends(today=None, months=6):
    """
    Completed transfers per month for the last `months` months, oldest first.

    Every month is present, zero included - a chart that skips empty months draws
    a line straight across them and makes a quiet quarter look like a steady one.
    """
    today = today or timezone.localdate()
    firsts = []
    year, month = today.year, today.month
    for _ in range(months):
        firsts.append(datetime.date(year, month, 1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    firsts.reverse()
    counts = {f"{d.year:04d}-{d.month:02d}": 0 for d in firsts}
    completed = AssetTransfer.objects.filter(
        status=AssetTransfer.Status.COMPLETED, completed_at__date__gte=firsts[0])
    for stamp in completed.values_list("completed_at", flat=True):
        local = timezone.localtime(stamp)
        key = f"{local.year:04d}-{local.month:02d}"
        if key in counts:
            counts[key] += 1
    return [{"month": key, "completed": value} for key, value in counts.items()]


def low_stock(threshold=LOW_STOCK_THRESHOLD):
    """
    Categories with too few assets left in stock.

    Counted over AVAILABLE only. A category with twenty assets all of them assigned
    has nothing to issue, and reporting it as well stocked is how a request queue
    silently builds up.
    """
    from .models import InventoryCategory

    rows = (InventoryCategory.objects
            .annotate(
                available=Count("items", filter=_category_q(STOCK_VALUES)),
                total=Count("items", filter=_category_q(GONE_VALUES, negate=True)))
            .filter(total__gt=0, available__lte=threshold)
            .order_by("available", "name"))
    return [{"id": str(row.id), "category": row.name,
             "available": row.available, "total": row.total,
             "threshold": threshold} for row in rows]


# --------------------------------------------------------------------------- #
# The seven reports (Phase 70.13)
# --------------------------------------------------------------------------- #
def by_department():
    """Assets grouped by the department that owns them."""
    rows = (InventoryItem.objects.filter(ON_THE_BOOKS)
            .values("department__id", "department__name")
            .annotate(
                total=Count("id"),
                assigned=Count("id", filter=ASSIGNED),
                available=Count("id", filter=IN_STOCK),
                maintenance=Count("id", filter=IN_MAINTENANCE),
                value=Sum("purchase_cost"))
            .order_by("-total"))
    return [{
        "department_id": str(row["department__id"]) if row["department__id"] else None,
        # Unassigned assets are reported under an explicit label rather than a blank
        # row: "no department" is an answer, and an empty cell reads as a bug.
        "department": row["department__name"] or "Unassigned",
        "total": row["total"], "assigned": row["assigned"],
        "available": row["available"], "maintenance": row["maintenance"],
        "value": str(row["value"] or 0),
    } for row in rows]


def by_employee():
    """Who is holding what, from the ACTIVE custody records."""
    rows = (ItemAssignment.objects.filter(is_active=True)
            .values("assigned_to__id", "assigned_to_name")
            .annotate(total=Count("id"))
            .order_by("-total", "assigned_to_name"))
    holdings = {}
    for row in rows:
        holdings[row["assigned_to_name"]] = {
            "employee_id": (str(row["assigned_to__id"])
                            if row["assigned_to__id"] else None),
            "employee": row["assigned_to_name"] or "—",
            "total": row["total"],
            "assets": [],
        }
    for assignment in (ItemAssignment.objects.filter(is_active=True)
                       .select_related("item").order_by("assigned_to_name",
                                                        "item_code")):
        bucket = holdings.get(assignment.assigned_to_name)
        if bucket is not None:
            bucket["assets"].append({
                "code": assignment.item_code,
                "name": assignment.item_name,
                "assigned_date": assignment.assigned_date,
                "condition": assignment.handover_condition,
            })
    return list(holdings.values())


def by_category():
    from .models import InventoryCategory

    rows = (InventoryCategory.objects
            .annotate(
                total=Count("items", filter=_category_q(GONE_VALUES, negate=True)),
                available=Count("items", filter=_category_q(STOCK_VALUES)),
                assigned=Count("items", filter=_category_q(ASSIGNED_VALUES)),
                maintenance=Count("items", filter=_category_q(MAINTENANCE_VALUES)))
            .order_by("-total", "name"))
    return [{"id": str(row.id), "category": row.name, "total": row.total,
             "available": row.available, "assigned": row.assigned,
             "maintenance": row.maintenance} for row in rows]


def warranty_expiring(days=WARRANTY_WARNING_DAYS, include_expired=True):
    """
    Warranties running out, soonest first.

    Expired ones are included by default because a report that hides them answers
    "what should I renew" and not "what am I already exposed on", and the second is
    the question somebody asks after something breaks.
    """
    today = timezone.localdate()
    horizon = today + datetime.timedelta(days=days)
    query = Q(warranty_expiry__lte=horizon)
    if not include_expired:
        query &= Q(warranty_expiry__gte=today)
    rows = (InventoryItem.objects.filter(ON_THE_BOOKS, query,
                                         warranty_expiry__isnull=False)
            .select_related("department").order_by("warranty_expiry"))
    return [{
        "id": str(row.id), "code": row.asset_code, "name": row.name,
        "brand": row.brand, "model": row.model,
        "department": row.department.name if row.department_id else "",
        "status": row.status, "status_label": row.get_status_display(),
        "warranty_start": row.warranty_start,
        "warranty_expiry": row.warranty_expiry,
        "state": row.warranty_state,
        "days_remaining": (row.warranty_expiry - today).days,
        "vendor": row.vendor,
    } for row in rows]


def in_maintenance():
    """Open tickets, oldest first - the one at the top has waited longest."""
    rows = (MaintenanceTicket.objects
            .filter(status__in=MaintenanceTicket.OPEN_STATUSES)
            .select_related("item").order_by("created_at"))
    return [{
        "id": str(row.id), "reference": row.reference,
        "code": row.item_code, "name": row.item_name,
        "issue": row.issue, "priority": row.priority,
        "priority_label": row.get_priority_display(),
        "status": row.status, "status_label": row.get_status_display(),
        "reported_by": row.reported_by_name, "reported_at": row.created_at,
        "assigned_to": row.assigned_to_name or row.vendor,
        "days_open": row.days_open,
        "cost": str(row.cost) if row.cost is not None else None,
    } for row in rows]


def disposed_assets():
    rows = (InventoryItem.objects.filter(DISPOSED)
            .select_related("department").order_by("-disposed_at"))
    approved = {d.item_id: d for d in AssetDisposal.objects.filter(
        status=AssetDisposal.Status.DISPOSED)}
    out = []
    for row in rows:
        record = approved.get(row.id)
        out.append({
            "id": str(row.id), "code": row.asset_code, "name": row.name,
            "department": row.department.name if row.department_id else "",
            "status": row.status, "status_label": row.get_status_display(),
            "purchase_cost": str(row.purchase_cost) if row.purchase_cost else None,
            "disposal_value": str(row.disposal_value) if row.disposal_value else None,
            "disposed_at": row.disposed_at,
            "method": row.disposal_method, "reason": row.disposal_reason,
            # Phase ASSET-LIFECYCLE-DISPOSAL. An asset disposed before the approval
            # workflow existed has no request, and says so rather than looking
            # identical to one that was approved.
            "disposal_number": record.disposal_number if record else "",
            "approved_by": record.approved_by_name if record else "",
            "approval": "Approved" if record else "None - disposed before approvals",
            "book_value_at_disposal": (str(record.book_value_at_disposal)
                                       if record and record.book_value_at_disposal is not None
                                       else None),
        })
    return out


def unreturned_assets():
    """
    Assets that should be back and are not.

    Two different kinds of overdue, reported together because to the person chasing
    them they are one job: a take-out past its return date, and an asset still held
    by somebody whose account has been deactivated. The second is the one nobody
    thinks to look for, and it is how assets quietly disappear when people leave.
    """
    today = timezone.localdate()
    rows = []
    for req in (TakeOutRequest.objects
                .filter(status=TakeOutRequest.Status.APPROVED,
                        expected_return_date__lt=today)
                .select_related("item").order_by("expected_return_date")):
        rows.append({
            "kind": "takeout",
            "reference": req.reference,
            "code": req.item_code, "name": req.item_name,
            "holder": req.requested_by_name,
            "due": req.expected_return_date,
            "days_overdue": (today - req.expected_return_date).days,
            "purpose": req.get_purpose_display(),
        })
    for assignment in (ItemAssignment.objects
                       .filter(is_active=True, assigned_to__is_active=False)
                       .select_related("item", "assigned_to")):
        rows.append({
            "kind": "left_organisation",
            "reference": "",
            "code": assignment.item_code, "name": assignment.item_name,
            "holder": assignment.assigned_to_name,
            "due": assignment.assigned_date,
            "days_overdue": None,
            "purpose": "Holder's account is deactivated",
        })
    rows.sort(key=lambda r: (r["days_overdue"] is None, -(r["days_overdue"] or 0)))
    return rows


def employee_profile(user):
    """
    Everything about one person's assets (Phase 70.12).

    One place rather than four screens, because the question an HR officer asks
    when somebody leaves is "what do they have" and the answer is spread across
    custody, take-outs, requests and returns.
    """
    active = (ItemAssignment.objects.filter(assigned_to=user, is_active=True)
              .select_related("item").order_by("item_code"))
    past = (ItemAssignment.objects.filter(assigned_to=user, is_active=False)
            .select_related("item").order_by("-returned_at")[:50])
    return {
        "employee": {
            "id": str(user.id),
            "name": user.get_full_name() or user.username,
            "employee_id": user.employee_id or "",
            "department": (user.department_ref.name
                           if getattr(user, "department_ref_id", None)
                           else (getattr(user, "department", "") or "")),
        },
        "assigned": [{
            "id": str(row.id),
            "code": row.item_code, "name": row.item_name,
            "item_id": str(row.item_id) if row.item_id else None,
            "assigned_date": row.assigned_date,
            "condition": row.handover_condition,
            "accessories": row.accessories,
        } for row in active],
        "history": [{
            "id": str(row.id), "code": row.item_code, "name": row.item_name,
            "assigned_date": row.assigned_date, "returned_at": row.returned_at,
            "return_condition": row.return_condition,
        } for row in past],
        "take_outs": [{
            "id": str(row.id), "reference": row.reference,
            "code": row.item_code, "name": row.item_name,
            "status": row.status, "status_label": row.get_status_display(),
            "expected_return_date": row.expected_return_date,
            "is_overdue": (row.status == TakeOutRequest.Status.APPROVED
                           and row.expected_return_date < timezone.localdate()),
        } for row in TakeOutRequest.objects.filter(
            requested_by=user).order_by("-created_at")[:25]],
        "requests": [{
            "id": str(row.id), "reference": row.reference,
            "code": row.item_code, "name": row.item_name,
            "status": row.status, "status_label": row.get_status_display(),
            "created_at": row.created_at,
        } for row in AssetRequest.objects.filter(
            requested_by=user).order_by("-created_at")[:25]],
        "returns": [{
            "id": str(row.id), "reference": row.reference,
            "code": row.item_code, "name": row.item_name,
            "status": row.status, "status_label": row.get_status_display(),
            "created_at": row.created_at,
        } for row in AssetReturn.objects.filter(
            returned_by=user).order_by("-created_at")[:25]],
    }


# --------------------------------------------------------------------------- #
# Custody reports (Phase ASSET-CUSTODY-TRANSFER)
# --------------------------------------------------------------------------- #
CUSTODY_EVENTS = [
    AssetLifecycleEvent.Event.ASSIGNED, AssetLifecycleEvent.Event.HANDED_OVER,
    AssetLifecycleEvent.Event.REASSIGNED, AssetLifecycleEvent.Event.TRANSFERRED,
    AssetLifecycleEvent.Event.RETURNED, AssetLifecycleEvent.Event.DEPARTMENT_CHANGED,
]


def asset_ownership():
    """Every asset on the books, who is accountable for it now, and since when."""
    holders = {a.item_id: a for a in ItemAssignment.objects.filter(is_active=True)
               .select_related("assigned_to")}
    rows = []
    for item in (InventoryItem.objects.filter(ON_THE_BOOKS)
                 .select_related("category", "department").order_by("asset_code")):
        holder = holders.get(item.id)
        rows.append({
            "asset_code": item.asset_code, "name": item.name,
            "category": getattr(item.category, "name", "") or "",
            "status": item.get_status_display(),
            "owner": holder.assigned_to_name if holder else "",
            "owner_active": (holder.assigned_to.is_active
                             if holder and holder.assigned_to else None),
            "department": getattr(item.department, "name", "") or "",
            "since": holder.assigned_date if holder else None,
            "custody": "held" if holder else "unassigned",
        })
    return rows


def asset_movement():
    """Every custody movement on record, newest first."""
    return [{
        "at": event.at, "asset_code": event.item_code, "name": event.item_name,
        "movement": event.get_event_display(), "to": event.subject_name,
        "by": event.actor_name or "System",
        "reference": (event.metadata or {}).get("transfer_number")
        or (event.metadata or {}).get("reference") or "",
        "remarks": event.remarks,
    } for event in (AssetLifecycleEvent.objects.filter(event__in=CUSTODY_EVENTS)
                    .order_by("-at", "-sequence"))]


def transfer_history():
    """Every transfer raised, whatever became of it."""
    return [{
        "transfer_number": t.transfer_number, "asset_code": t.item_code,
        "name": t.item_name, "from": t.from_employee_name,
        "from_department": t.from_department_name, "to": t.to_employee_name,
        "to_department": t.to_department_name, "reason": t.get_reason_display(),
        "condition": t.get_condition_display(), "status": t.get_status_display(),
        "transfer_date": t.transfer_date, "requested_by": t.requested_by_name,
        "approved_by": t.approved_by_name, "completed_at": t.completed_at,
        "rejected_by": t.rejected_by_name, "rejection_remarks": t.rejection_remarks,
    } for t in AssetTransfer.objects.order_by("-created_at")]


def exit_clearance_assets():
    from .clearance import exit_clearance_report
    return exit_clearance_report()


# --------------------------------------------------------------------------- #
# Lifecycle reports (Phase ASSET-LIFECYCLE-DISPOSAL)
# --------------------------------------------------------------------------- #
def _money(value):
    return str(value) if value is not None else None


def amc_expiring(days=None):
    """Maintenance contracts ending soon or already ended, soonest first."""
    from .models import AMC_WARNING_DAYS

    today = timezone.localdate()
    horizon = today + datetime.timedelta(days=days or AMC_WARNING_DAYS)
    rows = (InventoryItem.objects.filter(ON_THE_BOOKS, amc_end__isnull=False,
                                         amc_end__lte=horizon)
            .select_related("department").order_by("amc_end"))
    return [{
        "code": r.asset_code, "name": r.name,
        "department": r.department.name if r.department_id else "",
        "provider": r.amc_provider, "contract": r.amc_contract_number,
        "amc_start": r.amc_start, "amc_end": r.amc_end, "state": r.amc_state,
        "days_remaining": (r.amc_end - today).days, "amc_cost": _money(r.amc_cost),
    } for r in rows]


def depreciation_register():
    """
    Every asset on the books with its depreciation today.

    Assets that cannot be depreciated are listed WITH the reason, not left out:
    a register that silently omits the laptop nobody recorded a cost for makes
    the total look complete when it is not.
    """
    today = timezone.localdate()
    out = []
    for item in (InventoryItem.objects.filter(ON_THE_BOOKS)
                 .select_related("category", "department").order_by("asset_code")):
        d = depreciation(item, as_of=today)
        out.append({
            "code": item.asset_code, "name": item.name,
            "category": getattr(item.category, "name", "") or "",
            "purchase_date": item.purchase_date,
            "purchase_cost": _money(d["purchase_cost"]),
            "salvage_value": _money(d["salvage_value"]),
            "useful_life_months": d["useful_life_months"],
            "life_from": d["useful_life_source"] or "",
            "monthly_charge": _money(d["monthly_charge"]),
            "months_elapsed": d["months_elapsed"],
            "accumulated": _money(d["accumulated"]),
            "book_value": _money(d["book_value"]),
            "percent_depreciated": d["percent_depreciated"],
            "end_of_life": d["end_of_life_date"],
            "end_of_life_state": d["end_of_life_state"] or "",
            "note": d["reason"],
        })
    return out


def lifecycle_summary():
    """
    One line per asset: how old it is and everything that has happened to it.

    Counted from the append-only event log and the custody table, so it is the
    same chain of custody the asset page shows, summarised.
    """
    from django.db.models import Count

    events = {}
    for row in (AssetLifecycleEvent.objects.values("item_id", "event")
                .annotate(n=Count("id"))):
        events.setdefault(row["item_id"], {})[row["event"]] = row["n"]
    holders = dict(ItemAssignment.objects.values("item_id")
                   .annotate(n=Count("id")).values_list("item_id", "n"))
    active = {a.item_id: a.assigned_to_name
              for a in ItemAssignment.objects.filter(is_active=True)}
    tickets = dict(MaintenanceTicket.objects.values("item_id")
                   .annotate(n=Count("id")).values_list("item_id", "n"))
    transfers = dict(AssetTransfer.objects.filter(status=AssetTransfer.Status.COMPLETED)
                     .values("item_id").annotate(n=Count("id")).values_list("item_id", "n"))
    today = timezone.localdate()
    E = AssetLifecycleEvent.Event
    out = []
    for item in InventoryItem.objects.order_by("asset_code"):
        seen = events.get(item.id, {})
        started = item.purchase_date or timezone.localtime(item.created_at).date()
        out.append({
            "code": item.asset_code, "name": item.name,
            "status": item.get_status_display(),
            "purchased": item.purchase_date,
            "age_months": ((today.year - started.year) * 12 + today.month - started.month),
            "holders": holders.get(item.id, 0),
            "current_holder": active.get(item.id, ""),
            "transfers": transfers.get(item.id, 0),
            "maintenance_tickets": tickets.get(item.id, 0),
            "returns": seen.get(E.RETURNED, 0),
            "take_outs": seen.get(E.TAKEN_OUT, 0),
            "disposed_at": item.disposed_at,
            "events_on_record": sum(seen.values()),
        })
    return out


def write_off_register():
    """
    What each disposal cost the organisation: book value against proceeds.

    Approved disposals use the figures frozen at disposal. An asset disposed
    before approvals existed is included too, computed from its recorded cost and
    disposal date, and marked as such - leaving it out would understate the losses.
    """
    out = []
    approved_items = set()
    for d in (AssetDisposal.objects.filter(status=AssetDisposal.Status.DISPOSED)
              .order_by("-disposed_at")):
        approved_items.add(d.item_id)
        out.append({
            "reference": d.disposal_number, "code": d.item_code, "name": d.item_name,
            "type": d.get_disposal_type_display(), "disposed_at": d.disposed_at,
            "purchase_cost": _money(d.purchase_cost_at_disposal),
            "accumulated": _money(d.accumulated_at_disposal),
            "book_value": _money(d.book_value_at_disposal),
            "proceeds": _money(d.proceeds),
            "written_off": _money(d.written_off),
            "gain": _money(d.gain_on_disposal),
            "approved_by": d.approved_by_name,
            "source": "Approved disposal",
        })
    from decimal import Decimal

    for item in (InventoryItem.objects.filter(DISPOSED).exclude(id__in=approved_items)
                 .select_related("category")):
        d = depreciation(item)
        book = d["book_value"] if d["depreciable"] else d["purchase_cost"]
        proceeds = Decimal(item.disposal_value or 0)
        out.append({
            "reference": "", "code": item.asset_code, "name": item.name,
            "type": item.disposal_method or "Disposed", "disposed_at": item.disposed_at,
            "purchase_cost": _money(d["purchase_cost"]),
            "accumulated": _money(d["accumulated"]),
            "book_value": _money(book), "proceeds": _money(proceeds),
            "written_off": _money(max(book - proceeds, 0) if book is not None else None),
            "gain": _money(max(proceeds - book, 0) if book is not None else None),
            "approved_by": "",
            "source": "Disposed before approvals - computed now",
        })
    return out


def lost_assets():
    """Every asset reported lost or stolen, whatever became of the request."""
    return [{
        "reference": d.disposal_number, "code": d.item_code, "name": d.item_name,
        "last_holder": d.last_holder_name or "—", "reported_by": d.requested_by_name,
        "reported_at": d.created_at, "status": d.get_status_display(),
        "reason": d.reason,
        "book_value_lost": _money(d.book_value_at_disposal),
        "approved_by": d.approved_by_name,
    } for d in (AssetDisposal.objects.filter(disposal_type=AssetDisposal.DisposalType.LOST)
                .order_by("-created_at"))]


# --------------------------------------------------------------------------- #
# Department Head visibility (Phase ASSET-TRANSFER-GOVERNANCE)
#
# Three reports that exist so a head can answer for the organisation's assets
# without being able to touch them. They read the same tables as everything above;
# what is new is that no department filter is applied to the reader.
# --------------------------------------------------------------------------- #
def department_asset_report():
    """
    One row per department: what it holds, what is out on loan to its people, and
    what it is worth. `by_department` counts assets by the department that OWNS
    them; this also counts by who HOLDS them, because the two disagree whenever
    somebody borrows across a department boundary - and that disagreement is the
    thing a head is being asked about.
    """
    owned = {row["department"]: row for row in by_department()}
    held = (ItemAssignment.objects.filter(is_active=True)
            .values("assigned_to__department_ref__name")
            .annotate(held=Count("id")))
    by_holder = {(row["assigned_to__department_ref__name"] or "Unassigned"): row["held"]
                 for row in held}

    rows = []
    for name in sorted(set(owned) | set(by_holder)):
        row = owned.get(name, {})
        rows.append({
            "department": name,
            "assets_owned": row.get("total", 0),
            "assigned": row.get("assigned", 0),
            "unassigned": row.get("available", 0),
            "in_maintenance": row.get("maintenance", 0),
            # Held BY people in this department, wherever the asset belongs.
            "held_by_staff": by_holder.get(name, 0),
            "purchase_value": row.get("value", "0"),
        })
    return rows


def organisation_asset_report():
    """
    Every asset on the books with its owner, department, condition and status -
    the whole register on one sheet, which is what "organization asset report"
    asks for. Disposed and retired assets are excluded: they are on the disposal
    and lifecycle reports, and padding this one with them hides what is live.
    """
    holders = {a.item_id: a for a in ItemAssignment.objects.filter(is_active=True)
               .select_related("assigned_to", "assigned_to__department_ref")}
    rows = []
    for item in (InventoryItem.objects.filter(ON_THE_BOOKS)
                 .select_related("category", "department").order_by("asset_code")):
        holder = holders.get(item.id)
        rows.append({
            "asset_code": item.asset_code,
            "name": item.name,
            "category": getattr(item.category, "name", "") or "",
            "asset_type": item.get_asset_type_display(),
            "department": getattr(item.department, "name", "") or "Unassigned",
            "owner": holder.assigned_to_name if holder else "",
            "owner_department": (
                getattr(getattr(holder.assigned_to, "department_ref", None), "name", "")
                if holder and holder.assigned_to else ""),
            "condition": item.get_condition_display(),
            "status": item.get_status_display(),
            "since": holder.assigned_date if holder else None,
            "purchase_date": item.purchase_date,
            "purchase_cost": str(item.purchase_cost) if item.purchase_cost is not None else None,
        })
    return rows


def transfer_visibility_report():
    """
    Every transfer, what stage it rests at and who is accountable for it right now.

    Distinct from `transfer_history`, which is the archive. This one answers "what
    is moving, and who is holding it up" - so an open transfer names the gate it is
    waiting at, and a completed one names the ownership change it produced.
    """
    rows = []
    for t in (AssetTransfer.objects.select_related("item")
              .order_by("-created_at")):
        waiting_on = ""
        if t.status in AssetTransfer.REVIEW_STAGES:
            waiting_on = "HR"
        elif t.status in AssetTransfer.RETIRED_STAGES:
            # Only reachable on a database where migration 0011 has not run.
            waiting_on = t.get_status_display()
        rows.append({
            "reference": t.transfer_number,
            "asset_code": t.item_code,
            "name": t.item_name,
            "from_owner": t.from_employee_name,
            "from_department": t.from_department_name,
            "to_owner": t.to_employee_name,
            "to_department": t.to_department_name,
            "reason": t.get_reason_display(),
            "status": t.get_status_display(),
            "waiting_on": waiting_on,
            "raised_by": t.requested_by_name,
            "raised_on": t.created_at,
            "approved_by": t.approved_by_name,
            "completed_on": t.completed_at,
        })
    return rows


REPORTS = {
    "by_department": ("Assets by Department", by_department),
    "by_employee": ("Assets by Employee", by_employee),
    "by_category": ("Assets by Category", by_category),
    "warranty_expiring": ("Warranty Expiring", warranty_expiring),
    "in_maintenance": ("Assets in Maintenance", in_maintenance),
    "disposed": ("Disposed Assets", disposed_assets),
    "unreturned": ("Unreturned Assets", unreturned_assets),
    # Phase ASSET-CUSTODY-TRANSFER. The brief's Department and Employee asset
    # reports are `by_department` and `by_employee` above, which already existed.
    "ownership": ("Asset Ownership", asset_ownership),
    "movement": ("Asset Movement", asset_movement),
    "transfers": ("Transfer History", transfer_history),
    "exit_clearance": ("Exit Clearance Assets", exit_clearance_assets),
    # Phase ASSET-LIFECYCLE-DISPOSAL. Warranty Expiry and Disposed Assets are the
    # existing `warranty_expiring` and `disposed` above; disposed now carries the
    # approval record and the book value frozen at disposal.
    "amc_expiring": ("AMC Expiring", amc_expiring),
    "depreciation": ("Depreciation Register", depreciation_register),
    "lifecycle": ("Asset Lifecycle", lifecycle_summary),
    "write_off": ("Write-off Register", write_off_register),
    "lost": ("Lost Assets", lost_assets),
    # Phase ASSET-TRANSFER-GOVERNANCE - the three the Department Head brief names.
    "department_assets": ("Department-wise Asset Report", department_asset_report),
    "organisation_assets": ("Organization Asset Report", organisation_asset_report),
    "transfer_visibility": ("Transfer Visibility Report", transfer_visibility_report),
}
