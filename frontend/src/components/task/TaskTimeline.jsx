import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';

import { fmtDateTime } from './taskLabels';
import { entrySummary, eventValue, groupTimeline, timeOnly } from './timelineGrouping';

/**
 * The activity timeline (Phase TASK-DETAIL-UI-POLISH).
 *
 * THE NOISE THIS EXISTS TO CUT
 * ----------------------------
 * Every row was rendered flat, oldest to newest, each repeating its own date in
 * full. A three-person task opens with three consecutive "Accepted" rows and
 * later carries a run of "Progress updated"; read as twelve identical-looking
 * blocks, the two entries that actually change the story — the submission, the
 * rework — are the hardest things on the card to find.
 *
 * So: rows are bucketed by DAY, consecutive rows of the SAME KIND collapse into
 * one entry, and the day's date is printed once at the top instead of on every
 * line.
 *
 * NOTHING IS DISCARDED
 * --------------------
 * Grouping hides no event: a collapsed group says how many it holds and opens
 * to show each one, and older days are behind a "show earlier" toggle rather
 * than trimmed. This is an audit trail, and a view that quietly dropped the
 * fourth of four acceptances would be worse than the wall of rows it replaced.
 */

/** One entry: a single event, or a folded run of the same kind. */
const Entry = ({ entry }) => {
  const [open, setOpen] = useState(false);
  const many = entry.rows.length > 1;
  const actors = [...new Set(entry.rows.map((r) => r.actor_name || 'System'))];

  if (!many) {
    const [row] = entry.rows;
    return (
      <li className="task-event">
        <span className="task-event-dot" aria-hidden="true" />
        <div className="task-event-body">
          <p className="task-event-head">
            <b>{entry.label}</b>
            {eventValue(row) && (
              <span className="task-event-value">{eventValue(row)}</span>
            )}
            <span className="task-event-meta">
              {row.actor_name || 'System'} · {timeOnly(row.created_at)}
            </span>
          </p>
          {row.remarks && <p className="task-event-note">{row.remarks}</p>}
        </div>
      </li>
    );
  }

  return (
    <li className="task-event is-group">
      <span className="task-event-dot" aria-hidden="true" />
      <div className="task-event-body">
        <button type="button" className="task-event-toggle"
          aria-expanded={open} onClick={() => setOpen(!open)}>
          <b>{entry.label}</b>
          <span className="task-event-count">{entry.rows.length}</span>
          {/* What the run actually did — "25% → 75%" — so the group does not
              hide the thing somebody opens it to see. */}
          {entrySummary(entry) && (
            <span className="task-event-value">{entrySummary(entry)}</span>
          )}
          <span className="task-event-meta">{actors.join(', ')}</span>
          <ChevronDown size={14} aria-hidden="true"
            className={open ? 'is-open' : ''} />
        </button>
        {open && (
          <ol className="task-event-children">
            {/* OLDEST FIRST inside the group, the reverse of the timeline
                around it: a progression reads 25 → 50 → 75, and showing it
                backwards would make a run of updates look like a retreat. */}
            {[...entry.rows].reverse().map((row) => (
              <li key={row.id}>
                {eventValue(row) && (
                  <span className="task-event-value">{eventValue(row)}</span>
                )}
                <span className="task-event-meta">
                  {row.actor_name || 'System'} · {timeOnly(row.created_at)}
                </span>
                {row.remarks && <p className="task-event-note">{row.remarks}</p>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </li>
  );
};

const RECENT_DAYS = 2;

const TaskTimeline = ({ rows = [] }) => {
  const [showAll, setShowAll] = useState(false);
  if (!rows.length) return <p className="task-sub">Nothing has happened yet.</p>;

  // Newest first: what happened last is what a person opening the page is
  // looking for, and the old order made them scroll past the task's whole life
  // to find it.
  const days = groupTimeline([...rows].reverse());
  const shown = showAll ? days : days.slice(0, RECENT_DAYS);
  const hidden = days.length - shown.length;

  return (
    <div className="task-timeline">
      {shown.map((day) => (
        <section key={day.key} className="task-day">
          <h3 className="task-day-label">
            {day.label}
            <span className="task-day-full">{fmtDateTime(day.entries[0].rows[0].created_at).split(',')[0]}</span>
          </h3>
          <ol className="task-events">
            {day.entries.map((entry, index) => (
              <Entry key={`${entry.action}-${index}`} entry={entry} />
            ))}
          </ol>
        </section>
      ))}

      {hidden > 0 && (
        <button type="button" className="task-link-btn"
          onClick={() => setShowAll(true)}>
          Show earlier activity ({hidden} more {hidden === 1 ? 'day' : 'days'})
        </button>
      )}
    </div>
  );
};

export default TaskTimeline;
