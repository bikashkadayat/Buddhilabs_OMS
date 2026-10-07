# Operator Runbook — Data Operations

**Phase S11 Parts 5 and 6.** For the platform team, not for support.

The split: [support-runbook.md](support-runbook.md) is "a customer has
reported something"; this is "I am about to touch a customer's data".

**Every action below is audited against the tenant it affects**, with the
operator who did it. The trail is what the customer is shown if they query
it, so the reason you type is the reason they read.

---

## 0. Three rules

1. **Nothing in this product permanently deletes customer data.** Archive is
   a gate, not a delete. There is no verb that destroys a tenant, by design —
   see §4 and [tenant-retention-and-deletion-policy.md](tenant-retention-and-deletion-policy.md).
2. **Use the verb that matches the decision.** Do not apply several in
   sequence until the state looks right; each records its own reason and the
   sequence is what the customer reads.
3. **Act on the console, not in a shell.** `manage.py shell` bypasses the
   audit trail. If a task needs the shell, it needs a verb.

---

## 1. Export process

**When:** a customer asks for their data; a customer is leaving; before any
change you might need to undo.

1. Platform → the tenant → **Exports** → *Create export*.
2. One request produces the bundle synchronously. The receipt carries the row
   count, byte size and **SHA-256**.
3. Check the manifest's integrity block: `dangling_references` and
   `cross_tenant_rows` must both be empty. **A bundle is not published if a
   dangling reference is found** — a partial export handed to a leaving
   customer is worse than none.
4. Download. The checksum travels in the `X-Export-SHA256` response header so
   the customer can verify the copy they received against the receipt without
   a second request.
5. Give the customer the checksum with the file.

**Retention:** the bundle is discarded after `TENANCY_EXPORT_RETENTION_DAYS`
(30). The **receipt is kept for ever** — so "did we ever send them their
data, and when" stays answerable after the file is gone.

**What a bundle contains:** every tenant-scoped table, plus the
platform-side records *about* that tenant (organization, settings, branding,
subscription, payments, audit entries, custom domains). It does **not**
contain the platform's price list, other customers' anything, or the
registration record's password hash and live token.

**What a bundle cannot do:** be imported back. The platform can always hand a
customer their data and cannot yet take it back (R5). Do not promise a
re-import.

### If an export fails

The receipt records the error and `saas_export_failed` fires. Re-run it;
the common causes are a storage permission and a disk that filled. The
customer is waiting — often one who is leaving — so this is not a
background task.

---

## 2. Archive process

**When:** non-payment after grace; a customer asks to pause; a workspace must
stop being usable without anything being lost.

1. **Export first.** Always. It takes seconds and it is the difference
   between a reversible and an unpleasant conversation.
2. Platform → the tenant → **Archive**, with a reason. The reason is on the
   permanent record.
3. The workspace closes: nobody can sign in, the admission gate refuses, and
   thirteen console verbs refuse to act on it until it is restored.

**Archiving copies nothing, moves nothing and deletes nothing.** It sets a
gate. Row counts before and after are identical, which the recovery drill
asserts.

**It also suspends the expiry notice ladder** — an archived tenant's
administrators cannot sign in to act on a renewal notice, so mailing them
one would be noise.

---

## 3. Restore process

**When:** payment arrives after an archive; an archive was wrong; a customer
returns.

1. Platform → the tenant → **Restore**.
2. The workspace reopens **in the state it was in before** — trial stays
   trial, active stays active. It does not silently become active.
3. Check Platform → the tenant → Health. If the subscription and the
   workspace status disagree, that is mirror drift: Platform health →
   **Correct mirrors**, which repairs it and audits the repair per customer.

Measured: **0.01 s**, 101 tenants in the drill.

### Archived while still subscribed

`saas_restore_needed` fires **critical** when a tenant is archived and its
subscription says active, trial or grace. That is either a restore that did
not complete or a customer archived by mistake. Both need the same phone
call. There is no "restore failed" record to read — a restore either
completes or raises — so this state is the signal.

### Database-level restore

A full point-in-time restore is a different procedure and belongs to
infrastructure: [disaster-recovery.md](disaster-recovery.md). Measured RTO
**2.5 s** for 101 tenants and 1,001 users, with all 106 RLS policies intact
afterwards — which is the thing a restore most plausibly loses.

**Before a production restore, decide about media.** Nothing prunes
`media/org/`. Rolling the database back leaves every file written since
unreferenced: unreachable, but on disk and in every later backup (R6).

---

## 4. Retention and deletion (Part 6)

Full policy: [tenant-retention-and-deletion-policy.md](tenant-retention-and-deletion-policy.md).
In summary:

| Object | Kept | Who can discard it |
|---|---|---|
| Tenant data | indefinitely, including while archived | nobody, through any console verb |
| Export bundle | 30 days | the retention job, audited as `RETENTION_ACTION` |
| Export receipt | for ever | nobody |
| Platform audit log | for ever, append-only | nobody — it refuses edits and deletes |
| Pending registration | until verified, then expired by the nightly job | the job |

**There is deliberately no delete verb.** A platform operator cannot destroy
a customer's workspace from the console. A contractual deletion request is
therefore a **deliberate engineering task with named approval**, not an
operational action — which is the point: the one operation no restore can
undo should not be one click away from a support queue.

### Handling a deletion request

1. Export, and deliver the bundle plus checksum to the customer.
2. Archive, with the request as the reason.
3. Record the request and the approval against the tenant.
4. Hold. If a hard deletion is contractually required, it is scheduled
   engineering work against the written policy, performed with a verified
   backup in hand.

---

## 5. Support escalation process

| Level | Who | Acts on | Escalates when |
|---|---|---|---|
| **L1 Support** | support | the customer's own pages; reading the console | any write to a customer's data is needed |
| **L2 Operator** | platform team | every console verb in this runbook | the fix needs a shell, a migration, or the cause is unknown |
| **L3 Engineering** | engineering | code, data repair, infrastructure | — |

### Escalate to L2 immediately

* a tenant stuck provisioning (`saas_provisioning_stuck`)
* a verified signup with no workspace (`saas_registration_stalled`)
* an archived tenant with a live subscription (`saas_restore_needed`)
* anything involving another tenant's data appearing anywhere

### Escalate to L3 immediately

* **any suspicion of cross-tenant data exposure.** Do not investigate in
  production first. Capture what was seen, by whom, on which hostname, at
  what time, and escalate. This is the one class of incident where the
  platform's whole premise is in question
* `saas_platform` firing — the monitoring itself is broken, so nothing else
  is being watched
* a 5xx rate that does not resolve to the connection budget
* a restore that raised

### What never to do

* Edit a subscription to fix a payment. Use the payment verb.
* Mark a domain verified. There is no verb; see
  [custom-domain-operations.md](custom-domain-operations.md) §6.
* Repair data with `manage.py shell` without an L3 present and a backup
  taken. It leaves no audit trail, which means the next person cannot tell
  what you did.
