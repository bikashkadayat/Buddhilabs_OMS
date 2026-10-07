# Customer Support Runbook

**Phase:** S6.75 Part 8.
**Audience:** whoever answers the platform's support address. Assumes access
to the platform console and to application logs, but **no** database access
and no engineering knowledge.

Each scenario is written the same way: what the customer says, what to look
at, what to do, and what not to do.

---

## How to use this

Three things are true of almost every ticket here, so they are stated once:

1. **The console is the tool.** Nearly everything below is answerable from
   Platform → the customer's page. If a scenario needs a shell, that is noted
   as an escalation, not a step.
2. **Read the workspace health panel first.** `Tenant Ready` /
   `Provisioning Incomplete` plus the configuration gaps answers most
   "nothing works" tickets in one glance.
3. **Every action you take is recorded against that customer**, with your
   email on it, and the customer may later ask what happened. Put the reason
   in the reason field; it is the only part of the record a human wrote.

**Before touching anything, check the platform itself.** Platform → Health → Launch
readiness. If a critical check is failing, the ticket in front of you is
probably a symptom and there will be more of them shortly.

---

## The seven support workflows (Phase S11 Part 8)

| Workflow | Section | First thing to look at |
|---|---|---|
| Registration support | §1, §2, §3 | Platform → Dashboard → Signups |
| **Login support** | **§4** | which hostname they are using |
| Provisioning support | §3 | Platform → the tenant → Health |
| Billing support | §5, §5b | Platform → Payments |
| Domain support | §5c | Platform → Domains, the `Why not` column |
| Export support | §6 | Platform → the tenant → Exports |
| Restore support | §7 | Platform → the tenant → Health |

Escalation levels and what never to do:
[operator-runbook.md](operator-runbook.md) §5.

**Two things to know before answering anything.** Customers never see
internal errors — by design — so "it just said something went wrong" is the
expected report, not a vague one; the cause is in the platform log and the
error tracker. And every console action you take is audited against that
tenant with your name and your reason, and the reason is what the customer
is shown if they query it.

## 1. "I registered and never got the email"

**Likely causes, in order of how often they happen:** the mail went to spam;
the mail provider is down; the address was mistyped; public registration is
on but SMTP is not configured at all.

**Look at:**
- Platform → Health → Launch readiness → **Email provider**. If it is failing, this is
  not one customer's problem — stop and fix the platform.
- Application logs for `verification email to <address> failed`. The send is
  logged and swallowed deliberately: a bounced email must not 500 a signup
  form, and the response must not reveal whether mail to that address
  succeeded.

**Do:**
- Ask them to check spam, and to confirm the address.
- Have them **submit the form again** with the same address. That is
  supported: it replaces the previous link, re-sends, and does not create a
  second registration. It is capped at `TENANCY_VERIFICATION_MAX_SENDS`
  (default 3) attempts.
- If the cap is reached, the subdomain reservation expires on its own
  (`TENANCY_VERIFICATION_TTL_HOURS`, default 24) — or ask an engineer to run
  `manage.py tenancy_expire_registrations`, which releases held subdomains
  whose links have expired.

**Do not** create the workspace for them from the console as a workaround
unless they ask. Their email address would then be unverified, which is the
one thing the registration flow exists to establish.

## 2. "The link says it is not valid / has expired"

**Look at:** nothing, usually. Both messages are normal and expected.

**Know why, so you can explain it:**
- Registering again **invalidates the previous link**. If they registered
  twice, only the newest email works.
- Links expire after 24 hours by default.
- Clicking twice is fine: the second click answers "already confirmed" and
  shows the workspace. It does not create a second one.

**Do:** have them register again. Nothing was created, so nothing is lost and
the subdomain is still theirs to claim.

## 3. "I confirmed my email but there is no workspace"

This is the serious one: it means verification succeeded and provisioning did
not. The customer has been told their email is confirmed.

**Look at:**
- The response they saw. It should have read *"Your email is confirmed, but we
  could not finish setting up your workspace. Nothing has been lost — please
  contact support."* If they saw anything with the word "plan" or a stack
  trace in it, escalate: the customer-facing message leaked an internal one.
