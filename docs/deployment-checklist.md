# Deployment Checklist

**Phase:** S6.75 Part 5 output.
**For:** whoever deploys this platform, each time.

The short version: **run `manage.py launch_readiness --strict` and make it
pass.** Everything below is either what that command checks (so you do not
have to remember it) or what it cannot check (so you do).

---

## 0. Why there is a list at all when there is a command

`manage.py check` fails on a critical misconfiguration, and `migrate`,
`runserver` and every management command run it. That is a real gate.

**It is not a gate on serving traffic.** Gunicorn and Daphne do not run
Django's system checks when they boot — they import the WSGI/ASGI
application and start answering requests. A platform with no purchasable
plan, no SMTP and `DEBUG=1` will serve happily. So the deploy pipeline has to
run the check itself, and that is step 3.

---

## 1. Before the first deploy of a SaaS posture

Once only, when turning a single-tenant installation into a platform.

- [ ] **Wildcard DNS** for `*.<TENANCY_BASE_DOMAIN>` pointing at the
      application, and a **wildcard TLS certificate** covering it. Without
      these a new tenant's hostname resolves nowhere, and provisioning will
      still have succeeded.
- [ ] **A platform hostname** (`admin.platform.com`), in DNS and on the
      certificate, set as `TENANCY_PLATFORM_HOSTS`. Not optional once public
      registration is on: with it empty the signup pages are reachable only
      on existing customers' hostnames, where they read as that customer
      inviting signups. (`tenancy.W003`)
- [ ] **`ALLOWED_HOSTS` contains the platform host AND `.<base domain>`.**
      A list naming the console and today's customers looks complete and
      refuses tomorrow's with a 400 before any view runs.
      (`tenancy.L004`)
- [ ] **PostgreSQL roles created** — `nifn_app` (NOSUPERUSER, NOBYPASSRLS,
      not the table owner), `nifn_migrate` (owns the tables, BYPASSRLS),
      `nifn_admin` (read-only). See `tenancy/rls.py`. The application must
      connect as `nifn_app`. (`tenancy.L005`)
- [ ] **At least one purchasable plan** with a current `PlanPrice`, and a
      sensible `trial_days`. Provisioning refuses without one, and in the
      self-service flow that refusal lands after the customer has registered,
      received the email and clicked the link. (`tenancy.L006`, `L007`)
- [ ] **SMTP configured and tested** end to end, with SPF and DKIM on the
      sending domain. Verification mail is the single point of failure in
      self-service signup: no email, no workspace, ever. (`tenancy.L008`)
- [ ] **Media storage that survives a redeploy** — a mounted volume or
      `USE_S3=True` with working credentials. The failure mode of an
      unmounted volume is uploads that appear to succeed and are gone after
      the next deploy. (`tenancy.L003`)
- [ ] **Redis for `CACHES`.** DRF's throttles live in the cache; with
      LocMemCache every rate limit is silently multiplied by the worker
      count, including the ones protecting registration and login.
      (`tenancy.L002`)
- [ ] **At least one platform operator** —
      `manage.py create_platform_admin --email ...`. (`tenancy.L010`)
- [ ] **Backups that have been restored at least once.** An untested backup
      is a hope. See `docs/disaster-recovery.md` §3.

## 2. Settings that must be right

| Setting | Must be | Checked by |
|---|---|---|
| `DJANGO_DEBUG` | `0` | `tenancy.L011` |
| `DJANGO_SECRET_KEY` | set, not a default | Django's own `--deploy` checks |
| `DJANGO_ALLOWED_HOSTS` | explicit list incl. `.<base domain>` | `tenancy.L004` |
| `TENANCY_ENABLED` | `1` | — (it is the switch) |
| `TENANCY_RLS_ENABLED` | `1`, connected as `nifn_app` | `tenancy.L005` |
| `TENANCY_BASE_DOMAIN` | the tenant domain | `tenancy.E001` |
| `TENANCY_PLATFORM_HOSTS` | the console/signup host | `tenancy.W001`, `W003` |
| `TENANCY_PUBLIC_REGISTRATION` | `1` only when you mean it | — |
| `TENANCY_PUBLIC_BASE_URL` | absolute URL of the signup pages | `tenancy.W005` |
| `TENANCY_SELF_SERVICE_TRIAL_DAYS` | > 0 | `tenancy.L007` |
| `TENANCY_EXPORT_RETENTION_DAYS` | ≤ 90 | advisory |
| `EMAIL_*` | a real SMTP provider | `tenancy.L008`, `W004` |

