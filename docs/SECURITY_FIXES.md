# Security Remediation — Phase 3.5

A pre-production hardening pass over the memo module and platform configuration.
Issues were fixed in priority order (CRITICAL → HIGH → MEDIUM → LOW); every fix
ships with a regression test that proves the vulnerability is closed.

Automated tests run on SQLite (`DATABASE_ENGINE=sqlite3`); the app runs on
PostgreSQL (backend `:8002`, frontend `:9003`). Live proofs below were captured
against the running Postgres stack.

---

## CRITICAL

### C1 — Stored XSS in memo body
**Risk:** the memo body was rendered with `dangerouslySetInnerHTML` and was never
sanitized. Any authenticated user could store `<script>`/`<img onerror>` that ran
in a checker/approver/admin session (privilege escalation).
**Fix (defense in depth):**
- Write: `memos/sanitizers.py` (`bleach` allowlist) called from
  `MemoCreateSerializer.validate_body`.
- Read: `DOMPurify` in `RichTextEditor` before rendering.
- PDF: a second sanitize pass before WeasyPrint render.
**Proving tests:** `memos/tests/test_security.py` — `test_memo_body_strips_script_tag`,
`_strips_onerror_attribute`, `_strips_iframe`, `_strips_javascript_uri`,
`_preserves_allowed_formatting`, `_link_gets_noopener_noreferrer`;
`frontend .../RichTextEditor.test.jsx`.

### C2 — Memo attachments had no access control
**Risk:** `/media/` exposed confidential HR/financial attachments with no per-memo
authorization; URLs were guessable and could be served inline.
**Fix:** authenticated `GET /memos/{id}/attachment/` enforcing `CanViewMemo`,
forcing `application/octet-stream` + `Content-Disposition: attachment` +
`X-Content-Type-Options: nosniff`; uploads stored under unguessable per-file UUID
directories; serializers no longer expose the raw MEDIA path.
**Proving tests:** `test_attachment_download_requires_auth`,
`_enforces_memo_permission`, `_allowed_for_creator`, `_allowed_for_assigned_checker`,
`_forces_content_disposition_attachment`, `_never_served_inline`,
`test_detail_serializer_does_not_leak_media_path`.

---

## HIGH

### H1 — Admin action contract inconsistency
`can_review/approve/reject` advertised admin capability the service refused.
Aligned the service to allow the assigned actor OR an admin; admin overrides are
flagged in the audit trail (`<action>_admin_override`, `admin_override=True`).
**Tests:** `memos/tests/test_admin_override.py`.

### H2 — Two divergent memo-number generators
`Memo.save()` minted a legacy `NIFN-MEMO-…` format while the service minted the
typed format. Collapsed to one: `Memo.save()` delegates to
`services.generate_memo_number`. **Tests:** `memos/tests/test_numbering.py`.

### H3 — Attachment validation was filename-only
Added magic-byte content sniffing (`python-magic`); a file whose bytes don't match
its extension (HTML renamed `.pdf`) is rejected.
**Tests:** `test_renamed_html_as_pdf_rejected`, `test_valid_pdf/png_accepted`.

### H4 — Memo-number race + lexical-sort bug
Replaced "max existing number under a lock that covered nothing" with an
authoritative `MemoNumberSequence` counter (locked + incremented), seeded from
existing numbers by migration. **Tests:** `test_sequential_numbers_unique_gap_free`,
`test_first_memo_of_year_no_race`, `test_sequence_beyond_9999_increments_correctly`.

### H5 — User-directory enumeration
`available-checkers/approvers` dumped the full roster with emails. Now
search-gated (≥2 chars), capped, throttled, and email/role removed.
**Tests:** `memos/tests/test_directory.py`.

### H6 — Insecure platform defaults
Added `DEFAULT_PERMISSION_CLASSES=[IsAuthenticated]` (fail closed) and global
throttling; DEBUG defaults off; SECRET_KEY/ALLOWED_HOSTS have no insecure fallback
when DEBUG is off (boot guards raise `ImproperlyConfigured`); CORS allow-all only
in DEBUG. Public endpoints (health, login, refresh) opt out with `AllowAny`.
**Tests:** `config/test_security_defaults.py`.

---

## MEDIUM

### M1 — Broad pool confidentiality
`CanViewMemo`/queryset pool scoped to the actor's department and excludes
financial/HR types. **Tests:** `memos/tests/test_confidentiality.py`.

### M2 — Orphaned drafts on submit failure
Atomic `POST /memos/create-and-submit/` rolls the draft back if submit fails.
**Tests:** `memos/tests/test_create_and_submit.py`.

### M3 — Token lifetime + rotation
Access token 24h→30min; refresh rotation + blacklist-on-rotation
(`token_blacklist` app); `POST /auth/logout/` revokes a refresh token.
**Tests:** `users/test_token_security.py`.

### M4 — Nondeterministic approver escalation
`resolve_next_approver` escalates to the oldest admin deterministically.
**Test:** `memos/tests/test_escalation.py`.

### M5 — Duplicated create path
`memoService.createMemo/createAndSubmit` handle JSON and FormData in one path;
`CreateMemo` no longer hand-rolls a multipart request.
**Tests:** `frontend .../memoService.test.js`.

---

## LOW

### L1 — Templates were read-only
`MemoTemplateViewSet` is now a `ModelViewSet`; read is open to authenticated
users, create/update/delete are admin-only.
**Tests:** `memos/tests/test_templates_and_notify.py`.

### L2 — Bundle size / code-splitting
TipTap split into a lazy chunk (loads only on edit); recharts pages, admin
consoles and memo pages are route-split. Initial JS dropped from
**1,282 kB / 369.97 kB gzip** to **316 kB / 85.5 kB gzip** (~77% smaller).

### L3 — Silent notification failures
`_notify_memo` now logs at ERROR with `memo_number` + recipient (still
best-effort; the transition succeeds).
**Test:** `test_notification_failure_logged_at_error`.

### L4 — Raw attachment path exposed
Closed together with C2: the detail serializer exposes only the gated download
URL and the create serializer's attachment field is write-only.
**Test:** `test_detail_serializer_does_not_leak_media_path`.
