from datetime import date, timedelta

import pytest
from django.core import mail
from rest_framework.test import APIClient

from leaves.models import Leave
from memos.models import Memo
from memos import services as memo_services
from memos import workflow as memo_workflow
from notifications.models import Category, Notification, NotificationPreference
from users.models import User

MONDAY = date.fromisocalendar(2026, 24, 1)


@pytest.fixture
def sync_email(settings):
    settings.NOTIFICATIONS_RUN_SYNC = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    return settings


@pytest.fixture
def maker(db):
    return User.objects.create_user(username="nmaker", email="nmaker@nif.test", password="pass12345", first_name="Mo", role=User.Roles.MAKER, department="ENG")


@pytest.fixture
def checker(db):
    return User.objects.create_user(username="nchecker", email="nchecker@nif.test", password="pass12345", first_name="Cho", role=User.Roles.CHECKER, department="ENG")


@pytest.fixture
def approver(db):
    return User.objects.create_user(username="napprover", email="napprover@nif.test", password="pass12345", role=User.Roles.APPROVER, department="ENG")


def _draft_memo(maker):
    from memos.services import generate_memo_number
    return Memo.objects.create(
        subject="s", memo_type=Memo.MemoType.CONFIDENTIAL,
        status=Memo.Status.DRAFT, created_by=maker, memo_number=generate_memo_number(Memo.MemoType.CONFIDENTIAL),
    )


