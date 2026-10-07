"""
Phase 26 - banking-style approval stamps, certification blocks and the seal.

What is worth testing here is not the CSS. It is the three things the redesign
depends on being right in the DATA, because each of them prints an assertion onto
a document that people rely on:

  * the stamp word is the signatory's OWN certification (a recommender stamps
    RECOMMENDED), never the generic "Approved" that status_label carries;
  * the certificate - and therefore the large seal - exists only for a memo that
    really is approved, so an APPROVED seal cannot appear over one in flight;
  * a verification ID is stable, so a code read off a printed copy still matches
    the one on screen later.

Plus the rendered PDF: assertions on the HTML the template produces, not on a 200,
because a status-code-only test passed while the template was printing its own
source onto the face of the document.
"""
import pytest
from django.template.loader import render_to_string

from users.models import User
from memos.models import Memo, MemoWorkflowStep
from memos.services import generate_memo_number
from memos import workflow

from .conftest import add_sections, act, drive_to_approval, matrix, route


def _memo(author, **kwargs):
    fields = { "subject": "Approval sought",
        "memo_type": Memo.MemoType.GENERAL,
        "status": Memo.Status.DRAFT, "created_by": author,
    }
    fields.update(kwargs)
    fields.setdefault("memo_number", generate_memo_number(fields["memo_type"]))
    memo = Memo.objects.create(**fields)
    # Every memo raised through the API gets its Background and Recommendation
    # blocks; an ORM-built one needs them too, or the PDF renders no content.
    add_sections(memo, ("Background", "<p>Approval is sought as set out below.</p>"))
    workflow.record_creation(memo, author)
    return memo


@pytest.fixture
def cast(db):
    def make(username, role=User.Roles.MAKER, designation="Officer"):
        return User.objects.create_user(
            username=username, email=f"{username}@nif.test", password="pass12345",
            first_name=username.capitalize(), last_name="T", role=role,
            department="Finance", designation=designation)
    return {
        "author": make("p26_author"),
        "reviewer": make("p26_reviewer"),
        "recommender": make("p26_recommender", User.Roles.CHECKER, "Dept Head"),
        "supporter": make("p26_supporter", User.Roles.CHECKER, "Manager"),
        "approver": make("p26_approver", User.Roles.APPROVER, "Director"),
    }


def _render(memo, **overrides):
    """Render the memo PDF template the way views.pdf does."""
    context = {
        "memo": memo, "org": {}, "logo": None,
        "document_number": memo.memo_number if memo else "X",
        "verify_url": "http://x/verify", "verify_qr": "", "issue_date": "",
        "issue_date_bs": "", "to_name": "To", "from_name": "From", "cc_names": [],
        "author_designation": "Officer", "department_label": "Finance",
        # Mirrors views.pdf: the document renders one block per authored section.
        "sections": ([{"title": s.title, "body_html": s.body}
                      for s in memo.sections.all()] if memo else []),
        "approver_name": "x",
        "memo_type": "general", "memo_type_label": "GENERAL",
        "attachments": [], "steps": [],
        "fully_approved": bool(memo and memo.status in (
            Memo.Status.APPROVED, Memo.Status.ARCHIVED)),
        "matrix": workflow.pdf_matrix(memo) if memo else [],
        "signatures": workflow.signature_blocks(memo) if memo else [],
        "signature_rows": (workflow.signature_rows(workflow.signature_blocks(memo))
                           if memo else []),
        "certificate": workflow.approval_certificate(memo) if memo else None,
    }
    context.update(overrides)
    return render_to_string("pdf/memo.html", context)


