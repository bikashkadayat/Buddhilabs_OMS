"""
The browser QA fixture generator.

The visual QA harness renders the REAL pages, so it has to be fed the REAL payloads.
Hand-written fixtures drift: a serializer gains a field, the harness keeps rendering
the old shape, and the screenshot certifies a page that no longer exists. This module
builds a fully populated minute through the actual API and writes what the server
returns to `frontend/qa/fixtures.json`, so the harness screenshots production shapes by
construction.

It is marked `qa` and excluded from the default run (see pytest.ini) because it writes
outside the backend tree. Regenerate with:

    ./run-tests.sh -m qa minutes/tests/test_qa_fixture.py
"""
import datetime
import json
import pathlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from minutes.models import Minute, MinuteParticipant

from .conftest import (  # noqa: F401  (fixtures)
    acknowledge, api, cast, make_minute, open_round, send_for_review, set_members,
    taxonomy,
)

FIXTURE_PATH = (pathlib.Path(__file__).resolve().parents[3]
                / "frontend" / "qa" / "fixtures.json")

# A one-page PDF and a 1x1 PNG, inline so the fixture needs no binary assets on disk.
PDF_BYTES = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
             b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 99 99]>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF\n")
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082")


def _get(api, actor, url):
    api.force_authenticate(actor)
    response = api.get(url)
    assert response.status_code == 200, (url, response.status_code, response.data)
    return json.loads(json.dumps(response.data, default=str))


