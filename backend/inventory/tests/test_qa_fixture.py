"""
Phase 70 - inventory QA evidence generator.

Walks one asset through the whole lifecycle via the real API - ordered, received,
stocked, requested, approved twice, handed over, accepted, returned, inspected,
sent for maintenance and back - then writes what the server returns plus all four
PDFs the brief names.

The point is the same one the document modules learned: a report written against a
reading of the code is an opinion, and a report written against production output
is evidence. The Phase 46 harness was fed an invented payload and certified two
charts that rendered empty.

Marked `qa` and excluded from the default run (see pytest.ini). Regenerate with:

    DATABASE_ENGINE=sqlite3 DJANGO_DEBUG=True DJANGO_SECRET_KEY=test-secret \
      .venv/bin/python -m pytest -q -p no:randomly -m qa inventory/tests/test_qa_fixture.py
"""
import json
import pathlib
from datetime import date

import pytest

from inventory.models import AssetDisposal, AssetRequest, AssetReturn, InventoryItem

OUT = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "qa-out"
FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "qa"

Status = InventoryItem.Status


def _get(api, url):
    response = api.get(url)
    assert response.status_code == 200, (url, response.status_code, response.data)
    return json.loads(json.dumps(response.data, default=str))


@pytest.mark.qa
@pytest.mark.django_db
def test_dump_the_inventory_qa_evidence(auth, employee, supervisor, officer, hr,
                                        head, admin, eng, laptop_category,
                                        settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.SITE_URL = "https://oms.nif.org.np"

    from inventory.services import seed_default_categories
    seed_default_categories()

    # A small fleet, so the dashboard and the reports have something to say.
    fleet = []
    for index, (name, status, cost) in enumerate([
        ("Dell Latitude 5540", Status.AVAILABLE, "142000.00"),
        ("Dell Latitude 5540", Status.ASSIGNED, "142000.00"),
        ("HP LaserJet Pro", Status.MAINTENANCE, "38000.00"),
        ("Epson Projector", Status.PROCUREMENT, "95000.00"),
        ("Cisco Switch 24p", Status.AVAILABLE, "76000.00"),
        ("Old Desktop Tower", Status.RETIRED, "45000.00"),
    ], start=1):
        fleet.append(InventoryItem.objects.create(
            asset_code=f"NIF-INV-Q{index:03d}", name=name, status=status,
            department=eng, category=laptop_category, purchase_cost=cost,
            brand=name.split()[0], model=name,
            serial_number=f"SN-Q{index:03d}-2026",
            location="Store room, 2nd floor"))

    # The asset this run follows all the way through.
    subject = InventoryItem.objects.create(
        asset_code="NIF-INV-Q900", name="ThinkPad T14 Gen 4",
        status=Status.PROCUREMENT, department=eng, category=laptop_category,
        brand="Lenovo", model="ThinkPad T14 Gen 4",
        serial_number="SN-Q900-2026", purchase_cost="158000.00",
        vendor="Neoteric Nepal", location="Store room, 2nd floor")

    store = auth(officer)
    assert store.post(f"/api/v1/inventory/items/{subject.id}/lifecycle/receive/",
                      {"remarks": "Delivered against PO/2026/114.",
                       "condition": "new"},
                      format="json").status_code == 200
    assert store.post(f"/api/v1/inventory/items/{subject.id}/lifecycle/stock/",
                      {"location": "Store room, 2nd floor",
                       "remarks": "Checked in, serial verified."},
                      format="json").status_code == 200

    created = auth(employee).post(
        "/api/v1/inventory/requests/",
        {"item": str(subject.id),
         "purpose": "My current machine cannot run the statistical tooling the "
                    "survey analysis needs."},
        format="json")
    assert created.status_code == 201, created.data
    request_id = created.data["id"]

    assert auth(supervisor).post(
        f"/api/v1/inventory/requests/{request_id}/supervisor/",
        {"approve": True, "remarks": "Agreed; the analysis workload justifies it."},
        format="json").status_code == 200
    assert auth(officer).post(
        f"/api/v1/inventory/requests/{request_id}/inventory/",
        {"approve": True, "remarks": "One in stock, serial verified.",
         "accessories": "Charger, sleeve, USB-C hub"},
        format="json").status_code == 200
    assert auth(officer).post(
        f"/api/v1/inventory/requests/{request_id}/handover/",
        {"condition": "new"}, format="json").status_code == 200
    assert auth(employee).post(
        f"/api/v1/inventory/requests/{request_id}/accept/",
        {"remarks": "Received with charger, sleeve and hub."},
        format="json").status_code == 200

    # A maintenance round on one of the fleet, so the ticket register has content.
    broken = fleet[2]
    ticket = auth(employee).post(
        "/api/v1/inventory/maintenance/",
        {"item": str(broken.id), "priority": "high",
         "issue": "Paper jams repeatedly and the fuser makes a grinding noise."},
        format="json")
    assert ticket.status_code == 201, ticket.data
    ticket_id = ticket.data["id"]
    auth(officer).post(f"/api/v1/inventory/maintenance/{ticket_id}/assign/",
                       {"vendor": "TechCare Pvt Ltd"}, format="json")

    # A return that ends in a DISPUTED condition, so the return form prints the
    # case it exists for.
    returning = fleet[1]
    from inventory.services import assign_item
    assign_item(returning.id, employee, officer)
    started = auth(employee).post(
        "/api/v1/inventory/returns/",
        {"item": str(returning.id), "reason": "Moving to a desktop setup.",
         "declared_condition": "good",
         "declared_remarks": "Working normally as far as I could tell."},
        format="json")
    assert started.status_code == 201, started.data
    return_id = started.data["id"]
    auth(officer).post(f"/api/v1/inventory/returns/{return_id}/verify/", {},
                       format="json")
    auth(officer).post(
        f"/api/v1/inventory/returns/{return_id}/inspect/",
        {"condition": "fair",
         "remarks": "Casing scuffed and one hinge is loose; still serviceable."},
        format="json")
    auth(officer).post(f"/api/v1/inventory/returns/{return_id}/accept/", {},
                       format="json")

    # Phase ASSET-LIFECYCLE-DISPOSAL: one asset taken off the books the proper
    # way, plus one still in review, so the Disposal page has both a decided
    # record and a live queue to draw.
    scrapped = InventoryItem.objects.create(
        asset_code="NIF-INV-Q901", name="Dell OptiPlex 3070",
        status=Status.AVAILABLE, department=eng, category=laptop_category,
        brand="Dell", model="OptiPlex 3070", serial_number="SN-Q901-2019",
        purchase_date=date(2020, 4, 12), purchase_cost="88000.00",
        salvage_value="4000.00", useful_life_months=60,
        location="Store room, 2nd floor")
    in_review = InventoryItem.objects.create(
        asset_code="NIF-INV-Q902", name="Canon imageRUNNER 2425",
        status=Status.AVAILABLE, department=eng, category=laptop_category,
        brand="Canon", model="imageRUNNER 2425", serial_number="SN-Q902-2021",
        purchase_date=date(2021, 11, 3), purchase_cost="210000.00",
        useful_life_months=84, amc_provider="Neoteric Nepal", amc_contract_number="AMC-2026-041",
        amc_start=date(2026, 1, 1), amc_end=date(2026, 12, 31), amc_cost="18000.00",
        location="Ground floor, reception")

    def raise_disposal(item, disposal_type, reason, proceeds=None):
        body = {"item": str(item.id), "disposal_type": disposal_type, "reason": reason}
        if proceeds is not None:
            body["expected_proceeds"] = proceeds
        made = auth(officer).post("/api/v1/inventory/disposals/", body, format="json")
        assert made.status_code == 201, made.data
        did = made.data["id"]
        assert auth(officer).post(
            f"/api/v1/inventory/disposals/{did}/submit/", {},
            format="json").status_code == 200
        return did

    disposed_id = raise_disposal(
        scrapped, "sale", "Six years old and superseded; a buyer has offered for it.",
        "6000.00")
    # The first gate is the head of the OWNING department - an HR approver is
    # refused there, which is the point of the gate.
    for approver, remark in ((head, "The department has no further use for it."),
                             (admin, "Approved; proceed with the sale.")):
        assert auth(approver).post(
            f"/api/v1/inventory/disposals/{disposed_id}/approve/",
            {"remarks": remark}, format="json").status_code == 200, approver

    open_id = raise_disposal(
        in_review, "scrap",
        "The fuser has failed twice this year and spares are no longer made.")

    api = auth(officer)
    payload = {
        "_generated_by": "backend/inventory/tests/test_qa_fixture.py (Phase 70)",
        "asset_id": str(subject.id),
        "request_id": request_id,
        "return_id": return_id,
        "disposal_id": disposed_id,
        "open_disposal_id": open_id,
        "ticket_id": ticket_id,
        "dashboard": _get(api, "/api/v1/inventory/dashboard/"),
        "history": _get(api, f"/api/v1/inventory/items/{subject.id}/history/"),
        "qr": _get(api, f"/api/v1/inventory/items/{subject.id}/qr/"),
        "requests": _get(api, "/api/v1/inventory/requests/"),
        "returns": _get(api, "/api/v1/inventory/returns/"),
        "maintenance": _get(api, "/api/v1/inventory/maintenance/"),
        "disposals": _get(api, "/api/v1/inventory/disposals/"),
        "disposal_detail": _get(api, f"/api/v1/inventory/disposals/{disposed_id}/"),
        "disposal_options": _get(api, "/api/v1/inventory/disposal-options/"),
        # Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD.
        "visibility_options": _get(api, "/api/v1/inventory/visibility-options/"),
        "lifecycle_summary": _get(
            api, f"/api/v1/inventory/items/{scrapped.id}/lifecycle-summary/"),
        "profile": _get(api, f"/api/v1/inventory/profile/{employee.id}/"),
        "reports": {
            name: _get(api, f"/api/v1/inventory/reports/{name}/")
            for name in ("by_department", "by_employee", "by_category",
                         "warranty_expiring", "in_maintenance", "disposed",
                         "unreturned",
                         # Phase ASSET-LIFECYCLE-DISPOSAL.
                         "depreciation", "lifecycle", "write_off", "lost",
                         "amc_expiring",
                         # Phase ASSET-TRANSFER-GOVERNANCE / VISIBILITY.
                         "department_assets", "ownership", "transfers",
                         "exit_clearance")
        },
    }

    # Guard rails: an empty register screenshots as a clean page and proves nothing.
    assert payload["dashboard"]["counts"]["total_assets"] >= 7
    assert len(payload["history"]) >= 6, "the lifecycle must have left a trail"
    assert payload["qr"]["qr"].startswith("data:image/png;base64,")
    assert AssetRequest.objects.get(pk=request_id).status == (
        AssetRequest.Status.ACCEPTED)
    assert AssetReturn.objects.get(pk=return_id).condition_disputed is True
    # A disposal fixture with no decided record would screenshot an empty page.
    assert AssetDisposal.objects.get(pk=disposed_id).status == AssetDisposal.Status.DISPOSED
    assert len(payload["disposals"]) >= 2
    assert payload["lifecycle_summary"]["depreciation"]["depreciable"] is True

    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "inventory-fixtures.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n")

    # All four PDFs the brief names.
    OUT.mkdir(parents=True, exist_ok=True)

    receipt = api.get(
        f"/api/v1/inventory/items/{subject.id}/assignment-receipt/")
    assert receipt.status_code == 200, receipt.status_code
    (OUT / "inventory-assignment-form.pdf").write_bytes(receipt.content)

    form = api.get(f"/api/v1/inventory/returns/{return_id}/form/")
    assert form.status_code == 200, form.status_code
    (OUT / "inventory-return-form.pdf").write_bytes(form.content)

    report = api.get("/api/v1/inventory/reports/by_department/?download=pdf")
    assert report.status_code == 200
    (OUT / "inventory-report.pdf").write_bytes(report.content)

    # The take-out gate pass, from the pre-existing workflow.
    from inventory.services import create_takeout
    from django.utils import timezone
    import datetime

    spare = fleet[4]
    takeout = create_takeout(
        item=spare, requester=employee, purpose="outside",
        reason="Network survey at the branch office.",
        expected_out_date=timezone.localdate(),
        expected_return_date=timezone.localdate() + datetime.timedelta(days=3))
    auth(hr).post(f"/api/v1/inventory/takeouts/{takeout.id}/approve/",
                  {"remarks": "Approved for the branch survey."}, format="json")
    gate = api.get(f"/api/v1/inventory/takeouts/{takeout.id}/gate-pass/")
    assert gate.status_code == 200, gate.status_code
    (OUT / "inventory-gate-pass.pdf").write_bytes(gate.content)
