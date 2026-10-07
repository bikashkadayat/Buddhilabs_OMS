"""Suggestion engine.

The false-positive cases matter more than the hits: a wrong suggestion that HR
accepts at a glance misattributes attendance silently, so the engine must stay
quiet when it is not reasonably sure.
"""
import pytest

from biometric.matching import (
    MATCH_EMPLOYEE_CODE,
    MATCH_EXACT_NAME,
    MATCH_SIMILAR_NAME,
    employee_code_tail,
    normalize,
    score_candidate,
    suggest,
    suggest_for_mapping,
    tokenize,
)
from biometric.models import BiometricEmployee

from .conftest import make_user

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------ helpers

def test_normalize_strips_separators_and_case():
    assert normalize("Bikash Kadayat") == "bikashkadayat"
    assert normalize("BIKASH-KADAYAT") == "bikashkadayat"
    assert normalize("  Bikash   Kadayat  ") == "bikashkadayat"
    assert normalize(None) == ""


def test_tokenize():
    assert tokenize("Bikash Kadayat") == ["bikash", "kadayat"]
    assert tokenize("Bikashkadayat") == ["bikashkadayat"]
    assert tokenize("") == []


def test_employee_code_tail():
    assert employee_code_tail("NIFN-EMP-2026-0017") == 17
    assert employee_code_tail("17") == 17
    assert employee_code_tail("NIFN-EMP-2026-0000") == 0
    assert employee_code_tail("no-digits") is None
    assert employee_code_tail(None) is None


# ------------------------------------------------------------------ scoring

def test_exact_name_match_after_normalisation(db, dept):
    """The device stores names without spaces; the OMS splits them."""
    user = make_user("m1", dept=dept, first_name="Bikash", last_name="Khatri")
    score, reasons = score_candidate("Bikashkhatri", "1", user)
    assert score == 1.0
    assert MATCH_EXACT_NAME in reasons


def test_partial_name_is_a_similar_match(db, dept):
    """The example from the spec: device 'Bikash' -> OMS 'Bikash Kadayat'."""
    user = make_user("m2", dept=dept, first_name="Bikash", last_name="Kadayat")
    score, reasons = score_candidate("Bikash", "1", user)
    assert MATCH_SIMILAR_NAME in reasons
    assert 0.8 <= score < 1.0


def test_short_name_does_not_match_a_longer_unrelated_one(db, dept):
    """'Ram' must not match 'Ramesh' — substring matching would allow it.

    Both are real, distinct Nepali given names; conflating them would attribute
    one employee's attendance to another.
    """
    user = make_user("m3", dept=dept, first_name="Ramesh", last_name="Thapa")
    score, reasons = score_candidate("Ram", "1", user)
    assert score == 0.0
    assert not reasons


def test_completely_different_names_score_zero(db, dept):
    user = make_user("m4", dept=dept, first_name="Sita", last_name="Gurung")
    score, _ = score_candidate("Bikash", "1", user)
    assert score == 0.0


def test_employee_code_tail_match_is_a_signal(db, dept):
    """Device ID 17 lining up with NIFN-EMP-2026-0017 is worth surfacing."""
    user = make_user("m5", dept=dept, first_name="Unrelated", last_name="Name")
    user.employee_id = "NIFN-EMP-2026-0017"
    user.save(update_fields=["employee_id"])
    score, reasons = score_candidate("Unrelated Name", "17", user)
    assert MATCH_EMPLOYEE_CODE in reasons
    assert score > 0


def test_name_match_outranks_code_match(db, dept):
    """Spec priority: exact name beats employee code."""
    named = make_user("m6", dept=dept, first_name="Bikash", last_name="Kadayat")
    coded = make_user("m7", dept=dept, first_name="Someone", last_name="Else")
    coded.employee_id = "NIFN-EMP-2026-0017"
    coded.save(update_fields=["employee_id"])

    ranked = suggest("Bikashkadayat", "17", [coded, named])
    assert ranked[0]["user"] == named
    assert ranked[0]["match_type"] == MATCH_EXACT_NAME


def test_blank_device_name_yields_no_name_match(db, dept):
    user = make_user("m8", dept=dept, first_name="Bikash", last_name="Kadayat")
    score, reasons = score_candidate("", "999", user)
    assert MATCH_SIMILAR_NAME not in reasons and MATCH_EXACT_NAME not in reasons


# ------------------------------------------------------------------ ranking

def test_suggest_orders_by_score_and_respects_limit(db, dept):
    exact = make_user("s1", dept=dept, first_name="Bikash", last_name="Kadayat")
    partial = make_user("s2", dept=dept, first_name="Bikash", last_name="Shrestha")
    unrelated = make_user("s3", dept=dept, first_name="Sita", last_name="Gurung")

    ranked = suggest("Bikash Kadayat", "1", [unrelated, partial, exact])
    assert ranked[0]["user"] == exact
    assert unrelated not in [r["user"] for r in ranked]
    assert len(suggest("Bikash", "1", [exact, partial], limit=1)) == 1


def test_suggest_is_deterministic(db, dept):
    """Equal scores must not reorder between calls — HR sees a stable list."""
    a = make_user("d1", dept=dept, first_name="Bikash", last_name="Kadayat")
    b = make_user("d2", dept=dept, first_name="Bikash", last_name="Adhikari")
    first = [r["user"].pk for r in suggest("Bikash", "1", [a, b])]
    second = [r["user"].pk for r in suggest("Bikash", "1", [b, a])]
    assert first == second


def test_suggest_for_mapping_excludes_users_already_mapped_on_that_device(device, dept):
    """Offering an already-mapped user would only produce a constraint error."""
    taken = make_user("x1", dept=dept, first_name="Bikash", last_name="Kadayat")
    free = make_user("x2", dept=dept, first_name="Bikash", last_name="Kadayat")
    BiometricEmployee.objects.create(device=device, device_user_id="5", user=taken)

    unmapped = BiometricEmployee.objects.create(
        device=device, device_user_id="6", device_name="Bikash Kadayat")
    suggested = [r["user"] for r in suggest_for_mapping(unmapped)]
    assert free in suggested
    assert taken not in suggested


def test_suggest_for_mapping_allows_a_user_mapped_on_a_DIFFERENT_device(
        device, second_device, dept):
    """Multi-device: being enrolled at Head Office must not block Branch."""
    user = make_user("x3", dept=dept, first_name="Bikash", last_name="Kadayat")
    BiometricEmployee.objects.create(device=second_device, device_user_id="5", user=user)

    unmapped = BiometricEmployee.objects.create(
        device=device, device_user_id="6", device_name="Bikash Kadayat")
    assert user in [r["user"] for r in suggest_for_mapping(unmapped)]
