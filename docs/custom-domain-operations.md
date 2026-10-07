# Custom Domain Operations — Onboarding, TLS, Renewal

**Phase S11 Part 4. Closes the operational half of R1.**

R1, from the Phase S10 certification:

> **Custom domains have no TLS path.** The wildcard certificate covers
> `*.<platform domain>`, not `hr.customer.com`. There is no ACME automation
> in this repository. With `SECURE_SSL_REDIRECT` on, an un-certificated
> custom domain redirects to an `https://` URL that cannot complete, so the
> customer sees a TLS error rather than guidance.

**The code gap is not closed and is not closing in this phase** — certificate
automation is an integration, which S11 is explicitly not building. What this
document closes is the *operational* gap: the work is defined, assigned,
bounded and quotable, so "custom domain" is a product you can sell rather
than a feature that half works.

**Sell it as assisted, not self-service.** The customer proves ownership
themselves; an operator makes it usable. Say so in the sales conversation,
and the rest of this is routine.

---

## 1. What the platform does by itself

| Step | Who | Automatic? |
|---|---|---|
| Claim a hostname | customer (Settings → Custom domain) | yes |
| Receive two DNS records to publish | customer | yes |
| Prove ownership by DNS | customer publishes, platform checks | yes |
| Become an allowed host | platform | **yes** — `tenancy.allowed_hosts` admits a hostname the moment a serving `TenantDomain` row exists, and only then |
| Resolve to the right tenant | platform | yes |
| **Have a TLS certificate** | **operator** | **no** |
| Renew that certificate | **operator** | **no** |

The two records do different jobs and this is the single most common support
conversation:

* the **verification** record (TXT or CNAME at a `_`-prefixed name) proves
  the domain is theirs;
* the **serving** record (CNAME at the hostname itself) is what actually
  sends visitors to us.

Publishing one and not the other produces "verified but it does not load" or
"it loads somebody else's error page and we are not verified".

---

## 2. Domain onboarding process

**Prerequisite:** the deployment can resolve DNS. Platform → Domains
shows `dns_available`. If it is false, **stop** — every check will answer
"we could not look" (HTTP 503, deliberately not 400), and nothing the
customer changes will help. Install `dnspython` and restart.

1. **Customer claims.** Settings → Custom domain → the hostname. Nothing
   resolves yet; the claim is a request to prove ownership.
2. **Customer publishes both records** from the page.
3. **Customer presses "Check now."** DNS changes take up to an hour to
   spread; pressing it more often does not help, and the page says so.
4. **On success** the domain is `active`, becomes an allowed host, and
   resolves to that tenant. On a multi-worker deployment allow up to a minute
   for every worker (or configure Redis — eviction cannot cross processes).
5. **Operator issues TLS** — §3. Until this is done the address is not usable
   in a browser.
6. **Operator confirms** with the customer and records the renewal date.

**SLA to quote:** same business day for step 5, once the customer has
completed step 4.

### An operator claiming on a customer's behalf

Platform → the tenant → Domains → claim. Reading a hostname off a support
ticket is a real case. It issues the same token and **resolves to nothing
until DNS agrees** — there is no path in this product by which a human grants
a hostname, deliberately.

---

## 3. TLS provisioning process

Run at the terminating proxy, not in the application. The application never
sees TLS.

### 3.1 With certbot and nginx

