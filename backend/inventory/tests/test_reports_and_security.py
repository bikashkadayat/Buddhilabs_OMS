"""
Phase 70 - dashboard, reports, QR, employee profile and RBAC.

The security probes are the point of this file as much as the happy paths. Phase 70
adds two new roles and widens what several people can do, and the questions worth
asking are the negative ones: what must a plain employee NOT see, and does the
dashboard leak the register to somebody who cannot open it.
"""
import datetime

import pytest
from django.utils import timezone

from inventory.models import InventoryItem, TakeOutRequest

Status = InventoryItem.Status


@pytest.fixture
def stocked_fleet(db, eng, ops):
    """A small fleet spread across every state the dashboard counts."""
    def make(code, name, status, **extra):
        return InventoryItem.objects.create(
            asset_code=code, name=name, status=status, department=eng, **extra)

    today = timezone.localdate()
    return {
        "available": make("NIF-INV-F001", "Spare Laptop", Status.AVAILABLE,
                          purchase_cost="120000.00"),
        "available_two": make("NIF-INV-F002", "Spare Monitor", Status.AVAILABLE),
        "assigned": make("NIF-INV-F003", "Issued Laptop", Status.ASSIGNED),
        "maintenance": make("NIF-INV-F004", "Broken Printer", Status.MAINTENANCE),
        "out": make("NIF-INV-F005", "Field Camera", Status.OUT),
        "retired": make("NIF-INV-F006", "Old Desktop", Status.RETIRED),
        "disposed": make("NIF-INV-F007", "Scrapped Scanner", Status.DISPOSED),
        "on_order": make("NIF-INV-F008", "New Projector", Status.PROCUREMENT),
        "expiring": make("NIF-INV-F009", "Warranty Laptop", Status.AVAILABLE,
                         warranty_expiry=today + datetime.timedelta(days=20)),
        "expired": make("NIF-INV-F010", "Lapsed Laptop", Status.AVAILABLE,
                        warranty_expiry=today - datetime.timedelta(days=5)),
    }


# ===========================================================================
# 70.9 / 70.14 - the dashboard
# ===========================================================================
@pytest.mark.django_db
def test_the_dashboard_counts_every_card_the_brief_names(auth, officer,
                                                          stocked_fleet):
    response = auth(officer).get("/api/v1/inventory/dashboard/")
    assert response.status_code == 200, response.data
    assert response.data["scope"] == "organisation"
    counts = response.data["counts"]

    for card in ("total_assets", "assigned", "available", "maintenance",
                 "taken_out", "pending_requests", "overdue_returns", "disposed"):
        assert card in counts, card

    assert counts["assigned"] == 1
    assert counts["maintenance"] == 1
    assert counts["taken_out"] == 1
    assert counts["disposed"] == 1
    assert counts["retired"] == 1
    assert counts["on_order"] == 1


@pytest.mark.django_db
def test_total_assets_excludes_what_has_left_the_organisation(auth, officer,
                                                               stocked_fleet):
    """
    An organisation's asset count is what it HOLDS, not what it has ever held.
    Disposed is reported separately rather than folded in, so neither question is
    lost.
    """
    counts = auth(officer).get("/api/v1/inventory/dashboard/").data["counts"]
    assert counts["disposed"] == 1
    assert counts["total_assets"] == InventoryItem.objects.exclude(
        status__in=[Status.DISPOSED, Status.ARCHIVED]).count()


@pytest.mark.django_db
def test_the_warranty_count_and_the_warranty_report_agree(auth, officer,
                                                           stocked_fleet):
    """
    One definition of "expiring", used by the tile and by the report it links to.
    Two definitions is how the minute module ended up reporting eleven states for
    six people.
    """
    api = auth(officer)
    counts = api.get("/api/v1/inventory/dashboard/").data["counts"]
    report = api.get("/api/v1/inventory/reports/warranty_expiring/").data["rows"]
    expiring = [row for row in report if row["state"] == "expiring"]
    assert counts["warranty_expiring"] == len(expiring)
    # The report also carries the already-expired one, because "what am I exposed
    # on" is the question somebody asks after something breaks.
    assert any(row["state"] == "expired" for row in report)


