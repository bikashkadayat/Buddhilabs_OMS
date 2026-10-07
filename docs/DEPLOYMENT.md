# Deployment — self-hosted VPS (Docker Compose)

The whole stack runs on a single VPS with Docker Compose. The production stack
(`docker-compose.prod.yml`) bundles an **nginx** container that serves the
pre-built React SPA and proxies the API to Gunicorn over plain HTTP — so the
browser is always same-origin (no CORS). **TLS is terminated by a host nginx
(or Cloudflare) that sits in front of the container**, which listens only on
`127.0.0.1:8080`.

Services: `db` (PostgreSQL), `backend` (Django + Gunicorn + WeasyPrint), `cron`
(scheduled jobs), `web` (nginx: built SPA + API reverse proxy, HTTP only).

> Requirements: a VPS with Docker + the Docker Compose plugin, a domain name
> whose DNS A/AAAA record points at the VPS IP, and a **host nginx** (or
> Cloudflare) terminating TLS on public ports 80 + 443 and forwarding to
> `127.0.0.1:8080`.

---

## Dev vs prod — which compose file

| | File | Command | TLS | Frontend | Security flags |
|---|---|---|---|---|---|
| **Local dev** | `docker-compose.yml` | `docker compose up` | none (HTTP) | Vite dev server `:5173` | forced **off** (localhost) |
| **Production** | `docker-compose.prod.yml` | `docker compose -f docker-compose.prod.yml up -d --build` | host nginx / Cloudflare | built `dist` via container nginx | secure by default |

The two stacks are independent. This guide is entirely about the **prod** stack.

---

## 1. Get the code onto the VPS

```bash
git clone <your-repo-url> nif && cd nif
```

## 2. Configure `.env`

```bash
cp .env.example .env
```

Fill in **every** value below (the prod stack refuses to start if a required one
is missing). Example values are for `https://nif.example.com`.

| Variable | Example | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(random)* | `python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"` |
| `DJANGO_DEBUG` | `False` | must be `False` in prod |
| `DJANGO_ALLOWED_HOSTS` | `nif.example.com` | your domain; `localhost,127.0.0.1` are appended automatically |
| `DATABASE_PASSWORD` | *(strong)* | |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://nif.example.com` | **required** — admin/session POSTs 403 without it |
| `FRONTEND_URL` | `https://nif.example.com` | email action links |
| `SITE_URL` | `https://nif.example.com` | PDF QR verification URLs |

Security flags default to secure (`SSL_REDIRECT`, `SESSION_COOKIE_SECURE`,
`CSRF_COOKIE_SECURE` = True; `HSTS = 31536000`). Leave them unset in prod.

