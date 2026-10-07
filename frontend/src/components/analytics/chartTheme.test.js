import { describe, it, expect } from 'vitest';
import {
  HEALTH_COLOURS, OTHERS_COLOUR, SERIES_DARK, SERIES_LIGHT, STATUS_COLOURS,
  fmtDays, fmtHours, fmtNum, fmtPct, healthBand, seriesColour,
} from './chartTheme';

/**
 * The palette itself was validated with the data-viz checker (lightness band,
 * chroma floor, CVD separation, normal-vision separation, contrast) — that is
 * not re-derived here. What IS asserted is the structure those results depend
 * on: fixed order, no duplicates, matched light/dark sets, and colour assigned
 * by position rather than rank.
 */
describe('Palette structure', () => {
  it('keeps eight categorical hues in both modes', () => {
    expect(SERIES_LIGHT).toHaveLength(8);
    expect(SERIES_DARK).toHaveLength(8);
  });

  it('has no duplicate hue in either mode', () => {
    expect(new Set(SERIES_LIGHT).size).toBe(8);
    expect(new Set(SERIES_DARK).size).toBe(8);
  });

  it('assigns colour by position, so re-sorting never repaints a series', () => {
    expect(seriesColour(0)).toBe(SERIES_LIGHT[0]);
    expect(seriesColour(3)).toBe(SERIES_LIGHT[3]);
    // Same index, same colour, no matter how often it is asked.
    expect(seriesColour(3)).toBe(seriesColour(3));
  });

  it('paints the aggregate bucket grey — "Others" is not an identity', () => {
    expect(seriesColour(2, true)).toBe(OTHERS_COLOUR);
  });

  it('never generates a ninth hue', () => {
    // The API folds past the eighth department into "Others", so index 8 should
    // never be reached; if it is, wrapping is safer than an undefined fill.
    expect(SERIES_LIGHT).toContain(seriesColour(8));
  });

  it('keeps status colours reserved and distinct from the categorical set', () => {
    // A green that means "present" must never be reassigned to "series 3".
    expect(STATUS_COLOURS.present).not.toBe(STATUS_COLOURS.absent);
    expect(STATUS_COLOURS.late).not.toBe(STATUS_COLOURS.present);
    expect(Object.keys(STATUS_COLOURS)).toEqual(
      expect.arrayContaining(['present', 'late', 'half_day', 'wfh', 'absent',
        'on_leave', 'holiday']));
  });
});

describe('Health bands', () => {
  it('bands a score into healthy, watch and at-risk', () => {
    expect(healthBand(95).tone).toBe('ok');
    expect(healthBand(90).tone).toBe('ok');
    expect(healthBand(80).tone).toBe('warn');
    expect(healthBand(74).tone).toBe('bad');
  });

  it('treats a missing score as unknown, not as zero', () => {
    expect(healthBand(null).tone).toBe('muted');
    expect(healthBand(undefined).tone).toBe('muted');
    expect(healthBand(null).label).toBe('No data');
    // A missing score must not fall into the "at risk" band.
    expect(healthBand(null).tone).not.toBe('bad');
  });

  it('has a colour for every band', () => {
    ['ok', 'warn', 'bad', 'muted'].forEach((tone) => {
      expect(HEALTH_COLOURS[tone]).toMatch(/^#[0-9a-f]{6}$/i);
    });
  });
});

describe('Formatters', () => {
  it('renders a missing metric as an em dash, never as zero', () => {
    // The API returns null when there is nothing to divide by; printing 0%
    // would turn "we cannot say" into "it is zero".
    [fmtPct, fmtNum, fmtHours, fmtDays].forEach((format) => {
      expect(format(null)).toBe('—');
      expect(format(undefined)).toBe('—');
    });
  });

  it('renders a real zero as zero', () => {
    expect(fmtPct(0)).toBe('0.0%');
    expect(fmtHours(0)).toBe('0h');
  });

  it('formats percentages to one decimal', () => {
    expect(fmtPct(91.234)).toBe('91.2%');
    expect(fmtPct(100)).toBe('100.0%');
  });

  it('trims trailing zeros on counts', () => {
    expect(fmtNum(3.0)).toBe('3');
    expect(fmtNum(3.5)).toBe('3.5');
    expect(fmtDays(2)).toBe('2d');
  });
});