@pytest.mark.django_db
def test_an_employee_gets_their_own_picture_rather_than_a_locked_door(
        auth, employee, officer, stock_item):
    """
    The dashboard is the module's front door. A 403 for most of the organisation
    is a worse answer than a smaller room.
    """
    from inventory.services import assign_item
    assign_item(stock_item.id, employee, officer)

    response = auth(employee).get("/api/v1/inventory/dashboard/")
    assert response.status_code == 200
    assert response.data["scope"] == "self"
    assert "counts" not in response.data, "an employee must not get the register"
    assert len(response.data["assigned"]) == 1
    assert response.data["assigned"][0]["code"] == stock_item.asset_code


@pytest.mark.django_db
def test_low_stock_counts_only_what_can_actually_be_issued(auth, officer, eng,
                                                            laptop_category):
    """
    A category with twenty assets all of them assigned has nothing to issue.
    Reporting it as well stocked is how a request queue silently builds up.
    """
    for index in range(5):
        InventoryItem.objects.create(
            asset_code=f"NIF-INV-L{index:03d}", name=f"Laptop {index}",
            category=laptop_category, department=eng, status=Status.ASSIGNED)
    InventoryItem.objects.create(
        asset_code="NIF-INV-L900", name="Last free laptop",
        category=laptop_category, department=eng, status=Status.AVAILABLE)

    low = auth(officer).get("/api/v1/inventory/dashboard/").data["low_stock"]
    rows = {row["category"]: row for row in low}
    assert "Laptop" in rows, "five assigned and one free is low stock"
    assert rows["Laptop"]["available"] == 1
    assert rows["Laptop"]["total"] == 6


# ===========================================================================
# 70.13 - the seven reports
# ===========================================================================
@pytest.mark.django_db
@pytest.mark.parametrize("name", [
    "by_department", "by_employee", "by_category", "warranty_expiring",
    "in_maintenance", "disposed", "unreturned",
])
def test_every_named_report_answers(auth, officer, stocked_fleet, name):
    response = auth(officer).get(f"/api/v1/inventory/reports/{name}/")
    assert response.status_code == 200, (name, response.data)
    assert response.data["report"] == name
    assert isinstance(response.data["rows"], list)
    assert response.data["label"]


@pytest.mark.django_db
def test_an_unknown_report_lists_what_is_available(auth, officer):
    response = auth(officer).get("/api/v1/inventory/reports/made_up/")
    assert response.status_code == 404
    assert "by_department" in response.data["available"]


@pytest.mark.django_db
def test_a_plain_employee_cannot_read_organisation_wide_reports(auth, employee):
    """Every one of them is organisation-wide by definition."""
    for name in ("by_department", "by_employee", "unreturned"):
        refused = auth(employee).get(f"/api/v1/inventory/reports/{name}/")
        assert refused.status_code == 403, name


@pytest.mark.django_db
def test_the_unreturned_report_finds_both_kinds_of_overdue(
        auth, officer, employee, other_employee, stock_item, eng, today):
    """
    A take-out past its date, AND an asset still held by somebody whose account is
    deactivated. The second is the one nobody thinks to look for, and it is how
    assets quietly disappear when people leave.
    """
    from inventory.services import assign_item

    overdue = InventoryItem.objects.create(
        asset_code="NIF-INV-OD1", name="Overdue Camera", department=eng,
        status=Status.OUT)
    TakeOutRequest.objects.create(
        reference="NIF-OUT-T-0001", item=overdue, item_code=overdue.asset_code,
        item_name=overdue.name, requested_by=employee,
        requested_by_name=employee.get_full_name(),
        reason="Field visit", expected_out_date=today - datetime.timedelta(days=10),
        expected_return_date=today - datetime.timedelta(days=3),
        status=TakeOutRequest.Status.APPROVED)

    assign_item(stock_item.id, other_employee, officer)
    other_employee.is_active = False
    other_employee.save(update_fields=["is_active"])

    rows = auth(officer).get(
        "/api/v1/inventory/reports/unreturned/").data["rows"]
    kinds = {row["kind"] for row in rows}
    assert "takeout" in kinds
    assert "left_organisation" in kinds
    takeout = next(r for r in rows if r["kind"] == "takeout")
    assert takeout["days_overdue"] == 3


