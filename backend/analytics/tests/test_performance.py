"""Query budgets and the cache contract.

Budgets are constants, exactly as ``BUDGET_ME = 12`` is in the Phase 9
dashboard suite. Lowering one is a win; raising one needs a comment saying why.

Wall-clock is measured and PRINTED but only loosely asserted. A CI runner on
SQLite is not the production Postgres these numbers were designed for, so a
hard 250 ms assertion here would either fail on a noisy runner or be so loose it
proves nothing. The number that actually protects the budget is the query count:
if that holds, the latency follows on real hardware.
"""
import time

import pytest
from django.db import connection
from django.urls import reverse

pytestmark = pytest.mark.django_db

# Cold-cache query budgets, per the approved performance strategy.
#
# `analytics-hr` is the outlier at 25 (the design estimated 20 before the code
# existed). It composes six domains in one payload — attendance, corrections,
# approval turnaround, queues, device mapping, WFH and comp-off — and each is
# already a fixed, grouped set of queries. The count is CONSTANT: it does not
# move with headcount, departments or window length, which is what the budget is
# actually protecting. Splitting the page into six requests to make one number
# smaller would be worse for the user and no cheaper for the database.
BUDGETS = {
    "analytics-executive": 18,
    "analytics-hr": 25,
    "analytics-management": 20,
    "analytics-attendance": 14,
    "analytics-departments": 16,
    "analytics-leave": 12,
    "analytics-wfh": 10,
    "analytics-comp-off": 9,
    "analytics-devices": 10,
    "analytics-meta": 4,
}
# Warm cache: the auth lookup, and nothing else touching the analytics tables.
WARM_BUDGET = 4

# Advisory only -- see the module docstring.
SOFT_LATENCY_BUDGET_MS = 2000


@pytest.fixture
def seeded(org, last_month_days, mark, take_leave, comp_entry, wfh_request,
           device, sync_log, enrol, balance):
    """Enough breadth that no query path is trivially empty."""
    from attendance.models import Attendance

    for member in org["eng"]:
        mark(member, last_month_days, overtime_hours="1.00")
    for member in org["ops"]:
        mark(member, last_month_days[:-3], status=Attendance.Status.LATE,
             late_minutes=15)
    for member in org["tiny"]:
        mark(member, last_month_days[:2])
    take_leave(org["eng"][0], last_month_days[-2:])
    comp_entry(org["eng"][1], "2.00", source_date=last_month_days[0])
    wfh_request(org["ops"][0], last_month_days[0])
    balance(org["eng"][0])
    gate = device("Main Gate")
    sync_log(gate, when=last_month_days[0])
    enrol(gate, user=org["eng"][0])
    return org


@pytest.mark.parametrize("url_name,budget", sorted(BUDGETS.items()))
def test_cold_cache_query_budget(url_name, budget, seeded, auth,
                                 django_assert_max_num_queries,
                                 tenant_binding_queries):
    client = auth(seeded["admin"])
    # + the tenant binding, which is one round trip per request under RLS and
    # zero otherwise. See the fixture for why it is budgeted, not excluded.
    with django_assert_max_num_queries(budget + tenant_binding_queries):
        response = client.get(reverse(url_name))
    assert response.status_code == 200


@pytest.mark.parametrize("url_name", sorted(BUDGETS))
def test_warm_cache_touches_almost_nothing(url_name, seeded, auth,
                                           django_assert_max_num_queries,
                                           tenant_binding_queries):
    """The point of the cache: a second identical request does no analytics work."""
    client = auth(seeded["admin"])
    first = client.get(reverse(url_name))
    assert first.status_code == 200

    if url_name == "analytics-meta":
        pytest.skip("meta is not cached: it is already a two-query endpoint")

    with django_assert_max_num_queries(WARM_BUDGET + tenant_binding_queries):
        second = client.get(reverse(url_name))
    assert second.status_code == 200
    assert second.data["cached"] is True
    assert second.data["data"] == first.data["data"]


@pytest.mark.parametrize("url_name", sorted(BUDGETS))
def test_latency_is_reported(url_name, seeded, auth, capsys):
    client = auth(seeded["admin"])
    started = time.perf_counter()
    response = client.get(reverse(url_name))
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert response.status_code == 200
    with capsys.disabled():
        # Report the real backend rather than a hardcoded "sqlite": these numbers
        # are only comparable against runs on the same engine, and CI runs on
        # PostgreSQL while a local run defaults to SQLite.
        print(f"  [latency] {url_name:28s} {elapsed_ms:7.1f} ms "
              f"(cold, {connection.vendor})")
    assert elapsed_ms < SOFT_LATENCY_BUDGET_MS, (
        f"{url_name} took {elapsed_ms:.0f}ms, far past anything explainable by "
        f"a slow runner -- this is a query-shape regression, not noise.")


