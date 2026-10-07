"""
Phase 30 - compact approval blocks whose row width follows the chain length.

The layout rule is data, not CSS, so it is testable: workflow.signature_rows()
groups the blocks and the template renders whatever grouping it is handed. That is
what "do not hardcode the number of blocks" means in practice - there is no block
count in the template and no per-count special case anywhere.

The interesting cases are the wraps. Three and four across are the easy ones; five
or more has to split WITHOUT leaving a row holding a single block beside empty
space, which is what a greedy fill produces.
"""
import pytest

from users.models import User
from memos.models import Memo
from memos.services import generate_memo_number
from memos import workflow

from .conftest import drive_to_approval, route


def _blocks(n):
    """n minimal certification blocks - only the keys the grouping reads."""
    return [{"heading": f"Step {i} By", "stamp": "Approved", "state": "done",
             "name": f"Person {i}", "designation": "Officer", "department": "Finance",
             "at": None, "status_label": "Approved", "remarks": "", "initials": "PX",
             "verified": True, "verification_id": "1111-2222-3333"}
            for i in range(n)]


# ---------------------------------------------------------------------------
# The grouping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("count,shape", [
    (1, [1]),
    (2, [2]),
    (3, [3]),          # the brief's headline case: three steps, one row
    (4, [4]),          # four steps, one row
    (5, [3, 2]),       # 5+ wraps
    (6, [3, 3]),
    (7, [4, 3]),
    (8, [4, 4]),
    (9, [3, 3, 3]),
    (10, [4, 3, 3]),
    (11, [4, 4, 3]),
    (12, [4, 4, 4]),
])
def test_rows_follow_the_chain_length(count, shape):
    rows = workflow.signature_rows(_blocks(count))
    assert [row["columns"] for row in rows] == shape
    # Nothing is dropped or duplicated by the grouping.
    assert sum(row["columns"] for row in rows) == count
    assert [b["name"] for row in rows for b in row["blocks"]] == \
        [b["name"] for b in _blocks(count)]


@pytest.mark.parametrize("count", range(1, 25))
def test_no_row_is_left_holding_a_lone_block(count):
    """
    A greedy fill gives 4+1 for five blocks - one block beside three empty cells,
    which looks like a rendering fault. Rows must stay balanced to within one.
    """
    rows = workflow.signature_rows(_blocks(count))
    widths = [row["columns"] for row in rows]
    assert max(widths) <= workflow.MAX_BLOCKS_PER_ROW
    if count > 1:
        assert max(widths) - min(widths) <= 1


@pytest.mark.parametrize("count", range(1, 13))
def test_cell_widths_fill_each_row(count):
    for row in workflow.signature_rows(_blocks(count)):
        assert round(row["width"] * row["columns"]) == 100


def test_no_blocks_means_no_rows():
    assert workflow.signature_rows([]) == []


@pytest.mark.parametrize("count,expected", [
    (1, True), (2, True), (3, True),     # room for the department line
    (4, False),                          # four across is too narrow for it
    (7, False),                          # wraps to 4+3 - the 4-row suppresses it
])
def test_department_shows_only_where_it_fits(count, expected):
    """
    "Department only if space permits" from the brief, decided per row rather than
    per document, and decided here rather than as a magic number in a CSS rule.
    """
    rows = workflow.signature_rows(_blocks(count))
    assert rows[0]["show_department"] is expected


@pytest.mark.parametrize("count", range(1, 13))
def test_every_row_agrees_about_the_department_line(count):
    """
    Decided once per section, not per row. A seven-step chain wraps to 4+3, and a
    per-row decision gave the narrower second row a department line the first row
    lacked - half the section carrying an extra line for no visible reason, which
    reads as a rendering fault rather than a choice.
    """
    flags = {row["show_department"] for row in workflow.signature_rows(_blocks(count))}
    assert len(flags) == 1


def test_a_narrower_row_limit_regroups_without_code_changes():
    """The limit is a parameter, not a constant baked into the loop."""
    rows = workflow.signature_rows(_blocks(6), max_per_row=2)
    assert [row["columns"] for row in rows] == [2, 2, 2]


# ---------------------------------------------------------------------------
# The rendered document
# ---------------------------------------------------------------------------
def _memo(author, **kwargs):
    fields = {"subject": "S", "memo_type": Memo.MemoType.GENERAL, "status": Memo.Status.DRAFT,
              "created_by": author}
    fields.update(kwargs)
    fields.setdefault("memo_number", generate_memo_number(fields["memo_type"]))
    memo = Memo.objects.create(**fields)
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
        "author": make("p30_author"),
        "reviewer": make("p30_reviewer"),
        "recommender": make("p30_recommender", User.Roles.CHECKER, "Dept Head"),
        "supporter": make("p30_supporter", User.Roles.CHECKER, "Manager"),
        "approver": make("p30_approver", User.Roles.APPROVER, "Director"),
    }


