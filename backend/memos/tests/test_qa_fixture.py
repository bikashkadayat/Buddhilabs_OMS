"""
Phase 49 - memo audit evidence generator.

Builds one fully routed, approved and archived memo through the real API and writes
what the server actually returns: the detail payload, the dashboard counts, the
timeline, the audit trail, and the real PDF export. The Phase 49 audit is then done
against production output rather than against a reading of the templates - which is how
the minute audit found that a "Resolution Register" existed in the payload and nowhere on
screen, and that a chart was being fed a shape the server never sends.

Marked `qa` and excluded from the default run (see pytest.ini) because it writes outside
the backend tree. Regenerate with:

    DATABASE_ENGINE=sqlite3 DJANGO_DEBUG=True DJANGO_SECRET_KEY=test-secret \
      .venv/bin/python -m pytest -q -p no:randomly -m qa memos/tests/test_qa_fixture.py
"""
import json
import pathlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from memos.models import Memo
from memos.services import generate_memo_number

from .conftest import add_sections, act, route

# frontend/qa/, NOT frontend/qa-out/. qa-out is the CAPTURE OUTPUT directory and is
# gitignored, so a fixture written there exists only on the machine that generated it —
# and qa/visual-qa.jsx imports this file at BUILD time. On a fresh checkout (which is
# what CI is) the import resolved to nothing and `npm run qa:build` failed outright.
# Harmless while the harness was a local tool; a hard blocker now that Phase 100.1
# Blocker 9 makes it a required CI gate. The other three generators already write to
# qa/ for this reason.
OUT = (pathlib.Path(__file__).resolve().parents[3] / "frontend" / "qa")

PDF_BYTES = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF\n")


def _get(api, actor, url):
    api.force_authenticate(actor)
    response = api.get(url)
    assert response.status_code == 200, (url, response.status_code, response.data)
    return json.loads(json.dumps(response.data, default=str))


