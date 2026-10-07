# Platform Admin Experience Redesign

**Scope** — the Platform Console only (`/platform/*`), the application a Buddhi
Labs operator uses to run the SaaS. No tenant-facing screen is touched by any
change in this document.

**Status** — built, tested and running. Nothing committed; nothing pushed.

---

## 1. What was wrong

The console was built in Phase S6 as a working surface and never revisited. It
had eleven pages reached from a flat sidebar, and three gaps that mattered more
than they looked:

| Gap | Consequence |
| --- | --- |
| No account surface at all | The account with the most power in the product had no profile page, no visible identity, and no route to its own password. An operator could not change their password from the console. |
| No way to see what needed doing | Five queues existed (payments, provisioning, domains, renewals, mirror drift) and each lived on its own page. Knowing whether anything was waiting meant opening five pages. |
| A flat rail of ten to eleven links | A list to be read rather than a place with rooms. Launch Readiness — consulted at deploy time and almost never after — had the same permanence as Organizations. |

The dashboard listed figures without ranking them, so "3 tenants stuck
provisioning" sat in the same visual weight as "47 organizations".

---

## 2. Top navigation bar

`frontend/src/components/platform/PlatformTopbar.jsx` (new, 186 lines).

A 60px bar above everything, carrying what used to be scattered or missing:

- **Brand** — the Buddhi Labs mark plus "Platform Console" as a subtitle, linking to the dashboard. The mark, not the wordmark lockup, because the lockup smears at this size.
- **Search** — one field, submitting to `/platform/organizations?search=…`. Organizations is the only list worth searching from a global bar; everything else in this console is reached *through* an organization.
- **Notifications** — a bell opening the panel in §4.
- **Theme toggle** — light/dark, persisted in `localStorage` under `platform:theme`.
- **Profile menu** — avatar initials, name, and a menu: My profile · Security & password · Activity log · Platform settings · Sign out.

Both popovers close on an outside click and on `Escape`, through one shared
`useDismiss` hook rather than two copies of the same listener. Opening one
closes the other. `aria-haspopup` / `aria-expanded` are set on both triggers.

The layout changed shape to accommodate it: the shell was a sidebar *beside* a
page, and is now a top bar *above* a body. That change is the source of the one
real bug found during this work — see §9.

## 3. Profile page

`frontend/src/pages/platform/Profile.jsx` (new, 214 lines) at `/platform/profile`.

Three sections:

