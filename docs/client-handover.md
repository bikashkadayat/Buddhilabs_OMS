# Client Handover

**Phase:** Client Handover Experience & Organization Access Delivery.
**For:** platform operators who hand new customers their workspace, and whoever reviews this phase.
**Status:** built and verified. Not committed.

The aim: an operator creates an organization and immediately has everything the customer needs to sign in, ready to copy or email. The customer can then get in without calling support.

---

## 1. Organization creation improvements

**Before.** After creating an organization, the success dialog showed the temporary password and a list of configuration counts. The login URL, the administrator's address, the plan and the trial were not on it. The operator retyped them from other screens into an email.

**Now.** The dialog reads **Workspace ready** (renamed in the experience-refinement phase) and shows a single access card:

| Field | Source |
|---|---|
| Organization name | the organization |
| Login URL | the customer's own domain **only once it is serving**, otherwise `https://<slug>.<base domain>/` |
| Their domain | if one was claimed: hostname and status, with a note when DNS is not yet verified |
| Administrator | name and email |
| Temporary password | masked, with Show and Hide |
| Plan and trial | plan name; trial length, end date and days left |

Supporting changes:

- The configuration counts are folded into a collapsible line ("Set up for them: N items across M areas"). Missing configuration is still shown as an alert.
- A warning states that this is the only time the password is shown.
- The form wording is plainer: **Create organization** instead of "Provision organization", and the workspace address preview sits under the address field.

The creation API response now carries an `access` package beside `admin_initial_password`. The password is never inside the package, because the package is also what the organization page reads later.

## 2. Client handover flow

```
Operator                                     Customer
────────                                     ────────
Create organization ──► "Workspace ready"
  Copy login URL / credentials / welcome / full package
  Send welcome email ──────────────────────► Welcome email (their logo, colour, name)
                                             Opens their own sign-in page
                                             Signs in with the temporary password
                                             Must choose their own password
                                             Lands on the welcome checklist
Console: "Signed in · <date>"  ◄──────────── (recorded automatically)
Dashboard › Sign-ins: "first sign-in"
```

The loop closes on its own. The first forced password change by a client administrator writes **"Client administrator signed in"** to the platform audit trail. The access card's status changes from *Not signed in yet* to *Signed in · date*, and the dashboard lists it. Operators no longer have to guess from silence whether a customer ever arrived.

## 3. Login URL delivery flow

- **One source of truth.** `tenancy.handover.login_url()` decides the URL, and the copy buttons, the welcome message, the email and the first-login wizard all read it.
- **A claimed domain is never handed out early.** Until DNS verification makes it *Active*, the subdomain is the login URL and the card says why. A customer sent to an unverified hostname reaches nothing.
- **Every organization page** now starts with an **Access information** panel (Part 7): login URL with Copy, the domain status, the administrator and whether they have signed in.
- **The customer's wizard** now shows the full address ("Your team signs in at https://…") instead of the bare slug.

## 4. Credential delivery flow

The rule from earlier phases stands: **the temporary password is never stored or logged.** Two delivery paths respect it:

| When | Action | What the server does |
|---|---|---|
| In the creation dialog, while the password is still on screen | **Send welcome email to \<admin\>** | The browser sends the password back. The server **checks it against the account's hash** before mailing it, so it cannot be made to mail arbitrary text as a customer's password. Nothing is stored. |
| Any time later, from the organization page | **Email new sign-in details** (asks for confirmation first) | Issues a **new** temporary password and emails it. The console never sees it. Allowed only while the administrator has not yet chosen their own password. Afterwards it refuses, so a password someone is using is never replaced. |

Other properties:

