import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * The Nepali calendar must stay a Nepali calendar.
 *
 * It began as the leave calendar with a different label in the menu, which
 * meant somebody opening a patro to check Dashain was shown their own annual
 * leave beside it. The two answer different questions — "when am I off" versus
 * "what day is it and what falls on it" — and the separation is only real if
 * this page has no access to leave data at all.
 *
 * A source test rather than a render test on purpose: a rendering assertion
 * passes as long as nothing happens to be on screen, which is exactly the
 * state a stray "show my leave here too" toggle would also produce.
 */
const here = dirname(fileURLToPath(import.meta.url));
const page = readFileSync(join(here, 'NepaliCalendar.jsx'), 'utf8');

describe('the Nepali calendar shows only Nepali events', () => {
  it('never requests leave data', () => {
    for (const hook of ['useCalendar(', 'useLeaves', 'useMyLeave', 'leaveService']) {
      expect(page, `imports or calls ${hook}`).not.toContain(hook);
    }
    // The leave-day endpoint by any route.
    expect(page).not.toMatch(/leave-day-records|\/leaves\/calendar\//);
  });

  it('renders no leave record into a day cell', () => {
    // CalendarDay draws a leave tag only when it is given a `record`.
    expect(page, 'a record is being passed to CalendarDay').not.toMatch(/record=\{/);
  });

  it('does fetch the two things it is for', () => {
    expect(page).toContain('useCalendarEvents');
    expect(page).toContain('useHolidays');
  });

  it('lists the month\'s events, for the widths where cells show dots', () => {
    expect(page).toContain('npc-month');
    expect(page).toMatch(/monthEvents/);
  });

  it('handles a Bikram Sambat month that straddles two Gregorian years', () => {
    // Poush runs across December into January; the holiday endpoint is keyed
    // by AD year, so one call drops half the month.
    expect(page).toMatch(/adYears/);
    expect(page).toMatch(/holidaysB/);
  });
});
