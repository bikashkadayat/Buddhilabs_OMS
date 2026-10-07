import React, { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useAuth } from '../../../hooks/useAuth';
import { useCalendar, useHolidays, useCalendarEvents } from '../../../hooks/useLeaveRecords';
import { days } from '../../../utils/leaveFormat';
import {
  bsMonthGrid, bsMonthLabel, shiftBsMonth, toBS, BS_WEEKDAYS,
} from '../../../services/bsDate';
import CalendarDay from '../../../components/leave-records/CalendarDay';
import { Skeleton, ErrorState } from '../../../components/leave-records/States';

const toISO = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const MyLeaveCalendar = () => {
  const { role } = useAuth();
  const isManager = ['approver', 'admin', 'checker'].includes(role);
  const today = new Date();
  const todayISO = toISO(today);
  // The cursor is a BIKRAM SAMBAT month now, not a Gregorian one.
  const todayBs = toBS(today) || { year: 2082, month: 0 };
  const [cursor, setCursor] = useState({ year: todayBs.year, month: todayBs.month });
  const [selected, setSelected] = useState(null);
  const [showTeam, setShowTeam] = useState(false);

  const grid = useMemo(() => bsMonthGrid(cursor.year, cursor.month), [cursor]);
  const gridStart = grid.length ? grid[0].iso : todayISO;
  const gridEnd = grid.length ? grid[grid.length - 1].iso : todayISO;

  const { data: records = [], isLoading, isError, error, refetch } = useCalendar(gridStart, gridEnd);

  /* A BS month can straddle two Gregorian years — Poush runs across December
     into January — and the holiday endpoint is keyed by AD year. Asking for
     only one of them silently drops every holiday on the far side of the
     boundary, which is the kind of bug that shows up once a year. */
  const adYears = useMemo(
    () => [...new Set(grid.map((c) => c.ad.getFullYear()))],
    [grid],
  );
  const { data: holidaysA = [] } = useHolidays(adYears[0]);
  const { data: holidaysB = [] } = useHolidays(adYears[1] ?? adYears[0]);
  const holidays = useMemo(
    () => (adYears.length > 1 ? [...holidaysA, ...holidaysB] : holidaysA),
    [adYears, holidaysA, holidaysB],
  );

  const recordByDate = useMemo(() => {
    const map = {};
    for (const r of records) map[r.date] = r;
    return map;
  }, [records]);
  const { data: events = [] } = useCalendarEvents(gridStart, gridEnd);
  /* Grouped by date because a day can carry several entries — a festival, a
     jayanti and an observance can land together, which is precisely why the
     events table allows more than one row per date. */
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

  const move = (delta) => setCursor((c) => shiftBsMonth(c, delta));
  const label = bsMonthLabel(cursor.year, cursor.month);

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>My Calendar</h2>
          <div className="lr-page-sub">Your leave days, weekends and holidays at a glance</div>
        </div>
        {isManager && (
          <button
            type="button"
            className={`lr-tab ${showTeam ? 'on' : ''}`}
            aria-pressed={showTeam}
            onClick={() => setShowTeam((s) => !s)}
          >
            Show team
          </button>
        )}
      </div>

      {showTeam && (
        <div className="lr-chart-card" role="note" style={{ marginTop: 0 }}>
          Per-day team overlay is available on the <a href="/leave/calendar">Team Calendar</a> and
          {' '}<a href="/leaves/team-attendance">Team Attendance</a> pages.
        </div>
      )}

      {isError && <ErrorState error={error} onRetry={refetch} />}
      {isLoading && !isError && <Skeleton rows={1} height={420} />}

      {!isLoading && !isError && (
        <div className="lr-cal">
          <div className="lr-cal-toolbar">
            <div className="lr-cal-title">
              {label.title}
              {/* The Gregorian span the Nepali month covers. A BS month never
                  lines up with an AD one, so without this the reader has no
                  way to place "Baishakh 2083" against anything else. */}
              <span className="lr-cal-span">{label.span}</span>
            </div>
            <div className="lr-cal-nav">
              <button type="button" className="lr-btn lr-btn-ghost" aria-label="Previous month" onClick={() => move(-1)}><ChevronLeft size={16} /></button>
              <button type="button" className="lr-btn lr-btn-ghost" onClick={() => setCursor({ year: todayBs.year, month: todayBs.month })}>Today</button>
              <button type="button" className="lr-btn lr-btn-ghost" aria-label="Next month" onClick={() => move(1)}><ChevronRight size={16} /></button>
            </div>
          </div>

          <div className="lr-cal-grid" role="grid" aria-label={`Leave calendar for ${label.title}`}>
            {/* Sunday first, and Saturday marked — that is the Nepali week.
                The old grid started on Monday, which is neither how a Nepali
                calendar is printed nor where Nepal's weekly holiday falls. */}
            {BS_WEEKDAYS.map((d) => (
              <div key={d.en} className={`lr-cal-dow ${d.en === 'Sat' ? 'is-saturday' : ''}`} role="columnheader">
                <span className="lr-dow-np">{d.np}</span>
                <span className="lr-dow-en">{d.en}</span>
              </div>
            ))}
            {grid.map((cell) => (
              <CalendarDay
                key={cell.iso}
                iso={cell.iso}
                date={cell.ad}
                bsDay={cell.bsDay}
                inMonth={!cell.outside}
                isToday={cell.iso === todayISO}
                record={recordByDate[cell.iso]}
                isWeekend={cell.isSaturday}
                isHoliday={Boolean(holidayByDate[cell.iso])}
                holidayName={holidayByDate[cell.iso]?.name}
                events={eventsByDate[cell.iso] || []}
                tithi={eventsByDate[cell.iso]?.find((e) => e.tithi)?.tithi}
                onSelect={setSelected}
              />
            ))}
          </div>

          <div className="lr-cal-legend">
            <span><span className="lr-cal-badge" style={{ position: 'static' }}>H</span> Holiday</span>
            <span><span style={{ width: 12, height: 12, background: 'var(--bg-main)', border: '1px solid var(--border)', display: 'inline-block', borderRadius: 3 }} /> Weekend</span>
            <span>Coloured tag = leave type (code shown for colour-blind support)</span>
          </div>
        </div>
      )}

      {selected && (
        <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Leave day details" onClick={() => setSelected(null)}>
          <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
            <div className="lr-modal-head">
              <h3>{selected.iso}</h3>
              <button type="button" className="lr-modal-close" aria-label="Close" onClick={() => setSelected(null)}><X size={18} aria-hidden="true" /></button>
            </div>

            {/* Festivals first: on a phone the cell shows only a dot, so this
                is the only place the name is legible. */}
            {selected.events?.length > 0 && (
              <ul className="lr-modal-events">
                {selected.events.map((e) => (
                  <li key={e.id || e.name}
                    className={e.is_public_holiday ? 'is-holiday' : undefined}>
                    <strong>{e.display_name || e.name}</strong>
                    {e.tithi ? <span className="lr-modal-tithi">{e.tithi}</span> : null}
                    <span className="lr-modal-kind">
                      {e.is_public_holiday ? 'Public holiday' : e.event_type_display || e.event_type}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {selected.record ? (
              <dl className="lr-modal-grid">
                <div><dt>Type</dt><dd>{selected.record.leave_type_code}</dd></div>
                <div><dt>Status</dt><dd style={{ textTransform: 'capitalize' }}>{selected.record.status}</dd></div>
                <div><dt>Portion</dt><dd>{selected.record.day_portion.replace('_', ' ')}</dd></div>
                <div><dt>Counts as</dt><dd>{days(selected.record.portion_days)} day</dd></div>
              </dl>
            ) : (
              !selected.events?.length && <p className="lr-page-sub">Nothing recorded on this day.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default MyLeaveCalendar;
