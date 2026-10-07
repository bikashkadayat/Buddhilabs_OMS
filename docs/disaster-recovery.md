# Disaster Recovery

**Phase:** S6.75 Part 6.
**Scope:** what can be recovered, by whom, how, and how long it takes.
**Status:** the tenant-level procedures are implemented and tested. The
infrastructure-level ones (database, files) are **procedures for an operator**,
not code, and are written here because the code cannot do them.

---

## 0. What this document is honest about

Four recovery paths are listed in the brief. They are not equally real, and
pretending otherwise is how a recovery plan fails on the day it is needed:

| Path | State | Who runs it | Tested by |
|---|---|---|---|
| **Tenant restore** (un-archive) | implemented | platform operator, one click | `tenancy/tests/test_archive.py` |
| **Export production** | implemented | platform operator, one click | `tenancy/tests/test_export.py` |
| **Export re-import** | **not built** | — | — |
| **Database restore** | procedure below | whoever runs the infrastructure | not automated |
| **File restore** | procedure below | whoever runs the infrastructure | not automated |

**The gap worth naming first: a bundle produced by `tenancy/export.py` cannot
be loaded back in by this platform.** It is a faithful, checksummed,
machine-readable copy in Django's own serialisation format — so it can be
loaded by somebody who writes an importer, and the ids and foreign keys are
all preserved to make that possible — but no `import_organization()` exists.
Export answers "give us our data"; it does not yet answer "put it back".
Restoring a tenant from a bundle today means a Django shell and
`loaddata`-style work by an engineer, against an empty organization, in
dependency order. See §5 for what that would take and §6 for the risk.

---

## 1. Tenant restore (archived → working)

The common case, and the only one that is a button.

**Symptom:** a customer's users cannot sign in; the console shows the
organization as `archived`.

**Recovery:**
1. Platform console → Organizations → the customer → **Data and archive**.
2. **Restore workspace**. It returns to the status recorded in
   `status_before_archive` — the state it was archived in, not a guess.
3. Verify: the page's **Workspace health** panel reads `Tenant Ready`, and
   the customer can sign in.

**Time:** seconds. **Data loss:** none — an archive copies, moves and deletes
nothing; see `tenancy/archive.py`.

**If the archive predates `status_before_archive`** (a row archived before
that column existed), the restore lands on `SUSPENDED` deliberately, so an
operator decides where it should go rather than the code guessing. Use
**Activate** afterwards.

## 2. Producing a customer's data

**Symptom:** a customer asks for their data, or is leaving.

**Recovery:**
1. Console → the customer → **Data and archive** → **Export data**
   (Everything).
2. Wait for the receipt: it reports rows, files, bytes and a SHA-256.
3. **Download**, and verify the file against the checksum in the receipt
   before handing it over.
4. Transfer over a channel the customer nominates. **Never email it** — the
   bundle contains password hashes and personal data for every employee.
5. The bundle expires after `TENANCY_EXPORT_RETENTION_DAYS` (default 30) and
   is discarded by `manage.py tenancy_expire_exports`. The receipt is kept
   for ever.

**Time:** seconds to minutes, depending on the tenant's media. **Caveat:** the
export runs synchronously inside the request; a very large tenant means a long
request. See the remaining risks in the S6.5 report.

## 3. Database restore

**Not automated, and not something this codebase can do for you.** The
platform is a single PostgreSQL database; every tenant's rows are in it.

**Before you need it** (this is the part that is usually missing):
- `pg_dump` on a schedule, to storage in a different failure domain from the
  database.
- Restore **tested** on a throwaway instance, with the migration state
  checked (`manage.py migrate --check`) and `manage.py launch_readiness`
  afterwards.
- Point-in-time recovery (WAL archiving) if the acceptable data loss is
  smaller than the gap between dumps.

**Recovery:**
1. Stop the application. A half-restored database serving requests will take
   writes it cannot keep.
2. Restore into a **new** database, never over the live one.
3. `manage.py migrate --check` — if it reports pending migrations, the dump
   predates the code; deploy the matching code, not a newer one.
4. `manage.py launch_readiness --strict`.
5. `manage.py tenancy_refresh_counters` — seat and storage counters are
   maintained incrementally and a restore can land mid-sequence.
6. `POST /api/v1/platform/mirrors/reconcile/` (or the **Correct mirrors**
   button on Platform health) — the subscription mirror columns are
   denormalised and a restore can leave them disagreeing.
7. Repoint the application and start it.

**Per-tenant restore from a full dump** is not supported and should not be
attempted by hand: a single tenant's rows span 106 tables with foreign keys
between them, and a partial restore will violate constraints or, worse, not
violate them while being wrong. Restore the whole database to a scratch
instance and export the one tenant from there (§2).

