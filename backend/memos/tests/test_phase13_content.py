"""
Phases 13-16: the enterprise content editor's server side.

The sanitizer is the gate — nothing the editor produces reaches a reader unless it
is allowed there — so most of this file is about what survives a save and what
does not. The rest covers classification, the role-hierarchy rule and the
signature cards.
"""
import pytest

from memos.models import Memo, MemoWorkflowStep
from memos.sanitizers import sanitize_memo_html
from memos.services import MIN_COMMENT_LENGTH, generate_memo_number
from users.models import User

from .conftest import act, drive_to_approval, matrix, route

RoleType = MemoWorkflowStep.RoleType

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def _memo(author, **kwargs):
    fields = { "subject": "S", "memo_type": Memo.MemoType.GENERAL, "status": Memo.Status.DRAFT,
        "created_by": author,
    }
    fields.update(kwargs)
    fields.setdefault("memo_number", generate_memo_number(fields["memo_type"]))
    return Memo.objects.create(**fields)


# ---------------------------------------------------------------------------
# Phase 13 - what the editor may now save
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label,html,must_contain", [
    ("table with header and spans",
     '<table class="memo-table"><thead><tr><th colspan="2">Head</th></tr></thead>'
     '<tbody><tr><td rowspan="2">a</td><td>b</td></tr></tbody></table>',
     ["<table", "<thead", "<th", 'colspan="2"', 'rowspan="2"', "memo-table"]),
    ("per-column widths from the resize handles",
     '<table><tr><td colwidth="180">x</td></tr></table>', ['colwidth="180"']),
    # Wrapped in a table on purpose: html5lib drops a <td> that has no table
    # context, which is a parser rule rather than a sanitizer decision.
    ("cell alignment",
     '<table><tr><td style="text-align:right">1</td></tr></table>',
     ["text-align:right"]),
    ("text colour and highlight",
     '<p><span style="color:#b91c1c;background-color:#fef08a">x</span></p>',
     ["color:#b91c1c", "background-color:#fef08a"]),
    ("font family and size",
     '<p><span style="font-family:Georgia;font-size:18px">x</span></p>',
     ["font-family:Georgia", "font-size:18px"]),
    ("paragraph alignment and line spacing",
     '<p style="text-align:justify;line-height:2">x</p>',
     ["text-align:justify", "line-height:2"]),
    ("indentation", '<p style="margin-left:40px">x</p>', ["margin-left:40px"]),
    ("strikethrough, sub and superscript",
     "<p><s>a</s><sub>b</sub><sup>c</sup><u>d</u></p>",
     ["<s>", "<sub>", "<sup>", "<u>"]),
    ("checklist", '<ul data-type="taskList"><li data-checked="true">x</li></ul>',
     ['data-type="taskList"', 'data-checked="true"']),
    ("horizontal rule", "<p>a</p><hr><p>b</p>", ["<hr>"]),
    ("page break", '<div class="memo-page-break"></div>', ["memo-page-break"]),
    ("section divider", '<div class="memo-divider"></div>', ["memo-divider"]),
    ("embedded raster image", f'<img src="{PNG}" alt="chart">', ["<img", "data:image/png"]),
    ("heading levels", "<h1>a</h1><h4>b</h4>", ["<h1>", "<h4>"]),
    ("blockquote", "<blockquote><p>q</p></blockquote>", ["<blockquote>"]),
])
def test_editor_output_survives_the_sanitizer(label, html, must_contain):
    """
    Every one of these was silently stripped before Phase 13 - the author saw
    their formatting vanish on save with no error at all.
    """
    cleaned = sanitize_memo_html(html)
    for fragment in must_contain:
        assert fragment in cleaned, f"{label}: lost {fragment!r} -> {cleaned}"