def _route(memo, author, reviewer, approver):
    """Send `memo` for review through reviewer -> approver on the approval matrix."""
    return memo_workflow.send_for_review(memo, author, rows=[
        {"assignee_id": str(reviewer.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ])


# --- Deliverable 5: memo action -> in-app + email --------------------------
@pytest.mark.django_db
def test_memo_submit_notifies_reviewer_in_app_and_email(sync_email, maker, checker, approver):
    memo = _draft_memo(maker)
    _route(memo, maker, checker, approver)

    # One category per workflow stage now, so a recipient can mute
    # "recommendation required" without also muting final approvals.
    notif = Notification.objects.filter(
        recipient=checker, category=Category.MEMO_REVIEW_REQUIRED).first()
    assert notif is not None
    assert memo.memo_number in notif.title
    assert notif.action_url == f"/memos/{memo.id}"

    # Two recipients, deliberately: the reviewer is asked to act, and the author
    # gets a confirmation naming who the memo is now with (Phase 9 lists "Memo
    # Submitted" as its own event). Asserted per recipient rather than on the
    # outbox length, so adding a recipient later cannot fail this for the wrong
    # reason.
    to_checker = [m for m in mail.outbox if m.to == [checker.email]]
    assert len(to_checker) == 1
    assert to_checker[0].alternatives  # has HTML alternative
    assert Notification.objects.filter(
        recipient=maker, category=Category.MEMO_SUBMITTED).exists()


@pytest.mark.django_db
def test_memo_approve_notifies_author(sync_email, maker, checker, approver):
    memo = _draft_memo(maker)
    memo = _route(memo, maker, checker, approver)
    memo = memo_workflow.act_on_step(memo, checker, "proceed", remarks="Reviewed and correct.")
    mail.outbox.clear()
    # Padded against the live policy, not a literal length. "Approved." was
    # NINE characters against a ten-character floor — one short — and the
    # test broke the day approver remarks became mandatory.
    memo_workflow.act_on_step(
        memo, approver, "proceed",
        remarks="Approved; budget figures verified against the quotation.".ljust(
            memo_services.MIN_COMMENT_LENGTH, "."),
    )
    assert Notification.objects.filter(recipient=maker, category=Category.MEMO_APPROVED).exists()
    assert any(m.to == [maker.email] for m in mail.outbox)


# --- preferences respected -------------------------------------------------
@pytest.mark.django_db
def test_email_preference_disabled_skips_email(sync_email, maker, checker, approver):
    NotificationPreference.objects.create(
        user=checker, category=Category.MEMO_REVIEW_REQUIRED, in_app_enabled=True, email_enabled=False,
    )
    memo = _draft_memo(maker)
    _route(memo, maker, checker, approver)
    # in-app still created for the checker, but no email to them
    assert Notification.objects.filter(recipient=checker).exists()
    assert [m for m in mail.outbox if m.to == [checker.email]] == []


@pytest.mark.django_db
def test_inapp_preference_disabled_skips_record(sync_email, maker, checker, approver):
    NotificationPreference.objects.create(
        user=checker, category=Category.MEMO_REVIEW_REQUIRED, in_app_enabled=False, email_enabled=True,
    )
    memo = _draft_memo(maker)
    _route(memo, maker, checker, approver)
    assert not Notification.objects.filter(recipient=checker).exists()
    # The email still goes out even with the in-app record suppressed.
    assert len([m for m in mail.outbox if m.to == [checker.email]]) == 1


# --- leave notifications ---------------------------------------------------
@pytest.mark.django_db
def test_leave_approval_notifies_user(sync_email, maker, approver):
    leave = Leave.objects.create(user=maker, leave_type="annual", reason="x", start_date=MONDAY, end_date=MONDAY, approver=approver)
    assert Notification.objects.filter(recipient=approver, category=Category.LEAVE_SUBMITTED).exists()

    leave.status = Leave.Status.APPROVED
    leave.save()
    assert Notification.objects.filter(recipient=maker, category=Category.LEAVE_APPROVED).exists()


@pytest.mark.django_db
def test_low_balance_notification_is_idempotent(sync_email, maker, approver):
    # ANNUAL entitled 18; take 16 days approved -> available 2 (< 3) -> alert.
    leave = Leave.objects.create(user=maker, leave_type="annual", reason="x", start_date=MONDAY, end_date=MONDAY + timedelta(days=21), approver=approver)
    leave.status = Leave.Status.APPROVED
    leave.save()
    count1 = Notification.objects.filter(recipient=maker, category=Category.LEAVE_BALANCE_LOW).count()
    assert count1 == 1
    # Re-trigger by re-saving; idempotency key prevents a duplicate.
    leave.save()
    assert Notification.objects.filter(recipient=maker, category=Category.LEAVE_BALANCE_LOW).count() == 1


# --- API -------------------------------------------------------------------
@pytest.mark.django_db
def test_notification_api_list_unread_mark(sync_email, maker, checker, approver):
    memo = _draft_memo(maker)
    _route(memo, maker, checker, approver)

    client = APIClient()
    client.force_authenticate(checker)
    assert client.get("/api/v1/notifications/unread-count/").data["unread"] == 1

    listed = client.get("/api/v1/notifications/")
    nid = listed.data["results"][0]["id"]
    assert client.post(f"/api/v1/notifications/{nid}/read/").data["is_read"] is True
    assert client.get("/api/v1/notifications/unread-count/").data["unread"] == 0

    # cannot see other users' notifications
    client.force_authenticate(approver)
    assert client.get("/api/v1/notifications/").data["count"] == 0


@pytest.mark.django_db
def test_preferences_get_and_update(maker):
    client = APIClient()
    client.force_authenticate(maker)
    prefs = client.get("/api/v1/notifications/preferences/")
    assert prefs.status_code == 200
    assert any(p["category"] == "WEEKLY_DIGEST" for p in prefs.data)

    resp = client.post("/api/v1/notifications/preferences/", {"category": "MEMO_APPROVED", "in_app_enabled": True, "email_enabled": False}, format="json")
    assert resp.status_code == 200
    assert NotificationPreference.objects.filter(user=maker, category="MEMO_APPROVED", email_enabled=False).exists()


@pytest.mark.django_db
def test_weekly_digest_only_for_opted_in(sync_email, maker):
    from django.core.management import call_command
    # No opt-in yet -> no mail.
    call_command("send_weekly_digest")
    assert len(mail.outbox) == 0

    NotificationPreference.objects.create(user=maker, category=Category.WEEKLY_DIGEST, email_enabled=True)
    call_command("send_weekly_digest")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [maker.email]
