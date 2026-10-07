"""Phase S9: let a verified custom domain past Django's host check.

THE DEFECT THIS EXISTS FOR, STATED PLAINLY.

Django validates the ``Host`` header against ``ALLOWED_HOSTS`` in
``HttpRequest.get_host()``, which runs BEFORE any middleware this project
owns. ``ALLOWED_HOSTS`` is read from ``DJANGO_ALLOWED_HOSTS``, and
``config.settings`` refuses to start with ``*`` in it once DEBUG is off --
correctly, because a wildcard there is a Host-header injection waiting to
happen.

So the rest of Phase S9 worked and the feature did not. A customer could
claim ``hr.theircompany.com``, publish the TXT record, watch it verify, point
the hostname at us -- and every request to it was answered with HTTP 400
"Invalid HTTP_HOST header", having never reached the resolver that knew the
domain was theirs. The only remedy was an operator editing an environment
variable and restarting the service for each customer, which is exactly the
manual step self-service custom domains exist to remove.

HOW THIS FIXES IT, AND WHY IT IS NOT A WILDCARD.

``django.http.request.validate_host`` is:

    any(pattern == "*" or is_same_domain(host, pattern)
        for pattern in allowed_hosts)

It ITERATES the setting. So a list subclass that yields the configured
patterns first and the verified custom hostnames after is enough, and it
keeps three properties the wildcard does not:

  * Only hostnames a customer PROVED they own are accepted -- a serving
    ``TenantDomain`` row, which exists only after DNS confirmed the token.
    An arbitrary Host header is refused exactly as before.
  * ``any()`` short-circuits and the static patterns come first, so the
    ordinary request -- on the platform's own base domain -- never looks at
    the dynamic part at all.
  * The dynamic part is a cached set, refreshed on a short TTL and evicted
    the moment a domain is verified or withdrawn, so it costs no query per
    request and a withdrawn domain stops being accepted at once.

WHY NOT MIDDLEWARE. There is nowhere to put it: the check happens inside
``get_host()``, so middleware either runs after the 400 has been raised or
has to call ``get_host()`` itself and hit the same exception. The setting is
the only hook Django offers here, which is why this is a setting.
"""
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "tenancy:serving-hostnames"
# Short, because this is the backstop rather than the mechanism: `claim`,
# `verify` and `remove` all evict it. A process that somehow missed the
# eviction converges within a minute instead of staying wrong until restart.
TTL = 60

# A ceiling, so this cannot become an unbounded per-request loop. Well past
# any realistic number of custom domains; if a deployment ever passes it, the
# warning says so rather than the setting silently truncating in the dark.
MAX_HOSTNAMES = 2000


def serving_hostnames():
    """Every hostname a verified custom domain is currently serving."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    hostnames = ()
    try:
        from .models import TenantDomain

        rows = list(TenantDomain.objects
                    .filter(status__in=TenantDomain.SERVING_STATUSES)
                    .values_list("hostname", flat=True)[:MAX_HOSTNAMES + 1])
        if len(rows) > MAX_HOSTNAMES:
            logger.error(
                "more than %s serving custom domains; only the first %s are "
                "accepted as hosts. Raise MAX_HOSTNAMES or move host "
                "validation to the edge.", MAX_HOSTNAMES, MAX_HOSTNAMES)
            rows = rows[:MAX_HOSTNAMES]
        hostnames = tuple(rows)
    except Exception:                                  # noqa: BLE001
        # BEFORE MIGRATIONS, AND DURING THEM, THERE IS NO TABLE. This is read
        # on the path of every request, including the first one a container
        # serves, so it must degrade to "no custom domains" rather than turn
        # a missing table into a 500 on the platform's own hostname.
        logger.debug("serving hostnames unavailable", exc_info=True)
        return ()

    cache.set(CACHE_KEY, hostnames, TTL)
    return hostnames


def forget():
    """Drop the cached hostnames. Called when a domain changes state."""
    cache.delete(CACHE_KEY)


class DynamicAllowedHosts(list):
    """``ALLOWED_HOSTS`` plus whatever customers have proved they own.

    Subclasses ``list`` rather than wrapping it so that everything else which
    reads this setting keeps working unchanged -- including
    ``config.settings``' own refusal to start with a wildcard in it, and
    Django's ``ALLOWED_HOSTS`` system check.
    """

    def __iter__(self):
        yield from list.__iter__(self)
        yield from serving_hostnames()

    def __contains__(self, item):
        if list.__contains__(self, item):
            return True
        return item in serving_hostnames()

    def __repr__(self):
        return f"DynamicAllowedHosts({list.__repr__(self)} + verified domains)"
