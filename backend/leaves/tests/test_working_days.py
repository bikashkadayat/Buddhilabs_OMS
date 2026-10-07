from datetime import timedelta
from decimal import Decimal

import pytest

from leaves.models import Holiday
from leaves import services
from .conftest import MONDAY


@pytest.mark.django_db
def test_calculate_working_days_skips_weekend():
    # Monday..Sunday inclusive => 6 working days (only Saturday is a weekend in Nepal).
    result = services.calculate_working_days(MONDAY, MONDAY + timedelta(days=6))
    assert result == Decimal("6.0")


@pytest.mark.django_db
def test_calculate_working_days_excludes_holiday():
    Holiday.objects.create(date=MONDAY + timedelta(days=2), name="Test Holiday")
    # Mon..Fri is 5 working days, minus one holiday (Wed) => 4.
    result = services.calculate_working_days(MONDAY, MONDAY + timedelta(days=4))
    assert result == Decimal("4.0")


@pytest.mark.django_db
def test_single_weekend_day_is_zero():
    saturday = MONDAY + timedelta(days=5)
    assert services.calculate_working_days(saturday, saturday) == Decimal("0.00")
