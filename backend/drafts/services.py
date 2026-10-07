"""
Draft service layer (Phase 111).

Thin views, fat services — the convention the rest of this codebase follows.
Everything that decides *what happens* to a draft lives here so the HTTP layer
stays a translation of request to call.
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action

from .models import DocumentDraft, DocumentDraftVersion

logger = logging.getLogger("drafts")

#: Hard ceiling on a single snapshot. A memo with a large pasted table is well
#: under this; anything above it is a bug or an attack, and either way must not
#: become an unbounded row. 512 KB of JSON is roughly 80 pages of rich text.
MAX_PAYLOAD_BYTES = getattr(settings, "DRAFT_MAX_PAYLOAD_BYTES", 512 * 1024)

#: How long a draft survives without being touched.
RETENTION_DAYS = getattr(settings, "DRAFT_RETENTION_DAYS", 90)


class DraftTooLarge(Exception):
    """Raised when a snapshot exceeds MAX_PAYLOAD_BYTES."""


def payload_size(payload):
    """Serialised size in bytes, which is what the row actually costs."""
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _log(user, event, draft, **extra):
    """
    Record a draft lifecycle event on the shared audit trail.

    Deliberately NOT called for autosave. MemoViewSet.update writes an AuditLog
    row *and* a user-visible MemoApprovalStep for every edit; a five-second
    autosave cadence routed through anything similar would put hundreds of
    entries on one document's timeline. Autosave frequency is a counter on the
    draft row instead (DocumentDraft.autosave_count), which answers the same
    question without the noise.
    """
    # Phase S5: routed through audit.services.log_action rather than writing
    # AuditLog directly.
    #
    # This was the ONE caller that bypassed the sanctioned writer, which the
    # AuditLog docstring already claimed was the only one. That mattered once
    # the rows carried an organization: draft lifecycle events would have been
    # the single kind of audit row with no owner, and no amount of care in
    # log_action would have covered them.
    #
    # `instance=draft` is what supplies the tenant: DocumentDraft is a Phase C
    # model owned through its `owner`, so the subject resolves it without
    # needing a request.
    log_action(
        user,
        AuditLog.Action.OTHER,
        instance=draft,
        changes={
            "event": event,
            "kind": draft.kind,
            "document_key": draft.document_key,
            "version": draft.version,
            **extra,
        },
    )


@transaction.atomic
def save_snapshot(*, user, kind, document_key, payload, milestone=None,
                  device_label=""):
    """
    Upsert the rolling snapshot, optionally retaining a milestone version.

    `milestone` is a DocumentDraftVersion.Reason or None. None is the ordinary
    idle/interval autosave: it overwrites and keeps no history.
    """
    size = payload_size(payload)
    if size > MAX_PAYLOAD_BYTES:
        raise DraftTooLarge(
            f"Draft is {size} bytes; the limit is {MAX_PAYLOAD_BYTES}."
        )

    draft, created = DocumentDraft.objects.select_for_update().get_or_create(
        kind=kind, document_key=document_key, owner=user,
        defaults={"payload": payload, "version": 1, "autosave_count": 1,
                  "device_label": device_label[:120]},
    )

    if created:
        _log(user, "draft_created", draft, bytes=size)
    else:
        draft.payload = payload
        draft.version += 1
        draft.autosave_count += 1
        if device_label:
            draft.device_label = device_label[:120]
        draft.save(update_fields=["payload", "version", "autosave_count",
                                  "device_label", "saved_at"])

    if milestone:
        _retain_version(draft, user, milestone)

    return draft


def _retain_version(draft, user, reason):
    """Write a milestone and trim to the newest DocumentDraftVersion.KEEP."""
    DocumentDraftVersion.objects.create(
        draft=draft, version=draft.version, payload=draft.payload,
        reason=reason, saved_by=user,
    )
    # Trim by primary key rather than slicing a delete(): Django cannot DELETE
    # from a sliced queryset, and the ids are already ordered by the model Meta.
    keep_ids = list(
        draft.versions.values_list("id", flat=True)[:DocumentDraftVersion.KEEP]
    )
    draft.versions.exclude(id__in=keep_ids).delete()


def get_draft(*, user, kind, document_key):
    """The user's snapshot for this document, or None."""
    return (DocumentDraft.objects
            .filter(kind=kind, document_key=document_key, owner=user)
            .prefetch_related("versions")
            .first())


