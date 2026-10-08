import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { platformService } from '../../../services/platformService';

/** Part 5: one customer's story, newest first. */
const Timeline = ({ slug }) => {
  const [events, setEvents] = useState(null);
  const [all, setAll] = useState(false);
  useEffect(() => { platformService.organizationTimeline(slug).then(({ data }) => setEvents(data)).catch(() => setEvents([])); }, [slug]);
  if (!events) return <p className="pc-muted">Loading…</p>;
  const shown = all ? events : events.slice(0, 15);
  return (
    <>
      <ol className="cs-timeline">
        {shown.map((e, i) => (
          <li key={`${e.kind}-${e.at}-${i}`} className={`is-${e.kind.split('_')[0]}`}>
            <time dateTime={e.at}>{new Date(e.at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</time>
            <strong>{e.link ? <Link to={e.link}>{e.title}</Link> : e.title}</strong>
            {e.detail && <span className="pc-muted">{e.detail}</span>}
          </li>
        ))}
      </ol>
      {events.length > 15 && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAll((v) => !v)}>
          {all ? 'Show recent only' : `Show all ${events.length} events`}
        </button>
      )}
    </>
  );
};

export default Timeline;
