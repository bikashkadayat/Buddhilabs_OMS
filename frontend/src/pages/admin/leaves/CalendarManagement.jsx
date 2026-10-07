import React, { useState } from 'react';

import HolidayManagement from './HolidayManagement';
import CalendarEventManagement from './CalendarEventManagement';

/**
 * One admin home for everything that appears on the calendar, in two tabs:
 *
 *   Holidays        — the leave engine's days off (one per day, non-working).
 *   Calendar events — display-only festivals / jayantis / observances shown on
 *                     the Nepali calendar (several per day, do not close office).
 *
 * They are ONE nav entry rather than two because the sidebar caps a module at
 * seven links, and because an admin thinks of them together — "what shows on
 * the calendar" — even though they are two different tables for good reasons
 * (see CalendarEvent's model docstring). Each tab is its own self-contained
 * page component; this only chooses which one is on screen.
 */
const TABS = [
  { key: 'holidays', label: 'Holidays' },
  { key: 'events', label: 'Calendar events' },
];

const CalendarManagement = ({ initialTab = 'holidays' }) => {
  const [tab, setTab] = useState(TABS.some((t) => t.key === initialTab) ? initialTab : 'holidays');

  return (
    <>
      <div className="lr-tabs" role="tablist" aria-label="Calendar management" style={{ padding: '0 4px' }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`lr-btn ${tab === t.key ? 'lr-btn-primary' : 'lr-btn-ghost'}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'holidays' ? <HolidayManagement /> : <CalendarEventManagement />}
    </>
  );
};

export default CalendarManagement;