# ---------------------------------------------------------------------------
# The stamp word
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_each_signatory_stamps_their_own_certification(api, cast):
    """
    The defect the redesign fixes: every completed step reported "Approved",
    so a recommender's block stamped APPROVED. On banking paperwork only the
    approver's stamp reads APPROVED.
    """
    memo = _memo(cast["author"])
    memo = drive_to_approval(api, cast["author"], memo,
                             (cast["reviewer"], "reviewer"),
                             (cast["recommender"], "recommender"),
                             (cast["supporter"], "supporter"),
                             (cast["approver"], "approver"))

    stamps = {b["heading"]: b["stamp"] for b in workflow.signature_blocks(memo)}
    assert stamps == {
        "Created By": "Created",
        "Reviewed By": "Reviewed",
        "Recommended By": "Recommended",
        "Supported By": "Supported",
        "Approved By": "Approved",
    }


@pytest.mark.django_db
def test_status_label_still_reads_approved_for_the_summary_column(api, cast):
    """
    `stamp` is additive. The workflow summary table and every existing caller read
    `status_label`, which must keep meaning "this step is signed off" - the two
    fields answer different questions and are deliberately not merged.
    """
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["recommender"], "recommender"),
          (cast["approver"], "approver"))
    act(api, cast["recommender"], memo, remarks="Recommended for approval.")

    block = {b["heading"]: b for b in workflow.signature_blocks(memo)}["Recommended By"]
    assert block["status_label"] == "Approved"
    assert block["stamp"] == "Recommended"


@pytest.mark.django_db
def test_an_unsigned_block_never_stamps_a_certification(api, cast):
    """
    A stamp asserts that somebody certified something. A queued, rejected or
    stood-down step has certified nothing, so its band must say why rather than
    carry a certification word.
    """
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["recommender"], "recommender"),
          (cast["supporter"], "supporter"), (cast["approver"], "approver"))

    blocks = {b["heading"]: b for b in workflow.signature_blocks(memo)}
    assert blocks["Recommended By"]["stamp"] == "Awaiting Action"
    assert blocks["Supported By"]["stamp"] == "Pending"
    assert blocks["Approved By"]["stamp"] == "Pending"
    for heading in ("Recommended By", "Supported By", "Approved By"):
        assert blocks[heading]["stamp"] not in ("Recommended", "Supported", "Approved")


@pytest.mark.django_db
def test_a_rejection_stamps_rejected_and_stands_the_rest_down(api, cast):
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["recommender"], "recommender"),
          (cast["approver"], "approver"))
    act(api, cast["recommender"], memo, decision="reject",
        remarks="The costing does not add up.")

    blocks = {b["heading"]: b for b in workflow.signature_blocks(memo)}
    assert blocks["Recommended By"]["stamp"] == "Rejected"
    assert blocks["Approved By"]["stamp"] == "Not Required"


# ---------------------------------------------------------------------------
# Verification IDs
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_verification_ids_are_stable_and_unique_per_signature(api, cast):
    """
    A code printed on paper has to still match months later, so it is derived from
    the identifiers that already pin the signature down rather than generated.
    """
    memo = _memo(cast["author"])
    memo = drive_to_approval(api, cast["author"], memo,
                             (cast["recommender"], "recommender"),
                             (cast["approver"], "approver"))

    first = {b["heading"]: b["verification_id"] for b in workflow.signature_blocks(memo)}
    second = {b["heading"]: b["verification_id"] for b in workflow.signature_blocks(memo)}
    assert first == second
    assert all(first.values())
    # One code per signature, not one per document.
    assert len(set(first.values())) == len(first)
    assert all(len(v) == 14 and v.count("-") == 2 for v in first.values())


@pytest.mark.django_db
def test_an_unsigned_step_carries_no_verification_id(api, cast):
    """
    An ID on an unsigned step would change the moment it was signed, which would
    invalidate anything quoting it.
    """
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["recommender"], "recommender"),
          (cast["approver"], "approver"))
    blocks = {b["heading"]: b for b in workflow.signature_blocks(memo)}
    assert blocks["Recommended By"]["verification_id"] == ""
    assert blocks["Approved By"]["verification_id"] == ""


