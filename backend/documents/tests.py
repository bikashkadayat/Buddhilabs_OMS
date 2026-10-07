from datetime import date, timedelta

import pytest
from rest_framework.test import APIClient

from documents.models import IssuedDocument
from leaves.models import Leave
from memos.models import Memo
from memos import services as memo_services
from memos import workflow as memo_workflow
from users.models import User

MONDAY = date.fromisocalendar(2026, 24, 1)


@pytest.fixture
def maker(db):
    return User.objects.create_user(username="dmaker", email="dmaker@nif.test", password="pass12345", first_name="Da", role=User.Roles.MAKER, department="ENG")


@pytest.fixture
def checker(db):
    return User.objects.create_user(username="dchecker", email="dchecker@nif.test", password="pass12345", role=User.Roles.CHECKER, department="ENG")


@pytest.fixture
def approver(db):
    return User.objects.create_user(username="dapprover", email="dapprover@nif.test", password="pass12345", role=User.Roles.APPROVER, department="ENG")


@pytest.fixture
def admin(db):
    return User.objects.create_user(username="dadmin", email="dadmin@nif.test", password="pass12345", role=User.Roles.ADMIN, department="ENG")


def _approved_memo(maker, checker, approver):
    from memos.services import generate_memo_number
    memo = Memo.objects.create(subject="Q3 budget",
                               status=Memo.Status.DRAFT, created_by=maker, memo_number=generate_memo_number(Memo.MemoType.CONFIDENTIAL),
                               memo_type=Memo.MemoType.CONFIDENTIAL)
    # Drive it through the approval matrix; approval auto-archives, which is what
    # the PDF endpoint accepts alongside `approved`.
    memo_workflow.record_creation(memo, maker)
    memo = memo_workflow.send_for_review(memo, maker, rows=[
        {"assignee_id": str(checker.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ])
    memo = memo_workflow.act_on_step(memo, checker, "proceed", remarks="Reviewed and correct.")
    memo = memo_workflow.act_on_step(memo, approver, "proceed",
                                     remarks="Approved; budget figures verified.")
    return memo


def _approved_leave(maker, approver):
    leave = Leave.objects.create(user=maker, leave_type="annual", reason="trip", start_date=MONDAY, end_date=MONDAY + timedelta(days=2), approver=approver)
    leave.status = Leave.Status.APPROVED
    leave.save()
    return leave


# --- memo PDF --------------------------------------------------------------
@pytest.mark.django_db
def test_memo_pdf_only_when_approved(maker, checker, approver):
    from memos.services import generate_memo_number
    draft = Memo.objects.create(subject="s", status=Memo.Status.DRAFT,
                                created_by=maker, memo_number=generate_memo_number(Memo.MemoType.CONFIDENTIAL), memo_type=Memo.MemoType.CONFIDENTIAL)
    client = APIClient()
    client.force_authenticate(maker)
    assert client.get(f"/api/v1/memos/{draft.id}/pdf/").status_code == 409

    memo = _approved_memo(maker, checker, approver)
    resp = client.get(f"/api/v1/memos/{memo.id}/pdf/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    body = b"".join(resp.streaming_content) if resp.streaming else resp.content
    assert body[:4] == b"%PDF"
    # an IssuedDocument was registered for verification
    assert IssuedDocument.objects.filter(document_number=memo.memo_number, doc_type="memo").exists()


# --- leave PDF + certificate ----------------------------------------------
@pytest.mark.django_db
def test_leave_pdf_and_certificate(maker, approver):
    leave = _approved_leave(maker, approver)
    client = APIClient()
    client.force_authenticate(maker)

    pdf = client.get(f"/api/v1/leaves/{leave.id}/pdf/")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    cert = client.get(f"/api/v1/leaves/{leave.id}/certificate/")
    assert cert.status_code == 200 and cert.content[:4] == b"%PDF"

    assert IssuedDocument.objects.filter(target_id=leave.id, doc_type="leave_application").exists()
    assert IssuedDocument.objects.filter(target_id=leave.id, doc_type="leave_certificate").exists()


@pytest.mark.django_db
def test_leave_pdf_rejected_when_not_approved(maker):
    leave = Leave.objects.create(user=maker, leave_type="annual", reason="x", start_date=MONDAY, end_date=MONDAY)
    client = APIClient()
    client.force_authenticate(maker)
    assert client.get(f"/api/v1/leaves/{leave.id}/pdf/").status_code == 409


# --- verification (public) -------------------------------------------------
@pytest.mark.django_db
def test_public_verification(maker, approver):
    leave = _approved_leave(maker, approver)
    client = APIClient()
    client.force_authenticate(maker)
    client.get(f"/api/v1/leaves/{leave.id}/certificate/")  # issues a CERT doc
    doc = IssuedDocument.objects.get(target_id=leave.id, doc_type="leave_certificate")

    anon = APIClient()  # no auth -> still works (public)
    resp = anon.get(f"/api/v1/verify/{doc.document_number}/?format=json")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True
    assert resp.json()["doc_type"] == "Leave Certificate"

    missing = anon.get("/api/v1/verify/NIFN-CERT-2099-9999/?format=json")
    assert missing.status_code == 404
    assert missing.json()["valid"] is False


@pytest.mark.django_db
def test_verification_html_page(maker, approver):
    leave = _approved_leave(maker, approver)
    APIClient().force_authenticate(maker)
    client = APIClient()
    client.force_authenticate(maker)
    client.get(f"/api/v1/leaves/{leave.id}/pdf/")
    doc = IssuedDocument.objects.get(target_id=leave.id, doc_type="leave_application")
    html = APIClient().get(f"/api/v1/verify/{doc.document_number}/")
    assert html.status_code == 200
    assert b"Document Verification" in html.content
