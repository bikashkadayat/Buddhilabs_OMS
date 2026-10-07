"""
The evidence contract (Phase T6.1).

WHAT THIS APP IS
----------------
A CONTRACT and a REGISTRY. Nothing else. It has no models, no migrations, no
views, no business logic, and — deliberately — no import of any business module.
It declares the SHAPE that evidence about a person takes, and provides a place
for a module to register itself as a source of it.

WHY IT IS ITS OWN APP RATHER THAN LIVING INSIDE `tasks`
-------------------------------------------------------
Phase T6 asks for a CENTRAL registry naming seven eventual sources — task, memo,
minute, circular, attendance, leave and inventory. A registry that lived inside
`tasks` would either make every other module depend on the task module in order
to publish evidence, or would quietly become "task evidence with aspirations".
Neither is a central registry.

Putting it here inverts the dependency: `tasks` imports `evidence` (a contract),
not the other way round, and the six modules that are not yet integrated import
nothing at all. When one of them is wired up, it registers a provider and this
app still does not know it exists.

WHAT IT MUST NEVER GROW
-----------------------
No scoring. No weighting. No ranking. No comparison between people. This is a
description of what happened, in units a human can check against records. The
moment a weight appears here, every consumer inherits a judgement that nobody
made openly — and consumers cannot see it to argue with it.

That prohibition is enforced by `evidence/tests/test_contract.py`, not by
convention.
"""
from dataclasses import dataclass, field
from enum import Enum

# The contract version. Consumers pin this.
#
# Bumped when a metric is REMOVED or its meaning changes — never when one is
# added, because an added metric cannot break a consumer that does not read it.
# A future appraisal module reads this to know whether the evidence it was
# written against is the evidence it is being handed.
CONTRACT_VERSION = "1.0"


class Unit(str, Enum):
    """
    What a number IS. A consumer that renders "12" needs to know whether that is
    a count, a percentage or a duration, and guessing from the field name is how
    days get displayed as a percentage.
    """
    COUNT = "count"
    PERCENT = "percent"
    DAYS = "days"


class Source(str, Enum):
    """
    Every module that may eventually publish evidence about a person.

    Declared in full NOW, with only TASK implemented, because the list is the
    architecture: it says what this registry is for and stops the first
    integration inventing its own shape. Six of these have no provider and
    `available_sources()` reports exactly that, rather than pretending.
    """
    TASK = "task"
    MEMO = "memo"
    MINUTE = "minute"
    CIRCULAR = "circular"
    ATTENDANCE = "attendance"
    LEAVE = "leave"
    INVENTORY = "inventory"


class PeriodType(str, Enum):
    """The windows evidence is aggregated over (Phase T6.3)."""
    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


@dataclass(frozen=True)
class Metric:
    """
    One piece of evidence: a number, what it is, and what it was measured over.

    `basis_of` is not optional decoration. A percentage without its denominator
    is the single most reliable way for a figure to be misquoted — "75%" means
    something very different over four tasks and over four hundred — so every
    ratio names the count it came from, and a contract test refuses one that
    does not.

    `definition` is shipped to consumers rather than kept in documentation,
    because the consumer is the one who has to explain the number to the person
    it is about.
    """
    key: str
    label: str
    unit: Unit
    value: float | int | None
    definition: str
    # For a PERCENT metric: the key of the count it was calculated over.
    basis_of: str | None = None
    # True when None means "no data", as distinct from zero. An average with no
    # completed tasks is not "0 days".
    null_means_no_data: bool = True

    def as_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit.value,
            "value": self.value,
            "definition": self.definition,
            "basis_of": self.basis_of,
        }


@dataclass(frozen=True)
class EvidenceSet:
    """
    Everything one source knows about one person over one window.

    Carries its own provenance — who, which source, which window, which contract
    version — so a set that has been serialised, stored and reopened a year
    later still says what it is. Evidence detached from its period is not
    evidence.
    """
    source: Source
    employee_id: str
    employee_name: str
    period_start: str
    period_end: str
    period_type: PeriodType
    metrics: list = field(default_factory=list)
    contract_version: str = CONTRACT_VERSION

    def as_dict(self):
        return {
            "source": self.source.value,
            "contract_version": self.contract_version,
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "period_type": self.period_type.value,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "metrics": [metric.as_dict() for metric in self.metrics],
            # Travels with every set, so a consumer cannot present it as
            # something it is not without deleting the sentence that says so.
            "disclaimer": DISCLAIMER,
        }


DISCLAIMER = (
    "Counts and durations describing recorded activity. This is evidence, not "
    "an assessment: nothing here is scored, weighted, ranked or compared "
    "between people, and it does not by itself support a judgement about "
    "anyone's performance."
)
