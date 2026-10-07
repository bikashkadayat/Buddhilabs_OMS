"""
Task numbering: NIFN-TSK-2083-0001.

The format is specified exactly, so it is pinned exactly — prefix, BS year, and
four digits of zero padding. The two properties worth more than the format are
that numbers never collide and never go backwards.
"""
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from tasks.models import Task, TaskNumberSequence
from tasks.services import current_bs_year, generate_task_number

pytestmark = pytest.mark.django_db

PATTERN = re.compile(r"^NIFN-TSK-(\d{4})-(\d{4,})$")


def test_number_matches_the_specified_format():
    number = generate_task_number()
    assert PATTERN.match(number), number


def test_the_year_is_bikram_sambat_not_gregorian():
    """
    The specification's example is 2083, which is BS. A number carrying the AD
    year would be wrong on every document it appeared on for the life of the
    system, and would not be noticed until somebody filed by year.
    """
    year = int(PATTERN.match(generate_task_number()).group(1))
    assert year == current_bs_year()
    # BS runs ~56–57 years ahead of AD; this is a sanity floor, not a conversion.
    assert 2075 <= year <= 2110


def test_numbers_increment_from_one_and_are_zero_padded():
    assert generate_task_number().endswith("-0001")
    assert generate_task_number().endswith("-0002")
    assert generate_task_number().endswith("-0003")


def test_the_counter_widens_past_the_padding_rather_than_colliding():
    """
    An integer counter formatted to four digits keeps working at 10000 — it
    widens. A string counter that had been incremented lexically would have
    wrapped or collided here.
    """
    year = current_bs_year()
    TaskNumberSequence.objects.create(year=year, last_value=9999)
    assert generate_task_number() == f"NIFN-TSK-{year}-10000"


@pytest.mark.skipif(
    connection.vendor == "sqlite",
    reason="SQLite locks the whole database rather than the counter row, so "
           "concurrent writers deadlock instead of serialising. The property "
           "under test is a row lock, which only PostgreSQL — the database CI "
           "and production use — actually has. Run via ./run-tests.sh.")
def test_concurrent_generation_never_collides(django_db_blocker):
    """
    The counter is handed out under select_for_update(), so two creators
    serialise rather than racing on a "max existing number" read.

    Threads rather than a mock, because the thing being tested is the lock.

    EACH THREAD BINDS ITS OWN TENANT, and that is not test scaffolding -- it
    is the rule. A thread gets its own database connection, and the tenant is
    bound per connection: in a request that happens in
    TenantResolutionMiddleware, and anywhere else it has to be done by
    entering a tenant context. Under row-level security a thread that skips it
    cannot see the counter row it is meant to lock and cannot insert one
    either, because the policy's WITH CHECK refuses a row for a tenant that is
    not bound. `tenant_context` sets the contextvar AND `app.current_org`,
    which is exactly why it is the thing to use rather than the contextvar
    alone.
    """
    from tenancy.context import tenant_context
    from tenancy.scoping import active_organization

    organization = active_organization()

    def one():
        with django_db_blocker.unblock():
            from django.db import connection
            try:
                with tenant_context(organization):
                    return generate_task_number()
            finally:
                connection.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(lambda _: one(), range(8)))

    assert len(set(numbers)) == len(numbers), numbers


def test_the_number_is_unique_at_the_database_level():
    from django.db import IntegrityError

    number = generate_task_number()
    Task.objects.create(task_number=number, title="First")
    with pytest.raises(IntegrityError):
        Task.objects.create(task_number=number, title="Duplicate")