@pytest.mark.parametrize("label,html,must_not_contain", [
    ("script tag", "<p>ok</p><script>alert(1)</script>", ["<script"]),
    ("event handler", f'<img src="{PNG}" onerror="alert(1)">', ["onerror"]),
    ("javascript href", '<a href="javascript:alert(1)">x</a>', ["javascript:"]),
    ("data href", '<a href="data:text/html,<script>x</script>">y</a>', ["data:text/html"]),
    ("svg image", '<img src="data:image/svg+xml;base64,PHN2Zz4=">', ["svg"]),
    # A remote src would make WeasyPrint fetch an arbitrary URL from inside our
    # network when rendering the PDF - an SSRF primitive and a read receipt.
    ("remote image", '<img src="https://evil.test/track.png">', ["evil.test"]),
    ("css url()", '<p style="background-color:red;border-image:url(https://evil.test/x)">y</p>',
     ["evil.test", "url("]),
    ("position fixed", '<p style="position:fixed;top:0">x</p>', ["position"]),
    ("iframe", '<iframe src="https://evil.test"></iframe>', ["iframe"]),
    ("style block", "<style>body{display:none}</style><p>x</p>", ["<style"]),
    ("unstyled class", '<div class="app-sidebar">x</div>', ["app-sidebar"]),
])
def test_dangerous_content_is_still_refused(label, html, must_not_contain):
    cleaned = sanitize_memo_html(html)
    for fragment in must_not_contain:
        assert fragment.lower() not in cleaned.lower(), f"{label}: kept {fragment!r} -> {cleaned}"


def test_a_blocked_image_leaves_nothing_behind():
    """
    Not merely stripped of its src: removed. A bare <img> renders as a broken-image
    icon on screen and an empty box in the PDF, where the correct outcome is
    nothing at all.
    """
    assert sanitize_memo_html('<p>a<img src="https://evil.test/x.png">b</p>') == "<p>ab</p>"


@pytest.mark.django_db
def test_a_full_document_round_trips_through_the_api(api, maker):
    """
    The end-to-end path that matters: a memo authored with a financial table, an
    image and mixed formatting comes back out of the API intact.
    """
    body = (
        '<h2 style="text-align:center">Q3 Budget</h2>'
        '<table class="memo-table memo-table-financial">'
        '<thead><tr><th>Item</th><th>Amount</th></tr></thead>'
        '<tbody><tr><td colwidth="200">Servers</td>'
        '<td style="text-align:right">1,250,000</td></tr></tbody></table>'
        f'<p><img src="{PNG}" alt="trend"></p>'
        '<div class="memo-page-break"></div>'
        '<ul data-type="taskList"><li data-checked="true">Quotation attached</li></ul>'
    )
    api.force_authenticate(maker)
    created = api.post("/api/v1/memos/",
                       {"subject": "Q3", "to_line": "CEO", "memo_type": "general"},
                       format="json")
    assert created.status_code == 201, created.data

    written = api.post(f"/api/v1/memos/{created.data['id']}/sections/",
                       {"sections": [{"title": "Background", "body": body}]},
                       format="json")
    assert written.status_code == 200, written.data

    detail = api.get(f"/api/v1/memos/{created.data['id']}/").data
    stored = detail["sections"][0]["body"]
    for fragment in ("memo-table-financial", "<thead", 'colwidth="200"',
                     "text-align:right", "data:image/png", "memo-page-break",
                     'data-type="taskList"'):
        assert fragment in stored, f"lost {fragment!r}"


# ---------------------------------------------------------------------------
# Phase 14 - classification and reference number
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_memo_type_defaults_to_general_and_round_trips(api, maker):
    """
    One dropdown with three values (E-memo-manual p.4). The pair of columns this
    replaced - a business category plus a separate sensitivity - is gone.
    """
    api.force_authenticate(maker)
    plain = api.post("/api/v1/memos/",
                     {"subject": "S", "to_line": "CEO"}, format="json")
    assert plain.data["status"] == "draft"
    assert Memo.objects.get(pk=plain.data["id"]).memo_type == "general"

    marked = api.post("/api/v1/memos/", {
        "subject": "S", "to_line": "CEO", "memo_type": "confidential",
        "reference_number": "TENDER/2026/017",
    }, format="json")
    detail = api.get(f"/api/v1/memos/{marked.data['id']}/").data
    assert detail["memo_type"] == "confidential"
    assert detail["memo_type_label"] == "CONFIDENTIAL"
    assert detail["reference_number"] == "TENDER/2026/017"
    assert detail["is_restricted"] is True


@pytest.mark.django_db
def test_a_general_memo_is_not_restricted(api, maker):
    """GENERAL is the open type: it must not be treated as secret."""
    memo = _memo(maker, memo_type=Memo.MemoType.GENERAL)
    assert memo.is_restricted is False


