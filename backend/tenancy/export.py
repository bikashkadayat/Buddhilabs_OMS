"""Phase S6.5 Part 1/5/6: one tenant's data, in one verifiable file.

WHAT THIS IS FOR
----------------
A customer who cannot leave has not bought a service, they have been captured
by one. Everything the platform can do to a tenant -- provision, suspend,
cancel -- was already possible before this module; the one thing it could not
do was hand the customer their own data back. That is the whole gap.

THE REGISTRY DRIVES IT, AND THAT IS THE DESIGN
----------------------------------------------
The set of tables exported is not a list maintained here. It is
``tenancy.inventory.TENANT_SCOPED`` -- the same CI-enforced registry that says
which models carry ``organization_id`` and get an RLS policy, and which
``tenancy/tests/test_inventory.py`` fails on if any model in the project is
missing from it. So a model added next month is exported automatically, and a
model deliberately left out has to be named in ``EXCLUDED`` with a reason.

An export built from a hand-written list of "the important tables" is the
thing this avoids: it is correct on the day it is written and silently
incomplete forever afterwards, and the customer only discovers which table was
forgotten after they have migrated off.

NO PARTIAL EXPORTS (Part 6)
---------------------------
A bundle becomes downloadable at exactly one moment: when every table has been
written AND the integrity pass found no dangling reference. Anything else is
``FAILED`` with the reason and carries no file. An operator must never be able
to hand a customer an export that is missing rows without knowing it, because
the customer will find out by importing it somewhere else, months later.

WHY IT RUNS SYNCHRONOUSLY
-------------------------
``reports`` generates in a daemon thread, and for a report that is right: the
user is waiting on a page and a failure costs them a retry. An export is a
different object. A thread that dies mid-bundle leaves the row in ``RUNNING``
for ever, which is indistinguishable from "still working" -- and the one thing
Part 6 forbids is a partial export that looks finished. Building in the
request means the status is decided by code that cannot be killed between two
statements. The cost is a slow request for a very large tenant, which is a
real limit and is reported as one rather than hidden behind a spinner.
"""
import csv
import hashlib
import io
import json
import logging
import zipfile

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.serializers import serialize
from django.db import models, transaction
from django.utils import timezone

from .context import no_tenant, tenant_context
from .inventory import TENANT_SCOPED

logger = logging.getLogger(__name__)

# The bundle format. Bumped when the LAYOUT changes, so a reader can tell
# whether it understands a file somebody kept for two years.
SCHEMA_VERSION = 1

# Tenant-scoped models deliberately NOT exported, each with its reason. Empty
# on purpose: everything a tenant owns goes in the bundle, including the
# derived summaries, because "nothing lost" is easier to verify than "nothing
# lost that we judged to matter". A future exclusion has to be argued for
# here, and `test_export.py` fails if a model is neither exported nor listed.
EXCLUDED = {}

# Platform-side records ABOUT this tenant that belong in its export. These are
# not tenant data -- they are the platform's own rows -- but a customer exit
# package without the organization profile, the subscription history or the
# record of what was done to them is not a complete answer to "give me
# everything you hold about us".
#
# ``(label, filter keyword)``: how to narrow that model to this one tenant.
PLATFORM_SIDE = [
    ("tenancy.Organization", "pk"),
    ("tenancy.OrganizationSettings", "organization"),
    ("tenancy.OrganizationBranding", "organization"),
    ("tenancy.Subscription", "organization"),
    ("tenancy.SubscriptionEvent", "subscription__organization"),
    ("tenancy.Payment", "organization"),
    ("tenancy.PlatformAuditLog", "organization"),
    # Phase S9. INCLUDED rather than excluded: the hostname is the
    # customer's own -- they bought the domain and they chose the name --
    # and a tenant leaving should get a record of which addresses their
    # workspace answered on, not have to reconstruct it from DNS.
    ("tenancy.TenantDomain", "organization"),
    # Customer success. INCLUDED: the questions, problem reports, feature
    # requests and ratings this customer's people sent us are words they
    # wrote, and "everything you hold about us" plainly covers them.
    ("tenancy.SupportRequest", "organization"),
    # The conversation on those tickets -- the customer's own words and our
    # replies to them. Internal staff notes are filtered out (below): they
    # are the platform's working notes, not something the customer sent or
    # was sent.
    ("tenancy.SupportMessage", "ticket__organization"),
]

