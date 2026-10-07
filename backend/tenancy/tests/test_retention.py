"""Phase S6.5 Part 4: the retention policy, asserted rather than asserted-in-prose.

The policy is `docs/tenant-retention-and-deletion-policy.md`. Two of its
claims are the kind that rot quietly unless a test holds them:

  * export bundles expire and the receipt does not;
  * nothing anywhere deletes a tenant's own records.

The second is a test about an ABSENCE, which is unusual and worth keeping: the
day somebody adds a `purge_organization()` to make a cleanup script easier,
this fails and the seven conditions in §6 of the policy get read before it
ships rather than afterwards.
"""
import datetime

import pytest

from tenancy import console, retention
from tenancy.models import PlatformAuditLog, TenantExport

pytestmark = pytest.mark.django_db


def _age(export_row, days):
    """Backdate an export's expiry so a sweep will select it."""
    from django.utils import timezone

    export_row.expires_at = timezone.now() - datetime.timedelta(days=days)
    export_row.save(update_fields=["expires_at"])
    return export_row


# --- bundles expire, receipts do not ------------------------------------
def test_a_fresh_bundle_is_not_swept(org, platform_user):
    console.create_export(platform_user, org)
    assert retention.expire_exports()["expired"] == []


def test_an_expired_bundle_loses_its_file_and_keeps_its_receipt(org,
                                                                 platform_user):
    export_row = _age(console.create_export(platform_user, org), 1)
    manifest, checksum, rows = (export_row.manifest, export_row.sha256,
                                export_row.row_count)

    result = retention.expire_exports()
    assert len(result["expired"]) == 1
    assert result["freed_bytes"] > 0

    export_row.refresh_from_db()
    assert export_row.status == TenantExport.Status.EXPIRED
    assert not export_row.file, "the copy is still on disk"
    assert export_row.is_downloadable is False
    # The receipt survives, which is the point: the platform can still say
    # what was handed over and whether it verified.
    assert export_row.manifest == manifest
    assert export_row.sha256 == checksum
    assert export_row.row_count == rows


def test_discarding_a_bundle_is_audited_with_the_rule_applied(org,
                                                               platform_user):
    _age(console.create_export(platform_user, org), 1)
    retention.expire_exports(actor=platform_user)

    entry = PlatformAuditLog.objects.filter(
        organization=org,
        action=PlatformAuditLog.Action.RETENTION_ACTION).first()
    assert entry is not None
    assert "expire after" in entry.changes["rule"]
    assert str(retention.retention_days()) in entry.changes["rule"]
    assert entry.changes["discarded_export"]["sha256"]
    assert "no tenant data was affected" in entry.note.lower()


def test_a_sweep_that_finds_nothing_writes_no_audit_entries(org,
                                                             platform_user):
    """A daily row saying nothing happened is noise, not evidence."""
    console.create_export(platform_user, org)
    before = PlatformAuditLog.objects.count()
    retention.expire_exports()
    assert PlatformAuditLog.objects.count() == before


def test_a_dry_run_reports_and_changes_nothing(org, platform_user):
    """The first thing anybody should do with a retention job."""
    export_row = _age(console.create_export(platform_user, org), 1)

    result = retention.expire_exports(dry_run=True)
    assert len(result["expired"]) == 1
    assert result["dry_run"] is True

    export_row.refresh_from_db()
    assert export_row.status == TenantExport.Status.READY
    assert export_row.file
    assert not PlatformAuditLog.objects.filter(
        action=PlatformAuditLog.Action.RETENTION_ACTION).exists()


def test_sweeping_twice_is_safe(org, platform_user):
    _age(console.create_export(platform_user, org), 1)
    first = retention.expire_exports()
    second = retention.expire_exports()
    assert len(first["expired"]) == 1
    assert second["expired"] == []


