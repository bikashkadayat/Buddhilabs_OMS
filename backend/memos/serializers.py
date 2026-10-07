from django.contrib.auth import get_user_model
from rest_framework import serializers

from leaves.models import Department

from config.uploads import (
    ALLOWED_ATTACHMENT_EXTENSIONS,
    ALLOWED_ATTACHMENT_MIMES,
    MAX_ATTACHMENT_SIZE,
    validate_attachment,
)

from .models import (
    Memo,
    MemoApprovalStep,
    MemoArchiveAccess,
    MemoAssignmentTransfer,
    MemoAttachment,
    MemoSection,
    MemoNoteRecipient,
    MemoStepUnavailability,
    MemoTemplate,
    MemoWorkflowStep,
)
from .sanitizers import sanitize_memo_html

User = get_user_model()

# Re-exported from config.uploads (Phase 11): these constants were defined here
# and used only here, which is how the correction attachments in Phase 9 ended
# up with no validation at all. The names stay importable so existing tests and
# any external reference keep working.
__all__ = ["ALLOWED_ATTACHMENT_EXTENSIONS", "ALLOWED_ATTACHMENT_MIMES",
           "MAX_ATTACHMENT_SIZE"]


class UserMiniSerializer(serializers.ModelSerializer):
    """
    Lightweight user representation embedded inside memo payloads so clients
    do not have to make a second round trip to resolve names/roles.
    """
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "role", "department", "employee_id"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class MemoEmployeeSerializer(serializers.ModelSerializer):
    """
    Employee shape for the Phase 4 approval-matrix picker.

    Phase 4 requires that ANY employee can be selected for ANY role type, so
    this directory is deliberately not filtered by role. It
    still excludes email (no PII / roster harvesting) and the endpoint behind it
    stays search-gated, capped and throttled.
    """
    full_name = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "full_name", "employee_id", "designation",
            "department", "role", "role_display",
        ]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_department(self, obj):
        return obj.department_name or ""


class MemoWorkflowStepSerializer(serializers.ModelSerializer):
    """
    One row of the approval matrix table (Phase 4): Sequence | Employee |
    Designation | Department | Role Type | Status | Action Date | Remarks.
    """
    assignee = UserMiniSerializer(read_only=True)
    role_type_display = serializers.CharField(source="get_role_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    # Falls back to the snapshot when the account has been deleted, so a historical
    # matrix never renders a blank row.
    display_name = serializers.CharField(read_only=True)
    acted_at_bs = serializers.SerializerMethodField()

    class Meta:
        model = MemoWorkflowStep
        fields = [
            "id", "sequence", "assignee", "role_type", "role_type_display",
            "status", "status_display", "assignee_name", "display_name",
            "designation", "department_label",
            "activated_at", "acted_at", "acted_at_bs", "remarks",
        ]
        read_only_fields = fields

    def get_acted_at_bs(self, obj):
        if not obj.acted_at:
            return None
        from config.nepali_dates import to_bs
        return to_bs(obj.acted_at)


class MemoWorkflowStepInputSerializer(serializers.Serializer):
    """One proposed matrix row. Ordering is the position in the submitted list."""
    assignee_id = serializers.UUIDField()
    role_type = serializers.ChoiceField(choices=MemoWorkflowStep.RoleType.choices)


class MemoMatrixInputSerializer(serializers.Serializer):
    """
    The full proposed matrix. Sequence is implied by list order, which is what
    lets the UI reorder rows by drag without renumbering anything client-side.
    """
    steps = MemoWorkflowStepInputSerializer(many=True)

    def validate_steps(self, value):
        if not value:
            raise serializers.ValidationError(
                "Add at least one approver to the workflow."
            )
        return value


class MemoWorkflowActionSerializer(serializers.Serializer):
    """Body of the workflow action endpoint."""
    decision = serializers.ChoiceField(choices=["proceed", "reject"])
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Phase 49.5 - governance request shapes
#
# Each one exists so the view stays a thin router: validation of shape lives here,
# validation of RULES lives in memos.governance, and neither is duplicated. Every
# reason field is required and non-blank because it is the only record of why a
# governance change was made.
# ---------------------------------------------------------------------------
class MemoNoteRequestSerializer(serializers.Serializer):
    """Ask one or more people to note a memo."""
    user_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=50)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class MemoNoteSerializer(serializers.Serializer):
    """Record the caller's own note. Remarks optional - noting is the statement."""
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class MemoUnavailabilitySerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=MemoStepUnavailability.Reason.choices)
    reason_note = serializers.CharField(required=False, allow_blank=True, default="")
    # Defaults to the active step. Naming a pending step is allowed so an absence
    # can be declared before the memo arrives at it.
    step_id = serializers.UUIDField(required=False, allow_null=True)


class MemoReplacementSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    reason = serializers.CharField()
    kind = serializers.ChoiceField(
        choices=[MemoAssignmentTransfer.Kind.ALTERNATE,
                 MemoAssignmentTransfer.Kind.ACTING],
        required=False, allow_null=True)
    step_id = serializers.UUIDField(required=False, allow_null=True)


class MemoReassignSerializer(serializers.Serializer):
    # Not required: a self-assign has no other party to name.
    user_id = serializers.UUIDField(required=False, allow_null=True)
    reason = serializers.CharField()
    kind = serializers.ChoiceField(
        choices=[MemoAssignmentTransfer.Kind.SELF_ASSIGN,
                 MemoAssignmentTransfer.Kind.ASSIGN_NEW,
                 MemoAssignmentTransfer.Kind.TRANSFER_OWNERSHIP])
    step_id = serializers.UUIDField(required=False, allow_null=True)


class MemoArchiveGrantSerializer(serializers.Serializer):
    target = serializers.ChoiceField(choices=MemoArchiveAccess.Target.choices)
    reason = serializers.CharField()
    department_id = serializers.UUIDField(required=False, allow_null=True)
    user_id = serializers.UUIDField(required=False, allow_null=True)
    group_id = serializers.IntegerField(required=False, allow_null=True)
    include_children = serializers.BooleanField(required=False, default=True)


class MemoSectionSerializer(serializers.ModelSerializer):
    """
    One titled content block - the manual's Background, Recommendation, and
    anything "+ Add more" produced (p.3).
    """
    class Meta:
        model = MemoSection
        fields = ["id", "position", "title", "body"]
        read_only_fields = ["id"]

    def validate_body(self, value):
        # Authored by any authenticated user and later rendered to approvers and
        # into the PDF, so it is untrusted input (stored-XSS surface).
        return sanitize_memo_html(value or "")


class MemoSectionListWriteSerializer(serializers.Serializer):
    """
    The whole ordered list, replaced in one call.

    Whole-list rather than per-row PATCHes because the manual's control adds and
    removes blocks freely and their ORDER is the document's order; sending the
    list is the only shape where the client cannot leave the positions
    inconsistent.
    """
    sections = MemoSectionSerializer(many=True)


class MemoAttachmentSerializer(serializers.ModelSerializer):
    """Read shape for a memo attachment; the URL is short-lived and user-bound."""
    url = serializers.SerializerMethodField()
    uploaded_by = UserMiniSerializer(read_only=True)
    # What the reader is shown: the name the uploader typed into the manual's
    # "File Name" box, falling back to the file's own name.
    label = serializers.CharField(read_only=True)

    # A second signed URL WITHOUT the download disposition, so the reader can
    # look at a quotation without saving it first. Same signature, same short
    # TTL, same user binding — the only difference is Content-Disposition, and
    # a reviewer deciding on a document should not have to litter their
    # Downloads folder to read it.
    preview_url = serializers.SerializerMethodField()

    class Meta:
        model = MemoAttachment
        fields = ["id", "original_name", "display_name", "label", "size",
                  "uploaded_by", "uploaded_at", "url", "preview_url"]
        read_only_fields = fields

    def get_preview_url(self, obj):
        from documents.protected_media import signed_media_url
        viewer = self._viewer() if hasattr(self, "_viewer") else getattr(
            self.context.get("request"), "user", None)
        if not getattr(viewer, "is_authenticated", False):
            viewer = None
        return signed_media_url(obj.file.name, ttl=300, download=False, user=viewer)

    def get_url(self, obj):
        from documents.protected_media import signed_media_url
        viewer = getattr(self.context.get("request"), "user", None)
        return signed_media_url(obj.file.name, ttl=300, download=True, user=viewer)