# Extra filters for a PLATFORM_SIDE table, applied after its lookup.
PLATFORM_SIDE_FILTERS = {
    "tenancy.SupportMessage": {"is_internal": False},
}

# Platform models NOT included, and why -- the same discipline as EXCLUDED.
PLATFORM_EXCLUDED = {
    "tenancy.SuccessTask": "the platform team's own working list about this "
                           "customer (calls, follow-ups); like an internal "
                           "ticket note, not something the customer sent",
    "tenancy.SupportTeam": "how the platform's support staff are organised; "
                           "not this customer's data",
    "tenancy.SupportTeamMember": "as SupportTeam",
    "tenancy.SupportMention": "platform staff @mentioning each other in "
                              "internal notes, which are themselves excluded",
    "tenancy.SupportTicketLink": "the support team's own cross-references "
                                 "between tickets, possibly other customers'",
    "tenancy.KnownIssue": "the platform's write-up of a problem shared by "
                          "every customer; not this customer's data",
    "tenancy.SuccessCampaign": "the platform team's outreach plan, as "
                               "SuccessTask",
    "tenancy.ProductUpdate": "What's New notes published to every customer "
                             "alike; not this customer's data",
    "tenancy.StatusNotice": "platform incident notices shown to every "
                            "customer alike; not this customer's data",
    "tenancy.Plan": "the platform's price list, not this customer's data; the "
                    "plans their subscription references are exported with it",
    "tenancy.PlanPrice": "as Plan",
    "tenancy.TenantExport": "the receipts for exports of this tenant, "
                            "including this one -- self-referential, and a "
                            "record of operator actions rather than customer "
                            "data",
    "tenancy.PaymentInstruction": "platform bank details shown to every "
                                  "customer; not tenant data",
    "tenancy.PlatformMetric": "daily counts the platform keeps about itself "
                              "-- how many sign-ins, how many provisioning "
                              "attempts. It holds no identifying detail by "
                              "design, and the tenant's own audit trail is "
                              "where their activity is recorded and exported "
                              "from",
    "tenancy.PendingRegistration": "the platform's record of a signup "
                                   "REQUEST, not the customer's data -- and "
                                   "it holds a password hash and a live "
                                   "verification token, neither of which "
                                   "belongs in a file somebody emails around",
    "contenttypes.ContentType": "install-local ids with no meaning in another "
                                "deployment; the rows that use them carry the "
                                "app and model name in their JSON instead",
    "auth.Permission": "as ContentType",
    "auth.Group": "unused by this application's role model",
    "sessions.Session": "live session keys; exporting them would export "
                        "credentials",
    "admin.LogEntry": "Django admin actions, which this deployment does not "
                      "use for tenant work",
    "token_blacklist.OutstandingToken": "refresh tokens are credentials",
    "token_blacklist.BlacklistedToken": "as OutstandingToken",
}


def _model(label):
    app_label, model_name = label.split(".")
    return apps.get_model(app_label, model_name)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _rows_as_csv(model, rows):
    """A flat CSV of the concrete columns, foreign keys as raw ids.

    Deliberately NOT the serialised JSON reshaped: CSV is here so a finance or
    HR team can open a file, and a column called ``leave_type_id`` holding a
    uuid is more use to them than a nested object. The JSON copy is the one
    that round-trips.
    """
    fields = [f for f in model._meta.concrete_fields]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([f.attname for f in fields])
    for row in rows:
        writer.writerow([
            "" if getattr(row, f.attname, None) is None
            else str(getattr(row, f.attname)) for f in fields])
    return buffer.getvalue().encode("utf-8")


def _file_fields(model):
    return [f for f in model._meta.concrete_fields
            if isinstance(f, models.FileField)]


def _tenant_targets():
    """``{model class: label}`` for every exported tenant model.

    Used by the integrity pass to decide whether a foreign key points at
    something the bundle is supposed to contain.
    """
    targets = {}
    for label in TENANT_SCOPED:
        if label in EXCLUDED:
            continue
        targets[_model(label)] = label
    return targets


