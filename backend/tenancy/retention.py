"""Phase S6.5 Part 4: the retention actions that only ever discard COPIES.

WHAT THIS MODULE IS ALLOWED TO TOUCH
------------------------------------
Export bundles, and nothing else. A bundle is a derived artifact: it can be
regenerated from live data at any time, and every extra day one exists is a
complete copy of a customer's workspace sitting on disk. So bundles expire.

A tenant's own records do not, and there is deliberately no code here that
could remove one. The policy that says why, and the seven conditions a future
purge would have to meet, is `docs/tenant-retention-and-deletion-policy.md`.

THE ROW OUTLIVES THE FILE
-------------------------
Expiring a bundle keeps the ``TenantExport`` row -- its manifest, checksum,
row counts and download history -- and drops only the file. The receipt is
what lets the platform answer "what did we hand this customer, when, and did
anybody take a second copy" long after the bundle itself should no longer
exist.
"""
import logging

from django.conf import settings
from django.utils import timezone

from . import platform_audit
from .context import no_tenant

logger = logging.getLogger(__name__)


def retention_days():
    return int(getattr(settings, "TENANCY_EXPORT_RETENTION_DAYS", 30))


def expire_exports(*, actor=None, now=None, dry_run=False):
    """Discard export bundles past their retention date.

    :param dry_run: report what would be discarded and touch nothing. Present
        because the first thing anybody should do with a retention job is run
        it without the deleting part.
    :returns: ``{"expired": [...], "freed_bytes": n, "dry_run": bool}``
    """
    from .models import PlatformAuditLog, TenantExport

    now = now or timezone.now()
    expired = []
    freed = 0

    with no_tenant():
        due = (TenantExport.objects
               .filter(status=TenantExport.Status.READY,
                       expires_at__lte=now)
               .exclude(file="")
               .select_related("organization")
               .order_by("created_at"))
        for export in due:
            record = {
                "export_id": str(export.pk),
                "organization": export.organization_slug,
                "sha256": export.sha256,
                "bytes": export.size_bytes,
                "created_at": export.created_at.isoformat(),
                "expired_at": export.expires_at.isoformat()
                if export.expires_at else None,
                "downloads": export.download_count,
            }
            expired.append(record)
            freed += export.size_bytes or 0
            if dry_run:
                continue

            # The file, then the row's pointer to it. In that order, so a
            # crash in between leaves a row that says READY pointing at a
            # missing file -- which the download view already refuses -- rather
            # than an EXPIRED row still holding a copy nobody will clean up.
            try:
                export.file.delete(save=False)
            except Exception:                      # noqa: BLE001
                logger.warning("could not remove export bundle %s",
                               export.pk, exc_info=True)
            export.status = TenantExport.Status.EXPIRED
            export.file = None
            export.save(update_fields=["status", "file"])

            platform_audit.record(
                actor, PlatformAuditLog.Action.RETENTION_ACTION,
                organization=export.organization,
                changes={"discarded_export": record,
                         "rule": f"export bundles expire after "
                                 f"{retention_days()} day(s)"},
                note=(f"Export bundle discarded under the retention policy. "
                      f"The receipt, manifest and checksum are kept; no "
                      f"tenant data was affected."))

    logger.info("retention sweep: %d bundle(s), %d bytes%s", len(expired),
                freed, " (dry run)" if dry_run else "")
    return {"expired": expired, "freed_bytes": freed, "dry_run": dry_run,
            "retention_days": retention_days()}
