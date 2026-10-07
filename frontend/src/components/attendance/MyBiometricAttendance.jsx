import React, { useEffect, useState } from 'react';
import { Fingerprint, Clock } from 'lucide-react';
import api from '../../services/api';
import Skeleton from '../common/Skeleton';

/* An employee's own biometric punches, on their profile page.
 *
 * The data comes from the backend's /attendance/biometric/me/ endpoint, which
 * filters the shared biometric dataset down to just this user (matched on their
 * HR-set biometric id) — the raw morx dashboard, which shows everyone, stays
 * admin-only. This component only ever sees the caller's own rows.
 */
const WINDOWS = [
  { key: '30', label: '30 days' },
  { key: '90', label: '90 days' },
  { key: 'all', label: 'All time' },
];

const Tile = ({ label, value, sub }) => (
  <div style={{ padding: 16, background: 'var(--bg-main)', borderRadius: 10, minWidth: 0 }}>
    <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 'var(--fs-h1)', fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.1 }}>{value}</div>
    {sub ? <div style={{ fontSize: 'var(--fs-label)', color: 'var(--text-muted)', marginTop: 3 }}>{sub}</div> : null}
  </div>
);

const fmtDate = (iso) => {
  try {
    return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
  } catch { return iso; }
};

const MyBiometricAttendance = () => {
  const [days, setDays] = useState('90');
  // The result is tagged with the window it was fetched for, and "loading" is
  // derived: the result on hand belongs to a different window. No synchronous
  // setState({ loading: true }) at the top of the effect, and so no extra render
  // on every change - stale rows are still never shown against a new window.
  const [result, setResult] = useState({ key: null });

  useEffect(() => {
    let alive = true;
    api.get('/attendance/biometric/me/', { params: { days } })
      .then((res) => { if (alive) setResult({ key: days, data: res.data }); })
      .catch((err) => {
        if (alive) setResult({ key: days, error: err?.response?.data?.detail || 'Could not load biometric attendance.' });
      });
    return () => { alive = false; };
  }, [days]);

  const loading = result.key !== days;
  const { data, error } = loading ? {} : result;

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
      <div style={{ width: 36, height: 36, borderRadius: 8, background: '#0ea5e9', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Fingerprint size={18} color="white" />
      </div>
      <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>My Biometric Attendance</span>
      <div style={{ marginLeft: 'auto', display: 'inline-flex', gap: 4, background: 'var(--bg-main)', borderRadius: 8, padding: 3 }}>
        {WINDOWS.map((w) => (
          <button key={w.key} type="button" onClick={() => setDays(w.key)}
            style={{
              border: 'none', cursor: 'pointer', fontSize: 'var(--fs-meta)', padding: '5px 10px', borderRadius: 6,
              background: days === w.key ? 'var(--brand-blue, #2563EB)' : 'transparent',
              color: days === w.key ? '#fff' : 'var(--text-secondary)', fontWeight: 500,
            }}>
            {w.label}
          </button>
        ))}
      </div>
    </div>
  );

  let body;
  if (loading) {
    body = <Skeleton rows={3} label="Loading attendance" />;
  } else if (error) {
    body = <div style={{ padding: 24, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>{error}</div>;
  } else if (data && data.configured === false) {
    body = (
      <div style={{ padding: 24, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', lineHeight: 1.6 }}>
        Your account isn’t linked to the biometric device yet. Ask HR to set your <b>biometric ID</b> so your
        check-ins appear here.
      </div>
    );
  } else if (data && data.available === false) {
    body = <div style={{ padding: 24, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>The biometric service is unavailable right now. Try again shortly.</div>;
  } else if (data && data.found === false) {
    body = <div style={{ padding: 24, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>Your biometric ID isn’t recognised by the device. Please check with HR.</div>;
  } else if (data && data.days) {
    const s = data.summary;
    body = (
      <>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 18 }}>
          <Tile label="Days present" value={s.days_present} sub={`in the last ${days === 'all' ? 'all time' : `${days} days`}`} />
          <Tile label="Usually arrives" value={s.median_arrival || '—'} sub={`office starts ${s.office_start}`} />
          <Tile label="Usually leaves" value={s.median_departure || '—'} />
          <Tile label="Late days" value={s.late_days} sub={s.days_present ? `${Math.round((s.late_days / s.days_present) * 100)}% of days` : ''} />
        </div>

        {data.days.length ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text-muted)', fontSize: 'var(--fs-meta)' }}>
                  <th style={{ padding: '8px 10px', fontWeight: 500 }}>Date</th>
                  <th style={{ padding: '8px 10px', fontWeight: 500 }}>Day</th>
                  <th style={{ padding: '8px 10px', fontWeight: 500 }}>Check-in</th>
                  <th style={{ padding: '8px 10px', fontWeight: 500 }}>Check-out</th>
                  <th style={{ padding: '8px 10px', fontWeight: 500 }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.days.map((d) => (
                  <tr key={d.date} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 10px', color: 'var(--text-primary)', fontWeight: 500 }}>{fmtDate(d.date)}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{d.weekday}</td>
                    <td style={{ padding: '8px 10px', color: d.late ? '#d97706' : 'var(--text-primary)' }}>{d.first || '—'}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{d.last || '—'}</td>
                    <td style={{ padding: '8px 10px' }}>
                      <span style={{
                        fontSize: 'var(--fs-label)', fontWeight: 600, padding: '2px 8px', borderRadius: 12,
                        background: d.late ? 'rgba(217,119,6,.12)' : 'rgba(16,185,129,.12)',
                        color: d.late ? '#b45309' : '#047857',
                      }}>
                        {d.late ? 'Late' : 'On time'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ padding: 20, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>No check-ins recorded in this period.</div>
        )}

        {s.last_seen ? (
          <div style={{ marginTop: 12, fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Clock size={13} /> Last seen {s.last_seen}
          </div>
        ) : null}
      </>
    );
  }

  return (
    <div className="table-card" style={{ padding: 24, marginTop: 24 }}>
      {header}
      {body}
    </div>
  );
};

export default MyBiometricAttendance;