@pytest.mark.django_db
def test_the_department_report_labels_unassigned_assets_rather_than_leaving_a_blank(
        auth, officer):
    InventoryItem.objects.create(
        asset_code="NIF-INV-ND1", name="Orphan Chair", department=None)
    rows = auth(officer).get(
        "/api/v1/inventory/reports/by_department/").data["rows"]
    assert any(row["department"] == "Unassigned" for row in rows), (
        "an empty cell reads as a bug; 'no department' is an answer")


@pytest.mark.django_db
def test_the_employee_report_lists_who_holds_what(auth, officer, employee,
                                                   stock_item):
    from inventory.services import assign_item
    assign_item(stock_item.id, employee, officer)

    rows = auth(officer).get(
        "/api/v1/inventory/reports/by_employee/").data["rows"]
    holder = next(r for r in rows if r["employee"] == employee.get_full_name())
    assert holder["total"] == 1
    assert holder["assets"][0]["code"] == stock_item.asset_code


# ===========================================================================
# 70.12 - the employee asset profile
# ===========================================================================
@pytest.mark.django_db
def test_an_employee_sees_their_own_profile(auth, employee, officer, stock_item):
    from inventory.services import assign_item
    assign_item(stock_item.id, employee, officer)

    response = auth(employee).get("/api/v1/inventory/profile/")
    assert response.status_code == 200
    assert response.data["employee"]["name"] == employee.get_full_name()
    assert len(response.data["assigned"]) == 1
    for block in ("history", "take_outs", "requests", "returns"):
        assert block in response.data, block


@pytest.mark.django_db
def test_an_employee_cannot_read_somebody_elses_profile(auth, employee,
                                                         other_employee):
    refused = auth(employee).get(
        f"/api/v1/inventory/profile/{other_employee.id}/")
    assert refused.status_code == 403


@pytest.mark.django_db
def test_an_officer_can_read_anybodys_profile(auth, officer, employee):
    """The question when somebody leaves is "what do they have"."""
    allowed = auth(officer).get(f"/api/v1/inventory/profile/{employee.id}/")
    assert allowed.status_code == 200
    assert allowed.data["employee"]["name"] == employee.get_full_name()


# ===========================================================================
# 70.10 - QR and scanning
# ===========================================================================
@pytest.mark.django_db
def test_a_qr_label_encodes_the_assets_url(auth, officer, stock_item, settings):
    settings.SITE_URL = "https://oms.nif.org.np"
    response = auth(officer).get(f"/api/v1/inventory/items/{stock_item.id}/qr/")
    assert response.status_code == 200
    assert response.data["url"].endswith(f"/inventory/items/{stock_item.id}")
    assert response.data["qr"].startswith("data:image/png;base64,")
    assert response.data["asset_code"] == stock_item.asset_code


@pytest.mark.django_db
def test_no_label_is_ever_printed_pointing_at_localhost(auth, officer, stock_item,
                                                         settings):
    """
    A QR printed onto an asset and stuck to it is the least correctable thing this
    system produces. The shared SITE_URL guard from Phase 49.5 covers it.
    """
    settings.SITE_URL = "http://localhost:8001"
    response = auth(officer).get(f"/api/v1/inventory/items/{stock_item.id}/qr/")
    assert response.status_code == 409
    assert response.data["qr"] is None
    assert "SITE_URL" in response.data["warning"]


@pytest.mark.django_db
def test_scanning_resolves_a_code_a_serial_or_an_id(auth, officer, stock_item):
    stock_item.serial_number = "SN-ABC-123"
    stock_item.save(update_fields=["serial_number"])
    api = auth(officer)
    for code in (stock_item.asset_code, "SN-ABC-123", str(stock_item.id)):
        found = api.get("/api/v1/inventory/scan/", {"code": code})
        assert found.status_code == 200, code
        assert found.data["item"]["asset_code"] == stock_item.asset_code


@pytest.mark.django_db
def test_a_scan_says_what_the_scanner_may_do_next(auth, officer, employee,
                                                   stock_item):
    """So the phone screen cannot offer an action the API would refuse."""
    api = auth(officer)
    free = api.get("/api/v1/inventory/scan/", {"code": stock_item.asset_code})
    assert free.data["can_assign"] is True
    assert free.data["can_return"] is False

    from inventory.services import assign_item
    assign_item(stock_item.id, employee, officer)
    held = api.get("/api/v1/inventory/scan/", {"code": stock_item.asset_code})
    assert held.data["can_assign"] is False
    assert held.data["can_return"] is True
    assert held.data["holder"] == employee.get_full_name()