- **A failed send changes nothing.** If SMTP refuses, a reissued password is rolled back, so any password the customer already holds still works. The API answers 502 and says so.
- **The email is in the customer's branding.** Their logo, name and colour are in the header, with "Powered by Buddhi Labs" only in the footer. It contains the organization, login URL, email, temporary password, trial end date, a pointer to the welcome checklist and, if `PLATFORM_SUPPORT_EMAIL` is set, where to write for help.
- **Rate limited** with its own scope, `access_send` at 20 per hour.
- **Audited.** "Sign-in details emailed" is recorded, plus "Temporary password reissued" when that path is used.
- **Copy buttons** for login URL, credentials, welcome message and full package all work on plain-HTTP origins too. A fresh deployment is often first opened over HTTP, where browsers withhold the Clipboard API.

## 5. Profile system improvements

- **Menu** (top-right avatar): My profile, Security & password, **Sessions**, Activity log, Platform settings, Sign out. Links to a section now scroll to it.
- **Photo.** Operators can upload, change and remove a photo. The top bar and profile page share the signed-in user's avatar component, so they cannot disagree.
- **Profile card** shows name, photo, email, username, role, last sign-in and two-step sign-in status ("Not available yet", stated rather than omitted).
- **Sessions.** "Where you are signed in" lists each live session: this browser or another, last active and expiry. **Sign out everywhere else** revokes the others. A session is a live refresh token; rotation means the time shown is when it was last active, not when it began, and it is labelled that way.
- **Recent actions** (from the platform audit trail) were already there and are unchanged.

### Two product bugs found by the live drive and fixed

1. **Operators' photos never displayed.** An operator belongs to no organization, so the photo was stored under `org/_unscoped/`, and the media server rejected every link to it as cross-tenant. Operator photos now live under `staff/profiles/`, which is protected by the link's signature and expiry like any path with no organization. Regression test: `users/test_profile.py::test_a_platform_operators_photo_is_served_back`.
2. **Operator media downloads went unaudited.** The download audit looked up the user with the tenant-scoped manager, which a request on the console host refuses. It now reads the signed user id across tenants and writes the operator's row in platform scope. Covered by the same test.

## 6. UX improvements

**Customer side**

- **Their own sign-in page.** A customer without a logo now sees a **monogram of their name in their colour**, not the Buddhi Labs logo. The same applies in the workspace header and on letterheads. The platform logo appears only where there is no tenant at all. The footer still carries only "Powered by Buddhi Labs".
- **"Create one" (public signup) no longer appears on customers' sign-in pages.** It used to show on every customer's page and never on the platform's, the reverse of what was intended. It now appears only on the platform host.
- The email placeholder `you@nif.org.np` on every customer's sign-in page is now `you@yourorganization.com`.
- **Welcome wizard:**
  - title "Welcome to your *ABC School* workspace" with the full sign-in address
  - "Everything is configured" in place of "Tenant Ready"
  - ready list adds **Organization created**, **Documents ready** and **Your own password is set**, each measured rather than assumed
  - plain dashes instead of `--`
- Other NIF leftovers removed: the "NIF-branded PDF" subtitle on attendance reports, and the search dialog's "Search NIF OMS" name.

**Operator side**

- **Dashboard › Recent**, one tabbed card (Part 9): Organizations, Domains, Payments, Signups, Sign-ins. Sign-ins are labelled *first sign-in* for customers and *last active* for operators. The platform cannot see customers' staff sign-ins and the list does not pretend to.
- The registration funnel's title no longer appears twice.
- A malformed funnel response no longer crashes the dashboard.
- Audit labels in plain words: "Organization created", "Workspace set up", "First administrator created", "Organization reactivated", and so on, instead of "Tenant bootstrapped" or "Tenant admin user created".
- Friendlier empty states and quick-action hints.

## 7. Remaining friction points

Ordered by how likely each is to produce a support call.