@pytest.mark.django_db
def test_two_memos_do_not_share_a_verification_id(api, cast):
    ids = set()
    for _ in range(2):
        memo = drive_to_approval(api, cast["author"], _memo(cast["author"]),
                                 (cast["approver"], "approver"))
        ids.add(workflow.approval_certificate(memo)["verification_id"])
    assert len(ids) == 2


# ---------------------------------------------------------------------------
# The certificate, and therefore the seal
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_certificate_names_the_approver_and_the_date(api, cast):
    memo = _memo(cast["author"])
    memo = drive_to_approval(api, cast["author"], memo,
                             (cast["recommender"], "recommender"),
                             (cast["approver"], "approver"))

    certificate = workflow.approval_certificate(memo)
    assert certificate["stamp"] == "Approved"
    assert certificate["approved_by"] == "P26_approver T"
    assert certificate["designation"] == "Director"
    assert certificate["approved_at"] is not None
    assert certificate["verification_id"]


@pytest.mark.django_db
@pytest.mark.parametrize("status", [
    Memo.Status.DRAFT, Memo.Status.DRAFT_FOR_REVIEW, Memo.Status.UNDER_REVIEW,
    Memo.Status.RECOMMENDED, Memo.Status.SUPPORTED, Memo.Status.REJECTED,
    Memo.Status.CANCELLED,
])
def test_no_certificate_for_a_memo_that_is_not_approved(cast, status):
    """
    The seal is rendered on the certificate's presence alone, so this is what
    stops an APPROVED seal appearing over an in-flight or rejected memo.
    """
    memo = _memo(cast["author"], status=status)
    assert workflow.approval_certificate(memo) is None


@pytest.mark.django_db
def test_the_approver_is_read_off_the_step_not_a_denormalised_column(api, cast):
    """
    `approved_at` is stored on the memo but WHO approved it never has been. Reading
    the signer from the completed Approver step keeps one source of truth with the
    matrix printed just above the seal.
    """
    memo = _memo(cast["author"])
    memo = drive_to_approval(api, cast["author"], memo, (cast["approver"], "approver"))

    step = memo.workflow_steps.get(role_type=MemoWorkflowStep.RoleType.APPROVER)
    certificate = workflow.approval_certificate(memo)
    assert certificate["approved_by"] == step.display_name
    assert certificate["approved_at"] == step.acted_at


# ---------------------------------------------------------------------------
# The rendered document
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_pdf_prints_certification_blocks_and_the_final_seal(api, cast):
    memo = _memo(cast["author"])
    memo = drive_to_approval(api, cast["author"], memo,
                             (cast["recommender"], "recommender"),
                             (cast["supporter"], "supporter"),
                             (cast["approver"], "approver"))
    html = _render(memo)

    # No template source on the face of the document.
    for leak in ("{#", "{%", "{{"):
        assert leak not in html

    # The blocks, each with its own stamp band.
    for caption in ("Created By", "Recommended By", "Supported By", "Approved By"):
        assert f'<div class="cert-caption">{caption}</div>' in html
    for word in ("Created", "Recommended", "Supported", "Approved"):
        assert f'<span class="cert-stamp">{word}</span>' in html

    # The seal, and the particulars beside it.
    assert '<div class="seal-word">Approved</div>' in html
    assert "P26_approver T" in html
    assert "Verification ID" in html

    # The withdrawn web-card presentation is gone, asserted on the ELEMENTS: the
    # class names alone would still match their own CSS rules in the stylesheet.
    assert '<span class="sig-avatar">' not in html
    assert '<div class="sig-card' not in html


@pytest.mark.django_db
def test_the_document_ends_with_the_seal(api, cast):
    """
    Phase 26 visual hierarchy: content, then the workflow summary, then the
    approval blocks, then the seal last. Asserted on position inside <body>, since
    every one of these class names also appears earlier in the stylesheet.
    """
    memo = _memo(cast["author"])
    memo = drive_to_approval(api, cast["author"], memo, (cast["approver"], "approver"))
    body = _render(memo).split("<body>", 1)[1]

    positions = [
        # The first authored block's own heading - the document no longer has a
        # single section called "Memo".
        body.index('<div class="section-title">Background</div>'),
        body.index("Workflow Summary"),
        body.index("Official Approvals"),
        body.index('<div class="seal-word">'),
    ]
    assert positions == sorted(positions)


