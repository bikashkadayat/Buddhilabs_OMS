"""
The evidence source registry (Phase T6.1).

A module publishes evidence by registering a PROVIDER — a callable that answers
"what does this source know about this person over this window" and returns an
`EvidenceSet`. The registry never imports a provider; providers register
themselves from their own app's `ready()`, so this module has no knowledge of
any business module and adding the seventh source requires no change here.

READ-ONLY, BY CONSTRUCTION
--------------------------
A provider is handed a user and two dates and returns a value object. There is
no write path — nothing in this app can change anybody's record, and a provider
that tried would have nowhere to put it.

NOT INTEGRATED YET, AND HONEST ABOUT IT
---------------------------------------
Six of the seven declared sources have no provider. `available()` reports which
are live and which are declared-but-absent, rather than returning an empty set
that a consumer would read as "this person has done nothing" — the difference
between "no data" and "no evidence" being exactly the kind of thing that gets
somebody unfairly appraised.
"""
import logging

from .schema import Source

logger = logging.getLogger("evidence")

_PROVIDERS = {}


def register(source, provider):
    """
    Register one source's provider. Idempotent — re-registering the same source
    replaces it rather than accumulating, so a double `ready()` in a test cannot
    produce two answers.
    """
    source = Source(source)
    if source in _PROVIDERS and _PROVIDERS[source] is not provider:
        logger.info("Evidence provider for %s replaced.", source.value)
    _PROVIDERS[source] = provider
    return provider


def unregister(source):
    """Used by tests that need the registry bare. Not called in production."""
    _PROVIDERS.pop(Source(source), None)


def provider_for(source):
    return _PROVIDERS.get(Source(source))


def available():
    """
    Every declared source and whether it can actually answer.

    Reports the absent ones rather than hiding them: a consumer needs to know
    that leave evidence is UNAVAILABLE, not merely empty, before drawing any
    conclusion from its absence.
    """
    return [
        {"source": source.value, "available": source in _PROVIDERS}
        for source in Source
    ]


def collect(source, user, period_start, period_end, period_type, **kwargs):
    """
    Ask one source for its evidence. Returns None when that source has no
    provider — never a fabricated empty set.
    """
    provider = provider_for(source)
    if provider is None:
        return None
    return provider(user, period_start, period_end, period_type, **kwargs)


def collect_all(user, period_start, period_end, period_type, **kwargs):
    """
    Every registered source, for one person over one window.

    A provider that raises is logged and SKIPPED rather than failing the whole
    collection — one module's bug must not make a person's entire record
    unavailable, and a partial record that says which parts are missing is more
    useful than none.
    """
    sets, unavailable = [], []
    for source in Source:
        provider = provider_for(source)
        if provider is None:
            unavailable.append(source.value)
            continue
        try:
            sets.append(provider(user, period_start, period_end, period_type,
                                 **kwargs))
        except Exception:  # noqa: BLE001
            logger.exception("Evidence provider %s failed.", source.value)
            unavailable.append(source.value)
    return {"sets": sets, "unavailable": unavailable}
