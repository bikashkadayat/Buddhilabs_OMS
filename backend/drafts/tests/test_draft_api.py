"""
Draft autosave API (Phase 111).

The behaviours here are the ones the feature exists for: an incomplete document
must be storable, a snapshot must belong to exactly one person, and the workflow
statuses of the three modules must be entirely unaffected by any of it.
"""
import pytest

from audit.models import AuditLog
from drafts import services
from drafts.models import DocumentDraft, DocumentDraftVersion

pytestmark = pytest.mark.django_db


def url(kind="memo", key="new"):
    return f"/api/v1/drafts/{kind}/{key}/"


# ---------------------------------------------------------------------------
# The core promise
# ---------------------------------------------------------------------------
class TestSnapshotsAreStored:
    def test_a_snapshot_round_trips(self, api, author, payload):
        api.force_authenticate(author)
        assert api.put(url(), {"payload": payload}, format="json").status_code == 200

        got = api.get(url())
        assert got.status_code == 200
        assert got.data["draft"]["payload"] == payload

    def test_an_incomplete_document_is_accepted(self, api, author):
        """
        The reason this app exists. Memo.subject, Circular.subject/body and
        Minute.meeting_time are all blank=False, so a snapshot routed through
        the real serializers would 400 for exactly the users who need autosave
        most — the ones part-way through.
        """
        api.force_authenticate(author)
        typing = {"form": {"subject": "", "to_line": ""},
                  "sections": [{"title": "Background", "body": "<p>Ju</p>"}]}
        assert api.put(url(), {"payload": typing}, format="json").status_code == 200
        assert api.get(url()).data["draft"]["payload"] == typing

    @pytest.mark.parametrize("kind", ["memo", "minute", "circular"])
    def test_every_module_uses_the_same_store(self, api, author, payload, kind):
        api.force_authenticate(author)
        assert api.put(url(kind), {"payload": payload},
                       format="json").status_code == 200
        assert DocumentDraft.objects.filter(kind=kind, owner=author).exists()

    def test_an_unknown_kind_is_rejected(self, api, author, payload):
        api.force_authenticate(author)
        assert api.put(url("leave"), {"payload": payload},
                       format="json").status_code == 404

    def test_missing_draft_reads_as_empty_not_an_error(self, api, author):
        """
        "Nothing to recover" is the normal case on most form mounts. Returning
        404 would make the client treat a real failure as routine.
        """
        api.force_authenticate(author)
        got = api.get(url())
        assert got.status_code == 200
        assert got.data["draft"] is None

    def test_a_second_save_overwrites_and_bumps_the_version(self, api, author, payload):
        api.force_authenticate(author)
        first = api.put(url(), {"payload": payload}, format="json")
        second = api.put(url(), {"payload": {"form": {"subject": "Changed"}}},
                         format="json")
        assert second.data["version"] == first.data["version"] + 1
        assert DocumentDraft.objects.filter(owner=author).count() == 1


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
class TestDraftsArePrivate:
    def test_another_user_cannot_read_your_draft(self, api, author, other, payload):
        api.force_authenticate(author)
        api.put(url(), {"payload": payload}, format="json")

        api.force_authenticate(other)
        assert api.get(url()).data["draft"] is None

    def test_two_users_editing_one_document_keep_separate_snapshots(
            self, api, author, other, payload):
        """
        Scoped per user, not per document: otherwise a colleague opening the
        same draft silently overwrites your recovery copy.
        """
        key = "11111111-1111-1111-1111-111111111111"
        api.force_authenticate(author)
        api.put(url("memo", key), {"payload": {"form": {"subject": "Mine"}}},
                format="json")
        api.force_authenticate(other)
        api.put(url("memo", key), {"payload": {"form": {"subject": "Theirs"}}},
                format="json")

        assert DocumentDraft.objects.filter(document_key=key).count() == 2
        assert api.get(url("memo", key)).data["draft"]["payload"]["form"]["subject"] \
            == "Theirs"

    def test_anonymous_access_is_refused(self, api, payload):
        assert api.put(url(), {"payload": payload}, format="json").status_code in (401, 403)
        assert api.get("/api/v1/drafts/").status_code in (401, 403)

    def test_the_list_shows_only_your_own_unfinished_work(
            self, api, author, other, payload):
        api.force_authenticate(author)
        api.put(url("memo", "new"), {"payload": payload}, format="json")
        api.force_authenticate(other)
        api.put(url("minute", "new"), {"payload": payload}, format="json")

        api.force_authenticate(author)
        rows = api.get("/api/v1/drafts/").data
        assert [r["kind"] for r in rows] == ["memo"]
        assert rows[0]["title"] == "Server refresh approval"