class MemoSignatureBlockSerializer(serializers.Serializer):
    """
    One approval certification block: Created By / Recommended By / Supported By /
    Approved By, with the name, designation, department, action date and status.

    Built server-side by workflow.signature_blocks() so the detail page and the
    PDF render the SAME data - they used to derive the approval section
    separately, which is how the PDF ended up with no "Created By" card.

    `stamp` is the certification word for the block's stamp band and is NOT
    interchangeable with `status_label`: a completed recommender step has
    status_label "Approved" (correct in a status column) but stamp "Recommended"
    (correct in a stamp). `initials` survives from the withdrawn avatar circles -
    still served so nothing that reads the payload breaks, no longer rendered.
    """
    key = serializers.CharField()
    heading = serializers.CharField()
    name = serializers.CharField()
    designation = serializers.CharField()
    department = serializers.CharField()
    at = serializers.DateTimeField(allow_null=True)
    status_label = serializers.CharField()
    stamp = serializers.CharField()
    state = serializers.CharField()
    remarks = serializers.CharField(allow_blank=True)
    initials = serializers.CharField()
    verified = serializers.BooleanField()
    verification_id = serializers.CharField(allow_blank=True)


class MemoApprovalCertificateSerializer(serializers.Serializer):
    """
    The Phase 26 "approved, by whom, when" summary that drives the seal on the
    detail page and in the PDF. Null for anything not fully approved.
    """
    stamp = serializers.CharField()
    approved_at = serializers.DateTimeField(allow_null=True)
    approved_by = serializers.CharField()
    designation = serializers.CharField()
    department = serializers.CharField()
    verification_id = serializers.CharField(allow_blank=True)
    archived_at = serializers.DateTimeField(allow_null=True)


class MemoTrackerStageSerializer(serializers.Serializer):
    """One milestone of the Phase 18 workflow tracker."""
    key = serializers.CharField()
    label = serializers.CharField()
    state = serializers.CharField()
    at = serializers.DateTimeField(allow_null=True)
    actor = serializers.CharField(allow_blank=True)


class MemoTimelineEntrySerializer(serializers.Serializer):
    """
    Phase 7 timeline entry. `kind` is "history" for something that happened and
    "upcoming" for a step still outstanding, so the UI can render completed
    events and the waiting-on chain in a single ordered list.
    """
    kind = serializers.CharField()
    id = serializers.CharField()
    sequence = serializers.IntegerField()
    action = serializers.CharField()
    label = serializers.CharField()
    actor_name = serializers.CharField()
    actor_id = serializers.CharField(allow_null=True)
    designation = serializers.CharField(allow_blank=True)
    at = serializers.DateTimeField(allow_null=True)
    remarks = serializers.CharField(allow_blank=True)
    state = serializers.CharField()


def memo_status_label(obj):
    """
    Human label for a memo status.

    This used to special-case `approved` as "Published", left over from an earlier
    spec. It is now simply the model's own display name, so the label a user sees
    is identical everywhere it appears - list, detail, badge, dashboard, export,
    PDF and notification - and there is exactly one place it is defined.
    """
    return obj.get_status_display()


def _pending_with(obj):
    """Who the memo is currently sitting with, for the list views."""
    step = obj.active_step
    if step is None:
        return None
    return {
        "id": str(step.assignee_id) if step.assignee_id else None,
        "name": step.display_name,
        "designation": step.designation or "",
        "role_type": step.role_type,
        "role_label": step.get_role_type_display(),
        "sequence": step.sequence,
    }


# The verb on the button for each role, so the work queue never has to guess.
_STEP_ACTION_LABELS = {
    MemoWorkflowStep.RoleType.REVIEWER: "Review",
    MemoWorkflowStep.RoleType.RECOMMENDER: "Recommend",
    MemoWorkflowStep.RoleType.SUPPORTER: "Support",
    MemoWorkflowStep.RoleType.APPROVER: "Approve",
}