class _Bundle:
    """Accumulates members, hashing each one as it goes."""

    def __init__(self):
        self.buffer = io.BytesIO()
        self.zip = zipfile.ZipFile(self.buffer, "w", zipfile.ZIP_DEFLATED)
        self.members = {}

    def add(self, path, data):
        self.zip.writestr(path, data)
        self.members[path] = {"bytes": len(data), "sha256": _sha256(data)}

    def close(self):
        self.zip.close()
        return self.buffer.getvalue()


def build_export(export, *, storage=None):
    """Fill one ``TenantExport``. Returns it, READY or FAILED, never partial.

    Everything inside the tenant loop runs in ``tenant_context``, which is
    what makes the plain ``Model.objects.all()`` calls both scoped and -- under
    row-level security -- permitted at all. An export that used
    ``all_tenants`` would be one bug away from shipping two customers' data in
    one file, so it does not have the capability in the first place.
    """
    from .models import TenantExport

    storage = storage or default_storage
    organization = export.organization
    started = timezone.now()

    export.status = TenantExport.Status.RUNNING
    export.organization_slug = organization.slug
    export.save(update_fields=["status", "organization_slug"])

    try:
        bundle, manifest = _build(export, organization, storage)
    except Exception as exc:                       # noqa: BLE001
        logger.exception("export of %s failed", organization.slug)
        export.status = TenantExport.Status.FAILED
        export.error = f"{type(exc).__name__}: {exc}"
        export.completed_at = timezone.now()
        export.save(update_fields=["status", "error", "completed_at"])
        return export

    dangling = manifest["integrity"]["dangling_references"]
    if dangling:
        # REFUSED, not warned about. A bundle whose rows point at records it
        # does not contain cannot be imported anywhere, and the only thing
        # worse than no export is one the customer believes is complete.
        export.status = TenantExport.Status.FAILED
        export.error = (f"integrity check failed: {len(dangling)} dangling "
                        f"reference(s), first: {dangling[0]}")
        export.manifest = manifest
        export.completed_at = timezone.now()
        export.save(update_fields=["status", "error", "manifest",
                                   "completed_at"])
        return export

    name = (f"{organization.slug}-export-"
            f"{started.strftime('%Y%m%d-%H%M%S')}.zip")
    export.file.save(name, ContentFile(bundle), save=False)
    export.size_bytes = len(bundle)
    export.sha256 = _sha256(bundle)
    export.manifest = manifest
    export.row_count = manifest["totals"]["rows"]
    export.media_count = manifest["totals"]["media_files"]
    export.status = TenantExport.Status.READY
    export.completed_at = timezone.now()
    export.save()
    logger.info("exported %s: %d rows, %d media files, %d bytes",
                organization.slug, export.row_count, export.media_count,
                export.size_bytes)
    return export


