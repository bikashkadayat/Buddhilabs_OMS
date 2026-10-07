import React, { useState, useEffect } from 'react';
import BrandLogo from '../../components/branding/BrandLogo';
import { useNavigate } from 'react-router-dom';
import { useLeaves } from '../../hooks/useLeaves';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import LeaveCard from '../../components/common/LeaveCard';
import { ArrowLeft, ChevronLeft, ChevronRight } from 'lucide-react';
import {
  bsMonthGrid, bsMonthLabel, shiftBsMonth, toBS, toNepaliNumeral, BS_WEEKDAYS,
} from '../../services/bsDate';

const TeamCalendar = () => {
  const navigate = useNavigate();
  const { leaves, loading, error, fetchLeaves } = useLeaves();
  const today = new Date();
  const todayBs = toBS(today) || { year: 2082, month: 0 };
  // A BIKRAM SAMBAT cursor, not a Gregorian one.
  const [cursor, setCursor] = useState({ year: todayBs.year, month: todayBs.month });
  const [selectedEvent, setSelectedEvent] = useState(null);

  useEffect(() => {
    fetchLeaves();
  }, [fetchLeaves]);
  useAutoRefresh(fetchLeaves, 30000); // team leave updates appear without a refresh

  const grid = bsMonthGrid(cursor.year, cursor.month);
  const label = bsMonthLabel(cursor.year, cursor.month);
  const toISO = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const todayISO = toISO(today);

  /* Events are matched on the cell's GREGORIAN date. Leave records are stored
     in AD, so converting them to BS to compare would mean two conversions per
     comparison and a rounding surface for no gain — the grid already carries
     the AD date each cell stands for. */
  const eventsOn = (ad) => leaves.filter((l) => {
    const start = new Date(l.start);
    const end = new Date(l.end);
    return ad >= new Date(start.getFullYear(), start.getMonth(), start.getDate())
        && ad <= new Date(end.getFullYear(), end.getMonth(), end.getDate());
  });

  const goToToday = () => setCursor({ year: todayBs.year, month: todayBs.month });
  const goToPrevMonth = () => setCursor((c) => shiftBsMonth(c, -1));
  const goToNextMonth = () => setCursor((c) => shiftBsMonth(c, 1));

  /* No blank leading cells: the grid returns whole weeks with the neighbouring
     month's days marked `outside`. A blank and a real date look identical at a
     glance, and people do read the edges of a calendar. */
  const days = grid.map((cell) => {
    const dayEvents = cell.outside ? [] : eventsOn(cell.ad);
    return (
      <div
        key={cell.iso}
        className={`cal-day${cell.iso === todayISO ? ' today' : ''}`
          + `${cell.isSaturday ? ' is-saturday' : ''}${cell.outside ? ' is-outside' : ''}`}
      >
        <span className="cal-date">{toNepaliNumeral(cell.bsDay)}</span>
        <span className="cal-date-ad">{cell.ad.getDate()}</span>
        {cell.isSaturday && !cell.outside && <span className="cal-holiday-tag">Holiday</span>}
        <div className="cal-events">
          {dayEvents.slice(0, 3).map((ev) => (
            <div key={`${ev.id}-${cell.iso}`} className={`cal-event ${ev.status}`}
              onClick={() => setSelectedEvent(ev)}>
              {ev.employee.split(' ')[0]} - {ev.type}
            </div>
          ))}
          {dayEvents.length > 3 && (
            <div className="cal-more">+{dayEvents.length - 3} more</div>
          )}
        </div>
      </div>
    );
  });

  return (
    <div className="page">
      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">
            <button className="pg-back" aria-label="Back" onClick={() => navigate('/leave')}>
              <ArrowLeft size={18} />
            </button>
            Leave Management
          </div>
          <div className="pg-title">Team Calendar</div>
          <div className="pg-desc">View team leave schedules and plan accordingly</div>
        </div>
        <div className="pg-head-right">
          <div className="pg-logo">
            <BrandLogo variant="letterhead" />
          </div>
        </div>
      </div>

      <div className="table-card" style={{ padding: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px', marginBottom: '24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button className="btn btn-ghost btn-sm" onClick={goToPrevMonth}>
              <ChevronLeft size={18} />
            </button>
            <h3 style={{ fontSize: 'var(--fs-h1)', fontWeight: 700, minWidth: '190px', textAlign: 'center' }}>
              {label.title}
              {/* The Gregorian span the Nepali month covers — a BS month never
                  lines up with an AD one. */}
              <span className="lr-cal-span">{label.span}</span>
            </h3>
            <button className="btn btn-ghost btn-sm" onClick={goToNextMonth}>
              <ChevronRight size={18} />
            </button>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button className="btn btn-ghost btn-sm" onClick={goToToday}>Today</button>
            <div className="calendar-legend">
              <span className="legend-item">
                <span className="legend-color pending"></span> Pending
              </span>
              <span className="legend-item">
                <span className="legend-color approved"></span> Approved
              </span>
            </div>
          </div>
        </div>

        {loading && <div style={{ padding: '4px 0 12px', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>Loading team leave…</div>}
        {error && (
          <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: 'rgba(220,38,38,.08)', color: '#b91c1c', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span>Could not load team leave data. {error}</span>
            <button className="btn btn-ghost btn-sm" onClick={fetchLeaves}>Retry</button>
          </div>
        )}

        <div className="calendar-container">
          <div className="calendar-header">
            <div className="calendar-title">
              {label.title} <span className="lr-cal-span">{label.span}</span>
            </div>
            <div className="calendar-legend">
              <span className="legend-item">
                <span className="legend-color pending"></span> Pending
              </span>
              <span className="legend-item">
                <span className="legend-color approved"></span> Approved
              </span>
              <span className="legend-item">
                <span className="legend-color today"></span> Today
              </span>
            </div>
          </div>

          <div className="calendar-grid">
            {BS_WEEKDAYS.map((h) => (
              <div key={h.en} className={`cal-day header ${h.en === 'Sat' ? 'is-saturday' : ''}`}>
                <span className="lr-dow-np">{h.np}</span>
                <span className="lr-dow-en">{h.en}</span>
              </div>
            ))}
            {days}
          </div>
        </div>
      </div>

      {selectedEvent && (
        <div className="modal-overlay" onClick={() => setSelectedEvent(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>Leave Details</h3>
            <LeaveCard leave={selectedEvent} />
            <button className="btn btn-primary" onClick={() => setSelectedEvent(null)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
};

export default TeamCalendar;