- Application logs for `self-service provisioning of <slug> failed`. That
  line carries the real reason.
- Platform → Health → Launch readiness. **Purchasable plans** and **Tenant
  provisioning** are the usual culprits.

**Do:**
1. Fix the platform fault (almost always: no purchasable plan, or storage not
   writable).
2. Tell the customer to click the same link again. The registration rolls
   back to `pending` on failure precisely so the link keeps working.
3. If the link has since expired, have them register again with the same
   details.

**Do not** hand-build the organization in the console and tell them it is
done, unless you also set their password — the account created that way has a
temporary password they do not know, and the one they chose at registration
will not work.

## 4. "Nobody at our organization can sign in"

**Look at:** Platform → the customer → the status badge.

| Status | What happened | What to do |
|---|---|---|
| `suspended` | an operator suspended them; the reason is on the page | if the dispute is settled: **Activate** |
| `archived` | an operator archived the workspace | **Restore workspace** — see the disaster recovery doc §1 |
| `grace` | subscription lapsed, still admitted | they can sign in; look for a different cause |
| `cancelled` | commercial end | **Activate** to win them back; no data was deleted |
| `trial`/`active` but `is_admitted` false | mirror drift | Platform health → **Correct mirrors** |

**If the status looks right and they still cannot get in:** confirm they are
using **their own address** — `<slug>.<platform domain>` — and not the
console host. A tenant account is refused on the console by design, and a
platform operator's account is refused on a tenant host.

## 4b. Login support — the hostname cases

Added in Phase S11. In a multi-tenant deployment most "I cannot log in"
reports are not about the password. **Ask which address they are using
before anything else.**

| What they say | Likely cause | What to do |
|---|---|---|
| "It says my account does not belong here" | Signing in on **another tenant's** hostname, or on the console host | Give them their own address: `<slug>.<platform domain>`, or their custom domain |
| "It accepted my password then logged me straight out" | A token from one workspace replayed on another — usually two tabs, two tenants | Ask them to sign out everywhere and use one address per browser profile |
| "The page will not load at all" | Their custom domain has no certificate, or DNS is not pointing here | §5c, and check Platform → Domains |
| "It says too many attempts" | Lockout after `LOGIN_MAX_FAILURES`, 15 minutes | Administration → Users → **Clear lockout**. Do not reset their password to work around it; the lockout is also what an attack looks like |
| "Nobody at our organization can sign in" | Workspace closed — see §4 | §4 |
| An **operator** cannot reach the console | Signing in on a tenant host | The console is served only on the platform hostname, deliberately |

**Never** "fix" a login by moving a user between organizations. A user
belongs to exactly one workspace and the database enforces it; the request
behind that ask is usually a second account.

## 5. "Our subscription/plan is wrong"

**Look at:** the customer's page → Subscription panel, and the Subscription
history table below it. Every change is there with who made it and why.

**Do:** use the verb that matches the decision — **Assign plan**, **Change
plan**, **Extend**, **Start trial**. Each records its own reason. Do not try
to fix a wrong state by applying several in sequence until it looks right;
the trail is what the customer will be shown if they query the bill.

**If the subscription and the workspace status disagree** (paid up but locked
out, or lapsed but admitted): that is mirror drift. Platform health →
**Correct mirrors**, which repairs it and audits the repair per customer.

## 5b. "We sent the money and nothing happened" (payment failure)

Added in Phase S10. The platform takes **no online payments** — there is no
gateway, by design — so every payment is a human verification, and "nothing
happened" almost always means the verification step is waiting on us.

**Look at:** Platform → Payments (the Payment Center). The **To review** tab
is ordered by age. Find the customer's reference.

**The states and what each one means:**