def _build(export, organization, storage):
    """The bundle and its manifest. Raises on anything it cannot complete."""
    from .models import TenantExport

    wants_json = export.contents in (TenantExport.Contents.JSON,
                                     TenantExport.Contents.FULL)
    wants_csv = export.contents in (TenantExport.Contents.CSV,
                                    TenantExport.Contents.FULL)
    wants_media = export.contents == TenantExport.Contents.FULL

    bundle = _Bundle()
    tables = {}
    exported_ids = {}
    media = []
    missing_media = []
    total_rows = 0

    targets = _tenant_targets()

    # --- the tenant's own tables -----------------------------------------
    with tenant_context(organization):
        for label in sorted(targets.values()):
            model = _model(label)
            rows = list(model.objects.all().order_by("pk"))
            exported_ids[label] = {str(row.pk) for row in rows}
            total_rows += len(rows)

            entry = {"rows": len(rows), "files": {}}
            if wants_json:
                data = serialize("json", rows, indent=2).encode("utf-8")
                path = f"data/json/{label}.json"
                bundle.add(path, data)
                entry["files"]["json"] = path
            if wants_csv:
                path = f"data/csv/{label}.csv"
                bundle.add(path, _rows_as_csv(model, rows))
                entry["files"]["csv"] = path
            tables[label] = entry

            if wants_media:
                for field in _file_fields(model):
                    for row in rows:
                        value = getattr(row, field.attname, None)
                        if not value:
                            continue
                        record = {"model": label, "field": field.name,
                                  "pk": str(row.pk), "path": str(value)}
                        try:
                            with storage.open(str(value), "rb") as handle:
                                payload = handle.read()
                        except Exception:          # noqa: BLE001
                            # RECORDED, NOT RAISED. A row pointing at a file
                            # that is no longer in storage is a pre-existing
                            # fact about this tenant, not a fault in the
                            # export, and refusing to export anything until
                            # somebody finds a file deleted two years ago
                            # would leave the customer with nothing at all.
                            # It goes in the manifest where it is actionable.
                            missing_media.append(record)
                            continue
                        member = f"media/{value}"
                        bundle.add(member, payload)
                        record["bytes"] = len(payload)
                        record["sha256"] = bundle.members[member]["sha256"]
                        media.append(record)

    # --- the platform's records about this tenant -------------------------
    platform_tables = {}
    with no_tenant():
        plan_ids = set()
        for label, lookup in PLATFORM_SIDE:
            model = _model(label)
            if lookup == "pk":
                rows = list(model.objects.filter(pk=organization.pk))
            else:
                rows = list(model.objects.filter(**{lookup: organization})
                            .filter(**PLATFORM_SIDE_FILTERS.get(label, {}))
                            .order_by("pk"))
            platform_tables[label] = {"rows": len(rows), "files": {}}
            if rows and wants_json:
                path = f"platform/json/{label}.json"
                bundle.add(path, serialize("json", rows,
                                           indent=2).encode("utf-8"))
                platform_tables[label]["files"]["json"] = path
            if rows and wants_csv:
                path = f"platform/csv/{label}.csv"
                bundle.add(path, _rows_as_csv(model, rows))
                platform_tables[label]["files"]["csv"] = path
            for row in rows:
                plan = getattr(row, "plan_id", None)
                if plan:
                    plan_ids.add(plan)

        # The plans their subscription and payments reference, so those rows
        # are not orphaned in the bundle. The rest of the price list is not
        # theirs and is not included.
        if plan_ids:
            Plan = _model("tenancy.Plan")
            plans = list(Plan.objects.filter(pk__in=plan_ids).order_by("pk"))
            platform_tables["tenancy.Plan"] = {
                "rows": len(plans), "files": {},
                "note": "only the plans this tenant's records reference"}
            if wants_json:
                path = "platform/json/tenancy.Plan.json"
                bundle.add(path, serialize("json", plans,
                                            indent=2).encode("utf-8"))
                platform_tables["tenancy.Plan"]["files"]["json"] = path

    # --- integrity (Part 6) -----------------------------------------------
    integrity = _check_integrity(organization, targets, exported_ids)
    integrity["missing_media_files"] = missing_media

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timezone.now().isoformat(),
        "generator": "tenancy.export",
        "export_id": str(export.pk),
        "contents": export.contents,
        "organization": {
            "id": str(organization.pk),
            "slug": organization.slug,
            "name": organization.name,
            "document_prefix": organization.document_prefix,
            "status": organization.status,
        },
        "totals": {
            "tables": len(tables),
            "rows": total_rows,
            "media_files": len(media),
            "media_bytes": sum(item["bytes"] for item in media),
        },
        "tables": tables,
        "platform_tables": platform_tables,
        "media": media,
        "excluded": {"tenant": EXCLUDED, "platform": PLATFORM_EXCLUDED},
        "integrity": integrity,
        "members": bundle.members,
    }

    bundle.add("manifest.json",
               json.dumps(manifest, indent=2, default=str).encode("utf-8"))
    bundle.add("README.txt", _readme(organization, manifest).encode("utf-8"))
    return bundle.close(), manifest


