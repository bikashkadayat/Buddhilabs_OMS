# Attachment Virus Scanning and Object Storage — Design

**Phase 46 item 5. This is a design, not an implementation.** Nothing described below is
in the codebase. It exists so the decisions are made and written down before somebody
implements it under time pressure and picks the fast option.

---

## 1. What exists today

Minute attachments (`minutes.MinuteAttachment`) go through this path:

| Stage | Where | What it does |
|---|---|---|
| Upload validation | `config/uploads.validate_attachment` | size cap (25 MB for minutes), extension allowlist, and a **libmagic magic-byte sniff** so an HTML file renamed `.pdf` is rejected |
| Storage | `MinuteAttachment.file`, `upload_to="minutes/attachments/%Y/%m/"` | `FileSystemStorage` by default; `S3Storage` when `USE_S3=True` (already wired, `django-storages`) |
| Serving | `documents.protected_media.signed_media_url` → `ProtectedMediaView` | HMAC-signed, expiring (`MEDIA_SIGNED_URL_TTL`, 300s), **bound to the requesting user**; `secure_file_response` sets `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff` |
| Public route | — | **there is none.** `/media/` is not served. Every byte leaves through the signed view |

So the current position is: content type is verified, the file cannot execute in the
application origin, and links are useless to anyone but their holder. What is **not**
checked is whether the file's contents are malicious. A legitimately-formed PDF carrying
an exploit, or a macro-bearing `.docx`, passes every check above.

**There is no task queue.** The only asynchronous work in this project is
`notifications/emails.py`, which uses a bare `threading.Thread`. That single fact drives
most of what follows: a scanning design that assumes Celery is a design for a different
codebase.

---

## 2. Threat model, stated plainly

Who we are protecting, from what:

1. **The person who downloads the attachment.** The dominant risk. A board paper is
   opened by twenty people on Windows laptops; one weaponised `.docx` reaches all of
   them. This is the risk scanning actually addresses.
2. **The server.** Low, but not zero: the application never opens attachment bytes (no
   thumbnailing, no text extraction, no PDF re-rendering), so there is no parser to
   attack. If preview rendering is ever added server-side, this rises sharply and this
   document must be revisited.
3. **Third parties.** The system must not become a malware distribution point via
   signed links. Links are user-bound and short-lived, which limits this considerably.

Explicitly **out of scope**: data-loss prevention, content classification, and scanning
of generated PDFs (the system authors those itself).

---

## 3. Where the scan goes: at ingest, blocking, with a quarantine state

Three placements were considered.

| Option | Verdict |
|---|---|
| **A. Synchronous at upload, before commit** | **Chosen.** The upload request already blocks on a 25 MB body; ClamAV scans that in tens to low hundreds of milliseconds against a warm daemon. The attachment row is only created if the scan passes, so an infected file never becomes a record and never gets a signed URL. No queue needed — which is decisive given there is no queue. |
| B. Asynchronous after commit | Rejected for now. It needs a worker this project does not have, and it opens a window in which a signed URL can be issued for an unscanned file. Would require a `scan_status` gate on the download view, i.e. all of option C's complexity plus a queue. |
| C. Scan on download | Rejected. Pays the cost on every read instead of once per write, and gives no signal at upload time when the uploader is present to be told. |

### 3.1 Failure mode is the real design decision

What happens when ClamAV is unreachable decides whether this feature is security or
theatre.

**Fail closed, by default.** If the daemon does not answer, the upload is refused with a
clear message ("Attachment could not be scanned; please try again shortly"). A
`ATTACHMENT_SCAN_FAIL_OPEN=False` setting exists so an operator can consciously choose
availability over safety during an incident, and choosing it **logs a warning per
upload** so the choice is visible in the log rather than forgotten in an env file.

Fail-open-by-default is how scanning quietly becomes decorative: the daemon dies, nobody
notices, and six months of uploads were never scanned.

### 3.2 The states an attachment can be in

Even with a blocking scan, the model needs to record the outcome — an auditor asks "was
this scanned, and when, with what signature version?"

```
scan_status   : clean | infected | error | skipped      (default: skipped)
scan_signature: ClamAV signature database version at scan time
scan_detail   : the virus name, when infected
scanned_at    : timestamp
```

