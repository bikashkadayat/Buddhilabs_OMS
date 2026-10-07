"""
Security probes for the circular module: RBAC, audience restriction, archive
access, attachment access.

A circular's visibility rule is the OPPOSITE shape to a memo's - a memo is private
and widens to its chain, a circular is meant to be read by an audience that may be
everybody. That inversion is where the mistakes will be, so most of these probes
ask what somebody must NOT be able to do rather than what they can.
"""
import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from circulars.models import Circular
from circulars.permissions import CanViewCircular, visible_circular_filter

from .conftest import (
    act, broadcast, drive_to_broadcast, drive_to_issued, make_circular, submit,
)

Status = Circular.Status

# Real magic bytes - the shared validator sniffs content, so a file whose bytes do
# not match its extension is refused. Writing b"hello" into a .pdf would test the
# rejection path rather than the happy one.
PDF_BYTES = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF\n")


def _visible_to(user):
    filtered = visible_circular_filter(user)
    queryset = Circular.objects.all()
    if filtered is not None:
        queryset = queryset.filter(filtered)
    return set(queryset.distinct().values_list("id", flat=True))


class _Request:
    def __init__(self, user):
        self.user = user
        self.method = "GET"


# ---------------------------------------------------------------------------
# The queryset filter and the object check must agree - at EVERY status
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_list_filter_and_the_object_check_agree_at_every_status(api, cast,
                                                                     draft):
    """
    The invariant both other document modules hold themselves to, adopted here
    because that is where its value was proved: if the two disagree, a circular
    appears in a list and then 403s when opened, or the reverse.
    """
    checker = CanViewCircular()
    watchers = [cast["outsider"], cast["staff_a"], cast["head"], cast["reviewer"]]

    def assert_agreement(label):
        draft.refresh_from_db()
        for user in watchers:
            in_list = draft.id in _visible_to(user)
            on_object = checker.has_object_permission(_Request(user), None, draft)
            assert in_list == on_object, (label, user.username, in_list, on_object)

    assert_agreement("draft")
    submit(api, draft, (cast["reviewer"], "reviewer"), (cast["issuer"], "issuer"))
    assert_agreement("under review")
    act(api, cast["reviewer"], draft, remarks="Checked and in order.")
    act(api, cast["issuer"], draft)
    assert_agreement("issued")
    broadcast(api, draft, cast["issuer"])
    assert_agreement("broadcast")
    api.force_authenticate(cast["hr"])
    api.post(f"/api/v1/circulars/{draft.id}/archive/")
    assert_agreement("archived")


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_draft_is_invisible_to_everyone_but_its_author(api, cast, draft):
    """
    Whatever its classification. A draft is not an announcement yet, so the
    recipient clause has nothing to grant.
    """
    for user in (cast["outsider"], cast["staff_a"], cast["head"], cast["reviewer"]):
        api.force_authenticate(user)
        assert api.get(f"/api/v1/circulars/{draft.id}/").status_code == 404, (
            user.username)
    api.force_authenticate(cast["author"])
    assert api.get(f"/api/v1/circulars/{draft.id}/").status_code == 200


@pytest.mark.django_db
def test_being_in_a_future_audience_grants_nothing_before_broadcast(api, cast,
                                                                     draft):
    """
    The recipient clause is gated on status, not merely on the row existing. A
    circular that has not been broadcast has told nobody anything.
    """
    circular = drive_to_issued(api, draft, (cast["issuer"], "issuer"))
    api.force_authenticate(cast["staff_a"])
    assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 404

    broadcast(api, circular, cast["issuer"])
    api.force_authenticate(cast["staff_a"])
    assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 200


# ---------------------------------------------------------------------------
# Audience restriction
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_somebody_outside_the_audience_cannot_read_a_broadcast_circular(api, cast,
                                                                        draft):
    circular = drive_to_broadcast(api, draft, cast, audience="employees",
                                  employee_ids=[str(cast["staff_a"].id)])
    api.force_authenticate(cast["staff_a"])
    assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 200
    api.force_authenticate(cast["outsider"])
    assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 404


