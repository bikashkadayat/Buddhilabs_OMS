"""What every series must satisfy before a chart library sees it.

These are not stylistic checks. Recharts plots a ``Decimal`` serialised as a
string, and a ``NaN``, as **zero** -- silently. A dashboard that quietly reads
zero is worse than one that fails, so the contract is asserted mechanically
against the real API responses rather than trusted.
"""
import json

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

ENDPOINTS = [
    "analytics-executive", "analytics-hr", "analytics-management",
    "analytics-attendance", "analytics-departments", "analytics-leave",
    "analytics-wfh", "analytics-comp-off", "analytics-devices",
]

# Keys whose values are ordered, densified series.
SERIES_KEYS = {"trend", "series", "points", "monthly", "quarterly", "yearly",
               "consumption_trend", "sync_trend", "comparisons"}


@pytest.fixture
def populated(org, last_month_days, mark, take_leave, comp_entry, wfh_request,
              device, sync_log, enrol, balance):
    """A little of everything, so no series is trivially empty."""
    from attendance.models import Attendance

    for member in org["eng"]:
        mark(member, last_month_days[:-2])
    mark(org["ops"][0], last_month_days[:3], status=Attendance.Status.LATE,
         late_minutes=30)
    mark(org["ops"][1], last_month_days[:2], status=Attendance.Status.WORK_FROM_HOME,
         is_wfh=True)
    mark(org["ops"][2], last_month_days[:4], overtime_hours="2.00")
    take_leave(org["eng"][0], last_month_days[-2:])
    comp_entry(org["eng"][1], "1.00", source_date=last_month_days[0])
    wfh_request(org["ops"][1], last_month_days[0])
    balance(org["eng"][0])
    gate = device("Main Gate")
    sync_log(gate, when=last_month_days[0])
    enrol(gate, user=org["eng"][0])
    return org


def walk(node, path="data"):
    """Yield every (path, value) leaf in a payload."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")
    else:
        yield path, node


@pytest.mark.parametrize("url_name", ENDPOINTS)
class TestContracts:
    def test_payload_is_json_serialisable(self, url_name, populated, auth):
        response = auth(populated["admin"]).get(reverse(url_name))
        assert response.status_code == 200
        # allow_nan=False is the assertion: json.dumps happily emits a bare NaN
        # by default, which JSON.parse then rejects in the browser.
        json.dumps(response.data, allow_nan=False, default=str)

    def test_no_decimals_or_non_finite_numbers(self, url_name, populated, auth):
        from decimal import Decimal
        import math

        response = auth(populated["admin"]).get(reverse(url_name))
        for path, value in walk(response.data["data"]):
            assert not isinstance(value, Decimal), f"{path} is a Decimal"
            if isinstance(value, float):
                assert math.isfinite(value), f"{path} is {value}"

    def test_percentages_are_within_range(self, url_name, populated, auth):
        response = auth(populated["admin"]).get(reverse(url_name))
        for path, value in walk(response.data["data"]):
            if path.endswith("_pct") and value is not None:
                assert 0 <= value <= 100, f"{path} = {value}"

    def test_series_are_dense_and_ascending(self, url_name, populated, auth):
        response = auth(populated["admin"]).get(reverse(url_name))
        for path, series in _series(response.data["data"]):
            periods_in_series = [point["period"] for point in series
                                 if isinstance(point, dict) and "period" in point]
            if len(periods_in_series) < 2:
                continue
            assert periods_in_series == sorted(periods_in_series), f"{path} is unordered"
            assert len(set(periods_in_series)) == len(periods_in_series), \
                f"{path} has duplicate buckets"

    def test_every_point_carries_a_label(self, url_name, populated, auth):
        response = auth(populated["admin"]).get(reverse(url_name))
        for path, series in _series(response.data["data"]):
            for point in series:
                if isinstance(point, dict) and "period" in point:
                    assert point.get("label"), f"{path} has an unlabelled point"

    def test_envelope_is_complete(self, url_name, populated, auth):
        response = auth(populated["admin"]).get(reverse(url_name))
        for field in ("window", "scope", "generated_at", "cached", "data"):
            assert field in response.data, f"{url_name} envelope is missing {field}"


def _series(node, path="data"):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SERIES_KEYS and isinstance(value, list):
                yield f"{path}.{key}", value
            yield from _series(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _series(value, f"{path}[{index}]")


class TestDensity:
    def test_a_month_with_no_data_is_a_zero_point_not_a_gap(self, org, auth,
                                                            last_month_days, mark):
        """A hole in a chart reads as an outage. Zeros are the honest shape."""
        mark(org["eng"][0], last_month_days[:1])
        response = auth(org["hr"]).get(reverse("analytics-attendance"),
                                       {"period": "last_12m", "granularity": "month"})
        trend = response.data["data"]["trend"]
        assert len(trend) == 12
        assert all("compliance_pct" in point for point in trend)

    def test_granularity_downgrade_is_reported_not_silent(self, org, auth):
        response = auth(org["hr"]).get(reverse("analytics-attendance"), {
            "from": "2024-01-01", "to": "2026-12-31", "granularity": "day"})
        window = response.data["window"]
        assert window["truncated"] is True
        assert window["granularity"] != "day"

    def test_department_trend_reports_folded_series(self, org, auth):
        response = auth(org["hr"]).get(reverse("analytics-management"))
        trends = response.data["data"]["department_trends"]
        assert "folded_departments" in trends
        assert trends["max_series"] >= 1
        assert len(trends["series"]) <= trends["max_series"] + 1  # +1 for "Others"


class TestDefinitionsAreServed:
    def test_every_published_kpi_has_a_definition(self, org, auth):
        """A KPI nobody can define is a KPI nobody trusts."""
        response = auth(org["hr"]).get(reverse("analytics-meta"))
        definitions = response.data["definitions"]
        for key in ("present_pct", "absent_pct", "late_pct", "compliance_pct",
                    "expected_working_days", "department_health_score",
                    "leave_forecast", "utilization_pct"):
            assert definitions.get(key), f"{key} has no published definition"

    def test_health_weights_are_returned_with_the_score(self, org, auth):
        response = auth(org["hr"]).get(reverse("analytics-executive"))
        weights = response.data["data"]["health_weights"]
        assert pytest.approx(sum(weights.values())) == 1.0