## 3. Every deploy, in order

```bash
# 1. The gate. Refuses on a critical misconfiguration.
manage.py check --deploy --fail-level WARNING   # or at least --deploy
manage.py launch_readiness --strict             # exits non-zero if blocked

# 2. Schema. Migrate as the OWNER role (nifn_migrate), not as nifn_app.
manage.py migrate --noinput
manage.py makemigrations --check --dry-run      # no model drift

# 3. Static files.
manage.py collectstatic --noinput

# 4. Start the application.
```

**Run 1 and 2 as separate steps from starting the server**, and let the
pipeline fail on them. That is the only thing standing between a
misconfiguration and a customer finding it.

## 4. Scheduled jobs

Without these the platform works and slowly drifts.

| Job | Cadence | Why |
|---|---|---|
| `manage.py subscriptions_advance` | nightly | moves trials into grace and grace into suspension. Without it nothing ever expires. |
| `manage.py tenancy_expire_registrations` | nightly | releases subdomains held by unverified signups. |
| `manage.py tenancy_expire_exports` | nightly | discards export bundles past retention. Each one is a whole customer's workspace in a file. |
| `manage.py tenancy_refresh_counters` | weekly | reconciles seat counters, which are maintained incrementally and bypassed by `bulk_create`. |
| `POST /platform/mirrors/reconcile/` | weekly, or on alert | corrects subscription mirror drift. The request path reads the mirror to decide who is admitted. |

## 5. After the deploy

- [ ] `manage.py launch_readiness` — read it, do not just exit-code it.
- [ ] Platform console → **Launch readiness**: verdict `Launch Ready`.
- [ ] Platform console → **Platform health**: no degraded signals.
- [ ] Sign in as an operator on the platform host.
- [ ] Sign in as a test tenant's user on `<slug>.<base domain>`.
- [ ] If public registration is on: register a throwaway organization, verify
      the email, sign in, and archive it afterwards. Nothing else exercises
      DNS, TLS, SMTP, provisioning and the bootstrap in one go.

## 6. What no checklist can check

Named here because the gaps matter more than the boxes:

- **Whether the backup restores.** Only a restore proves it.
- **Whether mail is delivered**, as opposed to accepted by the SMTP server.
  SPF/DKIM/DMARC alignment and reputation decide that, and nothing in this
  codebase can see it. A verification email in spam is a signup that never
  happens.
- **A CUSTOM DOMAIN NEEDS ITS OWN CERTIFICATE, and nothing here obtains one.**
  Found by the Phase S10 certification review. The wildcard above covers
  `*.<platform domain>`; it does **not** cover `hr.customer.com`. Phase S9
  lets a customer prove they own a hostname and start serving on it, and the
  platform will then answer that hostname over **HTTP only** unless somebody
  has issued a certificate for it at the edge.

  There is no ACME/Let's Encrypt automation in this repository — deliberately,
  it is an integration rather than hardening — so today this is **manual
  operator work, per custom domain**:

  1. The customer verifies the domain (Settings → Custom domain).
  2. An operator issues a certificate for that hostname at the terminating
     proxy, e.g. `certbot --nginx -d hr.customer.com`, and arranges renewal.
  3. Only then is the address usable in a browser.

  Two consequences to plan for before selling custom domains:

  * **`SECURE_SSL_REDIRECT` is on in production**, so an un-certificated
    custom domain redirects to an `https://` URL that cannot complete. The
    customer sees a TLS error, not a helpful page.
  * **HSTS is sent with `includeSubDomains` and `preload`.** That is correct
    for the platform's own domain. It says nothing about a customer's domain,
    so each one needs its own certificate from the first request.

  Until certificate issuance is automated, treat "custom domain" as an
  assisted feature: self-service to verify, operator-assisted to go live. The
  support runbook §5c covers the customer conversation.

- **Whether the wildcard certificate covers the depth you use.** `*.a.com`
  does not cover `x.y.a.com`.
- **Whether the export you handed a customer can be loaded anywhere.** This
  platform can produce a bundle and cannot import one — see
  `docs/disaster-recovery.md` §5.