```bash
# The hostname must already resolve to this load balancer, which it does
# once the customer has published the serving CNAME.
sudo certbot certonly --nginx -d hr.customer.com \
     --non-interactive --agree-tos -m ops@example.com

# Add a server block. The Host header MUST pass through unchanged --
# it is how the request says which tenant it belongs to.
cat >/etc/nginx/sites-available/hr.customer.com <<'CONF'
server {
    listen 443 ssl http2;
    server_name hr.customer.com;

    ssl_certificate     /etc/letsencrypt/live/hr.customer.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hr.customer.com/privkey.pem;

    location / { try_files $uri /index.html; root /srv/spa; }

    location ~ ^/(api/v1|media|ws)/ {
        proxy_pass http://app_upstream;
        proxy_set_header Host $http_host;          # <- the important line
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
CONF
sudo ln -s /etc/nginx/sites-available/hr.customer.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 3.2 With a managed load balancer

Add the hostname to the listener's certificate set (ACM, Cloudflare, GCP
managed certificates). Validation is usually DNS — the customer has already
proved they control DNS, so they can complete it. Confirm the LB forwards
`Host` unchanged; most do by default.

### 3.3 Verify, from outside

```bash
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' \
     https://hr.customer.com/api/v1/tenant/public/branding/
# 200 0  -- served, certificate verified

curl -sS https://hr.customer.com/api/v1/tenant/public/branding/ | jq .name
# the customer's own display name, which proves it reached the right tenant
```

If the second command returns another tenant's name, **stop and escalate** —
that is a resolution fault, not a TLS one.

---

## 4. Certificate renewal process

| | |
|---|---|
| **Let's Encrypt lifetime** | 90 days |
| **Renew at** | 60 days (certbot's timer does this) |
| **Risk** | a renewal that fails silently takes a paying customer's address down with no warning from this platform — it does not watch certificates |

```bash
systemctl status certbot.timer        # must be active
certbot renew --dry-run               # monthly, in a maintenance window
```

### Monitor expiry outside the application

The platform has no certificate alert. Add one to your existing monitoring:

```bash
# Every hostname Platform -> Custom domains lists as active.
for host in $(cat /etc/platform/custom-domains.txt); do
  end=$(echo | openssl s_client -servername "$host" -connect "$host":443 2>/dev/null \
        | openssl x509 -noout -enddate | cut -d= -f2)
  days=$(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
  [ "$days" -lt 21 ] && echo "WARN $host expires in $days days"
done
```

Keep that host list generated from the console rather than hand-maintained:

```bash
manage.py shell -c "
from tenancy.context import no_tenant
from tenancy.models import TenantDomain
with no_tenant():
    for h in TenantDomain.objects.filter(
            status__in=TenantDomain.SERVING_STATUSES
    ).values_list('hostname', flat=True):
        print(h)
" > /etc/platform/custom-domains.txt
```

---

## 5. Offboarding a domain

Platform → the tenant → Domains → withdraw (or the customer, from their own
page). The address stops resolving at once, audited against the tenant that
held it.

**Then remove the certificate and server block**, or the proxy keeps
answering on a hostname the platform no longer serves — which, depending on
the default server, is either an error page or somebody else's workspace.

```bash
sudo rm /etc/nginx/sites-enabled/hr.customer.com
sudo certbot delete --cert-name hr.customer.com
sudo systemctl reload nginx
```

---

## 6. When to refuse

| Request | Answer |
|---|---|
| "Mark it verified, our DNS is complicated" | **No.** There is no verb for it. A hostname is a claim about something outside this platform; an operator asserting it is the unverified-column arrangement Phase S9 replaced |
| "Point our domain at a tenant we do not own" | No — and the platform refuses it anyway, without revealing who holds it |
| "Use `*.ourcompany.com`" | Not supported. One hostname per claim; a wildcard would need a wildcard certificate per customer |
| "Use our apex domain `customer.com`" | Technically possible, but apex records cannot be CNAMEs — they need ALIAS/ANAME support from their DNS provider, or an A record pointing at a fixed IP you must then never change. Prefer a subdomain |

---

## 7. Residual risk after this document

| | |
|---|---|
| **Still open** | certificate issuance and renewal are manual, per domain |
| **Bounded by** | this procedure, an SLA, and an expiry monitor outside the app |
| **Costs** | roughly 10 minutes of operator time per domain, plus the renewal monitor |
| **Closes when** | ACME automation is built — out of scope for S11 (an integration), and the right next engineering task if custom domains sell |
