# Production Deployment Guide — Multi-Tenant SaaS

**Phase S11 Parts 1 and 2.** How to stand this platform up for paying
customers, and how to prove you have.

Everything here is derived from the launch gate (`tenancy.launch`), which is
the authority — if this document and `manage.py check --deploy` disagree,
the gate is right and this document is stale.

```bash
manage.py check --deploy      # the gate. Non-zero until it is satisfied.
manage.py launch_readiness    # the same checks, as a readable report.
```

---

## 0. The shape of a production deployment

```
                      wildcard DNS  *.app.example.com  ->  load balancer
                      per-domain    hr.customer.com    ->  load balancer
                                             |
                                        TLS terminates
                                             |
                                        nginx / ALB
                                     (Host forwarded unchanged)
                                             |
                    +------------------------+------------------------+
                    |                                                 |
            static SPA bundle                              gunicorn/uvicorn
            (frontend/dist)                                config.asgi
                                                                  |
                                              +-------------------+-------------------+
                                              |                   |                   |
                                        PostgreSQL 14+        Redis            object store / disk
                                        (RLS enforced)   (cache, channels)       (tenant media)
                                                                  |
                                                            cron container
                                                   (check_alerts, expiry, retention)
```

**The Host header must reach Django unchanged.** It is how a request says
which tenant it belongs to. nginx preserves it by default; a proxy
configured to rewrite it (`proxy_set_header Host $proxy_host`, or Vite's
`changeOrigin: true`) makes every request look like one hostname and
tenant resolution stops working entirely. Use `proxy_set_header Host
$http_host;`.

---

## 1. Environment variables

### 1.1 Core — nothing starts without these

| Variable | Value | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | 50+ random chars | Rotating it invalidates every session **and every signed media URL** |
| `DJANGO_DEBUG` | `0` | **Blocking** (`L011`). With it on, tracebacks, settings and SQL are served to whoever triggers an error |
| `DJANGO_ALLOWED_HOSTS` | `.app.example.com,admin.app.example.com` | See §6 — the **leading dot is the wildcard** |
| `SITE_URL` | `https://admin.app.example.com` | Used in emails and document QR codes |
| `FRONTEND_URL` | same as `SITE_URL` | Where emailed links point |
| `DJANGO_LOG_LEVEL` | `INFO` | JSON to stdout; `WARNING` discards the provisioning and domain-verification trail |

### 1.2 Multi-tenancy

| Variable | Value | Notes |
|---|---|---|
| `TENANCY_ENABLED` | `1` | Host resolution, the admission gate, the console host rule |
| `TENANCY_RLS_ENABLED` | `1` | PostgreSQL row-level security. **Requires `ATOMIC_REQUESTS`**, which the settings set for you |
| `TENANCY_BASE_DOMAIN` | `app.example.com` | Tenants live at `<slug>.app.example.com` |
| `TENANCY_PLATFORM_HOSTS` | `admin.app.example.com` | The console's own hostname. Not optional once public signup is on |
| `TENANCY_DEFAULT_SLUG` | `nif` | Only used by the single-tenant fallback; harmless here |
| `TENANCY_PUBLIC_REGISTRATION` | `1` to sell self-service | Off ships inert — an anonymous stranger causing a tenant to exist is a decision, not a default |
| `TENANCY_SELF_SERVICE_TRIAL_DAYS` | `14` | **The signup page advertises this number.** Change it here, not in the page |
| `TENANCY_VERIFICATION_TTL_HOURS` | `24` | How long an emailed signup link is good for |
| `TENANCY_VERIFICATION_MAX_SENDS` | `3` | Resends before a registration has to start again |
| `TENANCY_EXPORT_RETENTION_DAYS` | `30` | How long a bundle is kept. The **receipt** is kept for ever |

### 1.3 PostgreSQL

| Variable | Value |
|---|---|
| `DATABASE_ENGINE` | `django.db.backends.postgresql` |
| `DATABASE_NAME` / `DATABASE_USER` / `DATABASE_PASSWORD` | the **application** role — see §3 |
| `DATABASE_HOST` / `DATABASE_PORT` | |
| `DATABASE_CONN_MAX_AGE` | **`0`**, unless you have a pooler. See §3.3 |
| `DATABASE_POOLER` | `1` *only* if pgbouncer (or equivalent) is in front |

