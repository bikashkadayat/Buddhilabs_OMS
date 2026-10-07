"""Per-tenant document numbers.

THE PROBLEM THIS SOLVES
-----------------------
Every number generator in the project hardcoded its prefix -- ``NIFN-TSK-``,
``NIF-INV-``, ``NIFN-EMP-`` -- and counted from a sequence row keyed on year
alone. Two consequences, both silent:

  * Tenant A creating a task advanced Tenant B's counter, so each tenant saw
    unexplained gaps in its own numbering and the gap-free guarantee those
    tables were deliberately built for was destroyed.
  * Two tenants would eventually mint the same string, colliding on the
    globally-unique number columns.

Phase S2 fixes the first by scoping the sequence tables to the organization,
and the second by routing every prefix through this module.

WHY THERE ARE TWO FORMAT FAMILIES
---------------------------------
Four of the existing formats carry NO organisation prefix at all -- ``MIN-``,
``CIR-``, ``TRF-``, ``DSP-``. Adding one would change the numbers NIF's own
documents are issued under, and Phase S2 is required to be invisible to NIF.
A document number is also a historical record: you cannot restate one that has
already been printed, signed and filed.

So an organization declares which family it uses:

  ``legacy_number_formats = True``   NIF, and only NIF. Emits exactly the
                                    strings the system has always emitted.
  ``legacy_number_formats = False``  Every future tenant. One consistent
                                    scheme, ``<PREFIX>-<KIND>-<year>-<n>``,
                                    prefixed throughout.

The flag is a deprecation seam, not a permanent fork: NIF can flip it whenever
it is willing to accept the modern format for NEW records, and nothing already
issued changes either way.
"""
from .scoping import active_organization

# Legacy literals, exactly as the generators hardcoded them before Phase S2.
# `None` means "this kind had no organisation prefix at all".
_LEGACY_PREFIX = {
    "TSK": "NIFN",      # NIFN-TSK-2083-0001
    "EMP": "NIFN",      # NIFN-EMP-2026-0001
    "MEMO": "NIFN",     # NIFN-HR-2026-0042  (kind is the memo type code)
    "LV": "NIFN",       # NIFN-LV-2026-0001
    "CERT": "NIFN",     # NIFN-CERT-2026-0001
    "INV": "NIF",       # NIF-INV-0001
    "OUT": "NIF",       # NIF-OUT-2083-0001
    "AR": "NIF",        # NIF-AR-2083-0001
    "RT": "NIF",        # NIF-RT-2083-0001
    "MT": "NIF",        # NIF-MT-2083-0001
    "MIN": None,        # MIN-2026-000001
    "CIR": None,        # CIR-2026-000001
    "TRF": None,        # TRF-2026-0001
    "DSP": None,        # DSP-2026-0001
}


def organization_for(organization=None):
    """The organization a number is being minted for."""
    return organization if organization is not None else active_organization()


def prefix_for(kind, organization=None):
    """The leading segment for ``kind``, including its trailing hyphen or ''.

    ``kind`` is the document class code: TSK, MEMO, MIN, CIR, INV, TRF, EMP...
    Memo uses its per-type code (HR, ADM, ...) and is handled by the caller
    passing ``kind="MEMO"`` for the prefix and writing the type code itself.
    """
    organization = organization_for(organization)

    if getattr(organization, "legacy_number_formats", False):
        legacy = _LEGACY_PREFIX.get(kind, "NIFN")
        return f"{legacy}-" if legacy else ""

    prefix = (organization.document_prefix or "").strip()
    return f"{prefix}-" if prefix else ""


def format_number(kind, *, year, value, width=4, organization=None,
                  type_code=None):
    """Build a document number.

        format_number("TSK", year=2083, value=1)
            -> "NIFN-TSK-2083-0001"      (NIF, legacy)
            -> "ABCS-TSK-2083-0001"      (another tenant)

        format_number("INV", year=None, value=1)
            -> "NIF-INV-0001"            (NIF, legacy -- not year-scoped)

    ``type_code`` replaces ``kind`` in the body while keeping ``kind``'s
    prefix rule, which is what memo numbers need (``NIFN-HR-2026-0042``).
    """
    prefix = prefix_for(kind, organization)
    body = type_code if type_code is not None else kind
    if year is None:
        return f"{prefix}{body}-{value:0{width}d}"
    return f"{prefix}{body}-{year}-{value:0{width}d}"
