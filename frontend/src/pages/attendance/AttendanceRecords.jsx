import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, RefreshCw, MapPin, Building2, AlertTriangle } from 'lucide-react';
import { attendanceService } from '../../services/attendanceService';
import { fmtDistance } from '../../utils/geo';
import AttendanceLocationModal from '../../components/attendance/AttendanceLocationModal';

// --- APP-BASED ATTENDANCE: RE-ENABLED ------------------------------------
// The location columns, the geofence chips and the map modal were commented
// out in this page while biometric devices were the only sanctioned source.
// The data was being captured and stored throughout, so every record that
// carries coordinates renders here immediately.

// Fixes worse than this (metres) are network/IP estimates, not GPS — flagged red.
const ACCURATE_THRESHOLD_M = 100;

const STATUS_META = {
  present: { label: 'Present', color: 'var(--success)' },
  late: { label: 'Late', color: 'var(--warning)' },
  half_day: { label: 'Half Day', color: '#eab308' },
  absent: { label: 'Absent', color: 'var(--danger)' },
  on_leave: { label: 'On Leave', color: 'var(--brand-blue)' },
  holiday: { label: 'Holiday', color: 'var(--text-muted)' },
};

// Where each row came from. `source_display` is the server's label ("Web
// app", "Mobile app", "Biometric device", "HR entry"); a biometric row also
// names the terminal. With App + Biometric the two ends of one day can come
// from different places, so each time carries its own small tag when it
// differs from the row's source.
const SourceCell = ({ row }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
    <span style={{ fontWeight: 600 }}>{row.source_display || '—'}</span>
    {row.source === 'biometric' && row.device_name && (
      <span style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{row.device_name}</span>
    )}
  </div>
);

const EndSource = ({ row, end }) => {
  const own = row[`${end}_source`];
  if (!own || own === row.source) return null;
  return (
    <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>
      via {row[`${end}_source_display`]}
    </div>
  );
};

const STATUS_FILTERS = ['present', 'late', 'half_day', 'absent', 'on_leave', 'holiday'];

const todayISO = () => new Date().toISOString().slice(0, 10);
const daysAgoISO = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
};
const fmtTime = (iso) =>
  iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—';

// One captured location: address (or coordinates) + accuracy chip + geofence
// verdict + a button that pins it on a map. The geofence row appears only when
// an office is configured.
const LocationCell = ({ address, lat, lng, accuracy, distance, within, onView, viewLabel, hasEvent }) => {
  if (!lat || !lng) {
    // Distinguish "never happened" from "happened but the browser gave us
    // nothing" — otherwise a blank column reads as a broken feature.
    if (!hasEvent) return <span style={{ color: 'var(--text-muted)' }}>—</span>;
    return (
      <span
        style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-meta)', fontStyle: 'italic' }}
        title="The employee's browser did not provide a location at this moment — no GPS, permission refused, or the site was not opened over HTTPS."
      >
        Not captured
      </span>
    );
  }
  const text = address || `${Number(lat).toFixed(5)}, ${Number(lng).toFixed(5)}`;
  const acc = accuracy != null ? `±${Math.round(accuracy)}m` : null;
  const poor = accuracy != null && accuracy > ACCURATE_THRESHOLD_M;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div className="att-loc" style={{ margin: 0 }}>
        <MapPin size={13} aria-hidden="true" />
        <span className="att-loc-text" title={`${text}${acc ? ` (accuracy ${acc})` : ''}`}>{text}</span>
        {acc && <span className={`att-loc-acc${poor ? ' att-loc-acc-poor' : ''}`}>{acc}</span>}
        <button type="button" className="att-loc-view" onClick={onView} aria-label={viewLabel}>
          View map
        </button>
      </div>
      {distance != null && within != null && (
        <span className={`att-geo${within ? ' att-geo-in' : ' att-geo-out'}`}
          title={`${fmtDistance(distance)} from the office`}>
          <Building2 size={12} aria-hidden="true" />
          {within ? 'At office' : `${fmtDistance(distance)} away`}
        </span>
      )}
    </div>
  );
};

const StatusChip = ({ status }) => {
  const meta = STATUS_META[status] || { label: status || '—', color: 'var(--text-secondary)' };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: meta.color, fontWeight: 600, fontSize: 'var(--fs-sm)' }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: meta.color, display: 'inline-block' }} />
      {meta.label}
    </span>
  );
};

