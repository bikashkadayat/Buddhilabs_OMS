# Tenant Retention, Deletion and Recovery Policy

**Status:** design, adopted in code where it says "implemented"; the deletion
mechanism itself is deliberately **not built**.
**Phase:** S6.5 Part 4.
**Applies to:** every organization on the platform, and every export bundle
the platform produces.

---

## 0. Why this document exists before the code

Part 4 of the brief asks for a policy and explicitly forbids building
permanent deletion. That ordering is right, and not only because it was asked
for: a deletion routine is the one piece of platform machinery whose bugs are
not recoverable. Everything else on this platform can be fixed by running it
again.

So the sequence is: write down what the platform promises, implement the parts
that only *preserve* (archive, export, expiry of generated bundles), and leave
the irreversible step to a later phase where it can be built against a policy
that already exists rather than inventing the policy inside the function that
executes it.

**Nothing in the current codebase deletes a tenant's business data. Anywhere.**
`console.cancel` ends a subscription and keeps every row. `archive` closes a
workspace and keeps every row. There is no code path, management command or
console button that removes an organization's records, and this document is
not a plan to add one casually.

---

## 1. The states a customer's data can be in

| State | Who can reach the data | What the platform promises | Implemented |
|---|---|---|---|
| **Active** (trial / active / grace) | the customer, their own users | normal service | yes |
| **Suspended** | nobody signs in; operators can read it | a dispute is open; data untouched | yes |
| **Cancelled** | nobody signs in; operators can read it | commercial end; data kept, no retention promise beyond §3 | yes |
| **Archived** | nobody signs in; operators can read and export it | **closed and preserved for a faithful restore** | yes |
| **Purged** | nobody | the data is gone, irreversibly | **not built** |

The important boundary is between *Archived* and *Purged*, and it is the only
one that is not reversible. Everything above *Purged* is a gate; *Purged*
is a loss.

## 2. Retention strategy

### 2.1 Tenant business data

Retained **indefinitely while the organization row exists**. There is no
automatic expiry of a customer's own records, in any state, including
cancelled and archived. This is a deliberate choice for an HRMS: the records
are employment records. Attendance registers, leave approvals, appraisals and
issued documents are the evidence a former employee needs years later, and
several carry statutory retention periods in the customer's own jurisdiction
that the platform does not know and must not assume.

The platform therefore does not decide when a customer's HR records expire.
The customer does, by asking.

### 2.2 Export bundles

Retained for **30 days** by default (`TENANCY_EXPORT_RETENTION_DAYS`), then
eligible for discard. This is the opposite default to §2.1 and for the
opposite reason: a bundle is a complete copy of a workspace in one file, so
every extra day one exists is risk with no benefit once the customer has
collected it.

- The `TenantExport` **row** is kept for ever — it is the receipt, and a
  record that an export happened is itself audit evidence.
- The **file** may be discarded after `expires_at`; the row moves to
  `EXPIRED` and keeps its manifest, checksum, row counts and download history.
- Discarding writes a `RETENTION_ACTION` audit entry naming the bundle, its
  checksum and the rule applied, so "this copy was destroyed on this date
  under the 30-day rule" is evidenced per bundle. A sweep that found nothing
  writes no entries — its evidence is the command's own return value and log
  line, not a daily row saying nothing happened.

### 2.3 Platform audit

Retained **indefinitely and immutably**. `PlatformAuditLog` refuses edits and
deletions at the model level, and is the one table that must survive a
customer leaving — it is how the platform answers "what did you do to this
customer, and who authorised it" after the customer and their data are gone.

An archive, a restore, an export and a retention action are all recorded
there, so the lifecycle of a tenant is reconstructable from the audit trail
alone.

## 3. Soft-delete strategy

The platform uses **state, not tombstones**.

There is no `is_deleted` column on tenant models and none is planned. The
reasons are specific rather than stylistic:

1. **A flag that must be checked is a flag somebody will forget.** The
   isolation guarantee on this platform is `organization_id` plus a
   PostgreSQL RLS policy — one predicate, enforced in the database, on 106
   tables. A second predicate that only the ORM knows about would be a second
   boundary with no database behind it, and the first query that forgot it
   would show deleted records to the customer.
2. **Deletion is a question about a whole workspace, not a row.** "This
   customer has left" is one decision about one organization. Expressing it as
   a few hundred thousand row flags is a worse representation of the same
   fact, and a much slower one.
3. **The archive already is the soft delete.** `Organization.status =
   ARCHIVED` turns off admission for every user in one place, the RLS policy
   keeps the rows invisible to every other tenant regardless, and
   `status_before_archive` makes the undo exact.

So: **archive is the soft delete, and it is reversible by design.**

## 4. Recovery windows

| Action | Recoverable | Window | How |
|---|---|---|---|
| Suspend | yes | unlimited | `console.activate` |
| Cancel | yes | unlimited | win-back: `CANCELLED → ACTIVE/TRIAL` |
| Archive | yes | unlimited while not purged | `console.restore_organization`, returns the tenant to `status_before_archive` |
| Export bundle discarded | yes | regenerate at any time from live data | run a new export |
| Purge | **no** | — | not built; see §6 |