# ---------------------------------------------------------------------------
# Version history (Decision 3: milestones, not every autosave)
# ---------------------------------------------------------------------------
class TestVersionHistory:
    def test_an_ordinary_autosave_keeps_no_history(self, api, author, payload):
        api.force_authenticate(author)
        for i in range(5):
            api.put(url(), {"payload": {"form": {"subject": f"v{i}"}}},
                    format="json")
        assert DocumentDraftVersion.objects.count() == 0

    def test_a_milestone_is_retained(self, api, author, payload):
        api.force_authenticate(author)
        api.put(url(), {"payload": payload, "milestone": "manual_save"},
                format="json")
        assert DocumentDraftVersion.objects.count() == 1

    def test_only_the_last_ten_milestones_survive(self, api, author):
        api.force_authenticate(author)
        for i in range(14):
            api.put(url(), {"payload": {"form": {"subject": f"v{i}"}},
                            "milestone": "interval"}, format="json")

        draft = DocumentDraft.objects.get(owner=author)
        assert draft.versions.count() == DocumentDraftVersion.KEEP
        newest = draft.versions.first()
        assert newest.payload["form"]["subject"] == "v13"

    def test_restoring_a_version_makes_it_live(self, api, author):
        api.force_authenticate(author)
        api.put(url(), {"payload": {"form": {"subject": "First"}},
                        "milestone": "manual_save"}, format="json")
        first_version = DocumentDraft.objects.get(owner=author).versions.first().version
        api.put(url(), {"payload": {"form": {"subject": "Second"}}}, format="json")

        restored = api.post(f"{url()}restore/", {"version": first_version},
                            format="json")
        assert restored.status_code == 200
        assert restored.data["draft"]["payload"]["form"]["subject"] == "First"

    def test_restoring_retains_what_it_replaced(self, api, author):
        """A restore must not be the thing that loses work."""
        api.force_authenticate(author)
        api.put(url(), {"payload": {"form": {"subject": "First"}},
                        "milestone": "manual_save"}, format="json")
        draft = DocumentDraft.objects.get(owner=author)
        api.put(url(), {"payload": {"form": {"subject": "Current"}}}, format="json")

        api.post(f"{url()}restore/", {"version": draft.versions.first().version},
                 format="json")

        superseded = draft.versions.filter(reason="before_restore").first()
        assert superseded is not None
        assert superseded.payload["form"]["subject"] == "Current"

    def test_a_version_can_be_previewed_before_restoring(self, api, author):
        api.force_authenticate(author)
        api.put(url(), {"payload": {"form": {"subject": "Preview me"}},
                        "milestone": "manual_save"}, format="json")
        v = DocumentDraft.objects.get(owner=author).versions.first().version

        got = api.get(f"{url()}versions/{v}/")
        assert got.status_code == 200
        assert got.data["payload"]["form"]["subject"] == "Preview me"

    def test_restoring_a_version_that_does_not_exist_is_404(self, api, author, payload):
        api.force_authenticate(author)
        api.put(url(), {"payload": payload}, format="json")
        assert api.post(f"{url()}restore/", {"version": 999},
                        format="json").status_code == 404