@pytest.mark.django_db
def test_a_restricted_circular_does_not_travel_department_wide(api, cast):
    """
    Classification narrows the DEFAULT department visibility. It does not override
    an explicit distribution - the audience still reads it, which is the point of
    sending it to them.
    """
    circular = make_circular(
        cast, classification=Circular.Classification.CONFIDENTIAL,
        subject="Confidential: disciplinary policy change")
    cast["head"].department = "Finance"
    cast["head"].save(update_fields=["department"])
    drive_to_issued(api, circular, (cast["issuer"], "issuer"))

    api.force_authenticate(cast["head"])
    assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 404, (
        "a department head does not read a confidential circular by default")

    broadcast(api, circular, cast["issuer"], audience="employees",
              employee_ids=[str(cast["head"].id)])
    api.force_authenticate(cast["head"])
    assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 200, (
        "but they do read it when it is explicitly sent to them")


@pytest.mark.django_db
def test_a_department_head_reads_their_departments_unrestricted_traffic(api, cast,
                                                                         draft):
    cast["head"].department = "Finance"
    cast["head"].save(update_fields=["department"])
    drive_to_issued(api, draft, (cast["issuer"], "issuer"))
    api.force_authenticate(cast["head"])
    assert api.get(f"/api/v1/circulars/{draft.id}/").status_code == 200


@pytest.mark.django_db
def test_hr_and_admin_read_everything(api, cast, draft):
    for user in (cast["hr"], cast["admin"]):
        api.force_authenticate(user)
        assert api.get(f"/api/v1/circulars/{draft.id}/").status_code == 200, (
            user.username)


# ---------------------------------------------------------------------------
# The registers are narrower than the circular
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_recipient_cannot_see_who_else_has_read_it(api, cast, draft):
    """
    A recipient is entitled to the announcement. They are not entitled to a list of
    which colleagues have not opened it yet - that is management information about
    other people.
    """
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    assert api.get(f"/api/v1/circulars/{circular.id}/").status_code == 200
    assert api.get(
        f"/api/v1/circulars/{circular.id}/recipients/").status_code == 403
    assert api.get(
        f"/api/v1/circulars/{circular.id}/acknowledgements/").status_code == 403

    detail = api.get(f"/api/v1/circulars/{circular.id}/").data
    assert detail["can_view_registers"] is False
    # The SUMMARY is fine - it names nobody.
    assert detail["read_summary"]["total"] > 0


@pytest.mark.django_db
def test_the_author_the_issuer_and_hr_can_see_the_registers(api, cast, draft):
    circular = drive_to_broadcast(api, draft, cast, (cast["reviewer"], "reviewer"),
                                  (cast["issuer"], "issuer"))
    for user in (cast["author"], cast["issuer"], cast["hr"], cast["admin"],
                 cast["reviewer"]):
        api.force_authenticate(user)
        assert api.get(
            f"/api/v1/circulars/{circular.id}/recipients/").status_code == 200, (
            user.username)


@pytest.mark.django_db
def test_the_pdf_omits_the_registers_for_a_reader_not_entitled_to_them(api, cast,
                                                                        settings):
    """
    The same boundary the recipients endpoint draws, applied to paper - otherwise
    the export would be a way around it.
    """
    settings.SITE_URL = "https://oms.nif.org.np"
    circular = make_circular(cast, acknowledgement_required=True)
    circular = drive_to_broadcast(api, circular, cast)

    api.force_authenticate(cast["staff_a"])
    recipient_copy = api.get(f"/api/v1/circulars/{circular.id}/pdf/")
    assert recipient_copy.status_code == 200
    api.force_authenticate(cast["author"])
    author_copy = api.get(f"/api/v1/circulars/{circular.id}/pdf/")
    assert author_copy.status_code == 200
    # The privileged copy carries the named register and is therefore larger.
    assert len(author_copy.content) > len(recipient_copy.content)


