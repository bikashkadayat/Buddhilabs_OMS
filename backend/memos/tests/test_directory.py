"""
H5: the employee directory behind the approval-matrix picker is search-gated,
PII-free and throttled.

This replaces the tests for /available-checkers/ and /available-approvers/, which
were removed with the legacy engine. The guarantees are the same; what changed is
that the directory is deliberately NOT filtered by role, because Phase 4 requires
that any employee can be placed in any role type.
"""
import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_directory_requires_a_search_term(api, maker, checker, other_checker):
    api.force_authenticate(maker)
    # No search term -> empty, so the endpoint cannot dump the whole roster.
    assert api.get("/api/v1/memos/employees/").data == []
    # A single character is still below the minimum.
    assert api.get("/api/v1/memos/employees/", {"search": "c"}).data == []
    # Two characters -> results.
    resp = api.get("/api/v1/memos/employees/", {"search": "ch"})
    assert len(resp.data) >= 1


@pytest.mark.django_db
def test_email_is_never_exposed_in_the_directory(api, maker, checker):
    api.force_authenticate(maker)
    resp = api.get("/api/v1/memos/employees/", {"search": "checker"})
    assert resp.status_code == 200 and resp.data
    row = resp.data[0]
    assert set(row.keys()) == {
        "id", "full_name", "employee_id", "designation",
        "department", "role", "role_display",
    }
    assert "email" not in row


@pytest.mark.django_db
def test_directory_search_does_not_match_email(api, maker, checker):
    # The checker's email is checker1@nif.test; searching it must not leak them.
    api.force_authenticate(maker)
    assert api.get("/api/v1/memos/employees/", {"search": "nif.test"}).data == []


@pytest.mark.django_db
def test_directory_returns_every_role_not_just_approvers(api, maker, other_maker,
                                                         checker, approver):
    """
    The old pickers filtered to approval-eligible roles. This one must not: a
    plain employee is a legitimate Reviewer on someone else's memo.
    """
    api.force_authenticate(maker)
    resp = api.get("/api/v1/memos/employees/", {"search": "er"})
    roles = {row["role"] for row in resp.data}
    assert "maker" in roles
    assert {"checker", "approver"} & roles


@pytest.mark.django_db
def test_directory_excludes_the_caller(api, maker, checker):
    """
    You cannot be in your own memo's workflow, so offering yourself in the picker
    would only ever lead to a rejected submission.
    """
    api.force_authenticate(maker)
    resp = api.get("/api/v1/memos/employees/", {"search": "maker"})
    assert str(maker.id) not in {row["id"] for row in resp.data}


@pytest.mark.django_db
def test_directory_is_throttled(api, maker, checker, monkeypatch):
    # Pin a tiny rate deterministically (independent of DRF settings caching).
    from memos.views import MemoDirectoryThrottle
    monkeypatch.setattr(MemoDirectoryThrottle, "get_rate", lambda self: "3/min")
    api.force_authenticate(maker)
    codes = [api.get("/api/v1/memos/employees/", {"search": "ch"}).status_code
             for _ in range(5)]
    assert codes.count(200) == 3  # first 3 allowed
    assert 429 in codes  # then rate-limited within the window
