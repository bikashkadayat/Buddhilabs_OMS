"""Mapping suggestion engine.

Proposes which OMS employee a device roster entry probably is. It only ever
*proposes* — nothing here writes a mapping. Every suggestion needs an explicit
HR decision, because a wrong auto-mapping silently misattributes attendance and
is very hard to notice after the fact.

Matching priority, highest first:
  1. Exact name        — device "Bikashkhatri" == OMS "Bikash Khatri"
  2. Similar name      — device "Bikash"       ~= OMS "Bikash Kadayat"
  3. Employee code     — device ID "17"        == tail of "NIFN-EMP-2026-0017"
  4. Manual selection  — no suggestion offered

Stdlib ``difflib`` only; deliberately no new dependency for this.
"""
import difflib
import re

# Scores are ordered to encode the priority above, not calibrated probabilities.
SCORE_EXACT_NAME = 1.0
SCORE_TOKEN_SUBSET = 0.88   # every device name token appears in the OMS name
SCORE_EMPLOYEE_CODE = 0.60
FUZZY_FLOOR = 0.72          # below this, a fuzzy name match is noise

MATCH_EXACT_NAME = "exact_name"
MATCH_SIMILAR_NAME = "similar_name"
MATCH_EMPLOYEE_CODE = "employee_code"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_EMPLOYEE_CODE_TAIL = re.compile(r"(\d+)\s*$")


def normalize(name):
    """Lowercase, strip everything non-alphanumeric, collapse to one string.

    Device enrolment names arrive without spaces ("Bikashkhatri") while the OMS
    stores them split ("Bikash Khatri"), so comparison has to happen with
    separators removed.
    """
    return _NON_ALNUM.sub("", (name or "").lower())


def tokenize(name):
    """Lowercase alphanumeric tokens. Empty for a blank name."""
    return [t for t in _NON_ALNUM.sub(" ", (name or "").lower()).split() if t]


def employee_code_tail(value):
    """Trailing integer of an employee code, e.g. NIFN-EMP-2026-0017 -> 17.

    Returns None when there is no trailing number.
    """
    match = _EMPLOYEE_CODE_TAIL.search(str(value or "").strip())
    return int(match.group(1)) if match else None


def score_candidate(device_name, device_user_id, user):
    """Score one (device roster entry, OMS user) pair.

    Returns ``(score, reasons)`` where reasons lists every signal that fired.
    Score 0.0 means "no suggestion" — the caller should require manual selection.
    """
    reasons = []
    score = 0.0

    device_norm = normalize(device_name)
    user_norm = normalize(user.get_full_name())

    if device_norm and user_norm:
        if device_norm == user_norm:
            score = SCORE_EXACT_NAME
            reasons.append(MATCH_EXACT_NAME)
        else:
            device_tokens = set(tokenize(device_name))
            user_tokens = set(tokenize(user.get_full_name()))
            # Token subset rather than substring: "ram" must not match "Ramesh".
            # Comparing whole tokens keeps a short given name from matching a
            # longer unrelated one, which raw containment would allow.
            if device_tokens and device_tokens <= user_tokens:
                score = SCORE_TOKEN_SUBSET
                reasons.append(MATCH_SIMILAR_NAME)
            else:
                ratio = difflib.SequenceMatcher(None, device_norm, user_norm).ratio()
                if ratio >= FUZZY_FLOOR:
                    score = ratio
                    reasons.append(MATCH_SIMILAR_NAME)

    code_tail = employee_code_tail(getattr(user, "employee_id", None))
    device_tail = employee_code_tail(device_user_id)
    if code_tail is not None and device_tail is not None and code_tail == device_tail:
        reasons.append(MATCH_EMPLOYEE_CODE)
        score = max(score, SCORE_EMPLOYEE_CODE)

    return score, reasons


def suggest(device_name, device_user_id, candidates, limit=5):
    """Rank candidate users for one device roster entry.

    ``candidates`` is any iterable of User rows — the caller decides the pool
    (typically active users without an active mapping on that device).
    """
    scored = []
    for user in candidates:
        score, reasons = score_candidate(device_name, device_user_id, user)
        if score <= 0:
            continue
        scored.append({
            "user": user,
            "score": round(score, 4),
            "reasons": reasons,
            "match_type": reasons[0] if reasons else None,
        })
    # Stable tie-break on name so identical scores do not reorder between calls.
    scored.sort(key=lambda s: (-s["score"], s["user"].get_full_name()))
    return scored[:limit]


def suggest_for_mapping(mapping, candidates=None, limit=5):
    """Suggestions for an unmapped BiometricEmployee row.

    Excludes anyone who already holds an active mapping on the same device —
    those would be rejected by the uniqueness constraint anyway, so offering
    them would only produce a confusing failure.
    """
    from django.contrib.auth import get_user_model

    from .models import BiometricEmployee

    if candidates is None:
        taken = BiometricEmployee.objects.filter(
            device=mapping.device, is_active=True, user__isnull=False,
        ).exclude(pk=mapping.pk).values_list("user_id", flat=True)
        candidates = get_user_model().objects.filter(is_active=True).exclude(pk__in=list(taken))
    return suggest(mapping.device_name, mapping.device_user_id, candidates, limit=limit)
