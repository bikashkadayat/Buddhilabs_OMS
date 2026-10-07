"""
Phase APM-FINAL.1 Part 7 — appraisal scale audit.

DESELECTED BY DEFAULT. These seed hundreds of appraisals and take minutes; they
are audit instrumentation, not part of the ordinary suite. Run them
deliberately:

    pytest -m slow -s appraisal/tests/test_scale.py

WHAT THEY MEASURE, AND WHAT THEY DO NOT
---------------------------------------
Query COUNT, not wall-clock time — the same discipline as the task module's
scale audit. A timing assertion on a laptop tells you about the laptop; a query
count is a property of the code and holds on any database.

The failure mode worth catching is the one that is invisible at 20 appraisals
and fatal at 500: a query per row. Each test measures the SAME endpoint at two
sizes and asserts the count did not move.

WHY THIS FILE EXISTS AT ALL
---------------------------
The APM-03b audit recorded that appraisal had no query-count coverage while
tasks and analytics did. The HR dashboard is the surface at risk: it is the only
one that reads every appraisal in the organisation, and three of its blocks —
department progress, review delays and the development-plan summary — were
written as loops over a queryset.
"""
import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from appraisal.models import Appraisal, DevelopmentPlan, Goal, TrainingPlan
from appraisal.services import user_snapshot
from users.models import User
from .conftest import APPRAISALS, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

Status = Appraisal.Status
_BATCH = [0]

# The stages a seeded appraisal is spread across. Deliberately includes the
# three review stages, because those are what the review-delay block reads.
SPREAD = [Status.GOAL_SETTING, Status.GOAL_APPROVAL, Status.MID_YEAR,
          Status.SELF_ASSESSMENT, Status.SUPERVISOR_REVIEW, Status.COMMITTEE,
          Status.FINAL_REVIEW, Status.DEVELOPMENT_PLAN, Status.TRAINING_PLAN,
          Status.CLOSED]

READINESS = ["", "ready", "ready_with_development", "development_required"]
KINDS = [k for k, _ in TrainingPlan.Kind.choices]


def seed(cycle, supervisor, departments, count):
    """
    `count` appraisals across every stage, with goals, development actions and
    training rows attached.

    Built with bulk_create rather than through the workflow: driving 500
    appraisals through ten transitions each would take an hour and would be
    measuring the engine. The SHAPES still vary — stages, departments, promotion
    answers, training kinds — because a table of identical rows exercises one
    query plan and hides the rest.
    """
    _BATCH[0] += 1
    batch = _BATCH[0]
    depts = list(departments.values())

    # bulk_create skips pre_save, so the organization must be set explicitly
    # or the user_tenant_has_organization check constraint rejects the batch.
    from tenancy.scoping import active_organization

    organization = active_organization()
    people = User.objects.bulk_create([
        User(username=f"scale{batch}_{i}", email=f"scale{batch}_{i}@nif.test",
             first_name=f"Scale{batch}", last_name=str(i),
             organization=organization,
             role=User.Roles.MAKER, designation="Officer",
             department=depts[i % len(depts)].name,
             department_ref=depts[i % len(depts)])
        for i in range(count)])

    appraisals = Appraisal.objects.bulk_create([
        Appraisal(
            cycle=cycle, employee=person,
            employee_name=person.get_full_name(), designation="Officer",
            department=person.department_ref,
            department_name=person.department_ref.name,
            supervisor=supervisor,
            supervisor_name=user_snapshot(supervisor)["name"],
            status=SPREAD[i % len(SPREAD)],
            promotion_readiness=READINESS[i % len(READINESS)],
            promotion_rationale="Recorded reasoning." if i % 4 else "",
            successor_for="Senior Officer" if i % 5 == 0 else "",
            final_summary="Summary." if i % 3 == 0 else "")
        for i, person in enumerate(people)])

    Goal.objects.bulk_create([
        Goal(appraisal=a, objective=f"Objective {n}", weight=50,
             owner=a.employee, owner_name=a.employee_name, position=n,
             status=Goal.Status.APPROVED)
        for a in appraisals for n in range(2)])

    DevelopmentPlan.objects.bulk_create([
        DevelopmentPlan(appraisal=a, area=f"Area {i % 7}",
                        action="Something specific will be done.")
        for i, a in enumerate(appraisals)])

    TrainingPlan.objects.bulk_create([
        TrainingPlan(appraisal=a, title=f"Course {i % 9}",
                     kind=KINDS[i % len(KINDS)])
        for i, a in enumerate(appraisals)])
    return appraisals