def _my_step(obj, request):
    """
    The viewer's own ACTIVE step on this memo, and what acting on it demands
    (Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE).

    The work queue offered a one-tap "Approve" on every memo because the list row
    told it nothing about the step: it sent `decision: "approve"` - not a value the
    endpoint accepts - with no remarks, on a Reviewer step that requires a comment
    of at least MIN_COMMENT_LENGTH characters. Two stacked 400s, the first hiding
    the second.

    So the row now states what the server will enforce: the role waiting on the
    viewer, the verb for it, whether a comment is required and how long it must be
    - read from the same constants `workflow.act_on_step` checks, so the client is
    never told a different rule from the one it is held to.

    Null when the memo is not waiting on the viewer. Uses the prefetched steps
    (`active_step`), not a query, for the same reason `approved_by` does.
    """
    from .services import MIN_COMMENT_LENGTH

    user = getattr(request, "user", None)
    step = obj.active_step
    if step is None or user is None or step.assignee_id != getattr(user, "id", None):
        return None

    # Where this step sits in the chain, and what each decision leads to (Phase
    # MEMO-QUEUE-UX-HARDENING). Skipped steps are not part of the chain any more,
    # so they are neither counted nor offered as "next". All of it from the
    # prefetched steps - `workflow_steps.all()` - so a page of rows costs nothing.
    chain = sorted(
        (s for s in obj.workflow_steps.all()
         if s.status != MemoWorkflowStep.StepStatus.SKIPPED),
        key=lambda s: s.sequence)
    position = next((i for i, s in enumerate(chain) if s.id == step.id), 0) + 1
    following = next(
        (s for s in chain if s.sequence > step.sequence
         and s.status == MemoWorkflowStep.StepStatus.PENDING), None)
    author = obj.created_by

    return {
        "id": str(step.id),
        "role_type": step.role_type,
        "role_label": step.get_role_type_display(),
        "action_label": _STEP_ACTION_LABELS.get(step.role_type, "Proceed"),
        "decisions": ["proceed", "reject"],
        "requires_comment": step.requires_comment,
        "min_comment_length": MIN_COMMENT_LENGTH,
        "sequence": step.sequence,
        "position": position,
        "total_steps": len(chain),
        # What "proceed" leads to: the next person, or - when this is the last
        # step - the memo being approved and filed (act_on_step archives it in
        # the same transaction).
        "next_step": ({
            "role_type": following.role_type,
            "role_label": following.get_role_type_display(),
            "assignee_name": following.display_name,
        } if following else None),
        "is_final_step": following is None,
        # What "reject" leads to: back to the author to revise (workflow._reject).
        "author_name": (author.get_full_name() or author.username) if author else "",
    }


class MemoApprovalStepSerializer(serializers.ModelSerializer):
    """Read-only history entry for one action in the memo workflow."""
    actor = UserMiniSerializer(read_only=True)
    acted_at_bs = serializers.SerializerMethodField()

    class Meta:
        model = MemoApprovalStep
        fields = ["id", "step_order", "actor", "action", "comment", "acted_at", "acted_at_bs"]
        read_only_fields = fields

    def get_acted_at_bs(self, obj):
        from config.nepali_dates import to_bs
        return to_bs(obj.acted_at)


class MemoListSerializer(serializers.ModelSerializer):
    """Slim serializer for list endpoints - deliberately omits body/attachment."""
    created_by = UserMiniSerializer(read_only=True)
    status_label = serializers.SerializerMethodField()
    department_label = serializers.SerializerMethodField()
    pending_with = serializers.SerializerMethodField()
    my_step = serializers.SerializerMethodField()
    ageing = serializers.SerializerMethodField()
    memo_type_label = serializers.CharField(
        source="get_memo_type_display", read_only=True)
    approved_by = serializers.SerializerMethodField()

    class Meta:
        model = Memo
        fields = [
            "id", "memo_number", "reference_number", "subject", "to_line",
            "memo_type", "memo_type_label",
            "status", "status_label",
            "created_by", "department_label", "pending_with", "my_step", "ageing",
            "created_at", "submitted_at", "approved_at", "archived_at",
            "approved_by",
        ]
        read_only_fields = fields

    def get_approved_by(self, obj):
        """
        Who signed the memo off, for the archive and approved lists (Phase 26).

        Null for anything not fully approved. Read from the prefetched workflow
        steps in Python - `workflow_steps__assignee` is on the list queryset, and a
        `.filter()` here would bypass that prefetch and fire a query per row.
        """
        from .workflow import approval_certificate
        certificate = approval_certificate(obj)
        return certificate["approved_by"] if certificate else None

    def get_status_label(self, obj):
        return memo_status_label(obj)

    def get_department_label(self, obj):
        return obj.resolved_department_name()

    def get_pending_with(self, obj):
        return _pending_with(obj)

    def get_my_step(self, obj):
        return _my_step(obj, self.context.get("request"))

    def get_ageing(self, obj):
        """
        How long the memo has been waiting on its current actor, and whether that
        is inside its SLA. Computed server-side so the inbox's colour
        coding and its numbers can never disagree.
        """
        from .workflow import step_ageing
        return step_ageing(obj, obj.active_step)


