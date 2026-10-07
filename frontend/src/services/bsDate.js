// Live Bikram Sambat (BS) date for the UI. Converts the current AD date using
// nepali-date-converter (offline, reliable). No hardcoded dates.
import * as NepaliDateModule from 'nepali-date-converter';

// UMD build interop: the constructor can be nested under default.default.
const NepaliDate = NepaliDateModule.default?.default || NepaliDateModule.default || NepaliDateModule;

const BS_MONTHS = [
  'Baishakh', 'Jestha', 'Ashadh', 'Shrawan', 'Bhadra', 'Ashwin',
  'Kartik', 'Mangsir', 'Poush', 'Magh', 'Falgun', 'Chaitra',
];

/** Current BS date as 'YYYY-MM-DD' (e.g. '2083-03-28'), or null on failure. */
export function todayBS(date = new Date()) {
  try {
    return new NepaliDate(date).format('YYYY-MM-DD');
  } catch {
    return null;
  }
}

/** Current BS date, long form (e.g. 'Ashadh 28, 2083'). Falls back to short. */
export function todayBSLong(date = new Date()) {
  try {
    const bs = new NepaliDate(date).getBS(); // { year, month (0-indexed), date }
    return `${BS_MONTHS[bs.month]} ${bs.date}, ${bs.year}`;
  } catch {
    return todayBS(date);
  }
}

/** ms until the next local midnight — used to refresh the date once per day. */
export function msUntilMidnight() {
  const now = new Date();
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 5);
  return next - now;
}

/* ---------------------------------------------------------------------------
 * Bikram Sambat month grids (Phase: Nepali calendar)
 *
 * Everything below exists because a BS month is NOT a fixed length — Baishakh
 * can be 30 or 31 days depending on the year, and there is no arithmetic rule
 * for it; the converter holds the table. So the month length is measured by
 * asking the converter, never computed.
 *
 * The week starts on SUNDAY, which is what a Nepali calendar prints and what
 * the existing Monday-aligned Gregorian grids got wrong for this purpose.
 * Saturday is the weekly holiday in Nepal; Sunday is a working day.
 * ------------------------------------------------------------------------- */

/** Nepali month names, in order. Index 0 = Baishakh. */
export const BS_MONTH_NAMES = BS_MONTHS;

/** Devanagari weekday names, Sunday first, as a Nepali calendar prints them. */
export const BS_WEEKDAYS = [
  { np: 'आइत', en: 'Sun' }, { np: 'सोम', en: 'Mon' }, { np: 'मङ्गल', en: 'Tue' },
  { np: 'बुध', en: 'Wed' }, { np: 'बिहि', en: 'Thu' }, { np: 'शुक्र', en: 'Fri' },
  { np: 'शनि', en: 'Sat' },
];

/**
 * Full Devanagari weekday names (…वार), Sunday first — the long form a printed
 * patro heads its columns with, distinct from the short BS_WEEKDAYS above.
 */
export const BS_WEEKDAYS_FULL = [
  'आइतवार', 'सोमवार', 'मङ्गलवार', 'बुधवार', 'बिहिवार', 'शुक्रवार', 'शनिवार',
];

/**
 * Devanagari (Nepali) month names, index 0 = Baishakh — the colloquial spellings
 * a patro prints (भदौ, असोज, कात्तिक…), not transliterations of BS_MONTHS.
 */
export const BS_MONTHS_NP = [
  'बैशाख', 'जेठ', 'असार', 'साउन', 'भदौ', 'असोज',
  'कात्तिक', 'मङ्सिर', 'पुस', 'माघ', 'फागुन', 'चैत',
];

const DEVANAGARI = ['०', '१', '२', '३', '४', '५', '६', '७', '८', '९'];

/** 15 -> '१५'. Used for the day number a Nepali calendar shows large. */
export const toNepaliNumeral = (n) =>
  String(n).split('').map((ch) => (DEVANAGARI[Number(ch)] ?? ch)).join('');

/** A JS Date -> { year, month (0-based), date } in BS, or null. */
export const toBS = (date) => {
  try {
    return new NepaliDate(date).getBS();
  } catch {
    return null;
  }
};

/** BS parts -> a JS Date at local midnight, or null if the date does not exist. */
export const bsToAd = (year, month, day) => {
  try {
    const d = new NepaliDate(year, month, day).toJsDate();
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  } catch {
    return null;
  }
};

/**
 * How many days a given BS month has.
 *
 * Measured, not calculated: the converter is the only authority on this, and
 * the lengths differ year to year. Walks down from 32 and returns the first day
 * that both constructs and still reports the month asked for — a converter that
 * silently rolls 2083-01-32 into Jestha would otherwise report 32.
 */
export const bsDaysInMonth = (year, month) => {
  for (let day = 32; day >= 28; day -= 1) {
    try {
      const d = new NepaliDate(year, month, day).getBS();
      if (d.month === month && d.date === day) return day;
    } catch {
      /* keep walking down */
    }
  }
  return 30;
};

/** The BS month one step before/after, rolling the year at Chaitra/Baishakh. */
export const shiftBsMonth = ({ year, month }, delta) => {
  const total = year * 12 + month + delta;
  return { year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 };
};

/**
 * A Sunday-aligned grid for one BS month.
 *
 * Returns whole weeks, so the grid is rectangular, with the leading and
 * trailing cells marked `outside` rather than omitted — an empty cell and a
 * neighbouring month's date look identical otherwise, and people do read the
 * edges of a calendar.
 *
 * Every cell carries BOTH dates: `bs` for what is displayed, `ad` for
 * everything the rest of the app keys on (leave records, holidays, today).
 */
export const bsMonthGrid = (year, month) => {
  const first = bsToAd(year, month, 1);
  if (!first) return [];
  const length = bsDaysInMonth(year, month);
  const lead = first.getDay();                       // 0 = Sunday
  const cells = [];

  const push = (bsYear, bsMonth, bsDay, outside) => {
    const ad = bsToAd(bsYear, bsMonth, bsDay);
    if (!ad) return;
    cells.push({
      bsYear, bsMonth, bsDay, ad, outside,
      iso: `${ad.getFullYear()}-${String(ad.getMonth() + 1).padStart(2, '0')}-${String(ad.getDate()).padStart(2, '0')}`,
      // Saturday is Nepal's weekly holiday; Sunday is a working day.
      isSaturday: ad.getDay() === 6,
    });
  };

  const prev = shiftBsMonth({ year, month }, -1);
  const prevLength = bsDaysInMonth(prev.year, prev.month);
  for (let i = lead; i > 0; i -= 1) push(prev.year, prev.month, prevLength - i + 1, true);
  for (let day = 1; day <= length; day += 1) push(year, month, day, false);

  const next = shiftBsMonth({ year, month }, 1);
  let day = 1;
  while (cells.length % 7 !== 0) { push(next.year, next.month, day, true); day += 1; }
  return cells;
};

/** 'Baishakh 2083' plus the Gregorian span it covers, e.g. '(Apr–May 2026)'. */
export const bsMonthLabel = (year, month) => {
  const start = bsToAd(year, month, 1);
  const end = bsToAd(year, month, bsDaysInMonth(year, month));
  const name = BS_MONTHS[month] || '';
  if (!start || !end) return `${name} ${year}`;
  const fmt = (d) => d.toLocaleDateString(undefined, { month: 'short' });
  const span = fmt(start) === fmt(end)
    ? `${fmt(start)} ${end.getFullYear()}`
    : `${fmt(start)}–${fmt(end)} ${end.getFullYear()}`;
  return { title: `${name} ${year}`, span };
};
