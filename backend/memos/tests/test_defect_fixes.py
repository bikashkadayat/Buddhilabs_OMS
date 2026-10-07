"""
Regression tests for the three defects the Phase 1 audit found by probing the
running API. Each of these reproduced against the pre-upgrade code.
"""
import pytest

from memos.models import Memo
from memos.services import generate_memo_number
from users.models import User

from .conftest import route


def _user(username, role=User.Roles.MAKER, department="Finance"):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role, department=department,
    )


# ---------------------------------------------------------------------------
# Defect 1 (HIGH): destroy was inherited from ModelViewSet with no object guard
# beyond CanViewMemo, and CanViewMemo granted every authenticated user read on
# any submitted non-sensitive memo. A stranger could hard-delete it: the probe
# returned "DESTROY status: 204 exists: False".
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_stranger_cannot_delete_a_submitted_memo(api, maker, checker, approver,
                                                 general_draft):
    route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    stranger = _user("del_stranger")

    api.force_authenticate(stranger)
    res = api.delete(f"/api/v1/memos/{general_draft.id}/")
    assert res.status_code in (403, 404)
    assert Memo.objects.filter(pk=general_draft.pk).exists()


@pytest.mark.django_db
def test_author_cannot_delete_a_memo_that_has_entered_the_workflow(
        api, maker, checker, approver, general_draft):
    route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(maker)
    res = api.delete(f"/api/v1/memos/{general_draft.id}/")
    assert res.status_code == 403
    assert "part of the audit record" in str(res.data)
    assert Memo.objects.filter(pk=general_draft.pk).exists()


@pytest.mark.django_db
def test_author_can_delete_own_untouched_draft_and_it_is_audited(api, maker, general_draft):
    from audit.models import AuditLog

    api.force_authenticate(maker)
    assert api.delete(f"/api/v1/memos/{general_draft.id}/").status_code == 204
    assert not Memo.objects.filter(pk=general_draft.pk).exists()

    # A deletion must never be invisible in the trail.
    entry = AuditLog.objects.filter(
        object_id=str(general_draft.id), action=AuditLog.Action.DELETE).first()
    assert entry is not None
    assert entry.changes["memo_number"] == general_draft.memo_number


# ---------------------------------------------------------------------------
# Defect 2 (MEDIUM): update actions routed through MemoDetailSerializer, which
# sets read_only_fields = fields, so PATCH returned 200 and discarded every
# field. The probe sent "CHANGED TITLE" and the title never moved.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_patch_actually_persists_the_edit(api, maker, general_draft):
    api.force_authenticate(maker)
    res = api.patch(f"/api/v1/memos/{general_draft.id}/",
                    {"subject": "Offsite budget (revised)", "to_line": "DCEO"},
                    format="json")
    assert res.status_code == 200

    general_draft.refresh_from_db()
    assert general_draft.subject == "Offsite budget (revised)"
    assert general_draft.to_line == "DCEO"
    # The response is the full detail shape, not the narrow write shape, because
    # clients re-render the whole memo from it.
    assert res.data["memo_number"] == general_draft.memo_number
    assert "timeline" in res.data


@pytest.mark.django_db
def test_patch_sanitises_the_body_like_creation_does(api, maker, general_draft):
    """The content lives in sections now, and its own endpoint sanitizes on write."""
    api.force_authenticate(maker)
    res = api.post(
        f"/api/v1/memos/{general_draft.id}/sections/",
        {"sections": [{
            "title": "Background",
            "body": '<p>ok</p><script>alert(1)</script>'
                    '<a href="javascript:evil()">x</a>',
        }]},
        format="json",
    )
    assert res.status_code == 200, res.data
    general_draft.refresh_from_db()
    section = general_draft.sections.first()
    assert "<script>" not in section.body
    assert "javascript:" not in section.body


@pytest.mark.django_db
def test_stranger_cannot_patch_someone_elses_memo(api, maker, checker, approver,
                                                  general_draft):
    route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    stranger = _user("patch_stranger")
    api.force_authenticate(stranger)
    res = api.patch(f"/api/v1/memos/{general_draft.id}/", {"title": "hijacked"}, format="json")
    assert res.status_code in (403, 404)
    general_draft.refresh_from_db()
    assert general_draft.subject == "Offsite budget"


@pytest.mark.django_db
def test_memo_is_locked_while_moving_through_its_workflow(
        api, maker, checker, approver, general_draft):
    route(api, maker, general_draft, (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(maker)
    res = api.patch(f"/api/v1/memos/{general_draft.id}/", {"title": "sneaky edit"}, format="json")
    assert res.status_code == 403
    assert "locked while it moves through its approval workflow" in str(res.data)


# ---------------------------------------------------------------------------
# Defect 3 (MEDIUM): DEFAULT_FILTER_BACKENDS was enabled globally but the
# viewset declared no filterset_fields / search_fields, so every documented
# query parameter was ignored and each client re-filtered one page client-side.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_status_filter_is_applied_server_side(api, maker, checker, approver):
    for index in range(3):
        Memo.objects.create(
            subject="S",
            memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
            created_by=maker, memo_number=generate_memo_number(Memo.MemoType.GENERAL),
        )
    sent = Memo.objects.create(
        subject="S",
        memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
        created_by=maker, memo_number=generate_memo_number(Memo.MemoType.GENERAL),
    )
    route(api, maker, sent, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(maker)
    res = api.get("/api/v1/memos/", {"status": "draft"})
    assert res.status_code == 200
    assert res.data["count"] == 3
    assert {row["status"] for row in res.data["results"]} == {"draft"}


@pytest.mark.django_db
def test_search_matches_number_and_subject(api, maker):
    target = Memo.objects.create(
        subject="Network hardware", memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
        created_by=maker, memo_number=generate_memo_number(Memo.MemoType.GENERAL),
    )
    Memo.objects.create(
        subject="Policy",
        memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
        created_by=maker, memo_number=generate_memo_number(Memo.MemoType.GENERAL),
    )

    api.force_authenticate(maker)
    # Title is gone with the manual's form, so subject is what a reader searches.
    by_subject = api.get("/api/v1/memos/", {"search": "hardware"})
    assert [row["id"] for row in by_subject.data["results"]] == [str(target.id)]

    by_number = api.get("/api/v1/memos/", {"search": target.memo_number})
    assert [row["id"] for row in by_number.data["results"]] == [str(target.id)]

    by_subject = api.get("/api/v1/memos/", {"search": "Network"})
    assert [row["id"] for row in by_subject.data["results"]] == [str(target.id)]


@pytest.mark.django_db
def test_ordering_is_applied_server_side(api, maker):
    for subject in ("A memo", "B memo", "C memo"):
        Memo.objects.create(
            subject=subject,
            memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
            created_by=maker, memo_number=generate_memo_number(Memo.MemoType.GENERAL),
        )
    api.force_authenticate(maker)
    res = api.get("/api/v1/memos/", {"ordering": "created_at"})
    assert [row["subject"] for row in res.data["results"]] == [
        "A memo", "B memo", "C memo"]
