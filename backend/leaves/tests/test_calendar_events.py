"""
Nepali calendar events (festivals, observances, jayantis).

The design point worth pinning: these are NOT holidays. `Holiday.date` is
unique and every row in it is excluded from working-day calculations, so
putting festivals there would have turned every marked day into a day off and
silently inflated everyone's leave balance. This table is display-only.
"""
from datetime import date

import pytest
from rest_framework.test import APIClient

from leaves.models import CalendarEvent, Holiday
from users.models import User


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def person(db):
    return User.objects.create_user(
        username="cal.user", email="cal.user@nif.test", password="pass12345",
        first_name="Cal", last_name="User", role="maker",
    )


@pytest.fixture
def events(db):
    CalendarEvent.objects.create(
        date=date(2026, 10, 20), name="Vijaya Dashami", name_np="विजया दशमी",
        event_type="festival", tithi="दशमी", is_public_holiday=True)
    # Same day, second entry — the reason this table exists at all.
    CalendarEvent.objects.create(
        date=date(2026, 10, 20), name="Ghatasthapana", name_np="घटस्थापना",
        event_type="religious", is_public_holiday=False)
    CalendarEvent.objects.create(
        date=date(2027, 1, 1), name="New Year", event_type="observance")
    CalendarEvent.objects.create(
        date=date(2026, 10, 25), name="Hidden", is_active=False)


@pytest.mark.django_db
def test_several_events_can_share_one_day(events):
    """Holiday.date is unique; this deliberately is not."""
    assert CalendarEvent.objects.filter(date=date(2026, 10, 20)).count() == 2


@pytest.mark.django_db
def test_creating_an_event_creates_no_holiday(db):
    """
    The leave engine must not see these. A festival marked here does not make
    the day non-working unless HR also records it in Holiday.

    Asserted as a DELTA, not an absolute count: migrations seed their own
    holidays, so `Holiday.objects.count() == 0` was testing the fixture rather
    than the behaviour — and passed or failed for reasons unrelated to events.
    """
    before = Holiday.objects.count()
    CalendarEvent.objects.create(
        date=date(2026, 3, 8), name="Holi", name_np="होली", event_type="festival",
        is_public_holiday=True)
    assert Holiday.objects.count() == before
    assert not Holiday.objects.filter(date=date(2026, 3, 8)).exists()


@pytest.mark.django_db
def test_the_endpoint_returns_a_range(api, person, events):
    api.force_authenticate(person)
    res = api.get("/api/v1/leaves/calendar-events/",
                  {"start": "2026-10-01", "end": "2026-10-31"})
    assert res.status_code == 200
    names = [e["name"] for e in res.data]
    assert "Vijaya Dashami" in names and "Ghatasthapana" in names
    assert "New Year" not in names          # outside the range
    assert "Hidden" not in names            # is_active=False


@pytest.mark.django_db
def test_the_range_crosses_a_gregorian_year(api, person, events):
    """
    A Bikram Sambat month can straddle two AD years — Poush runs across
    December into January. A year-keyed endpoint drops everything past the
    boundary, which is why this one takes a range.
    """
    api.force_authenticate(person)
    res = api.get("/api/v1/leaves/calendar-events/",
                  {"start": "2026-12-15", "end": "2027-01-15"})
    assert [e["name"] for e in res.data] == ["New Year"]


@pytest.mark.django_db
def test_display_name_prefers_devanagari(api, person, events):
    api.force_authenticate(person)
    res = api.get("/api/v1/leaves/calendar-events/",
                  {"start": "2026-10-20", "end": "2026-10-20"})
    by_name = {e["name"]: e for e in res.data}
    assert by_name["Vijaya Dashami"]["display_name"] == "विजया दशमी"
    assert by_name["Vijaya Dashami"]["tithi"] == "दशमी"
    # An entry with no Nepali name still renders something.
    res2 = api.get("/api/v1/leaves/calendar-events/",
                   {"start": "2027-01-01", "end": "2027-01-01"})
    assert res2.data[0]["display_name"] == "New Year"


@pytest.mark.django_db
def test_a_silly_range_is_clamped_not_served(api, person, events):
    """A calendar endpoint must not become a way to pull the whole table."""
    api.force_authenticate(person)
    res = api.get("/api/v1/leaves/calendar-events/",
                  {"start": "2000-01-01", "end": "2099-12-31"})
    assert res.status_code == 200
    # 2026-10-20 is inside 400 days of 2000-01-01? No — so the clamp bites.
    assert all(e["date"] < "2001-03-01" for e in res.data)


@pytest.mark.django_db
def test_bad_dates_fall_back_rather_than_500(api, person, events):
    api.force_authenticate(person)
    res = api.get("/api/v1/leaves/calendar-events/",
                  {"start": "not-a-date", "end": "also-bad"})
    assert res.status_code == 200


@pytest.mark.django_db
def test_anonymous_callers_are_refused(api, events):
    assert api.get("/api/v1/leaves/calendar-events/").status_code == 401


# --- the fixture is the single source for "the office is closed" ------------
@pytest.mark.django_db
def test_a_public_holiday_event_also_closes_the_office(tmp_path):
    """
    The trap this closes, measured before it was closed:

        2026-07-15 working days                          1.00
        + CalendarEvent(is_public_holiday=True)          1.00   ← unchanged
        + Holiday row                                    0.00

    CalendarEvent is display-only — the leave engine reads Holiday and nothing
    else. So a festival flagged as a public holiday in the fixture painted the
    calendar red while leave carried on deducting the day. The loader creates
    the Holiday row too.
    """
    import json
    from django.core.management import call_command
    from leaves.services import calculate_working_days

    day = date(2026, 7, 15)          # a Wednesday, so weekends are not the reason
    assert day.weekday() < 5
    assert calculate_working_days(day, day) == 1

    fixture = tmp_path / "events.json"
    fixture.write_text(json.dumps([
        {"date": "2026-07-15", "name": "Test Festival", "is_public_holiday": True},
        {"date": "2026-07-16", "name": "Test Observance", "is_public_holiday": False},
    ]))
    call_command("seed_calendar_events", file=str(fixture))

    assert Holiday.objects.filter(date=day).exists()
    assert calculate_working_days(day, day) == 0

    # An observance is NOT a day off; only the flag creates a Holiday.
    other = date(2026, 7, 16)
    assert CalendarEvent.objects.filter(date=other).exists()
    assert not Holiday.objects.filter(date=other).exists()
    assert calculate_working_days(other, other) == 1


@pytest.mark.django_db
def test_reloading_does_not_rename_a_holiday_hr_edited(tmp_path):
    """
    get_or_create, not update_or_create: HR may have reworded a holiday, and a
    fixture reload should not quietly overwrite that. Removal is never
    automatic either — leave already taken against the date was calculated
    with it.
    """
    import json
    from django.core.management import call_command

    Holiday.objects.create(date=date(2026, 7, 15), name="Dashain (office closed)")
    fixture = tmp_path / "events.json"
    fixture.write_text(json.dumps([
        {"date": "2026-07-15", "name": "Vijaya Dashami", "is_public_holiday": True},
    ]))
    call_command("seed_calendar_events", file=str(fixture))

    assert Holiday.objects.get(date=date(2026, 7, 15)).name == "Dashain (office closed)"
    assert Holiday.objects.filter(date=date(2026, 7, 15)).count() == 1