def clear():
    Goal.objects.all().delete()
    DevelopmentPlan.objects.all().delete()
    TrainingPlan.objects.all().delete()
    Appraisal.objects.all().delete()
    User.objects.filter(username__startswith="scale").delete()


@pytest.mark.parametrize("name,url", [
    ("HR dashboard", f"{APPRAISALS}dashboard/"),
    ("appraisal list", APPRAISALS),
    ("appraisal summary report", f"{APPRAISALS}reports/appraisal-summary/"),
    ("department summary report", f"{APPRAISALS}reports/department-summary/"),
    ("goal completion report", f"{APPRAISALS}reports/goal-completion/"),
    ("training needs report", f"{APPRAISALS}reports/training-needs/"),
    ("development plans report", f"{APPRAISALS}reports/development-plans/"),
    ("promotion readiness report", f"{APPRAISALS}reports/promotion-readiness/"),
])
def test_query_count_does_not_grow_with_the_organisation(
        cast, auth, cycle, departments, name, url):
    """
    The one shape that matters. A count that holds from 50 to 250 appraisals
    holds at 2,500; a count that triples has a query per row in it and will take
    the page down at organisation scale.
    """
    seed(cycle, cast["supervisor"], departments, 50)
    with CaptureQueriesContext(connection) as small:
        assert auth(cast["hr"]).get(url).status_code == 200
    clear()

    seed(cycle, cast["supervisor"], departments, 250)
    with CaptureQueriesContext(connection) as large:
        assert auth(cast["hr"]).get(url).status_code == 200

    small_n, large_n = len(small.captured_queries), len(large.captured_queries)
    print(f"  {name:32} 50: {small_n:4} q   250: {large_n:4} q")
    assert large_n <= small_n + 2, (
        f"{name} went from {small_n} to {large_n} queries for 5x the "
        f"appraisals — it has a query per row in it.")


def test_the_employees_own_surfaces_cost_the_same_at_any_size(
        cast, auth, cycle, departments, make_appraisal):
    """
    An employee's own record must not get slower because the organisation grew.
    Their dashboard, their goals and their evidence read one appraisal.
    """
    mine = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                          status=Status.SELF_ASSESSMENT)
    seed(cycle, cast["supervisor"], departments, 250)

    surfaces = {
        "employee dashboard": f"{APPRAISALS}dashboard/",
        "own record": f"{APPRAISALS}{mine.id}/",
        "own evidence pack": f"{APPRAISALS}{mine.id}/evidence/",
    }
    for name, url in surfaces.items():
        with CaptureQueriesContext(connection) as captured:
            assert auth(cast["employee"]).get(url).status_code == 200
        n = len(captured.captured_queries)
        print(f"  {name:32} {n:4} q  (250 appraisals in the system)")
        assert n < 40, f"{name} took {n} queries for one person's record"


def test_the_list_returns_a_bounded_page_not_the_whole_table(
        cast, auth, cycle, departments):
    seed(cycle, cast["supervisor"], departments, 250)
    body = auth(cast["hr"]).get(APPRAISALS).data
    rows = body["results"] if isinstance(body, dict) else body
    assert len(rows) < 250, "the list returned the entire table in one response"


def test_the_hr_dashboard_is_bounded_however_many_people_it_covers(
        cast, auth, cycle, departments):
    """
    The two people-shaped lists — promotion readiness and succession — and the
    review-delay table are the blocks that could grow without limit. Every one
    must be capped, or the payload grows with headcount until the page stops
    rendering.
    """
    seed(cycle, cast["supervisor"], departments, 250)
    hr = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data["hr"]

    assert len(hr["review_delays"]) <= 20
    assert len(hr["training_needs"]["top_requests"]) <= 10
    assert len(hr["development_plans"]["top_areas"]) <= 10
    # Promotion and succession are NOT capped, deliberately: a truncated list of
    # people recommended for promotion is worse than a long one, because the
    # names that fall off are invisible. Measured so the growth is known.
    print(f"\n  promotion rows: {len(hr['promotion_readiness'])}  "
          f"succession rows: {len(hr['succession'])}  "
          f"departments: {len(hr['by_department'])}")