@pytest.mark.django_db
def test_a_draft_type_memo_keeps_no_log(api, maker):
    """
    "This is only temporary and log record is not kept for this one" (p.4).

    Asserted on the memo's own history rather than on a flag: a DRAFT memo that
    quietly accumulated approval steps would satisfy any flag you cared to set.
    """
    api.force_authenticate(maker)
    created = api.post("/api/v1/memos/", {
        "subject": "Circulated for comment", "to_line": "CEO",
        "memo_type": "draft",
    }, format="json")
    assert created.status_code == 201, created.data
    memo = Memo.objects.get(pk=created.data["id"])

    assert memo.keeps_log is False
    assert memo.approval_steps.count() == 0

    # ...and an edit does not start one either.
    api.patch(f"/api/v1/memos/{memo.id}/", {"subject": "Still a draft"},
              format="json")
    assert memo.approval_steps.count() == 0


@pytest.mark.django_db
def test_a_general_memo_does_keep_a_log(api, maker):
    """The counterpart, so the rule above is shown to be about DRAFT and not
    about memos in general."""
    api.force_authenticate(maker)
    created = api.post("/api/v1/memos/", {
        "subject": "Formally raised", "to_line": "CEO", "memo_type": "general",
    }, format="json")
    memo = Memo.objects.get(pk=created.data["id"])
    assert memo.keeps_log is True
    assert memo.approval_steps.count() >= 1


@pytest.mark.django_db
def test_a_confidential_memo_is_hidden_from_its_own_department_head(api, maker,
                                                                   checker, approver):
    """
    A CONFIDENTIAL memo is out of the department head's scope: "accessed only by
    the employees (by function) who have role in the memo" (p.4).
    """
    open_memo = _memo(maker, subject="Open", status=Memo.Status.DRAFT_FOR_REVIEW,
                      department_name="Finance")
    secret = _memo(maker, subject="Secret", status=Memo.Status.DRAFT_FOR_REVIEW,
                   memo_type="confidential", department_name="Finance")

    api.force_authenticate(checker)
    visible = {row["id"] for row in api.get("/api/v1/memos/", {"scope": "department"}).data["results"]}
    assert str(open_memo.id) in visible
    assert str(secret.id) not in visible
    # And the object-level guard agrees with the queryset, as it must.
    assert api.get(f"/api/v1/memos/{secret.id}/").status_code == 404

    # Being routed on it is still enough, classification or not. Uses a fresh
    # draft: `secret` above is already past DRAFT, and send-for-review only
    # accepts a draft.
    routed = _memo(maker, subject="Secret routed", memo_type="confidential",
                   department_name="Finance")
    route(api, maker, routed, (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(checker)
    assert api.get(f"/api/v1/memos/{routed.id}/").status_code == 200


@pytest.mark.django_db
def test_classification_and_reference_are_filterable(api, maker):
    _memo(maker, subject="Plain")
    secret = _memo(maker, subject="Secret", memo_type="confidential")
    referenced = _memo(maker, subject="Referenced", reference_number="BOARD/2026/09")

    api.force_authenticate(maker)
    by_class = api.get("/api/v1/memos/", {"memo_type": "confidential"})
    assert [r["id"] for r in by_class.data["results"]] == [str(secret.id)]

    by_ref = api.get("/api/v1/memos/", {"reference_number": "board/2026"})
    assert [r["id"] for r in by_ref.data["results"]] == [str(referenced.id)]

    # And the reference is searchable alongside the memo number.
    found = api.get("/api/v1/memos/", {"search": "BOARD/2026/09"})
    assert [r["id"] for r in found.data["results"]] == [str(referenced.id)]


# ---------------------------------------------------------------------------
# Phase 15 - the role hierarchy must not run backwards
# ---------------------------------------------------------------------------
@pytest.fixture
def cast(db):
    def make(username, role=User.Roles.MAKER, designation="Officer"):
        return User.objects.create_user(
            username=username, email=f"{username}@nif.test", password="pass12345",
            first_name=username.capitalize(), last_name="T", role=role,
            department="Finance", designation=designation)
    return {
        "author": make("p13_author"),
        "reviewer": make("p13_reviewer"),
        "recommender": make("p13_recommender", User.Roles.CHECKER, "Dept Head"),
        "supporter": make("p13_supporter", User.Roles.CHECKER, "Manager"),
        "approver": make("p13_approver", User.Roles.APPROVER, "Director"),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("order", [
    ("supporter", "recommender", "approver"),   # support before recommendation
    ("recommender", "reviewer", "approver"),    # review after recommendation
    ("supporter", "reviewer", "approver"),
])
def test_a_chain_whose_roles_run_backwards_is_refused(api, cast, order):
    """
    Phase 15: Recommended must complete before Supported, and Supported before
    Approved. Enforced on the MATRIX rather than re-checked per transition - if the
    chain cannot express an out-of-order approval, the engine cannot perform one.
    """
    memo = _memo(cast["author"])
    api.force_authenticate(cast["author"])
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/",
                   {"workflow": matrix(*[(cast[r], r) for r in order])}, format="json")
    assert res.status_code == 400
    assert "must be Reviewer, then Recommender" in str(res.data)
    assert not memo.workflow_steps.exists()


@pytest.mark.django_db
def test_the_canonical_hierarchy_is_accepted(api, cast):
    memo = _memo(cast["author"])
    api.force_authenticate(cast["author"])
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/", {"workflow": matrix(
        (cast["reviewer"], "reviewer"), (cast["recommender"], "recommender"),
        (cast["supporter"], "supporter"), (cast["approver"], "approver"),
    )}, format="json")
    assert res.status_code == 200, res.data