## 4. File restore

Tenant documents, logos, payment proofs and export bundles live in media
storage: a mounted volume (`FileSystemStorage`) or a bucket (`USE_S3`).

**Before you need it:** bucket versioning, or volume snapshots, with a
retention at least as long as the database backups — a restored database
whose files are gone leaves every document row pointing at nothing.

**Recovery:** restore the object store or volume to the same prefix layout.
The paths are deterministic and recorded in the rows:
`org/<token>/<module>/<kind>/<YYYY>/<MM>/<uuid>/<filename>`.

**Verify:** run an export of an affected tenant (§2) and read
`integrity.missing_media_files` in the manifest — it lists every row whose
file is absent from storage. That is the fastest inventory of file loss this
platform can produce, and it is per tenant.

## 5. What an export re-import would require

Not built. If it is built later, it has to handle:

1. **Order.** Phase A (identity, configuration, sequences), then Phase B
   (transactional records), then Phase C (logs and summaries) — the order in
   `tenancy/inventory.py`, for the same reason the backfill used it.
2. **Ids.** The bundle preserves primary keys. An import must either keep
   them (and refuse if any collide) or rewrite every foreign key that points
   at them. Keeping them is the only safe option, which means importing into
   a tenant that does not already have those rows.
3. **The organization.** A bundle carries its own `tenancy.Organization` row.
   Importing it into a platform where that slug exists must refuse, loudly.
4. **Numbering.** `*NumberSequence` rows are in the bundle. If they are not
   restored with the records, the first new document re-issues a number that
   already exists, and the per-tenant unique constraint turns that into a
   failed save at the worst moment.
5. **Media.** Files must be written back at the exact stored paths before the
   rows that reference them are usable.
6. **Verification.** Re-run `bootstrap.verify_organization` and compare the
   census against `manifest.totals` before letting anybody in.

## 6. Recovery objectives — MEASURED (Phase S10 Part 4)

The table below used to say "depends entirely on infrastructure outside this
repo" for the row that matters most. Phase S10 ran the drill instead:
**101 organizations, 1,001 users, a 24 MB database**, dumped and restored into
an empty database, after which the application role was reconnected and asked
tenant-scoped questions. 24 of 24 checks passed.

| | Target | Measured | Notes |
|---|---|---|---|
| Database dump | minutes | **0.10 s** | `pg_dump`, 2.6 MB of SQL |
| Database restore | minutes | **2.4 s** | into an empty database, as the migration role |
| **RTO, dump + restore** | < 1 h | **2.5 s** | at this data volume; scales with size, not tenant count |
| Tenant restore (archive → open) | minutes | **0.01 s** | one console action |
| Export production | minutes | **0.18 s** | 88 rows, checksummed |
| Export re-import | — | **still not possible without engineering work** | unchanged; see §5 |
| File restore | depends on snapshots | **structure verified** | per-tenant tree; see below |

### What the drill proved that a document could not

Two failures are invisible until somebody actually restores:

1. **The row-level security policies came back.** 106 policies before, 106
   after, with `FORCE ROW LEVEL SECURITY` still set on 106 tables. A dump
   that restored the data without the policies would hand the recovered
   platform every tenant's rows in one unguarded pile — and it would look
   like a successful restore.
2. **The application role can still read it.** A restore performed as the
   superuser who took it, which the application role then cannot use, is a
   restore that has not happened. After the drill, five tenants were each
   asked for their own user list under RLS and each saw exactly its own ten;
   two sampled tenants had **zero overlap**.

### File restore, precisely

Media is stored at `media/org/<tenant-token>/…`, so it restores **per tenant**
as well as wholesale. The drill wrote a file under one tenant's prefix after
the restore, confirmed its owner could obtain a signed link, and confirmed a
second tenant was refused one (the refusal is logged at ERROR).

**One caveat found by the drill, and it is an operational one.** Nothing
prunes the media tree. Restoring the database to an earlier point leaves every
file written after that point on disk with no row referencing it: unreachable
— no organization means no signable link — but still occupying space and still
in every subsequent backup. The drill environment had **347 tenant media
directories, all orphaned**. Before a production restore, decide whether to
roll the media tree back to the same point; afterwards, an orphan sweep is a
housekeeping job somebody should own.

### Domain restore

Custom domains are rows, so they come back with the database. The drill
confirmed a domain can be claimed and verified *after* a restore and resolves
to the right tenant. Note that a hostname only becomes an allowed host once a
serving `TenantDomain` row exists, and that the host cache is per-process:
immediately after a restore, allow up to a minute (or configure Redis) before
every worker is serving the restored domains.