class TestNoNPlusOne:
    def test_hr_dashboard_count_is_flat_as_the_organisation_grows(
            self, org, dept_ops, last_month_days, mark, auth,
            django_assert_max_num_queries, tenant_binding_queries):
        """The heaviest endpoint is the one whose constancy matters most."""
        from analytics.tests.conftest import make_user

        client = auth(org["hr"])
        with django_assert_max_num_queries(
                BUDGETS["analytics-hr"] + tenant_binding_queries):
            client.get(reverse("analytics-hr"))

        for index in range(20):
            mark(make_user(f"an_hrbulk_{index}", dept=dept_ops), last_month_days[:3])

        with django_assert_max_num_queries(
                BUDGETS["analytics-hr"] + tenant_binding_queries):
            response = client.get(reverse("analytics-hr"), {"period": "last_6m"})
        assert response.status_code == 200

    def test_query_count_is_flat_as_the_organisation_grows(
            self, org, dept_ops, last_month_days, mark, auth,
            django_assert_max_num_queries, tenant_binding_queries):
        """The real N+1 guard: doubling the headcount must not move the count."""
        from analytics.tests.conftest import make_user

        client = auth(org["admin"])
        with django_assert_max_num_queries(
                BUDGETS["analytics-executive"] + tenant_binding_queries):
            client.get(reverse("analytics-executive"))

        newcomers = [make_user(f"an_bulk_{i}", dept=dept_ops) for i in range(25)]
        for member in newcomers:
            mark(member, last_month_days[:3])

        # A different window so the first response's cache entry cannot serve
        # this one -- otherwise the assertion would pass trivially.
        with django_assert_max_num_queries(
                BUDGETS["analytics-executive"] + tenant_binding_queries):
            response = client.get(reverse("analytics-executive"),
                                  {"period": "last_6m"})
        assert response.status_code == 200
        assert response.data["scope"]["headcount"] == 37

    def test_query_count_is_flat_as_departments_grow(
            self, org, last_month_days, mark, auth, django_assert_max_num_queries,
            tenant_binding_queries):
        """Department rollups must be grouped queries, not a query per department."""
        from leaves.models import Department

        from analytics.tests.conftest import make_user

        for index in range(6):
            extra = Department.objects.create(name=f"Extra {index}",
                                              code=f"AN-X{index}")
            for member_index in range(3):
                member = make_user(f"an_x{index}_{member_index}", dept=extra)
                mark(member, last_month_days[:4])

        client = auth(org["admin"])
        with django_assert_max_num_queries(
                BUDGETS["analytics-departments"] + tenant_binding_queries):
            response = client.get(reverse("analytics-departments"))
        assert response.status_code == 200
        assert len(response.data["data"]["departments"]) >= 9


class TestCacheBehaviour:
    def test_different_windows_do_not_share_an_entry(self, seeded, auth):
        client = auth(seeded["admin"])
        first = client.get(reverse("analytics-attendance"), {"period": "last_6m"})
        second = client.get(reverse("analytics-attendance"), {"period": "last_12m"})
        assert first.data["cached"] is False
        assert second.data["cached"] is False
        assert first.data["window"]["from"] != second.data["window"]["from"]

    def test_two_managers_in_one_department_share_an_entry(self, org, auth,
                                                           dept_eng):
        """Keying on scope rather than user id is what makes the cache hit at all."""
        from analytics.tests.conftest import make_user

        second_head = make_user("an_head_2", org["manager"].role, dept_eng)
        auth(org["manager"]).get(reverse("analytics-attendance"))

        from rest_framework.test import APIClient

        other = APIClient()
        other.force_authenticate(user=second_head)
        response = other.get(reverse("analytics-attendance"))
        assert response.data["cached"] is True

    def test_a_backdated_write_retires_cached_history(self, seeded, auth,
                                                      last_month_days):
        """A correction to a closed month must not wait out a 24-hour TTL."""
        from decimal import Decimal

        from attendance.models import Attendance

        client = auth(seeded["admin"])
        first = client.get(reverse("analytics-attendance"))
        assert client.get(reverse("analytics-attendance")).data["cached"] is True

        Attendance.objects.create(
            employee=seeded["ops"][0], date=last_month_days[-1],
            status=Attendance.Status.PRESENT, working_hours=Decimal("8.00"))

        refreshed = client.get(reverse("analytics-attendance"))
        assert refreshed.data["cached"] is False
        assert refreshed.data["data"]["kpis"]["attended_days"] > \
            first.data["data"]["kpis"]["attended_days"]

    def test_a_cache_outage_degrades_instead_of_failing(self, seeded, auth,
                                                        monkeypatch):
        """Losing Redis must make analytics slower, never broken.

        ``ResilientRedisCache`` turns a backend error into a miss on read and a
        dropped write, so an outage is simulated here by forcing exactly that:
        every read misses, every write is discarded. The endpoint must still
        answer, and answer identically.
        """
        client = auth(seeded["admin"])
        warm = client.get(reverse("analytics-attendance"))
        assert warm.status_code == 200

        from analytics import cache as analytics_cache

        monkeypatch.setattr(analytics_cache.cache, "get",
                            lambda key, default=None, **kwargs: default)
        monkeypatch.setattr(analytics_cache.cache, "set",
                            lambda *args, **kwargs: False)

        degraded = client.get(reverse("analytics-attendance"))
        assert degraded.status_code == 200
        assert degraded.data["cached"] is False
        assert degraded.data["data"] == warm.data["data"]
