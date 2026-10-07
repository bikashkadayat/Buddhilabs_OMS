"""
Phase 8 — leave-workflow email/notification verification.

Runs with NOTIFICATIONS_RUN_SYNC so sends happen inline and land in mail.outbox,
and the locmem email backend. Verifies: correct recipients resolved dynamically,
emails go to the login email, NotificationLog records each send, dedup, and that
an email failure never breaks apply/approve/reject.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import override_settings
from rest_framework.test import APIClient

from users.models import User
from leaves.models import Leave
from notifications.models import Notification, NotificationLog
from .conftest import _user, MONDAY

SYNC = override_settings(
    NOTIFICATIONS_RUN_SYNC=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    NOTIFY_CC_HR_ON_SUBMIT=True,
)
END = MONDAY + timedelta(days=1)  # Mon–Tue = 2 working days


@pytest.fixture
def actors(db):
    emp = _user("n_emp", User.Roles.MAKER, department="ENG")
    head = _user("n_head", User.Roles.CHECKER, department="ENG")
    hr = _user("n_hr", User.Roles.APPROVER, department="ENG")
    return emp, head, hr


def _apply(emp, approver):
    """Submit a leave, selecting `approver` as the reporting manager.

    The selected manager is the primary approver (leaves.approvals), so pass the
    person the test expects to act — these tests have the Dept Head review.
    """
    c = APIClient(); c.force_authenticate(emp)
    r = c.post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": str(MONDAY), "end_date": str(END),
        "reason": "Family event", "approver": str(approver.id),
    }, format="json")
    assert r.status_code == 201, r.content
    return r.json()["id"]


@SYNC
@pytest.mark.django_db
def test_submit_notifies_dept_head_and_hr_cc(actors):
    emp, head, hr = actors
    mail.outbox = []
    _apply(emp, head)
    recipients = {addr for m in mail.outbox for addr in m.to}
    assert head.email in recipients            # Dept Head (dynamic, by department)
    assert hr.email in recipients              # HR CC
    # In-app records + NotificationLog 'sent' rows exist for the head.
    assert Notification.objects.filter(recipient=head, category="LEAVE_SUBMITTED").exists()
    assert NotificationLog.objects.filter(recipient=head, status="sent").exists()
    assert all("[NIF]" in m.subject for m in mail.outbox)


@SYNC
@pytest.mark.django_db
def test_dept_head_grant_notifies_employee_and_hr_record(actors):
    """Dept Head approval is final: the employee is emailed 'Approved' and HR gets
    a record copy (not an action item)."""
    emp, head, hr = actors
    leave_id = _apply(emp, head)
    mail.outbox = []
    c = APIClient(); c.force_authenticate(head)
    r = c.post(f"/api/v1/leaves/{leave_id}/dept-head-review/", {"decision": "approve"}, format="json")
    assert r.status_code == 200, r.content
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.APPROVED
    recipients = {addr for m in mail.outbox for addr in m.to}
    assert hr.email in recipients              # HR record copy (visibility)
    assert emp.email in recipients             # employee granted
    to_emp = [m for m in mail.outbox if emp.email in m.to]
    assert any("Approved" in m.subject for m in to_emp)


@SYNC
@pytest.mark.django_db
def test_grant_emails_employee_login_email(actors):
    """The grant email goes to the employee's account (login) email on Dept Head
    approval — no HR second stage required."""
    emp, head, hr = actors
    leave_id = _apply(emp, head)
    mail.outbox = []
    ch = APIClient(); ch.force_authenticate(head)
    r = ch.post(f"/api/v1/leaves/{leave_id}/dept-head-review/", {"decision": "approve"}, format="json")
    assert r.status_code == 200, r.content
    to_emp = [m for m in mail.outbox if emp.email in m.to]
    assert to_emp and any("Approved" in m.subject for m in to_emp)
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.APPROVED


@SYNC
@pytest.mark.django_db
def test_l1_rejection_emails_employee(actors):
    emp, head, hr = actors
    leave_id = _apply(emp, head)
    mail.outbox = []
    c = APIClient(); c.force_authenticate(head)
    r = c.post(f"/api/v1/leaves/{leave_id}/dept-head-review/",
               {"decision": "reject", "remarks": "Insufficient handover"}, format="json")
    assert r.status_code == 200, r.content
    to_emp = [m for m in mail.outbox if emp.email in m.to]
    assert to_emp and any("Rejected" in m.subject for m in to_emp)


@SYNC
@pytest.mark.django_db
def test_email_failure_never_breaks_workflow(actors):
    """A raising SMTP send must not fail the API; it is logged as 'failed'."""
    emp, head, hr = actors
    with patch("notifications.emails.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp down")):
        leave_id = _apply(emp, head)  # still 201 despite email blowing up
    assert Leave.objects.filter(pk=leave_id).exists()
    assert NotificationLog.objects.filter(status="failed").exists()


@SYNC
@pytest.mark.django_db
def test_notifications_are_deduplicated(actors):
    emp, head, hr = actors
    leave_id = _apply(emp, head)
    leave = Leave.objects.get(pk=leave_id)
    mail.outbox = []
    # The status transition fires leave_finalized once (via the post_save signal);
    # re-invoking it must NOT send a second email (idempotency-keyed dedup).
    from leaves import notifications as ln
    leave.status = Leave.Status.APPROVED
    leave.save()                     # signal -> exactly one employee email
    ln.leave_finalized(leave)        # same idempotency key -> no duplicate
    to_emp = [m for m in mail.outbox if emp.email in m.to]
    assert len(to_emp) == 1