| State | What it means | What to do |
|---|---|---|
| `awaiting_proof` | We opened a payment; the customer has not uploaded a receipt | Tell them where to upload it (their Settings → Subscription page). Nothing is wrong. |
| `submitted` | They uploaded a receipt; **nobody has looked** | The common case. Decide it in the Payment Center (below). |
| `under_review` | An operator is deciding it | "Being reviewed by" on the card says who. The console will not let you take it from them; ask them. |
| `needs_info` | We asked the customer for something | Tab **Waiting on customer**. They answer by uploading again, and it returns to **To review**. |
| `verified` | Money confirmed, subscription moved | If the customer still cannot work, it is mirror drift — see §5. |
| `rejected` | We looked and declined | The reason is on the record and was emailed to them. Read it to them. |

### Deciding a payment (Payment Center)

**Changed in the billing-operations phase.** An earlier version of this
section was a Django-shell procedure, because the console was read-only.
Payments are now decided in the console by any platform operator. No server
access is needed.

1. **Platform → Payments → To review.** Each card shows the organization,
   plan, amount, method, their transaction ID, the date they say they paid,
   who sent it and when, and their note.
2. **View receipt.** It opens in the console, from a platform-authenticated
   request. It is never a public link, and it is served so it cannot run
   scripts.
3. **Match the amount against the card**, not the plan page. The amount was
   fixed server-side when the customer requested the plan.
4. **Decide:**
   - **Approve.** Add an optional note, then confirm. One step: the
     subscription activates (or extends), a subscription event and an audit
     entry are written, revenue figures update, and the customer is emailed.
     Approving twice is refused, so a double-click cannot buy two terms.
   - **Ask for more.** Say exactly what is missing. Nothing is rejected; the
     customer sees your words and uploads again.
   - **Reject.** A reason is required. The customer reads it word for word,
     with what to do next.
5. Put the reference and the decision in the ticket.

**Never:** hand-edit a subscription to "fix" a payment. Use the Payment
Center; it is what writes the audit trail the customer will be shown if they
query the bill.

**If the amount is wrong:** reject with the reason, and ask them to request
the plan again. A payment carries the amount it was opened with; there is no
verb for editing it, on purpose.

**Where customers are told to pay** is Platform → Payment methods: bank
accounts, eSewa, Khalti, Fonepay and QR codes. **Add**, **Edit**, **Hide
from customers**, **Archive** and **Restore** live there. If no method is
shown to customers, their subscription page tells them what they owe and
gives them no way to pay it, and the Payment methods page warns you.

**Fallback, only if the console is down:** the same service functions can
be run from `python manage.py shell` inside `no_tenant()`. Approval is
`payments.claim_for_review(payment, operator)` and then
`payments.verify_payment(...)`; rejection is `payments.reject_payment(...,
reason=...)`. A shell decision does **not** email the customer or write the
platform audit entry, so do both by hand.

## 5c. "Our own web address does not work" (domain verification failure)

Added in Phase S10; the capability arrived in Phase S9. **Nine times out of
ten this is one of two DNS records being missing, and the customer believes
they published both.**

**First, decide which record they are missing.** Settings → Custom domain
shows the customer two records and they do different jobs:

* the **verification** record (TXT or CNAME, at a `_`-prefixed name) proves
  the domain is theirs;
* the **serving** record (CNAME, at the hostname itself) is what actually
  sends visitors to us.

Publishing the first and not the second gives "verified, but the address
still does not load". Publishing the second and not the first gives "the
address loads somebody else's error page and we are not verified".

**Look at:** Platform → Domains. The columns that matter:

| Column | Read it as |
|---|---|
| `Checks` | How many times they have pressed "Check now". **Three or more is a customer who is stuck and will not get there alone.** |
| `Why not` | The exact lookup that failed, including what WAS found at that name. |
| `dns_available` banner | If this says DNS lookups are unavailable, **stop** — the problem is ours, not theirs. See below. |

**Do:**
1. Read `Why not` to them literally. If it says "Found: <something else>",
   they have published the record at the right name with the wrong value —
   usually their DNS panel appended their domain to a name that already
   included it, producing `_nifn-verify.hr.example.com.example.com`.
2. If it says "Nothing found", ask them how long ago they published it. DNS
   changes take up to an hour to spread; the page says so, and pressing
   "Check now" more often does not make it faster.
