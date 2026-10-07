import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ResponsiveContainer, ComposedChart, BarChart, Bar, Line, XAxis, YAxis,
  Tooltip, CartesianGrid, Legend,
} from 'recharts';
import { ArrowLeft, Fingerprint, Clock } from 'lucide-react';
import api from '../../services/api';
import Skeleton from '../../components/common/Skeleton';

/* My Attendance — the employee's own biometric check-ins/check-outs, with
 * daily / weekly / monthly / custom views and charts. All data comes from the
 * backend's /attendance/biometric/me/ endpoint, which returns only this user's
 * rows (matched on their HR-set biometric id); the whole-office morx dashboard
 * stays admin-only.
 */

const VIEWS = [
  { key: 'daily', label: 'Daily', params: { days: 30 } },
  { key: 'weekly', label: 'Weekly', params: { days: 84 } },
  { key: 'monthly', label: 'Monthly', params: { days: 365 } },
  { key: 'custom', label: 'Custom', params: null },
];

const clock = (m) => (m == null ? '—' : `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`);
const shortDate = (iso) => { try { return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }); } catch { return iso; } };
const isoDaysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10); };
const weekStartLabel = (iso) => {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() - d.getDay()); // back to Sunday
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
};
const monthLabel = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
const avg = (nums) => (nums.length ? Math.round(nums.reduce((a, b) => a + b, 0) / nums.length) : null);

const Tile = ({ label, value, sub }) => (
  <div style={{ padding: 16, background: 'var(--bg-main)', borderRadius: 10, minWidth: 0 }}>
    <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 'var(--fs-h1)', fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.1 }}>{value}</div>
    {sub ? <div style={{ fontSize: 'var(--fs-label)', color: 'var(--text-muted)', marginTop: 3 }}>{sub}</div> : null}
  </div>
);

// Group daily rows (newest-first from the API) into weekly / monthly buckets.
const groupBy = (days, keyFn, labelFn) => {
  const map = new Map();
  for (const d of days) {
    const k = keyFn(d.date);
    if (!map.has(k)) map.set(k, { key: k, label: labelFn(d.date), present: 0, late: 0, hours: [], ins: [], outs: [] });
    const g = map.get(k);
    g.present += 1;
    if (d.late) g.late += 1;
    if (d.hours != null) g.hours.push(d.hours);
    if (d.first_min != null) g.ins.push(d.first_min);
    if (d.last_min != null) g.outs.push(d.last_min);
  }
  return [...map.values()]
    .sort((a, b) => (a.key < b.key ? -1 : 1))
    .map((g) => ({
      label: g.label, present: g.present, late: g.late,
      avgHours: g.hours.length ? Math.round((g.hours.reduce((a, b) => a + b, 0) / g.hours.length) * 10) / 10 : 0,
      avgIn: avg(g.ins), avgOut: avg(g.outs),
    }));
};

const TimeTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: 'var(--bg-card, #fff)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', fontSize: 'var(--fs-meta)' }}>
      <div style={{ fontWeight: 600, marginBottom: 4, color: 'var(--text-primary)' }}>{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ color: p.color }}>{p.name}: {clock(p.value)}</div>
      ))}
    </div>
  );
};

