"""
Phase 50 - circular QA evidence generator.

Builds one fully worked circular through the real API - reviewed, issued, broadcast
to the organisation, partly read, partly acknowledged - and writes what the server
actually returns, plus the real PDF. The visual QA harness renders the REAL pages
against these payloads, so what is screenshotted is production shape by
construction.

That matters because of what the same harness caught in an earlier phase: it had
been fed an invented chart payload the server never sends, so two charts
screenshotted empty and the capture certified a page nobody had really seen.

Marked `qa` and excluded from the default run (see pytest.ini) because it writes
outside the backend tree. Regenerate with:

    DATABASE_ENGINE=sqlite3 DJANGO_DEBUG=True DJANGO_SECRET_KEY=test-secret \
      .venv/bin/python -m pytest -q -p no:randomly -m qa circulars/tests/test_qa_fixture.py
"""
import json
import pathlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from circulars.models import Circular
from circulars.services import MIN_REMARK_LENGTH

from .conftest import act, broadcast, make_circular, submit

OUT = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "qa-out"
FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "qa"

PDF_BYTES = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF\n")


def _get(api, actor, url):
    api.force_authenticate(actor)
    response = api.get(url)
    assert response.status_code == 200, (url, response.status_code, response.data)
    return json.loads(json.dumps(response.data, default=str))


@pytest.mark.qa
@pytest.mark.django_db
def test_dump_the_circular_qa_evidence(api, cast, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.SITE_URL = "https://oms.nif.org.np"

    circular = make_circular(
        cast,
        subject="Revised office hours and attendance policy, effective 1 Ashadh",
        category=Circular.Category.POLICY,
        classification=Circular.Classification.INTERNAL,
        priority=Circular.Priority.HIGH,
        external_reference="BOD/2026/07",
        acknowledgement_required=True,
        acknowledgement_due_days=7,
        content=(
            "<p>Following the Board's decision of 12 Ashadh, the Foundation's "
            "office hours are revised with effect from the first of next month.</p>"
            '<table class="memo-table memo-table-bordered"><thead><tr>'
            "<th>Day</th><th>Opening</th><th>Closing</th></tr></thead><tbody>"
            "<tr><td>Sunday to Thursday</td><td>09:00</td><td>17:00</td></tr>"
            "<tr><td>Friday</td><td>09:00</td><td>14:00</td></tr>"
            "</tbody></table>"
            "<p>Department heads are asked to confirm that their teams have been "
            "briefed. All staff must acknowledge this circular within seven days.</p>"),
    )

    api.force_authenticate(cast["author"])
    for name in ("revised-attendance-policy.pdf", "office-hours-annexure.pdf"):
        uploaded = api.post(f"/api/v1/circulars/{circular.id}/attachments/",
                            {"file": SimpleUploadedFile(name, PDF_BYTES)},
                            format="multipart")
        assert uploaded.status_code == 201, uploaded.data

    # Reviewed, then issued - so the chain, the tracker and the PDF's review table
    # are all exercised with both role types.
    submit(api, circular, (cast["reviewer"], "reviewer"), (cast["issuer"], "issuer"))
    assert act(api, cast["reviewer"], circular,
               remarks="Checked against the staff handbook and the leave policy."
               ).status_code == 200
    assert act(api, cast["issuer"], circular,
               remarks="Issued for organisation-wide broadcast.".ljust(
                   MIN_REMARK_LENGTH, ".")).status_code == 200
    circular.refresh_from_db()

    sent = broadcast(api, circular, cast["issuer"], audience="organisation",
                     remarks="All staff, ahead of the effective date.")
    assert sent.status_code == 200, sent.data
    circular.refresh_from_db()

    # A realistic mix: some have opened it, some have acknowledged, one declined,
    # and the rest are outstanding - so every state in the register renders.
    for reader in (cast["staff_a"], cast["staff_b"], cast["head"], cast["hr"]):
        api.force_authenticate(reader)
        api.get(f"/api/v1/circulars/{circular.id}/")

    api.force_authenticate(cast["staff_a"])
    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
             {"accept": True, "remarks": "Noted; the team has been briefed."},
             format="json")
    api.force_authenticate(cast["staff_b"])
    api.post(f"/api/v1/circulars/{circular.id}/acknowledge/",
             {"accept": False,
              "remarks": "The Friday closing clashes with the branch rota."},
             format="json")

    payload = {
        "_generated_by": "backend/circulars/tests/test_qa_fixture.py (Phase 50)",
        "circular_id": str(circular.id),
        "user": {"id": str(cast["author"].id),
                 "full_name": cast["author"].get_full_name(),
                 "role": cast["author"].role,
                 "designation": cast["author"].designation},
        "detail": _get(api, cast["author"], f"/api/v1/circulars/{circular.id}/"),
        "recipients": _get(api, cast["author"],
                           f"/api/v1/circulars/{circular.id}/recipients/"),
        "acknowledgements": _get(api, cast["author"],
                                 f"/api/v1/circulars/{circular.id}/acknowledgements/"),
        "dashboard": _get(api, cast["author"], "/api/v1/circulars/dashboard/"),
        "taxonomy": _get(api, cast["author"], "/api/v1/circulars/taxonomy/"),
        "audiences": _get(api, cast["author"], "/api/v1/circulars/audiences/"),
        "list": _get(api, cast["author"], "/api/v1/circulars/?scope=all"),
        "broadcasted_list": _get(api, cast["author"],
                                 "/api/v1/circulars/?scope=broadcasted"),
    }

    # Guard rails: an empty register screenshots as a clean page and proves nothing.
    detail = payload["detail"]
    assert detail["status"] == Circular.Status.BROADCASTED
    assert detail["read_summary"]["total"] > 0
    assert detail["read_summary"]["read"] >= 4
    assert detail["acknowledgement"]["acknowledged"] == 1
    assert detail["acknowledgement"]["declined"] == 1
    assert detail["acknowledgement"]["pending"] > 0
    assert len(detail["attachments"]) == 2
    assert len(detail["workflow_steps"]) == 2
    assert len(detail["broadcasts"]) == 1

    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "circular-fixtures.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n")

    OUT.mkdir(parents=True, exist_ok=True)
    api.force_authenticate(cast["author"])
    pdf = api.get(f"/api/v1/circulars/{circular.id}/pdf/")
    assert pdf.status_code == 200
    assert len(pdf.content) > 1000
    (OUT / "circular.pdf").write_bytes(pdf.content)

    # The same document as a RECIPIENT sees it - the registers must be omitted, so
    # the export cannot be used to walk around the register permission.
    api.force_authenticate(cast["staff_a"])
    recipient_copy = api.get(f"/api/v1/circulars/{circular.id}/pdf/")
    assert recipient_copy.status_code == 200
    (OUT / "circular-recipient-copy.pdf").write_bytes(recipient_copy.content)
    assert len(recipient_copy.content) < len(pdf.content)
