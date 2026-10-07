"""Phase S6.5 Parts 1, 5 and 6: the export, and what makes it trustworthy.

The assertions here are about three different promises, and they fail for
different reasons:

  COMPLETE    every table a tenant owns is in the bundle, because the set of
              tables comes from the CI-enforced registry rather than a list
              somebody maintained by hand.
  FAITHFUL    the bundle's checksums match its bytes, the media files are
              there, and every reference between exported rows resolves.
  ISOLATED    one customer's bundle contains one customer's data, which is
              asserted against a second tenant holding the same shapes.
"""
import io
import json
import zipfile

import pytest

from tenancy import console, export as export_module
from tenancy.context import tenant_context
from tenancy.inventory import PLATFORM_GLOBAL, TENANT_SCOPED

pytestmark = pytest.mark.django_db


def _bundle(export_row):
    export_row.file.seek(0)
    return zipfile.ZipFile(io.BytesIO(export_row.file.read()))


def _manifest(export_row):
    with _bundle(export_row) as archive:
        return json.loads(archive.read("manifest.json"))


# --- COMPLETE -----------------------------------------------------------
def test_every_tenant_table_is_exported_or_excluded_with_a_reason():
    """The registry drives the export, so a new model cannot be forgotten.

    THE FAILURE THIS PREVENTS is not hypothetical, it is the normal fate of
    export code: a hand-written list of "the important tables" is correct the
    day it ships and silently incomplete from the first model added
    afterwards -- and the customer discovers which table was missing after
    they have migrated off.
    """
    covered = set(export_module._tenant_targets().values())
    excluded = set(export_module.EXCLUDED)
    assert covered | excluded == TENANT_SCOPED, (
        "these tenant models are neither exported nor excluded with a "
        f"reason: {sorted(TENANT_SCOPED - covered - excluded)}")
    for label, reason in export_module.EXCLUDED.items():
        assert reason.strip(), f"{label} is excluded with no reason given"


def test_every_platform_table_is_included_or_excluded_with_a_reason():
    """Same discipline for the platform's own records about a tenant.

    A customer exit package with no subscription history is not an answer to
    "give us everything you hold about us", and the rows that are deliberately
    withheld -- credentials, our price list -- have to be named.
    """
    included = {label for label, _ in export_module.PLATFORM_SIDE}
    included.add("tenancy.Plan")              # the referenced ones only
    excluded = set(export_module.PLATFORM_EXCLUDED)
    assert included | excluded == PLATFORM_GLOBAL, (
        "unclassified platform models: "
        f"{sorted(PLATFORM_GLOBAL - included - excluded)}")


def test_the_bundle_has_a_file_for_every_table(org, platform_user):
    export_row = console.create_export(platform_user, org)
    assert export_row.status == "ready", export_row.error

    manifest = _manifest(export_row)
    with _bundle(export_row) as archive:
        names = set(archive.namelist())

    assert manifest["totals"]["tables"] == len(TENANT_SCOPED)
    for label, entry in manifest["tables"].items():
        assert entry["files"]["json"] in names, label
        assert entry["files"]["csv"] in names, label
    assert "README.txt" in names, (
        "a bundle a customer cannot read without asking us is not portable")


def test_the_bundle_carries_the_tenants_actual_configuration(org, platform_user):
    """Not just empty files: the seeded workspace is in there."""
    export_row = console.create_export(platform_user, org)
    with _bundle(export_row) as archive:
        departments = json.loads(archive.read("data/json/leaves.Department.json"))
        leave_types = json.loads(archive.read("data/json/leaves.LeaveType.json"))

    assert {row["fields"]["code"] for row in departments} >= {"HR", "FIN"}
    assert {row["fields"]["code"] for row in leave_types} >= {
        "ANNUAL", "SICK", "UNPAID", "SPECIAL"}
    # Primary keys are preserved, which is what makes the JSON copy the one
    # that can be loaded back.
    assert all(row["pk"] for row in departments)


# --- FAITHFUL -----------------------------------------------------------
def test_every_member_checksum_matches_its_bytes(org, platform_user):
    """A manifest whose checksums are wrong is worse than none at all."""
    import hashlib

    export_row = console.create_export(platform_user, org)
    manifest = _manifest(export_row)
    with _bundle(export_row) as archive:
        for path, entry in manifest["members"].items():
            data = archive.read(path)
            assert len(data) == entry["bytes"], path
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], path


def test_the_receipt_checksum_matches_the_file_handed_over(org, platform_user):
    import hashlib

    export_row = console.create_export(platform_user, org)
    export_row.file.seek(0)
    assert hashlib.sha256(export_row.file.read()).hexdigest() == export_row.sha256
    assert export_row.size_bytes == export_row.file.size


