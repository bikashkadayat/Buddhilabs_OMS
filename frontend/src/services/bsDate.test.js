import { describe, it, expect } from 'vitest';
import {
  bsMonthGrid, bsDaysInMonth, bsToAd, toBS, shiftBsMonth,
  toNepaliNumeral, bsMonthLabel, BS_WEEKDAYS,
} from './bsDate';

/**
 * Bikram Sambat month grids.
 *
 * The thing worth testing is that nothing here ASSUMES a month length. A BS
 * month is 29–32 days with no arithmetic rule — the converter's table is the
 * only authority — so a grid built on "30 days" or on Gregorian-style leap
 * logic drifts silently and only shows up as an off-by-one weeks later.
 */
describe('BS month lengths are measured, not assumed', () => {
  it('reports a plausible length for every month of a year', () => {
    for (let m = 0; m < 12; m += 1) {
      const n = bsDaysInMonth(2083, m);
      expect(n, `month ${m}`).toBeGreaterThanOrEqual(29);
      expect(n, `month ${m}`).toBeLessThanOrEqual(32);
    }
  });

  it('finds months of different lengths within one year', () => {
    // If every month came back the same, the measurement is not working.
    const lengths = new Set(Array.from({ length: 12 }, (_, m) => bsDaysInMonth(2083, m)));
    expect(lengths.size).toBeGreaterThan(1);
  });

  it('the last day converts back to the same month', () => {
    for (let m = 0; m < 12; m += 1) {
      const last = bsDaysInMonth(2083, m);
      const bs = toBS(bsToAd(2083, m, last));
      expect(bs.month, `month ${m} day ${last} rolled over`).toBe(m);
      expect(bs.date).toBe(last);
    }
  });
});

describe('the grid', () => {
  const grid = bsMonthGrid(2083, 0);

  it('is whole weeks, so the calendar is rectangular', () => {
    expect(grid.length % 7).toBe(0);
    expect(grid.length).toBeGreaterThanOrEqual(28);
  });

  it('starts on a Sunday — a Nepali calendar is Sunday-first', () => {
    expect(grid[0].ad.getDay()).toBe(0);
    expect(BS_WEEKDAYS[0].en).toBe('Sun');
    expect(BS_WEEKDAYS[6].en).toBe('Sat');
  });

  it('marks leading and trailing cells as outside rather than dropping them', () => {
    // An empty cell and a neighbouring month's date look identical otherwise,
    // and people do read the edges of a calendar.
    const inside = grid.filter((c) => !c.outside);
    expect(inside.length).toBe(bsDaysInMonth(2083, 0));
    expect(inside[0].bsDay).toBe(1);
    expect(inside[inside.length - 1].bsDay).toBe(bsDaysInMonth(2083, 0));
    expect(grid.some((c) => c.outside)).toBe(true);
  });

  it('runs consecutive AD dates with no gaps or repeats', () => {
    for (let i = 1; i < grid.length; i += 1) {
      const gap = (grid[i].ad - grid[i - 1].ad) / 86400000;
      expect(Math.round(gap), `gap before cell ${i}`).toBe(1);
    }
  });

  it('carries an ISO date for keying holidays and leave records', () => {
    for (const cell of grid) {
      expect(cell.iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(new Date(cell.iso).getTime()).not.toBeNaN();
    }
  });

  it('flags Saturday, which is the Nepali weekly holiday', () => {
    const saturdays = grid.filter((c) => c.isSaturday);
    expect(saturdays.length).toBe(grid.length / 7);
    for (const s of saturdays) expect(s.ad.getDay()).toBe(6);
  });
});

describe('navigation and labels', () => {
  it('rolls the year at Chaitra and Baishakh', () => {
    expect(shiftBsMonth({ year: 2083, month: 11 }, 1)).toEqual({ year: 2084, month: 0 });
    expect(shiftBsMonth({ year: 2083, month: 0 }, -1)).toEqual({ year: 2082, month: 11 });
  });

  it('labels the month with the Gregorian span it covers', () => {
    const label = bsMonthLabel(2083, 0);
    expect(label.title).toBe('Baishakh 2083');
    // Baishakh straddles two Gregorian months, which is exactly why the span
    // is shown at all.
    expect(label.span).toMatch(/–/);
  });

  it('renders Devanagari numerals', () => {
    expect(toNepaliNumeral(15)).toBe('१५');
    expect(toNepaliNumeral(2083)).toBe('२०८३');
    expect(toNepaliNumeral(0)).toBe('०');
  });
});