@pytest.mark.django_db
def test_an_unapproved_memo_prints_no_seal(api, cast):
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["approver"], "approver"))
    html = _render(memo)
    assert '<div class="seal-word">' not in html
    assert '<div class="approved-stamp">' not in html
    # The blocks still print - an in-flight memo shows who it is waiting on.
    assert '<div class="cert-caption">Approved By</div>' in html


# ---------------------------------------------------------------------------
# The API payload the archive view reads
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_detail_payload_carries_the_certificate(api, cast):
    memo = _memo(cast["author"])
    memo = drive_to_approval(api, cast["author"], memo, (cast["approver"], "approver"))

    api.force_authenticate(cast["author"])
    payload = api.get(f"/api/v1/memos/{memo.id}/").data
    certificate = payload["approval_certificate"]
    assert certificate["stamp"] == "Approved"
    assert certificate["approved_by"] == "P26_approver T"
    assert certificate["verification_id"]
    assert payload["signatures"][0]["stamp"] == "Created"


@pytest.mark.django_db
def test_detail_payload_omits_the_certificate_before_approval(api, cast):
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["approver"], "approver"))
    api.force_authenticate(cast["author"])
    assert api.get(f"/api/v1/memos/{memo.id}/").data["approval_certificate"] is None


@pytest.mark.django_db
def test_archive_list_names_the_approver(api, cast):
    """The archive answers "who approved this" from the list, not only the memo."""
    memo = _memo(cast["author"])
    drive_to_approval(api, cast["author"], memo, (cast["approver"], "approver"))

    api.force_authenticate(cast["author"])
    rows = api.get("/api/v1/memos/?scope=archived").data["results"]
    row = next(r for r in rows if r["memo_number"] == memo.memo_number)
    assert row["approved_by"] == "P26_approver T"
    assert row["approved_at"]


@pytest.mark.django_db
def test_list_reports_no_approver_for_an_in_flight_memo(api, cast):
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["approver"], "approver"))
    api.force_authenticate(cast["author"])
    rows = api.get("/api/v1/memos/?scope=mine").data["results"]
    row = next(r for r in rows if r["memo_number"] == memo.memo_number)
    assert row["approved_by"] is None


@pytest.mark.django_db
def test_naming_the_approver_costs_the_list_no_extra_queries(api, cast):
    """
    `approved_by` is derived per row. Derived from the PREFETCHED steps in Python -
    a `.filter()` on the related manager would ignore prefetch_related and fire a
    query per row, which is the defect that already cost this list ~100 queries a
    page once.

    Asserted by DOUBLING the row count and requiring the query count not to move,
    rather than by pinning an absolute number that would need editing every time an
    unrelated prefetch is added.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for _ in range(6):
        drive_to_approval(api, cast["author"], _memo(cast["author"]),
                          (cast["approver"], "approver"))

    api.force_authenticate(cast["author"])
    with CaptureQueriesContext(connection) as ctx:
        response = api.get("/api/v1/memos/?scope=archived&page_size=50")
    assert response.status_code == 200
    assert len(response.data["results"]) >= 6
    six_rows = len(ctx.captured_queries)

    for _ in range(6):
        drive_to_approval(api, cast["author"], _memo(cast["author"]),
                          (cast["approver"], "approver"))
    with CaptureQueriesContext(connection) as ctx:
        response = api.get("/api/v1/memos/?scope=archived&page_size=50")
    assert len(response.data["results"]) >= 12
    assert len(ctx.captured_queries) == six_rows, (
        f"query count grew with row count: {six_rows} -> {len(ctx.captured_queries)}")
