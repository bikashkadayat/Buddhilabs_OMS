"""
Phase T6.1 / T6.8 — the evidence contract itself.

This file guards the ARCHITECTURE rather than any number. The registry's value
is entirely in what it refuses to become: if a weight, a score or a ranking
appears in the contract, every consumer inherits a judgement nobody made openly
and cannot see in order to argue with it.
"""
import pytest

from evidence import registry
from evidence.schema import (
    CONTRACT_VERSION, DISCLAIMER, EvidenceSet, Metric, PeriodType, Source, Unit,
)

FORBIDDEN = ("score", "rating", "rank", "grade", "band", "percentile",
             "weight", "index", "appraisal")


# ---------------------------------------------------------------------------
# What the contract must never become
# ---------------------------------------------------------------------------
def test_the_schema_declares_no_judgement_concept():
    """
    Not a naming convention — a structural guarantee. There is nowhere in this
    schema to PUT a score, which is why a consumer cannot be handed one.
    """
    import evidence.schema as schema

    for name in dir(schema):
        if name.startswith("_"):
            continue
        assert not any(word in name.lower() for word in FORBIDDEN), name


def test_a_metric_has_no_weight_field():
    fields = set(Metric.__dataclass_fields__)
    assert fields == {"key", "label", "unit", "value", "definition",
                      "basis_of", "null_means_no_data"}
    for name in fields:
        assert not any(word in name for word in FORBIDDEN), name


def test_an_evidence_set_has_no_aggregate_or_total_score():
    fields = set(EvidenceSet.__dataclass_fields__)
    for name in fields:
        assert not any(word in name for word in FORBIDDEN), name


def test_units_are_only_things_a_person_can_check():
    """
    Count, percent and days. A unit like "points" would be a score by another
    name — nobody can verify a point against a record.
    """
    assert {u.value for u in Unit} == {"count", "percent", "days"}


def test_every_serialised_set_carries_the_disclaimer():
    """
    So a consumer cannot lift the numbers and leave the caveat behind — it would
    have to delete the sentence deliberately.
    """
    payload = EvidenceSet(
        source=Source.TASK, employee_id="1", employee_name="A Person",
        period_start="2026-01-01", period_end="2026-12-31",
        period_type=PeriodType.ANNUAL, metrics=[]).as_dict()
    assert payload["disclaimer"] == DISCLAIMER
    assert "not an assessment" in DISCLAIMER
    assert "not" in DISCLAIMER and "compared between people" in DISCLAIMER


def test_the_contract_is_versioned():
    """A consumer pins this to know the evidence it was written against is the
    evidence it is being handed."""
    assert CONTRACT_VERSION
    payload = EvidenceSet(
        source=Source.TASK, employee_id="1", employee_name="A",
        period_start="a", period_end="b", period_type=PeriodType.DAILY).as_dict()
    assert payload["contract_version"] == CONTRACT_VERSION


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
def test_all_seven_sources_are_declared():
    """
    The list IS the architecture: it says what the registry is for and stops the
    first integration inventing its own shape.
    """
    assert {s.value for s in Source} == {
        "task", "memo", "minute", "circular", "attendance", "leave", "inventory"}


def test_only_task_is_integrated_and_the_rest_say_so(db):
    """
    Absent evidence must be distinguishable from empty evidence. "This person
    has no leave evidence" and "leave is not connected" are very different
    claims to make about somebody.
    """
    rows = {row["source"]: row["available"] for row in registry.available()}
    assert rows["task"] is True
    for source in ("memo", "minute", "circular", "attendance", "leave",
                   "inventory"):
        assert rows[source] is False, source


def test_collecting_from_an_unregistered_source_returns_none_not_an_empty_set(db):
    """An empty set would be read as "nothing happened"; None is "we don't know"."""
    assert registry.collect(Source.LEAVE, None, "2026-01-01", "2026-12-31",
                            PeriodType.ANNUAL) is None


def test_registering_twice_replaces_rather_than_duplicates(db):
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        return None

    registry.register(Source.MEMO, provider)
    registry.register(Source.MEMO, provider)
    try:
        registry.collect(Source.MEMO, None, "a", "b", PeriodType.DAILY)
        assert len(calls) == 1
    finally:
        registry.unregister(Source.MEMO)


def test_one_failing_provider_does_not_lose_the_whole_record(db):
    """
    A partial record that names what is missing is more useful than none — and
    one module's bug must not make a person's entire evidence unavailable.
    """
    def broken(*args, **kwargs):
        raise RuntimeError("that module is having a bad day")

    registry.register(Source.MEMO, broken)
    try:
        result = registry.collect_all(None, "2026-01-01", "2026-12-31",
                                      PeriodType.ANNUAL)
    finally:
        registry.unregister(Source.MEMO)

    assert "memo" in result["unavailable"]
    assert "leave" in result["unavailable"]      # never registered


def test_the_registry_has_no_write_path():
    """
    A provider is handed a person and two dates and returns a value object.
    There is nowhere for it to put a change, which is what makes "read-only"
    structural rather than a promise.
    """
    public = [name for name in dir(registry) if not name.startswith("_")]
    for name in public:
        assert not any(word in name for word in
                       ("save", "write", "update", "delete", "create")), name