3. Press **Check now** from the console once propagation has had time.
4. If it verifies, the address begins serving immediately on the worker that
   verified it — **and up to a minute later on the others**, because the
   host cache is per-process unless Redis is configured. If the customer
   reports it working intermittently for the first minute, that is this, and
   it resolves itself.

**If the banner says DNS lookups are unavailable:** the deployment has no
resolver installed (`dnspython`). Every check answers "we could not look",
which the API reports as **503, not 400** — deliberately, so nobody tells a
customer their records are wrong when we never checked. Escalate; do not ask
the customer to change anything.

**Never:** mark a domain verified by hand. There is no verb for it and that
is the point — a hostname is a claim about something outside this platform,
and an operator asserting it is exactly the unverified-column arrangement
Phase S9 replaced. If a customer genuinely cannot publish a record, the
answer is that they keep using their `*.<platform domain>` address.

**"It verified but the browser says the connection is not private."** That is
TLS, not DNS, and it is expected today: the platform's wildcard certificate
covers `*.<platform domain>` and cannot cover a customer's own hostname.
Somebody has to issue a certificate for that hostname at the proxy before the
address works in a browser — see the deployment checklist. Because
`SECURE_SSL_REDIRECT` is on, an un-certificated custom domain redirects to an
`https://` URL that cannot complete, so the customer sees a TLS error rather
than anything explanatory. **Escalate; do not ask the customer to change
their DNS.**

**Taking a domain back:** Platform → the customer → Domains → withdraw. Use
this if a hostname changes hands or a claim was made in error. It is audited
against the tenant that held it, and the address stops resolving at once.

## 6. "We need a copy of our data" / "We are leaving"

**Do:**
1. Confirm the request comes from someone authorised at the customer — an
   administrator on their own account, not any employee.
2. Console → the customer → **Data and archive** → **Export data**
   (Everything).
3. Check the receipt: status `ready`, and a row count that is not suspiciously
   small.
4. **Download**, verify the SHA-256 against the receipt, and transfer over a
   channel they nominate. **Never email it.** The bundle contains password
   hashes and personal data for every one of their employees.
5. Record the handover in the ticket. The platform records that the export was
   created and that it was downloaded, separately, with your email on both.

**If they are leaving:** export first, confirm they have it, and only then
**Archive workspace** with a reason. Archiving preserves everything and is
reversible. There is no "delete this customer" button, deliberately — see
`docs/tenant-retention-and-deletion-policy.md` §6.

## 7. "Please restore our workspace / our data"

- **Workspace archived:** Restore. Seconds, no data loss. §1 of the disaster
  recovery doc.
- **A record they deleted themselves:** not a platform operation. Their own
  administrators have the audit trail; the platform does not restore
  individual tenant records and must not, because "somebody deleted a leave
  request" is indistinguishable from "somebody tidied up".
- **"Load our export back in":** **not supported.** The platform can produce
  a bundle and cannot import one. Escalate to engineering; do not promise a
  timeline.

## 8. When to escalate rather than act

Escalate if any of these is true. They all mean the platform, not one
customer, is wrong:

- Launch readiness shows a failing **critical** check.
- A customer received an internal error message — a stack trace, a Django
  page, or anything naming our plans, tables or settings.
- Two customers report the same thing within an hour.
- An organization exists with no subscription, or a subscription with no
  organization. (Provisioning is atomic, so this should be impossible; if it
  has happened, something bypassed it.)
- Any request to delete a customer's data permanently. There is no mechanism,
  and the conditions one would need are written down in the retention policy.

## Quick reference

| Need | Where |
|---|---|
| Is the platform itself healthy? | Platform → Health → Launch readiness |
| Is this customer's workspace complete? | their page → Workspace health |
| What did we do to this customer, and who? | their page → Platform actions |
| What did they do about their own billing? | their page → Subscription history |
| How many signups/logins/exports lately? | Platform → Dashboard → events |
| Release a stuck subdomain | `manage.py tenancy_expire_registrations` |
| Discard expired export bundles | `manage.py tenancy_expire_exports` |
| Re-check the platform from a shell | `manage.py launch_readiness --strict` |