**Archive has no expiry on purpose.** A recovery window that silently becomes
a deletion window is how customers lose data in other people's platforms: the
archive was taken *because* somebody wanted the data kept, and a timer that
converts that into a purge contradicts the reason the archive exists. Purging
an archived tenant must always be a separate, explicit, authorised decision.

## 5. Compliance rules

These are the rules the platform follows now, as implemented:

1. **Right of access / portability.** A tenant's complete dataset — every one
   of the 106 tenant-scoped tables, plus the platform's own records about
   them, plus every referenced media file — is exportable in one operator
   action, in both a machine-restorable form (JSON, ids preserved) and a
   human-readable one (CSV), with a manifest and per-file SHA-256 checksums.
   Implemented: `tenancy/export.py`.
2. **Integrity of what is handed over.** No partial exports: a bundle is
   downloadable only if every table was written and every foreign key between
   exported tables resolves inside the bundle. A failure produces a receipt
   explaining why and no file. Implemented.
3. **Isolation during all of it.** The export reads through the tenant-scoped
   managers inside `tenant_context`, never `all_tenants`, and the integrity
   pass re-checks every exported row's `organization_id`. Under RLS the
   database enforces the same boundary independently.
4. **Credentials are never exported.** Sessions, refresh tokens and the
   blacklist are excluded by name with a recorded reason; password hashes
   travel in the `users.User` rows because they are part of the customer's own
   data and the bundle is handed only to the customer, but a bundle must
   therefore be treated as a credential store — see §5.7.
5. **Least exposure for the bundle.** Export files live outside the tenant
   media tree, under `platform/`, which `documents.protected_media` refuses to
   sign or serve at both ends. The only route to a bundle is a
   platform-authenticated, audited download.
6. **Every access is recorded.** `EXPORT_CREATED` and `EXPORT_DOWNLOADED` are
   separate audit actions, because "we produced an export" and "somebody took
   a copy of a customer's entire dataset off the platform" are different
   questions, and the second can happen repeatedly, months later, by a
   different operator.
7. **Operational handling of a bundle** (process, not code): transfer to the
   customer over a channel the customer nominates, never email; confirm
   receipt; discard at `expires_at`. A bundle contains password hashes and
   personal data for every employee of that customer.
8. **Data residency** is out of scope at this phase. The platform runs in one
   deployment; a customer with a residency requirement needs the
   `storage_prefix` seam on `Organization` (already present, unused) and is a
   later phase.

## 6. Purge: what would have to be true before it is built

Not built, and this is the specification a later phase should be held to.

1. **A written, authorised request** from the customer, recorded against the
   organization, naming who asked and on what authority.
2. **An export taken and confirmed collected first**, referenced by id and
   checksum in the purge record. Purging without a verified handover is data
   destruction, not data erasure.
3. **A cooling-off period** between authorisation and execution, with the
   tenant archived throughout, during which the decision can be withdrawn.
4. **Two-operator authorisation.** One operator must not be able to purge a
   customer alone; the platform has no second channel for that today.
5. **A purge that is itself auditable after the fact**: the
   `PlatformAuditLog` entries and the `TenantExport` receipts survive the
   purge, so the platform can still answer what existed, what was handed over
   and who authorised its destruction — with no customer data in those
   records beyond the organization's name, slug and counts.
6. **Order of operations**, because referential integrity makes this
   unforgiving: media files first (they are unreferenced after their rows go),
   then tenant rows in reverse dependency order — Phase C, then B, then A —
   then the platform-side rows, then the organization. `Organization` uses
   `PROTECT` from tenant tables precisely so a half-purge cannot orphan rows;
   that protection is a feature here, not an obstacle.
7. **A dry-run mode** that reports exactly what would be removed, and a test
   that the dry run's counts equal the real run's counts on a seeded tenant.

Until all seven exist, the honest answer to "can you delete a customer
completely?" is **no, and deliberately not** — the platform can close a
workspace, preserve it, and hand its contents back.

---

## Appendix: where each promise lives in the code

| Promise | Code |
|---|---|
| Archive closes a workspace, preserves everything | `tenancy/archive.py` |
| An archived tenant cannot change | `console._refuse_if_archived` |
| A renewal cannot reopen an archive | `tenancy/lifecycle.py:apply_derived` |
| Restore is exact | `Organization.status_before_archive` |
| Export covers every tenant table | `tenancy/export.py` + `tenancy/inventory.py` |
| No partial exports | `tenancy/export.py:_check_integrity` |
| Bundles are not publicly addressable | `documents/protected_media.py` (`platform/` refused) |
| Every export and download is audited | `PlatformAuditLog.Action.EXPORT_*` |
| Bundle expiry | `TenantExport.expires_at`, `RETENTION_ACTION` |
| Nothing deletes tenant data | absence of any such code path; asserted by `test_retention.py` |