### 1.4 Redis

| Variable | Value | Notes |
|---|---|---|
| `REDIS_URL` | `redis://host:6379/0` | **Required in practice** (`L002`) |

Not optional for a platform, for three reasons the launch gate names:

1. **Throttles live in the cache.** With a per-process cache, every rate
   limit is silently multiplied by the worker count — including the ones
   protecting registration and login.
2. **The tenant resolver caches host → organization.** Unshared, each worker
   resolves independently.
3. **A verified custom domain takes up to 60 s to start serving on each
   worker**, and a withdrawn one up to 60 s to stop, because eviction cannot
   cross processes. Only the TTL converges.

### 1.5 SMTP

| Variable | Value |
|---|---|
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST` / `EMAIL_PORT` | e.g. `587` |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | |
| `EMAIL_USE_TLS` | `1` |
| `DEFAULT_FROM_EMAIL` | `Platform <no-reply@example.com>` |

**Blocking** (`L008`). Verification is the only route from a registration to
a workspace, so with no working transport **every signup stops at "check
your email" and no tenant is ever created** — while the platform reports
success, because its own `send` succeeded.

Configure SPF, DKIM and DMARC for the sending domain before launch.
Verification mail that lands in spam is indistinguishable, from the
customer's side, from mail that was never sent.

### 1.6 Storage

| Variable | Value |
|---|---|
| `USE_S3` | `1` for object storage, `0` for a mounted disk |
| `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION_NAME`, `AWS_S3_ENDPOINT_URL` | when `USE_S3=1` |
| `AWS_S3_QUERYSTRING_AUTH` | `0` — this platform signs its own URLs |
| `MEDIA_SIGNED_URL_TTL` | `300` — document attachments |
| `BRANDING_URL_TTL` | `86400` — logos, fetched on every page |

**The bucket must not be public.** Every file is served through
`/api/v1/media/` behind an HMAC signature bound to the tenant *and* the
user. Media lives at `org/<tenant-token>/…`, which is what makes a
per-tenant restore possible.

### 1.7 Branding

No variables. Branding is **per tenant, in the database**, set by the
customer (Settings → Branding) or by an operator. The only platform-level
values are the fallbacks used when a tenant has set nothing:

| Variable | Used for |
|---|---|
| `ORG_NAME`, `ORG_ADDRESS`, `ORG_TEL`, `ORG_EMAIL`, `ORG_WEBSITE` | the letterhead and email footer of a tenant that has customised neither |

Set these to the **platform operator's** details, not a customer's.

| Variable | Used for |
|---|---|
| `PLATFORM_SUPPORT_EMAIL` | Optional. Printed in every new customer's welcome email and handover package as "Trouble signing in? Write to …". Leave blank to omit the line. See `client-handover.md`. |

### 1.8 Domains

See §6 and [custom-domain-operations.md](custom-domain-operations.md).

### 1.9 Payments

No variables, and no gateway. Payment methods are **database rows** managed
from the Django admin at `/admin/tenancy/paymentinstruction/`. **Not from
the platform console** -- there is no console page for payment instructions,
and there is deliberately none for *deciding* a payment either; see the
support runbook §5b for the procedure that exists today.

**A fresh install has two placeholder rows** — `Bank Transfer` and `eSewa` —
with no account number, no eSewa ID and no instructions. Left as they are,
the subscription page shows a customer their reference and amount above two
headings with nothing under them. `L009` catches it ("configured but
empty"). Fill them in before selling.

### 1.10 Observability

| Variable | Value | Notes |
|---|---|---|
| `SENTRY_DSN` | your DSN | `L015`. Customers never see internal errors **by design**, so nothing else will tell you |
| `SENTRY_ENVIRONMENT` | `production` | |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0`–`0.1` | |
| `ALERT_EMAILS` | `ops@example.com,oncall@example.com` | `L017`. Without it every alert fires into a log line nobody reads |
| `ALERT_WEBHOOK_URL` | a chat webhook | Either this or `ALERT_EMAILS` |

### 1.11 Security

