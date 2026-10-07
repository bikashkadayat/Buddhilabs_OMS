import React from 'react';
import { ExternalLink, Fingerprint } from 'lucide-react';

// The biometric attendance dashboard (morx-collector's read-only UI) runs as its
// own service — see the `biometric` service in docker-compose.yml. It's a
// self-contained React app with its own /api endpoints, so rather than porting
// its charts into this app we embed the whole dashboard in an iframe. It shows
// the live check-in/check-out punches for every employee pulled from the ZK
// biometric device.
//
// The URL is loaded by the browser, so it is the host-published origin of that
// service (localhost in local dev). Override with VITE_BIOMETRIC_URL for other
// environments (e.g. a reverse-proxied path in production).
const BIOMETRIC_URL = import.meta.env.VITE_BIOMETRIC_URL || 'http://localhost:8765';

const BiometricAttendance = () => (
  <div style={{ height: 'calc(100vh - 72px)', display: 'flex', flexDirection: 'column' }}>
    <div
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 12, padding: '14px 24px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap',
      }}
    >
      <div>
        <h2 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Fingerprint size={20} /> Biometric Attendance
        </h2>
        <div className="lr-page-sub">Live check-in &amp; check-out punches for all employees, pulled from the biometric device.</div>
      </div>
      <a className="btn btn-ghost" href={BIOMETRIC_URL} target="_blank" rel="noopener noreferrer">
        <ExternalLink size={15} /> Open full screen
      </a>
    </div>
    <iframe
      title="Biometric Attendance Dashboard"
      src={BIOMETRIC_URL}
      style={{ flex: 1, width: '100%', border: 'none' }}
    />
  </div>
);

export default BiometricAttendance;
