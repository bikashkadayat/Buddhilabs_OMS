"""
Shared fixtures for the minute suite.

Every test that needs a minute in flight goes through these, so there is exactly one
definition of "a minute submitted for acknowledgement". The memo suite learned this the
hard way: each test built its own routing by hand, and when the engine changed they all
broke at once.
"""
import datetime

import pytest
from rest_framework.test import APIClient

from users.models import User
from minutes.models import Minute, MinuteParticipant, MinuteType
from minutes import workflow
from minutes.services import generate_minute_number

LIST = "/api/v1/minutes/"


@pytest.fixture
def api():
    return APIClient()


def make_user(username, role=User.Roles.MAKER, designation="Officer",
              department="Finance"):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.replace("_", " ").title(), last_name="T",
        role=role, designation=designation, department=department)


@pytest.fixture
def cast(db):
    """The people a minute needs: an author, an FRO, members, and outsiders."""
    return {
        "initiator": make_user("min_initiator"),
        "fro": make_user("min_fro", User.Roles.CHECKER, "Department Head"),
        "member_a": make_user("min_member_a", designation="Board Member"),
        "member_b": make_user("min_member_b", designation="Board Member"),
        "absentee": make_user("min_absentee", designation="Board Member"),
        "invitee": make_user("min_invitee", designation="Consultant"),
        "stranger": make_user("min_stranger"),
        # HR is the APPROVER role - see minutes/permissions._is_hr.
        "hr": make_user("min_hr", User.Roles.APPROVER, "HR Manager"),
        "admin": make_user("min_admin", User.Roles.ADMIN, "Administrator"),
    }


@pytest.fixture
def taxonomy(db):
    """
    The seeded types. Migration 0009 puts the manual's four there, so this only looks
    one up - a test that created its own row would stop proving the seed works.
    """
    return {
        "department": MinuteType.objects.get(code="department"),
        "branch": MinuteType.objects.get(code="branch"),
        "mancom": MinuteType.objects.get(code="mancom"),
    }


def make_minute(cast, taxonomy, **kwargs):
    fields = {
        "subject": "Third quarter review",
        "minute_type": taxonomy["department"],
        "meeting_date": datetime.date(2026, 8, 5),
        "meeting_time": datetime.time(10, 30),
        "created_by": cast["initiator"],
        "agenda_body": "<p>The committee considered the capital plan.</p>",
        "department_name": "Finance",
        "status": Minute.Status.DRAFT,
    }
    fields.update(kwargs)
    fields.setdefault("minute_number", generate_minute_number())
    minute = Minute.objects.create(**fields)
    workflow.record_creation(minute, minute.created_by)
    return minute


@pytest.fixture
def minute(cast, taxonomy):
    return make_minute(cast, taxonomy)


def set_members(api, minute, *pairs, actor=None):
    """[(user, attendance), ...] -> set the member list through the API."""
    actor = actor or minute.created_by
    api.force_authenticate(actor)
    response = api.post(
        f"{LIST}{minute.id}/participants/",
        {"participants": [{"user_id": str(u.id), "attendance": attendance}
                          for u, attendance in pairs]}, format="json")
    assert response.status_code == 200, response.data
    minute.refresh_from_db()
    return minute


def send_for_review(api, minute, actor=None):
    actor = actor or minute.created_by
    api.force_authenticate(actor)
    return api.post(f"{LIST}{minute.id}/send-for-review/", {}, format="json")


def send_for_acknowledgement(api, minute, actor=None):
    actor = actor or minute.created_by
    api.force_authenticate(actor)
    response = api.post(f"{LIST}{minute.id}/send-for-acknowledgement/", {},
                        format="json")
    minute.refresh_from_db()
    return response


def acknowledge(api, actor, minute, remarks=""):
    api.force_authenticate(actor)
    response = api.post(f"{LIST}{minute.id}/acknowledge/",
                        {"remarks": remarks}, format="json")
    minute.refresh_from_db()
    return response


def open_round(api, minute, *pairs):
    """Set the members and submit for acknowledgement in one step."""
    set_members(api, minute, *pairs)
    response = send_for_acknowledgement(api, minute)
    assert response.status_code == 200, response.data
    return minute


__all__ = [
    "LIST", "acknowledge", "api", "cast", "make_minute", "make_user", "minute",
    "open_round", "send_for_acknowledgement", "send_for_review", "set_members",
    "taxonomy", "Minute", "MinuteParticipant",
]
