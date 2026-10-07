"""The caller's real IP address, behind however many reverse proxies.

Phase 11 audit finding H1/M1. Two separate defects shared one root cause:

* ``audit.services.log_action`` read ``REMOTE_ADDR``, which behind nginx is the
  proxy's address. Every audit row therefore recorded the same useless IP, which
  defeats the reason for storing one at all.
* ``NUM_PROXIES`` was unset, so DRF's throttles used the WHOLE ``X-Forwarded-For``
  header as the rate-limit identity. That header is set by the caller, so
  rotating it handed out a fresh bucket on every request -- unlimited password
  guessing against the login endpoint, and every anonymous limit decorative.

Both are now answered by one function, configured by one setting.

Reading X-Forwarded-For correctly
---------------------------------
Each proxy APPENDS the address it received the connection from, so the header
grows left-to-right and the rightmost entries are the ones our own
infrastructure wrote:

    X-Forwarded-For: <client>, <cloudflare-edge>, <ingress-nginx>
                        ^untrusted            ^^^^^^^^^^^^^^^^^^^^ trusted

With ``TRUSTED_PROXY_DEPTH = N`` we step N entries in from the RIGHT. Anything
further left was written by someone we do not control and is treated as a claim,
not a fact.

Why the direction matters
-------------------------
Getting the depth wrong is not symmetrical, and the asymmetry is the whole
safety argument:

* **Too low** -- we read one of our own proxies. Every client then shares a
  single throttle bucket: rate limiting becomes too strict and audit rows record
  an internal address. Annoying, visible, and *not exploitable*.
* **Too high** -- we step past our own infrastructure into caller-supplied text
  and trust it. That is exactly the bypass this module exists to close.

So the depth is CLAMPED to the number of addresses actually present, the default
errs low, and ``verify_proxy_config`` exists so the value is set from observed
production traffic rather than from a guess about the topology.
"""
import ipaddress
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

XFF_HEADER = "HTTP_X_FORWARDED_FOR"
REAL_IP_HEADER = "HTTP_X_REAL_IP"

# Errs low on purpose (see module docstring): one hop is the minimum any
# containerised deployment has, and being too low is the safe direction.
DEFAULT_TRUSTED_PROXY_DEPTH = 1


def trusted_proxy_depth():
    """How many proxies of ours sit in front of Django.

    Deliberately a setting rather than a constant: the same code runs behind one
    nginx in staging and behind Cloudflare plus an ingress in production.
    """
    return max(0, int(getattr(settings, "TRUSTED_PROXY_DEPTH",
                              DEFAULT_TRUSTED_PROXY_DEPTH)))


def _valid(address):
    """True when this parses as an IP. A proxy can write ``unknown`` or a
    hostname into the header, and ``GenericIPAddressField`` would reject it."""
    if not address:
        return False
    try:
        ipaddress.ip_address(address)
    except ValueError:
        return False
    return True


def client_ip(request, depth=None):
    """The caller's address, or None when nothing usable can be determined.

    None rather than a fallback string: ``AuditLog.ip_address`` is nullable, and
    "we do not know" is more honest in an audit trail than a placeholder that
    reads like a real address.
    """
    if request is None:
        return None

    meta = getattr(request, "META", {}) or {}
    depth = trusted_proxy_depth() if depth is None else max(0, int(depth))

    # depth 0 means Django is directly exposed; the socket peer is the client
    # and the header (if any) is entirely caller-supplied.
    if depth == 0:
        remote = meta.get("REMOTE_ADDR")
        return remote if _valid(remote) else None

    forwarded = meta.get(XFF_HEADER, "")
    if forwarded:
        addresses = [part.strip() for part in forwarded.split(",") if part.strip()]
        if addresses:
            # Clamp: never step further left than the header actually goes.
            # Without this, a caller could SHORTEN the header to make their own
            # value land in the trusted position.
            index = len(addresses) - min(depth, len(addresses))
            candidate = addresses[index]
            if _valid(candidate):
                return candidate
            logger.warning("Unparseable address %r in X-Forwarded-For; "
                           "falling back to REMOTE_ADDR", candidate[:64])

    # X-Real-IP is written by our own nginx and is not a list, so it cannot be
    # extended or truncated by the caller the way X-Forwarded-For can.
    real_ip = meta.get(REAL_IP_HEADER, "").strip()
    if _valid(real_ip):
        return real_ip

    remote = meta.get("REMOTE_ADDR")
    return remote if _valid(remote) else None


def describe(request):
    """Everything ``verify_proxy_config`` needs to explain what Django sees.

    Exists so the operator can set ``TRUSTED_PROXY_DEPTH`` from a real request
    instead of reasoning about the deployment diagram.
    """
    meta = getattr(request, "META", {}) or {}
    forwarded = meta.get(XFF_HEADER, "")
    addresses = [part.strip() for part in forwarded.split(",") if part.strip()]
    depth = trusted_proxy_depth()
    return {
        "remote_addr": meta.get("REMOTE_ADDR"),
        "x_forwarded_for": forwarded or None,
        "x_forwarded_for_parsed": addresses,
        "x_real_ip": meta.get(REAL_IP_HEADER) or None,
        "configured_depth": depth,
        "resolved_client_ip": client_ip(request),
        "hops_available": len(addresses),
        # The operator's cue: if the header carries more addresses than the
        # configured depth accounts for, the leftmost ones are untrusted input.
        "depth_looks_low": bool(addresses) and len(addresses) > depth,
    }