@pytest.mark.django_db
def test_two_people_at_the_same_rank_are_allowed(api, cast):
    """Two supporters in sequence is a real chain; the sequence still orders them."""
    second = User.objects.create_user(
        username="p13_supporter2", email="s2@nif.test", password="pass12345",
        first_name="Second", last_name="Supporter", role=User.Roles.CHECKER,
        department="Finance")
    memo = _memo(cast["author"])
    api.force_authenticate(cast["author"])
    res = api.post(f"/api/v1/memos/{memo.id}/matrix/", {"workflow": matrix(
        (cast["supporter"], "supporter"), (second, "supporter"),
        (cast["approver"], "approver"),
    )}, format="json")
    assert res.status_code == 200, res.data


@pytest.mark.django_db
def test_the_full_hierarchy_walks_created_to_archived(api, cast):
    memo = _memo(cast["author"])
    memo = drive_to_approval(
        api, cast["author"], memo,
        (cast["recommender"], "recommender"),
        (cast["supporter"], "supporter"),
        (cast["approver"], "approver"),
    )
    assert memo.status == Memo.Status.ARCHIVED
    history = list(memo.approval_steps.order_by("step_order")
                   .values_list("action", flat=True))
    assert history == ["sent_for_review", "recommended", "supported",
                       "approved", "archived"]


# ---------------------------------------------------------------------------
# Phase 16 - signature cards
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_signature_cards_open_with_created_by(api, cast):
    from memos.workflow import signature_blocks

    memo = _memo(cast["author"])
    route(api, cast["author"], memo,
          (cast["recommender"], "recommender"), (cast["approver"], "approver"))

    blocks = signature_blocks(memo)
    assert [b["heading"] for b in blocks] == [
        "Created By", "Recommended By", "Approved By"]

    created = blocks[0]
    assert created["name"] == "P13_author T"
    assert created["designation"] == "Officer"
    assert created["initials"] == "PT"
    # The author's card is complete by definition; the others are not yet signed.
    assert created["verified"] is True
    assert blocks[1]["verified"] is False
    assert blocks[1]["state"] == "active"
    assert blocks[2]["state"] == "pending"


@pytest.mark.django_db
def test_signature_card_verification_follows_the_actual_signature(api, cast):
    from memos.workflow import signature_blocks

    memo = _memo(cast["author"])
    route(api, cast["author"], memo,
          (cast["recommender"], "recommender"), (cast["approver"], "approver"))
    act(api, cast["recommender"], memo, remarks="Recommended for approval.")

    blocks = {b["heading"]: b for b in signature_blocks(memo)}
    assert blocks["Recommended By"]["verified"] is True
    assert blocks["Recommended By"]["status_label"] == "Approved"
    assert blocks["Recommended By"]["remarks"] == "Recommended for approval."
    assert blocks["Recommended By"]["at"] is not None
    # Not yet signed, so not yet verified.
    assert blocks["Approved By"]["verified"] is False