def test_media_files_travel_with_the_rows_that_reference_them(org,
                                                               platform_user):
    """Part 5. A dataset whose document rows point at files nobody shipped is
    not portable, it is a list of broken links."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from users.models import User

    with tenant_context(org):
        member = User.objects.create_user(
            username="photo-owner", email="photo@abc.test",
            password="x-Photo-1", organization=org)
        member.profile_photo = SimpleUploadedFile(
            "face.gif",
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
            b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
            b"\x00\x02\x02D\x01\x00;",
            content_type="image/gif")
        member.save(update_fields=["profile_photo"])
        stored = member.profile_photo.name

    export_row = console.create_export(platform_user, org)
    assert export_row.media_count == 1, export_row.manifest["integrity"]

    manifest = _manifest(export_row)
    entry = manifest["media"][0]
    assert entry["path"] == stored
    assert entry["model"] == "users.User"
    with _bundle(export_row) as archive:
        assert archive.read(f"media/{stored}"), "the file itself is missing"


def test_a_file_missing_from_storage_is_reported_not_fatal(org, platform_user):
    """A row pointing at a file deleted two years ago is a fact about this
    tenant, not a fault in the export -- and refusing to export anything
    until somebody finds it would leave the customer with nothing."""
    from django.core.files.storage import default_storage
    from django.core.files.uploadedfile import SimpleUploadedFile

    from users.models import User

    with tenant_context(org):
        member = User.objects.create_user(
            username="ghost-photo", email="ghost@abc.test",
            password="x-Ghost-1", organization=org)
        member.profile_photo = SimpleUploadedFile(
            "gone.gif", b"GIF89a\x01\x00\x01\x00\x80\x00\x00",
            content_type="image/gif")
        member.save(update_fields=["profile_photo"])
        default_storage.delete(member.profile_photo.name)

    export_row = console.create_export(platform_user, org)
    assert export_row.status == "ready", export_row.error
    missing = export_row.manifest["integrity"]["missing_media_files"]
    assert len(missing) == 1 and missing[0]["model"] == "users.User"


def test_the_integrity_pass_finds_no_dangling_reference(org, platform_user):
    export_row = console.create_export(platform_user, org)
    integrity = export_row.manifest["integrity"]
    assert integrity["dangling_references"] == []
    assert integrity["cross_tenant_rows"] == []
    assert integrity["checked_tables"] == len(TENANT_SCOPED)


def test_a_dangling_reference_fails_the_export_and_writes_no_file(
        org, platform_user, monkeypatch):
    """Part 6: no partial exports.

    Simulated by withholding one table from the collected ids, which is what
    a genuinely missing table would look like to the integrity pass. The
    assertion that matters is the CONSEQUENCE: FAILED, no file, and a reason
    an operator can read -- never a downloadable bundle that cannot be
    imported anywhere.

    `leaves.LeaveType` is the table withheld because the bootstrap gives
    every tenant twelve EntitlementRule rows that point at it, so emptying it
    produces real dangling references. Withholding a table nothing references
    -- Department, in a tenant with no staff yet -- would leave the integrity
    pass with nothing to find and the test passing for the wrong reason.
    """
    real = export_module._check_integrity

    def broken(organization, targets, exported_ids):
        starved = dict(exported_ids)
        starved["leaves.LeaveType"] = set()
        return real(organization, targets, starved)

    monkeypatch.setattr(export_module, "_check_integrity", broken)

    export_row = console.create_export(platform_user, org)
    assert export_row.status == "failed"
    assert "dangling" in export_row.error
    assert not export_row.file, "a failed export must not be downloadable"
    assert export_row.is_downloadable is False
    # The manifest is still kept, because it is the evidence of what failed.
    assert export_row.manifest["integrity"]["dangling_references"]


def test_an_export_that_raises_lands_in_failed_with_the_reason(
        org, platform_user, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("storage is on fire")

    monkeypatch.setattr(export_module, "_build", explode)
    export_row = console.create_export(platform_user, org)
    assert export_row.status == "failed"
    assert "storage is on fire" in export_row.error
    assert not export_row.file


# --- ISOLATED -----------------------------------------------------------
def test_one_customers_bundle_contains_one_customers_data(org, nif,
                                                           platform_user):
    """Asserted against a second tenant holding the same shapes.

    An export is the one operation that reads a whole tenant at once, so it
    is also the one where a forgotten scope would be invisible: the bundle
    would simply be bigger, and nobody counts rows in a zip.
    """
    from leaves.models import Department

    with tenant_context(nif):
        theirs = Department.objects.create(name="NIF Only", code="NIFONLY")

    export_row = console.create_export(platform_user, org)
    with _bundle(export_row) as archive:
        departments = archive.read("data/json/leaves.Department.json").decode()
        everything = b"".join(archive.read(name)
                              for name in archive.namelist()
                              if name.endswith(".json")).decode()

    assert "NIFONLY" not in departments
    assert str(theirs.pk) not in everything, (
        "another tenant's row id appears somewhere in this bundle")
    assert str(nif.pk) not in departments


def test_the_platform_side_of_the_export_is_also_one_tenants(org, nif,
                                                              platform_user):
    export_row = console.create_export(platform_user, org)
    with _bundle(export_row) as archive:
        subscriptions = archive.read(
            "platform/json/tenancy.Subscription.json").decode()
        organizations = archive.read(
            "platform/json/tenancy.Organization.json").decode()

    assert str(org.pk) in organizations
    assert str(nif.pk) not in organizations
    assert str(nif.subscription.pk) not in subscriptions


def test_the_subscription_history_and_audit_trail_are_included(org,
                                                                platform_user):
    """"Everything you hold about us" includes what we did to them."""
    export_row = console.create_export(platform_user, org)
    manifest = _manifest(export_row)
    platform_tables = manifest["platform_tables"]

    assert platform_tables["tenancy.Subscription"]["rows"] == 1
    assert platform_tables["tenancy.SubscriptionEvent"]["rows"] >= 1
    assert platform_tables["tenancy.PlatformAuditLog"]["rows"] >= 1
    # The plan their subscription points at, so that row is not an orphan.
    assert platform_tables["tenancy.Plan"]["rows"] == 1


def test_credentials_are_never_exported(org, platform_user):
    export_row = console.create_export(platform_user, org)
    with _bundle(export_row) as archive:
        names = archive.namelist()

    for forbidden in ("sessions.Session", "token_blacklist.OutstandingToken",
                      "token_blacklist.BlacklistedToken"):
        assert not any(forbidden in name for name in names), forbidden
        assert forbidden in export_module.PLATFORM_EXCLUDED


# --- the receipt, and who may touch it ----------------------------------
def test_the_export_record_is_platform_data_not_tenant_data():
    """A customer must not be able to discover that an export of their
    workspace exists, let alone read one."""
    assert "tenancy.TenantExport" in PLATFORM_GLOBAL


def test_the_receipt_survives_the_bundle(org, platform_user):
    """The manifest is stored on the row as well as inside the zip.

    A receipt that only exists inside the thing it describes is not a
    receipt: after the bundle expires, the platform still has to answer what
    was handed over and whether it verified.
    """
    export_row = console.create_export(platform_user, org)
    assert export_row.manifest["totals"]["rows"] == export_row.row_count
    assert export_row.manifest["organization"]["slug"] == org.slug

    export_row.file.delete(save=True)
    export_row.refresh_from_db()
    assert export_row.manifest["totals"]["rows"] == export_row.row_count


def test_a_download_is_audited_every_time_not_once(org, platform_user):
    from tenancy.models import PlatformAuditLog

    export_row = console.create_export(platform_user, org)
    console.record_export_download(platform_user, export_row)
    console.record_export_download(platform_user, export_row)

    export_row.refresh_from_db()
    assert export_row.download_count == 2
    assert export_row.downloaded_at is not None
    entries = PlatformAuditLog.objects.filter(
        organization=org, action=PlatformAuditLog.Action.EXPORT_DOWNLOADED)
    assert entries.count() == 2, (
        "taking a second copy of a customer's dataset is a second event")


def test_creating_an_export_is_audited_with_its_checksum(org, platform_user):
    from tenancy.models import PlatformAuditLog

    export_row = console.create_export(platform_user, org)
    entry = PlatformAuditLog.objects.filter(
        organization=org,
        action=PlatformAuditLog.Action.EXPORT_CREATED).first()
    assert entry is not None
    assert entry.changes["sha256"] == export_row.sha256
    assert entry.actor_email == platform_user.email


def test_a_failed_export_is_audited_too(org, platform_user, monkeypatch):
    """Otherwise the trail reads as though nobody ever tried."""
    from tenancy.models import PlatformAuditLog

    monkeypatch.setattr(export_module, "_build",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("nope")))
    console.create_export(platform_user, org)
    entry = PlatformAuditLog.objects.filter(
        organization=org,
        action=PlatformAuditLog.Action.EXPORT_CREATED).first()
    assert entry is not None
    assert entry.changes["status"] == "failed"


# --- the contents switch -------------------------------------------------
def test_a_json_only_export_omits_the_csv_and_the_media(org, platform_user):
    from tenancy.models import TenantExport

    export_row = console.create_export(platform_user, org,
                                       contents=TenantExport.Contents.JSON)
    with _bundle(export_row) as archive:
        names = archive.namelist()
    assert any(name.startswith("data/json/") for name in names)
    assert not any(name.startswith("data/csv/") for name in names)
    assert not any(name.startswith("media/") for name in names)


def test_the_csv_is_flat_and_readable(org, platform_user):
    """CSV exists so a customer's HR team can open a file. A nested object in
    a spreadsheet cell is not that."""
    import csv as csv_module

    from tenancy.models import TenantExport

    export_row = console.create_export(platform_user, org,
                                       contents=TenantExport.Contents.CSV)
    with _bundle(export_row) as archive:
        text = archive.read("data/csv/leaves.Department.csv").decode()

    rows = list(csv_module.reader(io.StringIO(text)))
    header = rows[0]
    # The columns are the model's own, in the model's own order -- so the
    # assertion is about SHAPE, not about which field happens to be declared
    # second. A foreign key appears as the raw `_id` column a spreadsheet can
    # match on, which is the whole reason this file exists beside the JSON.
    assert header[0] == "id"
    assert "organization_id" in header
    assert {"code", "name"} <= set(header)
    assert all(not cell.startswith("{") for cell in rows[1])
    assert len(rows) >= 6                      # header + five departments