**Email (SMTP).** To actually send mail, set `EMAIL_BACKEND` to the SMTP backend
and all of `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` — the app
**fails fast at boot** if SMTP is selected but any of those are empty:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=apikey-or-user
EMAIL_HOST_PASSWORD=your-smtp-password
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=no-reply@nif.example.com
```

Leave `EMAIL_BACKEND` unset for a mail-less deployment (emails print to logs).

`.env` is gitignored — never commit it.

## 3. Build and start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

`backend` runs `migrate` + `collectstatic` + Gunicorn on start; `web` builds the
SPA and starts nginx on `127.0.0.1:8080` (plain HTTP — the host proxy handles
TLS). Watch progress:

```bash
docker compose -f docker-compose.prod.yml logs -f web backend
```

## 4. Create the first admin user

The database starts empty:

```bash
docker compose -f docker-compose.prod.yml exec backend python manage.py shell -c "
from users.models import User
u,_=User.objects.get_or_create(username='admin', defaults={'email':'admin@nif.test'})
u.email='admin@nif.test'; u.role=User.Roles.ADMIN; u.is_active=True; u.is_staff=True
u.must_change_password=False; u.set_password('CHANGE-ME-NOW'); u.save()
print('admin ready:', u.email)
"
```

Log in, then create the other users from the Admin console.

---

## 5. Reverse proxy — two layers

**Inside the container** (`deploy/nginx.conf`, already built into the `web`
image) nginx routes, on plain HTTP `127.0.0.1:8080`:

- `/api/*`, `/admin/*`, `/static/*`, `/media/*` → `backend:8000` (Gunicorn)
- `/assets/*` → the hashed SPA bundle (cached hard)
- everything else → the built SPA (`/srv`) with SPA fallback to `index.html`

**On the host**, you provide the TLS-terminating nginx (or Cloudflare) that the
public hits. It must set `X-Forwarded-Proto https`, which the container nginx
forwards and Django reads (`SECURE_PROXY_SSL_HEADER`) to recognise the HTTPS and
avoid redirect loops. A ready-to-use vhost ships at
[`deploy/host-nginx.conf.example`](../deploy/host-nginx.conf.example) — copy it
to `/etc/nginx/sites-available/nif` and edit the domain. Minimal version:

```nginx
# Redirect HTTP -> HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name nif.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name nif.example.com;

    # Certs from certbot:  sudo certbot --nginx -d nif.example.com
    ssl_certificate     /etc/letsencrypt/live/nif.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/nif.example.com/privkey.pem;

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;   # <-- REQUIRED
        proxy_set_header X-Forwarded-Host  $host;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/nif /etc/nginx/sites-enabled/nif
sudo certbot --nginx -d nif.example.com   # obtains + auto-renews the cert
sudo nginx -t && sudo systemctl reload nginx
```

> Using **Cloudflare** instead of a host nginx? Point it at the VPS, set SSL mode
> to *Full (strict)*, and change the `web` port mapping in
> `docker-compose.prod.yml` from `127.0.0.1:8080:80` to `80:80` so Cloudflare can
> reach it. Cloudflare already sends `X-Forwarded-Proto`.

For a **plain-HTTP local test** of the prod image (no host proxy), temporarily
map `web` to `8080:80`, browse `http://<host>:8080`, and set the three
`DJANGO_*_SECURE` flags to `False` in `.env`.

---

## 6. Verify

1. Open `https://nif.example.com` — valid TLS padlock.
2. Log in with the admin account from Step 4.
3. Create a memo, submit, review, approve, download the PDF (QR link uses your
   domain).

Run the post-deploy checklist below.

### Post-deploy checklist

- [ ] Site loads over **HTTPS** with a valid certificate.
- [ ] Session/CSRF cookies have the **Secure** flag (browser dev tools → Application → Cookies).
- [ ] Response carries **`Strict-Transport-Security`** (HSTS) — `curl -sI https://nif.example.com | grep -i strict`.
- [ ] Admin login + a POST from the domain succeed (**no CSRF 403**).
- [ ] A test email sends (`docker compose -f docker-compose.prod.yml exec backend python manage.py sendtestemail you@example.com`).
- [ ] Email action links + PDF QR URLs point at **`https://nif.example.com`**, not localhost.
- [ ] Upload a profile photo, then `docker compose -f docker-compose.prod.yml up -d --build` again — the photo **survives** (media volume).

---

## Notes / production hardening

- **Media persistence**: uploaded attachments, profile photos and generated PDFs
  live in the named Docker volume `media_data` (mounted at `/app/media` on both
  `backend` and `cron`), so they survive rebuilds, restarts and redeploys. For
  multi-instance / durable object storage, set `USE_S3=True` and the `AWS_*`
  vars in `.env` (needs `django-storages[boto3]` — see `backend/requirements/`).
- **Database backups**: `postgres_data` is a named volume — back it up regularly,
  e.g. `docker compose -f docker-compose.prod.yml exec db pg_dump -U leave_user leave_system > backup.sql`.
- **Static files**: served by WhiteNoise from within Gunicorn (content-hashed,
  far-future cache headers); `collectstatic` runs on every backend start.
- **Secrets**: `DJANGO_SECRET_KEY`, DB credentials and SMTP come from `.env` only;
  the insecure defaults baked into `Dockerfile.backend` are always overridden.
- **Updating**: `git pull && docker compose -f docker-compose.prod.yml up -d --build`.
