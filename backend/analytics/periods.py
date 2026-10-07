"""Window and granularity parsing, and the bucketing every trend chart shares.

One module owns "what range of dates are we talking about and how are they
grouped", because nine endpoints answering that question nine different ways is
how two dashboards end up disagreeing about what "this quarter" means.

Nothing here touches the database.
"""
from dataclasses import dataclass
from datetime import date as _date, timedelta

from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.utils import timezone
from rest_framework.exceptions import ValidationError

# A request for daily granularity over three years would return ~1100 points per
# series, which no chart can draw and no browser should be asked to parse. The
# cap is applied by DOWNGRADING granularity, and the response says so.
MAX_POINTS = {
    "day": 90,
    "month": 36,
    "quarter": 12,
    "year": 5,
}
MAX_WINDOW_MONTHS = 36

GRANULARITIES = ("day", "month", "quarter", "year")

PRESETS = ("mtd", "qtd", "ytd", "last_30d", "last_90d", "last_6m", "last_12m", "last_24m")

# Coarser granularity in the order we fall back through it.
_COARSER = {"day": "month", "month": "quarter", "quarter": "year", "year": "year"}

TRUNC = {"month": TruncMonth, "quarter": TruncQuarter, "year": TruncYear}


@dataclass(frozen=True)
class Window:
    """A resolved analysis window. Immutable so it can be cached and hashed."""
    start: _date
    end: _date
    granularity: str
    preset: str | None = None
    truncated: bool = False
    compare: str | None = None

    @property
    def days(self):
        return (self.end - self.start).days + 1

    def as_dict(self):
        return {
            "from": self.start.isoformat(),
            "to": self.end.isoformat(),
            "granularity": self.granularity,
            "preset": self.preset,
            "truncated": self.truncated,
            "compare": self.compare,
        }

    def cache_token(self):
        return (f"{self.start.isoformat()}:{self.end.isoformat()}:{self.granularity}"
                f":{self.compare or '-'}")


# ---------------------------------------------------------------------------
# date helpers
# ---------------------------------------------------------------------------
def month_start(d):
    return d.replace(day=1)


def add_months(d, months):
    """Shift a date by whole months, clamping the day to the target month."""
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    # Clamp: 31 Jan + 1 month is the last day of February, not an exception.
    day = min(d.day, _days_in_month(year, month))
    return _date(year, month, day)


def _days_in_month(year, month):
    nxt = _date(year + (month == 12), (month % 12) + 1, 1)
    return (nxt - timedelta(days=1)).day


def quarter_of(d):
    return (d.month - 1) // 3 + 1


def quarter_start(d):
    return _date(d.year, (quarter_of(d) - 1) * 3 + 1, 1)


def months_between(start, end):
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------
def preset_window(preset, today=None):
    """(start, end) for a named preset. `end` is never in the future."""
    today = today or timezone.localdate()
    if preset == "mtd":
        return month_start(today), today
    if preset == "qtd":
        return quarter_start(today), today
    if preset == "ytd":
        return _date(today.year, 1, 1), today
    if preset == "last_30d":
        return today - timedelta(days=29), today
    if preset == "last_90d":
        return today - timedelta(days=89), today
    if preset == "last_6m":
        return month_start(add_months(today, -5)), today
    if preset == "last_12m":
        return month_start(add_months(today, -11)), today
    if preset == "last_24m":
        return month_start(add_months(today, -23)), today
    raise ValidationError({"period": f"Unknown period. Use one of: {', '.join(PRESETS)}."})


def default_granularity(start, end):
    """Pick a sensible bucket size for a window the caller did not qualify."""
    span_months = months_between(start, end)
    if span_months <= 2:
        return "day"
    if span_months <= 24:
        return "month"
    if span_months <= 60:
        return "quarter"
    return "year"