`skipped` is what rows uploaded **before** this feature carry. It must be a distinct
value from `clean`: back-filling old rows as `clean` would be a lie stored in the
database. A rescan sweep (§6) is what moves them.

`infected` rows should never exist under option A — the row is not created. The value
exists for the rescan sweep, which finds infections in already-stored files when
signatures catch up. Those rows are **not deleted**: the file is moved to quarantine and
the row kept, because "an infected file was attached to MIN-2026-000123 on this date by
this user" is exactly the audit record an incident needs. Downloads are refused; the
metadata stays readable.

---

## 4. ClamAV integration

### 4.1 Deployment

`clamd` as its own container, on the internal network only, with `freshclam` updating
signatures inside the same image:

```yaml
clamav:
  image: clamav/clamav:stable
  restart: unless-stopped
  volumes:
    - clamav-db:/var/lib/clamav      # signatures survive restarts; a cold start
                                     # otherwise re-downloads ~200 MB
  healthcheck:
    test: ["CMD", "clamdcheck.sh"]
    interval: 60s
    start_period: 300s               # the first signature load genuinely takes minutes
```

Sizing: `clamd` holds the full signature set in memory — **budget 2–3 GB RAM** for it.
This is the most commonly missed operational fact about ClamAV and the usual cause of a
container that gets OOM-killed in week two.

### 4.2 Talking to it

Use the `clamd` INSTREAM protocol over TCP, via the `clamd` Python package
(`ClamdNetworkSocket`). **Stream the bytes; do not share a volume.** A shared volume
means the app and the scanner must agree on paths, which breaks the moment `USE_S3=True`
and there is no local path at all. Streaming works identically for both storage
backends, which is the whole point.

New settings, all with safe defaults:

```python
CLAMAV_ENABLED   = _env_bool('CLAMAV_ENABLED', False)   # off until deployed
CLAMAV_HOST      = os.getenv('CLAMAV_HOST', 'clamav')
CLAMAV_PORT      = int(os.getenv('CLAMAV_PORT', '3310'))
CLAMAV_TIMEOUT   = int(os.getenv('CLAMAV_TIMEOUT', '30'))
ATTACHMENT_SCAN_FAIL_OPEN = _env_bool('ATTACHMENT_SCAN_FAIL_OPEN', False)
```

`CLAMAV_ENABLED=False` by default means merging the implementation does not break
development machines or CI, where no daemon exists. It must be **True in production**,
and the deployment checklist (`docs/GO_LIVE.md`) is where that belongs.

### 4.3 Where the code goes

`config/uploads.py`, beside `validate_attachment` — **not** in `minutes/`.

This is the same reasoning that created `config/uploads.py` in the first place, recorded
in its own docstring: memo upload validation once lived in `memos/serializers.py`, and
when attendance corrections gained attachments they inherited none of it. Scanning
implemented inside the minute module would repeat that mistake exactly, and the next
module to accept uploads would be unscanned.

```python
def scan_attachment(value):
    """Raise ValidationError if the bytes are malicious. Returns a scan record."""
```

Called from `validate_attachment` as the **last** check, after size and magic bytes: both
are free by comparison, and there is no point streaming 25 MB to a scanner to then reject
the file for being 25 MB.

`validate_attachment` returns the scan record so the caller can persist it — the
alternative, scanning again at save time, doubles the cost for nothing.

### 4.4 Cost

Ballpark, warm daemon, LAN: **20–150 ms for a 1 MB office document, 0.5–2 s at the 25 MB
cap.** Added to an upload request that is already uploading 25 MB, this is not the
dominant term. It does mean the request timeout must exceed `CLAMAV_TIMEOUT` plus upload
time — check the gunicorn `--timeout` and any proxy `proxy_read_timeout` before enabling.

---

## 5. Object storage

`USE_S3=True` already switches the default storage to `S3Storage`, so the module needs no
change to work against a bucket. What still needs deciding:

### 5.1 The bucket must stay private

`querystring_auth` defaults to `False` in this project's config, and `default_acl` is
`None`. That combination is only safe because **`ProtectedMediaView` never hands out the
storage URL** — it streams bytes through the application after checking permissions.

