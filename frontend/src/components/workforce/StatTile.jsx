import React from 'react';
import { Link } from 'react-router-dom';

/**
 * One number with a label. Deliberately not memoised: it renders a string and
 * memoising it would cost more than it saves.
 */
const StatTile = ({ label, value, tone = 'muted', hint, to, icon }) => {
  const body = (
    <>
      <span className={`wf-tile-value wf-tone-${tone}`}>{value ?? 0}</span>
      <span className="wf-tile-label">
        {icon}{label}
      </span>
      {hint && <span className="wf-tile-hint">{hint}</span>}
    </>
  );
  return to
    ? <Link to={to} className="wf-tile wf-tile-link">{body}</Link>
    : <div className="wf-tile">{body}</div>;
};

/**
 * The status row shared by both the manager and HR dashboards.
 *
 * Built from the counts object the API returns rather than a hardcoded list, so
 * a status the backend adds later shows up instead of silently vanishing —
 * the mistake Phase 8 had to fix in three places.
 */
export const StatusTileRow = ({ counts = {}, presentNow, extra = null }) => (
  <div className="wf-tiles">
    {presentNow !== undefined && (
      <StatTile label="Present now" value={presentNow} tone="ok"
                hint="Checked in, however they did it" />
    )}
    <StatTile label="Present" value={counts.present} tone="ok" />
    <StatTile label="Late" value={counts.late} tone="warn" />
    <StatTile label="Half day" value={counts.half_day} tone="warn" />
    <StatTile label="Work from home" value={counts.wfh} tone="accent" />
    <StatTile label="On leave" value={counts.on_leave} tone="info" />
    <StatTile label="Absent" value={counts.absent} tone="bad" />
    {counts.holiday > 0 && <StatTile label="Holiday" value={counts.holiday} tone="muted" />}
    {extra}
  </div>
);

export default StatTile;
