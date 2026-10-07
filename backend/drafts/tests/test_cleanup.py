"""Phase 111.17 - retention. Drafts are working state, not records."""
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from drafts import services
from drafts.models import DocumentDraft, DocumentDraftVersion

pytestmark = pytest.mark.django_db


def _aged(draft, days):
    """Backdate saved_at past auto_now, which ignores ordinary assignment."""
    stamp = timezone.now() - timedelta(days=days)
    DocumentDraft.objects.filter(pk=draft.pk).update(saved_at=stamp)


@pytest.fixture
def draft(author):
    return services.save_snapshot(
        user=author, kind="memo", document_key="new",
        payload={"form": {"subject": "Old work"}},
        milestone=DocumentDraftVersion.Reason.MANUAL_SAVE)


def test_a_stale_draft_is_purged(draft, author):
    _aged(draft, 91)
    assert services.purge_expired() == 1
    assert not DocumentDraft.objects.exists()


def test_a_recent_draft_survives(draft, author):
    _aged(draft, 30)
    assert services.purge_expired() == 0
    assert DocumentDraft.objects.exists()


def test_retention_runs_off_last_touched_not_created(draft, author):
    """
    A document someone returns to every week is live work however long ago they
    started it, so the clock has to be saved_at rather than created_at.
    """
    DocumentDraft.objects.filter(pk=draft.pk).update(
        created_at=timezone.now() - timedelta(days=400))
    _aged(draft, 2)
    assert services.purge_expired() == 0


def test_purging_takes_the_versions_with_it(draft, author):
    _aged(draft, 91)
    services.purge_expired()
    assert not DocumentDraftVersion.objects.exists()


def test_the_command_reports_what_it_removed(draft, author):
    _aged(draft, 91)
    out = StringIO()
    call_command("purge_expired_drafts", stdout=out)
    assert "Purged 1 draft(s)" in out.getvalue()


def test_the_window_is_overridable(draft, author):
    _aged(draft, 10)
    call_command("purge_expired_drafts", "--days", "5", "--quiet")
    assert not DocumentDraft.objects.exists()


def test_the_job_is_in_the_crontab_and_the_heartbeat_registry():
    """
    Mirrors the existing monitoring contract: a scheduled job with no heartbeat
    entry is silently unmonitored, which is how four of eleven jobs once went
    unnoticed when they stopped running.
    """
    from monitoring.heartbeat import CRON_JOBS

    crontab = Path(__file__).resolve().parents[2] / "deploy" / "crontab"
    assert "manage.py purge_expired_drafts" in crontab.read_text()
    assert "PURGE_DRAFTS" in CRON_JOBS