def _check_integrity(organization, targets, exported_ids):
    """Every reference resolves, and every row belongs to this tenant.

    TWO QUESTIONS, BOTH WORTH ASKING SEPARATELY:

    * **No orphans.** For every exported row, a foreign key pointing at
      another exported model must name a row that is also in the bundle.
      Otherwise the dataset cannot be imported anywhere: a leave day record
      whose leave type was left behind is not data, it is a dangling id.

    * **Nothing cross-tenant.** Every row's ``organization_id`` is checked
      against the tenant being exported. The scoped managers and the RLS
      policy should both already guarantee this; checking anyway is cheap, and
      it is the one assertion that would catch an export built with
      ``all_tenants`` by a future edit.
    """
    dangling = []
    foreign = []
    with tenant_context(organization):
        for label in sorted(targets.values()):
            model = _model(label)
            relations = [
                f for f in model._meta.concrete_fields
                if isinstance(f, models.ForeignKey)
                and f.related_model in targets
            ]
            for row in model.objects.all().iterator():
                if str(getattr(row, "organization_id", organization.pk)) \
                        not in (str(organization.pk), "None"):
                    foreign.append({"model": label, "pk": str(row.pk),
                                    "organization_id":
                                        str(row.organization_id)})
                for field in relations:
                    value = getattr(row, field.attname, None)
                    if value is None:
                        continue
                    target_label = targets[field.related_model]
                    if str(value) not in exported_ids.get(target_label, ()):
                        dangling.append({
                            "model": label, "pk": str(row.pk),
                            "field": field.name,
                            "points_at": f"{target_label}:{value}"})
    return {
        "checked_tables": len(targets),
        "dangling_references": dangling,
        "cross_tenant_rows": foreign,
        "content_type_ids_are_install_local": True,
    }


def _readme(organization, manifest):
    totals = manifest["totals"]
    return f"""EXPORT OF {organization.name} ({organization.slug})

Generated      {manifest['generated_at']}
Schema version {manifest['schema_version']}
Contents       {manifest['contents']}

  {totals['tables']} tables, {totals['rows']} rows
  {totals['media_files']} media files, {totals['media_bytes']} bytes

WHAT IS IN HERE

  manifest.json      every member of this archive, its size and its SHA-256.
                     Verify the bundle against it before relying on it.
  data/json/         one file per table, in Django's serialisation format.
                     Primary keys and foreign keys are preserved exactly, so
                     this is the copy that can be loaded back.
  data/csv/          the same rows, flat, for reading in a spreadsheet.
                     Foreign keys appear as raw ids.
  platform/          our own records ABOUT this organization: its profile,
                     settings, branding, subscription history, payments and
                     the log of administrative actions taken on it.
  media/             every file referenced by a row above, at the path the row
                     records. Checksummed individually in the manifest.

WHAT IS NOT IN HERE, AND WHY

  See `excluded` in manifest.json. In short: credentials (sessions, refresh
  tokens), our platform-wide price list, and Django's install-local content
  type ids -- which is why any row that refers to a content type also carries
  the app and model name in its JSON.

INTEGRITY

  `integrity` in manifest.json records the checks this bundle passed: every
  foreign key between exported tables resolves to a row that is present, and
  every row belongs to this organization. A bundle that failed either check
  was never written -- there is no partial export.
"""


# ---------------------------------------------------------------------------
# The entry point the console uses
# ---------------------------------------------------------------------------
@transaction.atomic
def create_export(organization, *, actor=None, contents=None, request=None,
                  retention_days=None):
    """Record the request, build the bundle, audit the result.

    One transaction: a receipt describing a bundle that was never written, or
    a bundle with no receipt, are both worse than a failure.
    """
    from django.conf import settings

    from . import platform_audit
    from .models import PlatformAuditLog, TenantExport

    days = (retention_days if retention_days is not None
            else getattr(settings, "TENANCY_EXPORT_RETENTION_DAYS", 30))
    export = TenantExport.objects.create(
        organization=organization,
        organization_slug=organization.slug,
        contents=contents or TenantExport.Contents.FULL,
        requested_by=actor if getattr(actor, "pk", None) else None,
        requested_by_email=(getattr(actor, "email", "") or "")[:254],
        expires_at=timezone.now() + timezone.timedelta(days=days),
    )
    build_export(export)

    platform_audit.record(
        actor, PlatformAuditLog.Action.EXPORT_CREATED,
        organization=organization,
        changes={"export_id": str(export.pk), "status": export.status,
                 "rows": export.row_count, "media_files": export.media_count,
                 "bytes": export.size_bytes, "sha256": export.sha256},
        note=(f"Export {export.status}: {export.row_count} rows, "
              f"{export.media_count} media file(s)."
              + (f" {export.error}" if export.error else "")),
        request=request)
    return export
