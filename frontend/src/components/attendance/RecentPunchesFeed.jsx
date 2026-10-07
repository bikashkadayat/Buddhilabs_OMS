import React from 'react';
import { Activity } from 'lucide-react';
import { LiveBadge } from './LivePresenceCard';

const timeOf = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return '—'; }
};

/**
 * The raw punch feed, newest first.
 *
 * Unmapped punches are shown rather than hidden: a punch with no employee
 * attached is exactly the thing HR needs to notice and resolve, and filtering
 * it out would make the device look like it had stopped reporting.
 */
const RecentPunchesFeed = ({ punches = [], connected }) => (
  <section className="table-card att-card">
    <header className="att-card-head">
      <h3><Activity size={16} aria-hidden="true" /> Recent punches</h3>
      <LiveBadge connected={connected} />
    </header>

    {punches.length === 0 ? (
      <p className="att-note">No punches recorded yet.</p>
    ) : (
      <ul className="att-punch-feed">
        {punches.map((p) => (
          <li key={p.punch_id} className={p.is_mapped ? '' : 'att-punch-unmapped'}>
            <span className="att-punch-time">{timeOf(p.timestamp)}</span>
            <span className="att-punch-name">
              {p.employee_name}
              {!p.is_mapped && (
                <em title="This device ID has no employee mapped yet">
                  {' '}(device #{p.device_user_id})
                </em>
              )}
            </span>
            <span className="att-punch-kind">{(p.punch_label || '').replace(/_/g, ' ')}</span>
          </li>
        ))}
      </ul>
    )}
  </section>
);

export default RecentPunchesFeed;
