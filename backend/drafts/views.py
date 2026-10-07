"""
Draft autosave API (Phase 111).

Owner-scoped throughout: a draft is one person's working state, so every query
in this module is filtered by `request.user` and there is no admin override. The
module's own permission rules continue to govern the real document; nothing here
can read or change one.
"""
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import DocumentDraft, DocumentDraftVersion
from .serializers import (
    DraftSerializer, DraftSummarySerializer, DraftVersionDetailSerializer,
    DraftWriteSerializer, RestoreSerializer,
)

VALID_KINDS = {choice.value for choice in DocumentDraft.Kind}


def _bad_kind(kind):
    return Response(
        {"detail": f"Unknown document kind '{kind}'."},
        status=status.HTTP_404_NOT_FOUND,
    )


# ---------------------------------------------------------------------------
# Per-kind guards (Phase TASK-AUTOSAVE-AND-SUBTASKS).
#
# Memo, Minute and Circular drafts are keyed by the user's own document, so the
# owner filter was the whole rule. A TASK draft is keyed by a task that may
# belong to somebody else, so "drafts follow task permissions": a key the caller
# may not edit is refused before the owner filter ever runs. Kept as a registry
# so this app stays generic and never imports a module at import time.
# ---------------------------------------------------------------------------
def _task_guard(user, document_key):
    from tasks import permissions as task_perms
    from tasks.models import Task

    if document_key == DocumentDraft.NEW:
        return task_perms.can_create_task(user)
    try:
        task = Task.objects.prefetch_related("assignees", "subtasks").get(pk=document_key)
    except (Task.DoesNotExist, ValueError, ValidationError):
        return False
    return task_perms.can_edit(user, task)


KIND_GUARDS = {DocumentDraft.Kind.TASK: _task_guard}


def _refused(kind, document_key, user):
    """A Response to return, or None when the caller may use this draft key."""
    if kind not in VALID_KINDS:
        return _bad_kind(kind)
    guard = KIND_GUARDS.get(kind)
    if guard is not None and not guard(user, document_key):
        return Response(
            {"detail": "You cannot keep a draft for this record."},
            status=status.HTTP_403_FORBIDDEN)
    return None


class DraftListView(APIView):
    """GET /api/v1/drafts/ - every unfinished document belonging to the caller."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        drafts = services.list_drafts(user=request.user)
        return Response(DraftSummarySerializer(drafts, many=True).data)


class DraftDetailView(APIView):
    """
    /api/v1/drafts/<kind>/<document_key>/

    GET     the snapshot plus its retained milestones
    PUT     upsert the snapshot (this is the autosave endpoint)
    DELETE  discard it
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, kind, document_key):
        refused = _refused(kind, document_key, request.user)
        if refused is not None:
            return refused
        draft = services.get_draft(
            user=request.user, kind=kind, document_key=document_key)
        if draft is None:
            # Not an error: "nothing to recover" is the normal case, and making
            # the client treat a 404 as success would hide real failures.
            return Response({"draft": None})
        return Response({"draft": DraftSerializer(draft).data})

    def put(self, request, kind, document_key):
        refused = _refused(kind, document_key, request.user)
        if refused is not None:
            return refused

        serializer = DraftWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            draft = services.save_snapshot(
                user=request.user,
                kind=kind,
                document_key=document_key,
                payload=data["payload"],
                milestone=data.get("milestone") or None,
                device_label=data.get("device_label", ""),
            )
        except services.DraftTooLarge as exc:
            # 413 rather than 400: the client's retry logic must not treat this
            # as a transient failure and loop, and the local copy is still safe.
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        # Deliberately narrow: autosave runs every few seconds, so echoing the
        # whole payload back would double the bandwidth of the feature for no
        # reader. The client already has the content it just sent.
        return Response({
            "id": str(draft.id),
            "version": draft.version,
            "saved_at": draft.saved_at,
        })

    def delete(self, request, kind, document_key):
        refused = _refused(kind, document_key, request.user)
        if refused is not None:
            return refused
        draft = services.get_draft(
            user=request.user, kind=kind, document_key=document_key)
        if draft is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        # `submitted` distinguishes "this became a real document" from "the user
        # threw it away" in the audit trail. Both delete the snapshot.
        reason = ("draft_submitted" if request.query_params.get("submitted")
                  else "draft_discarded")
        services.discard(user=request.user, draft=draft, reason=reason)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DraftVersionView(APIView):
    """GET one retained milestone in full, so it can be previewed before restoring."""
    permission_classes = [IsAuthenticated]

    def get(self, request, kind, document_key, version):
        refused = _refused(kind, document_key, request.user)
        if refused is not None:
            return refused
        draft = services.get_draft(
            user=request.user, kind=kind, document_key=document_key)
        if draft is None:
            return Response({"detail": "No draft."}, status=status.HTTP_404_NOT_FOUND)
        row = draft.versions.filter(version=version).first()
        if row is None:
            return Response({"detail": "No such version."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(DraftVersionDetailSerializer(row).data)


class DraftRestoreView(APIView):
    """POST a version number to make that milestone the live snapshot."""
    permission_classes = [IsAuthenticated]

    def post(self, request, kind, document_key):
        refused = _refused(kind, document_key, request.user)
        if refused is not None:
            return refused
        draft = services.get_draft(
            user=request.user, kind=kind, document_key=document_key)
        if draft is None:
            return Response({"detail": "No draft."}, status=status.HTTP_404_NOT_FOUND)

        serializer = RestoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        restored = services.restore_version(
            user=request.user, draft=draft,
            version_number=serializer.validated_data["version"])
        if restored is None:
            return Response({"detail": "No such version."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"draft": DraftSerializer(restored).data})


class DraftRecoveredView(APIView):
    """
    POST to record that the user accepted a recovery offer.

    Its own endpoint rather than a flag on the GET, because reading a draft
    happens on every form mount and only a minority of those become a recovery.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, kind, document_key):
        refused = _refused(kind, document_key, request.user)
        if refused is not None:
            return refused
        draft = services.get_draft(
            user=request.user, kind=kind, document_key=document_key)
        if draft is None:
            return Response({"detail": "No draft."}, status=status.HTTP_404_NOT_FOUND)
        services.mark_recovered(user=request.user, draft=draft)
        return Response({"ok": True})