@pytest.mark.django_db
def test_an_employee_can_only_scan_what_they_hold(auth, employee, other_employee,
                                                   officer, stock_item):
    from inventory.services import assign_item
    assign_item(stock_item.id, employee, officer)

    mine = auth(employee).get("/api/v1/inventory/scan/",
                              {"code": stock_item.asset_code})
    assert mine.status_code == 200
    theirs = auth(other_employee).get("/api/v1/inventory/scan/",
                                      {"code": stock_item.asset_code})
    assert theirs.status_code == 403


@pytest.mark.django_db
def test_an_unknown_code_says_so(auth, officer):
    missing = auth(officer).get("/api/v1/inventory/scan/", {"code": "NOPE-123"})
    assert missing.status_code == 404
    blank = auth(officer).get("/api/v1/inventory/scan/")
    assert blank.status_code == 400


# ===========================================================================
# 70.15 - RBAC
# ===========================================================================
@pytest.mark.django_db
def test_the_two_new_roles_are_genuinely_new(officer, supervisor, employee, head,
                                              hr, admin):
    """
    An inventory officer is a JOB, not a rank - a plain employee in a group. A
    supervisor is an ORG-CHART fact carried by employee_type. Neither is a
    `User.role`, because that field drives permissions in every other module and
    inventory should not get a say in what somebody can do with a memo.
    """
    from inventory import roles

    assert roles.is_inventory_officer(officer) is True
    assert officer.role == "maker", "the store may be run by a junior member of staff"
    assert roles.is_supervisor(supervisor) is True
    assert supervisor.role == "maker"

    assert roles.is_inventory_officer(employee) is False
    assert roles.is_supervisor(employee) is False

    # HR and Admin keep everything they had. A Department Head does NOT: Phase
    # ASSET-TRANSFER-GOVERNANCE made them an organisation-wide reader, and the
    # write half of what `can_manage_assets` grants went with the department
    # scoping that used to contain it.
    for user in (hr, admin):
        assert roles.can_manage_assets(user) is True
    assert roles.can_manage_assets(head) is False
    assert roles.can_browse_register(head) is True, "sees everything"
    assert roles.role_summary(head)["is_read_only_viewer"] is True


@pytest.mark.django_db
def test_the_pre_existing_manager_test_was_not_silently_widened(officer,
                                                                 employee, head):
    """
    `is_manager` still means Admin / HR / Department Head. Three existing test
    files and every old endpoint depend on that; redefining it to include inventory
    officers would have been a security change disguised as a refactor.
    """
    from inventory import roles

    assert roles.is_manager(head) is True
    assert roles.is_manager(officer) is False
    assert roles.is_manager(employee) is False


@pytest.mark.django_db
def test_the_role_summary_is_what_the_ui_renders_its_menus_from(auth, officer):
    summary = auth(officer).get("/api/v1/inventory/dashboard/").data["roles"]
    assert summary["is_inventory_officer"] is True
    assert summary["can_manage_assets"] is True
    assert summary["can_view_all_assets"] is True
    assert summary["is_admin"] is False


@pytest.mark.django_db
def test_a_plain_employee_cannot_move_an_asset_through_the_lifecycle(
        auth, employee, stock_item):
    api = auth(employee)
    for verb, payload in (("receive", {}), ("stock", {}),
                          ("dispose", {"reason": "Trying to write this off."}),
                          ("archive", {})):
        refused = api.post(
            f"/api/v1/inventory/items/{stock_item.id}/lifecycle/{verb}/",
            payload, format="json")
        assert refused.status_code == 403, verb


@pytest.mark.django_db
def test_an_unauthenticated_caller_gets_nothing(api, stock_item):
    api.force_authenticate(None)
    for url in ("/api/v1/inventory/dashboard/",
                "/api/v1/inventory/requests/",
                "/api/v1/inventory/scan/?code=X",
                f"/api/v1/inventory/items/{stock_item.id}/history/"):
        assert api.get(url).status_code in (401, 403), url