| Variable | Value |
|---|---|
| `DJANGO_SECURE_SSL_REDIRECT` | `1` |
| `DJANGO_SESSION_COOKIE_SECURE` / `DJANGO_CSRF_COOKIE_SECURE` | `1` |
| `DJANGO_SECURE_HSTS_SECONDS` | `31536000` |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` / `_PRELOAD` | `1` |
| `TRUSTED_PROXY_DEPTH` | number of proxies in front, for correct client IPs |
| `LOGIN_LOCKOUT_ENABLED` / `LOGIN_LOCKOUT_SECONDS` | `1` / `900` |

`includeSubDomains` + `preload` commits **every** subdomain of
`TENANCY_BASE_DOMAIN` to HTTPS, permanently. That is correct for a platform
issuing tenant subdomains. It says nothing about a customer's own domain —
see §6.

---

## 2. Database roles

Three roles, and the separation is what makes RLS mean anything.

| Role | Attributes | Used by |
|---|---|---|
| `nifn_app` | `NOSUPERUSER NOBYPASSRLS NOCREATEDB`, **not** the table owner | the application |
| `nifn_migrate` | owns the tables, `BYPASSRLS` | `manage.py migrate` only |
| `nifn_admin` | read-only | humans investigating |

```sql
CREATE ROLE nifn_migrate LOGIN PASSWORD '…' BYPASSRLS;
CREATE ROLE nifn_app     LOGIN PASSWORD '…' NOSUPERUSER NOBYPASSRLS NOCREATEDB;
CREATE DATABASE platform OWNER nifn_migrate;

-- as nifn_migrate:
--   manage.py migrate        (creates the tables, policies and FORCE RLS)

GRANT USAGE ON SCHEMA public TO nifn_app;
GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES
  ON ALL TABLES IN SCHEMA public TO nifn_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO nifn_app;
```

**`nifn_app` must never be granted `TRUNCATE`** and must never own a table.
A table owner is exempt from its own policies unless `FORCE ROW LEVEL
SECURITY` is set — the migration sets it, and the role separation is the
second lock. `L005` verifies at boot that the connected role cannot bypass
RLS, by asking `pg_roles`.

### 2.1 Why the migration role is separate

`nifn_app` is `NOCREATEDB` by design, so it cannot create its own database —
which also means it cannot run the test suite's database creation. That is
intentional: the role the internet talks to should not be able to create or
drop anything.

### 2.2 Verifying isolation after deployment

```sql
-- 106 of each, and the three numbers must agree.
SELECT count(*) FROM pg_policies WHERE schemaname = 'public';
SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity;
```

```bash
manage.py shell -c "from tenancy import inventory; print(len(inventory.TENANT_SCOPED))"
```

### 2.3 Connection budget

**Measured in Phase S10 Part 3, and it is the one setting most likely to
break a launch.** With `CONN_MAX_AGE > 0` under ASGI, connections held on
worker threads are reclaimed by nothing until they expire: one process
reached **95 idle connections** and **14.5 % of requests failed** with

```
FATAL: remaining connection slots are reserved for roles with
       the SUPERUSER attribute
