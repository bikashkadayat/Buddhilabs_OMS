"""
Phase 70.4 - the asset master fields, over the API rather than over the model.

A field that exists on the model but not on the serializer is a field the
organisation does not have: nobody can set it, nothing displays it, and the
migration that added it only made the table wider. So every assertion here goes
through the endpoint an officer actually calls.

The upload tests matter more than they look. `photo` and `document` were added as
plain Image/FileFields, and every other upload in this codebase is checked against
`config.uploads.validate_attachment` - which reads the leading BYTES rather than
trusting the extension. An asset photo field that accepts anything named `.png` is
a file-upload hole with an inventory label on it.
"""
import base64

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from inventory.models import InventoryItem

pytestmark = pytest.mark.django_db

ITEMS = "/api/v1/inventory/items/"

# Real leading bytes. libmagic reads these; the extension is what it compares
# them against, so a mismatch is what the validator is for.
#
# The PNG is a genuine 1x1 image rather than a header followed by padding,
# because ImageField runs Pillow over it as a SECOND check after the magic-byte
# validator - two independent layers, both of which a real photo passes.
PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    b"IQAAAABJRU5ErkJggg==")
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 64
SCRIPT = b"<?php system($_GET['c']); ?>" + b"\n" * 64


def _upload(name, payload, content_type):
    return SimpleUploadedFile(name, payload, content_type=content_type)


class TestTheMasterFieldsAreReachable:
    def test_an_officer_can_record_where_an_asset_physically_is(
            self, auth, officer, stock_item):
        """
        Location is not department. A laptop owned by Finance can sit in the server
        room, and an audit that can only answer "whose is it" cannot answer "where
        do I go to look at it".
        """
        client = auth(officer)
        res = client.patch(f"{ITEMS}{stock_item.id}/",
                           {"location": "Head Office, 3rd floor"}, format="json")
        assert res.status_code == 200, res.data
        assert res.data["location"] == "Head Office, 3rd floor"
        stock_item.refresh_from_db()
        assert stock_item.location == "Head Office, 3rd floor"

    def test_a_warranty_reads_as_a_period_rather_than_a_deadline(
            self, auth, officer, stock_item):
        client = auth(officer)
        res = client.patch(
            f"{ITEMS}{stock_item.id}/",
            {"warranty_start": "2026-01-01", "warranty_expiry": "2029-01-01"},
            format="json")
        assert res.status_code == 200, res.data
        assert res.data["warranty_start"] == "2026-01-01"
        # Both ends in BS as well, because that is the calendar the forms use.
        assert res.data["warranty_start_bs"]
        assert res.data["warranty_expiry_bs"]

    def test_a_warranty_cannot_end_before_it_starts(
            self, auth, officer, stock_item):
        client = auth(officer)
        res = client.patch(
            f"{ITEMS}{stock_item.id}/",
            {"warranty_start": "2029-01-01", "warranty_expiry": "2026-01-01"},
            format="json")
        assert res.status_code == 400
        assert "warranty_expiry" in res.data

    def test_the_warranty_state_is_derived_rather_than_stored(
            self, auth, officer, stock_item, today):
        """
        Derived, so it is right the instant the date passes. A stored flag would
        need something to run overnight, and the day nothing runs is the day the
        register says an expired warranty is live.
        """
        from datetime import timedelta

        client = auth(officer)
        for expiry, expected in [
            (today - timedelta(days=1), "expired"),
            (today + timedelta(days=10), "expiring"),
            (today + timedelta(days=900), "active"),
        ]:
            stock_item.warranty_expiry = expiry
            stock_item.save(update_fields=["warranty_expiry"])
            res = client.get(f"{ITEMS}{stock_item.id}/")
            assert res.data["warranty_state"] == expected, expiry


class TestUploads:
    def test_an_invoice_can_be_attached_to_the_asset_it_paid_for(
            self, auth, officer, stock_item):
        client = auth(officer)
        res = client.patch(
            f"{ITEMS}{stock_item.id}/",
            {"document": _upload("invoice.pdf", PDF, "application/pdf"),
             "document_name": "Invoice 2026-114"},
            format="multipart")
        assert res.status_code == 200, res.data
        assert res.data["document"]
        assert res.data["document_name"] == "Invoice 2026-114"

    def test_a_photo_can_be_attached(self, auth, officer, stock_item):
        client = auth(officer)
        res = client.patch(f"{ITEMS}{stock_item.id}/",
                           {"photo": _upload("front.png", PNG, "image/png")},
                           format="multipart")
        assert res.status_code == 200, res.data
        assert res.data["photo"]

    def test_a_script_wearing_a_png_extension_is_refused(
            self, auth, officer, stock_item):
        """
        The bytes are checked, not the name. This is the whole point of routing
        these fields through the shared validator rather than trusting Django's
        ImageField, and it is the case that a `.png` extension check passes.
        """
        client = auth(officer)
        res = client.patch(f"{ITEMS}{stock_item.id}/",
                           {"photo": _upload("evil.png", SCRIPT, "image/png")},
                           format="multipart")
        assert res.status_code == 400, res.data
        stock_item.refresh_from_db()
        assert not stock_item.photo

    def test_a_document_cannot_be_an_executable_named_pdf(
            self, auth, officer, stock_item):
        client = auth(officer)
        res = client.patch(f"{ITEMS}{stock_item.id}/",
                           {"document": _upload("invoice.pdf", SCRIPT,
                                                "application/pdf")},
                           format="multipart")
        assert res.status_code == 400, res.data

    def test_a_pdf_is_not_accepted_as_an_asset_photo(
            self, auth, officer, stock_item):
        """
        The photo field is narrower than the shared default on purpose - the UI
        renders it in an <img>, and a PDF there is a broken card rather than a
        picture of the asset.
        """
        client = auth(officer)
        res = client.patch(f"{ITEMS}{stock_item.id}/",
                           {"photo": _upload("scan.pdf", PDF, "application/pdf")},
                           format="multipart")
        assert res.status_code == 400, res.data