1. **No self-service "Forgot password".** The sign-in page says "Contact your administrator". For a customer's *only* administrator, nobody can help except the platform team, and they have no console action for an administrator who has already set their own password. **This is the most likely support call after this phase.** Recommended next: an emailed reset link.
2. **The email carries the password in plain text.** This is common practice and mitigated: the password works once, must be changed at first sign-in, and is reissuable. A one-time set-password link would be stronger and remove the password from the inbox entirely.
3. **Custom domains have no TLS automation** (risk R1 in `launch-report.md`). The handover hands out the subdomain until a domain is *Active*, but going live on a customer's own domain still needs an operator to issue a certificate.
4. **No two-step sign-in** for operators or customers. It is shown honestly as "Not available yet".
5. **Sessions show no device or location.** SimpleJWT records none against a token. Adding it means recording IP and user agent at sign-in.
6. **The sign-in hero background stays the platform's navy.** The monogram, buttons and focus colours take the customer's colour; the illustrated panel does not.
7. **Small console rough edges seen in screenshots, pre-existing and not changed:**
   - the forecast shows "NPR 832.5" (no second decimal)
   - the audit table on the organization page squeezes into narrow columns on a phone
8. **Payments still cannot be approved from the console** (N1 in `launch-report.md`). Unchanged by this phase, and still the main blocker for paid sales.

---

## Verification

| Check | Result |
|---|---|
| Backend tests, new | `tenancy/tests/test_handover.py` (13), `users/test_sessions.py` (4), operator photo regression (1), and 3 new authority routes in `test_platform_api.py`, which refuse tenant administrators and anonymous callers |
| Frontend tests, new | `components/platform/handover.test.jsx` (11), plus a BrandLogo monogram test |
| Frontend full suite | **1565 passed**, 0 unhandled errors; lint clean on touched files |
| Live drive (Daphne, `DEBUG=0`, PostgreSQL + RLS as `nifn_app`, Redis, real SMTP) | **31/31**, 0 server 5xx, 0 ERROR-level log lines |
| S11 production drill, re-run | **34/34**, 0 server 5xx |
| Browser (headless Chrome against the same stack, desktop and 390 px) | every screen rendered, no console errors, no horizontal scroll on the phone dashboard; signup link 0 on the customer's page and 1 on the platform's |

Full-suite backend figures are in the hand-over message.

## Files

**Backend, new:** `tenancy/handover.py`, `tenancy/templates/emails/workspace_access.{html,txt}`, `users/session_views.py`, `tenancy/migrations/0018_handover_audit_actions.py`, `tenancy/tests/test_handover.py`, `users/test_sessions.py`.

**Backend, changed:**
- `tenancy/views.py`: access, send and recent endpoints; `access` in the create response
- `tenancy/urls.py`, `config/urls.py`
- `tenancy/console.py`: `recent()`
- `tenancy/models.py`: three audit actions and plain-word labels
- `tenancy/onboarding.py`
- `users/views.py`: first-sign-in hook
- `users/models.py`: operator photo path
- `documents/protected_media.py`: audit lookup
- `config/settings.py`: `PLATFORM_SUPPORT_EMAIL`, `access_send` throttle
- tests: `tenancy/tests/test_platform_api.py`, `tenancy/tests/test_platform_console.py`, `users/test_profile.py`

**Frontend, new:** `components/platform/HandoverCard.jsx`, `components/platform/RecentActivity.jsx`, `utils/handover.js`, `components/platform/handover.test.jsx`.

**Frontend, changed:**
- `components/platform/NewOrganization.jsx`, `PlatformTopbar.jsx`, `RegistrationFunnel.jsx`
- `pages/platform/OrganizationDetail.jsx`, `Dashboard.jsx`, `Profile.jsx`, `Organizations.jsx`
- `pages/Login.jsx`, `hooks/useTenantBranding.js`, `components/branding/BrandLogo.jsx`
- `components/onboarding/OnboardingWizard.jsx`, `components/search/CommandPalette.jsx`, `pages/attendance/AttendanceReports.jsx`
- `services/platformService.js`, `index.css`, and the matching tests

**New setting:** `PLATFORM_SUPPORT_EMAIL`, optional. When set, it is printed in the welcome email and package; when blank, the line is left out.
