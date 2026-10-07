"""Prove TRUSTED_PROXY_DEPTH is right, instead of guessing it.

The setting cannot be derived from the codebase -- it depends on how many
proxies are actually in front of THIS deployment. Guessing it too high
re-introduces the rate-limit bypass the setting exists to close, so this command
exists to set it from observed traffic.

Two modes:

    # What does Django see for a request I make right now?
    python manage.py verify_proxy_config --url https://your-domain/api/v1/health/

    # What has it been seeing for real users?
    python manage.py verify_proxy_config --from-audit

The second mode is the more useful one after go-live: it reads addresses the
audit log actually recorded and tells you whether they look like clients or like
your own infrastructure.
"""
import ipaddress
import urllib.request
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.test import RequestFactory
from django.utils import timezone

from audit.models import AuditLog
from config import client_ip as client_ip_module

PRIVATE_HINT = (
    "This is a PRIVATE address, which means Django is recording one of your own "
    "proxies rather than the caller. TRUSTED_PROXY_DEPTH is too LOW."
)


class Command(BaseCommand):
    help = "Verify TRUSTED_PROXY_DEPTH against what Django actually receives."

    def add_arguments(self, parser):
        parser.add_argument("--url", help="Public URL to call and inspect.")
        parser.add_argument("--from-audit", action="store_true",
                            help="Analyse addresses already recorded in the audit log.")
        parser.add_argument("--days", type=int, default=7,
                            help="Window for --from-audit (default 7).")

    def handle(self, *args, **options):
        depth = client_ip_module.trusted_proxy_depth()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"TRUSTED_PROXY_DEPTH = {depth}"))
        self.stdout.write(
            "  Django steps this many entries in from the RIGHT of "
            "X-Forwarded-For.\n"
            "  Too low  -> every client shares one throttle bucket (safe, "
            "visible).\n"
            "  Too high -> caller-supplied text is trusted as the client IP "
            "(the bypass).\n")

        self._simulate(depth)
        if options["url"]:
            self._probe(options["url"])
        if options["from_audit"]:
            self._from_audit(options["days"])
        if not options["url"] and not options["from_audit"]:
            self.stdout.write(self.style.WARNING(
                "\nNo live check requested. Re-run with --url (before go-live) "
                "or --from-audit (after) to confirm against real traffic."))

    # -- simulation -------------------------------------------------------
    def _simulate(self, depth):
        """Show, for a synthetic header, which entry each depth would pick."""
        factory = RequestFactory()
        chain = "203.0.113.10, 198.51.100.7, 172.18.0.5"
        request = factory.get("/", HTTP_X_FORWARDED_FOR=chain,
                              REMOTE_ADDR="172.18.0.4")
        self.stdout.write(self.style.MIGRATE_HEADING("Worked example"))
        self.stdout.write(f"  X-Forwarded-For: {chain}")
        self.stdout.write("  REMOTE_ADDR:     172.18.0.4")
        for candidate in range(0, 4):
            resolved = client_ip_module.client_ip(request, depth=candidate)
            marker = "  <-- current setting" if candidate == depth else ""
            self.stdout.write(f"    depth={candidate} -> {resolved}{marker}")
        self.stdout.write(
            "  Pick the depth whose result is the CLIENT (203.0.113.10 here), "
            "not one of your proxies.\n")

    # -- live probe -------------------------------------------------------
    def _probe(self, url):
        self.stdout.write(self.style.MIGRATE_HEADING(f"Probing {url}"))
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                status = response.status
        except Exception as exc:  # noqa: BLE001 -- any failure is informative
            self.stdout.write(self.style.ERROR(f"  Request failed: {exc}"))
            return
        self.stdout.write(f"  HTTP {status} — endpoint reachable.")
        self.stdout.write(
            "  Now make an authenticated request from a real client and re-run "
            "with --from-audit; the address recorded there is the ground truth.")

    # -- audit-log analysis ----------------------------------------------
    def _from_audit(self, days):
        since = timezone.now() - timedelta(days=days)
        rows = (AuditLog.objects.filter(created_at__gte=since)
                .exclude(ip_address__isnull=True)
                .values_list("ip_address", flat=True))
        counts = Counter(rows)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Addresses recorded in the last {days} days"))
        if not counts:
            self.stdout.write(self.style.WARNING(
                "  No audited requests with an address yet. Sign in once and "
                "re-run."))
            return

        private = 0
        for address, count in counts.most_common(10):
            try:
                is_private = ipaddress.ip_address(address).is_private
            except ValueError:
                is_private = False
            flag = " [PRIVATE]" if is_private else ""
            private += count if is_private else 0
            self.stdout.write(f"  {count:6d}  {address}{flag}")

        total = sum(counts.values())
        distinct = len(counts)
        self.stdout.write(f"\n  {distinct} distinct address(es) over {total} entries.")

        if private > total * 0.5:
            self.stdout.write(self.style.ERROR(f"  {PRIVATE_HINT}"))
            self.stdout.write(self.style.ERROR(
                "  Raise TRUSTED_PROXY_DEPTH by one and re-check."))
        elif distinct == 1 and total > 20:
            self.stdout.write(self.style.ERROR(
                "  Every request resolved to the SAME address. That is a proxy, "
                "not your user base. TRUSTED_PROXY_DEPTH is too LOW."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "  Addresses look like real, varied clients. Setting appears "
                "correct."))