def _render(memo):
    from django.template.loader import render_to_string
    signatures = workflow.signature_blocks(memo)
    return render_to_string("pdf/memo.html", {
        "memo": memo, "org": {}, "logo": None, "document_number": memo.memo_number,
        "verify_url": "", "verify_qr": "", "issue_date": "", "issue_date_bs": "",
        "to_name": "To", "from_name": "From", "cc_name": "",
        "author_designation": "Officer", "department_label": "Finance",
        "body_html": "<p>body</p>", "approver_name": "x", "memo_type": "general",
        "attachments": [], "steps": [],
        "fully_approved": memo.status in (Memo.Status.APPROVED, Memo.Status.ARCHIVED),
        "matrix": workflow.pdf_matrix(memo),
        "signatures": signatures,
        "signature_rows": workflow.signature_rows(signatures),
        "certificate": workflow.approval_certificate(memo),
    })


@pytest.mark.django_db
def test_three_signatures_print_as_one_row_of_three(api, cast):
    """The brief's headline case, end to end from the workflow engine."""
    memo = drive_to_approval(api, cast["author"], _memo(cast["author"]),
                             (cast["recommender"], "recommender"),
                             (cast["approver"], "approver"))
    html = _render(memo)

    # Created + Recommended + Approved = three blocks, one row, one table.
    assert html.count('<table class="cert-row-grid">') == 1
    assert html.count('<td class="cert-cell') == 3
    assert 'style="width: 33.3333%"' in html


@pytest.mark.django_db
def test_five_signatures_wrap_into_two_rows(api, cast):
    memo = drive_to_approval(api, cast["author"], _memo(cast["author"]),
                             (cast["reviewer"], "reviewer"),
                             (cast["recommender"], "recommender"),
                             (cast["supporter"], "supporter"),
                             (cast["approver"], "approver"))
    html = _render(memo)
    # Created + 4 steps = 5 blocks -> 3 + 2.
    assert html.count('<table class="cert-row-grid">') == 2
    assert html.count('<td class="cert-cell') == 5


@pytest.mark.django_db
def test_the_seal_still_comes_after_every_block(api, cast):
    """Compact blocks, prominent seal - and the seal stays last."""
    memo = drive_to_approval(api, cast["author"], _memo(cast["author"]),
                             (cast["recommender"], "recommender"),
                             (cast["approver"], "approver"))
    body = _render(memo).split("<body>", 1)[1]
    assert body.rindex('<td class="cert-cell') < body.index('<div class="seal-word">')


@pytest.mark.django_db
def test_the_authorisation_wording_appears_once_not_per_block(api, cast):
    """
    An identical three-line sentence repeated under every signature is what made
    the old blocks tall. It is now one footnote under the section.
    """
    memo = drive_to_approval(api, cast["author"], _memo(cast["author"]),
                             (cast["recommender"], "recommender"),
                             (cast["approver"], "approver"))
    html = _render(memo)
    assert html.count("no wet signature is required") == 1
    assert html.count('<div class="cert-verify">') == 3


@pytest.mark.django_db
def test_an_in_flight_memo_prints_compact_blocks_without_a_seal(api, cast):
    memo = _memo(cast["author"])
    route(api, cast["author"], memo, (cast["recommender"], "recommender"),
          (cast["approver"], "approver"))
    html = _render(memo)
    assert html.count('<td class="cert-cell') == 3
    assert '<div class="seal-word">' not in html


@pytest.mark.django_db
def test_the_template_carries_no_block_count(api, cast):
    """
    Guards the brief's "DO NOT HARDCODE NUMBER OF BLOCKS" against a future edit:
    the row width must come from the data, never from a literal in the template.

    Phase 31 moved the block markup into pdf/_approval_blocks.html so the minute PDF
    could print the identical section, so this now follows the markup to the partial
    and additionally asserts memo.html still INCLUDES it - a future edit that inlined
    the blocks again would silently un-share the two documents.
    """
    from pathlib import Path
    from django.conf import settings

    memo_template = (Path(settings.BASE_DIR) / "memos/templates/pdf/memo.html").read_text()
    partial = (Path(settings.BASE_DIR)
               / "documents/templates/pdf/_approval_blocks.html").read_text()

    assert '{% include "pdf/_approval_blocks.html" %}' in memo_template
    assert "signature_rows" in partial
    # The old pairing did its own arithmetic with divisibleby:2.
    assert "divisibleby" not in partial
    # No fixed cell width - it is interpolated from row.width.
    assert "{{ row.width }}%" in partial
    for literal in ("width: 33%", "width: 50%", "width: 25%"):
        assert literal not in partial
        assert literal not in memo_template