def test_the_sweep_never_touches_a_failed_export(org, platform_user,
                                                  monkeypatch):
    """It has no file to discard, and its receipt is the record of a failure."""
    from tenancy import export as export_module

    monkeypatch.setattr(export_module, "_build",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    export_row = _age(console.create_export(platform_user, org), 1)
    assert export_row.status == TenantExport.Status.FAILED

    retention.expire_exports()
    export_row.refresh_from_db()
    assert export_row.status == TenantExport.Status.FAILED


def test_retention_does_not_touch_tenant_data(org, platform_user):
    """The sweep's blast radius, asserted."""
    from django.apps import apps

    from tenancy.context import tenant_context
    from tenancy.inventory import TENANT_SCOPED

    def census():
        counts = {}
        with tenant_context(org):
            for label in sorted(TENANT_SCOPED):
                app_label, model_name = label.split(".")
                counts[label] = apps.get_model(app_label,
                                                model_name).objects.count()
        return counts

    _age(console.create_export(platform_user, org), 1)
    before = census()
    retention.expire_exports()
    assert census() == before


# --- the policy's central claim -----------------------------------------
def test_no_code_path_deletes_a_tenants_records():
    """Part 4: "Do NOT permanently delete data" -- held by a test.

    Deliberately a test about an ABSENCE. The day somebody adds a
    `purge_organization()` because a cleanup script needed one, this fails,
    and §6 of the policy -- the seven conditions a purge must meet -- gets
    read before it ships rather than after.

    Scoped to the tenancy package on purpose: a tenant user deleting their
    own leave request through the application is ordinary business, and
    nothing to do with platform retention.
    """
    import pathlib
    import re

    package = pathlib.Path(__file__).resolve().parent.parent
    forbidden = re.compile(r"\b(purge|destroy|erase|wipe)_(organization|tenant)\b")
    offenders = []
    for path in package.rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        text = path.read_text()
        if forbidden.search(text):
            offenders.append(path.name)
        # `.delete()` on an Organization would take every tenant row with it
        # through the cascade, which is the one call that must not exist.
        if re.search(r"Organization\.objects\.(?:[a-z_]+\(.*?\)\.)*delete\(",
                     text):
            offenders.append(f"{path.name} (Organization delete)")
    assert offenders == [], (
        "the retention policy says the platform cannot delete a tenant; "
        f"these modules now suggest otherwise: {offenders}")


def test_cancelling_a_tenant_still_deletes_nothing(org, platform_user):
    """Already true in Phase S6; pinned here because the policy relies on it."""
    from django.apps import apps

    from tenancy.context import tenant_context
    from tenancy.inventory import TENANT_SCOPED

    def census():
        counts = {}
        with tenant_context(org):
            for label in sorted(TENANT_SCOPED):
                app_label, model_name = label.split(".")
                counts[label] = apps.get_model(app_label,
                                                model_name).objects.count()
        return counts

    before = census()
    console.cancel(platform_user, org, reason="Customer left")
    assert census() == before


def test_the_retention_window_is_configurable_and_short(settings):
    """Short on purpose: a bundle is a whole workspace in one file."""
    assert retention.retention_days() == 30
    settings.TENANCY_EXPORT_RETENTION_DAYS = 7
    assert retention.retention_days() == 7


def test_the_policy_document_exists_and_names_its_mechanisms():
    """The deliverable for Part 4 is a document; this is the link that keeps
    it from drifting away from the code it describes."""
    import pathlib

    # tests -> tenancy -> backend -> the repository root, which is where
    # `docs/` lives.
    policy = (pathlib.Path(__file__).resolve().parents[3]
              / "docs" / "tenant-retention-and-deletion-policy.md")
    assert policy.exists(), "Part 4's documented policy is missing"
    text = policy.read_text()
    for mechanism in ("tenancy/archive.py", "tenancy/export.py",
                      "status_before_archive", "RETENTION_ACTION",
                      "TENANCY_EXPORT_RETENTION_DAYS"):
        assert mechanism in text, f"the policy does not mention {mechanism}"