# ---------------------------------------------------------------------------
# Discard and submit
# ---------------------------------------------------------------------------
class TestDiscard:
    def test_discarding_removes_the_snapshot(self, api, author, payload):
        api.force_authenticate(author)
        api.put(url(), {"payload": payload}, format="json")
        assert api.delete(url()).status_code == 204
        assert not DocumentDraft.objects.filter(owner=author).exists()

    def test_discarding_nothing_is_not_an_error(self, api, author):
        """The client deletes on successful submit without checking first."""
        api.force_authenticate(author)
        assert api.delete(url()).status_code == 204

    def test_discard_and_submit_are_distinguishable_in_the_audit_trail(
            self, api, author, payload):
        api.force_authenticate(author)
        api.put(url(), {"payload": payload}, format="json")
        api.delete(f"{url()}?submitted=1")

        events = [row.changes.get("event") for row in AuditLog.objects.all()]
        assert "draft_submitted" in events
        assert "draft_discarded" not in events


# ---------------------------------------------------------------------------
# Phase 111.16 - audit, without drowning the workflow trail (blocker B2)
# ---------------------------------------------------------------------------
class TestAuditIsQuiet:
    def test_autosave_does_not_write_audit_rows(self, api, author):
        """
        MemoViewSet.update writes an AuditLog row AND a user-visible timeline
        step per edit. A five-second autosave cadence routed anywhere near that
        would put hundreds of entries on one document.
        """
        api.force_authenticate(author)
        api.put(url(), {"payload": {"form": {"subject": "one"}}}, format="json")
        before = AuditLog.objects.count()

        for i in range(30):
            api.put(url(), {"payload": {"form": {"subject": f"typing {i}"}}},
                    format="json")

        assert AuditLog.objects.count() == before

    def test_autosave_frequency_is_still_recorded(self, api, author):
        """Counted on the row, which answers the same question without the noise."""
        api.force_authenticate(author)
        for i in range(6):
            api.put(url(), {"payload": {"form": {"subject": f"v{i}"}}},
                    format="json")
        assert DocumentDraft.objects.get(owner=author).autosave_count == 6

    @pytest.mark.parametrize("event,call", [
        ("draft_created", "create"),
        ("draft_restored", "restore"),
        ("draft_recovered", "recover"),
        ("draft_discarded", "discard"),
    ])
    def test_lifecycle_events_are_logged(self, api, author, event, call):
        api.force_authenticate(author)
        api.put(url(), {"payload": {"form": {"subject": "x"}},
                        "milestone": "manual_save"}, format="json")
        if call == "restore":
            v = DocumentDraft.objects.get(owner=author).versions.first().version
            api.post(f"{url()}restore/", {"version": v}, format="json")
        elif call == "recover":
            api.post(f"{url()}recovered/")
        elif call == "discard":
            api.delete(url())

        events = [row.changes.get("event") for row in AuditLog.objects.all()]
        assert event in events


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
class TestLimits:
    def test_an_oversized_snapshot_is_refused_with_413(self, api, author):
        """
        413 and not 400: the client must not treat this as transient and retry
        forever. The local copy is still intact either way.
        """
        api.force_authenticate(author)
        huge = {"form": {"subject": "x" * (services.MAX_PAYLOAD_BYTES + 10)}}
        assert api.put(url(), {"payload": huge}, format="json").status_code == 413

    def test_a_non_object_payload_is_rejected(self, api, author):
        api.force_authenticate(author)
        assert api.put(url(), {"payload": "just a string"},
                       format="json").status_code == 400

    def test_a_device_label_is_truncated_not_rejected(self, api, author, payload):
        api.force_authenticate(author)
        r = api.put(url(), {"payload": payload, "device_label": "L" * 400},
                    format="json")
        # The serializer caps it; an over-long label must never be the reason a
        # user's work fails to save.
        assert r.status_code == 400
        r = api.put(url(), {"payload": payload, "device_label": "Chrome on macOS"},
                    format="json")
        assert r.status_code == 200
        assert DocumentDraft.objects.get(owner=author).device_label == "Chrome on macOS"
