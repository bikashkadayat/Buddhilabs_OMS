"""
Shared primitives for the memo module: memo numbering, the audit helper, and the
history-row writer.

WHAT USED TO BE HERE
--------------------
The original two-slot workflow engine (submit -> review -> approve, routed
through Memo.current_reviewer and Memo.current_approver) lived in this module.
It has been removed: memos.workflow is now the single workflow engine, and the
two routing columns no longer exist. Migration 0009 converted every legacy-routed
memo into MemoWorkflowStep rows before 0010 dropped the columns, so no live memo
was stranded by the change.

The functions that remain are the ones the matrix engine and the API layer both
depend on, and none of them are workflow-stage-specific.

Design rules the engine in workflow.py inherits from here:
  * Each mutating transition runs inside a single @transaction.atomic block.
  * The memo row is re-read with select_for_update() so two concurrent actors
    cannot both act on the same memo and corrupt its state.
  * A guard failure raises rest_framework ValidationError with a clear message.
  * State is never mutated without also writing a MemoApprovalStep and an
    AuditLog entry - the two together give a complete, immutable history.
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.services import log_action
from .models import Memo, MemoApprovalStep
from config.uploads import DOCUMENT_ATTACHMENT_EXTENSIONS

logger = logging.getLogger("memos")
User = get_user_model()

MIN_COMMENT_LENGTH = 10

# "Choose file: Accept only PDF,doc,docx,xls,xlsx,csv" and "Single file size greater
# than 2MB will not be attached" (E-memo-manual p.6) — superseded, see
# MAX_MEMO_ATTACHMENT_SIZE below.
#
# Passed EXPLICITLY into the shared validator rather than narrowing its defaults, so
# tightening memos does not also change what minutes, leave or attendance accept.
# The shared document-module policy (Phase ATTACHMENT-POLICY-FINAL).
#
# This list was previously the six types named by the E-memo manual (p.6), and
# `test_an_image_is_refused` was written to hold it there. That restriction has
# been lifted by explicit instruction: images are now accepted on memos as they
# already were on minutes. THE MANUAL AND THE CODE NOW DISAGREE — p.6 still says
# "accept only PDF, doc, docx, xls, xlsx, csv" and should be updated, or the
# next person to read it will file this as a bug.
MEMO_ATTACHMENT_EXTENSIONS = DOCUMENT_ATTACHMENT_EXTENSIONS | {
    # Phase MEMO-ATTACHMENT-ENTERPRISE. Memos carry procurement packs, so slide
    # decks and bundles were asked for explicitly.
    "ppt", "pptx",
    # ZIP IS A DELIBERATE EXCEPTION, requested by name. Every other type here is
    # verified by its magic bytes; an archive hides its contents from that check
    # entirely, so what is allowed is the container, not what is inside it. The
    # shared default excludes it for that reason and tasks opt in separately.
    # Worth revisiting if memo attachments are ever auto-extracted or scanned.
    "zip",
}
# 10 MB, the project default, raised from the manual's 2 MB (Phase
# MEMO-ATTACHMENT-SIZE-UPGRADE).
#
# Memo was the only module below the shared default — minutes allow 25MB for
# board packs, tasks 15MB for evidence — while being the module that carries
# procurement quotations, contracts and subscription proposals. A 300 dpi
# scanned quotation runs 2-10MB and an unedited phone photo 3-6MB, so the two
# file types added last phase, pptx and zip, were effectively unusable at 2MB.
#
# THE EXTERNAL E-MEMO MANUAL (p.6) STILL SAYS 2MB and needs correcting; the
# repo's own user guide already said 10 MB, so the code and the shipped guide
# had been contradicting each other.
MAX_MEMO_ATTACHMENT_SIZE = 10 * 1024 * 1024


# BATCH LIMITS (Phase MEMO-P1-PRODUCTION-BLOCKERS).
#
# Until now the per-file 10MB cap was the ONLY bound on an upload: nothing
# limited how many files a request carried or how many a memo accumulated, so
# ten files at the limit made a 94MB request that was accepted and stored.
#
# The numbers are set from what a procurement pack actually looks like -- a
# quotation, a comparison sheet, a contract draft and a few scans -- with room
# to spare, rather than from what the server can survive:
#   * 10 per request keeps one submission to a single reasonable batch;
#   * 20 per memo allows a second and third round of documents on a memo that
#     is still being negotiated, without letting one memo become a file share;
#   * 50MB total is five full-size scans, and refuses the 94MB case.
MAX_MEMO_ATTACHMENTS_PER_REQUEST = 10
MAX_MEMO_ATTACHMENTS = 20
MAX_MEMO_UPLOAD_TOTAL_SIZE = 50 * 1024 * 1024


def validate_memo_attachment_batch(files, *, existing_count=0):
    """Validate a whole memo upload request: count, running total, total bytes."""
    from config.uploads import validate_attachment_batch
    return validate_attachment_batch(
        files,
        max_count=MAX_MEMO_ATTACHMENTS_PER_REQUEST,
        max_total_size=MAX_MEMO_UPLOAD_TOTAL_SIZE,
        existing_count=existing_count,
        max_per_parent=MAX_MEMO_ATTACHMENTS,
    )


def validate_memo_attachment(uploaded):
    """
    Validate one uploaded file against the manual's rules.

    Delegates to the project validator, which checks size, extension AND the leading
    bytes - so a .docx that is really an HTML file is refused rather than stored and
    served back later.
    """
    from config.uploads import validate_attachment
    return validate_attachment(
        uploaded,
        max_size=MAX_MEMO_ATTACHMENT_SIZE,
        extensions=MEMO_ATTACHMENT_EXTENSIONS,
    )

# Short codes embedded in generated memo numbers, per memo type. The vocabulary is
# the manual's three (p.4); the older business categories it replaced (INT/EXT/FIN/HR)
# still appear inside memo numbers already issued, which is history and stays readable.
MEMO_TYPE_CODES = {
    Memo.MemoType.GENERAL: "GEN",
    Memo.MemoType.CONFIDENTIAL: "CON",
    Memo.MemoType.DRAFT: "DRF",
}


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------
def create_audit_log(actor, action, instance=None, metadata=None, request=None):
    """
    Central audit helper for the memo module.

    Delegates to audit.services.log_action, which records the actor, the action,
    a generic reference to the object, the JSON metadata passed here, the client
    IP (resolved through our own proxies only) and the user agent. Passing
    `request` is what makes the IP and user-agent columns meaningful, so every
    caller in this app threads it through.
    """
    # A DRAFT memo is explicitly excluded from the log (p.4). Applied to the global
    # trail as well as the per-memo history, because "no log record is kept" is not
    # satisfied by keeping it somewhere else.
    if isinstance(instance, Memo) and not instance.keeps_log:
        logger.info("audit skipped (draft memo) action=%s target=%s", action, instance)
        return
    logger.info("audit action=%s actor=%s target=%s", action, getattr(actor, "id", None), instance)
    log_action(actor, action, instance=instance, changes=metadata or {}, request=request)


# ---------------------------------------------------------------------------
# History rows
# ---------------------------------------------------------------------------
def _next_step_order(memo):
    last = memo.approval_steps.order_by("-step_order").first()
    return (last.step_order + 1) if last else 1


def _record_step(memo, actor, action, comment=""):
    """
    Append one immutable history row. Never updates an existing one.

    A DRAFT memo keeps no history: "This is only temporary and log record is not
    kept for this one" (E-memo-manual p.4). The guard lives here rather than at
    each of the dozen call sites, so no future transition can forget it - and it
    reads `memo.keeps_log` rather than testing the type, so the rule has one home.
    """
    if not memo.keeps_log:
        return None
    return MemoApprovalStep.objects.create(
        memo=memo,
        step_order=_next_step_order(memo),
        actor=actor,
        action=action,
        comment=comment or "",
    )


def _require_comment(comment):
    comment = (comment or "").strip()
    if len(comment) < MIN_COMMENT_LENGTH:
        raise ValidationError(
            f"A comment of at least {MIN_COMMENT_LENGTH} characters is required."
        )
    return comment


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------
@transaction.atomic
def generate_memo_number(memo_type):
    """
    Return the next memo number in the form NIFN-{TYPE_CODE}-{YYYY}-{XXXX}
    (e.g. NIFN-HR-2026-0042). The sequence resets each calendar year and is
    scoped per type code.

    Numbers are handed out by an authoritative per-(type, year) counter row
    (MemoNumberSequence). We ensure the row exists, then lock it with
    select_for_update() and increment - so concurrent creations serialize on
    the lock instead of racing on a "max existing number" read (H4). The
    integer counter also removes the previous lexical-sort bug beyond 9999.
    """
    from .models import MemoNumberSequence

    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    type_code = MEMO_TYPE_CODES.get(memo_type, "GEN")
    year = timezone.now().year

    # Phase S2: per (organization, type_code, year). The row used to be shared
    # across tenants, so one company filing a memo advanced every other
    # company's memo numbers.
    #
    # get_or_create is safe under the unique constraint if two creators race to
    # create the first row of the year; the loser retries the get.
    MemoNumberSequence.objects.get_or_create(
        organization=organization, type_code=type_code, year=year)
    seq = MemoNumberSequence.objects.select_for_update().get(
        organization=organization, type_code=type_code, year=year
    )
    seq.last_value += 1
    seq.save(update_fields=["last_value"])
    return numbering.format_number("MEMO", year=year, value=seq.last_value,
                                   organization=organization,
                                   type_code=type_code)
