"""The cron entrypoint for the subscription lifecycle."""
import datetime
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from tenancy import services
from tenancy.models import Subscription

pytestmark = pytest.mark.django_db

S = Subscription.Status


def _run(**kwargs):
    out, err = StringIO(), StringIO()
    call_command("subscriptions_advance", stdout=out, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


def test_it_reports_a_quiet_run(nif):
    out, err = _run(date="2026-06-15")
    assert "entered grace: 0, suspended: 0" in out
    assert "subscription mirrors consistent" in out
    assert err == ""


def test_it_moves_an_expired_subscription_to_grace(org):
    services.transition(org.subscription, S.ACTIVE,
                        period_start=datetime.date(2026, 1, 1),
                        period_end=datetime.date(2026, 2, 1))
    out, _ = _run(date="2026-02-02")
    assert "entered grace: 1" in out
    org.refresh_from_db()
    assert org.subscription.status == S.GRACE


def test_dry_run_changes_nothing(org):
    services.transition(org.subscription, S.ACTIVE,
                        period_start=datetime.date(2026, 1, 1),
                        period_end=datetime.date(2026, 2, 1))
    out, _ = _run(date="2026-02-02", dry_run=True)
    assert "[dry-run] would move 1 to grace" in out
    assert "abcschool" in out
    org.refresh_from_db()
    assert org.subscription.status == S.ACTIVE      # untouched


def test_a_bad_date_is_refused_clearly():
    with pytest.raises(CommandError):
        _run(date="not-a-date")


def test_it_never_touches_nif(nif):
    _run(date="2030-01-01")
    nif.refresh_from_db()
    assert nif.subscription.status == S.ACTIVE
    assert nif.is_admitted is True
