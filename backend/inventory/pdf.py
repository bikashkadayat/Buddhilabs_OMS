"""Take-Out Gate Pass PDF — reuses the shared NIF letterhead (common_context +
base_pdf.html: larger logo, org contact block, QR verification)."""
from config.nepali_dates import to_bs
from documents.pdf import common_context, render_pdf


def render_gate_pass(req):
    ctx = common_context(req.reference)
    ctx.update({
        "reference": req.reference,
        "item_code": req.item_code,
        "item_name": req.item_name,
        "holder": req.requested_by_name,
        "department": req.department.name if req.department else "—",
        "purpose": req.get_purpose_display(),
        "reason": req.reason or "—",
        "out_ad": str(req.expected_out_date), "out_bs": to_bs(req.expected_out_date) or "—",
        "return_ad": str(req.expected_return_date), "return_bs": to_bs(req.expected_return_date) or "—",
        "approver": req.approver_name or "—",
        "approver_remarks": req.approver_remarks or "",
        "status_label": req.get_status_display(),
        "action_ad": req.action_date.date().isoformat() if req.action_date else "—",
        "action_bs": to_bs(req.action_date.date()) if req.action_date else "—",
    })
    return render_pdf("pdf/gate_pass.html", ctx)


def render_assignment_receipt(item, assignment):
    """Asset handover / assignment receipt on the shared NIF letterhead."""
    ctx = common_context(item.asset_code)
    specs = []
    for label, val in [
        ("Brand", item.brand), ("Model", item.model), ("CPU", item.cpu), ("RAM", item.ram),
        ("Storage", " ".join(x for x in [item.storage_size, item.storage_type] if x)),
        ("GPU", item.gpu), ("Screen", item.screen_size), ("OS", item.os),
        ("Serial No.", item.serial_number), ("MAC", item.mac_address),
    ]:
        if val:
            specs.append({"label": label, "value": val})
    ctx.update({
        "asset_code": item.asset_code, "item_name": item.name,
        "asset_type": item.get_asset_type_display(),
        "category": item.category.name if item.category else "—",
        "specs": specs, "accessories": assignment.accessories or item.accessories or "—",
        "holder": assignment.assigned_to_name,
        "assigned_by": assignment.assigned_by_name or "—",
        "condition": assignment.get_handover_condition_display() if assignment.handover_condition else "—",
        "assigned_ad": str(assignment.assigned_date) if assignment.assigned_date else "—",
        "assigned_bs": to_bs(assignment.assigned_date) if assignment.assigned_date else "—",
        "is_handover": assignment.is_handover,
        "remarks": assignment.note or "",
    })
    return render_pdf("pdf/assignment_receipt.html", ctx)


def render_return_form(record):
    """
    Asset Return Form (Phase 70.16).

    Prints BOTH conditions - what the employee declared and what the officer found
    - side by side, and says so when they differ. A return form that printed only
    one of them would be exactly the document somebody produces to argue about the
    other, which is the moment it needs to carry both.
    """
    ctx = common_context(record.reference)
    ctx.update({
        "reference": record.reference,
        "item_code": record.item_code,
        "item_name": record.item_name,
        "holder": record.returned_by_name,
        "reason": record.reason or "—",
        "status_label": record.get_status_display(),
        "declared_condition": (record.get_declared_condition_display()
                               if record.declared_condition else "—"),
        "declared_remarks": record.declared_remarks or "—",
        "inspected_condition": (record.get_inspected_condition_display()
                                if record.inspected_condition else "—"),
        "inspection_remarks": record.inspection_remarks or "—",
        "disputed": record.condition_disputed,
        "verified_by": record.verified_by_name or "—",
        "verified_ad": (record.verified_at.date().isoformat()
                        if record.verified_at else "—"),
        "inspected_ad": (record.inspected_at.date().isoformat()
                         if record.inspected_at else "—"),
        "returned_ad": str(record.returned_date) if record.returned_date else "—",
        "returned_bs": (to_bs(record.returned_date)
                        if record.returned_date else "—"),
        "rejection_reason": record.rejection_reason or "",
    })
    return render_pdf("pdf/return_form.html", ctx)


def render_report(name, label, rows, generated_for=""):
    """
    Any of the seven inventory reports as a PDF (Phase 70.16).

    ONE template for all seven rather than seven. The columns are derived from the
    rows the report builder returns, so adding an eighth report needs no template
    at all - and no report can be printed with columns that do not match the data
    it holds.
    """
    from django.utils import timezone

    ctx = common_context(f"REPORT-{name.upper()}")
    # Union of keys, in first-seen order, so a row with an extra field does not
    # silently lose it and the column order stays stable across runs.
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    # Nested structures (by_employee's per-person asset list) do not belong in a
    # flat table; the count beside the name carries the information.
    columns = [c for c in columns
               if not any(isinstance(row.get(c), (list, dict)) for row in rows)]

    ctx.update({
        "report_name": name,
        "report_label": label,
        "columns": [{"key": c, "label": c.replace("_", " ").title()}
                    for c in columns],
        "rows": [[row.get(c) for c in columns] for row in rows],
        "row_count": len(rows),
        "generated_for": generated_for,
        "generated_at": timezone.now(),
    })
    return render_pdf("pdf/inventory_report.html", ctx)