const MyAttendance = () => {
  const navigate = useNavigate();
  const [view, setView] = useState('daily');
  const [custom, setCustom] = useState({ from: isoDaysAgo(30), to: isoDaysAgo(0) });
  // Tagged with the request it answers; "loading" is derived from that (see
  // MyBiometricAttendance). A result for another view or range is never shown.
  const [result, setResult] = useState({ key: null });
  const requestKey = `${view}|${custom.from}|${custom.to}`;

  useEffect(() => {
    const cfg = VIEWS.find((v) => v.key === view);
    const params = view === 'custom' ? { from: custom.from, to: custom.to } : cfg.params;
    const key = `${view}|${custom.from}|${custom.to}`;
    let alive = true;
    api.get('/attendance/biometric/me/', { params })
      .then((res) => { if (alive) setResult({ key, data: res.data }); })
      .catch((err) => { if (alive) setResult({ key, error: err?.response?.data?.detail || 'Could not load attendance.' }); });
    return () => { alive = false; };
  }, [view, custom.from, custom.to]);

  const loading = result.key !== requestKey;
  const { data, error } = loading ? {} : result;
  const grouped = view === 'weekly' || view === 'monthly';

  // Chart data: chronological (API returns newest-first).
  const chart = useMemo(() => {
    if (!data?.days) return [];
    if (view === 'weekly') return groupBy(data.days, (iso) => { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() - d.getDay()); return d.toISOString().slice(0, 10); }, weekStartLabel);
    if (view === 'monthly') return groupBy(data.days, (iso) => iso.slice(0, 7), monthLabel);
    return [...data.days].reverse().map((d) => ({
      label: shortDate(d.date), avgIn: d.first_min, avgOut: d.last_min, avgHours: d.hours ?? 0,
    }));
  }, [data, view]);

  const linked = data && data.configured !== false && data.available !== false && data.found !== false && data.days;

  return (
    <div className="page" style={{ paddingBottom: 60 }}>
      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">
            <button className="pg-back" aria-label="Back" onClick={() => navigate(-1)}><ArrowLeft size={18} /></button>
            My Attendance
          </div>
          <div className="pg-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Fingerprint size={22} /> Biometric Attendance
          </div>
          <div className="pg-desc">Your check-in and check-out times recorded by the biometric device.</div>
        </div>
      </div>

      {/* View switcher */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'inline-flex', gap: 4, background: 'var(--bg-main)', borderRadius: 10, padding: 4 }}>
          {VIEWS.map((v) => (
            <button key={v.key} type="button" onClick={() => setView(v.key)}
              style={{
                border: 'none', cursor: 'pointer', fontSize: 'var(--fs-sm)', padding: '7px 14px', borderRadius: 7,
                background: view === v.key ? 'var(--brand-blue, #2563EB)' : 'transparent',
                color: view === v.key ? '#fff' : 'var(--text-secondary)', fontWeight: 500,
              }}>
              {v.label}
            </button>
          ))}
        </div>
        {view === 'custom' && (
          <div style={{ display: 'inline-flex', gap: 8, alignItems: 'center', fontSize: 'var(--fs-sm)', color: 'var(--text-secondary)' }}>
            <input type="date" value={custom.from} max={custom.to} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))}
              style={{ padding: '6px 8px', borderRadius: 8, border: '1px solid var(--border)' }} />
            <span>to</span>
            <input type="date" value={custom.to} min={custom.from} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))}
              style={{ padding: '6px 8px', borderRadius: 8, border: '1px solid var(--border)' }} />
          </div>
        )}
      </div>

      {loading && <div className="table-card"><Skeleton rows={4} label="Loading attendance" /></div>}
      {!loading && error && <div className="table-card" style={{ padding: 40, color: 'var(--text-muted)' }}>{error}</div>}
      {!loading && data?.configured === false && (
        <div className="table-card" style={{ padding: 40, color: 'var(--text-muted)', lineHeight: 1.6 }}>
          Your account isn’t linked to the biometric device yet. Ask HR to set your <b>biometric ID</b> so your
          check-ins appear here.
        </div>
      )}
      {!loading && data?.available === false && (
        <div className="table-card" style={{ padding: 40, color: 'var(--text-muted)' }}>The biometric service is unavailable right now. Try again shortly.</div>
      )}
      {!loading && data?.found === false && (
        <div className="table-card" style={{ padding: 40, color: 'var(--text-muted)' }}>Your biometric ID isn’t recognised by the device. Please check with HR.</div>
      )}

      {!loading && linked && (
        <>
          {/* Summary */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
            <Tile label="Days present" value={data.summary.days_present} />
            <Tile label="Usually arrives" value={data.summary.median_arrival || '—'} sub={`office starts ${data.summary.office_start}`} />
            <Tile label="Usually leaves" value={data.summary.median_departure || '—'} />
            <Tile label="Late days" value={data.summary.late_days}
              sub={data.summary.days_present ? `${Math.round((data.summary.late_days / data.summary.days_present) * 100)}% of days` : ''} />
          </div>

          {data.days.length === 0 ? (
            <div className="table-card" style={{ padding: 40, color: 'var(--text-muted)' }}>No check-ins recorded in this period.</div>
          ) : (
            <>
              {/* Check-in / check-out times chart */}
              <div className="table-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 12 }}>
                  {grouped ? 'Average check-in & check-out time' : 'Check-in & check-out times'}
                </div>
                <ResponsiveContainer width="100%" height={280}>
                  <ComposedChart data={chart} margin={{ top: 8, right: 8, bottom: 4, left: -8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 'var(--fs-label)', fill: 'var(--text-muted)' }} interval="preserveStartEnd" />
                    <YAxis domain={[360, 1200]} ticks={[360, 540, 720, 900, 1080]} tickFormatter={clock}
                      tick={{ fontSize: 'var(--fs-label)', fill: 'var(--text-muted)' }} width={52} />
                    <Tooltip content={<TimeTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 'var(--fs-meta)' }} />
                    <Line type="monotone" dataKey="avgIn" name="Check-in" stroke="#2563EB" strokeWidth={2} dot={{ r: 2 }} connectNulls />
                    <Line type="monotone" dataKey="avgOut" name="Check-out" stroke="#0ea5e9" strokeWidth={2} dot={{ r: 2 }} connectNulls />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* Hours at office chart */}
              <div className="table-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 12 }}>
                  {grouped ? 'Average hours at the office' : 'Hours at the office'}
                </div>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={chart} margin={{ top: 8, right: 8, bottom: 4, left: -16 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 'var(--fs-label)', fill: 'var(--text-muted)' }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 'var(--fs-label)', fill: 'var(--text-muted)' }} width={36} />
                    <Tooltip formatter={(v) => [`${v} h`, 'Hours']} labelStyle={{ color: '#111' }}
                      contentStyle={{ fontSize: 'var(--fs-meta)', borderRadius: 8, border: '1px solid var(--border)' }} />
                    <Bar dataKey="avgHours" name="Hours" fill="#6366F1" radius={[4, 4, 0, 0]} maxBarSize={40} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* Detail table */}
              <div className="table-card" style={{ padding: 20 }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 12 }}>
                  {grouped ? (view === 'weekly' ? 'Week by week' : 'Month by month') : 'Day by day'}
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)' }}>
                    <thead>
                      <tr style={{ textAlign: 'left', color: 'var(--text-muted)', fontSize: 'var(--fs-meta)' }}>
                        <th style={{ padding: '8px 10px', fontWeight: 500 }}>{grouped ? 'Period' : 'Date'}</th>
                        {!grouped && <th style={{ padding: '8px 10px', fontWeight: 500 }}>Day</th>}
                        <th style={{ padding: '8px 10px', fontWeight: 500 }}>{grouped ? 'Days present' : 'Check-in'}</th>
                        <th style={{ padding: '8px 10px', fontWeight: 500 }}>{grouped ? 'Avg check-in' : 'Check-out'}</th>
                        <th style={{ padding: '8px 10px', fontWeight: 500 }}>{grouped ? 'Avg check-out' : 'Hours'}</th>
                        <th style={{ padding: '8px 10px', fontWeight: 500 }}>{grouped ? 'Late days' : 'Status'}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {grouped
                        ? chart.slice().reverse().map((g) => (
                          <tr key={g.label} style={{ borderTop: '1px solid var(--border)' }}>
                            <td style={{ padding: '8px 10px', color: 'var(--text-primary)', fontWeight: 500 }}>{g.label}</td>
                            <td style={{ padding: '8px 10px' }}>{g.present}</td>
                            <td style={{ padding: '8px 10px' }}>{clock(g.avgIn)}</td>
                            <td style={{ padding: '8px 10px' }}>{clock(g.avgOut)}</td>
                            <td style={{ padding: '8px 10px', color: g.late ? '#b45309' : 'var(--text-secondary)' }}>{g.late}</td>
                          </tr>
                        ))
                        : data.days.map((d) => (
                          <tr key={d.date} style={{ borderTop: '1px solid var(--border)' }}>
                            <td style={{ padding: '8px 10px', color: 'var(--text-primary)', fontWeight: 500 }}>{shortDate(d.date)}</td>
                            <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{d.weekday}</td>
                            <td style={{ padding: '8px 10px', color: d.late ? '#d97706' : 'var(--text-primary)' }}>{d.first || '—'}</td>
                            <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{d.last || '—'}</td>
                            <td style={{ padding: '8px 10px' }}>{d.hours != null ? `${d.hours} h` : '—'}</td>
                            <td style={{ padding: '8px 10px' }}>
                              <span style={{
                                fontSize: 'var(--fs-label)', fontWeight: 600, padding: '2px 8px', borderRadius: 12,
                                background: d.late ? 'rgba(217,119,6,.12)' : 'rgba(16,185,129,.12)',
                                color: d.late ? '#b45309' : '#047857',
                              }}>{d.late ? 'Late' : 'On time'}</span>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {data.summary.last_seen && (
                  <div style={{ marginTop: 12, fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <Clock size={13} /> Last seen {data.summary.last_seen}
                  </div>
                )}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
};

export default MyAttendance;