1. **Identity** — name, email, role badge ("Platform staff"), last sign-in.
2. **Security** (`id="security"`, so the menu's "Security & password" item deep-links to it) — change password, and a short list of what protects the account: the password rules in force, the lockout threshold, and the fact that every console action is audited.
3. **Activity** — this operator's own recent entries from the platform audit log.

The page reuses the existing `/api/v1/auth/` endpoints. No new backend surface
was added for it: an operator's profile and password are the same endpoints a
tenant user's are, and the console simply never offered them.

## 4. Notification centre

`frontend/src/components/platform/PlatformNotifications.jsx` (new, 144 lines).

**Derived, not stored.** There is no notifications table for platform staff and
this does not add one. Five conditions are read from the three endpoints the
dashboard already calls, ordered by who is inconvenienced:

1. Payments awaiting verification — *a customer has paid and is waiting on us*
2. Tenants stuck provisioning — *a customer who cannot work*
3. Subscription mirror drift — repairable from Platform health
4. Custom domains not verifying — failed, or pending after three checks
5. Subscriptions expiring within 30 days

That is a deliberate trade, and worth stating plainly. A stored feed would
survive a refresh, support read/unread, and could be delivered by email. It
would also be a second copy of the truth that can disagree with the pages it
links to, and "the bell said three but the queue shows none" is the failure
that teaches people to stop opening the bell. Until read state is actually
needed, the queue *is* the notification.

The empty state names what was checked — "Payments, provisioning, domains and
renewals are all clear" — because a bare "nothing here" reads as "not loaded
yet".

## 5. Executive dashboard

`frontend/src/pages/platform/Dashboard.jsx` (rewritten, 308 lines).

Six bands, in the order an operator's attention should go:

1. **Attention strip** — anything from `health.problems`, each row linking to the page that fixes it. Absent entirely when there is nothing wrong, rather than rendering an empty box.
2. **Metric band** — the headline figures, each one a link to the list behind it.
3. **Lifecycle row** — the six organization states (trial, active, in grace, suspended, …) as a quiet band under the headlines. Small because it is a breakdown; present because "in grace" is the state that becomes a support call if nobody looks.
4. **Quick actions** — create an organization, verify a payment, add a plan, open health.
5. **Feeds** — newest organizations, recent platform activity, signups.
6. **System health** — the launch-readiness summary, which is also where Launch Readiness now lives (§6).

Colour appears only where a figure needs a human: the page is greyscale until
something is amber or red. A dashboard where everything is coloured has no way
left to say "this one".

## 6. Sidebar, cleaned up

`frontend/src/components/platform/PlatformLayout.jsx`.

Ten entries, in three groups, ordered by how often an operator opens them:

| Group | Items |
| --- | --- |
| *(ungrouped)* | Dashboard |
| Customers | Organizations · Subscriptions · Payments · Domains |
| Platform | Plans · Usage · Health · Audit · Settings |

Removed from the rail: **Launch Readiness** and **Platform Metrics**.

Launch Readiness is a deploy-time pre-flight check consulted twice a year, and
a permanent rail entry for that is how a rail stops being scannable. It is now
reached from **Platform → Health**, where somebody asking "is the platform all
right" already is, and from the dashboard's System health card. The route is
unchanged.

> Worth recording, because it was nearly shipped wrong: removing the rail entry
> left the page reachable only by typing the URL. The Health page had no link to
> it — the only one was on the dashboard — while this report and the support
> runbook both told operators to look under Platform → Health. Found by checking
> the documentation against the code rather than by a test. The link is now on
> the Health page, and `consolePages.test.jsx` asserts it, so the sentence in
> the runbook stays true.

Platform Metrics was the dashboard's own figures under a second name, which
meant two screens to keep in agreement.

Ten undifferentiated links read as a list to be searched. Three groups of three
or four read as a place with rooms in it.

## 7. Mobile

Below 1024px: the rail becomes a drawer behind a hamburger in the top bar, with
a scrim; the search field and the operator's name are dropped from the bar
(the avatar and both popovers stay). Below 640px: page headers stack, and the
activity feed collapses from two columns to one.

## 8. Visual refinement

- **One elevation** — every card sits on a single soft shadow plus a hairline border. Mixed shadow depths are what make a page look assembled from different templates.
- **One spacing rhythm** — 24/32px between sections instead of 8px everywhere, so sections separate without borders doing the work.
- **Dark theme, console only** — scoped to `.pf-shell[data-theme="dark"]` as a token override. A dark palette for the tenant application would mean auditing several hundred components, and a half-converted dark mode is worse than none. The console is one shell and eleven pages.
- Every size comes from the existing type and spacing tokens; the design-system test that forbids off-scale literals still passes.

---

## 9. Bugs found and fixed while doing this

Five, all pre-existing or self-inflicted, all found by looking rather than by a
failing test.

### 9.1 A platform operator's session could not be refreshed — HTTP 500

Found by reading the live server's log, not by a test.

`/api/v1/auth/refresh/` answered **500 for every platform operator**. SimpleJWT's
`TokenRefreshSerializer.validate` looks the token's user up with
`get_user_model().objects.get(...)` — the *tenant-scoped* manager. Refresh runs
before authentication (the token is the credential), and on the console's own
hostname no tenant is bound by design, so the scoped read raised
`TenantScopeMissing`.

The effect: an operator was signed out whenever their access token expired, with
a 500 in the log and nothing on screen to explain it.

**The same bug, on the other end of the session**, found while fixing the first:
`LogoutView` calls `RefreshToken(...).blacklist()`, and SimpleJWT's
`blacklist()` and `outstand()` *also* look the user up with `User.objects`, to
fill `OutstandingToken.user`. Signing out answered 500 too — and because the
500 came from the blacklist write itself, the token was left **valid**. "Log
out" revoked nothing.

**Fix** — `tenancy.tokens.TenantSafeRefreshToken`, a `RefreshToken` subclass
whose `blacklist()` and `outstand()` read the user with `all_tenants`, plus a
`TenantSafeTokenRefreshSerializer` in `users/token_serializers.py` for the
serializer's own inline lookup. All four call sites (login, refresh
pre-validation, refresh rotation, logout) now go through the subclass.

