import React, { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';

import { useCalendarEvents, useHolidays } from '../../hooks/useLeaveRecords';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import {
  bsMonthGrid, bsMonthLabel, shiftBsMonth, toBS, toNepaliNumeral,
  BS_MONTH_NAMES, BS_MONTHS_NP, BS_WEEKDAYS_FULL, BS_WEEKDAYS,
} from '../../services/bsDate';

/**
 * The Nepali calendar — festivals and public holidays, and nothing else.
 *
 * DELIBERATELY SEPARATE FROM THE LEAVE CALENDAR.
 *
 * `/leaves/my-calendar` is a work calendar: it answers "when am I off, and what
 * did I book". This one answers "what day is it, and what falls on it". Those
 * are different questions asked by different people at different moments, and
 * the earlier arrangement — one page doing both, labelled "Nepali Calendar" in
 * the menu — meant somebody opening a patro to check Dashain was shown their
 * own annual leave alongside it.
 *
 * So this page fetches NO leave data at all. Not hidden, not filtered out —
 * never requested.
 *
 * The grid below is a self-contained dark `npc-cal` widget that mirrors a
 * printed patro: full Devanagari weekday headings, a large BS day numeral, the
 * Gregorian day in the corner, festival(s) above and the tithi below. Its
 * styles are scoped to `npc-cal-*` so restyling it never touches the shared
 * leave calendars, which keep using `lr-cal-*` / CalendarDay.
 */
const toISO = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/* English weekday headings, Sunday first — Sunday and Saturday print red. The
   short form is swapped in on narrow screens where the full name will not fit. */
const WEEKDAYS_EN = [
  { full: 'Sunday', abbr: 'Sun' }, { full: 'Monday', abbr: 'Mon' },
  { full: 'Tuesday', abbr: 'Tue' }, { full: 'Wednesday', abbr: 'Wed' },
  { full: 'Thursday', abbr: 'Thu' }, { full: 'Friday', abbr: 'Fri' },
  { full: 'Saturday', abbr: 'Sat' },
];

/* The same, in Devanagari. Full names from BS_WEEKDAYS_FULL; the short forms
   reuse BS_WEEKDAYS (आइत, सोम…) for narrow screens. */
const WEEKDAYS_NP = BS_WEEKDAYS_FULL.map((full, i) => ({ full, abbr: BS_WEEKDAYS[i].np }));

const LANG_KEY = 'npc-lang';

const NepaliCalendar = () => {
  const today = new Date();
  const todayISO = toISO(today);
  const todayBs = toBS(today) || { year: 2082, month: 0 };
  const [cursor, setCursor] = useState({ year: todayBs.year, month: todayBs.month });
  const [selected, setSelected] = useState(null);
  // Display language for the calendar (numbers, month, weekdays, festival names).
  // Defaults to Nepali — it is the Nepali calendar — and the choice is remembered.
  const [lang, setLang] = useState(() => {
    try { return localStorage.getItem(LANG_KEY) === 'en' ? 'en' : 'np'; } catch { return 'np'; }
  });
  const np = lang === 'np';
  const num = (n) => (np ? toNepaliNumeral(n) : String(n));
  const toggleLang = () => setLang((l) => {
    const next = l === 'np' ? 'en' : 'np';
    try { localStorage.setItem(LANG_KEY, next); } catch { /* private mode: ignore */ }
    return next;
  });

  const grid = useMemo(() => bsMonthGrid(cursor.year, cursor.month), [cursor]);
  const gridStart = grid.length ? grid[0].iso : todayISO;
  const gridEnd = grid.length ? grid[grid.length - 1].iso : todayISO;

  const {
    data: events = [], isLoading, isError, error, refetch,
  } = useCalendarEvents(gridStart, gridEnd);

  /* A Bikram Sambat month can straddle two Gregorian years — Poush runs across
     December into January — and the holiday endpoint is keyed by AD year.
     Asking for one silently drops every holiday on the far side. */
  const adYears = useMemo(
    () => [...new Set(grid.map((c) => c.ad.getFullYear()))], [grid],
  );
  const { data: holidaysA = [] } = useHolidays(adYears[0]);
  const { data: holidaysB = [] } = useHolidays(adYears[1] ?? adYears[0]);
  const holidays = useMemo(
    () => (adYears.length > 1 ? [...holidaysA, ...holidaysB] : holidaysA),
    [adYears, holidaysA, holidaysB],
  );

  const eventsByDate = useMemo(() => {
    const map = {};
    for (const e of events) (map[e.date] ||= []).push(e);
    return map;
  }, [events]);
  const holidayByDate = useMemo(() => {
    const map = {};
    for (const h of holidays) map[h.date] = h;
    return map;
  }, [holidays]);

  const label = bsMonthLabel(cursor.year, cursor.month);
  const title = np
    ? `${BS_MONTHS_NP[cursor.month]} ${num(cursor.year)}`
    : `${BS_MONTH_NAMES[cursor.month]} ${cursor.year}`;
  // Subtitle always carries the English month + Gregorian span, so both scripts
  // are on screen for cross-reference whichever language is selected.
  const enSub = `${BS_MONTH_NAMES[cursor.month]} · ${String(label.span).replace('–', '/')}`;
  const weekdays = np ? WEEKDAYS_NP : WEEKDAYS_EN;
  const move = (delta) => setCursor((c) => shiftBsMonth(c, delta));
  const goToday = () => setCursor({ year: todayBs.year, month: todayBs.month });

  const openDay = (cell) => {
    const dayEvents = eventsByDate[cell.iso] || [];
    const holiday = holidayByDate[cell.iso];
    if (!dayEvents.length && !holiday) return;
    setSelected({
      iso: cell.iso, events: dayEvents, holidayName: holiday?.name,
      isHoliday: Boolean(holiday),
    });
  };

  /* Everything falling in the month on screen, listed under the grid. On a
     phone the cells clamp names hard, so without this the month's festivals
     would be readable only one tap at a time. */
  const monthEvents = useMemo(() => grid
    .filter((c) => !c.outside && eventsByDate[c.iso])
    .flatMap((c) => eventsByDate[c.iso].map((e) => ({ ...e, cell: c }))), [grid, eventsByDate]);

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>Nepali Calendar</h2>
          <div className="lr-page-sub">
            Bikram Sambat months, festivals and public holidays
          </div>
        </div>
      </div>

      {isError && <ErrorState error={error} onRetry={refetch} />}
      {isLoading && !isError && <Skeleton rows={1} height={480} />}

      {!isLoading && !isError && (
        <div className="npc-cal">
          <div className="npc-cal-head">
            <div className="npc-cal-heading">
              <h3 className="npc-cal-month">{title}</h3>
              <span className="npc-cal-sub">{enSub}</span>
            </div>
            <div className="npc-cal-nav">
              <button
                type="button"
                className="npc-lang"
                onClick={toggleLang}
                aria-label={np ? 'Switch calendar to English' : 'नेपालीमा बदल्नुहोस्'}
                title={np ? 'Switch to English' : 'Switch to Nepali'}
              >
                {np ? 'EN' : 'नेप'}
              </button>
              <button type="button" className="npc-today" onClick={goToday}>{np ? 'आज' : 'Today'}</button>
              <button type="button" className="npc-arrow" aria-label="Previous month" onClick={() => move(-1)}>
                <ChevronLeft size={18} aria-hidden="true" />
              </button>
              <button type="button" className="npc-arrow" aria-label="Next month" onClick={() => move(1)}>
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </div>
          </div>

          <div className="npc-cal-grid" role="grid" aria-label={`Nepali calendar for ${label.title}`}>
            {weekdays.map((d, i) => (
              <div key={WEEKDAYS_EN[i].full} className={`npc-dow${i === 0 || i === 6 ? ' is-red' : ''}`} role="columnheader">
                <span className="npc-dow-full">{d.full}</span>
                <span className="npc-dow-abbr">{d.abbr}</span>
              </div>
            ))}

            {grid.map((cell) => {
              const dayEvents = eventsByDate[cell.iso] || [];
              const holiday = holidayByDate[cell.iso];
              const isHoliday = Boolean(holiday) || dayEvents.some((e) => e.is_public_holiday);
              const isSat = cell.isSaturday;
              const isToday = cell.iso === todayISO;
              const tithi = dayEvents.find((e) => e.tithi)?.tithi;
              // Festival name follows the selected language: Devanagari
              // (display_name = name_np) in Nepali, the English `name` in
              // English. A public holiday only carries an English name, so it
              // heads the label in both. The other form is the fallback.
              const ev = dayEvents[0];
              const festival = np
                ? (holiday?.name || ev?.display_name || ev?.name)
                : (holiday?.name || ev?.name || ev?.display_name);
              const openable = dayEvents.length > 0 || Boolean(holiday);

              const cls = [
                'npc-cell',
                cell.outside && 'is-out',
                isSat && 'is-sat',
                isHoliday && 'is-holiday',
                isToday && 'is-today',
                openable && 'is-openable',
                dayEvents.length > 0 && 'has-events',
              ].filter(Boolean).join(' ');

              return (
                <div
                  key={cell.iso}
                  className={cls}
                  role={openable ? 'button' : 'gridcell'}
                  tabIndex={openable ? 0 : -1}
                  onClick={openable ? () => openDay(cell) : undefined}
                  onKeyDown={openable ? (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDay(cell); }
                  } : undefined}
                  aria-label={`${cell.ad.toDateString()}, Nepali day ${cell.bsDay}${festival ? `, ${festival}` : ''}${isToday ? ', today' : ''}`}
                >
                  <div className="npc-cell-top">
                    {festival ? <span className="npc-fest" title={festival}>{festival}</span> : <span />}
                    <span className="npc-ad">{cell.ad.getDate()}</span>
                  </div>
                  <span className="npc-bs">{num(cell.bsDay)}</span>
                  {tithi ? <span className="npc-tithi">{tithi}</span> : <span className="npc-tithi" />}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!isLoading && !isError && monthEvents.length > 0 && (
        <div className="lr-chart-card npc-list">
          <h3 className="memo-panel-title">{np ? 'यस महिना' : 'This month'}</h3>
          <ul className="npc-month">
            {monthEvents.map((e) => (
              <li key={`${e.date}-${e.id || e.name}`}
                className={e.is_public_holiday ? 'is-holiday' : undefined}>
                <span className="npc-when">
                  {num(e.cell.bsDay)} {np ? BS_MONTHS_NP[cursor.month] : BS_MONTH_NAMES[cursor.month]}
                  <span className="npc-ad">{e.cell.ad.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</span>
                </span>
                <span className="npc-name">{np ? (e.display_name || e.name) : (e.name || e.display_name)}</span>
                {e.is_public_holiday && <span className="npc-tag">{np ? 'बिदा' : 'Holiday'}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {selected && (
        <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Day details" onClick={() => setSelected(null)}>
          <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
            <div className="lr-modal-head">
              <h3>{selected.iso}</h3>
              <button type="button" className="lr-modal-close" aria-label="Close" onClick={() => setSelected(null)}><X size={18} aria-hidden="true" /></button>
            </div>
            {selected.events?.length > 0 ? (
              <ul className="lr-modal-events">
                {selected.events.map((e) => (
                  <li key={e.id || e.name} className={e.is_public_holiday ? 'is-holiday' : undefined}>
                    <strong>{e.display_name || e.name}</strong>
                    {e.tithi ? <span className="lr-modal-tithi">{e.tithi}</span> : null}
                    <span className="lr-modal-kind">
                      {e.is_public_holiday ? 'Public holiday' : e.event_type_display || e.event_type}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="lr-page-sub">
                {selected.holidayName ? `Public holiday: ${selected.holidayName}` : 'Nothing falls on this day.'}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default NepaliCalendar;