# ---------------------------------------------------------------------------
# the public entry point
# ---------------------------------------------------------------------------
def parse_window(params, *, default_preset="last_12m", today=None):
    """Resolve `?from/?to/?period/?granularity/?compare` into a Window.

    Precedence: explicit from/to beats a preset. An out-of-range granularity is
    downgraded rather than rejected, and the downgrade is reported through
    ``truncated`` so the UI can say so instead of quietly drawing less data.
    """
    today = today or timezone.localdate()
    raw_from, raw_to = params.get("from"), params.get("to")
    preset = params.get("period")

    if raw_from or raw_to:
        try:
            end = _date.fromisoformat(raw_to) if raw_to else today
            start = _date.fromisoformat(raw_from) if raw_from else month_start(end)
        except ValueError:
            raise ValidationError({"detail": "Dates must be YYYY-MM-DD."})
        preset = None
    else:
        start, end = preset_window(preset or default_preset, today)
        preset = preset or default_preset

    if end < start:
        raise ValidationError({"detail": "`to` must not precede `from`."})

    truncated = False
    # Cap the span itself before anything queries against it.
    if months_between(start, end) > MAX_WINDOW_MONTHS:
        start = month_start(add_months(end, -(MAX_WINDOW_MONTHS - 1)))
        truncated = True

    granularity = (params.get("granularity") or "").lower() or default_granularity(start, end)
    if granularity not in GRANULARITIES:
        raise ValidationError(
            {"granularity": f"Use one of: {', '.join(GRANULARITIES)}."})

    # Downgrade until the series fits. Terminates: "year" maps to itself and a
    # 36-month cap can never exceed 5 yearly points.
    while bucket_count(start, end, granularity) > MAX_POINTS[granularity]:
        coarser = _COARSER[granularity]
        if coarser == granularity:
            break
        granularity = coarser
        truncated = True

    compare = params.get("compare")
    if compare and compare not in ("previous", "year_ago"):
        raise ValidationError({"compare": "Use 'previous' or 'year_ago'."})

    return Window(start=start, end=end, granularity=granularity, preset=preset,
                  truncated=truncated, compare=compare or None)


def bucket_count(start, end, granularity):
    if granularity == "day":
        return (end - start).days + 1
    if granularity == "month":
        return months_between(start, end)
    if granularity == "quarter":
        return (end.year * 4 + quarter_of(end)) - (start.year * 4 + quarter_of(start)) + 1
    return end.year - start.year + 1


# ---------------------------------------------------------------------------
# bucket labelling and densification
# ---------------------------------------------------------------------------
def bucket_key(d, granularity):
    """The canonical string key for the bucket a date falls in."""
    if granularity == "day":
        return d.isoformat()
    if granularity == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if granularity == "quarter":
        return f"{d.year:04d}-Q{quarter_of(d)}"
    return f"{d.year:04d}"


def bucket_label(key, granularity):
    """Human label for a chart axis."""
    if granularity == "day":
        return key[5:]                       # MM-DD; the year is in the title
    if granularity == "month":
        year, month = key.split("-")
        return f"{_MONTHS[int(month) - 1]} {year[2:]}"
    return key


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def buckets(window):
    """Every bucket key in the window, in order. The spine of every series.

    Series are built by filling THIS list from a single grouped query, so a
    period with no rows becomes a zero point rather than a hole in the chart.
    """
    start, end, granularity = window.start, window.end, window.granularity
    keys = []
    if granularity == "day":
        day = start
        while day <= end:
            keys.append(day.isoformat())
            day += timedelta(days=1)
        return keys
    if granularity == "month":
        cursor = month_start(start)
        while cursor <= end:
            keys.append(bucket_key(cursor, "month"))
            cursor = add_months(cursor, 1)
        return keys
    if granularity == "quarter":
        cursor = quarter_start(start)
        while cursor <= end:
            keys.append(bucket_key(cursor, "quarter"))
            cursor = add_months(cursor, 3)
        return keys
    for year in range(start.year, end.year + 1):
        keys.append(f"{year:04d}")
    return keys


def densify(window, rows, *, key_field, value_fields, transform=None):
    """Turn sparse grouped rows into a dense, ordered series.

    ``rows`` is any iterable of dicts holding a date (or already-bucketed key)
    under ``key_field``. Missing buckets become zeros. This is the single place
    that guarantees the chart-contract test's "no holes, sorted ascending".
    """
    granularity = window.granularity
    collected = {}
    for row in rows:
        raw = row[key_field]
        if raw is None:
            continue
        key = raw if isinstance(raw, str) else bucket_key(_as_date(raw), granularity)
        bucket = collected.setdefault(key, {field: 0 for field in value_fields})
        for field in value_fields:
            bucket[field] += _numeric(row.get(field))

    series = []
    for key in buckets(window):
        values = collected.get(key) or {field: 0 for field in value_fields}
        point = {"period": key, "label": bucket_label(key, granularity), **values}
        series.append(transform(point) if transform else point)
    return series


def _as_date(value):
    return value.date() if hasattr(value, "date") else value


def _numeric(value):
    if value is None:
        return 0
    return value


def previous_window(window):
    """The comparison window for `?compare=`.

    'previous' is the immediately preceding span of equal length; 'year_ago' is
    the same calendar span shifted back twelve months. Both keep the original
    granularity so the two series can share an axis.
    """
    if window.compare == "year_ago":
        return Window(start=add_months(window.start, -12), end=add_months(window.end, -12),
                      granularity=window.granularity, preset=window.preset)
    span = window.days
    end = window.start - timedelta(days=1)
    return Window(start=end - timedelta(days=span - 1), end=end,
                  granularity=window.granularity, preset=window.preset)
