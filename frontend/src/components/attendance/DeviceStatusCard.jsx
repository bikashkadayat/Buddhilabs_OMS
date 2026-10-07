import React from 'react';
import { HardDrive } from 'lucide-react';

const timeOf = (iso) => {
  if (!iso) return 'never';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return 'unknown'; }
};

/**
 * Biometric terminal health. HR/Admin only — the API omits `devices` entirely
 * for anyone else, so this renders nothing rather than leaking infrastructure
 * detail to a department head.
 */
const DeviceStatusCard = ({ devices = [] }) => {
  if (!devices.length) return null;

  return (
    <section className="table-card att-card">
      <header className="att-card-head">
        <h3><HardDrive size={16} aria-hidden="true" /> Devices</h3>
      </header>

      <ul className="att-device-list">
        {devices.map((d) => (
          <li key={d.id} className="att-device">
            <span className={`att-dot att-dot-${d.connection_status}`} aria-hidden="true" />
            <span className="att-device-name">{d.name}</span>
            <span className="att-device-meta">
              {d.connection_status === 'online' ? 'Online' : 'Offline'}
              {' · last seen '}{timeOf(d.last_seen_at)}
            </span>

            {/* Punches sitting unsent in the collector's spool. This is
                attendance that exists but has not reached us yet — a number
                climbing while syncs still succeed is the real warning sign. */}
            {d.pending_punches > 0 && (
              <span className="att-device-warn" title="Punches queued on the collector">
                {d.pending_punches} queued
              </span>
            )}
            {d.unmapped_count > 0 && (
              <span className="att-device-warn" title="Device IDs with no employee mapped">
                {d.unmapped_count} unmapped
              </span>
            )}
            {d.failed_batches > 0 && (
              <span className="att-device-warn" title="Sync batches the server rejected">
                {d.failed_batches} failed
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
};

export default DeviceStatusCard;