Why `all_tenants` and not a tenant scope: there is none that would work. A
platform operator's `User` row has `organization IS NULL`, so there is nothing
to bind, and `no_tenant()` does not help — `scoping._apply_pending_tenant_scope`
refuses whenever no organization is bound and never consults
`tenant_explicitly_unset()`. `all_tenants` is this project's one sanctioned
cross-tenant read, and `tenancy.authentication.TenantJWTAuthentication.get_user`
already reads the user that way for exactly the same reason.

Nothing is widened by it. The id comes from a signed token, so this decides
only *which id is being asked about*, never which rows the caller may see;
under row-level security the connection still governs visibility; the tenant
binding is still enforced at authentication, where `_refuse_wrong_tenant`
rejects a token presented on another tenant's hostname; and `OutstandingToken`
is already classified platform-global in `tenancy/inventory.py`.

Tests: three in `tenancy/tests/test_enforcement_live.py`, on the console's own
hostname — refresh succeeds, the old token is really blacklisted and attributed
to the operator, and sign-out revokes for real. Each was confirmed to fail
against the unfixed code.

### 9.2 The mobile drawer slid in sideways

Self-inflicted, by the layout change in §2. The console's old stylesheet had an
`@media (max-width: 860px)` block that turned the sidebar into a horizontal
scrolling strip. Its markup is gone; its rules were not. Both queries matched at
phone width, and the new drawer block only sets `position` / `transform` /
`transition` — so the old rules still won `flex-direction: row`, `width: auto`
and `overflow-x: auto`. The drawer slid in as a sideways strip of nav items.

Nothing failed. It just looked wrong, on the one screen size nobody was looking
at. Fixed by deleting the rules for a layout that no longer exists, along with
four now-dead classes (`.pf-brand`, `.pf-brand-name`, `.pf-brand-mark`,
`.pf-who`), and by stating the shell's background where the shell is laid out
rather than inheriting it from the rule the old layout left behind.

Two guards added in `console.test.jsx`, both confirmed to fail when violated:
no rule anywhere lays `.pf-side` out as a row, and no `pf-` class is defined in
the stylesheet that no component uses.

### 9.3 A platform operator who went to `/` landed in the tenant application

Found while checking that the new profile page's endpoints work for an
operator, which they do — `/api/v1/profile/me/` and
`/api/v1/auth/change-password/` both answer correctly. The next endpoint along
did not.

`RequirePlatform` already bounces a tenant user out of `/platform`. **Nothing
bounced an operator the other way.** An operator who typed the root URL, or
followed an old bookmark, mounted the *tenant* application with a session bound
to no tenant — and the tenant shell's first act is to call tenant endpoints.
`/api/v1/notifications/` answers **HTTP 500** for exactly that reason.

The 500 itself is correct and deliberate: it is `TenantScopeMissing`, which
`tenancy/exceptions.py` describes as the signal for *a bug*, not a refusal —
"raised rather than silently defaulting; a default here is how one tenant's rows
end up stamped with another tenant's id". Making it quiet would hide real
scoping defects. What was wrong was that an operator could reach it at all.

Fixed in `RequireAuth`, as the mirror of `RequirePlatform`: platform staff are
sent to `/platform`. The forced-password-change redirect still takes
precedence, so an operator created with `must_change_password` cannot skip it —
asserted, because getting that order wrong is how a redirect becomes a hole.

**Left open, and recorded in the launch report rather than fixed here:** a
platform session reaching a tenant API answers 500 rather than 403. The clean
fix is a permission that refuses a tenant-bound endpoint to a platform session,
which is a change across the whole API surface and the wrong thing to do late
in a redesign.

### 9.4 The support runbook described buttons that do not exist