class TestDisposalCannotBeForged:
    def test_disposal_fields_are_readable_but_not_writable(
            self, auth, officer, stock_item):
        """
        Disposing of an asset is a lifecycle transition that writes an event naming
        who did it. A PATCH that could set `disposed_at` directly would be a way to
        write an asset off with nothing in the history saying anybody did.
        """
        client = auth(officer)
        res = client.patch(
            f"{ITEMS}{stock_item.id}/",
            {"disposed_at": "2026-01-01T00:00:00Z",
             "disposal_method": "sold", "disposal_reason": "made this up"},
            format="json")
        assert res.status_code == 200, res.data
        stock_item.refresh_from_db()
        assert stock_item.disposed_at is None
        assert stock_item.disposal_method == ""
        assert stock_item.status != InventoryItem.Status.DISPOSED

    def test_status_cannot_be_edited_directly_on_an_existing_asset(
            self, auth, officer, stock_item):
        """
        The module's central claim is that nothing changes an asset's status
        without writing an event. A PATCH that could set `status` would be a way
        to retire an asset, or move it out of somebody's custody, with nothing in
        the history saying anybody did it.
        """
        client = auth(officer)
        res = client.patch(f"{ITEMS}{stock_item.id}/",
                           {"status": InventoryItem.Status.RETIRED}, format="json")
        assert res.status_code == 400, res.data
        assert "lifecycle" in str(res.data["status"]).lower()
        stock_item.refresh_from_db()
        assert stock_item.status == InventoryItem.Status.AVAILABLE

    def test_resending_the_current_status_is_not_an_error(
            self, auth, officer, stock_item):
        """
        A form that round-trips every field it loaded would otherwise be unable to
        save a location change - the rule is about CHANGING status, not about
        mentioning it.
        """
        client = auth(officer)
        res = client.patch(
            f"{ITEMS}{stock_item.id}/",
            {"status": stock_item.status, "location": "Store room"}, format="json")
        assert res.status_code == 200, res.data
        assert res.data["location"] == "Store room"

    def test_an_asset_can_still_be_registered_at_whatever_status_it_arrives_in(
            self, auth, officer, laptop_category, eng):
        """Registering is a choice; editing afterwards is not."""
        client = auth(officer)
        res = client.post(ITEMS, {
            "name": "Ordered Laptop", "category": str(laptop_category.id),
            "department": str(eng.id), "asset_type": "it",
            "status": InventoryItem.Status.PROCUREMENT,
        }, format="json")
        assert res.status_code == 201, res.data
        assert res.data["status"] == InventoryItem.Status.PROCUREMENT

    def test_an_approved_disposal_is_the_only_way_and_it_records_who(
            self, auth, officer, head, admin, stock_item):
        """
        Phase ASSET-LIFECYCLE-DISPOSAL: the one-click verb that used to be "the only
        way" now refuses - one person clicking it could skip both approvals. The way
        is a request approved by a department head and an administrator, and the
        history names the administrator whose approval disposed of the asset.
        """
        from .conftest import dispose_via_workflow

        client = auth(officer)
        bypass = client.post(
            f"{ITEMS}{stock_item.id}/lifecycle/dispose/",
            {"method": "sold", "reason": "End of useful life, sold at auction",
             "value": "5000.00"}, format="json")
        assert bypass.status_code == 409
        stock_item.refresh_from_db()
        assert stock_item.status != InventoryItem.Status.DISPOSED

        dispose_via_workflow(stock_item, officer, head, admin, disposal_type="sale",
                             reason="End of useful life, sold at auction",
                             proceeds="5000.00")
        stock_item.refresh_from_db()
        assert stock_item.status == InventoryItem.Status.DISPOSED
        assert stock_item.disposed_at is not None

        history = client.get(f"{ITEMS}{stock_item.id}/history/").data
        disposal = [row for row in history if row["to_status"] == "disposed"]
        assert len(disposal) == 1
        assert admin.get_full_name() in disposal[0]["actor"]