const AttendanceRecords = () => {
  const [dateFrom, setDateFrom] = useState(daysAgoISO(6));
  const [dateTo, setDateTo] = useState(todayISO());
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [pinned, setPinned] = useState(null);

  // Server-side filters the list endpoint understands. Employee search is applied
  // client-side (below) so the page works for every privileged role without the
  // HR/Admin-only report-options endpoint.
  const params = useMemo(
    () => ({ date_from: dateFrom, date_to: dateTo, ...(status ? { status } : {}) }),
    [dateFrom, dateTo, status],
  );

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['attendance', 'records', params],
    queryFn: () => attendanceService.list(params),
  });

  const rows = useMemo(() => {
    const all = Array.isArray(data) ? data : [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((r) =>
      `${r.employee_name || ''} ${r.employee_id || ''} ${r.department_name || ''}`.toLowerCase().includes(q),
    );
  }, [data, search]);

  const located = useMemo(
    () => rows.filter((r) => (r.check_in_lat && r.check_in_lng) || (r.check_out_lat && r.check_out_lng)).length,
    [rows],
  );
  const offSite = useMemo(
    () => rows.filter((r) => r.check_in_within_office === false).length,
    [rows],
  );

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div>
          <h2>Attendance Records</h2>
          <div className="lr-page-sub">Employee check-in &amp; check-out records</div>
        </div>
        <button type="button" className="btn btn-ghost" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw size={15} className={isFetching ? 'lr-spin' : ''} /> Refresh
        </button>
      </div>

      <div className="table-card" style={{ padding: 16, marginBottom: 16 }}>
        <div className="att-row2" style={{ gap: 12, flexWrap: 'wrap' }}>
          <label className="lr-field"><span>From</span>
            <input type="date" value={dateFrom} max={dateTo} onChange={(e) => setDateFrom(e.target.value)} />
          </label>
          <label className="lr-field"><span>To</span>
            <input type="date" value={dateTo} min={dateFrom} max={todayISO()} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          <label className="lr-field"><span>Status</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {STATUS_FILTERS.map((s) => <option key={s} value={s}>{STATUS_META[s].label}</option>)}
            </select>
          </label>
          <label className="lr-field" style={{ flex: 1, minWidth: 200 }}><span>Search employee</span>
            <span style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <Search size={15} style={{ position: 'absolute', left: 10, color: 'var(--text-muted)' }} aria-hidden="true" />
              <input type="text" placeholder="Name, ID or department" value={search}
                onChange={(e) => setSearch(e.target.value)} style={{ paddingLeft: 32, width: '100%' }} />
            </span>
          </label>
        </div>
      </div>

      {isLoading && <div className="table-card" style={{ padding: 24 }}>Loading attendance records…</div>}
      {isError && (
        <div className="att-err" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span>Could not load attendance records. You may not have permission, or the server is unavailable.</span>
          <button type="button" className="btn btn-ghost" onClick={() => refetch()}>Retry</button>
        </div>
      )}

      {!isLoading && !isError && rows.length === 0 && (
        <div className="table-card" style={{ padding: 24, color: 'var(--text-secondary)' }}>
          No attendance records for this filter.
        </div>
      )}

      {!isLoading && !isError && rows.length > 0 && (
        <>
          <div className="att-rec-tally">
            {rows.length} record{rows.length === 1 ? '' : 's'}
            {' · '}
            {located} with a location
            {offSite > 0 && (
              <>
                {' · '}
                <span className="att-rec-offsite">
                  <AlertTriangle size={12} aria-hidden="true" />
                  {offSite} checked in away from the office
                </span>
              </>
            )}
            {located === 0 && rows.length > 0 && (
              <>
                {' — '}
                no coordinates captured for these records. App check-in may be
                off for this organization, or these are device punches.
              </>
            )}
          </div>
          <div className="lr-table-wrap">
            <table className="lr-table">
              <caption className="sr-only">Attendance records</caption>
              <thead>
                <tr>
                  <th scope="col">Employee</th>
                  <th scope="col">Department</th>
                  <th scope="col">Date</th>
                  <th scope="col">Status</th>
                  <th scope="col">Source</th>
                  <th scope="col">Check-in</th>
                  <th scope="col">Check-in location</th>
                  <th scope="col">Check-out</th>
                  <th scope="col">Check-out location</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{r.employee_name || '—'}</div>
                      <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.employee_id || ''}</div>
                    </td>
                    <td>{r.department_name || '—'}</td>
                    <td>
                      <div>{r.date}</div>
                      <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>B.S. {r.date_bs}</div>
                    </td>
                    <td><StatusChip status={r.status} /></td>
                    <td><SourceCell row={r} /></td>
                    <td>{fmtTime(r.check_in)}<EndSource row={r} end="check_in" /></td>
                    <td>
                      <LocationCell address={r.check_in_address} lat={r.check_in_lat} lng={r.check_in_lng}
                        accuracy={r.check_in_accuracy} distance={r.check_in_distance_m} within={r.check_in_within_office}
                        onView={() => setPinned(r)} hasEvent={!!r.check_in}
                        viewLabel={`View check-in location for ${r.employee_name || 'employee'} on ${r.date}`} />
                    </td>
                    <td>{fmtTime(r.check_out)}<EndSource row={r} end="check_out" /></td>
                    <td>
                      <LocationCell address={r.check_out_address} lat={r.check_out_lat} lng={r.check_out_lng}
                        accuracy={r.check_out_accuracy} distance={r.check_out_distance_m} within={r.check_out_within_office}
                        onView={() => setPinned(r)} hasEvent={!!r.check_out}
                        viewLabel={`View check-out location for ${r.employee_name || 'employee'} on ${r.date}`} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {pinned && <AttendanceLocationModal record={pinned} onClose={() => setPinned(null)} />}
    </div>
  );
};

export default AttendanceRecords;