@pytest.mark.qa
@pytest.mark.django_db
def test_dump_the_memo_audit_evidence(api, maker, checker, other_checker, approver,
                                      admin, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)

    memo = Memo.objects.create(
        subject="Approval of NPR 1,745,000 for the server refresh programme",
        to_line="CEO",
        memo_type=Memo.MemoType.CONFIDENTIAL,
        reference_number="TENDER/ICT/2026/11",
        status=Memo.Status.DRAFT,
        created_by=maker,
        memo_number=generate_memo_number(Memo.MemoType.CONFIDENTIAL),
    )
    add_sections(
        memo,
        ("Background",
         '<table class="memo-table memo-table-financial"><thead><tr>'
         "<th>Item</th><th>Qty</th><th>Unit (NPR)</th><th>Amount (NPR)</th>"
         "</tr></thead><tbody>"
         "<tr><td>Rack mounted application servers</td><td>4</td>"
         "<td>312,500</td><td>1,250,000</td></tr>"
         "<tr><td>Core switches with redundant power</td><td>2</td>"
         "<td>155,000</td><td>310,000</td></tr>"
         "</tbody></table>"),
        ("Recommendation", "<p>Recommendation: approve as tabled.</p>"),
    )
    from memos import workflow
    workflow.record_creation(memo, maker)

    api.force_authenticate(maker)
    for name in ("evaluation-committee-scoring.pdf", "vendor-quotations.pdf"):
        uploaded = api.post(f"/api/v1/memos/{memo.id}/attachments/",
                            {"file": SimpleUploadedFile(name, PDF_BYTES)},
                            format="multipart")
        assert uploaded.status_code in (200, 201), uploaded.data

    # A four-role chain, so the matrix, the workflow summary and the PDF's approval
    # blocks are all exercised with every role type the module has.
    route(api, maker, memo,
          (checker, "reviewer"),
          (other_checker, "recommender"),
          (admin, "supporter"),
          (approver, "approver"))
    for user, remarks in [
        (checker, "Reviewed; scoring sheet checked against the bids."),
        (other_checker, "Recommended; within the approved Q3 ceiling."),
        (admin, "Supported."),
        (approver, "Approved within delegated authority."),
    ]:
        response = act(api, user, memo, remarks=remarks)
        assert response.status_code == 200, response.data
    memo.refresh_from_db()

    payload = {
        "_generated_by": "backend/memos/tests/test_qa_fixture.py (Phase 49)",
        "memo_id": str(memo.id),
        "status": memo.status,
        "detail": _get(api, maker, f"/api/v1/memos/{memo.id}/"),
        "timeline": _get(api, maker, f"/api/v1/memos/{memo.id}/timeline/"),
        # As the admin, not the author: unlike the minute module, the memo audit trail
        # is HR/Admin only - the author of the memo is refused their own trail.
        "audit_trail": _get(api, admin, f"/api/v1/memos/{memo.id}/audit-trail/"),
        "dashboard": _get(api, maker, "/api/v1/memos/dashboard/"),
        "charts": _get(api, maker, "/api/v1/memos/dashboard/charts/"),
        "list": _get(api, maker, "/api/v1/memos/?scope=all"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "memo-fixtures.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n")

    api.force_authenticate(approver)
    pdf = api.get(f"/api/v1/memos/{memo.id}/pdf/")
    assert pdf.status_code == 200, pdf.status_code
    content = (b"".join(pdf.streaming_content) if pdf.streaming else pdf.content)
    assert len(content) > 1000
    (OUT / "memo.pdf").write_bytes(content)


@pytest.mark.qa
@pytest.mark.django_db
def test_dump_the_phase495_memo_pdf(api, maker, checker, other_checker, approver,
                                   admin, other_maker, settings, tmp_path):
    """
    Phase 49.5: the same memo again, but exercising the three new registers - a
    completed note round, a reassignment caused by an absence, and an archive grant
    - so the PDF can be reviewed with them present rather than argued about.
    """
    settings.MEDIA_ROOT = str(tmp_path)
    settings.SITE_URL = "https://oms.nif.org.np"

    memo = Memo.objects.create(
        subject="Approval of NPR 1,745,000 for the server refresh programme",
        to_line="CEO",
        memo_type=Memo.MemoType.CONFIDENTIAL,
        reference_number="TENDER/ICT/2026/11", status=Memo.Status.DRAFT,
        created_by=maker,
        memo_number=generate_memo_number(Memo.MemoType.CONFIDENTIAL))
    add_sections(
        memo,
        ("Background",
         "<p>The evaluation committee has completed its scoring of the three "
         "quotations obtained under the standing procurement policy.</p>"),
    )
    from memos import workflow
    workflow.record_creation(memo, maker)

    route(api, maker, memo, (checker, "reviewer"), (other_checker, "recommender"),
          (approver, "approver"))

    # The reviewer goes on leave and is replaced, so the reassignment register has
    # a row with a real reason behind it.
    api.force_authenticate(checker)
    assert api.post(f"/api/v1/memos/{memo.id}/unavailable/",
                    {"reason": "on_leave", "reason_note": "Back on the 20th."},
                    format="json").status_code == 200
    api.force_authenticate(maker)
    assert api.post(f"/api/v1/memos/{memo.id}/replacement/",
                    {"user_id": str(admin.id), "kind": "acting",
                     "reason": "Acting reviewer while on annual leave."},
                    format="json").status_code == 200

    for user, remarks in [
        (admin, "Reviewed as acting reviewer; scoring sheet checked."),
        (other_checker, "Recommended; within the approved Q3 ceiling."),
        (approver, "Approved within delegated authority."),
    ]:
        assert act(api, user, memo, remarks=remarks).status_code == 200
    memo.refresh_from_db()
    assert memo.status == Memo.Status.ARCHIVED

    # A completed note round.
    api.force_authenticate(maker)
    api.post(f"/api/v1/memos/{memo.id}/notes/",
             {"user_ids": [str(other_maker.id)]}, format="json")
    api.force_authenticate(other_maker)
    api.post(f"/api/v1/memos/{memo.id}/note/",
             {"remarks": "Noted; no comment on the costing."}, format="json")

    # And an archive grant, live plus one withdrawn, so both states print.
    api.force_authenticate(maker)
    granted = api.post(f"/api/v1/memos/{memo.id}/archive-access/",
                       {"target": "employee", "user_id": str(other_maker.id),
                        "reason": "Shared with internal audit for the annual file."},
                       format="json")
    assert granted.status_code == 201, granted.data
    second = api.post(f"/api/v1/memos/{memo.id}/archive-access/",
                      {"target": "employee", "user_id": str(checker.id),
                       "reason": "Temporary access during the review."},
                      format="json")
    api.delete(f"/api/v1/memos/{memo.id}/archive-access/"
               f"{second.data['grants'][0]['id']}/",
               {"reason": "Review complete."}, format="json")

    OUT.mkdir(parents=True, exist_ok=True)
    api.force_authenticate(approver)
    pdf = api.get(f"/api/v1/memos/{memo.id}/pdf/")
    assert pdf.status_code == 200
    (OUT / "memo-phase495.pdf").write_bytes(pdf.content)

    # The same document with SITE_URL unset, to confirm the warning replaces the QR
    # rather than a localhost link being printed.
    settings.SITE_URL = ""
    unguarded = api.get(f"/api/v1/memos/{memo.id}/pdf/")
    assert unguarded.status_code == 200
    (OUT / "memo-phase495-no-site-url.pdf").write_bytes(unguarded.content)