That is a deliberate choice worth keeping, and its cost is honest: every download passes
through the application server. The alternative — issuing S3 pre-signed URLs directly —
is cheaper but throws away the two properties the current design has:

- the URL is bound to the requesting user,
- the download is auditable, because it goes through code we control.

**Recommendation: keep streaming through the application.** Revisit only if attachment
egress becomes a measured bottleneck, and if so, issue *short* pre-signed URLs (60 s)
from the same view that performs the permission check — never from the serializer, which
would hand a URL to anyone who can read the list.

Bucket configuration, all of which is deployment work rather than code:

- Block Public Access **on**, at bucket and account level.
- Versioning **on** — it is the cheapest possible recovery from a mistaken delete, and
  it makes quarantine (§5.2) reversible.
- Server-side encryption on (SSE-S3 is sufficient; SSE-KMS if key custody is required).
- Lifecycle rule moving objects to infrequent access after 180 days. Minute attachments
  are read heavily for a month and then almost never — but they can never be deleted, so
  the storage bill grows forever and tiering is what keeps it flat-ish.
- **No** lifecycle expiry. An archived minute's attachments are part of a permanent
  record.

### 5.2 Quarantine

A separate prefix in the same bucket (`quarantine/`) rather than a separate bucket:
one set of credentials, one lifecycle policy, one place to look. Moving an object there
is a copy-and-delete, and with versioning on it is reversible if a scan turns out to be a
false positive — which happens, particularly with macro-bearing but legitimate finance
spreadsheets.

Nothing in the application ever reads from `quarantine/`. It exists for a human with
bucket access to examine.

---

## 6. Rescan sweep

Signatures improve; a file clean on Monday is known-infected on Friday. A management
command, run weekly, in the same idempotent style as `escalate_minute_actions`:

```
python manage.py rescan_minute_attachments [--since DAYS] [--limit N] [--dry-run]
```

- Walks attachments ordered by `scanned_at` ascending, nulls first — so `skipped` rows
  from before the feature existed are picked up first.
- `--limit` bounds a run, because rescanning 10000 attachments in one pass is exactly
  the kind of job that gets killed halfway; ordering by `scanned_at` makes the next run
  resume naturally rather than restarting.
- On a new detection: move to quarantine, set `scan_status='infected'`, write a
  `MinuteAuditLog` row against the minute, and notify the uploader and the minute's
  initiator. **Not** a silent deletion — somebody has that file on their laptop and needs
  to be told.

---

## 7. What this does not do, and what it costs

Stated so nobody believes more than is true:

- **Signature scanning is not detection of unknown malware.** ClamAV catches known
  samples. A targeted document built for this organisation will pass. The mitigation for
  that is not a better scanner; it is that recipients open board papers in a viewer with
  macros disabled.
- **Macro-bearing office files are not blocked, only scanned.** Blocking macros outright
  would be a stronger control and a real inconvenience for a finance team that uses
  them. That is a policy decision for the organisation, not a technical one — if the
  answer is "block", it belongs in the extension allowlist, not the scanner.
- **Password-protected archives cannot be scanned.** ClamAV reports them as such. Under
  fail-closed they will be refused, which is the correct outcome and will generate
  support calls. Worth saying out loud before go-live.
- **The blocking design ties upload latency to the scanner's availability.** That is the
  price of having no queue, and it is the honest trade: a working synchronous scan is
  worth more than a queue-based one nobody builds.

---

## 8. Implementation order, when somebody picks this up

1. Add the four scan fields to `MinuteAttachment` (and to the memo attachment model —
   same defect, same fix, one migration each).
2. Add `scan_attachment` to `config/uploads.py`, default-disabled.
3. Wire it as the last check inside `validate_attachment`; persist the returned record.
4. Refuse downloads where `scan_status == 'infected'` in `ProtectedMediaView`.
5. Deploy the `clamav` container; set `CLAMAV_ENABLED=True`; add both to `GO_LIVE.md`.
6. Add `rescan_minute_attachments`; schedule it weekly.
7. Only then consider object storage, which is an independent change and should not be
   entangled with this one.

Steps 1–5 are the security fix. Step 6 is what keeps it true over time. Step 7 is a
scaling decision with no bearing on either.
