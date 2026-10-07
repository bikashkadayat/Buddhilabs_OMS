"""
Admin user delete — endpoint + guards.

DELETE /api/v1/users/admin/users/<id>/ permanently removes a user, but only:
  * for Admins,
  * when the target is INACTIVE (409 otherwise),
  * not your own account,
  * not the last active Admin,
  * not the sole approver of pending memos.
Cascade must not trip the approved-leave hard-delete guard, and profile media
is cleaned up.
"""
from datetime import date, timedelta

import pytest
from rest_framework.test import APIClient

from users.models import User
from leaves.models import Leave, LeaveDayRecord


def _user(username, role=User.Roles.MAKER, active=True, **extra):
    u = User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role, department="ENG",
        employment_type=User.EmploymentType.PERMANENT, date_of_joining=date(2018, 1, 1),
        **extra,
    )
    if not active:
        u.is_active = False
        u.save(update_fields=["is_active"])
    return u


def _admin(username="admin_x"):
    return _user(username, role=User.Roles.ADMIN, is_staff=True, is_superuser=True)


def _client(user):
    c = APIClient(); c.force_authenticate(user); return c


def _url(u):
    return f"/api/v1/users/admin/users/{u.id}/"


@pytest.mark.django_db
def test_non_admin_cannot_delete():
    _admin()  # keep an admin around
    maker = _user("nd_maker")
    target = _user("nd_target", active=False)
    r = _client(maker).delete(_url(target))
    assert r.status_code in (403, 401)
    assert User.objects.filter(id=target.id).exists()


@pytest.mark.django_db
def test_active_user_cannot_be_deleted_409():
    admin = _admin()
    target = _user("active_t", active=True)
    r = _client(admin).delete(_url(target))
    assert r.status_code == 409
    assert "Deactivate" in r.json()["detail"]
    assert User.objects.filter(id=target.id).exists()


@pytest.mark.django_db
def test_inactive_user_is_deleted():
    admin = _admin()
    target = _user("gone_t", active=False)
    r = _client(admin).delete(_url(target))
    assert r.status_code == 204
    assert not User.objects.filter(id=target.id).exists()


@pytest.mark.django_db
def test_cannot_delete_own_account():
    # The real lock-out protection: an admin cannot delete their own account
    # even after deactivating it. (Any permitted requester is itself a qualifying
    # active admin, so the "last admin" path only reduces to self-delete, which
    # this guard catches first.)
    admin = _admin()
    _admin("other_admin")
    admin.is_active = False
    admin.save(update_fields=["is_active"])
    r = _client(admin).delete(_url(admin))
    assert r.status_code == 409
    assert "own account" in r.json()["detail"]
    assert User.objects.filter(id=admin.id).exists()


@pytest.mark.django_db
def test_delete_cascades_leave_with_approved_records():
    admin = _admin()
    target = _user("hist_t", active=False)
    leave = Leave.objects.create(
        user=target, leave_type="annual", start_date=date(2027, 1, 4),
        end_date=date(2027, 1, 4), reason="x", status=Leave.Status.APPROVED,
    )
    LeaveDayRecord.objects.filter(leave_request=leave).update(status=LeaveDayRecord.Status.APPROVED)
    assert LeaveDayRecord.objects.filter(leave_request=leave, status=LeaveDayRecord.Status.APPROVED).exists()
    r = _client(admin).delete(_url(target))
    assert r.status_code == 204, r.content
    assert not User.objects.filter(id=target.id).exists()
    assert not Leave.objects.filter(id=leave.id).exists()


def _pending_memo(approver):
    """
    A memo awaiting `approver`'s decision. Routing is an approval-matrix row now,
    so "pending on this approver" means an outstanding Approver step rather than a
    value in a current_approver column.
    """
    creator = _user(f"mc_{approver.username}")
    from memos.models import Memo, MemoWorkflowStep
    memo = Memo.objects.create(
        subject="Budget review",
        memo_type=Memo.MemoType.GENERAL,
        created_by=creator, status=Memo.Status.DRAFT_FOR_REVIEW,
    )
    MemoWorkflowStep.objects.create(
        memo=memo, sequence=1, assignee=approver,
        role_type=MemoWorkflowStep.RoleType.APPROVER,
        status=MemoWorkflowStep.StepStatus.ACTIVE,
    )
    return memo


@pytest.mark.django_db
def test_sole_approver_of_pending_memo_blocked():
    # Requester is a superuser whose ROLE is not approver/admin, so it does not
    # count as an available approver -> the inactive approver is genuinely sole.
    requester = _user("su_maker", role=User.Roles.MAKER, is_staff=True, is_superuser=True)
    approver = _user("sole_appr", role=User.Roles.APPROVER, active=False)
    _pending_memo(approver)
    r = _client(requester).delete(_url(approver))
    assert r.status_code == 409, r.content
    assert "sole approver" in r.json()["detail"]
    assert User.objects.filter(id=approver.id).exists()


@pytest.mark.django_db
def test_approver_deletable_when_another_approver_exists():
    admin = _admin()
    _user("backup_appr", role=User.Roles.APPROVER, active=True)  # can take over
    approver = _user("appr2", role=User.Roles.APPROVER, active=False)
    _pending_memo(approver)
    r = _client(admin).delete(_url(approver))
    assert r.status_code == 204, r.content
    assert not User.objects.filter(id=approver.id).exists()
