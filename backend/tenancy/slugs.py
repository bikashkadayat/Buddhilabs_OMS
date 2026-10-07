"""Tenant slug validation.

A slug is not a cosmetic label: it becomes a DNS label (`<slug>.platform.com`),
so the rules below are the union of what DNS accepts and what the platform must
keep for itself. Getting this wrong produces an organization that can be created
but never reached -- a failure discovered at go-live rather than at creation.
"""
import re

from django.core.exceptions import ValidationError

# A DNS label is capped at 63 octets (RFC 1035 s2.3.4). Organization.slug is
# max_length=63 for the same reason; this validator is the second line.
MAX_SLUG_LENGTH = 63

# Lowercase alphanumeric with internal hyphens. No leading/trailing hyphen
# (invalid as a DNS label), no underscores (invalid in a hostname), no dots
# (would create a sub-sub-domain and break a wildcard certificate).
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# Hostnames the platform itself needs. A tenant holding one of these could
# intercept the platform console, the API, or ACME validation.
RESERVED_SLUGS = frozenset({
    # platform surfaces
    "www", "api", "app", "admin", "platform", "console", "dashboard",
    # infrastructure
    "static", "assets", "media", "cdn", "ws", "wss", "smtp", "mail", "imap",
    "pop", "ns", "ns1", "ns2", "mx", "ftp", "proxy", "gateway",
    # operations
    "status", "health", "metrics", "monitoring", "grafana", "sentry",
    # product surfaces that must not be shadowed by a tenant
    "login", "logout", "auth", "signup", "register", "onboarding",
    "billing", "payment", "payments", "invoice", "invoices", "subscribe",
    "help", "support", "docs", "blog", "about", "legal", "privacy", "terms",
    # environments
    "test", "testing", "staging", "stage", "dev", "develop", "demo", "sandbox",
    "preview", "local", "localhost",
    # misc
    "null", "none", "undefined", "root", "system", "security", "abuse",
    "postmaster", "webmaster", "hostmaster",
})


def validate_tenant_slug(value):
    """Reject a slug that could not work as a subdomain, or that we reserve."""
    if not value:
        raise ValidationError("A slug is required.")

    if len(value) > MAX_SLUG_LENGTH:
        raise ValidationError(
            f"A slug may be at most {MAX_SLUG_LENGTH} characters "
            f"(a DNS label cannot be longer).")

    if not SLUG_RE.match(value):
        raise ValidationError(
            "A slug must be lowercase letters, digits and internal hyphens "
            "only, and must start and end with a letter or digit.")

    if value in RESERVED_SLUGS:
        raise ValidationError(f"'{value}' is reserved by the platform.")

    # An all-numeric slug is a valid DNS label but would be ambiguous against
    # any future id-based route, and a bare UUID reads as a leaked internal id.
    if value.isdigit():
        raise ValidationError("A slug cannot be entirely numeric.")

    return value
