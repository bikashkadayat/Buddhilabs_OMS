import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { taskService } from '../../services/taskService';
import { priorityTone } from '../../components/task/taskLabels';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The calendar (Phase T3, Part 3): Day, Week and Month.
 *
 * THE WINDOW IS THE SERVER'S DECISION
 * -----------------------------------
 * This page sends an anchor date and a view name; the server returns the window
 * it resolved along with the events. So the week starts on the same day here as
 * it does in a report (Sunday, matching the Nepali working week), and this file
 * has no date arithmetic to get wrong — it renders `start`..`end` as they came
 * back.
 *
 * FOUR KINDS, ONE PER TASK
 * ------------------------
 * The server classifies each task as overdue / due / upcoming / completed and
 * sends exactly one. A cell showing a task as both completed and upcoming is a
 * cell nobody can read, so the classification is not re-derived here.
 */

/**
 * The four kinds, and the colour each carries — the specification's rules:
 * red overdue, orange due soon, green completed, blue active.
 *
 * Every one is labelled in words as well as coloured. A calendar that encodes
 * urgency only in colour is unreadable to a colour-blind reader and useless
 * printed, and this is a page people print.
 */
const KIND_LABEL = {
  overdue: 'Overdue',
  due: 'Due soon',
  upcoming: 'Active',
  completed: 'Completed',
};

const VIEWS = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
];

const iso = (date) => date.toISOString().slice(0, 10);

/** Step the anchor by one window in either direction. */
const step = (anchor, view, direction) => {
  const date = new Date(`${anchor}T00:00:00`);
  if (view === 'day') date.setDate(date.getDate() + direction);
  else if (view === 'week') date.setDate(date.getDate() + 7 * direction);
  else date.setMonth(date.getMonth() + direction, 1);
  return iso(date);
};

/** Every date in the returned window, so empty days still render. */
const daysBetween = (start, end) => {
  const out = [];
  const cursor = new Date(`${start}T00:00:00`);
  const last = new Date(`${end}T00:00:00`);
  while (cursor <= last) {
    out.push(iso(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return out;
};

const DayCell = ({ date, events, isToday }) => {
  const label = new Date(`${date}T00:00:00`).getDate();
  return (
    <div className={`task-cal-cell${isToday ? ' is-today' : ''}`}>
      <span className="task-cal-date">{label}</span>
      <div className="task-cal-events">
        {events.map((event) => (
          <Link key={event.task.id} to={`/tasks/${event.task.id}`}
            className={`task-cal-event is-${event.kind}`}
            title={`${event.task.task_number} · ${KIND_LABEL[event.kind]}`}>
            <span className={`task-cal-dot is-${priorityTone(event.task.priority)}`}
              aria-hidden="true" />
            <span className="task-cal-title">{event.task.title}</span>
          </Link>
        ))}
      </div>
    </div>
  );
};

const TaskCalendar = () => {
  const today = iso(new Date());
  const [view, setView] = useState('month');
  const [anchor, setAnchor] = useState(today);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'calendar', view, anchor],
    queryFn: () => taskService.getCalendar({ view, date: anchor }),
  });

  const byDate = useMemo(() => {
    const map = new Map();
    for (const event of data?.events || []) {
      if (!map.has(event.date)) map.set(event.date, []);
      map.get(event.date).push(event);
    }
    return map;
  }, [data]);

  const days = data ? daysBetween(data.start, data.end) : [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Task Calendar</h1>
          <p className="lr-page-sub">
            Tasks on the day they are due — a task with no due date is not on a
            calendar
          </p>
        </div>
        <div className="task-head-badges">
          <div className="task-view-toggle" role="group" aria-label="Calendar view">
            {VIEWS.map((option) => (
              <button key={option.value} type="button"
                aria-pressed={view === option.value}
                className={`lr-btn${view === option.value ? ' is-on' : ''}`}
                onClick={() => setView(option.value)}>
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="task-cal-bar">
        <button type="button" className="lr-btn" aria-label="Previous period"
          onClick={() => setAnchor(step(anchor, view, -1))}>
          <ChevronLeft size={14} />
        </button>
        <button type="button" className="lr-btn" onClick={() => setAnchor(today)}>
          Today
        </button>
        <button type="button" className="lr-btn" aria-label="Next period"
          onClick={() => setAnchor(step(anchor, view, 1))}>
          <ChevronRight size={14} />
        </button>
        {data && (
          <span className="task-cal-range">
            {data.start} → {data.end}
          </span>
        )}
        {data && (
          <span className="task-cal-legend">
            {Object.entries(KIND_LABEL).map(([kind, label]) => (
              <span key={kind} className={`task-cal-key is-${kind}`}>
                {label}: <b>{data.counts?.[kind] ?? 0}</b>
              </span>
            ))}
          </span>
        )}
      </div>

      {isLoading && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}

      {!isLoading && !isError && data && (
        <div className={`task-cal task-cal-${view}`}>
          {view === 'month' && (
            /* Sunday first, matching the working week the server splits on. */
            <div className="task-cal-headings" aria-hidden="true">
              {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => (
                <span key={d}>{d}</span>
              ))}
            </div>
          )}
          <div className="task-cal-grid">
            {/* A month never starts on Sunday by luck, so the first row is
                padded — otherwise the 1st would sit under whichever heading
                happened to be first. */}
            {view === 'month' && Array.from({
              length: (new Date(`${data.start}T00:00:00`).getDay()),
            }).map((_, i) => (
              /* Index-keyed: these are blanks with no identity of their own. */
              <div key={`pad-${i}`} className="task-cal-cell is-pad" aria-hidden="true" />
            ))}
            {days.map((date) => (
              <DayCell key={date} date={date} isToday={date === today}
                events={byDate.get(date) || []} />
            ))}
          </div>
          {days.length > 0 && byDate.size === 0 && (
            <p className="task-sub" style={{ marginTop: 12 }}>
              Nothing is due in this period.
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default TaskCalendar;
