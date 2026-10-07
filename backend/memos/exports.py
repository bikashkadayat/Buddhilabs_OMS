"""
Excel export for the memo archive and list views (Phase 6).

Uses openpyxl, already a production dependency via reports.report_service, so
this adds no new package. write_only mode keeps memory flat for a large archive
because rows are streamed to the sheet instead of being held as cell objects.
"""
import io

HEADERS = [
    "Memo Number", "Subject", "Type", "Status",
    "Created By", "Department", "Pending With", "Created", "Sent For Review",
    "Approved Date", "Approver", "Archived", "Last Action",
]


def _name(user):
    if user is None:
        return ""
    return user.get_full_name() or user.username


def _naive(value):
    """
    Strip tzinfo: openpyxl cannot write timezone-aware datetimes to xlsx. The
    values are already in the project timezone when localtime() is applied.
    """
    if value is None:
        return None
    from django.utils.timezone import is_aware, localtime
    return localtime(value).replace(tzinfo=None) if is_aware(value) else value


def _last_action(memo):
    """Human summary of the most recent thing that happened to the memo."""
    latest = max(
        memo.approval_steps.all(),
        key=lambda step: (step.step_order, step.acted_at),
        default=None,
    )
    if latest is None:
        return ""
    who = _name(latest.actor)
    return f"{latest.get_action_display()} by {who}" if who else latest.get_action_display()


def _approver_name(memo):
    """Who actually approved it: the completed Approver step on the matrix."""
    for step in memo.workflow_steps.all():
        if step.role_type == "approver" and step.status == "completed":
            return step.display_name
    return ""


def _pending_with(memo):
    """Who the memo is currently sitting with, for the in-flight rows."""
    for step in memo.workflow_steps.all():
        if step.status == "active":
            return f"{step.display_name} ({step.get_role_type_display()})"
    return ""


def memos_to_xlsx(queryset):
    """Return the xlsx bytes for `queryset`. Caller is responsible for scoping."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Memos")
    sheet.freeze_panes = "A2"

    # Column widths must be set before any row is streamed: in write_only mode
    # openpyxl serialises the sheet as it goes, and dimensions written after the
    # first append land after <sheetData> where they are ignored.
    from openpyxl.utils import get_column_letter
    widths = [22, 40, 30, 12, 10, 16, 22, 20, 26, 18, 18, 18, 22, 18, 34]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    from openpyxl.cell import WriteOnlyCell
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1E3A5F")
    header_cells = []
    for label in HEADERS:
        cell = WriteOnlyCell(sheet, value=label)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
        header_cells.append(cell)
    sheet.append(header_cells)

    for memo in queryset:
        sheet.append([
            memo.memo_number,
            memo.subject,
            memo.get_memo_type_display(),
            memo.get_status_display(),
            _name(memo.created_by),
            memo.resolved_department_name(),
            _pending_with(memo),
            _naive(memo.created_at),
            _naive(memo.submitted_at),
            _naive(memo.approved_at),
            _approver_name(memo),
            _naive(memo.archived_at),
            _last_action(memo),
        ])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