@pytest.mark.django_db
def test_signature_card_shows_a_rejection(api, cast):
    from memos.workflow import signature_blocks

    memo = _memo(cast["author"])
    route(api, cast["author"], memo,
          (cast["recommender"], "recommender"), (cast["approver"], "approver"))
    act(api, cast["recommender"], memo, decision="reject",
        remarks="The costing does not add up.")

    blocks = {b["heading"]: b for b in signature_blocks(memo)}
    assert blocks["Recommended By"]["state"] == "rejected"
    assert blocks["Recommended By"]["verified"] is False
    # The step that never ran reads "Not Required", not "Pending" forever.
    assert blocks["Approved By"]["status_label"] == "Not Required"


@pytest.mark.django_db
def test_detail_payload_carries_signatures_and_tracker(api, cast):
    memo = _memo(cast["author"])
    route(api, cast["author"], memo,
          (cast["recommender"], "recommender"), (cast["approver"], "approver"))

    api.force_authenticate(cast["author"])
    data = api.get(f"/api/v1/memos/{memo.id}/").data
    assert [b["heading"] for b in data["signatures"]] == [
        "Created By", "Recommended By", "Approved By"]
    # Phase 49.5 added `reviewed` and `noted`. Reviewed sits first among the role
    # stages, matching ROLE_RANK where reviewer is rank 0; noted sits after approved
    # because a note is information given about a decided memo, and it is the one
    # stage read from the note round rather than from a workflow step.
    assert [s["key"] for s in data["tracker"]] == [
        "created", "reviewed", "recommended", "supported", "approved", "noted",
        "archived"]


# ---------------------------------------------------------------------------
# Phase 18 - the workflow tracker
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_tracker_states_advance_with_the_chain(api, cast):
    from memos.workflow import build_tracker

    memo = _memo(cast["author"])
    route(api, cast["author"], memo,
          (cast["recommender"], "recommender"), (cast["supporter"], "supporter"),
          (cast["approver"], "approver"))

    def states():
        memo.refresh_from_db()
        return {s["key"]: s["state"] for s in build_tracker(memo)}

    # `reviewed` and `noted` are "skipped", not "pending": this chain has no
    # reviewer and nobody was asked to note, and a stage the memo never asked for is
    # not something it is forever waiting on. That distinction is what makes the two
    # new stages safe to add to every memo's tracker.
    assert states() == {"created": "done", "reviewed": "skipped",
                        "recommended": "active", "supported": "pending",
                        "approved": "pending", "noted": "skipped",
                        "archived": "pending"}

    act(api, cast["recommender"], memo, remarks="Recommended for approval.")
    assert states()["recommended"] == "done"
    assert states()["supported"] == "active"

    # Exactly at the floor with no margin, so it is padded against the live
    # constant rather than left one policy change away from breaking.
    act(api, cast["supporter"], memo,
        remarks="Supported; the case is made.".ljust(MIN_COMMENT_LENGTH, "."))
    act(api, cast["approver"], memo, remarks="Approved; figures verified.")
    assert states() == {"created": "done", "reviewed": "skipped",
                        "recommended": "done", "supported": "done",
                        "approved": "done", "noted": "skipped",
                        "archived": "done"}


@pytest.mark.django_db
def test_tracker_marks_a_stage_the_chain_omits_as_skipped(api, cast):
    """
    A memo routed straight to an approver is not forever "pending" a
    recommendation it never asked for.
    """
    from memos.workflow import build_tracker

    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["approver"], "approver"))
    states = {s["key"]: s["state"] for s in build_tracker(memo)}
    assert states["recommended"] == "skipped"
    assert states["supported"] == "skipped"
    assert states["approved"] == "active"


@pytest.mark.django_db
def test_tracker_shows_a_rejection_and_stands_the_rest_down(api, cast):
    from memos.workflow import build_tracker

    memo = _memo(cast["author"])
    route(api, cast["author"], memo,
          (cast["recommender"], "recommender"), (cast["approver"], "approver"))
    act(api, cast["recommender"], memo, decision="reject",
        remarks="Not supportable as costed.")

    memo.refresh_from_db()
    states = {s["key"]: s["state"] for s in build_tracker(memo)}
    assert states["recommended"] == "rejected"
    assert states["approved"] == "skipped"
    assert states["archived"] == "rejected"