@pytest.mark.qa
@pytest.mark.django_db
def test_dump_the_browser_qa_fixtures(api, cast, taxonomy, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    today = datetime.date(2026, 8, 5)

    # ---- the archived minute: the one every panel is screenshotted from ----------
    minute = make_minute(
        cast, taxonomy,
        subject="Third quarter capital expenditure and the ICT refresh programme",
        reference_number="DEPT/2026/07",
        meeting_date=today,
        meeting_time=datetime.time(10, 30),
        fro=cast["fro"], fro_name=cast["fro"].get_full_name(),
        agenda_body=(
            "<p>The Board considered the deferred server refresh and the three "
            "quotations obtained under the standing procurement policy.</p>"
            "<table><thead><tr><th>S.N.</th><th>Agendas</th>"
            "<th>Responsibility (Primary)</th><th>Responsibility (Secondary)</th>"
            "<th>Deadline</th></tr></thead><tbody>"
            "<tr><td>1</td><td>Approve the ICT capital expenditure of NPR "
            "1,745,000</td><td>Min Member A T</td><td>Min Member B T</td>"
            "<td>2026-09-15</td></tr>"
            "<tr><td>2</td><td>Defer the vehicle replacement programme to the "
            "fourth quarter</td><td>Min Member B T</td><td>&mdash;</td>"
            "<td>2026-10-01</td></tr>"
            "<tr><td>3</td><td>Close the two outstanding audit recommendations"
            "</td><td>Min Fro T</td><td>Min Member A T</td><td>2026-08-24</td></tr>"
            "</tbody></table>"),
    )

    api.force_authenticate(cast["initiator"])
    for name, blob, content_type in [
        ("evaluation-committee-scoring.pdf", PDF_BYTES, "application/pdf"),
        ("server-room-layout.png", PNG_BYTES, "image/png"),
    ]:
        response = api.post(
            f"/api/v1/minutes/{minute.id}/attachments/",
            {"file": SimpleUploadedFile(name, blob, content_type=content_type)},
            format="multipart")
        assert response.status_code == 201, response.data

    # Five members: three present, one absent, one invitee.
    open_round(api, minute,
               (cast["member_a"], MinuteParticipant.Attendance.PRESENT),
               (cast["member_b"], MinuteParticipant.Attendance.PRESENT),
               (cast["fro"], MinuteParticipant.Attendance.PRESENT),
               (cast["absentee"], MinuteParticipant.Attendance.ABSENT),
               (cast["invitee"], MinuteParticipant.Attendance.INVITEE))
    for member in (cast["member_a"], cast["member_b"], cast["fro"]):
        assert acknowledge(api, member, minute,
                           remarks="Read and noted.").status_code == 200
    minute.refresh_from_db()

    # ---- a draft, for the create/edit screens ------------------------------------
    draft = make_minute(
        cast, taxonomy,
        subject="Audit Committee, third quarter sitting",
        reference_number="AUD/2026/03",
        minute_type=taxonomy["mancom"],
        meeting_date=today + datetime.timedelta(days=3),
        meeting_time=datetime.time(14, 0),
        fro=cast["fro"], fro_name=cast["fro"].get_full_name(),
        agenda_body="<p>The Committee will review progress against the plan.</p>")
    set_members(api, draft,
                (cast["member_a"], MinuteParticipant.Attendance.PRESENT),
                (cast["member_b"], MinuteParticipant.Attendance.PRESENT))

    # ---- one minute with the FRO, for the review screens -------------------------
    in_review = make_minute(
        cast, taxonomy, subject="Branch operations review",
        minute_type=taxonomy["branch"],
        meeting_date=today + datetime.timedelta(days=1),
        meeting_time=datetime.time(9, 0),
        fro=cast["fro"], fro_name=cast["fro"].get_full_name())
    set_members(api, in_review,
                (cast["member_a"], MinuteParticipant.Attendance.PRESENT))
    assert send_for_review(api, in_review).status_code == 200

    # ---- what the harness needs --------------------------------------------------
    initiator = cast["initiator"]
    payload = {
        "_generated_by": "backend/minutes/tests/test_qa_fixture.py",
        "user": {"id": str(initiator.id), "full_name": initiator.get_full_name(),
                 "role": initiator.role, "designation": initiator.designation},
        "archived_id": str(minute.id),
        "draft_id": str(draft.id),
        "review_id": str(in_review.id),
        "archived": _get(api, initiator, f"/api/v1/minutes/{minute.id}/"),
        "draft": _get(api, initiator, f"/api/v1/minutes/{draft.id}/"),
        "in_review": _get(api, cast["fro"], f"/api/v1/minutes/{in_review.id}/"),
        "taxonomy": _get(api, initiator, "/api/v1/minutes/taxonomy/"),
        "audit_trail": _get(api, initiator,
                            f"/api/v1/minutes/{minute.id}/audit-trail/"),
        "list": _get(api, initiator, "/api/v1/minutes/?scope=all"),
        "archived_list": _get(api, initiator, "/api/v1/minutes/?scope=archived"),
        "dashboard": _get(api, initiator, "/api/v1/minutes/dashboard/"),
        "charts": _get(api, initiator, "/api/v1/minutes/dashboard/charts/"),
    }

    # Guard rails: an empty panel screenshots as a clean page and proves nothing.
    detail = payload["archived"]
    assert detail["status"] == Minute.Status.ARCHIVED
    assert len(detail["participants"]) == 5
    assert len(detail["attachments"]) == 2
    assert detail["acknowledgement"]["total"] == 3
    assert detail["acknowledgement"]["is_complete"] is True
    assert any(block["stamp"] == "ABSENT" for block in detail["signatures"])
    assert any(block["stamp"] == "ACKNOWLEDGED" for block in detail["signatures"])
    assert "<table" in detail["agenda_body"]
    assert payload["in_review"]["status"] == Minute.Status.DRAFT_FOR_REVIEW

    FIXTURE_PATH.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")

    # The exports, from the SAME minute the screenshots are taken from. Reviewing a
    # PDF built from different data than the screen would compare two documents rather
    # than one document in two renderings.
    out = FIXTURE_PATH.parent.parent / "qa-out"
    out.mkdir(parents=True, exist_ok=True)
    api.force_authenticate(initiator)
    for suffix, url in [
        ("minute.pdf", f"/api/v1/minutes/{minute.id}/pdf/"),
        ("acknowledgement-sheet.pdf",
         f"/api/v1/minutes/{minute.id}/acknowledgement-sheet/"),
        ("minute.xlsx", f"/api/v1/minutes/{minute.id}/excel/"),
    ]:
        response = api.get(url)
        assert response.status_code == 200, (url, response.status_code)
        content = (b"".join(response.streaming_content)
                   if response.streaming else response.content)
        assert len(content) > 1000, (url, len(content))
        (out / suffix).write_bytes(content)