# ---------------------------------------------------------------------------
# Workflow manipulation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_nobody_can_act_out_of_turn_or_on_a_chain_they_are_not_in(api, cast,
                                                                   draft):
    submit(api, draft, (cast["reviewer"], "reviewer"), (cast["issuer"], "issuer"))
    # Not in the chain at all.
    assert act(api, cast["staff_a"], draft).status_code in (403, 404)
    # In the chain, but not their turn.
    early = act(api, cast["issuer"], draft)
    assert early.status_code == 403
    assert "It is not your turn" in str(early.data)


@pytest.mark.django_db
def test_only_the_author_can_submit_or_set_the_chain(api, cast, draft):
    from .conftest import chain_rows

    api.force_authenticate(cast["staff_a"])
    assert api.post(f"/api/v1/circulars/{draft.id}/chain/",
                    {"workflow": chain_rows((cast["issuer"], "issuer"))},
                    format="json").status_code in (403, 404)
    assert api.post(f"/api/v1/circulars/{draft.id}/send-for-review/",
                    {}, format="json").status_code in (403, 404)


@pytest.mark.django_db
def test_a_reviewer_cannot_broadcast(api, cast, draft):
    """
    Reviewing the text is not the same authority as deciding who reads it.
    """
    circular = drive_to_issued(api, draft, (cast["reviewer"], "reviewer"),
                               (cast["issuer"], "issuer"))
    response = broadcast(api, circular, cast["reviewer"])
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_recipient_cannot_broadcast_or_archive(api, cast, draft):
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    assert api.post(f"/api/v1/circulars/{circular.id}/broadcast/",
                    {"audience": "organisation"},
                    format="json").status_code == 403
    assert api.post(f"/api/v1/circulars/{circular.id}/archive/").status_code == 403


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_attachment_url_is_signed_and_never_a_raw_media_path(api, cast, draft,
                                                                 settings,
                                                                 tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    api.force_authenticate(cast["author"])
    uploaded = api.post(f"/api/v1/circulars/{draft.id}/attachments/",
                        {"file": SimpleUploadedFile("policy.pdf", PDF_BYTES)},
                        format="multipart")
    assert uploaded.status_code == 201, uploaded.data
    url = uploaded.data[0]["url"]

    # The real shape, asserted precisely rather than by guessing at a parameter
    # name: /api/v1/media/?p=<path>&e=<expiry>&s=<hmac>&u=<user>&dl=1. The path is
    # a QUERY parameter of a guarded view, never a served location - so the three
    # things that make the link safe are all present and checkable.
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    assert parsed.path == "/api/v1/media/", parsed.path
    params = parse_qs(parsed.query)
    # Phase S3: every new upload lives under its owning tenant's prefix
    # (org/<token>/...). The tenant is in the path AND in the signed `o`
    # parameter, and documents.protected_media refuses a link whose claim and
    # path disagree -- which is what stops a leaked link addressing another
    # tenant's file.
    from tenancy.keys import token_for

    token = token_for(cast["author"].organization_id)
    assert params["p"][0].startswith(f"org/{token}/circulars/attachments/")
    assert params["o"][0] == token, "link not bound to a tenant"
    assert params["s"][0], "no signature"
    assert int(params["e"][0]) > 0, "no expiry"
    assert params["u"][0] == str(cast["author"].id), "not bound to the reader"
    assert params["dl"][0] == "1", "not forced to download"


@pytest.mark.django_db
def test_a_spoofed_attachment_is_refused(api, cast, draft, settings, tmp_path):
    """
    The shared validator sniffs magic bytes, so an HTML file renamed to .pdf is
    rejected. Reused rather than reimplemented - the project learned that lesson
    when a second module inherited none of the first one's upload validation.
    """
    settings.MEDIA_ROOT = str(tmp_path)
    api.force_authenticate(cast["author"])
    refused = api.post(
        f"/api/v1/circulars/{draft.id}/attachments/",
        {"file": SimpleUploadedFile("evil.pdf", b"<html><script>x</script></html>")},
        format="multipart")
    assert refused.status_code == 400


@pytest.mark.django_db
def test_attachments_are_frozen_once_the_circular_is_issued(api, cast, draft,
                                                             settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    api.force_authenticate(cast["author"])
    uploaded = api.post(f"/api/v1/circulars/{draft.id}/attachments/",
                        {"file": SimpleUploadedFile("policy.pdf", PDF_BYTES)},
                        format="multipart")
    attachment_id = uploaded.data[0]["id"]

    circular = drive_to_issued(api, draft, (cast["issuer"], "issuer"))
    for actor in (cast["author"], cast["admin"]):
        api.force_authenticate(actor)
        assert api.post(f"/api/v1/circulars/{circular.id}/attachments/",
                        {"file": SimpleUploadedFile("late.pdf", PDF_BYTES)},
                        format="multipart").status_code == 403
        assert api.delete(
            f"/api/v1/circulars/{circular.id}/attachments/{attachment_id}/"
        ).status_code == 403


@pytest.mark.django_db
def test_an_attachment_of_one_circular_is_not_reachable_through_another(
        api, cast, draft, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    other = make_circular(cast, subject="Another circular")
    api.force_authenticate(cast["author"])
    uploaded = api.post(f"/api/v1/circulars/{draft.id}/attachments/",
                        {"file": SimpleUploadedFile("policy.pdf", PDF_BYTES)},
                        format="multipart")
    attachment_id = uploaded.data[0]["id"]
    assert api.delete(
        f"/api/v1/circulars/{other.id}/attachments/{attachment_id}/"
    ).status_code == 404


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_audit_trail_with_ip_addresses_is_not_open_to_every_reader(api, cast,
                                                                        draft):
    circular = drive_to_broadcast(api, draft, cast)
    api.force_authenticate(cast["staff_a"])
    assert api.get(
        f"/api/v1/circulars/{circular.id}/audit-trail/").status_code == 403
    for user in (cast["author"], cast["hr"], cast["admin"]):
        api.force_authenticate(user)
        assert api.get(
            f"/api/v1/circulars/{circular.id}/audit-trail/").status_code == 200, (
            user.username)


@pytest.mark.django_db
def test_every_governance_action_is_audited(api, cast, draft):
    from circulars.models import CircularAuditLog

    circular = drive_to_broadcast(api, draft, cast, (cast["reviewer"], "reviewer"),
                                  (cast["issuer"], "issuer"))
    actions = set(circular.audit_entries.values_list("action", flat=True))
    Action = CircularAuditLog.Action
    for expected in (Action.CREATED, Action.SENT_FOR_REVIEW, Action.REVIEWED,
                     Action.ISSUED, Action.AUDIENCE_SET, Action.BROADCAST):
        assert expected in actions, expected


@pytest.mark.django_db
def test_the_read_flood_is_kept_off_the_timeline(api, cast, draft):
    """
    A four-hundred-recipient circular writes four hundred `read` rows. On the
    timeline they would bury every workflow event; the read register answers that
    question properly, per person.
    """
    circular = drive_to_broadcast(api, draft, cast)
    for user in (cast["staff_a"], cast["staff_b"], cast["head"]):
        api.force_authenticate(user)
        api.get(f"/api/v1/circulars/{circular.id}/")

    from circulars.models import CircularAuditLog
    assert circular.audit_entries.filter(
        action=CircularAuditLog.Action.READ).count() == 3

    api.force_authenticate(cast["author"])
    timeline = api.get(f"/api/v1/circulars/{circular.id}/").data["timeline"]
    assert not [row for row in timeline if row["action"] == "read"]


@pytest.mark.django_db
def test_an_unauthenticated_caller_gets_nothing(api, draft):
    api.force_authenticate(None)
    assert api.get("/api/v1/circulars/").status_code in (401, 403)
    assert api.get(f"/api/v1/circulars/{draft.id}/").status_code in (401, 403)