@pytest.mark.django_db
def test_tracker_names_who_each_stage_is_with(api, cast):
    from memos.workflow import build_tracker

    memo = _memo(cast["author"])
    route(api, cast["author"], memo,
          (cast["recommender"], "recommender"), (cast["approver"], "approver"))
    stages = {s["key"]: s for s in build_tracker(memo)}
    assert stages["created"]["actor"] == "P13_author T"
    assert stages["recommended"]["actor"] == "P13_recommender T"
    assert stages["approved"]["actor"] == "P13_approver T"


# ---------------------------------------------------------------------------
# The generated document, against the manual (E-memo-manual pp. 4, 6)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTheGeneratedMemoDocument:
    """
    What the memo PDF SAYS, asserted on the rendered HTML rather than the PDF
    bytes: WeasyPrint subsets its fonts, so scraping text out of the binary finds
    fragments and proves nothing either way.
    """

    def render(self, api, memo, actor):
        """
        Render the document template directly.

        NOT through /pdf/: that endpoint is deliberately available only for an
        approved or archived memo, and these tests are about what the template
        prints, which is the same at every status. `context()` below mirrors
        MemoViewSet.pdf field for field.
        """
        from django.template.loader import render_to_string
        return render_to_string("pdf/memo.html", self.context(memo))

    def context(self, memo):
        from documents.pdf import common_context
        from memos.sanitizers import sanitize_memo_html
        from memos import workflow
        ctx = common_context(memo.memo_number)
        ctx.update({
            "memo": memo, "to_name": memo.to_line or "—", "from_name": "Author",
            "cc_names": [d.name for d in memo.cc_departments.all()],
            "author_designation": "Officer",
            "department_label": memo.resolved_department_name() or "—",
            "sections": [{"title": s.title,
                          "body_html": sanitize_memo_html(s.body)}
                         for s in memo.sections.all()],
            "approver_name": "—",
            "memo_type": memo.memo_type,
            "memo_type_label": memo.get_memo_type_display(),
            "fully_approved": False, "attachments": [], "steps": [],
            "matrix": workflow.pdf_matrix(memo),
            "signatures": workflow.signature_blocks(memo),
            "signature_rows": workflow.signature_rows(
                workflow.signature_blocks(memo)),
        })
        return ctx

    def test_the_header_carries_the_manuals_rows(self, api, maker, general_draft):
        general_draft.to_line = "CEO"
        general_draft.save(update_fields=["to_line"])
        html = self.render(api, general_draft, maker)

        for row in ("To", "From", "Date", "Subject", "Memo Type"):
            assert f'class="k">{row}<' in html, f"{row} row missing from the memo PDF"
        assert "CEO" in html

    def test_each_content_block_prints_under_its_own_heading(
            self, api, maker, general_draft):
        from memos.models import MemoSection
        MemoSection.objects.filter(memo=general_draft).delete()
        MemoSection.objects.create(memo=general_draft, position=0, title="Background",
                                   body="<p>Why this matters.</p>")
        MemoSection.objects.create(memo=general_draft, position=1,
                                   title="Recommendation",
                                   body="<p>Approve as tabled.</p>")

        html = self.render(api, general_draft, maker)
        assert '<div class="section-title">Background</div>' in html
        assert '<div class="section-title">Recommendation</div>' in html
        assert html.index("Background") < html.index("Recommendation")
        assert "Why this matters." in html
        assert "Approve as tabled." in html

    def test_confidential_is_printed_on_the_face_of_the_document(
            self, api, maker, draft_memo):
        assert draft_memo.memo_type == "confidential"
        html = self.render(api, draft_memo, maker)
        assert "CONFIDENTIAL" in html

    def test_no_template_source_leaks_onto_the_page(self, api, maker, general_draft):
        """
        Django's hash comment is single-line only, so a multi-line {# ... #} prints
        onto the face of the document. One did, briefly, when the header was
        rewritten - hence this guard.
        """
        html = self.render(api, general_draft, maker)
        body = html.split("<body>", 1)[1]
        for leak in ("{#", "{%", "{{"):
            assert leak not in body, f"{leak} leaked into the document"