class MemoDetailSerializer(serializers.ModelSerializer):
    """Full memo payload including workflow history and per-user capabilities."""
    created_by = UserMiniSerializer(read_only=True)
    approval_steps = MemoApprovalStepSerializer(many=True, read_only=True)
    workflow_steps = MemoWorkflowStepSerializer(many=True, read_only=True)
    attachments = MemoAttachmentSerializer(many=True, read_only=True)
    sections = MemoSectionSerializer(many=True, read_only=True)
    keeps_log = serializers.BooleanField(read_only=True)
    cc_department_names = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    department_label = serializers.SerializerMethodField()
    created_at_bs = serializers.SerializerMethodField()
    pending_with = serializers.SerializerMethodField()
    my_step = serializers.SerializerMethodField()
    is_read_only = serializers.BooleanField(read_only=True)

    ageing = serializers.SerializerMethodField()
    memo_type_label = serializers.CharField(
        source="get_memo_type_display", read_only=True)
    is_restricted = serializers.BooleanField(read_only=True)
    signatures = serializers.SerializerMethodField()
    tracker = serializers.SerializerMethodField()
    approval_certificate = serializers.SerializerMethodField()

    # Phase 49.5. Three registers that the Phase 49 audit found had no
    # representation at all. Each is computed rather than stored, so a revoked
    # grant or a withdrawn note request disappears from the payload immediately.
    notes = serializers.SerializerMethodField()
    assignment_history = serializers.SerializerMethodField()
    archive_sharing = serializers.SerializerMethodField()
    can_request_notes = serializers.SerializerMethodField()
    can_note = serializers.SerializerMethodField()
    can_manage_assignments = serializers.SerializerMethodField()
    can_share_archive = serializers.SerializerMethodField()

    # Capability flags. The UI renders its action bar purely from these, so every
    # authorization rule has exactly one home on the server and the client never
    # re-derives who may do what.
    can_edit = serializers.SerializerMethodField()
    can_edit_matrix = serializers.SerializerMethodField()
    can_send_for_review = serializers.SerializerMethodField()
    can_act = serializers.SerializerMethodField()
    can_withdraw = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    can_archive = serializers.SerializerMethodField()
    can_download_pdf = serializers.SerializerMethodField()

    class Meta:
        model = Memo
        fields = [
            "id", "memo_number", "reference_number", "subject",
            "to_line", "memo_type", "memo_type_label", "status", "status_label",
            "is_restricted", "keeps_log", "sections",
            "cc_department_names", "department_unit_name",
            "department_sub_unit_name",
            "created_by", "department", "department_label",
            "attachment_url", "attachments",
            "created_at", "created_at_bs", "updated_at", "submitted_at",
            "finalized_at", "approved_at", "archived_at",
            "approval_steps", "workflow_steps", "timeline",
            "pending_with", "ageing", "my_step", "is_read_only",
            "signatures", "tracker", "approval_certificate",
            "notes", "assignment_history", "archive_sharing",
            "can_edit", "can_edit_matrix", "can_send_for_review", "can_act",
            "can_withdraw", "can_delete", "can_archive", "can_download_pdf",
            "can_request_notes", "can_note", "can_manage_assignments",
            "can_share_archive",
        ]
        read_only_fields = fields

    def get_status_label(self, obj):
        return memo_status_label(obj)

    def get_department_label(self, obj):
        return obj.resolved_department_name()

    def get_pending_with(self, obj):
        return _pending_with(obj)

    def get_ageing(self, obj):
        from .workflow import step_ageing
        return step_ageing(obj, obj.active_step)

    def get_cc_department_names(self, obj):
        """
        The CC list as names. On a GENERAL memo these departments may also read it
        once archived (p.4), so the reader is shown who that is.
        """
        return [dept.name for dept in obj.cc_departments.all()]

    def get_timeline(self, obj):
        from .workflow import build_timeline
        return MemoTimelineEntrySerializer(build_timeline(obj), many=True).data

    def get_signatures(self, obj):
        from .workflow import signature_blocks
        return MemoSignatureBlockSerializer(signature_blocks(obj), many=True).data

    def get_tracker(self, obj):
        from .workflow import build_tracker
        return MemoTrackerStageSerializer(build_tracker(obj), many=True).data

    def get_approval_certificate(self, obj):
        from .workflow import approval_certificate
        certificate = approval_certificate(obj)
        return MemoApprovalCertificateSerializer(certificate).data if certificate else None

    # --- Phase 49.5 blocks ---------------------------------------------
    def get_notes(self, obj):
        from .governance import note_summary
        return note_summary(obj)

    def get_assignment_history(self, obj):
        from .governance import assignment_history
        return assignment_history(obj)

    def get_archive_sharing(self, obj):
        from .governance import archive_sharing_history
        return archive_sharing_history(obj)

    def get_can_request_notes(self, obj):
        from .governance import can_request_notes
        user = self._request_user()
        return bool(user and can_request_notes(user, obj))

    def get_can_note(self, obj):
        """
        True only when the caller has an OUTSTANDING request. A capability flag
        that stayed true after noting would render a button the API refuses.
        """
        user = self._request_user()
        if user is None:
            return False
        round_ = getattr(obj, "note_round", None)
        if round_ is None:
            return False
        return round_.recipients.filter(
            user=user, status=MemoNoteRecipient.NoteStatus.PENDING).exists()

    def get_can_manage_assignments(self, obj):
        from .governance import can_manage_assignments
        user = self._request_user()
        return bool(user and can_manage_assignments(user, obj))

    def get_can_share_archive(self, obj):
        from .governance import can_share_archive
        user = self._request_user()
        return bool(user and can_share_archive(user, obj))

    def get_my_step(self, obj):
        """The requesting user's own row in the matrix, if they have one."""
        user = self._request_user()
        if user is None:
            return None
        step = next(
            (s for s in obj.workflow_steps.all() if s.assignee_id == user.id), None
        )
        if step is None:
            return None
        return {
            "id": str(step.id),
            "sequence": step.sequence,
            "role_type": step.role_type,
            "role_label": step.get_role_type_display(),
            "status": step.status,
            "requires_comment": step.requires_comment,
            "is_my_turn": step.status == MemoWorkflowStep.StepStatus.ACTIVE,
        }

    def get_created_at_bs(self, obj):
        from config.nepali_dates import to_bs
        return to_bs(obj.created_at)

    def _request_user(self):
        request = self.context.get("request")
        if request is None or not getattr(request, "user", None):
            return None
        user = request.user
        return user if getattr(user, "is_authenticated", False) else None

    def get_attachment_url(self, obj):
        # Short-lived signed download URL (documents.protected_media). Object-level
        # access is enforced here: this URL is only produced when the memo was
        # serialized behind CanViewMemo, and it expires quickly. Never a raw
        # /media/ path; forced as a download (C2). The gated
        # /api/v1/memos/{id}/attachment/ endpoint remains for blob clients.
        if not obj.attachment:
            return None
        from documents.protected_media import signed_media_url
        # Phase 11 (M7): bound to the requesting user, so a leaked link is
        # useless to anyone else. `request` is None only when a serializer is
        # used outside a request (email delivery), where an unbound link is
        # the deliberate fallback.
        viewer = getattr(self.context.get("request"), "user", None)
        return signed_media_url(obj.attachment.name, ttl=300, download=True,
                                user=viewer)

    def _is_admin(self, user):
        return user is not None and user.role == User.Roles.ADMIN

    def get_can_edit(self, obj):
        """
        Mirrors permissions.CanMutateMemo. A rejected memo is editable so the
        author can act on the comments and resubmit - which is how Phase 2's
        "return to draft with comments" is realised without losing the Rejected
        status from the record.
        """
        user = self._request_user()
        if user is None or obj.is_read_only:
            return False
        if obj.status not in (Memo.Status.DRAFT, Memo.Status.REJECTED):
            return False
        return self._is_admin(user) or obj.created_by_id == user.id

    def _is_author(self, obj, user):
        return user is not None and obj.created_by_id == user.id

    def get_can_edit_matrix(self, obj):
        user = self._request_user()
        if user is None or obj.is_read_only:
            return False
        if obj.status not in (Memo.Status.DRAFT, Memo.Status.REJECTED):
            return False
        return self._is_admin(user) or self._is_author(obj, user)

    def get_can_send_for_review(self, obj):
        user = self._request_user()
        if user is None or obj.is_read_only:
            return False
        if obj.status not in (Memo.Status.DRAFT, Memo.Status.REJECTED):
            return False
        if not (self._is_admin(user) or self._is_author(obj, user)):
            return False
        # Without a matrix there is nobody to route to; the UI shows the
        # "build the workflow first" state instead of an enabled button.
        return any(True for _ in obj.workflow_steps.all())

    def get_can_act(self, obj):
        """True when it is this user's turn on the matrix (or they are admin)."""
        user = self._request_user()
        if user is None or obj.is_read_only:
            return False
        active = next(
            (s for s in obj.workflow_steps.all()
             if s.status == MemoWorkflowStep.StepStatus.ACTIVE), None,
        )
        if active is None:
            return False
        return self._is_admin(user) or active.assignee_id == user.id

    def get_can_withdraw(self, obj):
        """
        The author stopping their own in-flight memo. Not available once the memo
        is terminal - there is nothing left to withdraw from.
        """
        user = self._request_user()
        if user is None or obj.status in Memo.TERMINAL_STATUSES:
            return False
        if obj.status == Memo.Status.DRAFT:
            # A draft has not gone anywhere; deleting it is the honest action.
            return False
        return self._is_admin(user) or self._is_author(obj, user)

    def get_can_delete(self, obj):
        user = self._request_user()
        if user is None or obj.status == Memo.Status.ARCHIVED:
            return False
        if self._is_admin(user):
            return True
        return self._is_author(obj, user) and obj.status == Memo.Status.DRAFT

    def get_can_archive(self, obj):
        user = self._request_user()
        if user is None or obj.status != Memo.Status.APPROVED:
            return False
        return self._is_admin(user) or user.role == User.Roles.APPROVER

    def get_can_download_pdf(self, obj):
        return obj.status in (Memo.Status.APPROVED, Memo.Status.ARCHIVED)


class MemoUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for editing a draft / rejected memo.

    This exists because the detail serializer declares `read_only_fields =
    fields`, so routing update actions through it made PATCH a silent no-op: the
    request returned 200 and discarded every field. Edits now land, and the same
    body sanitisation as creation applies.
    """
    cc_department_ids = serializers.PrimaryKeyRelatedField(
        many=True, required=False, source="cc_departments",
        queryset=Department.objects.all())

    class Meta:
        model = Memo
        fields = ["subject", "to_line", "memo_type", "reference_number",
                  "department_unit", "department_sub_unit", "cc_department_ids"]


class MemoCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer used to create a draft memo (any authenticated user).

    created_by / status / memo_number are injected by the view layer
    (thin-serializer, fat-service convention) - see MemoViewSet.perform_create.
    id / memo_number / status are returned read-only so the client can navigate
    to the new memo and show its number after creation.
    """
    cc_department_ids = serializers.PrimaryKeyRelatedField(
        many=True, required=False, source="cc_departments",
        queryset=Department.objects.all())

    class Meta:
        model = Memo
        fields = ["id", "memo_number", "status", "subject",
                  "to_line", "memo_type", "reference_number",
                  "department_unit", "department_sub_unit", "cc_department_ids",
                  "attachment"]
        read_only_fields = ["id", "memo_number", "status"]
        # Accept the upload but never echo the raw MEDIA path back (C2/L4);
        # clients read the gated attachment_url from the detail endpoint.
        extra_kwargs = {"attachment": {"write_only": True}}

    def validate_attachment(self, value):
        # Phase 11 (audit finding M2): the implementation moved to
        # config.uploads so attendance-correction attachments get the same
        # checks. They previously had NONE of them — the validation existed
        # here and simply was not applied the next time the feature was built.
        # 10 MB and fifteen file types — see memos.services for why this no
        # longer matches the external manual's p.6.
        from .services import validate_memo_attachment
        return validate_memo_attachment(value)


class MemoTemplateSerializer(serializers.ModelSerializer):
    """CRUD serializer for admin-editable memo templates."""
    class Meta:
        model = MemoTemplate
        fields = [
            "id", "name", "memo_type", "subject_template", "body_template",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