```

Django closes connections on `request_finished`, which closes the connection
belonging to *the thread the signal fires on* — but under ASGI the view ran
on a pool thread.

Pick one:

* **`DATABASE_CONN_MAX_AGE=0`** — measured: 0 errors, peak 33 connections for
  32 concurrent requests. Costs a few milliseconds per request.
* **pgbouncer in TRANSACTION mode**, with `DATABASE_POOLER=1`. Safe here:
  tenant binding is `SET LOCAL` inside `ATOMIC_REQUESTS`, so nothing depends
  on the session outliving the transaction. A session-scoped `SET` would have
  made transaction pooling unusable — it is not one.

`L014` grades this.

---

## 3. Scheduled jobs

Nothing below is optional. The rules are only consulted when the job runs.

| Command | Cadence | What breaks without it |
|---|---|---|
| `check_alerts` | **every 5 min** | every alert in §4 is correct and never evaluated |
| `tenancy_expire_notices` | nightly | no renewal, grace or expiry mail |
| `tenancy_expire_exports` | nightly | bundles outlive their retention |
| `tenancy_expire_registrations` | nightly | abandoned signups hold their subdomain |
| `check_backups` | nightly | backup freshness is unmonitored |

The cron container's own liveness is alerted on (`cron_overdue`), so a cron
host that dies reports itself — provided `check_alerts` is one of the jobs
still running somewhere.

---

## 4. Alerting

Nine SaaS alert categories, evaluated every five minutes, firing on **state
transitions only** — once when a condition starts, once when it clears, one
digest a day while it persists. See [alerting-coverage.md](alerting-coverage.md).

---

## 5. Static files and the SPA

```bash
cd frontend && npm ci && npx vite build     # -> frontend/dist
cd backend  && manage.py collectstatic --noinput
```

`frontend/dist` is served by nginx (or a CDN); `backend/staticfiles` is
served by WhiteNoise for the Django admin and DRF. **Django has no route for
the SPA** — a deep link like `/settings/branding` must be rewritten to
`index.html` by the web server, or every bookmark and refresh 404s:

```nginx
location / { try_files $uri /index.html; }
```

---

## 6. DNS, hosts and TLS

| | |
|---|---|
| Wildcard DNS | `*.app.example.com` → the load balancer |
| Wildcard TLS | `*.app.example.com`, covering **one** label only |
| `DJANGO_ALLOWED_HOSTS` | `.app.example.com,admin.app.example.com` |

A wildcard certificate for `*.app.example.com` covers `abc.app.example.com`
and **not** `a.b.app.example.com`. `tenancy.slugs` refuses a slug containing
a dot for exactly that reason.

The leading dot in `ALLOWED_HOSTS` is Django's wildcard:
`.app.example.com` matches the base domain and every subdomain. `L004`
checks it with Django's own matcher.

**Customer-owned domains need a certificate each — see
[custom-domain-operations.md](custom-domain-operations.md).** A verified
custom domain becomes an allowed host automatically (`tenancy.allowed_hosts`
admits only hostnames a customer has proved they own) but it does **not**
acquire TLS automatically. With `SECURE_SSL_REDIRECT` on, an un-certificated
custom domain redirects to an `https://` URL that cannot complete.

---

## 7. Production configuration checklist (Part 2)

Run in order. Every line is verifiable.

### Before the first deploy

- [ ] PostgreSQL 14+, three roles created, `nifn_app` is `NOBYPASSRLS` and not an owner
- [ ] Redis reachable
- [ ] SMTP credentials tested, SPF/DKIM/DMARC published
- [ ] Object store or media volume, **not public**
- [ ] Wildcard DNS and wildcard TLS for `TENANCY_BASE_DOMAIN`
- [ ] Separate hostname for the console, in `TENANCY_PLATFORM_HOSTS` and in DNS/TLS
- [ ] Proxy forwards `Host` unchanged (`proxy_set_header Host $http_host;`)

### Deploy

- [ ] `manage.py migrate` **as `nifn_migrate`**
- [ ] Grants applied to `nifn_app` (§2) — no `TRUNCATE`
- [ ] `manage.py collectstatic --noinput`; SPA built and served with an `index.html` fallback
- [ ] `manage.py create_platform_admin …` — the first operator
- [ ] Payment instructions filled in (the seeded rows are empty placeholders)
- [ ] Scheduled jobs installed (§3), `check_alerts` every 5 minutes

### The five blocking settings

- [ ] `DJANGO_DEBUG=0`
- [ ] `EMAIL_BACKEND` is SMTP and a test message arrives
- [ ] `REDIS_URL` set and reachable
- [ ] `DATABASE_CONN_MAX_AGE=0` **or** pgbouncer + `DATABASE_POOLER=1`
- [ ] `SENTRY_DSN` set, **and** `ALERT_EMAILS` or `ALERT_WEBHOOK_URL` set

### Prove it

- [ ] `manage.py check --deploy` → **exit 0**
- [ ] `manage.py launch_readiness` → **score 100, Launch Ready**
- [ ] Console reachable on the platform host; refused on a tenant host
- [ ] A test registration completes: signup → email → verify → workspace
- [ ] `SELECT count(*) FROM pg_policies WHERE schemaname='public';` matches `len(inventory.TENANT_SCOPED)`
- [ ] Backups running and **a restore rehearsed** — see [disaster-recovery.md](disaster-recovery.md)

### After go-live, day one

- [ ] Platform → Health → Launch readiness is green
- [ ] Platform → Domains shows `dns_available: true`
- [ ] An alert has been deliberately triggered and arrived
- [ ] Media orphan sweep scheduled or assigned (R6)
