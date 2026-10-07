"""Shared fixtures for the appraisal suite."""
import datetime

import pytest
from django.apps import apps
from django.utils import timezone
from rest_framework.test import APIClient

from appraisal.models import Appraisal, AppraisalCycle, Competency
from appraisal.services import user_snapshot
from users.models import User

APPRAISALS = "/api/v1/appraisals/"
CYCLES = "/api/v1/appraisal-cycles/"


@pytest.fixture
def api():
    return APIClient()


def make_user(username, role=User.Roles.MAKER, designation="Officer",
              department="Engineering", department_ref=None):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.replace("_", " ").title(), last_name="T",
        role=role, designation=designation, department=department,
        department_ref=department_ref)


@pytest.fixture
def departments(db):
    Department = apps.get_model("leaves", "Department")
    return {
        "engineering": Department.objects.create(name="Engineering", code="ENG"),
        "finance": Department.objects.create(name="Finance", code="FIN"),
    }


@pytest.fixture
def cast(db, departments):
    eng, fin = departments["engineering"], departments["finance"]
    supervisor = make_user("apr_supervisor", User.Roles.CHECKER,
                           "Department Head", department_ref=eng)
    eng.head = supervisor
    eng.save(update_fields=["head"])
    return {
        "employee": make_user("apr_employee", department_ref=eng),
        "peer": make_user("apr_peer", department_ref=eng),
        "supervisor": supervisor,
        "committee": make_user("apr_committee", User.Roles.CHECKER,
                               "Committee Member", department_ref=eng),
        "outsider": make_user("apr_outsider", department="Finance",
                              department_ref=fin),
        # HR is the APPROVER role — see appraisal.permissions.is_hr.
        "hr": make_user("apr_hr", User.Roles.APPROVER, "HR Manager",
                        department_ref=eng),
        "admin": make_user("apr_admin", User.Roles.ADMIN, "Administrator"),
    }


@pytest.fixture
def auth(api):
    def _auth(user):
        api.force_authenticate(user=user)
        return api
    return _auth


@pytest.fixture
def cycle(db):
    today = timezone.localdate()
    return AppraisalCycle.objects.create(
        name="FY 2083/84 Annual",
        status=AppraisalCycle.Status.ACTIVE,
        period_start=today - datetime.timedelta(days=180),
        period_end=today + datetime.timedelta(days=180),
    )


@pytest.fixture
def competencies(db):
    """The ten seeded by migration 0002 — looked up, never re-created, so a
    test that passed would still prove the seed works."""
    return {c.code: c for c in Competency.objects.all()}


def build_appraisal(cycle, employee, supervisor, committee=(), *,
                    status=Appraisal.Status.GOAL_SETTING, goals=None,
                    actor=None):
    """
    An appraisal at any stage, driven through the REAL engine.

    Stages are reached by RUNNING transitions, never by assigning to `status` —
    a fixture that set the field directly would happily build states the engine
    cannot produce, and the tests would then pass against a system that does not
    exist.
    """
    from appraisal import workflow
    from appraisal.models import CompetencyRating, DevelopmentPlan, Goal

    actor = actor or supervisor
    appraisal = Appraisal.objects.create(
        cycle=cycle, employee=employee,
        employee_name=user_snapshot(employee)["name"],
        designation=user_snapshot(employee)["designation"],
        department=getattr(employee, "department_ref", None),
        department_name=user_snapshot(employee)["department"],
        supervisor=supervisor,
        supervisor_name=user_snapshot(supervisor)["name"])
    if committee:
        appraisal.committee.set(committee)
    workflow.record_creation(appraisal, actor)

    for position, (objective, weight) in enumerate(
            goals or [("Deliver the quarterly return", 60),
                      ("Improve the archive process", 40)]):
        Goal.objects.create(appraisal=appraisal, objective=objective,
                            weight=weight, owner=employee,
                            owner_name=user_snapshot(employee)["name"],
                            position=position)

    if status == Appraisal.Status.GOAL_SETTING:
        return appraisal

    workflow.submit_goals(appraisal, employee)
    if status == Appraisal.Status.GOAL_APPROVAL:
        return appraisal

    workflow.agree_goals(appraisal, supervisor)
    if status == Appraisal.Status.MID_YEAR:
        return appraisal

    workflow.record_mid_year(appraisal, supervisor)
    if status == Appraisal.Status.SELF_ASSESSMENT:
        return appraisal

    appraisal.self_assessment = "I delivered both objectives, with detail here."
    appraisal.save(update_fields=["self_assessment"])
    workflow.submit_self_assessment(appraisal, employee)
    if status == Appraisal.Status.SUPERVISOR_REVIEW:
        return appraisal

    appraisal.supervisor_comments = "A solid year; specifics recorded here."
    appraisal.save(update_fields=["supervisor_comments"])
    workflow.record_supervisor_review(appraisal, supervisor)
    if status == Appraisal.Status.COMMITTEE:
        return appraisal

    appraisal.committee_comments = "The committee agrees with the supervisor."
    appraisal.save(update_fields=["committee_comments"])
    workflow.record_committee_review(appraisal, supervisor)
    if status == Appraisal.Status.FINAL_REVIEW:
        return appraisal

    appraisal.final_summary = "Objectives met; development areas identified."
    appraisal.save(update_fields=["final_summary"])
    workflow.record_final_review(appraisal, supervisor)
    if status == Appraisal.Status.DEVELOPMENT_PLAN:
        return appraisal

    DevelopmentPlan.objects.create(
        appraisal=appraisal, area="Presentation skills",
        action="Lead two team briefings next quarter.",
        accountable=employee,
        accountable_name=user_snapshot(employee)["name"])
    workflow.agree_development_plan(appraisal, supervisor)
    if status == Appraisal.Status.TRAINING_PLAN:
        return appraisal

    workflow.agree_training_plan(appraisal, supervisor)
    return appraisal


@pytest.fixture
def make_appraisal(db):
    return build_appraisal
