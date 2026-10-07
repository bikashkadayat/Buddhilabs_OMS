"""
Approval certification primitives, shared by every document type that prints a
banking-style approval section.

WHY THIS IS NOT IN memos/
-------------------------
Phase 36 asks the minute PDF for exactly the approval treatment Phases 26 and 30
built for memos: role-specific stamp words, compact rows that fit three or four
signatories across, balanced wrapping beyond that, and derived verification IDs.
That is the same behaviour, not similar behaviour - so it lives once, here, and
both `memos.workflow` and `minutes.workflow` import it.

The functions are deliberately free of any model import. They take plain values and
lists of plain dicts, which is what makes them shareable across two apps whose
tables have nothing to do with each other: the memo module keeps its own step model
and the minute module keeps its own, and neither leaks into this file.

The status MACHINES are not shared and should not be. A memo goes
Draft -> ... -> Approved -> Archived; a minute additionally carries decisions,
action items and an acknowledgement round that runs after approval. Forcing one
engine to express both would produce a configuration language, not a simplification.
"""
import hashlib

# The certification word printed inside a block's stamp band, keyed by role code.
#
# Deliberately NOT a status label. A completed step's status reads "Approved"
# whatever its role, which is right for a status column and wrong for a stamp: on
# banking paperwork the recommender's stamp reads RECOMMENDED and only the
# approver's reads APPROVED.
#
# Keyed by the role CODES both modules use ("reviewer", "recommender", ...) plus the
# two synthetic openers - "created" for a memo's author and "initiated" for a
# minute's initiator, who occupy the same first block on the document.
STAMP_WORDS = {
    "created": "Created",
    "initiated": "Initiated",
    "reviewer": "Reviewed",
    "recommender": "Recommended",
    "supporter": "Supported",
    "approver": "Approved",
}

# What the band reads when the step has NOT been signed. A stamp asserts that
# somebody certified something, so an unsigned block must never show a
# certification word - it shows why there isn't one.
UNSIGNED_STAMPS = {
    "rejected": "Rejected",
    "active": "Awaiting Action",
    "pending": "Pending",
    "skipped": "Not Required",
}

# The widest a certification row gets before it is worth wrapping. Four blocks
# across A4's usable 17.8cm leaves ~4.2cm each, which still fits a full name and a
# designation; five would start hyphenating them.
MAX_BLOCKS_PER_ROW = 4

# At or below this many columns there is horizontal room for the department line.
DEPARTMENT_FITS_UP_TO = 3


def stamp_for(role_code, state, fallback=""):
    """
    The word for one block's stamp band.

    A signed block stamps its own certification; anything else states why it has
    none. `fallback` covers a role code this map has never heard of, so an
    unrecognised role prints its status rather than an empty band.
    """
    if state == "done":
        return STAMP_WORDS.get(role_code, fallback)
    return UNSIGNED_STAMPS.get(state, fallback)


def signature_rows(blocks, max_per_row=MAX_BLOCKS_PER_ROW,
                   department_fits_up_to=DEPARTMENT_FITS_UP_TO):
    """
    Group certification blocks into rows for a compact approval section.

    The count is never hardcoded: three signatories give one row of three, four give
    one row of four, and five or more wrap. Wrapping is BALANCED rather than greedy -
    five blocks render 3+2, where a greedy fill would give 4+1 and leave one block
    sitting beside three empty cells:

        n=3 -> [3]        n=6 -> [3, 3]      n=9  -> [3, 3, 3]
        n=4 -> [4]        n=7 -> [4, 3]      n=10 -> [4, 3, 3]
        n=5 -> [3, 2]     n=8 -> [4, 4]      n=11 -> [4, 4, 3]

    Each row carries its column count and the cell width that follows from it.
    Whether the department line is drawn is decided ONCE for the whole section, from
    the widest row, and repeated on every row - deciding it per row is the obvious
    reading of "department only if space permits" and it looks wrong: a seven-step
    chain wraps to 4+3 and the narrower second row would carry a line the first row
    lacks, so half the section gains an extra line for no reason a reader can see.
    """
    total = len(blocks)
    if not total:
        return []

    row_count = -(-total // max_per_row)           # ceil
    # Spread the remainder one row at a time. Chunking by a single ceil'd size is NOT
    # balanced - ten blocks come out 4+4+2, leaving a short final row.
    base, remainder = divmod(total, row_count)
    sizes = [base + (1 if i < remainder else 0) for i in range(row_count)]
    show_department = max(sizes) <= department_fits_up_to

    rows = []
    start = 0
    for size in sizes:
        chunk = blocks[start:start + size]
        start += size
        columns = len(chunk)
        rows.append({
            "blocks": chunk,
            "columns": columns,
            # Percentage rather than a fraction: the template writes it straight into
            # a width, and WeasyPrint needs the unit.
            "width": round(100 / columns, 4),
            "show_department": show_department,
        })
    return rows


def verification_id(*parts):
    """
    A Digital Verification ID for one signature.

    Derived, not stored: a hash over the identifiers that already pin the signature
    down (document, step, signer, instant). That keeps it stable across renders - the
    same signature always yields the same ID, so a code quoted from a printed copy
    still matches the one on screen months later - without adding a column that would
    need generating, backfilling and protecting from edits.

    Formatted in groups of four because its purpose is to be read off paper and typed
    back in.

    NOTE: this is a check digit, not a cryptographic signature. It proves the record
    has not been re-keyed under the same identifiers; it does not prove the document
    was not altered. Real non-repudiation needs a signing key.
    """
    payload = ":".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()
    return "-".join((digest[0:4], digest[4:8], digest[8:12]))


def stamp_iso(value):
    """A datetime rendered for hashing, or empty - never the string 'None'."""
    return value.isoformat() if value else ""


def initials(name):
    parts = [p for p in (name or "").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "—"