Found by checking the documentation against the code. `docs/support-runbook.md`
§5b — which I wrote in Phase S11 — told an operator to *Claim for review*,
*Accept* or *Reject* a payment in the console. Those controls do not exist:
`Payments` is read-only by an explicit decision recorded in the page itself,
there is no endpoint behind them, and the Django admin keeps `Payment.status`
read-only on purpose. The state machine, the reviewer lock and the idempotency
guard are all built and tested in `tenancy/payments.py`; only the way to reach
them is missing.

So the one procedure a manual-bank-transfer platform cannot do without was
documented as a UI walkthrough of a UI that is not there. §5b now says so
plainly and gives the shell procedure that does work. Building the endpoint is
explicitly out of scope for this brief and is the top item in the launch report.

Three stale console references were corrected at the same time, all introduced
by this redesign's own nav changes: *Launch readiness* and *Custom domains* no
longer exist under those names or in those places, and *Registration funnel* is
a card on the dashboard rather than a page.

### 9.5 A test passed while throwing

`newOrganization.test.jsx` mounted the component without a Router, and its
success state renders a `<Link>`. React Router destructured a null context and
threw — *after* the assertion had passed, so vitest reported "1547 passed, 2
errors" and the suite looked clean. Fixed by mounting it in a `MemoryRouter`,
the way the application mounts it.

---

## 10. Files

**New**

```
frontend/src/components/platform/PlatformTopbar.jsx        186
frontend/src/components/platform/PlatformNotifications.jsx 144
frontend/src/pages/platform/Profile.jsx                    214
frontend/src/pages/platform/Settings.jsx                   121
frontend/src/components/platform/console.test.jsx          ~160
backend/tenancy/tokens.py  — TenantSafeRefreshToken (added to an existing file)
```

**Changed**

```
frontend/src/components/platform/PlatformLayout.jsx   grouped nav, drawer, theme
frontend/src/pages/platform/Dashboard.jsx             rewritten (308)
frontend/src/index.css                                pf- block; stale rules removed
frontend/src/App.jsx                                  /platform/profile, /platform/settings
frontend/src/components/platform/newOrganization.test.jsx  mounted in a Router
frontend/src/components/common/RequireAuth.jsx        operators go to the console
frontend/src/components/common/RequireAuth.test.jsx   3 tests for that
frontend/src/pages/platform/Health.jsx                Launch readiness lives here
frontend/src/pages/platform/consolePages.test.jsx     1 test for that
backend/users/token_serializers.py                    tenant-safe refresh + logout
backend/tenancy/tests/test_enforcement_live.py        3 session tests
docs/support-runbook.md                               §5b rewritten; stale nav refs
docs/production-deployment.md                         stale nav refs
docs/custom-domain-operations.md                      stale nav refs
```

No database migration. No new backend endpoint. No tenant-facing file.

---

## 11. Verification

Measured after the last product change in this phase (the refresh-token fix in §9).

| Run | Result |
|---|---|
| Backend, SQLite settings, full suite | **4690 passed, 0 failed** |
| Backend, PostgreSQL 16 with row-level security, connected as `nifn_app` (no BYPASSRLS, not the owner), full suite | **4731 passed, 0 failed** |
| Frontend, full vitest suite | **1553 passed, 0 unhandled errors** (previously 2, both from components rendered with no router) |
| Live drive against Daphne on the console's own hostname: sign in, refresh, replay the old refresh token (refused 401), sign out (205), use the revoked token (refused 401) | Passed for a **platform operator** and for a **tenant admin**; **0** `Internal Server Error` in the server log |
| S11 production drill, re-run after this redesign (see `launch-report.md` §2) | **34/34**, 0 server 5xx |

Before the §9 fix the live drive failed at the second step: refresh answered 500 for a platform operator, and so did sign-out.

---

## 12. Not built, deliberately

- **A stored notification feed.** §4 explains the trade. Revisit when read state or email delivery is actually wanted.
- **Dark mode for the tenant application.** Several hundred components; a half-converted dark mode is worse than none.
- **Global search across subscriptions, payments and audit.** The top bar searches organizations only. Everything else in this console is reached through an organization, and a search box that returns four kinds of thing needs a result-type UI that nothing yet asks for.
- **Operator-to-operator features** (roles among platform staff, invitations, per-operator permissions). The platform has one kind of operator today and the brief did not ask for more.
