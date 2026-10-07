import React from 'react';
import { Radio, Users, WifiOff } from 'lucide-react';

const STATUS_COLOR = {
  present: 'var(--success)',
  late: 'var(--warning)',
  half_day: '#eab308',
  absent: 'var(--danger)',
  on_leave: 'var(--brand-blue)',
};

/**
 * "Live" vs "Polling" is shown deliberately: when the socket is down the data is
 * up to 20s stale, and a dashboard that looks live while it is not is worse than
 * one that admits the difference.
 */
export const LiveBadge = ({ connected, pollSeconds = 20 }) => (
  <span className={`att-live-badge${connected ? ' att-live-on' : ''}`}
        title={connected
          ? 'Live — updates arrive as they happen'
          : `Reconnecting — refreshing every ${pollSeconds}s in the meantime`}>
    {connected ? <Radio size={12} aria-hidden="true" /> : <WifiOff size={12} aria-hidden="true" />}
    {connected ? 'Live' : 'Polling'}
  </span>
);

const Tile = ({ label, value, color }) => (
  <div className="att-stat">
    <div className="att-stat-val" style={{ color }}>{value}</div>
    <div className="att-stat-lbl">{label}</div>
  </div>
);

const LivePresenceCard = ({ data, connected }) => {
  const counts = data?.counts || {};
  return (
    <section className="table-card att-card">
      <header className="att-card-head">
        <h3><Users size={16} aria-hidden="true" /> Present today</h3>
        <LiveBadge connected={connected} />
      </header>

      {data?.is_holiday && (
        <p className="att-note">{data.holiday_name} — no check-in expected.</p>
      )}

      <div className="att-stats">
        <Tile label="Present" value={counts.present || 0} color={STATUS_COLOR.present} />
        <Tile label="Late" value={counts.late || 0} color={STATUS_COLOR.late} />
        <Tile label="Half day" value={counts.half_day || 0} color={STATUS_COLOR.half_day} />
        <Tile label="On leave" value={counts.on_leave || 0} color={STATUS_COLOR.on_leave} />
        <Tile label="Absent" value={counts.absent || 0} color={STATUS_COLOR.absent} />
      </div>

      <p className="att-note">
        <strong>{data?.present_now || 0}</strong> of {data?.total_employees || 0} checked in.
      </p>
    </section>
  );
};

export default LivePresenceCard;