def list_drafts(*, user):
    """Every unfinished document this user has, newest first."""
    return DocumentDraft.objects.filter(owner=user).order_by("-saved_at")


@transaction.atomic
def discard(*, user, draft, reason="draft_discarded"):
    """
    Delete a draft. Used when the user discards, and when the real document is
    successfully saved or submitted so the snapshot cannot outlive its purpose
    and offer to "restore" content that is already committed.
    """
    _log(user, reason, draft)
    draft.delete()


@transaction.atomic
def restore_version(*, user, draft, version_number):
    """
    Make a retained milestone the live snapshot.

    The snapshot being replaced is itself retained first, so restoring is not
    destructive — a user who restores the wrong version can get back to where
    they were.
    """
    target = draft.versions.filter(version=version_number).first()
    if target is None:
        return None

    _retain_version(draft, user, DocumentDraftVersion.Reason.BEFORE_RESTORE)

    draft.payload = target.payload
    draft.version += 1
    draft.save(update_fields=["payload", "version", "saved_at"])
    _log(user, "draft_restored", draft, restored_from=version_number)
    return draft


def mark_recovered(*, user, draft):
    """The user accepted a recovery offer and is now editing the restored copy."""
    _log(user, "draft_recovered", draft)


def mark_submitted(*, user, draft):
    """The draft became a real document. Logged, then discarded by the caller."""
    _log(user, "draft_submitted", draft)



def purge_expired(*, days=None, now=None):
    """
    Delete drafts untouched for `days`. Returns the number removed.

    Retention runs off `saved_at`, not `created_at`: a document someone returns
    to every week is live work regardless of when they started it.
    """
    days = RETENTION_DAYS if days is None else days
    cutoff = (now or timezone.now()) - timezone.timedelta(days=days)
    stale = DocumentDraft.objects.filter(saved_at__lt=cutoff)
    removed = stale.count()
    stale.delete()  # cascades to versions
    return removed


def remind_unfinished_task_drafts(*, idle_hours=1, now=None):
    """
    Phase TASK-AUTOSAVE-AND-SUBTASKS: tell the owner of a task draft that has
    sat untouched for `idle_hours` that it is still there to recover.

    In-app only, once per draft VERSION: the notification idempotency key
    carries the version, so a later autosave (which bumps it) may remind again
    after another idle hour, and a draft nobody touches is mentioned once.
    """
    from django.utils import timezone as _tz

    from notifications.dispatcher import notify
    from notifications.models import Category

    now = now or _tz.now()
    cutoff = now - timedelta(hours=idle_hours)
    sent = 0
    rows = DocumentDraft.objects.filter(
        kind=DocumentDraft.Kind.TASK, saved_at__lt=cutoff).select_related("owner")
    for draft in rows:
        payload = draft.payload if isinstance(draft.payload, dict) else {}
        title = (payload.get("title") or "").strip()[:120] or "Untitled task"
        if draft.is_unsaved_document:
            url = "/tasks/create"
        else:
            url = f"/tasks/{draft.document_key}/edit"
        try:
            created = notify(
                draft.owner, Category.TASK_DRAFT_RECOVERABLE,
                "You have an unsaved task draft",
                f"\"{title}\" was last autosaved {draft.saved_at:%Y-%m-%d %H:%M}. "
                "Open it to continue or discard it.",
                action_url=url,
                idempotency_key=f"draft-recover-{draft.pk}-{draft.version}",
                object_id=str(draft.pk))
        except Exception:  # pragma: no cover - the bell is a convenience
            logger.exception("Draft reminder failed for %s", draft.pk)
            continue
        if created:
            sent += 1
    return sent
