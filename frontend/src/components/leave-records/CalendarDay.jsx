import React from 'react';
import { toNepaliNumeral } from '../../services/bsDate';

/**
 * A single day cell in the month calendar. Colour comes from the leave type,
 * but a text code/badge is always shown too (colour-blind support).
 *
 * BIKRAM SAMBAT PRIMARY. When `bsDay` is given the cell leads with the Nepali
 * date in Devanagari, the way a printed Nepali calendar does, and carries the
 * Gregorian day underneath in small type. The AD date is kept rather than
 * dropped because every other surface in this product — email, timestamps,
 * exports — speaks AD, and a calendar you cannot cross-reference against them
 * is a calendar people stop trusting.
 *
 * Without `bsDay` it renders exactly as before, so the Gregorian callers that
 * have not been converted are unaffected.
 *
 * @param {{
 *   date: Date, inMonth: boolean, record?: Object, isHoliday?: boolean,
 *   isWeekend?: boolean, holidayName?: string, bsDay?: number,
 *   isToday?: boolean, onSelect?: (record:Object)=>void
 * }} props
 */
const CalendarDay = ({
  date, inMonth, record, isHoliday, isWeekend, holidayName, bsDay, isToday,
  events = [], tithi, iso, onSelect,
}) => {
  const dayNum = date.getDate();
  const bs = typeof bsDay === 'number';
  /* A day is worth opening if it carries EITHER leave or events. It used to be
     leave alone, which left the festival names unreachable on a phone — where
     the cell is too narrow to print them and they are shown as dots instead. */
  const openable = Boolean(record) || events.length > 0;
  const open = () => onSelect?.({ iso, date, record, events, holidayName, isHoliday });
  const color = record?.display_color;

  const classes = [
    'lr-cal-day',
    !inMonth && 'lr-cal-out',
    isWeekend && 'lr-cal-weekend',
    isHoliday && 'lr-cal-holiday',
    record && 'lr-cal-has-leave',
    // A festival that is a public holiday colours the cell red, the way a
    // printed Nepali calendar does. An observance that is NOT a day off is
    // listed but left black — the colour has to keep meaning "office closed".
    events.some((e) => e.is_public_holiday) && 'lr-cal-festival',
    bs && 'lr-cal-bs',
    isToday && 'lr-cal-today',
  ].filter(Boolean).join(' ');

  const label = [
    // The screen-reader label leads with the Gregorian date even in BS mode:
    // it is unambiguous, and a Devanagari numeral read aloud by an English
    // synthesiser is not.
    date.toDateString(),
    bs && `Nepali day ${bsDay}`,
    isToday && 'today',
    isWeekend && 'weekend',
    isHoliday && `holiday${holidayName ? `: ${holidayName}` : ''}`,
    events.length && events.map((e) => e.display_name || e.name).join(', '),
    record && `${record.leave_type_code} ${record.status}`,
  ].filter(Boolean).join(', ');

  return (
    <div
      className={classes}
      style={color ? { '--day-color': color } : undefined}
      role={openable ? 'button' : 'gridcell'}
      tabIndex={openable ? 0 : -1}
      aria-label={label}
      onClick={openable ? open : undefined}
      onKeyDown={openable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } } : undefined}
    >
      {bs ? (
        <>
          <span className="lr-cal-date lr-cal-bs-date">{toNepaliNumeral(bsDay)}</span>
          <span className="lr-cal-ad-date">{dayNum}</span>
        </>
      ) : (
        <span className="lr-cal-date">{dayNum}</span>
      )}
      {tithi && <span className="lr-cal-tithi">{tithi}</span>}
      {isHoliday && <span className="lr-cal-badge" title={holidayName}>H</span>}
      {/* Events under the date, as a Nepali calendar prints them. Two shown at
          most: a cell that grows with its content breaks the grid's rhythm,
          and the rest are reachable by opening the day. */}
      {events.slice(0, 2).map((e) => (
        <span
          key={e.id || e.name}
          className={`lr-cal-event${e.is_public_holiday ? ' is-holiday' : ''}`}
          title={e.detail || e.name}
        >
          {e.display_name || e.name}
        </span>
      ))}
      {events.length > 2 && (
        <span className="lr-cal-event is-more">+{events.length - 2}</span>
      )}
      {/* The same events as dots, for cells too narrow to print a name. CSS
          decides which of the two is visible — doing it with a JS breakpoint
          would give the calendar a second source of truth for its own width. */}
      {events.length > 0 && (
        <span className="lr-cal-dots" aria-hidden="true">
          {events.slice(0, 3).map((e) => (
            <i key={e.id || e.name}
              className={`lr-cal-dot${e.is_public_holiday ? ' is-holiday' : ''}`} />
          ))}
        </span>
      )}
      {record && (
        <span className="lr-cal-tag" style={{ background: color }}>
          {record.leave_type_code}
          {record.day_portion !== 'full' ? ' ½' : ''}
        </span>
      )}
    </div>
  );
};

export default CalendarDay;
